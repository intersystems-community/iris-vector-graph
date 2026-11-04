"""Shared API authentication and access-control middleware for iris-vector-graph.

Provides:
- ApiKeyMiddleware: opt-in API key gate (IVG_API_KEY env var)
- ReadOnlyMiddleware: write rejection by path (IVG_READ_ONLY env var)
- is_mutation_cypher: the Cypher check routes that carry a query apply themselves

Both are no-ops when their respective env vars are unset, preserving full
backward compatibility for existing deployments.

Usage in FastAPI app::

    from iris_vector_graph.api_auth import ApiKeyMiddleware, ReadOnlyMiddleware

    app.add_middleware(ReadOnlyMiddleware)
    app.add_middleware(ApiKeyMiddleware)
    app.add_middleware(CORSMiddleware, ...)   # outermost: answers preflights

Starlette runs the last-added middleware first.
"""
from __future__ import annotations

import os
from typing import Callable, Iterable

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from iris_vector_graph._api_key import API_KEY_HEADER, configured_key, request_is_authorized

# The public allow-list: the only paths served without the key when IVG_API_KEY is set.
# The health probe and the API docs pages, which were exempt before the gate became
# default-deny (R097). Matched exactly against the decoded request path, which is the
# value the router dispatches on, so a case, slash or encoding variant of a public path
# is not public. An app whose OpenAPI document lives elsewhere passes it in
# ``public_paths`` (cypher_api serves it at ``/openapi``).
PUBLIC_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})

# Cypher keywords that constitute mutations (case-insensitive word-boundary check).
_MUTATION_KEYWORDS: tuple[str, ...] = (
    "CREATE",
    "DELETE",
    "MERGE",
    "SET",
    "REMOVE",
    "FOREACH",
)

# HTTP methods that constitute writes on the fhir-event endpoint.
_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class ApiKeyMiddleware:
    """Opt-in API key gate, default-deny.

    Activated only when ``IVG_API_KEY`` is non-empty. Then every HTTP request and every
    WebSocket upgrade needs the key in the ``X-Api-Key`` header, except:

    - HTTP requests to a path in :data:`PUBLIC_PATHS` or ``public_paths``;
    - WebSocket upgrades to a path in ``in_protocol_ws_paths``: a protocol that
      authenticates inside the session (Bolt's HELLO/LOGON), because a browser cannot put
      a header on a WebSocket upgrade.

    A pure ASGI middleware rather than ``BaseHTTPMiddleware``, which never sees WebSocket
    scopes. Add it after ``ReadOnlyMiddleware`` (so it runs first: an unauthenticated
    write gets 401, not a 403 that reveals the mode) and before ``CORSMiddleware`` (so
    CORS answers preflights, which never carry the key, and decorates the 401).
    """

    def __init__(
        self,
        app: ASGIApp,
        public_paths: Iterable[str] = (),
        in_protocol_ws_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.public_paths = PUBLIC_PATHS | frozenset(public_paths)
        self.in_protocol_ws_paths = frozenset(in_protocol_ws_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        expected = configured_key()
        if kind not in ("http", "websocket") or not expected:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        exempt = self.public_paths if kind == "http" else self.in_protocol_ws_paths
        if path in exempt or request_is_authorized(Headers(scope=scope), expected):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            # Closing before accept makes the server refuse the upgrade (HTTP 403).
            await WebSocketClose(code=1008, reason="unauthorized")(scope, receive, send)
            return
        response = JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": f"{API_KEY_HEADER} header required"},
        )
        await response(scope, receive, send)


class ReadOnlyMiddleware(BaseHTTPMiddleware):
    """Opt-in read-only enforcement middleware.

    Activated when ``IVG_READ_ONLY`` env var is ``"true"`` (case-insensitive).

    When active it refuses, with ``403`` and ``{"error": "read_only_mode"}``:

    - write HTTP methods (POST/PUT/PATCH/DELETE) on ``/fhir-event/``, unless
      ``IVG_ALLOW_FHIR_WRITES=true``;
    - write HTTP methods on any path under one of ``write_prefixes``, except the
      paths in ``read_paths`` (a POST that only reads, such as ``/admin/explain``).
      A route added under a prefix later is refused unless it is named a read.

    Bodies are not inspected: a route that carries Cypher refuses a mutation itself
    (:func:`is_mutation_cypher`), and GraphQL mutations refuse at the resolver
    (``api/gql/schema.py``). Add it before ``ApiKeyMiddleware``, so the key check runs
    first.
    """

    def __init__(
        self,
        app: ASGIApp,
        write_prefixes: Iterable[str] = (),
        read_paths: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        self.write_prefixes = tuple(write_prefixes)
        self.read_paths = frozenset(read_paths)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not read_only_enabled():
            return await call_next(request)

        path = request.url.path
        method = request.method.upper()
        if method not in _WRITE_METHODS:
            return await call_next(request)

        # Block write methods on fhir-event unless explicitly allowed.
        if (path.startswith("/fhir-event")
                and os.environ.get("IVG_ALLOW_FHIR_WRITES", "").lower() != "true"):
            return read_only_refusal(
                "fhir-event writes are disabled; set IVG_ALLOW_FHIR_WRITES=true to allow"
            )

        if path.startswith(self.write_prefixes) and path not in self.read_paths:
            return read_only_refusal(f"{method} {path} writes; the server is read-only (IVG_READ_ONLY)")

        return await call_next(request)


def read_only_enabled() -> bool:
    """Whether ``IVG_READ_ONLY`` is ``true`` (case-insensitive)."""
    return os.environ.get("IVG_READ_ONLY", "").lower() == "true"


def read_only_refusal(detail: str) -> JSONResponse:
    """The ``403`` a write gets under ``IVG_READ_ONLY``."""
    return JSONResponse(status_code=403, content={"error": "read_only_mode", "detail": detail})


def is_mutation_cypher(query: str) -> bool:
    """Return True if the Cypher query string contains a mutation keyword.

    Used by the Cypher router to enforce ``IVG_READ_ONLY`` on query content.
    Case-insensitive, whole-word match to avoid false positives in strings.
    """
    import re
    upper = query.upper()
    for kw in _MUTATION_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return True
    return False
