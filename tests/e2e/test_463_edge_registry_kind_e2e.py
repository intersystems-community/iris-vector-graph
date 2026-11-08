"""4.6.3, live: every row `embedding_registry` holds for `kg_EdgeEmbeddings` says kind `edge`.

`kg_EdgeEmbeddings` is keyed (s, p, o_id): an edge route. 4.6.0 (R316) made setup's adoption record it as `edge` and
made `initialize_schema()` repair a row an earlier setup recorded as `node`. A row written any other way still took
the column's default, `node`: `set_embedding_identity(identity, "kg_EdgeEmbeddings")`, and an engine declaring a model
(`embedding_config`) claiming the table where no row was (its edge writes' `enforce_embedding_identity`, and setup's
own claim). The default graph then had a third node route, read in no order by `Graph.KG.PyOps`, until the next setup.
Each case reads the registry row back after the call.

Runs in the scratch namespace ``IVG_NAMESPACE`` names (IVG463*, IVG460* or IVG262*, IVG installed). It deletes and
rewrites that namespace's `kg_EdgeEmbeddings` registry row and runs `initialize_schema()` there.

    IVG_NAMESPACE=IVG463B pytest tests/e2e/test_463_edge_registry_kind_e2e.py
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    ns262.requires_container,
    pytest.mark.skipif(not NS.startswith(("IVG463", "IVG460", "IVG262")),
                       reason="IVG_NAMESPACE must name an IVG463*/IVG460*/IVG262* scratch namespace: it rewrites its "
                              "embedding registry"),
]

EDGE = "kg_EdgeEmbeddings"


@pytest.fixture
def conn():
    c = ns262.connect(NS)
    yield c
    c.close()


def _engine(conn, **kw):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=768, **kw)


def _kinds(conn) -> dict:
    """{(table_name, graph_id): kind} as the registry holds them, an absent kind read as the column's default."""
    return {(t, g): k for t, g, k in ns262.rows(
        conn, "SELECT table_name, COALESCE(graph_id, ''), COALESCE(kind, 'node') FROM Graph_KG.embedding_registry")}


def _drop_edge_row(conn) -> None:
    cur = conn.cursor()
    cur.execute(f"DELETE FROM Graph_KG.embedding_registry WHERE table_name = '{EDGE}'")
    conn.commit()
    assert (EDGE, "") not in _kinds(conn)


def test_setup_records_the_edge_table_as_an_edge_route(conn):
    _drop_edge_row(conn)
    _engine(conn).initialize_schema()
    assert _kinds(conn)[(EDGE, "")] == "edge"


def test_setup_repairs_a_row_recorded_as_a_node_route(conn):
    _engine(conn).initialize_schema()
    cur = conn.cursor()
    cur.execute(f"UPDATE Graph_KG.embedding_registry SET kind = 'node' WHERE table_name = '{EDGE}'")
    conn.commit()
    assert _kinds(conn)[(EDGE, "")] == "node"  # the wrong row, built
    _engine(conn).initialize_schema()
    kinds = _kinds(conn)
    assert kinds[(EDGE, "")] == "edge"
    assert kinds[("kg_NodeEmbeddings", "")] == "node"


def test_set_embedding_identity_on_the_edge_table_records_an_edge_route(conn):
    from iris_vector_graph.embedding_identity import EmbeddingIdentity

    e = _engine(conn)
    _drop_edge_row(conn)
    e.set_embedding_identity(
        EmbeddingIdentity(mechanism="external", model_key="m463", declared_config=None, dimension=768,
                          dtype="DOUBLE"),
        EDGE,
    )
    assert _kinds(conn)[(EDGE, "")] == "edge"


def test_a_declared_models_claim_on_the_edge_table_records_an_edge_route(conn):
    e = _engine(conn, embedding_config="m463-config")
    _drop_edge_row(conn)
    e._record_configured_embedding_identity(conn.cursor())
    assert _kinds(conn)[(EDGE, "")] == "edge"


def teardown_module(module):
    """Leave the namespace as setup leaves it: the edge row adopted, no model claimed."""
    c = ns262.connect(NS)
    try:
        cur = c.cursor()
        cur.execute("DELETE FROM Graph_KG.embedding_registry WHERE COALESCE(graph_id, '') = ''")
        c.commit()
        _engine(c).initialize_schema()
    finally:
        c.close()
