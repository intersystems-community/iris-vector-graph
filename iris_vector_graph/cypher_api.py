"""Mindwalk Cypher HTTP API — runs inside IRIS via WSGI or standalone via uvicorn."""

from __future__ import annotations

import json
import os
import struct
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field

try:
    from iris_vector_graph.embedded import _ensure_embedded_iris_first
    _ensure_embedded_iris_first()
    import iris
    _EMBEDDED = hasattr(iris, 'cls')
except (ImportError, Exception):
    _EMBEDDED = False

from contextlib import asynccontextmanager

from iris_vector_graph.engine import IRISGraphEngine


@asynccontextmanager
async def _lifespan(app):
    from iris_vector_graph.bolt_server import start_tcp_bolt_server
    bolt_port = int(os.environ.get("BOLT_TCP_PORT", "7687"))
    bolt_enabled = os.environ.get("BOLT_ENABLED", "1") not in ("0", "false", "no")
    srv = None
    if bolt_enabled:
        try:
            srv = await start_tcp_bolt_server(lambda: _make_engine(), port=bolt_port)
            import logging
            logging.getLogger(__name__).info("Bolt TCP server on port %d", bolt_port)
        except OSError as e:
            import logging
            logging.getLogger(__name__).warning("Bolt TCP port %d unavailable: %s", bolt_port, e)
    yield
    if srv:
        srv.close()
        await srv.wait_closed()


app = FastAPI(
    title="Mindwalk Cypher API",
    version="1.0.0",
    openapi_url="/openapi",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_BROWSER_DIR = Path(__file__).parent / "browser_static"
if _BROWSER_DIR.exists():
    app.mount("/browser", StaticFiles(directory=str(_BROWSER_DIR), html=True), name="browser")


@app.get("/browser")
def browser_redirect():
    return RedirectResponse("/browser/")


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/db/"):
        expected = os.environ.get("IVG_API_KEY", "")
        provided = request.headers.get("X-API-Key", "")
        if expected and provided != expected:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "X-API-Key required"},
            )
    return await call_next(request)


from iris_vector_graph.bolt_server import BoltSession


class CypherRequest(BaseModel):
    query: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    limitRows: int = 1000
    fhir_patient_id: str | None = None
    fhir_base_url: str | None = None
    fhir_auth: tuple[str, str] | list[str] | None = None


@app.websocket("/")
@app.websocket("")
async def bolt_ws(ws: WebSocket):
    session = BoltSession(ws, _make_engine)
    await session.run()


class Neo4jStatement(BaseModel):
    statement: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class Neo4jTxRequest(BaseModel):
    statements: list[Neo4jStatement]


_engine_cache: IRISGraphEngine | None = None
_engine_lock = threading.Lock()
_IRIS_NAMESPACE = os.environ.get("IRIS_NAMESPACE", "USER")


def _make_engine() -> IRISGraphEngine:
    try:
        import iris as _iris_wrapper
        _state = _iris_wrapper.runtime.state
    except Exception:
        _iris_wrapper = None
        _state = "unavailable"

    host = os.environ.get("IRIS_HOST")

    if host:
        port = int(os.environ.get("IRIS_PORT", "1972"))
        namespace = os.environ.get("IRIS_NAMESPACE", "USER")
        username = os.environ.get("IRIS_USERNAME", "_SYSTEM")
        password = os.environ.get("IRIS_PASSWORD", "SYS")
        if _iris_wrapper and hasattr(_iris_wrapper, "dbapi"):
            conn = _iris_wrapper.dbapi.connect(
                hostname=host, port=port, namespace=namespace,
                username=username, password=password,
            )
        else:
            conn = iris.connect(hostname=host, port=port, namespace=namespace,
                                username=username, password=password)
        return IRISGraphEngine(conn)

    if _state.startswith("embedded") and _iris_wrapper and hasattr(_iris_wrapper, "dbapi"):
        conn = _iris_wrapper.dbapi.connect(
            mode="embedded", namespace=os.environ.get("IRIS_NAMESPACE", "USER")
        )
        return IRISGraphEngine(conn)

    if _EMBEDDED:
        from iris_vector_graph.embedded import EmbeddedConnection
        return IRISGraphEngine(EmbeddedConnection())

    raise RuntimeError(
        "No IRIS connection available. Set IRIS_HOST env var or run inside IRIS "
        "(embedded mode via iris-embedded-python-wrapper or EmbeddedConnection)."
    )


def _get_engine() -> IRISGraphEngine:
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = _make_engine()
    return _engine_cache


def _reset_engine():
    global _engine_cache
    try:
        if _engine_cache is not None:
            _engine_cache.conn.close()
    except Exception:
        pass
    _engine_cache = None


def _run_cypher(query: str, parameters: dict | None = None, limit: int = 1000) -> dict:
    with _engine_lock:
        for attempt in range(2):
            try:
                engine = _get_engine()
                result = engine.execute_cypher(query, parameters=parameters or {})
                break
            except Exception as e:
                if attempt == 0:
                    _reset_engine()
                    continue
                raise
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
        result = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
        node_count = result["rows"][0][0] if result.get("rows") else 0
        return {"status": "ok", "engine": True, "nodes": node_count}
    except Exception as e:
        return {"status": "ok", "engine": False, "error": str(e)}


