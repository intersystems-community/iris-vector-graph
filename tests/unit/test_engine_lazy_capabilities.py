"""A fresh engine knows what the namespace has deployed without initialize_schema().

`IRISGraphEngine(conn)` used to leave `capabilities.objectscript_deployed = False`
until `initialize_schema()` ran, so `bulk_ingest_edges` on a fully deployed
namespace silently took the per-row Python path: 1.5K edges/s against 9-20K through
`Graph.KG.EdgeScan.BulkIngestEdgesSQL` (SNB SF0.1: 1,095 s against 203 s). And
`initialize_schema()` is no fix for a caller: on a loaded graph it also builds the
full-text index (24 minutes per side at SF1).

Capabilities are now read from `%Dictionary` on first use, once. Every test here
says what the dictionary answers: a mock connection is never detected as deployed by
accident, because a count that is not a number is not evidence of a class.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.capabilities import IRISCapabilities
from iris_vector_graph.engine import IRISGraphEngine

_SENTINEL = "Graph.KG.PageRank"


def _engine(pagerank_count, graphops_count=0):
    """An engine whose dictionary query answers `pagerank_count` for the sentinel class.

    Every other query answers (0,), so the vector-dtype probe in __init__ and the
    fallback write paths see an empty namespace.
    """
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    state = {"sql": ""}
    executed = []

    def execute(sql, *args, **kwargs):
        state["sql"] = sql
        executed.append(sql)

    def fetchone():
        sql = state["sql"]
        if "%Dictionary.ClassDefinition" in sql and _SENTINEL in sql:
            return (pagerank_count,)
        if "%Dictionary.ClassDefinition" in sql and "GraphOperators" in sql:
            return (graphops_count,)
        return (0,)

    cursor.execute.side_effect = execute
    cursor.fetchone.side_effect = fetchone
    cursor.fetchall.return_value = []
    cursor.description = []
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    executed.clear()
    return eng, cursor, executed


def _dictionary_queries(executed):
    return [s for s in executed if "%Dictionary.ClassDefinition" in s]


class TestDetection:
    def test_construction_runs_no_detection(self):
        eng, _cursor, executed = _engine(1)
        assert _dictionary_queries(executed) == []

    def test_a_deployed_namespace_is_detected_on_first_use(self):
        eng, _cursor, executed = _engine(1, graphops_count=1)
        native = MagicMock()
        native.classMethodValue.return_value = 1
        with patch.object(eng, "_iris_obj", return_value=native):
            caps = eng.capabilities
        assert caps.objectscript_deployed is True
        assert caps.graphoperators_deployed is True
        assert caps.kg_built is True
        assert any(_SENTINEL in s for s in _dictionary_queries(executed))

    def test_detection_runs_once(self):
        eng, _cursor, executed = _engine(1)
        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            eng.capabilities
            first = len(_dictionary_queries(executed))
            eng.capabilities
            eng.capabilities.objectscript_deployed
        assert first > 0
        assert len(_dictionary_queries(executed)) == first

    def test_an_undeployed_namespace_is_detected_as_undeployed(self):
        eng, _cursor, _executed = _engine(0)
        native = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=native):
            caps = eng.capabilities
        assert caps.objectscript_deployed is False
        assert caps.kg_built is False
        # No class, so no ^KG marker to ask the server about.
        native.classMethodValue.assert_not_called()

    def test_a_mock_count_is_not_evidence_of_a_class(self):
        """An unconfigured MagicMock cursor answers MagicMock objects, which are truthy."""
        eng = IRISGraphEngine(MagicMock(), embedding_dimension=4)
        native = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=native):
            caps = eng.capabilities
        assert caps.objectscript_deployed is False
        assert caps.kg_built is False

    def test_a_failing_connection_reads_as_undeployed(self):
        eng, _cursor, _executed = _engine(1)
        eng.conn.cursor.side_effect = RuntimeError("connection closed")
        assert eng.capabilities.objectscript_deployed is False

    def test_assignment_replaces_detection(self):
        eng, _cursor, executed = _engine(0)
        eng.capabilities = IRISCapabilities(objectscript_deployed=True)
        assert eng.capabilities.objectscript_deployed is True
        assert _dictionary_queries(executed) == []

    def test_assigning_none_detects_again(self):
        eng, _cursor, executed = _engine(1)
        eng.capabilities = IRISCapabilities()
        assert eng.capabilities.objectscript_deployed is False
        eng.capabilities = None
        with patch.object(eng, "_iris_obj", return_value=MagicMock()):
            assert eng.capabilities.objectscript_deployed is True

    def test_detection_does_not_open_native_api_on_the_cursor_connection(self):
        """createIRIS on the connection that runs cursor DDL corrupts its parameter
        binding (see engine._iris_obj); the ^KG marker is read through _iris_obj."""
        eng, _cursor, _executed = _engine(1)
        native = MagicMock()
        native.classMethodValue.return_value = 0
        fake_iris = MagicMock()
        with patch.object(eng, "_iris_obj", return_value=native) as made, patch.dict(
            "sys.modules", {"iris": fake_iris}
        ):
            eng.capabilities
        fake_iris.createIRIS.assert_not_called()
        made.assert_called()
        methods = [c.args[1] for c in native.classMethodValue.call_args_list]
        assert "IsSet" in methods


class TestBulkIngestEdges:
    def test_fresh_engine_on_deployed_namespace_uses_server_loader(self):
        eng, cursor, executed = _engine(1)
        with patch.object(eng, "_iris_obj", return_value=MagicMock()), patch(
            "iris_vector_graph.schema._call_classmethod_large", return_value="2"
        ) as large:
            n = eng.bulk_ingest_edges(
                [{"s": "a", "p": "KNOWS", "o": "b"}, {"s": "b", "p": "KNOWS", "o": "c"}],
                auto_sync=False,
            )
        assert n == 2
        assert large.call_args.args[1:3] == ("Graph.KG.EdgeScan", "BulkIngestEdgesSQL")
        assert not [s for s in executed if s.lstrip().upper().startswith("INSERT")]

    def test_fresh_engine_on_undeployed_namespace_falls_back(self):
        eng, cursor, executed = _engine(0)
        with patch.object(eng, "_iris_obj", return_value=MagicMock()), patch(
            "iris_vector_graph.schema._call_classmethod_large"
        ) as large:
            eng.bulk_ingest_edges([{"s": "a", "p": "KNOWS", "o": "b"}], auto_sync=False)
        large.assert_not_called()
        assert [s for s in executed if "INSERT INTO Graph_KG.rdf_edges" in s]


class TestBulkCreateNodes:
    """The server-side node loader writes every property through the iFind index:
    ~40 nodes/s on SNB data, against ~1.6K/s on the Python path, which disables the
    indexes around the batch. Detection must not move nodes onto it."""

    _NODES = [{"id": "n1", "labels": ["L"], "properties": {"k": "v"}}]

    def test_fresh_engine_on_deployed_namespace_keeps_python_path(self):
        eng, _cursor, executed = _engine(1)
        with patch.object(eng, "_iris_obj", return_value=MagicMock()), patch(
            "iris_vector_graph.schema._call_classmethod_large"
        ) as large:
            eng.bulk_create_nodes(self._NODES)
        large.assert_not_called()
        assert _dictionary_queries(executed) == []

    def test_explicit_capabilities_keep_the_server_path(self):
        """What initialize_schema() (or a caller) sets is used as before."""
        eng, _cursor, _executed = _engine(1)
        eng.capabilities = IRISCapabilities(objectscript_deployed=True)
        with patch.object(eng, "_iris_obj", return_value=MagicMock()), patch(
            "iris_vector_graph.schema._call_classmethod_large", return_value="1"
        ) as large:
            created = eng.bulk_create_nodes(self._NODES)
        assert large.call_args.args[1:3] == ("Graph.KG.EdgeScan", "BulkIngestNodesSQL")
        assert created == ["n1"]


class TestCheckObjectscriptClassesIsStrict:
    @pytest.mark.parametrize("value", [MagicMock(), object(), None, "", "0", 0])
    def test_non_counts_and_zero_are_not_deployed(self, value):
        from iris_vector_graph.schema import GraphSchema

        cur = MagicMock()
        cur.fetchone.return_value = (value,)
        with patch("iris_vector_graph.schema._call_classmethod", return_value=1) as cm:
            caps = GraphSchema.check_objectscript_classes(cur)
        assert caps.objectscript_deployed is False
        assert caps.kg_built is False
        assert not [c for c in cm.call_args_list if c.args[2] == "IsSet"]

    @pytest.mark.parametrize("value", [1, 3, "1", True])
    def test_counts_are_deployed(self, value):
        from iris_vector_graph.schema import GraphSchema

        cur = MagicMock()
        cur.fetchone.return_value = (value,)
        with patch("iris_vector_graph.schema._call_classmethod", return_value=MagicMock()):
            caps = GraphSchema.check_objectscript_classes(cur)
        assert caps.objectscript_deployed is True
        # A MagicMock from the native call is not a set flag either.
        assert caps.kg_built is False
