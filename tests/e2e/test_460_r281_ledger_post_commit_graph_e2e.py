"""4.6.0 R281: a ledger commit's post_commit_properties write the revision's graph only.

The ledger keeps nodes in the default graph (GraphLedger.redact: "The ledger records nodes graph-blind, in the default
graph"), and `_apply_post_commit_properties` wrote each property as `DELETE ... WHERE s = ? AND "key" = ?` then an
INSERT with no graph_id: the DELETE took the property from every graph holding a node of that id, the INSERT wrote it
to the default graph. Now both are scoped to the default graph, the revision's.

Two graphs share the node id r281n: tag "old" in the default graph, tag "keep" in graph g281. A commit whose
post_commit_properties set r281n's tag to "new" leaves rdf_props holding ('', "new") and ('g281', "keep").

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up). The ledger is enabled for the
test, and disabled after it if it was not enabled before.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODE, GRAPH = "r281n", "g281"


def _tags(conn, schema):
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT graph_id, val FROM {schema}.rdf_props WHERE s = ? AND "key" = ?', [NODE, "tag"])
        return sorted((str(g or ""), str(v)) for g, v in cur.fetchall())
    finally:
        cur.close()


def _clean(engine):
    for nid, graph in ((NODE, None), (NODE, GRAPH), ("r281x", None)):
        engine.delete_node(nid, graph=graph)


@pytest.fixture(scope="module")
def world():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    engine = IRISGraphEngine(conn, embedding_dimension=alt.DIM, namespace=alt.NS, schema_prefix=schema)
    _clean(engine)
    engine.create_node(NODE, properties={"tag": "old"})
    engine.create_node(NODE, properties={"tag": "keep"}, graph=GRAPH)
    was_enabled = bool(engine.ledger.meta().get("enabled"))
    if not was_enabled:
        engine.ledger.enable()
    yield {"engine": engine, "conn": conn, "schema": schema}
    try:
        if not was_enabled:
            engine.ledger.disable()
        _clean(engine)
    finally:
        conn.close()


def test_the_fixture_holds_one_tag_per_graph(world):
    assert _tags(world["conn"], world["schema"]) == [("", "old"), (GRAPH, "keep")]


def test_post_commit_properties_write_the_default_graph_only(world):
    from iris_vector_graph.ledger.changeset import Changeset

    cs = Changeset(actor="r281", actor_type="test", post_commit_properties={NODE: {"tag": "new"}})
    cs.upsert_node("r281x")
    result = world["engine"].ledger.commit(cs)
    assert result.post_commit_applied and result.post_commit_error is None
    assert _tags(world["conn"], world["schema"]) == [("", "new"), (GRAPH, "keep")]
