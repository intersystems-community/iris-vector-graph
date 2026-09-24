"""Spec 231 US5: the 4.0.1 fixes to `fhir_bridge.py`.

`unified_clinical_pipeline` called `kg_PERSONALIZED_PAGERANK(top_k=...)`, a keyword the
method has never accepted. The `TypeError` was caught by the pipeline's own `except` and
reported as `anchors_resolved_but_no_graph_connectivity`, so every run with resolved
anchors claimed the graph had no connectivity.

`fhir_search_conditions` interpolated `patient_id` into the query string unencoded, so
`p1&_count=100000` rewrote the search.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from iris_vector_graph import fhir_bridge


class TestPipelinePassesReturnTopK:
    def _run(self, engine):
        with patch.object(
            fhir_bridge,
            "fhir_search_conditions",
            return_value={"conditions": [{"code": "J45"}], "error": None},
        ), patch.object(fhir_bridge, "get_kg_anchors", return_value=["MeSH:D001249"]):
            return fhir_bridge.unified_clinical_pipeline(
                engine, "asthma", "http://fhir.test", "p1", ppr_top_k=7
            )

    def test_ppr_receives_return_top_k(self):
        engine = MagicMock()
        engine.kg_PERSONALIZED_PAGERANK.return_value = {"MeSH:D001249": 0.5}
        self._run(engine)
        kwargs = engine.kg_PERSONALIZED_PAGERANK.call_args.kwargs
        assert kwargs.get("return_top_k") == 7
        assert "top_k" not in kwargs

    def test_real_signature_accepts_the_call(self):
        """Bind against the real method, not a MagicMock that accepts any keyword."""
        from iris_vector_graph.engine import IRISGraphEngine

        engine = create_autospec(IRISGraphEngine, instance=True)
        engine.kg_PERSONALIZED_PAGERANK.return_value = {"MeSH:D001249": 0.5}
        result = self._run(engine)
        assert result["status"] != "anchors_resolved_but_no_graph_connectivity"
        assert result["ppr_results"] == {"MeSH:D001249": 0.5}


class TestPatientIdEncoded:
    @pytest.mark.parametrize(
        "patient_id, encoded",
        [
            ("p1&_count=100000", "p1%26_count%3D100000"),
            ("Patient/p 1", "Patient%2Fp%201"),
            ("p1#frag", "p1%23frag"),
        ],
    )
    def test_patient_id_is_url_encoded(self, patient_id, encoded):
        resp = MagicMock()
        resp.json.return_value = {"resourceType": "Bundle", "entry": []}
        with patch.object(fhir_bridge.requests, "get", return_value=resp) as get:
            fhir_bridge.fhir_search_conditions("http://fhir.test", patient_id)
        url = get.call_args.args[0]
        assert f"patient={encoded}&" in url
        assert url.count("&") == 1

    def test_plain_id_unchanged(self):
        resp = MagicMock()
        resp.json.return_value = {"resourceType": "Bundle", "entry": []}
        with patch.object(fhir_bridge.requests, "get", return_value=resp) as get:
            fhir_bridge.fhir_search_conditions("http://fhir.test", "p1")
        assert get.call_args.args[0] == "http://fhir.test/Condition?patient=p1&_format=json"


class TestDeprecated:
    """FR-019: the bridge module is superseded by the FHIR named graph (spec 231)."""

    def test_get_kg_anchors_warns(self):
        engine = MagicMock()
        engine.get_kg_anchors.return_value = []
        with pytest.warns(DeprecationWarning, match="5.0"):
            fhir_bridge.get_kg_anchors(engine, ["J45"])

    def test_pipeline_warns(self):
        engine = MagicMock()
        with patch.object(
            fhir_bridge,
            "fhir_search_conditions",
            return_value={"conditions": [], "error": None},
        ), pytest.warns(DeprecationWarning, match="fhir_concept_ppr"):
            fhir_bridge.unified_clinical_pipeline(engine, "asthma", "http://fhir.test", "p1")

    def test_fhir_search_conditions_does_not_warn(self):
        """A plain FHIR client helper, not part of the deprecated bridge model."""
        resp = MagicMock()
        resp.json.return_value = {"resourceType": "Bundle", "entry": []}
        with patch.object(fhir_bridge.requests, "get", return_value=resp), warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            fhir_bridge.fhir_search_conditions("http://fhir.test", "p1")