def _resolve_patient_anchors(req: CypherRequest) -> list[str]:
    from iris_vector_graph.fhir_bridge import fhir_search_conditions, get_kg_anchors

    fhir_url = req.fhir_base_url or os.environ.get("FHIR_BASE_URL", "")
    if not fhir_url:
        return []
    auth = tuple(req.fhir_auth) if req.fhir_auth else None
    fhir_result = fhir_search_conditions(
        fhir_base_url=fhir_url,
        patient_id=req.fhir_patient_id,
        auth=auth,
    )
    if fhir_result["error"] or not fhir_result["conditions"]:
        return []
    icd_codes = [c["code"] for c in fhir_result["conditions"] if c.get("code")]
    if not icd_codes:
        return []
    engine = _get_engine()
    return get_kg_anchors(engine, icd_codes)


@app.post("/api/cypher")
def cypher_query(req: CypherRequest):
    trace_id = str(uuid.uuid4())[:8]
    t0 = time.time()
    try:
        params = dict(req.parameters)
        if req.fhir_patient_id:
            anchors = _resolve_patient_anchors(req)
            params["patient_anchors"] = anchors
        result = _run_cypher(req.query, params, req.limitRows)
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


class QueryV2Request(BaseModel):
    statement: str
    parameters: dict[str, Any] = Field(default_factory=dict)


@app.get("/db/neo4j")
@app.get("/")
def neo4j_discovery(request: Request):
    host = request.headers.get("host", "localhost:8000")
    bolt_url = f"bolt://{host}"
    return {
        "bolt_routing": bolt_url,
        "bolt_direct": bolt_url,
        "neo4j_version": "5.0.0-compat",
        "neo4j_edition": "community",
        "query": "/db/neo4j/query/v2",
        "transaction": "/db/neo4j/tx",
        "db/cluster": None,
        "db/data": "/db/neo4j/tx/commit",
        "db/management": None,
    }


@app.get("/db/neo4j/tx")
def neo4j_tx_endpoint():
    return {"commit": "/db/neo4j/tx/commit"}


@app.post("/db/{db_name}/query/v2")
def neo4j_query_v2(db_name: str, req: QueryV2Request):
    trace_id = str(uuid.uuid4())[:8]
    t0 = time.time()
    try:
        r = _run_cypher(req.statement, req.parameters)
        duration = int((time.time() - t0) * 1000)
        _log("POST", f"/db/{db_name}/query/v2", 200, duration, trace_id)
        return {
            "data": {
                "fields": r["columns"],
                "values": r["rows"],
            },
            "bookmarks": [],
        }
    except Exception as e:
        duration = int((time.time() - t0) * 1000)
        _log("POST", f"/db/{db_name}/query/v2", 400, duration, trace_id)
        raise HTTPException(status_code=400, detail={
            "status": "error",
            "error": str(e),
            "trace_id": trace_id,
        })


def _neo4j_meta(value: Any) -> dict | None:
    if isinstance(value, dict) and "id" in value:
        return {"id": value.get("id"), "type": "node"}
    return None


