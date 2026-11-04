"""4.6.0 R102: `POST /admin/schema/init` builds the vector column at the `embedding_dimension` it is sent.

The route called `initialize_schema()` on the server's engine and never read `embedding_dimension`. The server builds
its engine with no dimension (`cypher_api._make_engine`), so setup refused with "embedding_dimension must be set" and
the route answered 500 whatever the request said; `ivg schema init --embedding-dim N` could not work.

IVG262FX8D, made anew, so the tables do not exist before the call. After it, the vector column is VECTOR(DOUBLE, 4):
a 4-wide vector is stored and a 3-wide one is refused by IRIS (memory: vector width enforced at INSERT).
Scratch namespace: `IVG_NS_IVG262FX8D` (default IVG262FX8D). The file drops and rebuilds it (4.6.0 R191): point it
at a namespace no other run is using.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NS_IVG262FX8D", "IVG262FX8D")


@pytest.fixture(scope="module")
def served():
    from fastapi.testclient import TestClient

    import iris_vector_graph.cypher_api as api
    from iris_vector_graph import IRISGraphEngine

    ns262.recreate(NS)
    conn = ns262.connect(NS)
    engine = IRISGraphEngine(conn, namespace=NS)  # as _make_engine builds it: no dimension
    saved = api._engine_cache
    api._engine_cache = engine
    try:
        yield TestClient(app=api.app, raise_server_exceptions=False), engine, conn
    finally:
        api._engine_cache = saved
        conn.close()


def test_the_route_builds_the_vector_column_at_the_requested_width(served):
    client, engine, conn = served
    resp = client.post("/admin/schema/init", json={"embedding_dimension": 4, "auto_deploy_objectscript": True})
    assert resp.status_code == 200, resp.text
    assert engine.embedding_dimension == 4

    engine.create_node("v4")
    assert engine.store_embedding("v4", [1.0, 0.0, 0.0, 0.0])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings WHERE node_id = 'v4'")
    assert cur.fetchone()[0] == 1
    with pytest.raises(Exception):
        cur.execute("INSERT INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id, emb) VALUES ('', 'v3', TO_VECTOR('1,0,0', DOUBLE))")
