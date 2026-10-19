"""Spec 252, T021 / T034: with the adjacency functional index declared, the engine's edge writers write rows only.

The index (Graph.KG.AdjIndex) maintains ^KG from the row; a writer that also calls WriteAdjacency does the
work twice. Without the index (a DDL-only namespace, an install ensure_indexes has not reached) the writers
keep writing ^KG themselves. An edge's weight lives in its row's qualifiers (FR-015), so the index, a
rebuild and Cypher's r.weight all see the same number.
"""

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(indexed, rows=()):
    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.return_value = None
    cur.fetchall.return_value = list(rows)
    eng = IRISGraphEngine(conn, store=MagicMock())
    eng._adjacency_indexed_cached = indexed  # the probe's answer, explicit (no MagicMock truthiness)
    native = MagicMock()
    eng._iris_obj = lambda: native
    return eng, cur, native


_ADJACENCY_METHODS = ("WriteAdjacency", "DeleteAdjacency", "DeleteAdjacencyAllGraphs")


def _adjacency_calls(native):
    return [c.args[1] for c in native.classMethodVoid.call_args_list if c.args[1] in _ADJACENCY_METHODS]


def _sql(cur):
    return [str(c.args[0]) for c in cur.execute.call_args_list]


def _edge_insert_params(cur):
    for c in cur.execute.call_args_list:
        sql = str(c.args[0])
        if "INSERT INTO" in sql and "rdf_edges" in sql:
            return c.args[1]
    raise AssertionError("no rdf_edges INSERT was executed")


def test_create_edge_writes_the_row_only_when_indexed():
    eng, cur, native = _engine(True)
    assert eng.create_edge("a", "R", "b") is True
    assert _adjacency_calls(native) == []
    assert any("INSERT INTO" in s and "rdf_edges" in s for s in _sql(cur))


def test_create_edge_keeps_writing_adjacency_without_the_index():
    eng, cur, native = _engine(False)
    eng.create_edge("a", "R", "b")
    assert _adjacency_calls(native) == ["WriteAdjacency"]


@pytest.mark.parametrize("indexed", [True, False])
def test_create_edge_weight_goes_into_qualifiers(indexed):
    eng, cur, _ = _engine(indexed)
    eng.create_edge("a", "R", "b", weight=2.5, qualifiers={"since": 2020})
    quals = json.loads(_edge_insert_params(cur)[3])
    assert quals == {"since": 2020, "weight": 2.5}


def test_a_default_weight_adds_nothing_to_qualifiers():
    eng, cur, _ = _engine(True)
    eng.create_edge("a", "R", "b")
    assert _edge_insert_params(cur)[3] is None


def test_set_edge_weight_updates_the_row():
    eng, cur, native = _engine(True, rows=[(7, '{"since": 2020}')])
    assert eng.set_edge_weight("a", "R", "b", 4.0) is True
    updates = [c for c in cur.execute.call_args_list if str(c.args[0]).startswith("UPDATE")]
    assert updates and json.loads(updates[0].args[1][0]) == {"since": 2020, "weight": 4.0}
    assert _adjacency_calls(native) == []


def test_set_edge_weight_on_a_missing_edge_writes_nothing():
    eng, cur, native = _engine(True, rows=[])
    assert eng.set_edge_weight("a", "R", "b", 4.0) is False
    assert not [s for s in _sql(cur) if s.startswith("UPDATE")]
    assert _adjacency_calls(native) == []


def test_delete_edge_deletes_the_row_only_when_indexed():
    eng, cur, native = _engine(True)
    eng.delete_edge("a", "R", "b")
    assert _adjacency_calls(native) == []
    assert any(s.startswith("DELETE") for s in _sql(cur))
