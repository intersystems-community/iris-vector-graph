"""Spec 231 US5: `/api/cypher` no longer fetches whatever `fhir_base_url` a caller names.

`_resolve_patient_anchors` passed `req.fhir_base_url` straight to `requests.get`, so any
client of the API could make the server issue GET requests to an address of its choosing
(SSRF), including link-local metadata endpoints. A request's URL is now honoured only if
it is listed in `IVG_FHIR_ALLOWED_BASES`; the list is empty by default, so the default
is to refuse. `FHIR_BASE_URL` from the server's environment stays trusted, since the
operator set it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from iris_vector_graph import cypher_api  # noqa: E402
from iris_vector_graph.cypher_api import CypherRequest, _resolve_patient_anchors  # noqa: E402

_OK = {"error": None, "conditions": [{"code": "J45"}]}


def _req(url):
    return CypherRequest(query="MATCH (n) RETURN n", fhir_patient_id="p1", fhir_base_url=url)


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("IVG_FHIR_ALLOWED_BASES", raising=False)
    monkeypatch.delenv("FHIR_BASE_URL", raising=False)
    return monkeypatch


class TestAllowlist:
    def test_refused_by_default(self, clean_env):
        with patch("iris_vector_graph.fhir_bridge.fhir_search_conditions") as search:
            with pytest.raises(ValueError, match="IVG_FHIR_ALLOWED_BASES"):
                _resolve_patient_anchors(_req("http://169.254.169.254/latest"))
        search.assert_not_called()

    def test_refused_when_not_listed(self, clean_env):
        clean_env.setenv("IVG_FHIR_ALLOWED_BASES", "https://fhir.a.example/r4")
        with patch("iris_vector_graph.fhir_bridge.fhir_search_conditions") as search:
            with pytest.raises(ValueError):
                _resolve_patient_anchors(_req("https://fhir.b.example/r4"))
        search.assert_not_called()

    def test_prefix_is_not_a_match(self, clean_env):
        """`https://fhir.a.example/r4.evil.example` must not ride on `.../r4`."""
        clean_env.setenv("IVG_FHIR_ALLOWED_BASES", "https://fhir.a.example/r4")
        with pytest.raises(ValueError):
            _resolve_patient_anchors(_req("https://fhir.a.example/r4.evil.example"))

    @pytest.mark.parametrize(
        "listed, requested",
        [
            ("https://fhir.a.example/r4", "https://fhir.a.example/r4"),
            ("https://fhir.a.example/r4/", "https://fhir.a.example/r4"),
            ("https://fhir.a.example/r4", "https://fhir.a.example/r4/"),
            ("https://x.example, https://fhir.a.example/r4", "https://fhir.a.example/r4"),
        ],
    )
    def test_listed_base_is_used(self, clean_env, listed, requested):
        clean_env.setenv("IVG_FHIR_ALLOWED_BASES", listed)
        with patch(
            "iris_vector_graph.fhir_bridge.fhir_search_conditions", return_value=_OK
        ) as search, patch(
            "iris_vector_graph.fhir_bridge.get_kg_anchors", return_value=["n1"]
        ), patch.object(cypher_api, "_get_engine", return_value=MagicMock()):
            assert _resolve_patient_anchors(_req(requested)) == ["n1"]
        assert search.call_args.kwargs["fhir_base_url"] == requested

    def test_environment_base_is_trusted(self, clean_env):
        clean_env.setenv("FHIR_BASE_URL", "http://internal-fhir:8080/fhir")
        with patch(
            "iris_vector_graph.fhir_bridge.fhir_search_conditions", return_value=_OK
        ) as search, patch(
            "iris_vector_graph.fhir_bridge.get_kg_anchors", return_value=["n1"]
        ), patch.object(cypher_api, "_get_engine", return_value=MagicMock()):
            assert _resolve_patient_anchors(_req(None)) == ["n1"]
        assert search.call_args.kwargs["fhir_base_url"] == "http://internal-fhir:8080/fhir"

    def test_no_url_anywhere_returns_empty(self, clean_env):
        assert _resolve_patient_anchors(_req(None)) == []


class TestEndpoint:
    def test_unlisted_base_is_http_400(self, clean_env):
        from fastapi.testclient import TestClient

        with patch.object(cypher_api, "_run_cypher", return_value={"status": "OK"}), patch(
            "iris_vector_graph.fhir_bridge.fhir_search_conditions"
        ) as search:
            resp = TestClient(cypher_api.app).post(
                "/api/cypher",
                json={
                    "query": "MATCH (n) RETURN n",
                    "fhir_patient_id": "p1",
                    "fhir_base_url": "http://169.254.169.254/latest",
                },
            )
        assert resp.status_code in (400, 401)
        if resp.status_code == 400:
            assert "IVG_FHIR_ALLOWED_BASES" in resp.text
        search.assert_not_called()
