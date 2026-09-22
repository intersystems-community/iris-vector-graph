"""FastAPI dependencies shared by the REST routers."""

from __future__ import annotations

from typing import Any, Generator

from fastapi import HTTPException, Request


def _resolve_connection(request: Request) -> Any | None:
    """Look up an IRIS DB-API connection stored on the FastAPI app state."""

    for attr in ("db_connection", "iris_connection", "connection"):
        conn = getattr(request.app.state, attr, None)
        if conn is not None:
            return conn

    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        return getattr(engine, "conn", None)

    return None


def get_engine_optional(request: Request) -> Any | None:
    """Yield the `IRISGraphEngine` on app state, or `None` when there is none.

    Unlike `get_db_connection` this never raises: an endpoint that has a connection
    can still answer without an engine. What it loses is the engine's build probes,
    and one of those is load-bearing — `_fetch_first_unsafe` is what makes the
    translator emit `TOP` instead of `FETCH FIRST` on the IRIS builds that SIGSEGV
    on `FETCH FIRST` over a multi-table JOIN.
    """

    return getattr(request.app.state, "engine", None)


def get_db_connection(request: Request) -> Generator[Any, None, None]:
    """FastAPI dependency that yields an IRIS DB-API connection."""

    connection = _resolve_connection(request)
    if connection is None:
        raise HTTPException(status_code=500, detail="IRIS database connection is unavailable")

    try:
        yield connection
    finally:
        # Connection ownership is managed externally (usually the component that
        # instantiated the FastAPI app), so we do not close it here.
        pass
