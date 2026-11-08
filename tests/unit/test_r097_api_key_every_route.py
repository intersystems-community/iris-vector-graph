"""R097: with IVG_API_KEY set, every route of every IVG app requires the key.

The gate used to list the prefixes it protected (`/api/`, `/fhir-event`, `/graphql`), so
everything else — `/admin/*`, `/schema`, `/db/*`, `/stats`, `/metrics`, the Neo4j
discovery document — answered without a key. These tests enumerate the routes each
app actually registers (`app.routes`, recursively for mounts) and hold every one of
them, except a small public allow-list, to the key. A route added later is covered by
the enumeration without anyone having to remember this file: that is the default-deny
property, tested.

No IRIS: the engine is a MagicMock, and the unauthenticated half of each test asserts
the engine was never touched, i.e. the handler did not run.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI, WebSocket  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Mount  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from tests.helpers.app_routes import served  # noqa: E402

KEY = "r097-secret-key"
GOOD = {"X-Api-Key": KEY}
DENIED = (401, 403)

# The public allow-list, per app: the health probe and the API docs pages, which the
# middleware treated as exempt before R097. The OpenAPI document is the app's own
# `openapi_url` (cypher_api moved it to `/openapi`).
PUBLIC_BASE = {"/health", "/docs", "/redoc"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    monkeypatch.delenv("IVG_ALLOW_FHIR_WRITES", raising=False)
    monkeypatch.setenv("BOLT_ENABLED", "0")
    # api.main builds a module-level app at import; make its IRIS connect fail fast
    # instead of reaching whatever listens on the default port.
    monkeypatch.setenv("IRIS_HOST", "127.0.0.1")
    monkeypatch.setenv("IRIS_PORT", "1")


def _engine() -> MagicMock:
    eng = MagicMock(name="engine")
    eng.execute_cypher.return_value = MagicMock(
        columns=["c"], rows=[[1]], error=None, bolt_column_types=None,
        metadata=MagicMock(warnings=[], index_usage=[], optimization_applied=[]),
    )
    return eng


@pytest.fixture
def cypher_api():
    eng = _engine()
    from iris_vector_graph import cypher_api as mod

    with patch.object(mod, "_make_engine", return_value=eng), \
         patch.object(mod, "_get_engine", return_value=eng), \
         patch.object(mod, "_engine_cache", eng):
        yield mod.app, eng


@pytest.fixture
def main_app():
    eng = _engine()
    # /health runs SELECT 1 through the connection (4.6.3); this database answers it
    eng.conn.cursor.return_value.fetchone.return_value = (1,)
    from api.main import create_app

    yield create_app(engine=eng), eng


@pytest.fixture
def gql_app():
    eng = _engine()
    from iris_vector_graph.gql import create_app

    app = create_app(eng)
    eng.reset_mock()  # create_app introspects labels to build the schema; not a request
    yield app, eng


APP_FIXTURES = ["cypher_api", "main_app", "gql_app"]


def _public(app: FastAPI) -> set[str]:
    return PUBLIC_BASE | {app.openapi_url}


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "probe", path) or "/"


def _http_requests(routes, prefix: str = ""):
    """(method, path) for every HTTP route, recursing into mounts (tests/helpers/app_routes.py: an included
    router's routes too, on any FastAPI)."""
    out = []
    for s in served(routes):
        path = prefix + s.path
        if isinstance(s.route, Mount):
            sub = getattr(s.route.app, "routes", None)
            if sub:
                out.extend(_http_requests(sub, path))
            else:  # StaticFiles and other leaf ASGI apps
                out.append(("GET", path + "/"))
                out.append(("GET", path + "/index.html"))
            continue
        if not s.methods:  # websocket route
            continue
        full = _concrete(path)
        for m in sorted(s.methods):
            out.append((m, full))
        if "GET" in s.methods and "HEAD" not in s.methods:
            out.append(("HEAD", full))
    return out


def _ws_paths(app: FastAPI) -> list[str]:
    return sorted({s.path for s in served(app.routes)
                   if not s.methods and not isinstance(s.route, Mount)} - {""})


def _send(client: TestClient, method: str, path: str, headers=None):
    kwargs = {"headers": headers or {}}
    if method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = {}
    return client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# Every route, every app
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", APP_FIXTURES)
def test_every_non_public_route_refuses_a_request_without_the_key(fixture, request):
    app, eng = request.getfixturevalue(fixture)
    client = TestClient(app, raise_server_exceptions=False)
    public = _public(app)
    reqs = [(m, p) for m, p in _http_requests(app.routes) if p not in public]
    assert reqs, "route enumeration found nothing; the test would pass vacuously"
    if fixture != "cypher_api":  # an included router's route (FastAPI 0.137+ keeps it off app.routes)
        assert ("POST", "/graphql") in reqs

    open_routes = []
    for method, path in reqs:
        resp = _send(client, method, path)
        if resp.status_code != 401:
            open_routes.append(f"{method} {path} -> {resp.status_code}")
    assert not open_routes, (
        f"{len(open_routes)}/{len(reqs)} routes of {fixture} answered without the key:\n  "
        + "\n  ".join(open_routes)
    )
    assert eng.mock_calls == [], f"a handler ran without the key: {eng.mock_calls[:5]}"


@pytest.mark.parametrize("fixture", APP_FIXTURES)
def test_every_route_accepts_the_key(fixture, request):
    app, _ = request.getfixturevalue(fixture)
    client = TestClient(app, raise_server_exceptions=False)
    refused = []
    for method, path in _http_requests(app.routes):
        resp = _send(client, method, path, GOOD)
        if resp.status_code in DENIED:
            refused.append(f"{method} {path} -> {resp.status_code}")
    assert not refused, "routes refused a correct key:\n  " + "\n  ".join(refused)


@pytest.mark.parametrize("fixture", APP_FIXTURES)
def test_the_public_allow_list_is_only_health_and_docs(fixture, request):
    app, _ = request.getfixturevalue(fixture)
    client = TestClient(app, raise_server_exceptions=False)
    registered = {p for _, p in _http_requests(app.routes)}
    answered_without_key = sorted(
        p for p in registered if _send(client, "GET", p).status_code not in DENIED
    )
    assert set(answered_without_key) <= _public(app), answered_without_key
    assert "/health" in answered_without_key
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("fixture", APP_FIXTURES)
def test_every_websocket_route_refuses_an_upgrade_without_the_key(fixture, request):
    """Bolt-over-WebSocket (`/` on cypher_api) authenticates inside the protocol (HELLO/LOGON),
    because a browser cannot set headers on a WebSocket; see test_r097_bolt_auth.py. Every
    other WebSocket route needs the key on the upgrade request."""
    app, eng = request.getfixturevalue(fixture)
    client = TestClient(app, raise_server_exceptions=False)
    in_protocol = {"/"} if fixture == "cypher_api" else set()
    paths = [p for p in _ws_paths(app) if p not in in_protocol]
    if fixture != "cypher_api":
        assert "/graphql" in paths
    for path in paths:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, subprotocols=["graphql-transport-ws"]) as ws:
                ws.send_json({"type": "connection_init"})
                ws.receive_json()
    assert eng.mock_calls == []


