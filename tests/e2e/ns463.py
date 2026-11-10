"""Shared by the 4.6.3 and 4.7.0 E2E files: the scratch namespace `IVG_NAMESPACE` (an IVG470*, IVG463*, IVG460* or
IVG262* namespace with a database of its own and IVG set up for `Graph_KG`, made by
`scripts/dev/make_scratch_namespace.py IVG463A`, or the gate's "primary" namespace: tests/e2e/gate.py), and an engine
on each Cypher route.

`engines()` answers {"default": the engine as a caller builds it (the native executor first, SQL when it declines),
"sql": native_cypher=False}. A case that runs on both reads each route's answer.
"""

from __future__ import annotations

import os
import subprocess

import pytest

PORT = int(os.environ.get("IVG_PORT", "31972"))
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
NS = os.environ.get("IVG_NAMESPACE", "")
ROUTES = ("default", "sql")


def _container_running() -> bool:
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "true"


requires_namespace = [
    pytest.mark.skipif(not (NS.upper() == NS and NS.startswith(("IVG470", "IVG463", "IVG460", "IVG262"))),
                       reason="IVG_NAMESPACE must name an IVG470*, IVG463*, IVG460* or IVG262* scratch namespace with IVG set up"),
    pytest.mark.skipif(not _container_running(), reason=f"{CONTAINER} is not running"),
]


def connect():
    import iris

    return iris.connect("localhost", PORT, NS, "_SYSTEM", "SYS")


def engines(conn) -> dict:
    import iris

    from iris_vector_graph import IRISGraphEngine

    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    return {
        "default": IRISGraphEngine(conn, namespace=NS, schema_prefix=schema),
        "sql": IRISGraphEngine(conn, namespace=NS, schema_prefix=schema, native_cypher=False),
    }


def rows(engine, query, params=None):
    """The rows, the error raised if the query fails."""
    return engine.execute_cypher(query, dict(params or {}), raise_on_error=True).rows


def _typed(v):
    if isinstance(v, list):
        return [_typed(e) for e in v]
    if isinstance(v, dict):
        return {k: _typed(e) for k, e in v.items()}
    return (v, type(v).__name__)


def typed(rows_):
    """Each value beside its Python type, inside lists and maps too: 1 and '1', or True and 1, are told apart."""
    return [[_typed(v) for v in row] for row in rows_]
