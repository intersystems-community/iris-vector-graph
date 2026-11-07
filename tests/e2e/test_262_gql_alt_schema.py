"""4.6.0 R079 (spec 262 FR-007): the auto-generated GraphQL API reads the engine's IVG Schema.

`iris_vector_graph.gql` named `Graph_KG` in its introspection (`GQLGraphEngine`) and its resolvers (stats, nodes by
label, outgoing and incoming), so on a namespace whose IVG Schema is `IVGALT` the schema generation found no labels
and every query read tables that are not there. IVG262ALT with schema `IVGALT`, set up anew when it is not.
Scratch namespace: `IVG_NS_IVG262ALT` (default IVG262ALT). The file drops and rebuilds it (4.6.0 R191): point it at
a namespace no other run is using.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

strawberry = pytest.importorskip("strawberry", reason="strawberry is a dev dependency")

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS, SCHEMA = os.environ.get("IVG_NS_IVG262ALT", ""), "IVGALT"
if not NS:  # the E2E gate names one per run (tests/e2e/gate.py GROUPS); never a fixed default
    pytest.skip("set IVG_NS_IVG262ALT to a scratch namespace of this run", allow_module_level=True)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.gql import create_app

    ns262.recreate(NS)
    conn = ns262.connect(NS)
    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, schema_prefix=SCHEMA, native_cypher=False)
    engine.initialize_schema()
    engine.create_node("gqa:1", labels=["GqlPerson"], properties={"name": "Ana"})
    engine.create_node("gqa:2", labels=["GqlPerson"], properties={"name": "Ben"})
    engine.create_node("gqa:3", labels=["GqlCity"], properties={"title": "Oslo"})
    engine.create_edge("gqa:1", "KNOWS", "gqa:2")
    engine.create_edge("gqa:1", "LIVES_IN", "gqa:3")
    conn.commit()
    assert "GRAPH_KG" not in {s.upper() for s in ns262.schemas(conn)}
    yield TestClient(create_app(engine))
    conn.close()


def _q(client, query, variables=None):
    response = client.post("/graphql", json={"query": query, "variables": variables or {}})
    assert response.status_code == 200, response.text
    data = response.json()
    assert "errors" not in data, data.get("errors")
    return data["data"]


def test_stats_count_the_ivgalt_tables(client):
    assert _q(client, "{ stats { nodeCount edgeCount labelCount } }")["stats"] == {
        "nodeCount": 3, "edgeCount": 2, "labelCount": 2}


def test_the_generated_schema_has_the_labels_and_their_properties(client):
    data = _q(client, '{ nodes(label: "GqlPerson") { id ... on GqlPerson { name } } }')
    assert sorted((n["id"], n["name"]) for n in data["nodes"]) == [("gqa:1", "Ana"), ("gqa:2", "Ben")]


def test_outgoing_and_incoming_read_ivgalt_edges(client):
    out = _q(client, '{ node(id: "gqa:1") { outgoing { predicate targetId } } }')["node"]["outgoing"]
    assert sorted((r["predicate"], r["targetId"]) for r in out) == [("KNOWS", "gqa:2"), ("LIVES_IN", "gqa:3")]
    inc = _q(client, '{ node(id: "gqa:3") { incoming { predicate targetId } } }')["node"]["incoming"]
    assert [(r["predicate"], r["targetId"]) for r in inc] == [("LIVES_IN", "gqa:1")]