@pytest.mark.parametrize("fixture", ["main_app", "gql_app"])
def test_graphql_websocket_accepts_the_key(fixture, request):
    app, _ = request.getfixturevalue(fixture)
    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/graphql", subprotocols=["graphql-transport-ws"],
                                  headers=GOOD) as ws:
        ws.send_json({"type": "connection_init"})
        assert ws.receive_json()["type"] == "connection_ack"


# ---------------------------------------------------------------------------
# Path variants: case, trailing slash, encoding, traversal
# ---------------------------------------------------------------------------

VARIANTS = [
    "/admin/queries",
    "/admin/queries/",
    "/Admin/queries",
    "/ADMIN/QUERIES",
    "/admin",
    "/admin/",
    "/%61dmin/queries",
    "/admin%2Fqueries",
    "//admin/queries",
    "/schema/",
    "/Schema",
    "/db/neo4j/",
    "/health/",
    "/HEALTH",
    "/Health",
    "/health%2F..%2Fadmin%2Fqueries",
    "/health/../admin/queries",
    "/docs/../admin/queries",
    "/docs/oauth2-redirect",
    "/no-such-route",
    "/",
]


@pytest.mark.parametrize("path", VARIANTS)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "DELETE", "OPTIONS"])
def test_path_and_method_variants_without_the_key_are_refused(cypher_api, path, method):
    app, eng = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.request(method, path)
    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
    assert eng.mock_calls == []


def test_a_default_public_path_the_app_does_not_serve_answers_nothing(cypher_api):
    # cypher_api serves its OpenAPI document at /openapi; /openapi.json stays on the
    # shared allow-list for the other apps, and here reaches no route.
    app, eng = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/openapi").status_code == 200
    assert eng.mock_calls == []


def test_query_string_does_not_change_the_public_path(cypher_api):
    app, _ = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/health?verbose=1").status_code == 200
    assert client.get("/stats?x=/health").status_code == 401


