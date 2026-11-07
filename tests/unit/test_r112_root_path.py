"""R112: the read-only and API-key gates hold behind a path prefix (`root_path`).

`ReadOnlyMiddleware` matched `request.url.path` and `ApiKeyMiddleware` matched
`scope["path"]`. On Starlette 0.35+ both keep the `root_path` prefix, which the router
strips before matching. Behind `uvicorn --root-path /ivg`, a proxy that sets `root_path`,
or a parent app that mounts IVG at `/ivg`, `POST /ivg/admin/load` routed to `/admin/load`
while the read-only check saw `/ivg/admin/load`, which is not under `/admin/`: the load ran
under `IVG_READ_ONLY=true`. The key gate failed the other way: `/ivg/health` was not on the
public allow-list, so a prefixed health probe needed the key.

Each test runs two ways of putting an app under `/ivg`:

- ``root_path``: the server sets `root_path` (uvicorn `--root-path`, a proxy, a2wsgi);
- ``mount``: a parent Starlette app mounts the IVG app at `/ivg`.

The write routes are enumerated from each app, as in R107, so a route added later is held
to the prefixed mode too. No IRIS: engines are MagicMocks, and a refused request must
leave them untouched.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from tests.helpers.app_routes import served  # noqa: E402

PREFIX = "/ivg"
KEY = "r112-key"
GOOD = {"X-Api-Key": KEY}
WRITE = "CREATE (n:Probe {id: 'r112'}) RETURN n"
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
HOW = ["root_path", "mount"]

# Non-GET routes that only read.
READ_POSTS = {
    "cypher_api": {("POST", "/admin/explain")},
    # /fraud/score runs SELECTs only (feature lookups and the model); the server has no
    # route that writes.
    "fraud": {("POST", "/fraud/score")},
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("IVG_API_KEY", raising=False)
    monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    monkeypatch.delenv("IVG_ALLOW_FHIR_WRITES", raising=False)
    monkeypatch.setenv("BOLT_ENABLED", "0")
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
    from api.main import create_app

    yield create_app(engine=eng), eng


@pytest.fixture
def gql_app():
    eng = _engine()
    from iris_vector_graph.gql import create_app

    app = create_app(eng)
    eng.reset_mock()  # create_app introspects labels to build the schema; not a request
    yield app, eng


@pytest.fixture
def fraud(monkeypatch):
    import importlib
    import importlib.util
    import sys
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "src"))
    for name in ("torch", "structlog"):
        if importlib.util.find_spec(name) is None:
            monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    monkeypatch.delitem(sys.modules, "iris_fraud_server.app", raising=False)
    mod = importlib.import_module("iris_fraud_server.app")
    eng = MagicMock(name="score_mlp_mode")
    monkeypatch.setattr(mod, "score_mlp_mode", eng)
    monkeypatch.setattr(mod.iris, "sql", eng.sql, raising=False)
    yield mod.create_app(), eng


APPS = ["cypher_api", "main_app", "gql_app", "fraud"]


def _client(app, how: str) -> TestClient:
    if how == "root_path":
        return TestClient(app, root_path=PREFIX, raise_server_exceptions=False)
    parent = Starlette(routes=[Mount(PREFIX, app=app)])
    return TestClient(parent, raise_server_exceptions=False)


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "123", path) or "/"


def _routes(app):
    """(method, path) for every HTTP route of the app (mounts' leaf apps as a GET; an included router's routes
    too, on any FastAPI: tests/helpers/app_routes.py)."""
    out = []
    for s in served(app.routes):
        if isinstance(s.route, Mount):
            out.append(("GET", s.path + "/"))
            continue
        if not s.methods:  # websocket
            continue
        for m in sorted(s.methods):
            out.append((m, _concrete(s.path)))
    return out


def _write_routes(name, app):
    return [(m, p) for m, p in _routes(app)
            if m not in READ_METHODS and (m, p) not in READ_POSTS.get(name, set())]


# A valid FHIR event: the fhir-event route materializes a node from it.
FHIR_EVENT = {
    "resourceType": "Condition",
    "id": "c-r112",
    "fhirUrl": "http://fhir.test/Condition/c-r112",
    "patientRef": "Patient/p-r112",
}

# A body that carries the write in every field a Cypher route reads, and a valid FHIR
# event, so a refusal is the gate's and not a 422.
WRITE_BODY = {
    **FHIR_EVENT,
    "query": WRITE,
    "statement": WRITE,
    "statements": [{"statement": WRITE}],
    "parameters": {},
}

# GraphQL writes: the biomedical mutation (api.main) and a CREATE through the Cypher field
# each schema carries (`executeCypher` on api.main, `cypher` on gql.create_app).
GRAPHQL_WRITES = [
    'mutation { createProtein(input: {id: "PROT:r112", name: "r112"}) { id } }',
    '{ executeCypher(query: "%s") }' % WRITE.replace('"', '\\"'),
    '{ cypher(query: "%s") { columns rows } }' % WRITE.replace('"', '\\"'),
]


def _refused(resp) -> bool:
    if resp.status_code == 403:
        return True
    if resp.status_code != 200:
        return False
    body = resp.json()
    if body.get("errors"):
        return any("read-only" in str(e.get("message", "")).lower() for e in body["errors"])
    # A field that answers its error in the data (api.main's executeCypher, a JSON string).
    data = body.get("data") or {}
    return bool(data) and all("read-only mode" in str(v).lower() for v in data.values())


# ---------------------------------------------------------------------------
# IVG_READ_ONLY behind the prefix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("name", APPS)
def test_every_write_route_refuses_under_read_only_behind_the_prefix(name, how, request,
                                                                     monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = request.getfixturevalue(name)
    client = _client(app, how)
    writes = _write_routes(name, app)
    if name == "fraud":
        assert writes == [], f"the fraud server grew a write route: {writes}"
        return
    assert writes, "route enumeration found no write route; the test would pass vacuously"

    ran = []
    for method, path in writes:
        bodies = [None] if method == "DELETE" else [WRITE_BODY]
        if path.rstrip("/") == "/graphql":
            bodies = [{"query": q} for q in GRAPHQL_WRITES]
        for body in bodies:
            eng.reset_mock()
            resp = client.request(method, PREFIX + path, json=body, headers=GOOD)
            not_in_this_schema = (
                path.rstrip("/") == "/graphql" and resp.status_code == 200
                and ("Cannot query field" in resp.text
                     or "Schema is not configured to execute mutation" in resp.text  # graphql-core 3.2
                     or "mutation operation is not supported by the schema" in resp.text)  # 3.3
            )
            if not_in_this_schema and not eng.mock_calls:  # the other schema's field
                continue
            if not _refused(resp) or eng.mock_calls:
                ran.append((method, PREFIX + path, (body or {}).get("query", "")[:40],
                            resp.status_code, resp.text[:120], eng.mock_calls[:2]))
    assert not ran, f"{name}: write routes that ran under IVG_READ_ONLY=true behind " \
                    f"{PREFIX} ({how}):\n  " + "\n  ".join(map(str, ran))


@pytest.mark.parametrize("how", HOW)
def test_admin_load_behind_the_prefix_is_refused(cypher_api, how, monkeypatch):
    """The reported case: `--root-path /ivg` and `POST /ivg/admin/load`."""
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = cypher_api
    resp = _client(app, how).post(PREFIX + "/admin/load", content=b'{"kind":"node","id":"x"}\n')
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "read_only_mode"
    eng.import_graph_ndjson.assert_not_called()


@pytest.mark.parametrize("how", HOW)
def test_admin_load_behind_the_prefix_runs_when_read_only_is_off(cypher_api, how):
    app, eng = cypher_api
    eng.import_graph_ndjson.return_value = {"nodes": 1}
    resp = _client(app, how).post(PREFIX + "/admin/load", content=b'{"kind":"node","id":"x"}\n')
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"] == {"nodes": 1}


@pytest.mark.parametrize("how", HOW)
def test_explain_behind_the_prefix_still_answers_under_read_only(cypher_api, how,
                                                                monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = cypher_api
    resp = _client(app, how).post(PREFIX + "/admin/explain",
                                  json={"query": "MATCH (n) RETURN n"})
    assert resp.status_code == 200, resp.text
    assert "SELECT" in resp.json()["sql"].upper()
    eng.execute_cypher.assert_not_called()


@pytest.mark.parametrize("how", HOW)
def test_fhir_event_behind_the_prefix_is_refused(main_app, how, monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = main_app
    resp = _client(app, how).post(PREFIX + "/fhir-event/", json=FHIR_EVENT)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "read_only_mode"
    assert eng.mock_calls == []


# Spellings of /admin/load (and /admin/queries/...) behind the prefix. Each either reaches
# no route or is refused; none runs the handler.
ADMIN_VARIANTS = [
    "/ivg/admin/load",
    "/ivg/admin/load/",
    "/ivg/admin//load",
    "/ivg//admin/load",
    "/ivg/Admin/load",
    "/ivg/ADMIN/LOAD",
    "/IVG/admin/load",
    "/ivg/%61dmin/load",
    "/%69vg/admin/load",
    "/ivg/admin%2Fload",
    "/ivg/admin/explain/../load",
    "/ivg/./admin/load",
    "/ivg/admin/load?x=/admin/explain",
    "/ivg/admin/explain/",
    "/ivg/admin/Explain",
]


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("path", ADMIN_VARIANTS)
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_admin_path_variants_behind_the_prefix_never_write(cypher_api, how, path, method,
                                                         monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = cypher_api
    resp = _client(app, how).request(method, path, content=b'{"kind":"node","id":"x"}\n')
    assert resp.status_code in (403, 404, 405), f"{method} {path} -> {resp.status_code}"
    assert eng.mock_calls == [], f"{method} {path} ran {eng.mock_calls[:2]}"


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("path", [
    "/ivg/fhir-event/", "/ivg/fhir-event", "/ivg/fhir-event//", "/ivg/%66hir-event/",
    "/ivg//fhir-event/", "/ivg/FHIR-EVENT/",
])
def test_fhir_event_variants_behind_the_prefix_never_write(main_app, how, path,
                                                          monkeypatch):
    monkeypatch.setenv("IVG_READ_ONLY", "true")
    app, eng = main_app
    resp = _client(app, how).post(path, json=FHIR_EVENT, follow_redirects=True)
    assert resp.status_code in (403, 404, 405), f"{path} -> {resp.status_code}"
    assert eng.mock_calls == []


# ---------------------------------------------------------------------------
# IVG_API_KEY behind the prefix
# ---------------------------------------------------------------------------

def _public(name, app) -> set[str]:
    base = {"/docs", "/redoc", app.openapi_url}
    return base if name == "fraud" else base | {"/health"}


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("name", APPS)
def test_every_non_public_route_needs_the_key_behind_the_prefix(name, how, request,
                                                               monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    app, eng = request.getfixturevalue(name)
    client = _client(app, how)
    public = _public(name, app)
    reqs = [(m, p) for m, p in _routes(app) if p not in public]
    assert reqs
    open_routes = []
    for method, path in reqs:
        resp = client.request(method, PREFIX + path,
                              json={} if method in ("POST", "PUT", "PATCH") else None)
        if resp.status_code != 401:
            open_routes.append(f"{method} {PREFIX}{path} -> {resp.status_code}")
    assert not open_routes, "\n  ".join(open_routes)
    assert eng.mock_calls == []


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("name", APPS)
def test_every_route_accepts_the_key_behind_the_prefix(name, how, request, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    app, _ = request.getfixturevalue(name)
    client = _client(app, how)
    refused = []
    for method, path in _routes(app):
        resp = client.request(method, PREFIX + path, headers=GOOD,
                              json={} if method in ("POST", "PUT", "PATCH") else None)
        if resp.status_code in (401, 403):
            refused.append(f"{method} {PREFIX}{path} -> {resp.status_code}")
    assert not refused, "\n  ".join(refused)


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("name", ["cypher_api", "main_app", "gql_app"])
def test_health_behind_the_prefix_is_public(name, how, request, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    app, _ = request.getfixturevalue(name)
    resp = _client(app, how).get(PREFIX + "/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == ("ok" if name == "cypher_api" else "healthy")


@pytest.mark.parametrize("how", HOW)
def test_the_openapi_document_behind_the_prefix_is_public(cypher_api, how, monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    app, _ = cypher_api
    resp = _client(app, how).get(PREFIX + "/openapi")
    assert resp.status_code == 200
    assert "/admin/load" in resp.json()["paths"]


# Spellings that are not the public path as the router sees it: every one needs the key.
KEY_VARIANTS = [
    "/ivg/health/",
    "/ivg/HEALTH",
    "/ivg/Health",
    "/IVG/health",
    "/ivg//health",
    "/ivg/health%2F..%2Fstats",
    "/ivg/health/../stats",
    "/ivg/stats",
    "/ivg/stats?x=/health",
    "/ivg",
    "/ivg/",
    "/ivgx/health",
    "/ivg/ivg/health",
    "/stats",
]


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("path", KEY_VARIANTS)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_key_path_variants_behind_the_prefix_need_the_key(cypher_api, how, path, method,
                                                        monkeypatch):
    monkeypatch.setenv("IVG_API_KEY", KEY)
    app, eng = cypher_api
    resp = _client(app, how).request(method, path)
    # A mount answers 404 for a path outside it before IVG's middleware runs.
    allowed = (401, 404) if how == "mount" else (401,)
    assert resp.status_code in allowed, f"{method} {path} -> {resp.status_code}"
    assert eng.mock_calls == []


@pytest.mark.parametrize("how", HOW)
def test_bolt_websocket_behind_the_prefix_authenticates_in_protocol(cypher_api, how,
                                                                   monkeypatch):
    """`/` is the in-protocol WebSocket path (Bolt's HELLO/LOGON); behind the prefix it is
    `/ivg/`, and the upgrade must reach Bolt without the header."""
    monkeypatch.setenv("IVG_API_KEY", KEY)
    app, _ = cypher_api
    with _client(app, how).websocket_connect(PREFIX + "/") as ws:
        ws.send_bytes(b"\x60\x60\xb0\x17" + b"\x00\x00\x04\x04" + b"\x00" * 12)
        assert ws.receive_bytes() == b"\x00\x00\x04\x04"


def test_the_gates_match_on_the_routers_path_and_the_fallback_agrees_with_it():
    """The gates use Starlette's own `get_route_path` (Starlette >= 0.35; FastAPI >= 0.118
    needs >= 0.40). The copy used where Starlette has none must answer the same."""
    from starlette.routing import get_route_path

    from iris_vector_graph import api_auth

    assert api_auth.route_path is get_route_path
    for scope in [
        {"path": "/ivg/admin/load", "root_path": "/ivg"},
        {"path": "/admin/load", "root_path": "/ivg"},
        {"path": "/ivg", "root_path": "/ivg"},
        {"path": "/ivg/", "root_path": "/ivg"},
        {"path": "/ivg/admin/load", "root_path": "/ivg/"},
        {"path": "/a/b/admin/load", "root_path": "/a/b"},
        {"path": "/ivgx/admin/load", "root_path": "/ivg"},
        {"path": "/admin/load", "root_path": ""},
        {"path": "/admin/load"},
    ]:
        assert api_auth._strip_root_path(scope) == get_route_path(scope), scope
