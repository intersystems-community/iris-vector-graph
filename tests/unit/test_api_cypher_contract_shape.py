"""`/api/cypher` response shape, against `contracts/cypher_api.yaml`.

Two things the contract tests caught that a mocked router test can pin down more
cheaply than a live container can:

* **Optional means absent, not null.** `line`, `column`, `suggestion`, `sqlQuery`,
  `indexesUsed` and `optimizationsApplied` are all optional in the contract, and
  `model_dump(by_alias=True)` emitted every one of them as JSON `null`. A client
  following the contract writes `if "suggestion" in data: use(data["suggestion"])`
  and gets `None` where it expected a string.

* **An undefined variable is a 400, not a 500.** The router has a `except ValueError`
  leg that classifies `UNDEFINED_VARIABLE` and `COMPLEXITY_LIMIT_EXCEEDED`, but the
  translator raises `SyntaxError` for an undefined variable (`translator.py:9737`),
  which is not a `ValueError` — so that leg was unreachable for the one case it was
  written for and every such query fell through to the generic 500 handler.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_db_connection, get_engine_optional
from api.routers.cypher import router


@pytest.fixture()
def client():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.description = [("p_name",)]
    cursor.fetchall.return_value = [("Cypher Test Protein",)]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_connection] = lambda: conn
    app.dependency_overrides[get_engine_optional] = lambda: SimpleNamespace(
        _fetch_first_unsafe=True, get_table_mapping=lambda label: None
    )
    return TestClient(app, raise_server_exceptions=False)


class TestOptionalFieldsAreAbsentNotNull:
    def test_success_response_omits_unset_metadata_fields(self, client):
        response = client.post(
            "/api/cypher", json={"query": "MATCH (p:Protein) RETURN p.name LIMIT 1"}
        )
        assert response.status_code == 200
        metadata = response.json().get("queryMetadata", {})
        assert "indexesUsed" not in metadata, (
            "indexesUsed is optional in the contract; emitting it as null makes "
            "`if 'indexesUsed' in metadata` true with a None value"
        )

    def test_error_response_omits_unset_suggestion(self, client):
        response = client.post("/api/cypher", json={"query": "MATCH (p:Protein) RETRUN p"})
        assert response.status_code == 400
        data = response.json()
        assert "suggestion" not in data or isinstance(data["suggestion"], str)
        for field in ("errorType", "message", "errorCode", "traceId"):
            assert data[field]

    def test_error_response_omits_unset_line_and_column(self, client):
        response = client.post("/api/cypher", json={"query": "MATCH (p:Protein) RETURN m.name"})
        data = response.json()
        for field in ("line", "column"):
            assert field not in data or isinstance(data[field], int)


class TestUndefinedVariableIsATranslationError:
    def test_returns_400_with_undefined_variable_code(self, client):
        response = client.post("/api/cypher", json={"query": "MATCH (p:Protein) RETURN m.name"})
        assert response.status_code == 400
        data = response.json()
        assert data["errorType"] == "translation"
        assert data["errorCode"] == "UNDEFINED_VARIABLE"
        assert "m" in data["message"]

    def test_the_message_is_not_the_generic_execution_failure(self, client):
        response = client.post("/api/cypher", json={"query": "MATCH (p:Protein) RETURN m.name"})
        assert response.json()["message"] != "query execution failed"


class TestVariableLengthDepthCap:
    """The depth cap belongs to the endpoint, not the translator.

    `contracts/cypher_api.yaml` promises `413 COMPLEXITY_LIMIT_EXCEEDED` for a
    variable-length path deeper than 10 hops. The translator's own ceiling went the
    other way — spec 203 raised the unbounded `*` default from 10 to 100 for TCK
    compliance — and those two are not in conflict, because they answer different
    questions: the library should translate what the TCK asks for, while a public HTTP
    endpoint should refuse an expansion whose cost it cannot bound. So the cap is
    enforced here, where the contract lives, and is configurable.
    """

    def test_depth_over_the_cap_is_refused(self, client):
        response = client.post(
            "/api/cypher", json={"query": "MATCH (p:Protein)-[r*1..20]->(m:Protein) RETURN p, m"}
        )
        assert response.status_code == 413
        data = response.json()
        assert data["errorType"] == "translation"
        assert data["errorCode"] == "COMPLEXITY_LIMIT_EXCEEDED"
        assert "20" in data["message"] and "10" in data["message"]

    def test_depth_at_the_cap_is_allowed(self, client):
        response = client.post(
            "/api/cypher", json={"query": "MATCH (p:Protein)-[r*1..10]->(m:Protein) RETURN p, m"}
        )
        assert response.status_code != 413

    def test_the_cap_is_configurable(self, client, monkeypatch):
        monkeypatch.setenv("IVG_CYPHER_MAX_HOPS", "25")
        response = client.post(
            "/api/cypher", json={"query": "MATCH (p:Protein)-[r*1..20]->(m:Protein) RETURN p, m"}
        )
        assert response.status_code != 413

    def test_an_unbounded_star_counts_as_the_translator_default(self, client, monkeypatch):
        """`*` with no upper bound is the expansion the cap exists to stop."""
        monkeypatch.setenv("IVG_CYPHER_MAX_HOPS", "10")
        response = client.post(
            "/api/cypher", json={"query": "MATCH (p:Protein)-[r*]->(m:Protein) RETURN p, m"}
        )
        assert response.status_code == 413
