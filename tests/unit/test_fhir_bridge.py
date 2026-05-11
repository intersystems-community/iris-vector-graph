"""Unit tests for iris_vector_graph.fhir_bridge — mocked FHIR, no IRIS needed."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.fhir_bridge import (
    FHIRSearchTool,
    GetPatientKGNeighborhoodTool,
    extract_icd_codes,
    extract_icd_codes_from_bundle,
    fhir_search_conditions,
    get_kg_anchors,
    unified_clinical_pipeline,
)


SAMPLE_CONDITION_BUNDLE = {
    "resourceType": "Bundle",
    "type": "searchset",
    "entry": [
        {
            "resource": {
                "resourceType": "Condition",
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/sid/icd-10-cm",
                            "code": "E11.9",
                            "display": "Type 2 diabetes mellitus without complications",
                        }
                    ]
                },
            }
        },
        {
            "resource": {
                "resourceType": "Condition",
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/sid/icd-10-cm",
                            "code": "I10",
                            "display": "Essential (primary) hypertension",
                        }
                    ]
                },
            }
        },
        {
            "resource": {
                "resourceType": "Condition",
                "code": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "44054006",
                            "display": "Type 2 diabetes mellitus",
                        }
                    ]
                },
            }
        },
    ],
}


class TestExtractIcdCodes:

    def test_extracts_icd10_codes_from_bundle(self):
        codes = extract_icd_codes(SAMPLE_CONDITION_BUNDLE)
        assert "E11.9" in codes
        assert "I10" in codes

    def test_excludes_non_icd_systems(self):
        codes = extract_icd_codes(SAMPLE_CONDITION_BUNDLE)
        assert "44054006" not in codes

    def test_empty_bundle_returns_empty(self):
        codes = extract_icd_codes({"resourceType": "Bundle", "entry": []})
        assert codes == []

    def test_no_entry_key_returns_empty(self):
        codes = extract_icd_codes({"resourceType": "Bundle"})
        assert codes == []

    def test_deduplicates_codes(self):
        bundle = {
            "entry": [
                {"resource": {"resourceType": "Condition", "code": {"coding": [
                    {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9"}
                ]}}},
                {"resource": {"resourceType": "Condition", "code": {"coding": [
                    {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9"}
                ]}}},
            ]
        }
        codes = extract_icd_codes(bundle)
        assert codes.count("E11.9") == 1

    def test_handles_malformed_entries(self):
        bundle = {
            "entry": [
                {"resource": {"resourceType": "Observation"}},
                {"resource": {"resourceType": "Condition", "code": {}}},
                {"resource": {"resourceType": "Condition"}},
                {},
            ]
        }
        codes = extract_icd_codes(bundle)
        assert codes == []


class TestExtractIcdCodesFromBundle:

    def test_returns_structured_dicts(self):
        conditions = extract_icd_codes_from_bundle(SAMPLE_CONDITION_BUNDLE)
        assert len(conditions) == 2
        assert conditions[0]["code"] == "E11.9"
        assert "display" in conditions[0]
        assert "system" in conditions[0]


class TestGetKgAnchors:

    def test_delegates_to_engine(self):
        engine = MagicMock()
        engine.get_kg_anchors.return_value = ["mesh:D003924", "mesh:D006973"]
        result = get_kg_anchors(engine, ["E11.9", "I10"])
        engine.get_kg_anchors.assert_called_once_with(
            icd_codes=["E11.9", "I10"], bridge_type="icd10_to_mesh"
        )
        assert result == ["mesh:D003924", "mesh:D006973"]

    def test_empty_codes_returns_empty(self):
        engine = MagicMock()
        result = get_kg_anchors(engine, [])
        assert result == []
        engine.get_kg_anchors.assert_not_called()

    def test_no_matches_logs_warning(self, caplog):
        engine = MagicMock()
        engine.get_kg_anchors.return_value = []
        with caplog.at_level(logging.WARNING):
            result = get_kg_anchors(engine, ["UNKNOWN_CODE"])
        assert result == []
        assert "get_kg_anchors returned empty" in caplog.text

    def test_custom_bridge_type(self):
        engine = MagicMock()
        engine.get_kg_anchors.return_value = ["mondo:123"]
        result = get_kg_anchors(engine, ["X99"], bridge_type="icd10_to_mondo")
        engine.get_kg_anchors.assert_called_once_with(
            icd_codes=["X99"], bridge_type="icd10_to_mondo"
        )


class TestFhirSearchConditions:

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_successful_search(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_CONDITION_BUNDLE
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fhir_search_conditions("http://fhir.example.com", "patient-123")
        assert result["error"] is None
        assert len(result["conditions"]) == 2
        assert result["conditions"][0]["code"] == "E11.9"

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_basic_auth_passed(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"entry": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        fhir_search_conditions(
            "http://fhir.example.com", "p1", auth=("user", "pass")
        )
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["auth"] == ("user", "pass")

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_timeout_returns_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.Timeout("timed out")

        result = fhir_search_conditions("http://fhir.example.com", "p1", timeout=1.0)
        assert result["error"] is not None
        assert "timed out" in result["error"]
        assert result["conditions"] == []

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_connection_error_returns_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("refused")

        result = fhir_search_conditions("http://unreachable:9999", "p1")
        assert result["error"] is not None
        assert "unreachable" in result["error"]
        assert result["conditions"] == []

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_http_401_returns_error(self, mock_get):
        import requests as req
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError(
            response=mock_resp
        )
        mock_get.return_value = mock_resp

        result = fhir_search_conditions("http://fhir.example.com", "p1")
        assert result["error"] is not None
        assert "401" in result["error"]


class TestUnifiedClinicalPipeline:

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_full_pipeline_success(self, mock_fhir):
        mock_fhir.return_value = {
            "conditions": [
                {"code": "E11.9", "system": "icd-10-cm", "display": "T2DM"},
                {"code": "I10", "system": "icd-10-cm", "display": "HTN"},
            ],
            "error": None,
        }
        engine = MagicMock()
        engine.get_kg_anchors.return_value = ["mesh:D003924", "mesh:D006973"]
        engine.kg_PERSONALIZED_PAGERANK.return_value = [
            {"node_id": "mesh:D003924", "score": 0.8},
            {"node_id": "mesh:D001249", "score": 0.5},
        ]

        result = unified_clinical_pipeline(
            engine=engine,
            query="diabetes hypertension",
            fhir_base_url="http://fhir.test",
            patient_id="maria-001",
        )
        assert result["status"] == "ok"
        assert len(result["anchors"]) == 2
        assert len(result["ppr_results"]) == 2
        assert result["fhir_conditions"][0]["code"] == "E11.9"

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_pipeline_no_fhir_conditions(self, mock_fhir):
        mock_fhir.return_value = {"conditions": [], "error": None}
        engine = MagicMock()

        result = unified_clinical_pipeline(
            engine=engine,
            query="unknown",
            fhir_base_url="http://fhir.test",
            patient_id="empty-patient",
        )
        assert result["status"] == "no_fhir_conditions"
        engine.get_kg_anchors.assert_not_called()

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_pipeline_no_bridges_loaded(self, mock_fhir):
        mock_fhir.return_value = {
            "conditions": [{"code": "E11.9", "system": "icd-10-cm", "display": "T2DM"}],
            "error": None,
        }
        engine = MagicMock()
        engine.get_kg_anchors.return_value = []

        result = unified_clinical_pipeline(
            engine=engine,
            query="diabetes",
            fhir_base_url="http://fhir.test",
            patient_id="p1",
        )
        assert result["status"] == "no_bridges_loaded"

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_pipeline_ppr_empty(self, mock_fhir):
        mock_fhir.return_value = {
            "conditions": [{"code": "E11.9", "system": "icd-10-cm", "display": "T2DM"}],
            "error": None,
        }
        engine = MagicMock()
        engine.get_kg_anchors.return_value = ["mesh:D003924"]
        engine.kg_PERSONALIZED_PAGERANK.return_value = []

        result = unified_clinical_pipeline(
            engine=engine,
            query="diabetes",
            fhir_base_url="http://fhir.test",
            patient_id="p1",
        )
        assert result["status"] == "anchors_resolved_but_no_graph_connectivity"
        assert result["anchors"] == ["mesh:D003924"]

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_pipeline_fhir_error(self, mock_fhir):
        mock_fhir.return_value = {"conditions": [], "error": "FHIR timeout"}
        engine = MagicMock()

        result = unified_clinical_pipeline(
            engine=engine,
            query="test",
            fhir_base_url="http://fhir.test",
            patient_id="p1",
        )
        assert result["status"] == "fhir_error"


class TestFHIRSearchTool:

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_tool_returns_conditions(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_CONDITION_BUNDLE
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        tool = FHIRSearchTool(base_url="http://fhir.test")
        result = tool("patient-123")
        assert result["error"] is None
        assert len(result["conditions"]) == 2

    @patch("iris_vector_graph.fhir_bridge.requests.get")
    def test_tool_handles_auth_failure(self, mock_get):
        import requests as req
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = req.exceptions.HTTPError(
            response=mock_resp
        )
        mock_get.return_value = mock_resp

        tool = FHIRSearchTool(base_url="http://fhir.test", auth=("bad", "creds"))
        result = tool("patient-123")
        assert result["error"] is not None


class TestGetPatientKGNeighborhoodTool:

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_tool_returns_neighborhood(self, mock_fhir):
        mock_fhir.return_value = {
            "conditions": [{"code": "E11.9", "system": "icd-10-cm", "display": "T2DM"}],
            "error": None,
        }
        engine = MagicMock()
        engine.get_kg_anchors.return_value = ["mesh:D003924"]
        engine.kg_PERSONALIZED_PAGERANK.return_value = [
            {"node_id": "mesh:D003924", "score": 0.9}
        ]

        tool = GetPatientKGNeighborhoodTool(
            engine=engine, fhir_base_url="http://fhir.test"
        )
        result = tool("maria-001")
        assert result["status"] == "ok"
        assert result["anchors"] == ["mesh:D003924"]
        assert len(result["ppr_results"]) == 1

    @patch("iris_vector_graph.fhir_bridge.fhir_search_conditions")
    def test_tool_empty_for_no_conditions(self, mock_fhir):
        mock_fhir.return_value = {"conditions": [], "error": None}
        engine = MagicMock()

        tool = GetPatientKGNeighborhoodTool(
            engine=engine, fhir_base_url="http://fhir.test"
        )
        result = tool("patient-no-conditions")
        assert result["status"] == "no_fhir_conditions"
        assert result["anchors"] == []
        assert result["ppr_results"] == []


class TestCypherRequestModel:

    def test_without_fhir_patient_id_unchanged(self):
        from iris_vector_graph.cypher_api import CypherRequest
        req = CypherRequest(query="MATCH (n) RETURN n")
        assert req.fhir_patient_id is None
        assert req.fhir_base_url is None
        assert req.fhir_auth is None

    def test_with_fhir_patient_id(self):
        from iris_vector_graph.cypher_api import CypherRequest
        req = CypherRequest(
            query="MATCH (n) WHERE n.id IN $patient_anchors RETURN n",
            fhir_patient_id="maria-001",
            fhir_base_url="http://fhir.test/fhir",
        )
        assert req.fhir_patient_id == "maria-001"
        assert req.fhir_base_url == "http://fhir.test/fhir"
