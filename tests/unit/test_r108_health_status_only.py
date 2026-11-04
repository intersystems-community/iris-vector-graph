"""R108: cypher_api's `/health` answers status only, and without the key.

`/health` is on the public allow-list, so anyone who can reach the port reads it. It
used to answer the graph's node count, and on a failure the raw exception text (a
connection error names the host, port, namespace and user). It also answered 200
`"status": "ok"` when IRIS was down, so a load balancer checking the status code could
not tell the two apart. Now: 200 `{"status": "ok"}` when the probe query answers, 503
`{"status": "unavailable"}` when it does not, and the exception goes to the server log.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from iris_vector_graph.result import IVGResult  # noqa: E402

KEY = "r108-key"
SECRET = "connect to iris-secret-host:1972 as _SYSTEM failed"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    monkeypatch.setenv("BOLT_ENABLED", "0")


def _client(get_engine):
    from iris_vector_graph import cypher_api as mod

    return patch.object(mod, "_get_engine", get_engine), TestClient(mod.app, raise_server_exceptions=False)


def test_health_answers_status_only_without_the_key():
    eng = MagicMock()
    eng.execute_cypher.return_value = IVGResult(columns=["c"], rows=[[8904]])
    p, client = _client(lambda: eng)
    with p:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert "8904" not in resp.text
    eng.execute_cypher.assert_called_once()


def test_health_hides_the_exception_and_logs_it(caplog):
    def boom():
        raise RuntimeError(SECRET)

    p, client = _client(boom)
    with p, caplog.at_level(logging.WARNING, logger="iris_vector_graph.cypher_api"):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}
    assert "secret" not in resp.text
    assert any(SECRET in r.getMessage() or SECRET in (r.exc_text or "") for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_health_reports_a_query_error_folded_into_the_result(caplog):
    # execute_cypher folds a failed statement into IVGResult.error; that is not "ok".
    eng = MagicMock()
    eng.execute_cypher.return_value = IVGResult(columns=[], rows=[], error=SECRET)
    p, client = _client(lambda: eng)
    with p, caplog.at_level(logging.WARNING, logger="iris_vector_graph.cypher_api"):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}
    assert any(SECRET in r.getMessage() for r in caplog.records)
