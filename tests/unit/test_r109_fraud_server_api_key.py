"""R109: the fraud scoring server (`src/iris_fraud_server`) holds every route to IVG_API_KEY.

It is a demo, but a deployable one: `irispython -m iris_fraud_server` binds 0.0.0.0 and
`POST /fraud/score` runs SQL in the IRIS process it lives in. It had no key check at all.
It now uses the same default-deny `ApiKeyMiddleware` as the other IVG servers (a no-op
while `IVG_API_KEY` is unset), and the demo page's client sends `FRAUD_API_KEY`.

The server imports torch and structlog at module level; neither is in the `dev` or
`full` extra, and neither has anything to do with authentication. When one is not
installed the test puts an inert stand-in in `sys.modules` for the import; the handlers
the test reaches never call it.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from tests.helpers.app_routes import served  # noqa: E402

SRC = Path(__file__).resolve().parents[2] / "src"
KEY = "r109-key"
PUBLIC = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


@pytest.fixture
def fraud_app(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    for name in ("torch", "structlog"):
        if importlib.util.find_spec(name) is None:
            monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    monkeypatch.delitem(sys.modules, "iris_fraud_server.app", raising=False)
    mod = importlib.import_module("iris_fraud_server.app")
    scored = MagicMock(name="score_mlp_mode")
    monkeypatch.setattr(mod, "score_mlp_mode", scored)
    sql = MagicMock(name="iris.sql")
    monkeypatch.setattr(mod.iris, "sql", sql, raising=False)
    # Not a context manager: the startup hook (model and centroid load) does not run.
    return TestClient(mod.create_app(), raise_server_exceptions=False), scored, sql


def _requests(app):
    out = []
    for s in served(app.routes):  # an included router's routes too (tests/helpers/app_routes.py)
        if isinstance(s.route, Mount) or not s.methods:
            continue
        for m in sorted(s.methods):
            out.append((m, re.sub(r"\{[^}]+\}", "x", s.path)))
    return out


SCORE = {"mode": "MLP", "payer": "acct:a", "device": "dev:b", "ip": "ip:c", "merchant": "m:d"}


def test_every_route_refuses_without_the_key(fraud_app, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    client, scored, sql = fraud_app
    reqs = [(m, p) for m, p in _requests(client.app) if p not in PUBLIC]
    assert ("POST", "/fraud/score") in reqs and ("GET", "/fraud/health") in reqs
    open_routes = []
    for method, path in reqs:
        resp = client.request(method, path, json=SCORE if method == "POST" else None)
        if resp.status_code != 401:
            open_routes.append((method, path, resp.status_code))
    assert not open_routes
    scored.assert_not_called()
    sql.exec.assert_not_called()


def test_score_runs_with_the_key(fraud_app, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    client, scored, _ = fraud_app
    scored.return_value = {"prob": 0.1, "reasons": ["a", "b", "c"], "trace_id": "t",
                           "mode": "MLP", "timestamp": "now"}
    resp = client.post("/fraud/score", json=SCORE, headers={"X-Api-Key": KEY})
    assert resp.status_code == 200, resp.text
    assert resp.json()["prob"] == 0.1
    scored.assert_called_once()


def test_no_key_configured_keeps_the_demo_open(fraud_app, monkeypatch):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    client, _, sql = fraud_app
    resp = client.get("/fraud/health")
    assert resp.status_code == 200
    sql.exec.assert_called_once()


def test_the_demo_page_client_sends_fraud_api_key(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    monkeypatch.setenv("FRAUD_API_KEY", KEY)
    from iris_demo_server.services.fraud_client import FraudAPIClient

    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"fraud_probability": 0.42})

    fc = FraudAPIClient(base_url="http://fraud.test")
    fc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(fc.score_transaction({"payer": "acct:a", "amount": 1.0}))
    assert seen["key"] == KEY
    assert result["fraud_probability"] == 0.42
