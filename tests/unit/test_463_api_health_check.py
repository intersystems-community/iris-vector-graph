"""4.6.3: api.main's /health runs a check and answers 503 with the reason when it fails.

It answered 200 `{"status": "healthy", ...}` whatever the database did: with no engine (`"database": "unavailable"`),
with a connection IRIS had dropped, with a namespace recording another IVG Schema. A load balancer reading the status
code kept sending traffic to an API that answered every query with an error. The check: `SELECT 1` through the
engine's connection answering 1, and, over an IRIS connection, the namespace's IVG Schema record naming the engine's
schema. The engines here are fakes: a connection whose cursor answers what each case needs.

The reason in the body is a fixed phrase and IRIS's text is logged at WARNING: /health needs no key, and cypher_api's
/health carries no error text for the same reason (docs/ADMIN_API.md).
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class _Cursor:
    def __init__(self, row=(1,), error=None):
        self.row, self.error, self.sql = row, error, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if self.error:
            raise self.error

    def fetchone(self):
        return self.row

    def close(self):
        pass


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _Engine:
    _schema_prefix = "Graph_KG"

    def __init__(self, cursor):
        self.conn = _Conn(cursor)

    def _iris_obj(self):
        return object()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.setenv("BOLT_ENABLED", "0")
    monkeypatch.setenv("IRIS_HOST", "127.0.0.1")
    monkeypatch.setenv("IRIS_PORT", "1")


def _health(engine):
    from api.main import create_app

    return TestClient(create_app(engine=engine)).get("/health")


def test_a_database_that_answers_is_healthy():
    cursor = _Cursor()
    resp = _health(_Engine(cursor))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "healthy", "database": "connected", "graphql": "available"}
    assert cursor.sql == ["SELECT 1"]


def _logged(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == "api.main"]


def test_a_failing_query_is_503_with_the_reason_and_iris_text_in_the_log(caplog):
    with caplog.at_level(logging.WARNING, logger="api.main"):
        resp = _health(_Engine(_Cursor(error=RuntimeError("[SQLCODE: <-30>] connection lost"))))
    assert resp.status_code == 503, resp.text
    assert resp.json() == {"status": "unhealthy", "database": "unavailable", "graphql": "available",
                           "reason": "the database did not answer SELECT 1"}
    assert "connection lost" not in resp.text  # /health needs no key: IRIS's text is in the log, not the body
    assert _logged(caplog) == ["/health: the database did not answer SELECT 1 ([SQLCODE: <-30>] connection lost)"]


def test_a_wrong_answer_is_503(caplog):
    with caplog.at_level(logging.WARNING, logger="api.main"):
        resp = _health(_Engine(_Cursor(row=(0,))))
    assert resp.status_code == 503
    assert resp.json()["reason"] == "the database did not answer SELECT 1"
    assert _logged(caplog) == ["/health: the database did not answer SELECT 1 (it answered (0,), not 1)"]


def test_no_engine_is_503(caplog):
    from api.main import create_app

    with patch("api.main.get_engine", side_effect=RuntimeError("connection refused")):
        app = create_app()
    caplog.clear()  # the startup WARNING; this test is about the request's
    with caplog.at_level(logging.WARNING, logger="api.main"):
        resp = TestClient(app).get("/health")
    assert resp.status_code == 503
    assert resp.json()["database"] == "unavailable"
    assert resp.json()["reason"] == "the API started without the database"
    assert _logged(caplog) == ["/health: the API started without the database (connection refused)"]


@pytest.mark.parametrize("recorded, reason, detail", [
    ("OtherKG", "the namespace records another IVG Schema",
     "the namespace records OtherKG, and this API's engine asks for Graph_KG"),
    (None, "the namespace has no IVG Schema record: setup has not finished; run initialize_schema()",
     "no record; this API's engine asks for Graph_KG"),
])
def test_an_iris_namespace_whose_record_does_not_match_is_503(recorded, reason, detail, caplog):
    with patch("api.main.is_iris_connection", return_value=True), \
            patch("api.main.read_record", return_value=recorded), caplog.at_level(logging.WARNING, logger="api.main"):
        resp = _health(_Engine(_Cursor()))
    assert resp.status_code == 503, resp.text
    assert resp.json()["reason"] == reason
    assert _logged(caplog) == [f"/health: {reason} ({detail})"]


def test_an_iris_namespace_recording_the_schema_in_another_case_is_healthy():
    with patch("api.main.is_iris_connection", return_value=True), \
            patch("api.main.read_record", return_value="GRAPH_KG"):
        resp = _health(_Engine(_Cursor()))
    assert resp.status_code == 200, resp.text


def test_a_record_that_cannot_be_read_is_503():
    with patch("api.main.is_iris_connection", return_value=True), \
            patch("api.main.read_record", side_effect=RuntimeError("<PROTECT> ^IVG.Deploy")):
        resp = _health(_Engine(_Cursor()))
    assert resp.status_code == 503
    assert resp.json()["reason"] == "the IVG Schema record could not be read"
