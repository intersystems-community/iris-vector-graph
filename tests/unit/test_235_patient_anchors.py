"""Spec 235 US6: `fhir_patient_anchors` with the class methods mocked. The live
answer is in tests/e2e/test_235_anchors_e2e.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

G1 = "fhir:IVGFHIR:X0001"
G2 = "fhir:IVGFHIR:X0002"
FOUR = ["code", "value-concept", "component-code", "component-value-concept"]


def _replies(by_graph):
    """by_graph: graph -> PatientConcepts reply, or None for a graph without the patient."""

    def answer(cls, op, *args):
        if op == "IndexedTokenParams":
            return json.dumps({"status": "ok", "used": FOUR[:2], "dropped": FOUR[2:]})
        if op == "PatientConcepts":
            got = by_graph.get(args[0])
            return json.dumps({"status": "ok", "present": bool(got is not None), "concepts": got or []})
        raise AssertionError(op)

    return answer


def _engine(graphs, by_graph):
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = [(g,) for g in graphs]
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    conn.reset_mock()  # the constructor's own SQL
    native = MagicMock()
    native.classMethodValue.side_effect = _replies(by_graph)
    return engine, native


def _calls(native, op):
    return [c.args for c in native.classMethodValue.call_args_list if c.args[1] == op]


def test_lists_registered_graphs():
    engine, native = _engine([G1, G2], {G1: ["MONDO:2", "MONDO:1"], G2: ["MONDO:1"]})
    with patch.object(engine, "_iris_obj", return_value=native):
        out = engine.fhir_patient_anchors("p1")
    assert [c[2] for c in _calls(native, "PatientConcepts")] == [G1, G2]
    assert all(c[3] == "Patient/p1" for c in _calls(native, "PatientConcepts"))
    assert out == {
        "graphs": [G1, G2],
        "anchors": [
            {"id": "MONDO:1", "graph": G1},
            {"id": "MONDO:1", "graph": G2},
            {"id": "MONDO:2", "graph": G1},
        ],
    }
    sql = engine.conn.cursor.return_value.execute.call_args.args[0]
    assert "fhir_graphs" in sql


def test_clinical_params_passed():
    engine, native = _engine([G1], {G1: []})
    with patch.object(engine, "_iris_obj", return_value=native):
        engine.fhir_patient_anchors("p1")
    assert json.loads(_calls(native, "PatientConcepts")[0][4]) == FOUR[:2]


def test_patient_absent_from_a_graph():
    engine, native = _engine([G1, G2], {G2: ["MONDO:9"]})
    with patch.object(engine, "_iris_obj", return_value=native):
        out = engine.fhir_patient_anchors("p1")
    assert out == {"graphs": [G2], "anchors": [{"id": "MONDO:9", "graph": G2}]}


def test_present_without_anchors():
    engine, native = _engine([G1], {G1: []})
    with patch.object(engine, "_iris_obj", return_value=native):
        assert engine.fhir_patient_anchors("p1") == {"graphs": [G1], "anchors": []}


def test_dedupes_within_a_graph():
    engine, native = _engine([G1], {G1: ["MONDO:1", "MONDO:1"]})
    with patch.object(engine, "_iris_obj", return_value=native):
        assert engine.fhir_patient_anchors("p1")["anchors"] == [{"id": "MONDO:1", "graph": G1}]


def test_graph_given_skips_listing():
    engine, native = _engine([G1, G2], {G1: ["MONDO:1"], G2: ["MONDO:2"]})
    with patch.object(engine, "_iris_obj", return_value=native):
        out = engine.fhir_patient_anchors("p1", graph=G2)
    assert out == {"graphs": [G2], "anchors": [{"id": "MONDO:2", "graph": G2}]}
    engine.conn.cursor.return_value.execute.assert_not_called()


def test_no_graphs():
    engine, native = _engine([], {})
    with patch.object(engine, "_iris_obj", return_value=native):
        assert engine.fhir_patient_anchors("p1") == {"graphs": [], "anchors": []}
    native.classMethodValue.assert_not_called()


@pytest.mark.parametrize("bad", ["", "a/b", "x y", "p'1", "a" * 65, None, 3])
def test_bad_id_raises_before_iris(bad):
    engine, native = _engine([G1], {G1: []})
    with patch.object(engine, "_iris_obj", return_value=native):
        with pytest.raises(ValueError):
            engine.fhir_patient_anchors(bad)
    native.classMethodValue.assert_not_called()
    engine.conn.cursor.assert_not_called()


def test_bad_graph_raises():
    engine, native = _engine([G1], {G1: []})
    with patch.object(engine, "_iris_obj", return_value=native):
        with pytest.raises(ValueError):
            engine.fhir_patient_anchors("p1", graph="")
    native.classMethodValue.assert_not_called()
