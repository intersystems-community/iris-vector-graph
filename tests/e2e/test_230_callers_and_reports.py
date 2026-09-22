"""Spec 230 US4 gate — every caller can name a graph, and every report is measured.

Four claims, each proven on the server because each one lives there:

* **the graph-blind callers** (FR-013) — `search_nodes_by_vector`, the
  `iris_vector_graph.operators` facade's `kg_KNN_VEC`, and `multi_vector_search`
  reached the scoped machinery underneath without a graph, so they answered from
  the default route for every graph and could not reach a routed table at all. The
  leak and the unreachability are both properties of which table the statement
  names, so a fake store cannot show either.
* **the reported width** (FR-014) — `Graph.KG.PyOps.getExpectedDimension` read the
  width of the literal `Graph_KG.kg_NodeEmbeddings`, and `vectorToJson` validated
  every write against that number. A route declared narrower or wider than the
  default table is the only thing that distinguishes a measured width from a
  hardcoded one, and a declared VECTOR width exists only in IRIS.
* **the exact two-hop count** (SC-010) — `Build2HopExactStats` walked one subscript
  past the writer's key, so `^KG("deg2p_exact")` stayed empty and every exact count
  came back 0. What the walk finds is a fact about the globals.
* **a bare `ivg.retrieve`** (SC-010) — the two-argument call asked IRIS to embed with
  an `%Embedding.Config` named `' '` and the whole statement failed at Query Open
  with `SQLCODE -280`. The GraphQL endpoint is where that call arrives.

`SKIP_IRIS_TESTS=true` fails rather than skips, for the reason the other two spec
230 E2E files give: a skipped scope test is indistinguishable from a passing one.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import pytest

pytestmark = [pytest.mark.e2e]

GRAPH_A = "ivg230:callers:a"
GRAPH_B = "ivg230:callers:b"

GRAPHS = (GRAPH_A, GRAPH_B)

MODEL_A = "ivg230-callers-model-a"
MODEL_B = "ivg230-callers-model-b"

#: Narrower and wider than the default table, which is declared at 768 on every
#: shipped schema. Both are needed: a `getExpectedDimension` that reported the
#: narrower one could still be reading a minimum rather than the route.
DIM_A = 8
DIM_B = 16

#: In all three graphs — A, B and the default graph. One entity, three graphs: the
#: fixture 227 made possible and the reason a node ID no longer identifies a row.
SHARED = "ivg230:callers:shared"
#: Each in one graph only. Every "did it leak" assertion is about one of these: a
#: shared ID cannot tell a scoped answer from an unscoped one.
ONLY_A = "ivg230:callers:only-a"
ONLY_B = "ivg230:callers:only-b"
ONLY_DEFAULT = "ivg230:callers:only-default"

#: The two-hop path, in graph A and in the default graph.
HOP_SRC = "ivg230:callers:hop:src"
HOP_MID = "ivg230:callers:hop:mid"
HOP_FAR = "ivg230:callers:hop:far"

PRED = "IVG230_CALLERS_LINKS"

#: A token that appears in no other row in the namespace, so the text leg's
#: candidate set is exactly this fixture's documents.
TERM = "ivg230quokkaword"

#: `ivg.retrieve('text', 5)` — the call this file is about — defaults to the BM25
#: index named `default`. Nothing else in the suite builds one.
DEFAULT_BM25 = "default"

#: Every node ID this file writes into the default graph, so the cleanup can name
#: them. Deleting the default graph by `graph_id` would take the rest of the suite's
#: default-graph rows with it.
DEFAULT_GRAPH_IDS = (SHARED, ONLY_DEFAULT, HOP_SRC, HOP_MID, HOP_FAR)


def _vec(dim: int, fill: float) -> list:
    return [float(fill)] * dim


def _as_query(vector) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def _iris(conn):
    import iris as _iris_mod

    return _iris_mod.createIRIS(conn)


def _default_route_width(conn) -> int:
    """The width `kg_NodeEmbeddings.emb` is declared at in this namespace."""
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
    assert width, "Graph_KG.kg_NodeEmbeddings declares no vector width"
    return int(width)


def _routed_tables(conn, graph: str) -> list:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT table_name FROM Graph_KG.embedding_registry "
            "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
            (graph,),
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _wipe(conn):
    """Remove both named graphs, their routes, and this file's default-graph rows.

    Routed tables are dropped rather than emptied: a route is created on demand at a
    declared width, so one left over from an earlier run answers this run's lookup
    with a stale width and the width assertions pass for the wrong reason.
    """
    cursor = conn.cursor()
    try:
        for graph in GRAPHS:
            for table in _routed_tables(conn, graph):
                with contextlib.suppress(Exception):
                    cursor.execute(f"DROP TABLE Graph_KG.{table}")
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.embedding_registry "
                    "WHERE COALESCE(graph_id, '') = ?",
                    (graph,),
                )
            for table in ("rdf_edges", "rdf_props", "rdf_labels", "docs"):
                with contextlib.suppress(Exception):
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
            for table in ("kg_NodeEmbeddings", "nodes"):
                with contextlib.suppress(Exception):
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )

        placeholders = ", ".join("?" for _ in DEFAULT_GRAPH_IDS)
        for table, column in (
            ("rdf_edges", "s"),
            ("rdf_props", "s"),
            ("rdf_labels", "s"),
            ("docs", "id"),
            ("kg_NodeEmbeddings", "node_id"),
            ("nodes", "node_id"),
        ):
            with contextlib.suppress(Exception):
                cursor.execute(
                    f"DELETE FROM Graph_KG.{table} "
                    f"WHERE COALESCE(graph_id, '') = '' AND {column} IN ({placeholders})",
                    DEFAULT_GRAPH_IDS,
                )
        with contextlib.suppress(Exception):
            cursor.execute(
                "DELETE FROM Graph_KG.rdf_edges "
                f"WHERE COALESCE(graph_id, '') = '' AND o_id IN ({placeholders})",
                DEFAULT_GRAPH_IDS,
            )
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 230 US4 asserts which table a caller's statement names and what "
            "width a server-side method reports. SKIP_IRIS_TESTS=true is not an "
            "acceptable outcome — start ivg-iris-enterprise with "
            "scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: a declared VECTOR width and a `^KG` walk "
            "cannot be observed from Python."
        )


@pytest.fixture
def callers_env(iris_connection):
    """Three graphs holding one node ID, with two routes and the default table.

    Vectors are written through the engine so the routes are created by the writes
    (227's FR-013): a test that created the tables itself would pass against an
    engine that never routes anything. The default graph keeps writing to
    `kg_NodeEmbeddings`, which is where 3.2.0 put it and where the `kg_KNN_VEC`
    procedure still looks.
    """
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    default_width = _default_route_width(iris_connection)

    for graph in GRAPHS:
        engine.create_node(SHARED, labels=["Ivg230Shared"], graph=graph)
    engine.create_node(ONLY_A, labels=["Ivg230OnlyA"], graph=GRAPH_A)
    engine.create_node(ONLY_B, labels=["Ivg230OnlyB"], graph=GRAPH_B)
    engine.create_node(SHARED, labels=["Ivg230Shared"])
    engine.create_node(ONLY_DEFAULT, labels=["Ivg230OnlyDefault"])

    engine.store_embedding(SHARED, _vec(DIM_A, 0.11), graph=GRAPH_A, model_key=MODEL_A)
    engine.store_embedding(ONLY_A, _vec(DIM_A, 0.12), graph=GRAPH_A, model_key=MODEL_A)
    engine.store_embedding(SHARED, _vec(DIM_B, 0.21), graph=GRAPH_B, model_key=MODEL_B)
    engine.store_embedding(ONLY_B, _vec(DIM_B, 0.22), graph=GRAPH_B, model_key=MODEL_B)
    engine.store_embedding(SHARED, _vec(default_width, 0.31))
    engine.store_embedding(ONLY_DEFAULT, _vec(default_width, 0.32))

    yield engine, default_width

    _wipe(iris_connection)


# --- T053 / FR-013: the three graph-blind callers scope --------------------------


def _ids(rows) -> set:
    """The node IDs out of either answer shape: `(id, score)` or `{"id": ...}`."""
    out = set()
    for row in rows:
        if isinstance(row, dict):
            out.add(str(row.get("id") or row.get("node_id")))
        else:
            out.add(str(row[0]))
    return out


def test_graph_blind_callers_scope(callers_env):
    """FR-013: each of the three entry points answers from the graph it is given,
    and from the default graph when it is given none.

    One test, because the claim is about the set of them: each one reaches the same
    routed machinery, and a fix that scoped two of the three would leave the third
    as the way around it.
    """
    engine, default_width = callers_env

    from iris_vector_graph.operators import IRISGraphOperators

    # 1. the search entry point
    scoped_a = engine.search_nodes_by_vector(
        _as_query(_vec(DIM_A, 0.11)), k=10, graph=GRAPH_A, model_key=MODEL_A
    )
    ids_a = _ids(scoped_a)
    assert ids_a, "graph A's search answered nothing over a populated route"
    assert ids_a <= {SHARED, ONLY_A}, sorted(ids_a)
    assert ONLY_B not in ids_a and ONLY_DEFAULT not in ids_a, sorted(ids_a)

    default_search = engine.search_nodes_by_vector(
        _as_query(_vec(default_width, 0.31)), k=10
    )
    ids_default = _ids(default_search)
    assert ONLY_DEFAULT in ids_default, (
        "a caller who named no graph did not get the default graph: "
        f"{sorted(ids_default)}"
    )
    assert ONLY_A not in ids_default and ONLY_B not in ids_default, sorted(ids_default)

    # 2. the operators facade
    facade = IRISGraphOperators(engine.conn)
    ids_b = _ids(
        facade.kg_KNN_VEC(
            _as_query(_vec(DIM_B, 0.21)), k=10, graph=GRAPH_B, model_key=MODEL_B
        )
    )
    assert ids_b, "the facade answered nothing for graph B"
    assert ids_b <= {SHARED, ONLY_B}, sorted(ids_b)
    assert ONLY_A not in ids_b and ONLY_DEFAULT not in ids_b, sorted(ids_b)

    ids_facade_default = _ids(facade.kg_KNN_VEC(_as_query(_vec(default_width, 0.31)), k=10))
    assert ONLY_DEFAULT in ids_facade_default, sorted(ids_facade_default)
    assert ONLY_A not in ids_facade_default, sorted(ids_facade_default)
    assert ONLY_B not in ids_facade_default, sorted(ids_facade_default)

    # 3. the multi-source fusion. Without a graph a routed table is unreachable —
    #    `vector_search` refuses it rather than scanning it — so this path could not
    #    read a routed vector at all, which is the other half of FR-013.
    route_a = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A)
    assert route_a is not None, "graph A's write created no route"
    source = {"table": f"Graph_KG.{route_a.table_name}", "col": "emb", "id_col": "node_id"}

    fused = engine.multi_vector_search(
        [source], _vec(DIM_A, 0.11), top_k=10, graph=GRAPH_A
    )
    ids_fused = _ids(fused)
    assert ids_fused, (
        "the multi-source path could not read a routed table even when given the "
        "graph that owns it"
    )
    assert ids_fused <= {SHARED, ONLY_A}, sorted(ids_fused)
    assert ONLY_B not in ids_fused, sorted(ids_fused)

    unscoped = engine.multi_vector_search([source], _vec(DIM_A, 0.11), top_k=10)
    assert _ids(unscoped) == set(), (
        "a routed table answered a search that named no graph: the refusal in "
        f"vector_search was bypassed, not defaulted ({unscoped})"
    )


# --- T054 / FR-014: the reported width is the route's ----------------------------


def test_expected_dimension_reads_the_route(callers_env):
    """`getExpectedDimension` reports the width of the route the write lands in.

    Graph A's route is narrower than `kg_NodeEmbeddings` and graph B's is wider, so
    a method still reading the literal table reports the same number for both — and
    `vectorToJson` rejects every correct write to either route on that number.
    """
    engine, default_width = callers_env
    iris_obj = _iris(engine.conn)

    assert default_width not in (DIM_A, DIM_B), (
        "this test needs the default table declared at a width neither route uses; "
        f"it is {default_width}"
    )

    width_a = int(
        iris_obj.classMethodValue(
            "Graph.KG.PyOps", "getExpectedDimension", GRAPH_A, MODEL_A
        )
    )
    width_b = int(
        iris_obj.classMethodValue(
            "Graph.KG.PyOps", "getExpectedDimension", GRAPH_B, MODEL_B
        )
    )
    width_default = int(
        iris_obj.classMethodValue("Graph.KG.PyOps", "getExpectedDimension")
    )

    assert width_a == DIM_A, (
        f"graph A's route is declared VECTOR(DOUBLE, {DIM_A}) and the reported "
        f"width is {width_a}"
    )
    assert width_b == DIM_B, (
        f"graph B's route is declared VECTOR(DOUBLE, {DIM_B}) and the reported "
        f"width is {width_b}"
    )
    assert width_default == default_width, (
        "a caller naming no graph must still get the default table's width, "
        f"{default_width}, not {width_default}"
    )

    # The consumer, not just the reporter: `vectorToJson` validates every write
    # against this number, and a width it rejects is a write that cannot happen.
    table_a = str(
        iris_obj.classMethodValue("Graph.KG.PyOps", "getVectorTable", GRAPH_A, MODEL_A)
    )
    route_a = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A)
    assert route_a is not None
    assert table_a.split(".")[-1] == route_a.table_name, (
        f"the reported table {table_a} is not graph A's route {route_a.table_name}"
    )


# --- T055 / SC-010: the exact two-hop count is a count --------------------------


@pytest.fixture
def hop_env(iris_connection):
    """A two-hop path on one predicate, in graph A and in the default graph."""
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    for graph in (GRAPH_A, None):
        engine.create_node(HOP_SRC, graph=graph)
        engine.create_node(HOP_MID, graph=graph)
        engine.create_node(HOP_FAR, graph=graph)
        engine.create_edge(HOP_SRC, PRED, HOP_MID, graph=graph)
        engine.create_edge(HOP_MID, PRED, HOP_FAR, graph=graph)

    engine.rebuild_kg()

    yield engine, _iris(iris_connection)

    _wipe(iris_connection)
    with contextlib.suppress(Exception):
        engine.rebuild_kg()


def test_build_2hop_exact_stats_returns_a_count(hop_env):
    """SC-010: a database with two-hop paths reports a non-zero exact count.

    The walk read one subscript past the writer's key, so it found nothing in every
    graph — including the default one — and `^KG("deg2p_exact")` was never written.
    Zero is the answer a graph with no two-hop paths gives, which is why this went
    unnoticed.
    """
    engine, iris_obj = hop_env

    count = engine.backfill_2hop_exact()
    assert count > 0, (
        "Build2HopExactStats reported no entries for a database holding two "
        "two-hop paths"
    )

    key_a = iris_obj.classMethodValue("Graph.KG.GraphKey", "ForIndex", GRAPH_A)
    key_default = iris_obj.classMethodValue("Graph.KG.GraphKey", "ForIndex", "")

    for key, label in ((key_a, GRAPH_A), (key_default, "the default graph")):
        stored = iris_obj.getString("KG", "deg2p_exact", key, HOP_SRC, PRED)
        assert stored is not None and int(stored) >= 1, (
            f"^KG(\"deg2p_exact\", {key!r}, ...) holds no exact count for "
            f"{label}: {stored!r}"
        )


# --- T056 / SC-010: a bare `ivg.retrieve` through GraphQL -----------------------


@pytest.fixture
def retrieve_env(iris_connection):
    """The default graph, populated for the two-argument `ivg.retrieve`.

    That call takes its BM25 index name (`default`) and its label filter (`*`) from
    the defaults, and embeds its query text. All three arms therefore have to be
    real: documents in `docs`, vectors in `kg_NodeEmbeddings` at the declared width,
    and an index named `default`.

    The embedder is a stub at the declared width. This namespace has no
    `%Embedding.Config` — the enterprise build cannot import `sentence_transformers`
    — and the point of the test is that the statement reaches the server, not that
    the server can embed.
    """
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    width = _default_route_width(iris_connection)
    engine.embedder = lambda _text: _vec(width, 0.31)

    engine.create_node(
        SHARED, labels=["Ivg230Shared"], properties={"name": f"{TERM} {TERM} shared"}
    )
    engine.create_node(
        ONLY_DEFAULT,
        labels=["Ivg230OnlyDefault"],
        properties={"name": f"{TERM} only default"},
    )
    engine.store_embedding(SHARED, _vec(width, 0.31))
    engine.store_embedding(ONLY_DEFAULT, _vec(width, 0.32))

    cursor = iris_connection.cursor()
    try:
        for node_id in (SHARED, ONLY_DEFAULT):
            cursor.execute(
                "INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES ('', ?, ?)",
                (node_id, f"{TERM} {TERM} document for {node_id}"),
            )
        iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    engine.bm25_build(DEFAULT_BM25, ["name"])

    yield engine, width

    with contextlib.suppress(Exception):
        engine.bm25_drop(DEFAULT_BM25)
    _wipe(iris_connection)


def _graphql(query: str, context: dict):
    """Execute one GraphQL document against the shipped schema.

    Its own event loop: `asyncio.get_event_loop()` raises once anything earlier in
    the session has closed the MainThread loop, which is how four GraphQL tests came
    to pass alone and fail in the full suite.
    """
    from api.gql.schema import schema

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(schema.execute(query, context_value=context))
    finally:
        loop.close()


RETRIEVE_DOC = """
query {
  executeCypher(query: "CALL ivg.retrieve('%s', 5) YIELD node, rrf_score RETURN node, rrf_score")
}
""" % TERM


def test_bare_retrieve_through_graphql(retrieve_env):
    """SC-010: `CALL ivg.retrieve('text', 5)` executes through the endpoint.

    The endpoint translated without an engine, so the vector arm had no way to
    obtain a query vector and fell back to IRIS's native `EMBEDDING(?, ?)` with the
    call's default config name — a blank — and the server refused the whole
    statement at Query Open:

        SQLCODE -280 <Embedding configuration error> %Embedding.Config ' ' does not
        exist.

    Both halves are asserted: the call answers when the endpoint has an engine, and
    the failure it produces without one is not that one.
    """
    engine, _width = retrieve_env

    result = _graphql(
        RETRIEVE_DOC, {"db_connection": engine.conn, "engine": engine}
    )
    assert result.errors is None, result.errors

    payload = result.data["executeCypher"]
    assert isinstance(payload, list), (
        f"the endpoint returned an error rather than rows: {payload}"
    )
    # `RETURN node` on a node variable projects the node, so the answer carries
    # node_id/node_labels/node_props rather than one `node` column.
    ids = {row["node_id"] for row in payload}
    assert ids, "a bare retrieve over a populated default graph answered nothing"
    assert SHARED in ids or ONLY_DEFAULT in ids, sorted(ids)

    # Without an engine in the context the endpoint builds its own, which this
    # namespace cannot embed with. That is a different failure, and it must not be
    # the -280 one: an endpoint that still hardcodes the native call would report
    # the missing config no matter what is in the context.
    bare = _graphql(RETRIEVE_DOC, {"db_connection": engine.conn})
    assert bare.errors is None, bare.errors
    bare_payload = bare.data["executeCypher"]
    text = str(bare_payload)
    assert "-280" not in text and "%Embedding.Config" not in text, (
        "the endpoint still asks the server to embed with a config named ' ': "
        f"{text}"
    )
