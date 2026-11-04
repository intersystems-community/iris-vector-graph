"""4.6.0 R181: `store_node` writes the `id` property, as `create_node` and `bulk_create_nodes` do.

`store_node('x', properties={...})` wrote no `id` row, so `WHERE n.id = 'x'` found nothing and `RETURN n.id` was null
(a pattern's `{id: 'x'}` compares the node id). It now writes `id = node_id` when the node has none: a caller's own `id` in `properties` wins, and an `id` the
node already holds (another value given to `create_node`) is kept, so a later `store_node` never rewrites it. A None
value removes the property rather than storing the text 'None'.

The statements are pinned here; tests/e2e/test_460_store_node_id_e2e.py checks the stored graph and the Cypher reads.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from iris_vector_graph import IRISGraphEngine


def _eng():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    # pin the post-214 (graph-scoped) branch rather than relying on the attribute default
    eng._nodes_has_graph_id = True
    eng._children_have_graph_id = True
    return eng, cursor


def _prop_writes(cursor):
    out = []
    for call in cursor.execute.call_args_list:
        sql = call.args[0]
        params = call.args[1] if len(call.args) > 1 else []
        if "rdf_props" in sql and sql.lstrip().upper().startswith("INSERT"):
            out.append((sql, list(params)))
    return out


def test_store_node_writes_the_id_property_when_missing():
    eng, cursor = _eng()
    assert eng.store_node("x", properties={"name": "X"}, labels=["L"]) is True
    id_writes = [(s, p) for s, p in _prop_writes(cursor) if "id" in p]
    assert len(id_writes) == 1, _prop_writes(cursor)
    sql, params = id_writes[0]
    # guarded: an id the node already holds is kept
    assert "NOT EXISTS" in sql and 'graph_id = ? AND s = ? AND "key" = ?' in sql, sql
    assert params == ["", "x", "id", "x", "", "x", "id"], params


def test_store_node_with_no_properties_still_writes_id():
    eng, cursor = _eng()
    eng.store_node("x")
    assert [p for _, p in _prop_writes(cursor)] == [["", "x", "id", "x", "", "x", "id"]]


def test_store_node_in_a_named_graph_scopes_the_id_row():
    eng, cursor = _eng()
    eng.store_node("x", graph="g1")
    assert [p for _, p in _prop_writes(cursor)] == [["g1", "x", "id", "x", "g1", "x", "id"]]


def test_a_caller_id_is_written_as_given_and_not_twice():
    eng, cursor = _eng()
    eng.store_node("x", properties={"id": "custom"})
    writes = _prop_writes(cursor)
    assert [p for _, p in writes] == [["", "x", "id", "custom"]], writes


def test_a_none_value_removes_the_property_instead_of_storing_none():
    eng, cursor = _eng()
    eng.store_node("x", properties={"gone": None, "kept": 1})
    writes = _prop_writes(cursor)
    assert not any("None" in p for _, p in writes), writes
    assert any(p == ["", "x", "kept", "1"] for _, p in writes), writes
    deletes = [c.args[1] for c in cursor.execute.call_args_list if c.args[0].lstrip().startswith("DELETE")]
    assert ["", "x", "gone"] in [list(d) for d in deletes], deletes


def test_the_legacy_unscoped_branch_writes_id_too():
    eng, cursor = _eng()
    eng._nodes_has_graph_id = False
    eng._children_have_graph_id = False
    eng.store_node("x")
    assert [p for _, p in _prop_writes(cursor)] == [["x", "id", "x", "x", "id"]]
