"""Unit tests for the /fhir-event sidecar endpoint.

Tests cover FhirEventPayload validation, date conversion, and handler logic
with a mocked IRISGraphEngine — no IRIS connection required.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Helpers — import the router module after building the test app
# ---------------------------------------------------------------------------

def _make_app(engine=None) -> FastAPI:
    """Build a minimal FastAPI app with the fhir_event router wired in."""
    from api.routers.fhir_event import router as fhir_event_router

    app = FastAPI()
    if engine is not None:
        app.state.engine = engine
    app.include_router(fhir_event_router, prefix="/fhir-event")
    return app


def _mock_engine(create_node_ok=True, edge_ok=True):
    eng = MagicMock()
    eng.create_node.return_value = create_node_ok
    eng.create_edge_temporal.return_value = edge_ok
    return eng


# ---------------------------------------------------------------------------
# T011 — FhirEventPayload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_missing_resource_type_returns_422(self):
        app = _make_app(_mock_engine())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/fhir-event/", json={
            "id": "enc-001", "fhirUrl": "Encounter/enc-001"
        })
        assert resp.status_code == 422

    def test_missing_fhir_url_returns_422(self):
        app = _make_app(_mock_engine())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter", "id": "enc-001"
        })
        assert resp.status_code == 422

    def test_absent_patient_ref_returns_temporal_edge_false(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-001",
            "fhirUrl": "Encounter/enc-001",
            "date": "2024-01-15",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["temporal_edge"] is False
        eng.create_edge_temporal.assert_not_called()

    def test_valid_full_payload_returns_temporal_edge_true(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-001",
            "patientRef": "Patient/p-000",
            "date": "2024-01-15",
            "fhirUrl": "Encounter/enc-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["temporal_edge"] is True
        assert data["node_id"] == "Encounter/enc-001"

    def test_absent_date_uses_current_time_and_warns(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        before = int(time.time())
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-002",
            "patientRef": "Patient/p-000",
            "fhirUrl": "Encounter/enc-002",
        })
        after = int(time.time())
        assert resp.status_code == 200
        data = resp.json()
        assert data["temporal_edge"] is True
        assert len(data["warnings"]) > 0
        # The timestamp passed to create_edge_temporal should be within [before, after]
        ts_used = eng.create_edge_temporal.call_args.kwargs.get(
            "timestamp", eng.create_edge_temporal.call_args[0][3] if eng.create_edge_temporal.call_args[0] else None
        )
        assert ts_used is None or (before <= ts_used <= after + 1)

    def test_empty_string_patient_ref_skips_edge(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Condition",
            "id": "cond-001",
            "patientRef": "",
            "fhirUrl": "Condition/cond-001",
            "date": "2024-02-01",
        })
        assert resp.status_code == 200
        assert resp.json()["temporal_edge"] is False
        eng.create_edge_temporal.assert_not_called()


# ---------------------------------------------------------------------------
# T012 — unix_from_date helper
# ---------------------------------------------------------------------------

class TestUnixFromDate:
    def test_iso_date_converts_correctly(self):
        from api.routers.fhir_event import unix_from_date
        ts, warnings = unix_from_date("2024-01-15")
        assert ts == 1705276800  # 2024-01-15 00:00:00 UTC
        assert warnings == []

    def test_iso_datetime_with_tz_converts(self):
        from api.routers.fhir_event import unix_from_date
        ts, warnings = unix_from_date("2024-01-15T12:00:00Z")
        assert ts == 1705276800 + 12 * 3600
        assert warnings == []

    def test_iso_datetime_no_tz_converts(self):
        from api.routers.fhir_event import unix_from_date
        ts, warnings = unix_from_date("2024-01-15T00:00:00")
        assert ts == 1705276800
        assert warnings == []

    def test_none_returns_current_time_with_warning(self):
        from api.routers.fhir_event import unix_from_date
        before = int(time.time())
        ts, warnings = unix_from_date(None)
        after = int(time.time())
        assert before <= ts <= after + 1
        assert len(warnings) == 1
        assert "date" in warnings[0].lower()

    def test_unparseable_string_returns_current_time_with_warning(self):
        from api.routers.fhir_event import unix_from_date
        before = int(time.time())
        ts, warnings = unix_from_date("not-a-date")
        after = int(time.time())
        assert before <= ts <= after + 1
        assert len(warnings) == 1

    def test_empty_string_returns_current_time_with_warning(self):
        from api.routers.fhir_event import unix_from_date
        _, warnings = unix_from_date("")
        assert len(warnings) == 1

    def test_offset_aware_iso_string_via_fromisoformat(self):
        """Covers the datetime.fromisoformat fallback for offset-aware strings."""
        from api.routers.fhir_event import unix_from_date
        ts, warnings = unix_from_date("2024-01-15T12:00:00+05:30")
        assert warnings == []
        assert ts == 1705300200  # 2024-01-15T12:00:00+05:30 = 2024-01-15T06:30:00Z


# ---------------------------------------------------------------------------
# Additional handler coverage
# ---------------------------------------------------------------------------

class TestHandlerBehavior:
    def test_pointer_node_always_written(self):
        """Pointer node is written even when patientRef is absent."""
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Observation",
            "id": "obs-001",
            "fhirUrl": "Observation/obs-001",
            "date": "2024-03-01",
        })
        assert resp.status_code == 200
        # create_node called at least once for the resource itself
        assert eng.create_node.call_count >= 1
        call_args = [str(c) for c in eng.create_node.call_args_list]
        assert any("Observation/obs-001" in a for a in call_args)

    def test_patient_pointer_node_also_written(self):
        """When patientRef present, patient pointer node is also upserted."""
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-003",
            "patientRef": "Patient/p-999",
            "fhirUrl": "Encounter/enc-003",
            "date": "2024-01-20",
        })
        node_ids = [str(c) for c in eng.create_node.call_args_list]
        assert any("Patient/p-999" in a for a in node_ids)

    def test_predicate_is_uppercased_resource_type(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        client.post("/fhir-event/", json={
            "resourceType": "MedicationRequest",
            "id": "med-001",
            "patientRef": "Patient/p-000",
            "fhirUrl": "MedicationRequest/med-001",
            "date": "2024-04-01",
        })
        kwargs = eng.create_edge_temporal.call_args.kwargs
        predicate = kwargs.get("predicate", eng.create_edge_temporal.call_args[0][1] if eng.create_edge_temporal.call_args[0] else None)
        assert predicate == "MEDICATIONREQUEST"

    def test_no_engine_returns_503(self):
        app = _make_app(engine=None)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-x",
            "fhirUrl": "Encounter/enc-x",
            "date": "2024-01-01",
        })
        assert resp.status_code in (500, 503)

    def test_create_node_exception_returns_500(self):
        eng = MagicMock()
        eng.create_node.side_effect = RuntimeError("IRIS write error")
        app = _make_app(eng)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-err",
            "fhirUrl": "Encounter/enc-err",
            "date": "2024-01-01",
        })
        assert resp.status_code == 500
        assert "IRIS write failed" in resp.json().get("detail", "")

    def test_create_edge_exception_returns_500(self):
        eng = MagicMock()
        eng.create_node.return_value = True
        eng.create_edge_temporal.side_effect = RuntimeError("temporal write error")
        app = _make_app(eng)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": "enc-terr",
            "patientRef": "Patient/p-err",
            "fhirUrl": "Encounter/enc-terr",
            "date": "2024-01-01",
        })
        assert resp.status_code == 500

    def test_response_schema_complete(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Condition",
            "id": "cond-x",
            "patientRef": "Patient/p-x",
            "fhirUrl": "Condition/cond-x",
            "date": "2024-05-01",
        })
        data = resp.json()
        assert "status" in data
        assert "node_id" in data
        assert "temporal_edge" in data
        assert "warnings" in data


# ---------------------------------------------------------------------------
# Code-label extraction (Tier-1 Shaarpec gap): nodes carry clinical codes
# ---------------------------------------------------------------------------

from api.routers.fhir_event import _extract_codes


class TestExtractCodes:
    def test_condition_icd10_primary_coding(self):
        content = {
            "resourceType": "Condition",
            "code": {"coding": [
                {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I50.20",
                 "display": "Heart failure with reduced ejection fraction"}
            ]},
        }
        props = _extract_codes("Condition", content)
        assert props["code"] == "I50.20"
        assert props["code_system"] == "http://hl7.org/fhir/sid/icd-10-cm"
        assert props["code_display"] == "Heart failure with reduced ejection fraction"

    def test_medicationrequest_uses_medicationCodeableConcept(self):
        content = {
            "resourceType": "MedicationRequest",
            "medicationCodeableConcept": {"coding": [
                {"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                 "code": "197361", "display": "Lisinopril 10 MG Oral Tablet"}
            ]},
        }
        props = _extract_codes("MedicationRequest", content)
        assert props["code"] == "197361"
        assert "rxnorm" in props["code_system"]
        assert props["code_display"].startswith("Lisinopril")

    def test_encounter_type_coding(self):
        content = {
            "resourceType": "Encounter",
            "type": [{"coding": [{"system": "http://snomed.info/sct",
                                  "code": "32485007", "display": "Hospital admission"}]}],
        }
        props = _extract_codes("Encounter", content)
        assert props["code"] == "32485007"
        assert props["code_display"] == "Hospital admission"

    def test_encounter_falls_back_to_class_coding(self):
        content = {
            "resourceType": "Encounter",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                      "code": "IMP", "display": "inpatient encounter"},
        }
        props = _extract_codes("Encounter", content)
        assert props["code"] == "IMP"

    def test_text_fallback_when_no_display(self):
        content = {"resourceType": "Condition",
                   "code": {"coding": [{"code": "E11.9"}], "text": "Type 2 diabetes"}}
        props = _extract_codes("Condition", content)
        assert props["code"] == "E11.9"
        assert props["code_display"] == "Type 2 diabetes"

    def test_none_content_returns_empty(self):
        assert _extract_codes("Condition", None) == {}

    def test_no_coding_returns_empty(self):
        assert _extract_codes("Observation", {"resourceType": "Observation"}) == {}

    def test_partial_coding_only_code(self):
        content = {"resourceType": "Condition", "code": {"coding": [{"code": "Z00.0"}]}}
        props = _extract_codes("Condition", content)
        assert props == {"code": "Z00.0"}


class TestHandlerPassesCodeProperties:
    def test_node_created_with_code_properties(self):
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Condition",
            "id": "cond-hf",
            "patientRef": "Patient/p-1",
            "fhirUrl": "Condition/cond-hf",
            "date": "2024-05-01",
            "content": {"resourceType": "Condition", "code": {"coding": [
                {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I50.20",
                 "display": "HFrEF"}]}},
        })
        assert resp.status_code == 200
        # find the create_node call for the resource node and check properties
        found = False
        for c in eng.create_node.call_args_list:
            kwargs = c.kwargs
            if kwargs.get("node_id") == "Condition/cond-hf":
                props = kwargs.get("properties") or {}
                assert props.get("code") == "I50.20"
                assert props.get("code_display") == "HFrEF"
                found = True
        assert found, "resource node create_node call not found with code properties"

    def test_pointer_only_when_no_content(self):
        """Backward-compat: no content → node still created, properties None/empty."""
        eng = _mock_engine()
        app = _make_app(eng)
        client = TestClient(app)
        resp = client.post("/fhir-event/", json={
            "resourceType": "Condition",
            "id": "cond-bare",
            "patientRef": "Patient/p-1",
            "fhirUrl": "Condition/cond-bare",
            "date": "2024-05-01",
        })
        assert resp.status_code == 200
        for c in eng.create_node.call_args_list:
            if c.kwargs.get("node_id") == "Condition/cond-bare":
                assert not c.kwargs.get("properties")  # None or empty
