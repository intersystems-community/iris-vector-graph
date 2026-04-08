"""Mindwalk Cypher HTTP API — runs inside IRIS via WSGI or standalone via uvicorn."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    import iris
    _EMBEDDED = True
except ImportError:
    _EMBEDDED = False

from iris_vector_graph.engine import IRISGraphEngine

app = FastAPI(
    title="Mindwalk Cypher API",
    version="1.0.0",
    openapi_url="/openapi",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/db/"):
        expected = os.environ.get("IVG_API_KEY", "")
        provided = request.headers.get("X-API-Key", "")
        if not expected or provided != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "X-API-Key required"},
            )
    return await call_next(request)


class CypherRequest(BaseModel):
    query: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    limitRows: int = 1000


class Neo4jStatement(BaseModel):
    statement: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class Neo4jTxRequest(BaseModel):
    statements: list[Neo4jStatement]


_engine_cache: IRISGraphEngine | None = None


def _get_engine() -> IRISGraphEngine:
    global _engine_cache
    if _engine_cache is not None:
        return _engine_cache

    host = os.environ.get("IRIS_HOST")
    if host:
        port = int(os.environ.get("IRIS_PORT", "1972"))
        namespace = os.environ.get("IRIS_NAMESPACE", "USER")
        username = os.environ.get("IRIS_USERNAME", "_SYSTEM")
        password = os.environ.get("IRIS_PASSWORD", "SYS")
        conn = iris.connect(hostname=host, port=port, namespace=namespace,
                            username=username, password=password)
        _engine_cache = IRISGraphEngine(conn)
        return _engine_cache
    if _EMBEDDED:
        from iris_vector_graph.embedded import EmbeddedConnection
        _engine_cache = IRISGraphEngine(EmbeddedConnection())
        return _engine_cache
    raise RuntimeError("IRIS_HOST not set and embedded iris not available")


def _run_cypher(query: str, parameters: dict | None = None, limit: int = 1000) -> dict:
    engine = _get_engine()
    result = engine.execute_cypher(query, parameters=parameters or {})
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    if len(rows) > limit:
        rows = rows[:limit]
    return {
        "status": "OK",
        "columns": columns,
        "rows": rows,
        "rowCount": len(rows),
    }


@app.get("/health")
def health():
    try:
        engine = _get_engine()
        cursor = engine.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM Graph_KG.nodes")
        row = cursor.fetchone()
        node_count = row[0] if row else 0
        return {"status": "ok", "engine": True, "nodes": node_count}
    except Exception as e:
        return {"status": "ok", "engine": False, "error": str(e)}


@app.post("/api/cypher")
def cypher_query(req: CypherRequest):
    trace_id = str(uuid.uuid4())[:8]
    t0 = time.time()
    try:
        result = _run_cypher(req.query, req.parameters, req.limitRows)
        duration = int((time.time() - t0) * 1000)
        _log("POST", "/api/cypher", 200, duration, trace_id)
        return result
    except Exception as e:
        duration = int((time.time() - t0) * 1000)
        _log("POST", "/api/cypher", 400, duration, trace_id)
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "error": str(e),
            "trace_id": trace_id,
        })


@app.post("/db/neo4j/tx/commit")
def neo4j_tx_commit(req: Neo4jTxRequest):
    trace_id = str(uuid.uuid4())[:8]
    t0 = time.time()
    results = []
    errors = []

    for stmt in req.statements:
        try:
            r = _run_cypher(stmt.statement, stmt.parameters)
            data = [{"row": row, "meta": [_neo4j_meta(v) for v in row]} for row in r["rows"]]
            results.append({"columns": r["columns"], "data": data})
        except Exception as e:
            errors.append({
                "code": "Neo.ClientError.Statement.SyntaxError",
                "message": str(e),
            })

    duration = int((time.time() - t0) * 1000)
    status = 200 if not errors else 400
    _log("POST", "/db/neo4j/tx/commit", status, duration, trace_id)
    return JSONResponse(status_code=status, content={"results": results, "errors": errors})


@app.get("/db/neo4j")
@app.get("/")
def neo4j_discovery():
    return {
        "bolt_routing": None,
        "bolt_direct": None,
        "neo4j_version": "5.0.0-compat",
        "neo4j_edition": "community",
        "db/cluster": None,
        "db/data": "/db/neo4j/tx/commit",
        "db/management": None,
    }


@app.get("/db/neo4j/tx")
def neo4j_tx_endpoint():
    return {"commit": "/db/neo4j/tx/commit"}


def _neo4j_meta(value: Any) -> dict | None:
    if isinstance(value, dict) and "id" in value:
        return {"id": value.get("id"), "type": "node"}
    return None


def _log(method: str, path: str, status: int, duration_ms: int, trace_id: str):
    line = json.dumps({
        "method": method, "path": path, "status": status,
        "duration_ms": duration_ms, "trace_id": trace_id,
    })
    if _EMBEDDED:
        try:
            iris.cls("%SYS.System").WriteToConsoleLog(f"CypherAPI: {line}", 0, 0)
        except Exception:
            pass
    else:
        print(line)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

try:
    from a2wsgi import ASGIMiddleware as _ASGIMiddleware
    wsgi_app = _ASGIMiddleware(app)
except ImportError:
    wsgi_app = None
