"""Spec 235 US7: the interpretation marker on the Python side, with IRIS mocked. The
live re-derivation is in tests/e2e/test_235_reinterpret_e2e.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.fhir_graph import _interp_stale
from iris_vector_graph.engine import IRISGraphEngine

G = "fhir:IVGFHIR:X0001"


class TestInterpStale:
    def test_none_is_stale(self):
        assert _interp_stale(None, 1) is True

    def test_empty_is_stale(self):
        """ObjectScript's NULL reaches Python as "" in some replies."""
        assert _interp_stale("", 1) is True

    def test_lower_is_stale(self):
        assert _interp_stale(1, 2) is True

    def test_equal_is_current(self):
        assert _interp_stale(1, 1) is False

    def test_higher_raises(self):
        """A graph written by a newer IVG: re-deriving it with older rules would
        silently downgrade it."""
        with pytest.raises(RuntimeError, match="newer"):
            _interp_stale(3, 1)


def _engine(replies=None):
    conn = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    conn.reset_mock()  # the constructor's own SQL
    native = MagicMock()
    if replies is not None:
        native.classMethodValue.side_effect = lambda cls, op, *a: json.dumps(replies[op])
    return engine, conn, native


class TestEnsureColumns:
    def _run(self, probe_error):
        engine, conn, _ = _engine()
        cur = MagicMock()
        seen = []

        def execute(sql, *a):
            seen.append(sql)
            if sql.startswith("SELECT interp_version") and probe_error:
                raise RuntimeError("SQLCODE -29 Field 'INTERP_VERSION' not found")

        cur.execute.side_effect = execute
        return engine._ensure_fhir_graph_columns(cur), seen

    def test_absent_column_added(self):
        ok, seen = self._run(probe_error=True)
        assert ok is True
        alters = [s for s in seen if s.startswith("ALTER")]
        assert alters == ["ALTER TABLE Graph_KG.fhir_graphs ADD COLUMN interp_version INTEGER"]

    def test_present_column_left(self):
        ok, seen = self._run(probe_error=False)
        assert ok is True
        assert not [s for s in seen if s.startswith("ALTER")]

    def test_already_exists_is_success(self):
        """Two initialisers racing: the loser's ALTER reports the column exists."""
        engine, _, _ = _engine()
        cur = MagicMock()

        def execute(sql, *a):
            if sql.startswith("SELECT"):
                raise RuntimeError("not found")
            raise RuntimeError("Field 'interp_version' already exists")

        cur.execute.side_effect = execute
        assert engine._ensure_fhir_graph_columns(cur) is True

    def test_missing_table_is_not_an_error(self):
        """A namespace without FHIR graphs: nothing to add, and no warning spam."""
        engine, _, _ = _engine()
        cur = MagicMock()
        cur.execute.side_effect = RuntimeError("Table 'GRAPH_KG.FHIR_GRAPHS' not found")
        assert engine._ensure_fhir_graph_columns(cur) is False

    def test_called_by_initialize_schema(self):
        from iris_vector_graph._engine import schema

        src = open(schema.__file__).read()
        init = src[src.index("def initialize_schema"):]
        assert "self._ensure_fhir_graph_columns(cursor)" in init


class TestReinterpret:
    def test_ok(self):
        reply = {
            "status": "ok",
            "graph": G,
            "resources": 12,
            "interpretation": {"full_rederivation": True, "interpretation_version": 1},
        }
        engine, _, native = _engine({"Reinterpret": reply})
        with patch.object(engine, "_iris_obj", return_value=native):
            assert engine.fhir_reinterpret(G) == reply
        native.classMethodValue.assert_called_once_with("Graph.KG.FHIRGraph", "Reinterpret", G)

    def test_busy(self):
        engine, _, native = _engine({"Reinterpret": {"status": "busy", "graph_id": G}})
        with patch.object(engine, "_iris_obj", return_value=native):
            assert engine.fhir_reinterpret(G) == {"status": "busy", "graph_id": G}

    def test_bad_graph(self):
        engine, _, native = _engine({})
        with patch.object(engine, "_iris_obj", return_value=native):
            with pytest.raises(ValueError):
                engine.fhir_reinterpret("")
        native.classMethodValue.assert_not_called()


class TestStatus:
    def _status(self, version):
        reply = {"status": "ok", "graph_id": G, "interpretation_version": version, "interpretation_current": 1}
        engine, _, native = _engine({"Status": reply})
        with patch.object(engine, "_iris_obj", return_value=native):
            return engine.fhir_graph_status(G)

    def test_current(self):
        out = self._status(1)
        assert out["interpretation_version"] == 1
        assert out["interpretation_stale"] is False

    def test_null(self):
        out = self._status(None)
        assert out["interpretation_version"] is None
        assert out["interpretation_stale"] is True


class TestCoverageStale:
    def test_stale_from_marker(self):
        replies = {
            "IndexedTokenParams": {"status": "ok", "used": ["code"], "dropped": []},
            "CoverageReport": {
                "status": "ok",
                "graph": G,
                "interpretation_version": None,
                "interpretation_current": 1,
                "stale": True,
                "types": {},
                "resolution": {"by_system": {}},
                "linked_patients": 0,
            },
            "LinkReport": {"status": "ok", "graph": G},
        }
        engine, _, native = _engine(replies)
        with patch.object(engine, "_iris_obj", return_value=native):
            rep = engine.fhir_coverage_report(G)
        assert rep["stale"] is True
        assert rep["interpretation_version"] is None
