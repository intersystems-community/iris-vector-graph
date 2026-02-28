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
