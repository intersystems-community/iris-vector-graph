"""4.6.0 (NR2): `Graph.KG.NKGAccelLoader.Load` on a library that is not there returns 0.

It is declared `As %Boolean`, and the Arno library is optional and not distributed with IVG. Before 4.6.0 a missing
file threw an uncaught `<DYNAMIC LIBRARY LOAD>` out of `$ZF(-4, 1, path)`: a terminal dropped to `USER 3e1>`, and a
Native API call raised. The error text is kept for the process (`LoadError()`).
"""

from __future__ import annotations

import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.integration, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

MISSING = "/usr/irissys/mgr/no-such.so"
LOADER = "Graph.KG.NKGAccelLoader"


@pytest.fixture
def native(iris_connection):
    import iris

    o = iris.createIRIS(iris_connection)
    o.classMethodVoid(LOADER, "Unload")  # this process's handle, if an earlier test loaded one
    yield o
    o.classMethodVoid(LOADER, "Unload")


def test_a_missing_library_returns_0(native):
    assert native.classMethodValue(LOADER, "Load", MISSING) == 0
    assert native.classMethodValue(LOADER, "IsLoaded") == 0


def test_the_error_is_kept_for_the_process(native):
    native.classMethodValue(LOADER, "Load", MISSING)
    error = str(native.classMethodValue(LOADER, "LoadError"))
    assert "DYNAMIC LIBRARY LOAD" in error and "no-such.so" in error


def test_the_process_runs_the_next_call(native, iris_connection):
    native.classMethodValue(LOADER, "Load", MISSING)
    assert native.classMethodValue("%SYSTEM.SYS", "NameSpace") == iris_connection.namespace
    assert native.classMethodValue(LOADER, "IsLoaded") == 0