@app.get("/schema")
def get_schema():
    try:
        eng = _get_engine()
        return {
            "labels": eng.get_labels(),
            "relationshipTypes": eng.get_relationship_types(),
            "propertyKeys": eng.get_property_keys(),
            "nodeCount": eng.get_node_count(),
            "edgeCount": eng.get_edge_count(),
            "labelDistribution": eng.get_label_distribution(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/indexes")
def get_indexes():
    try:
        eng = _get_engine()
        result = eng.execute_cypher("SHOW INDEXES")
        return {
            "columns": result.columns,
            "indexes": result.rows,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/server")
def get_server_info():
    try:
        eng = _get_engine()
        st = eng.status()
        return {
            "ivg_version": _ivg_version(),
            "iris_version": _iris_version(eng),
            "namespace": _IRIS_NAMESPACE,
            "schema": {
                "nodes": st.tables.nodes,
                "edges": st.tables.edges,
                "labels": st.tables.labels,
                "embeddings": st.tables.node_embeddings,
            },
            "adjacency": {
                "kg_populated": st.adjacency.kg_populated,
                "nkg_populated": st.adjacency.nkg_populated,
                "bfs_path": st.adjacency.bfs_path,
            },
            "objectscript_deployed": st.objectscript.deployed,
            "arno_loaded": st.arno.loaded,
            "probe_ms": st.probe_ms,
            "errors": st.errors,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
def get_metrics():
    try:
        eng = _get_engine()
        st = eng.status()
        lines = [
            "# HELP ivg_nodes_total Total nodes in the graph",
            "# TYPE ivg_nodes_total gauge",
            f"ivg_nodes_total {st.tables.nodes}",
            "# HELP ivg_edges_total Total edges in the graph",
            "# TYPE ivg_edges_total gauge",
            f"ivg_edges_total {st.tables.edges}",
            "# HELP ivg_embeddings_total Total node embeddings",
            "# TYPE ivg_embeddings_total gauge",
            f"ivg_embeddings_total {st.tables.node_embeddings}",
            "# HELP ivg_kg_populated Whether ^KG adjacency index is built (0/1)",
            "# TYPE ivg_kg_populated gauge",
            f"ivg_kg_populated {1 if st.adjacency.kg_populated else 0}",
            "# HELP ivg_nkg_populated Whether ^NKG adjacency index is built (0/1)",
            "# TYPE ivg_nkg_populated gauge",
            f"ivg_nkg_populated {1 if st.adjacency.nkg_populated else 0}",
            "# HELP ivg_status_probe_ms Time to collect status in milliseconds",
            "# TYPE ivg_status_probe_ms gauge",
            f"ivg_status_probe_ms {st.probe_ms:.2f}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
def get_stats():
    try:
        eng = _get_engine()
        return {
            "labelDistribution": eng.get_label_distribution(),
            "nodeCount": eng.get_node_count(),
            "edgeCount": eng.get_edge_count(),
            "embeddingCount": eng.embedding_count(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AdminSchemaRequest(BaseModel):
    embedding_dimension: int = 768
    auto_deploy_objectscript: bool = False


@app.post("/admin/schema/init")
def admin_schema_init(req: AdminSchemaRequest):
    try:
        eng = _get_engine()
        result = eng.initialize_schema(
            auto_deploy_objectscript=req.auto_deploy_objectscript
        )
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/indexes/rebuild")
def admin_indexes_rebuild():
    try:
        eng = _get_engine()
        kg_ok = eng.rebuild_kg()
        nkg_ok = eng.rebuild_nkg()
        return {"status": "ok", "kg": kg_ok, "nkg": nkg_ok}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AdminEmbedRequest(BaseModel):
    label: str = None
    force: bool = False


@app.post("/admin/embed")
def admin_embed(req: AdminEmbedRequest):
    try:
        eng = _get_engine()
        result = eng.embed_nodes(label=req.label, force=req.force)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/load")
async def admin_load(request: Request):
    try:
        import tempfile, os as _os
        body = await request.body()
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".ndjson", delete=False) as f:
            f.write(body)
            path = f.name
        try:
            eng = _get_engine()
            result = eng.import_graph_ndjson(path)
        finally:
            _os.unlink(path)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/export")
def admin_export():
    try:
        import tempfile, os as _os
        eng = _get_engine()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
            path = f.name
        try:
            result = eng.export_graph_ndjson(path)
            with open(path, "rb") as f:
                data = f.read()
        finally:
            try:
                _os.unlink(path)
            except Exception:
                pass
        return Response(content=data, media_type="application/x-ndjson",
                        headers={"Content-Disposition": "attachment; filename=graph.ndjson"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/snapshot")
def admin_snapshot_save():
    try:
        import tempfile, os as _os
        eng = _get_engine()
        snap_dir = _os.environ.get("IVG_SNAPSHOT_DIR", tempfile.gettempdir())
        path = _os.path.join(snap_dir, f"ivg_snapshot_{int(time.time())}.snapshot")
        result = eng.save_snapshot(path)
        return {"status": "ok", "path": path, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/queries")
def admin_list_queries():
    try:
        eng = _get_engine()
        cursor = eng.conn.cursor()
        try:
            cursor.execute(
                "SELECT ID, State, ClientName, Command FROM %SYS.ProcessQuery "
                "WHERE Command IS NOT NULL FETCH FIRST 50 ROWS ONLY"
            )
            rows = cursor.fetchall()
            queries = [
                {"id": str(r[0]), "state": r[1], "client": r[2], "command": str(r[3])[:200]}
                for r in rows
            ]
        except Exception:
            queries = []
        return {"queries": queries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/admin/queries/{query_id}")
def admin_kill_query(query_id: str):
    try:
        eng = _get_engine()
        cursor = eng.conn.cursor()
        try:
            cursor.execute("SELECT %SYSTEM.SYS.KillProcess(?)", [int(query_id)])
            return {"status": "ok", "killed": query_id}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not kill query {query_id}: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExplainRequest(BaseModel):
    query: str
    parameters: dict = {}


@app.post("/admin/explain")
def admin_explain(req: ExplainRequest):
    try:
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql
        parsed = parse_query(req.query)
        eng = _get_engine()
        sql_result = translate_to_sql(parsed, req.parameters, engine=eng)
        return {
            "cypher": req.query,
            "sql": sql_result.sql,
            "parameters": sql_result.parameters,
            "var_length_paths": sql_result.var_length_paths,
            "is_transactional": sql_result.is_transactional,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _ivg_version() -> str:
    try:
        from importlib.metadata import version
        return version("iris-vector-graph")
    except Exception:
        return "unknown"


def _iris_version(eng) -> str:
    try:
        cursor = eng.conn.cursor()
        cursor.execute("SELECT %Version.GetVersion()")
        row = cursor.fetchone()
        return str(row[0]) if row else "unknown"
    except Exception:
        return "unknown"


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
