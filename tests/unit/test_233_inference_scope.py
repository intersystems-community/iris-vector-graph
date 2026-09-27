"""Spec 233 FR-015: `materialize_inference` reads one graph and indexes what it writes.

Two defects the live E2E (tests/e2e/test_233_inference_adjacency_e2e.py) can only
half-see. The domain/range and owl rules read `SELECT s, p, o_id FROM rdf_edges ...
LIMIT 50000` with no graph predicate, so one graph's rules ran over another's edges,
and a large namespace hid it behind the LIMIT. And inferred rows went in over SQL
with no `^KG` write, after `import_rdf` had already run BuildKG, so concept expansion
never saw them. Retraction has to take back what inference wrote. These tests read
the SQL and the adjacency calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph._validate import graph_index_key

SUB = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


class _Cursor:
    def __init__(self, inferred=()):
        self.inferred = list(inferred)
        self.calls = []
        self._last = ("", [])
        self.rowcount = 0
        self.description = []

    def execute(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        self._last = (sql, list(params or []))

    def fetchall(self):
        sql, params = self._last
        if "SELECT s, o_id" in sql and params and params[0] == SUB:
            return [("a", "b"), ("b", "c")]
        if "SELECT s, p, o_id" in sql and "LIKE" in sql:
            return self.inferred
        return []

    def fetchone(self):
        return (0,)

    def close(self):
        pass


@pytest.fixture
def eng():
    conn = MagicMock()
    cursor = _Cursor()
    conn.cursor.return_value = cursor
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    with patch.object(engine, "_iris_obj", return_value=native):
        yield engine, cursor, native


@pytest.mark.parametrize("rules", ["rdfs", "owl"])
@pytest.mark.parametrize("graph", ["g1", None])
def test_every_read_is_graph_scoped(eng, rules, graph):
    engine, cursor, _ = eng
    engine.materialize_inference(rules=rules, graph=graph)
    reads = [sql for sql, _ in cursor.calls if sql.lstrip().upper().startswith("SELECT") and "rdf_edges" in sql]
    assert reads
    assert [sql for sql in reads if "graph_id" not in sql] == []
    assert [sql for sql in reads if "LIMIT" in sql.upper()] == []


@pytest.mark.parametrize("graph", ["g1", None])
def test_inferred_edges_written_to_adjacency(eng, graph):
    engine, _, native = eng
    assert engine.materialize_inference(graph=graph) == {"inferred": 1}
    writes = [c.args for c in native.classMethodVoid.call_args_list if c.args[:2] == ("Graph.KG.EdgeScan", "WriteAdjacency")]
    assert writes == [("Graph.KG.EdgeScan", "WriteAdjacency", "a", SUB, "c", "1.0", graph_index_key(graph))]


@pytest.mark.parametrize("graph", ["g1", None])
def test_retract_takes_inferred_edges_out_of_adjacency(graph):
    conn = MagicMock()
    conn.cursor.return_value = _Cursor(inferred=[("a", SUB, "c")])
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    with patch.object(engine, "_iris_obj", return_value=native):
        engine.retract_inference(graph=graph)
    kills = [c.args for c in native.classMethodVoid.call_args_list if c.args[:2] == ("Graph.KG.EdgeScan", "DeleteAdjacency")]
    assert kills == [("Graph.KG.EdgeScan", "DeleteAdjacency", "a", SUB, "c", graph_index_key(graph))]
