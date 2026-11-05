"""The session's native process is checked and replaced at the end of every test module (4.6.0 full E2E, 2026-11-05).

`tests/conftest.py` sends every `iris.createIRIS(session_connection)` to one dedicated native connection, opened once
for the session. In the 1:55 h run on IVGFIX, its server process (pid 160821) stopped compiling cached queries:
`<UNDEFINED>lviter+16^%qaqpnl *mt(` and "failed to compile Cached Query Class" from 87% of the run on, and about 110
setup and teardown errors in six files that ran after it (EraseAll found no routed table, and failed with -124). A
fresh process compiled the same statements. Nothing in the files that failed caused it, and nothing named the file that
did.

So at the end of each module, before that process is retired, it compiles a statement it has never seen on
`rdf_edges`. A failure is a `NativeProcessDegraded` warning naming the module that just ran, plus a line in a log
file; then the connection is closed, and the next module opens a new one. The proxy every fixture holds follows it.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    NativeProcessDegraded,
    ReconnectingNative,
    probe_native_compiles,
    report_degraded_native,
)


class _Result:
    def __init__(self, code, message=None):
        self._v = {"%SQLCODE": code, "%Message": message}

    def get(self, name):
        return self._v[name]


class _ProbeNative:
    def __init__(self, code=0, message=None, raises=None):
        self.code, self.message, self.raises = code, message, raises
        self.sql = []

    def classMethodObject(self, cls, method, *args):
        assert (cls, method) == ("%SQL.Statement", "%ExecDirect")
        self.sql.append(args[1])
        if self.raises is not None:
            raise self.raises
        return _Result(self.code, self.message)


def test_a_process_that_compiles_passes():
    assert probe_native_compiles(_ProbeNative(0)) is None


def test_each_probe_is_a_statement_the_process_has_never_compiled_and_reads_the_edge_table():
    native = _ProbeNative(0)
    probe_native_compiles(native)
    probe_native_compiles(native)
    assert native.sql[0] != native.sql[1], "a cached statement would not be compiled again"
    assert all("Graph_KG.rdf_edges" in s for s in native.sql)


def test_a_failed_compile_is_reported_with_what_iris_said():
    said = "Process 160821 failed to compile Cached Query Class %sqlcq.IVGFIX.u1"
    assert probe_native_compiles(_ProbeNative(-400, said)) == f"SQLCODE -400: {said}"


def test_a_planner_crash_raised_through_the_native_api_is_reported():
    crash = RuntimeError("<UNDEFINED>lviter+16^%qaqpnl *mt(")
    assert "lviter" in probe_native_compiles(_ProbeNative(raises=crash))


def test_a_namespace_without_the_tables_is_not_a_degraded_process():
    assert probe_native_compiles(_ProbeNative(-30, "Table 'GRAPH_KG.RDF_EDGES' not found")) is None


def test_a_dead_connection_is_not_a_degraded_process():
    gone = RuntimeError("<COMMUNICATION LINK ERROR> Connection closed")
    assert probe_native_compiles(_ProbeNative(raises=gone)) is None


def test_the_report_names_the_module_warns_and_appends_to_the_log(tmp_path):
    log = tmp_path / "sub" / "native-probe.log"
    with pytest.warns(NativeProcessDegraded, match="tests.e2e.test_x_e2e"):
        report_degraded_native("tests.e2e.test_x_e2e", "IVGFIX", "SQLCODE -400: no", log)
    with pytest.warns(NativeProcessDegraded):
        report_degraded_native("tests.e2e.test_y_e2e", "IVGFIX", "SQLCODE -400: no", log)
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert "IVGFIX tests.e2e.test_x_e2e: SQLCODE -400: no" in lines[0]
    assert "tests.e2e.test_y_e2e" in lines[1]


class _FakeNative:
    def __init__(self, generation):
        self.generation = generation

    def classMethodValue(self, cls, method, *args):
        return f"gen{self.generation}"


def test_the_proxy_follows_a_retired_connection_without_calling_it_a_death():
    opened, retired = [], [0]

    def _open():
        opened.append(_FakeNative(len(opened) + 1))
        return opened[-1]

    deaths = []
    proxy = ReconnectingNative(_open, on_reconnect=deaths.append, generation=lambda: retired[0])
    assert proxy.classMethodValue("X", "Y") == "gen1"
    assert proxy.classMethodValue("X", "Y") == "gen1" and len(opened) == 1
    retired[0] += 1
    assert proxy.classMethodValue("X", "Y") == "gen2"
    assert len(opened) == 2 and deaths == []
