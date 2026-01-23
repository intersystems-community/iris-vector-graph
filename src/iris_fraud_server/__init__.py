"""
IRIS Fraud Scoring - Embedded Python Server

FastAPI/ASGI application running in IRIS embedded Python (irispython).
Follows iris-pgwire pattern for embedded Python servers.

Key differences from external FastAPI:
- Runs via /usr/irissys/bin/irispython (not system python)
- All database operations use iris.sql.exec() (no external connections)
- TorchScript model loaded in embedded Python context
- Single process with IRIS (zero IPC overhead)

Performance Target: <20ms p95 @ 200 QPS (MLP mode)

Constitutional Compliance:
- I. IRIS-Native Development (embedded Python)
- II. Test-First with Live Database (iris.sql.exec)
- III. Performance as a Feature (<20ms SLO tracked)
- VII. Explicit Error Handling (zero-vector fallback, graceful degradation)
- V. Observability (trace_id, reason codes, structured logging)
"""

__version__ = "0.1.0"
__all__ = ["create_app", "run_server"]

from .app import create_app, run_server
