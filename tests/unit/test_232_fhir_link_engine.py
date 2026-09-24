"""Spec 232: the engine's json_links and link-report surface (contracts/python.md).

As for 231, Python owns the argument shape, validation before any round trip, and
turning the JSON reply into a dict or an exception.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.exceptions import FHIRGraphError

G = "fhir:IVGFHIR:X0001"
CQF = "extension:http://hl7.org/fhir/StructureDefinition/cqf-library"


@pytest.fixture
def eng():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    native = MagicMock()
    native.classMethodValue.return_value = json.dumps({"status": "registered", "graph_id": G})
    with patch.object(engine, "_iris_obj", return_value=native):
        yield engine, native


def _args(native):
    return native.classMethodValue.call_args.args


class TestRegisterJsonLinks:
    def test_none_passes_empty_for_the_default(self, eng):
        engine, native = eng
        engine.fhir_graph_register()
        assert _args(native) == ("Graph.KG.FHIRGraph", "Register", "", "[]", 60, "")

    def test_empty_list_turns_links_off(self, eng):
        engine, native = eng
        engine.fhir_graph_register(json_links=[])
        assert _args(native)[5] == "[]"

    def test_list_passes_its_json(self, eng):
        engine, native = eng
        links = ["PlanDefinition.library", CQF]
        engine.fhir_graph_register(json_links=links)
        assert json.loads(_args(native)[5]) == links

    @pytest.mark.parametrize(
        "links,message",
        [
            (["plandefinition.library"], "json link 'plandefinition.library'"),
            (["PlanDefinition.library", "PlanDefinition.library"], "listed twice"),
            (["extension:relative/x"], "json link 'extension:relative/x'"),
            ("PlanDefinition.library", "JSON array of strings"),
            ([3], "JSON array of strings"),
        ],
    )
    def test_bad_entry_raises_before_the_round_trip(self, eng, links, message):
        engine, native = eng
        with pytest.raises(ValueError, match=message):
            engine.fhir_graph_register(json_links=links)
        native.classMethodValue.assert_not_called()

    def test_rebuilt_passes_through(self, eng):
        engine, native = eng
        native.classMethodValue.return_value = json.dumps(
            {"status": "registered", "graph_id": G, "json_links": [], "rebuilt": True, "rebuild": {"status": "ok"}}
        )
        out = engine.fhir_graph_register(json_links=[])
        assert out["rebuilt"] is True
        assert out["rebuild"] == {"status": "ok"}
        assert out["json_links"] == []


class TestLinkReport:
    def test_graph_only(self, eng):
        engine, native = eng
        native.classMethodValue.return_value = json.dumps({"graph": G, "source": None, "rows": [], "totals": {}})
        out = engine.fhir_link_report(G)
        assert _args(native) == ("Graph.KG.FHIRGraph", "LinkReport", G, "")
        assert out["rows"] == []

    def test_source(self, eng):
        engine, native = eng
        native.classMethodValue.return_value = json.dumps({"graph": G, "rows": []})
        engine.fhir_link_report(G, source="PlanDefinition/pd1")
        assert _args(native) == ("Graph.KG.FHIRGraph", "LinkReport", G, "PlanDefinition/pd1")

    @pytest.mark.parametrize("source", ["", "PlanDefinition", "/pd1", "PlanDefinition/", "a/b/c", 3])
    def test_bad_source_raises_before_the_round_trip(self, eng, source):
        engine, native = eng
        with pytest.raises(ValueError, match="source"):
            engine.fhir_link_report(G, source=source)
        native.classMethodValue.assert_not_called()

    def test_graph_required(self, eng):
        engine, native = eng
        with pytest.raises(ValueError):
            engine.fhir_link_report("")
        native.classMethodValue.assert_not_called()

    def test_error_reply_raises(self, eng):
        engine, native = eng
        native.classMethodValue.return_value = json.dumps({"status": "error", "error": f"graph '{G}' is not registered"})
        with pytest.raises(FHIRGraphError, match="not registered"):
            engine.fhir_link_report(G)
