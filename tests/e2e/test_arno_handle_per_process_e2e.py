"""A $ZF(-4,1) library handle belongs to the process that loaded it; another process's handle must not break this one.

Spec 210 kept the handle and the function ids in ^ArnoAccel so that every process would "see" the library. They are
only valid in the process that called $ZF(-4,1), so a process that loads after another one wrote numbers the first
process then called through. Where the numbers differed, ReaderRevisions answered {} (no readers, so every algorithm
quietly went to its Owner) and the reader gate test saw "library reports no reader" (2026-11-01, after the pagerank
Owner fix gave the gate something to prove). The library's path is configuration and stays in ^ArnoAccel("lib_path");
each process loads it for itself.

A recorded path is taken only in the instance's manager or binary directory (4.6.0 R311, `ArnoAccel.TrustedPath`);
one anywhere else is ignored and the process loads the default build instead.
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = [pytest.mark.e2e]

AA = "Graph.KG.ArnoAccel"
LIB = "/tmp/libarno_callout.so"
DEFAULT = "/usr/irissys/mgr/libarno_callout.so"


def _native(conn):
    import iris

    return iris.createIRIS(conn)


@pytest.fixture
def two(iris_connection):
    """Two connections (two IRIS processes) in the test namespace, with the library configured; ^ArnoAccel restored."""
    import iris

    a = _native(iris_connection)
    if not int(a.classMethodValue("%File", "Exists", LIB)):
        if os.environ.get("IVG_REQUIRE_ARNO") == "1":
            pytest.fail(f"IVG_REQUIRE_ARNO=1 and the container has no {LIB}")
        pytest.skip(f"no Arno library at {LIB}")
    if not int(a.classMethodValue("%File", "Exists", DEFAULT)):
        if os.environ.get("IVG_REQUIRE_ARNO") == "1":
            pytest.fail(f"IVG_REQUIRE_ARNO=1 and the container has no {DEFAULT}")
        pytest.skip(f"no Arno library at {DEFAULT}")
    ns = str(a.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    host = os.environ.get("IVG_HOST", "localhost")
    port = int(os.environ.get("IVG_PORT", "31972"))
    conn_b = iris.connect(host, port, ns, "_SYSTEM", "SYS")
    b = _native(conn_b)
    saved = a.get("^ArnoAccel", "lib_path")
    try:
        yield a, b
    finally:
        a.kill("^ArnoAccel")
        if saved is not None:
            a.set(saved, "^ArnoAccel", "lib_path")
        conn_b.close()


def _readers(o) -> dict:
    return json.loads(str(o.classMethodValue(AA, "ReaderRevisions")))


def test_a_handle_another_process_wrote_does_not_break_this_ones_calls(two):
    a, _ = two
    assert a.classMethodValue(AA, "Load", LIB) == 1
    want = _readers(a)
    assert want.get("pagerank", 0) >= 1, want
    # what another process's Load used to leave in the shared global: numbers that mean nothing here
    a.set(97, "^ArnoAccel", "dllid")
    for fn in ("version", "init", "kg_pagerank_global"):
        a.set(97, "^ArnoAccel", "fid", fn)
    assert _readers(a) == want
    assert str(a.classMethodValue(AA, "LibraryVersion")).startswith("arno-callout ")


def test_both_processes_keep_working_whichever_loaded_last(two):
    a, b = two
    assert a.classMethodValue(AA, "Load", LIB) == 1
    assert b.classMethodValue(AA, "Load", LIB) == 1
    assert _readers(a) == _readers(b) and _readers(a)
    assert str(a.classMethodValue(AA, "LibraryVersion")) == str(b.classMethodValue(AA, "LibraryVersion"))


def test_a_handle_loaded_from_another_path_is_not_the_configured_library(two):
    """A reconfigured lib_path is loaded on next use: the handle this process holds is for the old path."""
    a, _ = two
    # a path TrustedPath takes (the manager directory) with no file there: the held /tmp handle must not answer for it
    missing = str(a.classMethodValue("%SYSTEM.Util", "ManagerDirectory")) + "ivg-no-such-libarno_callout.so"
    assert a.classMethodValue(AA, "Load", LIB) == 1
    a.kill("^ArnoAccel")
    a.set(missing, "^ArnoAccel", "lib_path")
    assert int(a.classMethodValue(AA, "IsAvailable")) == 0
    a.set(DEFAULT, "^ArnoAccel", "lib_path")
    assert int(a.classMethodValue(AA, "IsAvailable")) == 1
    assert a.classMethodValue(AA, "LoadedPath") == DEFAULT


def test_a_recorded_path_outside_the_instance_directories_is_not_loaded(two):
    """R311: a lib_path outside the manager and binary directories is ignored; the next use loads the default."""
    a, _ = two
    assert a.classMethodValue(AA, "Load", LIB) == 1
    a.kill("^ArnoAccel")
    a.set("/tmp/ivg-no-such-libarno_callout.so", "^ArnoAccel", "lib_path")
    assert int(a.classMethodValue(AA, "IsAvailable")) == 1
    assert a.classMethodValue(AA, "LoadedPath") == DEFAULT


def test_a_process_that_never_loaded_loads_on_first_use_when_a_path_is_configured(two):
    a, b = two
    a.kill("^ArnoAccel")
    a.set(LIB, "^ArnoAccel", "lib_path")
    assert int(b.classMethodValue(AA, "IsAvailable")) == 1
    assert _readers(b).get("pagerank", 0) >= 1
    assert str(b.classMethodValue("%SYSTEM.SYS", "NameSpace")) == str(a.classMethodValue("%SYSTEM.SYS", "NameSpace"))


def test_build_nkg_rust_calls_nothing_and_leaves_the_namespace(two):
    """With the handle in ^||ArnoAccel, NKGAccel.BuildNKGRust (no caller in IVG) would have reached arno's deprecated
    kg_build_nkg, which reads USER and leaves the process there: every later call on the connection then ran in USER
    (123 E2E failures after test_objectscript_split_resolution called it, 2026-11-01). It refuses instead."""
    a, _ = two
    ns = str(a.classMethodValue("%SYSTEM.SYS", "NameSpace"))
    assert a.classMethodValue(AA, "Load", LIB) == 1
    out = json.loads(str(a.classMethodValue("Graph.KG.NKGAccel", "BuildNKGRust")))
    assert "deprecated" in out["error"]
    assert str(a.classMethodValue("%SYSTEM.SYS", "NameSpace")) == ns
