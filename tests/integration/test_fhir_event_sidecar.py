"""Integration tests for the /fhir-event sidecar endpoint.

Requires a live ivg-iris container (Community, port 21972).
Tests write pointer nodes and temporal edges to IRIS and verify via SQL/engine.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def iris_engine(iris_connection):
    """IRISGraphEngine backed by the live ivg-iris container."""
    from iris_vector_graph import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def sidecar_client(iris_engine):
    """TestClient for the /fhir-event endpoint wired to the live engine."""
    from api.routers.fhir_event import router as fhir_event_router
    app = FastAPI()
    app.state.engine = iris_engine
    app.include_router(fhir_event_router, prefix="/fhir-event")
    return TestClient(app)


def _unique_id() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# T013 — pointer node is written to Graph_KG.nodes
# ---------------------------------------------------------------------------

class TestPointerNodeCreation:

    def test_encounter_creates_pointer_node(self, sidecar_client, iris_engine):
        enc_id = f"enc-test-{_unique_id()}"
        fhir_url = f"Encounter/{enc_id}"
        resp = sidecar_client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": enc_id,
            "fhirUrl": fhir_url,
            "date": "2024-01-15",
        })
        assert resp.status_code == 200, resp.text
        # Verify node exists in IRIS
        cursor = iris_engine.conn.cursor()
        cursor.execute(
            "SELECT node_id FROM Graph_KG.nodes WHERE node_id = ?",
            [fhir_url],
        )
        row = cursor.fetchone()
        assert row is not None, f"Pointer node {fhir_url!r} not found in Graph_KG.nodes"
        assert row[0] == fhir_url

    def test_pointer_node_label_is_resource_type(self, sidecar_client, iris_engine):
        obs_id = f"obs-test-{_unique_id()}"
        fhir_url = f"Observation/{obs_id}"
        sidecar_client.post("/fhir-event/", json={
            "resourceType": "Observation",
            "id": obs_id,
            "fhirUrl": fhir_url,
            "date": "2024-02-01",
        })
        cursor = iris_engine.conn.cursor()
        cursor.execute(
            "SELECT label FROM Graph_KG.rdf_labels WHERE s = ?",
            [fhir_url],
        )
        rows = cursor.fetchall()
        labels = [r[0] for r in rows]
        assert "Observation" in labels, f"Label 'Observation' not found; got {labels}"


# ---------------------------------------------------------------------------
# T014 — temporal edge is written to ^KG temporal store
# ---------------------------------------------------------------------------

class TestTemporalEdgeCreation:

    def test_encounter_creates_temporal_edge(self, sidecar_client, iris_engine):
        patient_id = f"Patient/pt-{_unique_id()}"
        enc_id = f"enc-{_unique_id()}"
        fhir_url = f"Encounter/{enc_id}"
        resp = sidecar_client.post("/fhir-event/", json={
            "resourceType": "Encounter",
            "id": enc_id,
            "patientRef": patient_id,
            "fhirUrl": fhir_url,
            "date": "2024-01-15",
        })
        assert resp.status_code == 200
        assert resp.json()["temporal_edge"] is True

        # Verify edge via get_edges_in_window
        edges = iris_engine.get_edges_in_window(
            source=patient_id,
            predicate="ENCOUNTER",
            start=0,
            end=9_999_999_999,
            direction="out",
        )
        found = [e for e in edges if e.get("o") == fhir_url or e.get("target") == fhir_url]
        assert len(found) >= 1, f"Temporal edge to {fhir_url!r} not found; edges={edges}"

    def test_condition_creates_temporal_edge(self, sidecar_client, iris_engine):
        patient_id = f"Patient/pt-{_unique_id()}"
        cond_id = f"cond-{_unique_id()}"
        fhir_url = f"Condition/{cond_id}"
        sidecar_client.post("/fhir-event/", json={
            "resourceType": "Condition",
            "id": cond_id,
            "patientRef": patient_id,
            "fhirUrl": fhir_url,
            "date": "2024-03-10",
        })
        edges = iris_engine.get_edges_in_window(
            source=patient_id,
            predicate="CONDITION",
            start=0,
            end=9_999_999_999,
        )
        found = [e for e in edges if e.get("o") == fhir_url or e.get("target") == fhir_url]
        assert len(found) >= 1


# ---------------------------------------------------------------------------
# T015 — absent patientRef skips temporal edge
# ---------------------------------------------------------------------------

class TestNoEdgeWithoutPatientRef:

    def test_absent_patient_ref_skips_edge(self, sidecar_client, iris_engine):
        obs_id = f"obs-nopt-{_unique_id()}"
        fhir_url = f"Observation/{obs_id}"
        resp = sidecar_client.post("/fhir-event/", json={
            "resourceType": "Observation",
            "id": obs_id,
            "fhirUrl": fhir_url,
            "date": "2024-04-01",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["temporal_edge"] is False

        # Pointer node should still exist
        cursor = iris_engine.conn.cursor()
        cursor.execute("SELECT node_id FROM Graph_KG.nodes WHERE node_id = ?", [fhir_url])
        assert cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# T016 — duplicate POST is idempotent
# ---------------------------------------------------------------------------

class TestIdempotency:

    def test_duplicate_post_is_idempotent(self, sidecar_client, iris_engine):
        patient_id = f"Patient/pt-idem-{_unique_id()}"
        enc_id = f"enc-idem-{_unique_id()}"
        fhir_url = f"Encounter/{enc_id}"
        payload = {
            "resourceType": "Encounter",
            "id": enc_id,
            "patientRef": patient_id,
            "fhirUrl": fhir_url,
            "date": "2024-05-01",
        }
        # POST twice
        r1 = sidecar_client.post("/fhir-event/", json=payload)
        r2 = sidecar_client.post("/fhir-event/", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Pointer node should exist exactly once
        cursor = iris_engine.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = ?", [fhir_url]
        )
        count = cursor.fetchone()[0]
        assert count == 1, f"Expected 1 node, got {count}"

        # Edge may be deduped or duplicated depending on upsert — at least 1
        edges = iris_engine.get_edges_in_window(
            source=patient_id, predicate="ENCOUNTER", start=0, end=9_999_999_999
        )
        found = [e for e in edges if e.get("o") == fhir_url or e.get("target") == fhir_url]
        assert len(found) >= 1
