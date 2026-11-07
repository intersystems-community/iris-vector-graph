"""The route enumeration the API-key and read-only tests walk finds the routes an included router serves.

FastAPI 0.137 stopped copying an included router's routes into the app: `include_router` adds one entry with no
`path` or `methods`. The R097, R107, R109 and R112 tests walked `app.routes` for those attributes, so on FastAPI
0.162 they found no `/graphql`, `/api/cypher` or `/fhir-event/` and held none of them to the key or to read-only
mode; the HTTP half still passed, on the routes the app declares itself. `tests/helpers/app_routes.py` enumerates the
routes as served on either FastAPI, and fails on an entry it cannot read rather than leave it out.

The expected routes are written here by hand from api/main.py and iris_vector_graph/gql, not read from the apps.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from fastapi import APIRouter, FastAPI, WebSocket  # noqa: E402
from starlette.routing import BaseRoute, Mount  # noqa: E402

from tests.helpers.app_routes import served  # noqa: E402


def _http(app) -> set:
    return {(m, s.path) for s in served(app.routes) if s.methods for m in s.methods}


def _ws(app) -> set:
    return {s.path for s in served(app.routes) if not s.methods and not isinstance(s.route, Mount)}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BOLT_ENABLED", "0")
    monkeypatch.setenv("IRIS_HOST", "127.0.0.1")
    monkeypatch.setenv("IRIS_PORT", "1")


def test_an_included_router_with_a_prefix_is_enumerated_with_the_prefix():
    router = APIRouter()

    @router.post("/run")
    def run():
        return {}

    @router.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.close()

    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.get("/own")
    def own():
        return {}

    assert {("POST", "/api/run"), ("GET", "/own")} <= _http(app)
    assert _ws(app) == {"/api/ws"}


def test_the_biomedical_app_serves_graphql_cypher_and_fhir_event():
    from api.main import create_app

    app = create_app(engine=MagicMock())
    assert {("GET", "/graphql"), ("POST", "/graphql"), ("POST", "/api/cypher"), ("POST", "/fhir-event/"),
            ("GET", "/health")} <= _http(app)
    assert _ws(app) == {"/graphql"}


def test_the_generic_graphql_app_serves_graphql_over_http_and_websocket():
    from iris_vector_graph.gql import create_app

    app = create_app(MagicMock())
    assert {("GET", "/graphql"), ("POST", "/graphql"), ("GET", "/health")} <= _http(app)
    assert _ws(app) == {"/graphql"}


def test_an_entry_it_cannot_read_fails_instead_of_dropping_out():
    class Opaque(BaseRoute):  # no path, no methods, not a FastAPI included router
        pass

    with pytest.raises(TypeError, match="Opaque"):
        served([Opaque()])
