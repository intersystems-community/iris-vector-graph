"""Spec 231 FR-017: the engine's FHIR-graph methods are thin callers of Graph.KG.FHIRGraph.

The work runs in ObjectScript (plan.md, "Where the work runs"), so what Python owns is
the argument shape, validation before any round trip, and turning the JSON reply into
a dict or an exception.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

G = "fhir:IVGFHIR:X0001"


def _eng(reply):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    native.classMethodValue.return_value = reply if isinstance(reply, str) else json.dumps(reply)
    patcher = patch.object(eng, "_iris_obj", return_value=native)
    patcher.start()
    return eng, native, patcher


@pytest.fixture
def eng_ok():
    eng, native, p = _eng({"status": "ok"})
    yield eng, native
    p.stop()


def _call(native):
    return native.classMethodValue.call_args.args


class TestRegister:
    def test_defaults(self, eng_ok):
        eng, native = eng_ok
        native.classMethodValue.return_value = json.dumps({"status": "registered", "graph_id": G})
        out = eng.fhir_graph_register()
        assert _call(native) == ("Graph.KG.FHIRGraph", "Register", "", "[]", 60, "")
        assert out["graph_id"] == G

    def test_arguments(self, eng_ok):
        eng, native = eng_ok
        eng.fhir_graph_register(
            endpoint="/csp/healthshare/ivgfhir/fhir/r4",
            denylist=["Provenance.target", "AuditEvent.entity"],
            interval_s=120,
        )
        args = _call(native)
        assert args[2] == "/csp/healthshare/ivgfhir/fhir/r4"
        assert json.loads(args[3]) == ["Provenance.target", "AuditEvent.entity"]
        assert args[4] == 120

    @pytest.mark.parametrize("bad", ["Provenance", "Provenance.", ".target", "a.b.c", 7])
    def test_denylist_entries_are_type_dot_param(self, eng_ok, bad):
        eng, native = eng_ok
        with pytest.raises(ValueError):
            eng.fhir_graph_register(denylist=[bad])
        native.classMethodValue.assert_not_called()

    @pytest.mark.parametrize("bad", [0, 59, -1])
    def test_interval_at_least_one_minute(self, eng_ok, bad):
        """Task Manager granularity is one minute (plan.md, Risks)."""
        eng, native = eng_ok
        with pytest.raises(ValueError):
            eng.fhir_graph_register(interval_s=bad)
        native.classMethodValue.assert_not_called()


class TestGraphOps:
    @pytest.mark.parametrize(
        "method, cls_method",
        [
            ("fhir_graph_rebuild", "Rebuild"),
            ("fhir_graph_sync", "SyncOnce"),
            ("fhir_graph_status", "Status"),
            ("fhir_graph_unschedule", "Unschedule"),
        ],
    )
    def test_one_graph_argument(self, eng_ok, method, cls_method):
        eng, native = eng_ok
        getattr(eng, method)(G)
        assert _call(native) == ("Graph.KG.FHIRGraph", cls_method, G)

    def test_schedule(self, eng_ok):
        eng, native = eng_ok
        eng.fhir_graph_schedule(G, interval_s=180)
        assert _call(native) == ("Graph.KG.FHIRGraph", "Schedule", G, 180)

    def test_schedule_default_interval_is_the_registered_one(self, eng_ok):
        eng, native = eng_ok
        eng.fhir_graph_schedule(G)
        assert _call(native) == ("Graph.KG.FHIRGraph", "Schedule", G, 0)

    @pytest.mark.parametrize("bad", [None, "", "0", "a\x01b"])
    def test_graph_is_required_and_validated(self, eng_ok, bad):
        eng, native = eng_ok
        with pytest.raises(ValueError):
            eng.fhir_graph_rebuild(bad)
        native.classMethodValue.assert_not_called()

    def test_busy_is_returned_not_raised(self):
        eng, native, p = _eng({"status": "busy"})
        try:
            assert eng.fhir_graph_sync(G) == {"status": "busy"}
        finally:
            p.stop()

    def test_error_raises_with_the_server_message(self):
        from iris_vector_graph.exceptions import FHIRGraphError

        eng, native, p = _eng({"status": "error", "error": "not a JsonAdvSQL repository"})
        try:
            with pytest.raises(FHIRGraphError, match="JsonAdvSQL"):
                eng.fhir_graph_rebuild(G)
        finally:
            p.stop()


class TestGraphId:
    def test_derivation(self):
        from iris_vector_graph._engine.fhir_graph import fhir_graph_id

        assert fhir_graph_id("IVGFHIR", "HSFHIR.X0001.R") == G
        assert fhir_graph_id("IVGFHIR", "X0001") == G

    def test_is_a_valid_graph_name(self):
        from iris_vector_graph._engine.fhir_graph import fhir_graph_id
        from iris_vector_graph._validate import validate_graph_name

        assert validate_graph_name(fhir_graph_id("IVGFHIR", "X0001")) == G