# ---------------------------------------------------------------------------
# The key itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {},
    {"X-Api-Key": ""},
    {"X-Api-Key": "wrong"},
    {"X-Api-Key": KEY + "x"},
    {"X-Api-Key": KEY[:-1]},
    {"X-Api-Key": KEY.upper()},
    {"X-Api-Key": f" {KEY}"},
    {"X-Api-Key": "clé".encode("utf-8")},
    {"X-Api-Key": KEY, "x-api-key": "wrong"},
])
def test_a_wrong_or_empty_key_is_refused(cypher_api, headers):
    app, eng = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/stats", headers=headers)
    if headers.get("x-api-key") == "wrong" and len(headers) == 2:
        # httpx merges case-insensitive duplicates into one header; whichever value wins,
        # the request must not crash the gate.
        assert resp.status_code in (200, 401)
        return
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"
    assert eng.mock_calls == []


def test_the_key_header_name_is_case_insensitive(cypher_api):
    app, _ = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/stats", headers={"x-api-key": KEY}).status_code not in DENIED


def test_a_non_ascii_server_key_does_not_crash_the_gate(cypher_api, monkeypatch):
    """`hmac.compare_digest` raises TypeError on non-ASCII str; the gate compares bytes."""
    monkeypatch.setenv("IVG_API_KEY", "clé-secrète")
    app, _ = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/stats", headers={"X-Api-Key": "wrong"}).status_code == 401


def test_the_comparison_is_constant_time(cypher_api):
    import hmac

    from iris_vector_graph import _api_key

    app, _ = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    with patch.object(_api_key.hmac, "compare_digest", wraps=hmac.compare_digest) as spy:
        assert client.get("/stats", headers=GOOD).status_code not in DENIED
        assert client.get("/stats", headers={"X-Api-Key": "nope"}).status_code == 401
    assert spy.call_count >= 2


def test_no_key_configured_leaves_every_route_open(cypher_api, monkeypatch):
    monkeypatch.delenv("IVG_API_KEY")
    app, _ = cypher_api
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/stats").status_code not in DENIED
    assert client.post("/admin/explain", json={"query": "MATCH (n) RETURN n"}).status_code \
        not in DENIED


# ---------------------------------------------------------------------------
# Default-deny on a bare app, and CORS
# ---------------------------------------------------------------------------

def _bare_app() -> FastAPI:
    from iris_vector_graph.api_auth import ApiKeyMiddleware

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/brand-new-route")
    def brand_new():
        return {"data": "secret"}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("secret")
        await websocket.close()

    app.add_middleware(ApiKeyMiddleware)
    return app


def test_a_route_nobody_listed_is_protected():
    client = TestClient(_bare_app())
    assert client.get("/brand-new-route").status_code == 401
    assert client.get("/brand-new-route", headers=GOOD).json() == {"data": "secret"}
    assert client.get("/health").status_code == 200


def test_a_websocket_nobody_listed_is_protected():
    client = TestClient(_bare_app())
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_text()
    with client.websocket_connect("/ws", headers=GOOD) as ws:
        assert ws.receive_text() == "secret"


def test_lifespan_passes_through_the_gate():
    app = _bare_app()
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("fixture", ["cypher_api", "main_app"])
def test_cors_preflight_is_answered_without_running_a_handler(fixture, request):
    """A browser's preflight never carries the key, so CORS must sit outside the gate:
    otherwise no browser client can send the key at all, and a 401 reaches the page
    without CORS headers, unreadable."""
    app, eng = request.getfixturevalue(fixture)
    client = TestClient(app, raise_server_exceptions=False)
    pre = client.options("/api/cypher", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key, content-type",
    })
    assert pre.status_code == 200
    assert "access-control-allow-origin" in pre.headers
    denied = client.post("/api/cypher", json={"query": "MATCH (n) RETURN n"},
                         headers={"Origin": "http://localhost:3000"})
    assert denied.status_code == 401
    assert "access-control-allow-origin" in denied.headers
    assert eng.mock_calls == []


# ---------------------------------------------------------------------------
# IVG_READ_ONLY interplay (api.main mounts ReadOnlyMiddleware)
# ---------------------------------------------------------------------------

def test_read_only_does_not_open_or_reveal_anything_without_the_key(main_app, monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = main_app
    client = TestClient(app, raise_server_exceptions=False)
    # Unauthenticated: 401 before the read-only check, so nothing is revealed about mode.
    assert client.post("/fhir-event/", json={}).status_code == 401
    assert client.post("/api/cypher", json={"query": "CREATE (n) RETURN n"}).status_code == 401
    assert client.post("/api/cypher", json={"query": "MATCH (n) RETURN n"}).status_code == 401
    assert eng.mock_calls == []


def test_read_only_still_applies_with_the_key(main_app, monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, _ = main_app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/fhir-event/", json={}, headers=GOOD)
    assert resp.status_code == 403
    assert resp.json()["error"] == "read_only_mode"
    resp = client.post("/api/cypher", json={"query": "CREATE (n) RETURN n"}, headers=GOOD)
    assert resp.status_code == 403
