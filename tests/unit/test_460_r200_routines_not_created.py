"""4.6.0 R200: on Community 2026.1 `kg_RRF_FUSE` was missing from `Graph_KG` after setup, and nothing said so.

Its CREATE failed at Prepare: `SQLCODE -1 ... SELECT expected, <end of statement> found ^ WITH V AS (`. 2026.1
rejects a `WITH` (common table expression) statement that spans lines inside a `LANGUAGE SQL` procedure body; the
same body on one line compiles, and 2026.3 takes both. The failure went to `_install_procedures`'s optional branch as
`logger.debug`, so setup ended with its INFO summary and no word of it, `status()` had no way to show it, and R131's
`/kg` role, which grants EXECUTE on it, could not be created there.

The fix is in the DDL: the three legs are derived tables, not CTEs, so 2026.1 builds the procedure
(`tests/integration/test_shipped_routines_exist.py` and `tests/e2e/test_460_r200_rrf_fuse_e2e.py` on the compose
image). And a routine setup could not create is no longer silent wherever the cause: setup logs a WARNING with the
error and names it in the summary, and `status().missing_routines` lists every declared routine the namespace lacks.
"""

from __future__ import annotations

import logging
import re

import pytest

from iris_vector_graph._engine.schema import _setup_summary
from iris_vector_graph.schema import GraphSchema
from tests.unit.test_460_r133_routine_fingerprints import _all_names, _Namespace, _run
from tests.unit.test_460_status_probes import _native, _Registry, _status

_CTE = re.compile(r"\bWITH\s+\w+\s+AS\s*\(", re.IGNORECASE)


def _sql_bodies():
    for stmt in GraphSchema.get_procedures_sql_list("Graph_KG"):
        if "LANGUAGE SQL" not in stmt.upper():
            continue
        name = re.search(r"CREATE OR REPLACE (?:FUNCTION|PROCEDURE)\s+([\w.]+)", stmt).group(1)
        body = stmt.split("BEGIN", 1)[1]
        yield name, re.sub(r"--[^\n]*", "", body)


def test_the_sql_bodies_are_checked():
    assert {"Graph_KG.kg_KNN_VEC", "Graph_KG.kg_TXT", "Graph_KG.kg_RRF_FUSE"} <= {n for n, _ in _sql_bodies()}


def test_no_language_sql_body_uses_a_common_table_expression():
    # 2026.1 rejects a multi-line WITH in a procedure body at Prepare (SQLCODE -1, "SELECT expected")
    assert [name for name, body in _sql_bodies() if _CTE.search(body)] == []


def test_a_routine_setup_could_not_create_is_a_warning_with_its_error(caplog):
    ns = _Namespace()
    ns.refuse.add("KG_RRF_FUSE")
    with caplog.at_level(logging.WARNING, logger="iris_vector_graph._engine.schema"):
        result = _run(ns)
    assert set(result["failed"]) == {"kg_RRF_FUSE"}
    assert "optional routine" in result["failed"]["kg_RRF_FUSE"]  # the server's message, kept
    assert sorted(n.upper() for n in result["created"]) == sorted(n for n in _all_names() if n != "KG_RRF_FUSE")
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("kg_RRF_FUSE" in m and "optional routine" in m for m in warned), warned


def test_nothing_failed_is_an_empty_map():
    assert _run(_Namespace())["failed"] == {}


def _summary(failed):
    status = {"objectscript_deployed": False, "objectscript_skipped": {}, "routines": {"failed": failed}}
    return _setup_summary("USER", "Graph_KG", None, status, 4)


def test_the_setup_summary_names_a_routine_not_created():
    line = _summary({"kg_RRF_FUSE": "[SQLCODE: <-1>:<Invalid SQL statement>] SELECT expected"})
    assert "1 routine not created: kg_RRF_FUSE ([SQLCODE: <-1>:<Invalid SQL statement>] SELECT expected)" in line


def test_the_setup_summary_says_nothing_of_routines_when_all_were_created():
    assert "not created" not in _summary({})


def _routines_present(names):
    return _Registry(answer={"INFORMATION_SCHEMA.ROUTINES": [(n,) for n in names]})


def test_status_lists_a_declared_routine_the_namespace_lacks():
    # the view does not list it, and the database answers -359 (no such routine) when asked (4.7.0 row 29)
    registry = _routines_present([n for n in _all_names() if n != "KG_RRF_FUSE"])
    registry.fail_on["SELECT TOP 0 Graph_KG.kg_RRF_FUSE("] = RuntimeError(
        "[SQLCODE: <-359>:<SQL Function (function stored procedure) not found>]")
    status, _ = _status(registry, native=_native())
    assert status.missing_routines == ["kg_RRF_FUSE"]
    assert "Routines missing: kg_RRF_FUSE" in status.report()


def test_status_lists_none_when_every_routine_is_there():
    status, _ = _status(_routines_present(_all_names()), native=_native())
    assert status.missing_routines == []
    assert "Routines missing" not in status.report()


def test_a_failed_routine_read_is_a_probe_error():
    registry = _Registry(fail_on={"INFORMATION_SCHEMA.ROUTINES": RuntimeError("[SQLCODE: <-99>]")})
    status, _ = _status(registry, native=_native())
    assert any("routine check failed" in e and "-99" in e for e in status.errors), status.errors


def test_a_routine_the_caller_may_not_run_is_unchecked_not_missing():
    """4.7.0 row 29: INFORMATION_SCHEMA.ROUTINES lists only routines the caller may run; a SELECT-only user saw none
    and status() reported every declared routine missing. Asked, the database answers -99 for those."""
    registry = _routines_present([n for n in _all_names() if n != "KG_RRF_FUSE"])
    registry.fail_on["SELECT TOP 0 Graph_KG.kg_RRF_FUSE("] = RuntimeError("[SQLCODE: <-99>:<Privilege violation>]")
    status, _ = _status(registry, native=_native())
    assert status.missing_routines == []
    assert "routines" in status.unknown
    assert any("could not check 1 routine(s)" in e and "kg_RRF_FUSE" in e for e in status.errors), status.errors


@pytest.mark.parametrize("error", ["[SQLCODE: <-400>:<Fatal error occurred>]", "connection reset"])
def test_a_routine_probe_failing_otherwise_is_an_error_not_a_crash(error):
    """Any answer but -359, -99 or -357 is neither missing nor present: named in `errors`, `routines` unknown. On
    33630819 this raised UnboundLocalError (the exception's name read after its `except` block)."""
    registry = _routines_present([n for n in _all_names() if n != "KG_RRF_FUSE"])
    registry.fail_on["SELECT TOP 0 Graph_KG.kg_RRF_FUSE("] = RuntimeError(error)
    status, _ = _status(registry, native=_native())
    assert status.missing_routines == []
    assert "routines" in status.unknown
    assert any("routine check of Graph_KG.kg_RRF_FUSE failed" in e and error in e for e in status.errors), status.errors
