"""Shared API authentication and access-control middleware for iris-vector-graph.

Provides:
- ApiKeyMiddleware: opt-in API key gate (IVG_API_KEY env var)
- ReadOnlyMiddleware: mutation rejection (IVG_READ_ONLY env var)

Both are no-ops when their respective env vars are unset, preserving full
backward compatibility for existing deployments.

Usage in FastAPI app::

    from iris_vector_graph.api_auth import ApiKeyMiddleware, ReadOnlyMiddleware

    app.add_middleware(ReadOnlyMiddleware)
    app.add_middleware(ApiKeyMiddleware)
"""
from __future__ import annotations

import os
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Routes that are always exempt from authentication — health checks, API docs.
_AUTH_EXEMPT: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})

# Route prefixes that require authentication when IVG_API_KEY is set.
_PROTECTED_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/fhir-event/",
    "/fhir-event",
    "/graphql",
)

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


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Opt-in API key authentication middleware.

    Activated only when ``IVG_API_KEY`` env var is non-empty.
    When active, all protected routes require the ``X-Api-Key`` header with
    the matching value.  Exempt routes (/health, /docs, etc.) always pass.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        expected = os.environ.get("IVG_API_KEY", "")
        if not expected:
            return await call_next(request)

        path = request.url.path
        if path in _AUTH_EXEMPT:
            return await call_next(request)

        if not any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("X-Api-Key", "")
        if provided != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "X-Api-Key header required"},
            )

        return await call_next(request)


class ReadOnlyMiddleware(BaseHTTPMiddleware):
    """Opt-in read-only enforcement middleware.

    Activated when ``IVG_READ_ONLY`` env var is ``"true"`` (case-insensitive).

    When active:
    - Blocks write HTTP methods (POST/PUT/PATCH/DELETE) on ``/fhir-event/``
      unless ``IVG_ALLOW_FHIR_WRITES=true``.
    - GraphQL mutation blocking is handled at the resolver level (see
      ``api/gql/schema.py``) — this middleware does not inspect GraphQL bodies.
    - Cypher mutation keyword blocking is handled in the Cypher router
      (``api/routers/cypher.py``) — this middleware does not inspect request bodies.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if os.environ.get("IVG_READ_ONLY", "").lower() != "true":
            return await call_next(request)

        path = request.url.path
        method = request.method.upper()

        # Block write methods on fhir-event unless explicitly allowed.
        if (path.startswith("/fhir-event")
                and method in _WRITE_METHODS
                and os.environ.get("IVG_ALLOW_FHIR_WRITES", "").lower() != "true"):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "read_only_mode",
                    "detail": "fhir-event writes are disabled; set IVG_ALLOW_FHIR_WRITES=true to allow",
                },
            )

        return await call_next(request)


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
