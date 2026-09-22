"""One node ID in two graphs: every read path answers from one graph only.

Live because the guarantee is in the SQL the readers emit against real `graph_id` columns,
not in Python. Spec 227 replaced `UNIQUE (node_id)` with `UNIQUE (graph_id, node_id)`, so
this fixture is now legal — and it is the shape that exposed three graph-blind readers:

- `engine.get_nodes` selected labels and properties on `s IN (...)` with no graph, merging
  both graphs into one node dict;
- `CoreQuery.nodes` (the published `/graphql` field) had no graph predicate and joined
  `rdf_labels` on `l.s = n.node_id` alone, so it returned other graphs' nodes;
- `nodes(orderBy: "<property>")` joined an unqualified `rdf_props`, which resolves against
  `SQLUser` and failed at Query Open.
"""

import os
import uuid

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IVG_PORT", "31972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")

PFX = f"gsn_{uuid.uuid4().hex[:6]}"
GRAPH_A = f"{PFX}_a"
GRAPH_B = f"{PFX}_b"
SHARED_ID = f"{PFX}:shared"


@pytest.fixture(scope="module")
def engine():
    try:
        import iris

        from iris_vector_graph.engine import IRISGraphEngine

        conn = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        eng = IRISGraphEngine(conn, embedding_dimension=4)
        eng.initialize_schema()
    except Exception as ex:  # pragma: no cover - environment guard
        pytest.skip(f"IRIS unavailable: {ex}")

    eng.create_node(
        SHARED_ID, labels=["ScopedA"], properties={"side": "a", "sort": "1"}, graph=GRAPH_A
    )
    eng.create_node(
        SHARED_ID, labels=["ScopedB"], properties={"side": "b", "sort": "2"}, graph=GRAPH_B
    )
    eng.create_node(
        f"{PFX}:a2", labels=["ScopedA"], properties={"side": "a", "sort": "3"}, graph=GRAPH_A
    )
    yield eng
    conn.close()


@pytest.fixture(scope="module")
def gql_client(engine):
    from fastapi.testclient import TestClient

    from api.main import create_app

    return TestClient(create_app(engine=engine))


def _gql(client, query):
    r = client.post("/graphql", json={"query": query})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("errors") is None, body["errors"]
    return body["data"]


def test_get_node_reads_only_the_named_graph(engine):
    in_a = engine.get_node(SHARED_ID, graph=GRAPH_A)
    in_b = engine.get_node(SHARED_ID, graph=GRAPH_B)

    assert in_a["labels"] == ["ScopedA"], in_a
    assert in_b["labels"] == ["ScopedB"], in_b
    assert in_a["side"] == "a"
    assert in_b["side"] == "b"


def test_a_node_in_another_graph_is_absent_not_empty(engine):
    """The default graph holds neither row, and no fallback widens the search."""
    assert engine.get_node(SHARED_ID) is None
    assert engine.get_nodes([SHARED_ID]) == []


def test_graphql_nodes_returns_one_graphs_rows(gql_client):
    data = _gql(
        gql_client,
        '{ nodes(labels: ["ScopedA"], graph: "%s", limit: 50) { id labels } }' % GRAPH_A,
    )
    ids = {n["id"] for n in data["nodes"]}
    assert ids == {SHARED_ID, f"{PFX}:a2"}, ids
    assert all(n["labels"] == ["ScopedA"] for n in data["nodes"]), data["nodes"]

    data_b = _gql(
        gql_client,
        '{ nodes(labels: ["ScopedB"], graph: "%s", limit: 50) { id labels } }' % GRAPH_B,
    )
    assert [n["id"] for n in data_b["nodes"]] == [SHARED_ID]
    assert data_b["nodes"][0]["labels"] == ["ScopedB"]


def test_graphql_label_from_another_graph_matches_nothing(gql_client):
    """`ScopedB` exists, but not in graph A — a cross-graph label join would find it."""
    data = _gql(
        gql_client,
        '{ nodes(labels: ["ScopedB"], graph: "%s", limit: 50) { id } }' % GRAPH_A,
    )
    assert data["nodes"] == []


def test_graphql_node_by_id_is_graph_scoped(gql_client):
    a = _gql(gql_client, '{ node(id: "%s", graph: "%s") { labels } }' % (SHARED_ID, GRAPH_A))
    b = _gql(gql_client, '{ node(id: "%s", graph: "%s") { labels } }' % (SHARED_ID, GRAPH_B))
    assert a["node"]["labels"] == ["ScopedA"]
    assert b["node"]["labels"] == ["ScopedB"]
    assert _gql(gql_client, '{ node(id: "%s") { labels } }' % SHARED_ID)["node"] is None


def test_graphql_order_by_a_property_runs_at_all(gql_client):
    """The unqualified `rdf_props` join made this a Query Open failure, not an ordering."""
    data = _gql(
        gql_client,
        '{ nodes(labels: ["ScopedA"], graph: "%s", orderBy: "sort", '
        'orderDirection: "ASC", limit: 50) { id } }' % GRAPH_A,
    )
    assert [n["id"] for n in data["nodes"]] == [SHARED_ID, f"{PFX}:a2"]


def test_graphql_property_filter_is_graph_scoped(gql_client):
    """`side: "b"` is graph B's value for the same node ID."""
    data = _gql(
        gql_client,
        '{ nodes(where: {key: "side", value: "b"}, graph: "%s", limit: 50) { id } }' % GRAPH_A,
    )
    assert data["nodes"] == []

    data_b = _gql(
        gql_client,
        '{ nodes(where: {key: "side", value: "b"}, graph: "%s", limit: 50) { id } }' % GRAPH_B,
    )
    assert [n["id"] for n in data_b["nodes"]] == [SHARED_ID]
