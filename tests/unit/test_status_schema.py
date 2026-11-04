"""Spec 262 T052: `status()` reports the namespace's IVG Schema and how many 4.5.x legacy objects remain, and
`report()` prints both (contracts/python-api.md, `engine.status()`; research R9)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.status import EngineStatus

from tests.unit.test_first_access import FakeNative, _conn


def test_the_status_carries_schema_and_legacy_objects():
    s = EngineStatus(schema="IVGALT", legacy_objects=3)
    assert s.schema == "IVGALT" and s.legacy_objects == 3
    assert EngineStatus().schema is None and EngineStatus().legacy_objects == 0


def test_report_prints_the_schema_and_the_legacy_count():
    text = EngineStatus(schema="IVGALT", legacy_objects=3).report()
    assert "IVG Schema:      IVGALT" in text
    assert "Legacy objects:  3 (remove_legacy_objects() lists them)" in text


def test_report_leaves_out_legacy_objects_when_there_are_none():
    text = EngineStatus(schema="Graph_KG", legacy_objects=0).report()
    assert "IVG Schema:      Graph_KG" in text
    assert "Legacy objects" not in text


def test_report_says_when_the_namespace_has_no_ivg_schema():
    assert "IVG Schema:      none recorded" in EngineStatus(schema=None).report()


@pytest.fixture
def native_patch():
    patches = []

    def apply(native):
        p = patch.object(IRISGraphEngine, "_iris_obj", return_value=native)
        p.start()
        patches.append(p)
        return native

    yield apply
    for p in patches:
        p.stop()


def test_status_on_a_not_upgraded_namespace_reports_why_and_sends_no_sql(native_patch):
    """status() is how an operator finds out: it does not raise, it says, and probes nothing else."""
    conn = _conn()
    native_patch(FakeNative(record=None))
    with patch("iris_vector_graph._engine.legacy.present_legacy_objects", return_value=[{}, {}]) as legacy:
        eng = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="DOUBLE")
        s = eng.status()
    assert s.schema is None
    assert s.legacy_objects == 2
    assert any("set up by an earlier IVG release" in e for e in s.errors), s.errors
    legacy.assert_called_once()
    assert legacy.call_args.kwargs.get("with_grants") is False
    assert conn.cursor.call_count == 0  # the legacy count is patched; nothing else ran


def test_status_on_a_mismatched_engine_reports_the_recorded_schema(native_patch):
    conn = _conn()
    native_patch(FakeNative(record="Graph_KG"))
    with patch("iris_vector_graph._engine.legacy.present_legacy_objects", return_value=[]):
        s = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="DOUBLE", schema_prefix="RAG").status()
    assert s.schema == "Graph_KG"
    assert any("RAG" in e and "Graph_KG" in e for e in s.errors), s.errors
    assert conn.cursor.call_count == 0


def test_status_on_an_accepted_engine_reports_its_schema(native_patch):
    conn = _conn()
    native_patch(FakeNative(record="IVGALT"))
    eng = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="DOUBLE", schema_prefix="IVGALT")
    with patch("iris_vector_graph._engine.legacy.present_legacy_objects", return_value=[{}]), \
            patch.object(IRISGraphEngine, "_detect_arno", return_value=False), \
            patch.object(IRISGraphEngine, "_ledger_status", return_value=None):
        s = eng.status()
    assert s.schema == "IVGALT"
    assert s.legacy_objects == 1
