"""4.6.0 (NR2): `Graph.KG.NKGAccelLoader.Load` on a library that is not there returns 0.

It is declared `As %Boolean`, and the Arno library is optional and not distributed with IVG. Before 4.6.0 a missing
file threw an uncaught `<DYNAMIC LIBRARY LOAD>` out of `$ZF(-4, 1, path)`: a terminal dropped to `USER 3e1>`, and a
Native API call raised. The reason is kept for the process (`LoadError()`).

4.6.0 R207 (walkthrough 1, P2): every `$ZF(-4, 1, path)` of a file that is not there writes two severity-1 lines to
messages.log ("Failed to load dynamic library", "Error loading dll"), in every process that tries, and an engine's
first call tried three times. A site that alerts on severity 1 was paged on every IVG call (138 lines in the
walkthrough). The loaders, and the SQL probe the algorithm wrappers use, now check that the file is there first, and
ask IRIS to load only a file that is. Each test names a path no other test uses, so the log lines it looks for are
its own.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.integration, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

MISSING = "/usr/irissys/mgr/no-such.so"
LOADER = "Graph.KG.NKGAccelLoader"
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")


@pytest.fixture
def native(iris_connection):
    import iris

    o = iris.createIRIS(iris_connection)
    o.classMethodVoid(LOADER, "Unload")  # this process's handle, if an earlier test loaded one
    yield o
    o.classMethodVoid(LOADER, "Unload")


def _missing_path() -> str:
    return f"/usr/irissys/mgr/no-such-{uuid.uuid4().hex[:12]}.so"


def _log_lines_naming(path: str) -> list:
    log = subprocess.run(["docker", "exec", CONTAINER, "cat", "/usr/irissys/mgr/messages.log"],
                         capture_output=True, text=True, timeout=60, errors="replace")
    assert log.returncode == 0, log.stderr
    return [line for line in log.stdout.splitlines() if path in line]


def test_a_missing_library_returns_0(native):
    assert native.classMethodValue(LOADER, "Load", MISSING) == 0
    assert native.classMethodValue(LOADER, "IsLoaded") == 0


def test_the_reason_is_kept_for_the_process(native):
    native.classMethodValue(LOADER, "Load", MISSING)
    error = str(native.classMethodValue(LOADER, "LoadError"))
    assert "not found" in error and "no-such.so" in error


def test_the_process_runs_the_next_call(native, iris_connection):
    native.classMethodValue(LOADER, "Load", MISSING)
    assert native.classMethodValue("%SYSTEM.SYS", "NameSpace") == iris_connection.namespace
    assert native.classMethodValue(LOADER, "IsLoaded") == 0


@pytest.mark.parametrize("loader", ["Graph.KG.NKGAccelLoader", "Graph.KG.NKGAccel", "Graph.KG.ArnoAccel"])
def test_a_missing_library_writes_nothing_to_messages_log(native, loader):
    path = _missing_path()
    assert native.classMethodValue(loader, "Load", path) == 0
    assert _log_lines_naming(path) == []


def test_the_sql_probe_of_a_missing_library_writes_nothing_to_messages_log(iris_connection):
    from iris_vector_graph.stores import arno_bridge

    arno_bridge._ensure_zf_call_function(iris_connection)
    path = _missing_path()
    cur = iris_connection.cursor()
    cur.execute("SELECT Graph_KG.ivg_arno_zf_probe(?, ?)", [path, "version"])
    assert int(cur.fetchone()[0]) == 0
    assert _log_lines_naming(path) == []


def test_the_server_says_whether_the_library_file_is_there(native):
    assert native.classMethodValue("Graph.KG.ArnoAccel", "LibExists", _missing_path()) == 0
    # a file every IRIS install has, standing in for a library that is there
    assert native.classMethodValue("Graph.KG.ArnoAccel", "LibExists", "/usr/irissys/bin/irisdb") == 1
