"""4.6.0 R290: a ledger commit's node operations write the revision's graph, the default graph, and no other.

The ledger keeps nodes in the default graph (R281; GraphLedger.redact). Graph.KG.LedgerApply matched node rows by id
alone: set_prop's lookup and UPDATE (a value in another graph was the "current" one, and the UPDATE rewrote it in
every graph), DeleteNodeRows' deletes of labels, properties, vectors and the node row, the incident-relationship
lookup before a delete (a named graph's edge refused a strict delete, and a detach delete removed it), and the label
and property lookups of add_label, remove_label and remove_prop.

The graphs, worked by hand. The id r290n is in both. In the default graph: label L290, tag "old". In graph g290: labels
L290 and K290, tag "keep", only_g "gval", and an edge r290n -r290rel-> r290m (both in g290). Then, one commit each:

- set tag "new": the default graph's tag is "new", g290's still "keep".
- set only_g "x": the default graph gains only_g "x" (it had none), g290's stays "gval".
- add label K290: the default graph gains it (it had none); g290 still holds its own.
- remove property tag: the default graph's goes, g290's "keep" stays.
- delete r290n (strict): it has no relationship in the default graph, so the commit is applied; the default graph's
  node, label and properties are gone, and g290's node, labels, properties and edge are all still there.

The namespace is `IVG_NAMESPACE` (an IVG460*/IVG262* scratch namespace with IVG set up). The ledger is enabled for the
test, and disabled after it if it was not enabled before.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

NODE, OTHER, GRAPH = "r290n", "r290m", "g290"


def _state(conn, schema):
    def q(sql):
        return sorted(tuple("" if v is None else str(v) for v in r) for r in alt.rows(conn, sql, [NODE]))

    return {
        "nodes": q(f"SELECT graph_id FROM {schema}.nodes WHERE node_id = ?"),
        "labels": q(f"SELECT graph_id, label FROM {schema}.rdf_labels WHERE s = ?"),
        "props": q(f'SELECT graph_id, "key", val FROM {schema}.rdf_props WHERE s = ? AND "key" <> \'id\''),
        "edges": q(f"SELECT graph_id, p, o_id FROM {schema}.rdf_edges WHERE s = ?"),
    }


def _clean(engine):
    for nid, graph in ((NODE, None), (NODE, GRAPH), (OTHER, GRAPH)):
        engine.delete_node(nid, graph=graph)


@pytest.fixture(scope="module")
def world():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    native = iris.createIRIS(conn)
    schema = native.get("^IVG.Deploy", "schema") or "Graph_KG"
    engine = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    _clean(engine)
    engine.create_node(NODE, labels=["L290"], properties={"tag": "old"})
    engine.create_node(NODE, labels=["L290", "K290"], properties={"tag": "keep", "only_g": "gval"}, graph=GRAPH)
    engine.create_node(OTHER, graph=GRAPH)
    engine.create_edge(NODE, "r290rel", OTHER, graph=GRAPH)
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


G290 = {
    "labels": [(GRAPH, "K290"), (GRAPH, "L290")],
    "props": [(GRAPH, "only_g", "gval"), (GRAPH, "tag", "keep")],
}


def _commit(world, build):
    from iris_vector_graph.ledger.changeset import Changeset

    cs = Changeset(actor="r290", actor_type="test")
    build(cs)
    world["engine"].ledger.commit(cs)  # a refused commit raises (ledger.errors)
    return _state(world["conn"], world["schema"])


def test_1_the_fixture_holds_the_id_in_both_graphs(world):
    assert _state(world["conn"], world["schema"]) == {
        "nodes": [("",), (GRAPH,)],
        "labels": [("", "L290")] + G290["labels"],
        "props": [("", "tag", "old")] + G290["props"],
        "edges": [(GRAPH, "r290rel", OTHER)],
    }


def test_2_set_property_writes_the_default_graph_only(world):
    state = _commit(world, lambda cs: cs.set_property(NODE, "tag", "new"))
    assert state["props"] == [("", "tag", "new")] + G290["props"]


def test_3_a_property_only_another_graph_holds_is_new_in_the_default_graph(world):
    state = _commit(world, lambda cs: cs.set_property(NODE, "only_g", "x"))
    assert state["props"] == [("", "only_g", "x"), ("", "tag", "new")] + G290["props"]


def test_4_a_label_only_another_graph_holds_is_added_to_the_default_graph(world):
    state = _commit(world, lambda cs: cs.add_label(NODE, "K290"))
    assert state["labels"] == [("", "K290"), ("", "L290")] + G290["labels"]


def test_5_remove_property_removes_the_default_graphs_only(world):
    state = _commit(world, lambda cs: cs.remove_property(NODE, "tag"))
    assert state["props"] == [("", "only_g", "x")] + G290["props"]


def test_6_delete_node_deletes_the_default_graphs_node_only(world):
    state = _commit(world, lambda cs: cs.delete_node(NODE))
    assert state == {
        "nodes": [(GRAPH,)],
        "labels": G290["labels"],
        "props": G290["props"],
        "edges": [(GRAPH, "r290rel", OTHER)],
    }
