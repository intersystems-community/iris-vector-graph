"""4.7.0 row 5 (python-devx), live: initialize_schema() on a fresh namespace compiles every class the package ships but
the %AI MCP classes, and its summary says so.

The report: on IRIS for Health 2026.1 with 4.6.2, IVG.CypherEngine and IVG.Percentile were "not compiled by setup".
Measured on IRIS Community 2026.1 (Build 234U) in a new namespace, with 4.6.2 from PyPI and with release-4.7.0: both
compile; setup's `objectscript_failed` is {} and `objectscript_skipped` names Graph.KG.MCPService, MCPToolSet and
MCPTools, which need %AI. The report's own check listed compiled classes with `Name %STARTSWITH 'Graph.KG.'`, so
any class outside that package (the two IVG.* classes) always read as missing. Not an IVG defect; the known answer
is pinned here, on whatever IRIS the namespace is.

Where: `IVG_SETUP_NS`, a namespace with no IVG classes yet (USER2-style on a throwaway container, or an IVG470*
scratch namespace made empty), on `IVG_PORT` (default 31972) as `IVG_SETUP_USER` / `IVG_SETUP_PASSWORD`.
"""

from __future__ import annotations

import os
import pathlib

import pytest

PORT = int(os.environ.get("IVG_PORT", "31972"))
NS = os.environ.get("IVG_SETUP_NS", "")
USER = os.environ.get("IVG_SETUP_USER", "_SYSTEM")
PASSWORD = os.environ.get("IVG_SETUP_PASSWORD", "SYS")
MCP = {"Graph.KG.MCPService", "Graph.KG.MCPToolSet", "Graph.KG.MCPTools"}

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(not NS, reason="IVG_SETUP_NS must name a namespace with no IVG classes yet")]


def test_setup_compiles_every_shipped_class_but_the_ai_ones():
    import iris

    import iris_vector_graph
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect("127.0.0.1", PORT, NS, USER, PASSWORD)
    try:
        cur = conn.cursor()

        def compiled():
            cur.execute("SELECT Name FROM %Dictionary.CompiledClass WHERE Name %STARTSWITH 'Graph.KG.' "
                        "OR Name %STARTSWITH 'IVG.'")
            return {r[0] for r in cur.fetchall()}

        assert compiled() == set(), "the namespace already holds IVG classes: this case proves nothing"
        src = pathlib.Path(iris_vector_graph.__file__).parent / "iris_src" / "src"
        if not src.is_dir():
            src = pathlib.Path(iris_vector_graph.__file__).parents[1] / "iris_src" / "src"
        shipped = {".".join(p.relative_to(src).with_suffix("").parts) for p in src.rglob("*.cls")}
        status = IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()
        assert status["objectscript_failed"] == {}
        skipped = set(status["objectscript_skipped"])
        assert skipped <= MCP, skipped
        assert {"IVG.CypherEngine", "IVG.Percentile"} <= compiled()
        assert shipped - compiled() == skipped
    finally:
        conn.close()
