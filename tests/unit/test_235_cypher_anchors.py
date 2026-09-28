"""Spec 235 US6: `/api/cypher` takes patient anchors from a synced FHIR graph first and
the external `fhir_bridge` only when no graph holds the patient (research R15).

`_resolve_patient_anchors` stays the pre-235 bridge path (tests/unit/
test_231_cypher_api_fhir_allowlist.py); `_patient_anchors` puts the graph in front of it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from iris_vector_graph import cypher_api  # noqa: E402
from iris_vector_graph.cypher_api import CypherRequest, _patient_anchors  # noqa: E402

G = "fhir:IVGFHIR:X0001"
_OK = {"error": None, "conditions": [{"code": "J45"}]}


def _req(**kw):
    return CypherRequest(query="MATCH (n) RETURN n", fhir_patient_id="p1", **kw)


def _engine(reply):
    eng = MagicMock()
    if isinstance(reply, Exception):
        eng.fhir_patient_anchors.side_effect = reply
    else:
        eng.fhir_patient_anchors.return_value = reply
    return eng


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("IVG_FHIR_ALLOWED_BASES", raising=False)
    monkeypatch.setenv("FHIR_BASE_URL", "http://internal-fhir:8080/fhir")
    return monkeypatch


def _bridge():
    return (
        patch("iris_vector_graph.fhir_bridge.fhir_search_conditions", return_value=_OK),
        patch("iris_vector_graph.fhir_bridge.get_kg_anchors", return_value=["n1", "n2"]),
    )


class TestPatientAnchors:
    def test_graph_holds_patient(self, env):
        eng = _engine({"graphs": [G], "anchors": [{"id": "MONDO:1", "graph": G}, {"id": "MONDO:2", "graph": G}]})
        s, k = _bridge()
        with patch.object(cypher_api, "_get_engine", return_value=eng), s as search, k:
            out = _patient_anchors(_req())
        search.assert_not_called()
        assert out == {
            "anchor_source": "graph",
            "ids": ["MONDO:1", "MONDO:2"],
            "anchors": [{"id": "MONDO:1", "graph": G}, {"id": "MONDO:2", "graph": G}],
        }
        assert all(isinstance(i, str) for i in out["ids"])

    def test_ids_distinct_across_graphs(self, env):
        eng = _engine({"graphs": [G, "fhir:X:Y"], "anchors": [{"id": "M:1", "graph": G}, {"id": "M:1", "graph": "fhir:X:Y"}]})
        with patch.object(cypher_api, "_get_engine", return_value=eng):
            assert _patient_anchors(_req())["ids"] == ["M:1"]

    def test_graph_holds_patient_no_anchors(self, env):
        eng = _engine({"graphs": [G], "anchors": []})
        s, k = _bridge()
        with patch.object(cypher_api, "_get_engine", return_value=eng), s as search, k:
            out = _patient_anchors(_req())
        search.assert_not_called()
        assert out == {"anchor_source": "graph", "ids": [], "anchors": []}

    def test_no_graph_uses_bridge(self, env):
        eng = _engine({"graphs": [], "anchors": []})
        s, k = _bridge()
        with patch.object(cypher_api, "_get_engine", return_value=eng), s as search, k:
            out = _patient_anchors(_req())
        assert search.call_args.kwargs == {
            "fhir_base_url": "http://internal-fhir:8080/fhir",
            "patient_id": "p1",
            "auth": None,
        }
        assert out == {
            "anchor_source": "fhir_bridge",
            "ids": ["n1", "n2"],
            "anchors": [{"id": "n1", "graph": None}, {"id": "n2", "graph": None}],
        }

    def test_graph_lookup_error_uses_bridge(self, env):
        """A namespace with no FHIR graph table answers like one with no graph."""
        eng = _engine(RuntimeError("no Graph_KG.fhir_graphs"))
        s, k = _bridge()
        with patch.object(cypher_api, "_get_engine", return_value=eng), s as search, k:
            out = _patient_anchors(_req())
        search.assert_called_once()
        assert out["anchor_source"] == "fhir_bridge"

    def test_bad_patient_id_uses_bridge(self, env):
        eng = _engine(ValueError("bad id"))
        s, k = _bridge()
        with patch.object(cypher_api, "_get_engine", return_value=eng), s as search, k:
            assert _patient_anchors(_req())["anchor_source"] == "fhir_bridge"
        search.assert_called_once()

    def test_fhir_graph_passed(self, env):
        eng = _engine({"graphs": [G], "anchors": []})
        with patch.object(cypher_api, "_get_engine", return_value=eng):
            _patient_anchors(_req(fhir_graph=G))
        eng.fhir_patient_anchors.assert_called_once_with("p1", graph=G)

    def test_no_graph_given(self, env):
        eng = _engine({"graphs": [G], "anchors": []})
        with patch.object(cypher_api, "_get_engine", return_value=eng):
            _patient_anchors(_req())
        eng.fhir_patient_anchors.assert_called_once_with("p1", graph=None)


class TestEndpoint:
    def _post(self, body, eng):
        from fastapi.testclient import TestClient

        run = MagicMock(return_value={"status": "OK", "columns": [], "rows": [], "rowCount": 0})
        with patch.object(cypher_api, "_run_cypher", run), patch.object(cypher_api, "_get_engine", return_value=eng):
            resp = TestClient(cypher_api.app).post("/api/cypher", json=body)
        return resp, run

    def test_graph_response(self, env):
        eng = _engine({"graphs": [G], "anchors": [{"id": "MONDO:1", "graph": G}]})
        resp, run = self._post({"query": "MATCH (n) RETURN n", "fhir_patient_id": "p1"}, eng)
        if resp.status_code == 401:
            pytest.skip("API key middleware active")
        body = resp.json()
        assert body["anchor_source"] == "graph"
        assert body["anchors"] == [{"id": "MONDO:1", "graph": G}]
        assert run.call_args.args[1]["patient_anchors"] == ["MONDO:1"]

    def test_no_patient_keeps_410_shape(self, env):
        eng = _engine({"graphs": [G], "anchors": []})
        resp, run = self._post({"query": "MATCH (n) RETURN n"}, eng)
        if resp.status_code == 401:
            pytest.skip("API key middleware active")
        assert set(resp.json()) == {"status", "columns", "rows", "rowCount"}
        eng.fhir_patient_anchors.assert_not_called()
        assert "patient_anchors" not in run.call_args.args[1]
