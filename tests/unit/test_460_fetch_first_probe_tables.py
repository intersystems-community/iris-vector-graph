"""4.6.0 R130: the FETCH FIRST probe of a `from_connect` engine leaves no table behind, and makes none outside the
IVG Schema.

The probe (engine._detect_fetch_first_unsafe) runs the crashing shape, a JOIN of two VARCHAR-keyed tables with
FETCH FIRST, in a child process, since the crash is a SIGSEGV of the client. It created `ivg_ff_probe_a` and
`ivg_ff_probe_b` unqualified, so in SQLUser, and dropped neither. The tables are now in the IVG Schema, named for
this probe, and dropped: by the child when it lives, by this process when the child crashed or hung (a crash is the
answer the probe is for, and leaves the child no chance to clean up).

The child is a real subprocess only in the live test (tests/e2e/test_460_fetch_first_probe_e2e.py); here
`subprocess.run` is patched, and the cleanup connection is `iris.dbapi.connect`, patched as the attribute the
engine calls.
"""

from __future__ import annotations

import re
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

PARAMS = {"hostname": "h", "port": 1, "namespace": "NS", "username": "u", "password": "p"}


def _engine(schema="RAG"):
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = (0,)
    eng = IRISGraphEngine(conn, embedding_dimension=4, schema_prefix=schema)
    eng._connection_params = dict(PARAMS)
    return eng


def _run(eng, *, returncode=0, stdout="", raises=None):
    seen = {}

    def fake_run(args, **kwargs):
        seen["script"] = args[-1]
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    cleanup = MagicMock()
    with patch("subprocess.run", side_effect=fake_run), patch("iris.dbapi.connect", return_value=cleanup) as connect:
        answer = eng._detect_fetch_first_unsafe()
    return answer, seen["script"], connect, cleanup


def _tables(script):
    return sorted(set(re.findall(r"\b[A-Za-z_]\w*\.ivg_ff_probe_\w+", script)))


def test_the_probe_tables_are_qualified_with_the_ivg_schema():
    _, script, _, _ = _run(_engine("RAG"), stdout="OK\nDROPPED\n")
    tables = _tables(script)
    assert len(tables) == 2 and all(t.startswith("RAG.ivg_ff_probe_") for t in tables), script
    # no unqualified reference to a probe table anywhere in the script
    assert not re.findall(r"(?<![\w.])ivg_ff_probe_\w+", script), script


def test_the_probe_still_runs_the_crash_shape_on_varchar_keys():
    _, script, _, _ = _run(_engine(), stdout="OK\nDROPPED\n")
    a, b = _tables(script)
    assert re.search(rf"CREATE TABLE {re.escape(a)} \(id VARCHAR\(64\)\)", script), script
    assert re.search(rf"CREATE TABLE {re.escape(b)} \(sid VARCHAR\(64\)\)", script), script
    assert re.search(rf"FROM {re.escape(a)} a JOIN {re.escape(b)} b ON b\.sid=a\.id FETCH FIRST 1 ROWS ONLY", script)


def test_the_child_drops_both_tables_and_the_parent_opens_no_connection():
    answer, script, connect, _ = _run(_engine(), stdout="OK\nDROPPED\n")
    assert answer is False
    tables = _tables(script)
    assert len(tables) == 2, script
    for table in tables:
        assert f"DROP TABLE {table}" in script, script
    connect.assert_not_called()


def test_two_probes_use_different_tables():
    _, first, _, _ = _run(_engine(), stdout="OK\nDROPPED\n")
    _, second, _, _ = _run(_engine(), stdout="OK\nDROPPED\n")
    assert set(_tables(first)).isdisjoint(_tables(second))


@pytest.mark.parametrize("outcome", [
    {"returncode": -11},
    {"returncode": 139},
    {"raises": subprocess.TimeoutExpired(cmd="python", timeout=5)},
], ids=["sigsegv", "sigsegv-shell", "hang"])
def test_a_crashed_or_hung_probe_is_unsafe_and_this_process_drops_its_tables(outcome):
    answer, script, connect, cleanup = _run(_engine(), **outcome)
    assert answer is True
    connect.assert_called_once_with(**PARAMS)
    dropped = [c.args[0] for c in cleanup.cursor.return_value.execute.call_args_list]
    assert dropped == [f"DROP TABLE {t}" for t in _tables(script)], dropped
    cleanup.close.assert_called_once()


def test_a_probe_that_failed_without_crashing_is_safe_and_is_cleaned_up_too():
    answer, script, connect, cleanup = _run(_engine(), returncode=1, stdout="")
    assert answer is False
    dropped = [c.args[0] for c in cleanup.cursor.return_value.execute.call_args_list]
    assert dropped == [f"DROP TABLE {t}" for t in _tables(script)]


# 4.7.0 row 1 (GitHub #13): on IRIS Community 2026.1 (Build 234U) the JOIN shape above runs, but any ORDER BY of an
# aggregate with FETCH FIRST (or LIMIT / OFFSET ... FETCH) fails at Prepare, SQLCODE -400 `<UNDEFINED>main+129^%qaqcasl
# *mt("top")`, measured on a single table too; TOP does not. The probe runs that shape as well, and a refusal of it
# marks the build as one to answer with TOP.


def test_the_probe_runs_the_aggregate_order_by_shape():
    _, script, _, _ = _run(_engine(), stdout="OK\nDROPPED\n")
    a, _b = _tables(script)
    assert re.search(rf"FROM {re.escape(a)} GROUP BY id ORDER BY COUNT\(\*\) DESC FETCH FIRST 1 ROWS ONLY", script), script


def test_a_refused_aggregate_order_by_marks_the_build_unsafe():
    answer, _, _, _ = _run(_engine(), stdout="AGG_FF_REFUSED: SQLCODE -400\nOK\nDROPPED\n")
    assert answer is True


def test_an_accepted_aggregate_order_by_leaves_the_build_safe():
    answer, _, _, _ = _run(_engine(), stdout="OK\nDROPPED\n")
    assert answer is False
