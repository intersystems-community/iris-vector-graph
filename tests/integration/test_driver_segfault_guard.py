"""Subprocess-isolated guard against the intersystems-iris driver SIGSEGV
(DP-451209 family) on the multi-JOIN parameterized SELECT shape that ivg's Cypher
translator generates.

WHY A SUBPROCESS: the failure mode is a NATIVE SIGSEGV in the driver's
cursor.execute — it kills the Python process with NO exception and NO pytest FAIL
(it reports as "0% coverage / process died"). A normal test cannot assert against a
crash that erases the test process. So we run the suspect query in a CHILD process and
assert the child did not die with signal 11 (exit code -11 / 139). If the driver
regresses to crashing, THIS test fails visibly instead of silently killing the suite.

Reference: productivity-framework/specs/058-memory-recall-bridge/IRISPYTHON_SEGFAULT_REPORT.md
(report filed against intersystems-iris 5.3.3; ivg generates valid SQL — the driver
crashes executing it). ivg owns the SQL generator, so it owns this regression guard.

The crashing shape (translator.py): 2 LEFT JOINs to rdf_props on different "key" params
(L3167/3180/3221) + an EXISTS structural guard (L294) + a param-concat LIKE (L2758) +
FETCH FIRST, with multiple positional params.

SCOPE / KNOWN LIMITATION: the report's crash is SCALE-DEPENDENT — it reproduced against
~1.1M nodes / 3.1M edges, where the driver picks a particular plan. On the small fixture
data here the same shape does NOT crash (different plan), so these tests do NOT reproduce
the original bug. What they DO guard: that this exact query shape, through both the raw
driver and ivg's execute_cypher, never SIGSEGVs the host on representative data — i.e.
they catch a regression to "crashes even on small data," and they make the shape an
explicit, runnable artifact. A faithful repro needs a large dataset; gate that behind
IVG_SEGFAULT_SCALE_TEST=1 with a pre-loaded large graph (not run by default — too heavy).
"""
import os
import sys
import subprocess
import textwrap
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

# signal 11 (SIGSEGV) shows up as returncode -11 (POSIX) or 139 (shell convention).
SEGV_RETURNCODES = {-11, 139}


def _conn_params(conn):
    """Pull host/port/namespace/creds off the live test connection for the subprocess."""
    return {
        "hostname": getattr(conn, "hostname", "localhost"),
        "port": int(getattr(conn, "port", os.environ.get("IVG_PORT", "21972"))),
        "namespace": getattr(conn, "namespace", "USER"),
        "username": getattr(conn, "username", "_SYSTEM"),
        "password": getattr(conn, "password", "SYS"),
    }


def _run_in_subprocess(body: str, params: dict) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        import faulthandler, sys
        faulthandler.enable()
        import iris  # the wrapper's unified iris (base dep)
        conn = iris.dbapi.connect(
            hostname={params['hostname']!r}, port={params['port']},
            namespace={params['namespace']!r},
            username={params['username']!r}, password={params['password']!r},
        )
        {body}
        print("SUBPROCESS_OK")
    """)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=120,
    )


class TestDriverSegfaultGuard:
    def test_raw_crash_shape_does_not_segfault(self, iris_connection):
        """The report's minimal raw-driver repro, run in a child process. We assert the
        child does not die with SIGSEGV. (It may raise a clean SQL error or return rows —
        both are acceptable; a SIGSEGV is not.)"""
        params = _conn_params(iris_connection)
        body = textwrap.dedent('''
            cur = conn.cursor()
            sql = (
                "SELECT n0.node_id AS f_id, p4.val AS f_posted_at "
                "FROM Graph_KG.nodes n0 "
                "JOIN Graph_KG.rdf_edges e2 ON e2.s = n0.node_id AND e2.p = ? "
                "JOIN Graph_KG.nodes n1 ON n1.node_id = e2.o_id "
                "LEFT JOIN Graph_KG.rdf_props p3 ON p3.s = n0.node_id AND p3.\\"key\\" = ? "
                "LEFT JOIN Graph_KG.rdf_props p4 ON p4.s = n0.node_id AND p4.\\"key\\" = ? "
                "WHERE EXISTS (SELECT 1 FROM Graph_KG.rdf_props _sgn0 "
                "WHERE _sgn0.s = n0.node_id AND _sgn0.\\"key\\" = 'title') "
                "AND ((n0.node_id LIKE (? || '%') AND p3.val = ?) AND n1.node_id = ?) "
                "FETCH FIRST 1 ROWS ONLY"
            )
            params = ['IN_PROJECT', 'title', 'posted_at', 'seg:', 'x', 'seg:target']
            try:
                cur.execute(sql, params)
                cur.fetchall()
            except Exception as e:
                # A clean exception is fine — the driver bug is the CRASH, not an error.
                print("SUBPROCESS_SQL_EXCEPTION:", type(e).__name__)
        ''')
        result = _run_in_subprocess(body, params)
        assert result.returncode not in SEGV_RETURNCODES, (
            "intersystems-iris driver SIGSEGV on the multi-JOIN parameterized SELECT "
            f"shape (DP-451209 family). returncode={result.returncode}\n"
            f"stderr tail:\n{result.stderr[-800:]}"
        )

    def test_cypher_path_crash_shape_does_not_segfault(self, engine, iris_connection):
        """The CONSUMER path: a Cypher query through execute_cypher that ivg translates
        to the crashing shape (LIKE-prefix on id + two property projections + a related
        node). Built on real (small) fixture data. Asserts no host SIGSEGV."""
        # Seed a tiny graph with the property/edge shape the query touches.
        eng = engine
        try:
            eng.create_node("seg:a")
            eng.create_node("seg:target")
            eng.set_node_property("seg:a", "title", "hello")
            eng.set_node_property("seg:a", "posted_at", "2026-06-27")
            eng.create_edge("seg:a", "IN_PROJECT", "seg:target")
        except Exception:
            pass  # best-effort seed; the point is the query shape, not the data

        params = _conn_params(iris_connection)
        body = textwrap.dedent('''
            from iris_vector_graph.engine import IRISGraphEngine, set_schema_prefix
            set_schema_prefix("Graph_KG")
            eng = IRISGraphEngine(conn, embedding_dimension=384)
            cypher = (
                "MATCH (n)-[:IN_PROJECT]->(m) "
                "WHERE n.node_id STARTS WITH 'seg:' AND n.title = 'hello' "
                "AND m.node_id = 'seg:target' "
                "RETURN n.node_id AS id, n.posted_at AS posted_at "
                "LIMIT 1"
            )
            try:
                res = eng.execute_cypher(cypher, read_only=True)
                print("SUBPROCESS_CYPHER_ROWS:", len(getattr(res, 'rows', []) or []))
            except Exception as e:
                print("SUBPROCESS_CYPHER_EXCEPTION:", type(e).__name__)
        ''')
        result = _run_in_subprocess(body, params)
        assert result.returncode not in SEGV_RETURNCODES, (
            "ivg execute_cypher -> generated SQL SIGSEGV'd the host process "
            f"(driver DP-451209 family). returncode={result.returncode}\n"
            f"stderr tail:\n{result.stderr[-800:]}"
        )
