"""4.6.0 R132, live: both Arno loaders resolve one path, `IVG_ARNO_LIB` first, and status names the file.

A path recorded in `^ArnoAccel("lib_path")` is taken only when `Graph.KG.ArnoAccel.TrustedPath` allows it: the default,
or a file directly in the instance's manager or binary directory (4.6.0 R311, CHANGELOG "A role with the database at RW
can no longer make another user's process run code"). Recorded anywhere else (the test container's /tmp build), both
loaders open the default instead. The trusted case copies the library into the manager directory under its own name
and removes it after.

Runs in the namespace `IVG_NAMESPACE` names (a scratch one: the test writes and removes `^ArnoAccel("lib_path")`
there). The library used is `IVG_ARNO_LIB`, else the test container's pinned build at /tmp/libarno_callout.so; the
test skips when neither file exists on the server. Each Native API connection is its own server process, so a new
connection is a process that has loaded nothing.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_NAMESPACE", "")
DEFAULT = "/usr/irissys/mgr/libarno_callout.so"
LIB = os.environ.get("IVG_ARNO_LIB") or "/tmp/libarno_callout.so"


@pytest.fixture(scope="module")
def setup():
    if not NS.startswith("IVG460"):
        pytest.skip("set IVG_NAMESPACE to a scratch IVG460* namespace")
    from iris_vector_graph import IRISGraphEngine

    conn = ns262.connect(NS)
    IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
    nat = ns262.native(conn)
    if not int(nat.classMethodValue("%File", "Exists", LIB)):
        pytest.skip(f"{LIB} is not on the server")
    saved = nat.get("^ArnoAccel", "lib_path")
    trusted = nat.classMethodValue("%SYSTEM.Util", "ManagerDirectory") + "libarno_callout_r132.so"
    assert int(nat.classMethodValue("%File", "CopyFile", LIB, trusted)) == 1, f"could not copy {LIB} to {trusted}"
    yield conn, nat, trusted
    nat.classMethodValue("%File", "Delete", trusted)
    if saved is None:
        nat.kill("^ArnoAccel", "lib_path")
    else:
        nat.set(saved, "^ArnoAccel", "lib_path")
    conn.close()


def _fresh_process():
    conn = ns262.connect(NS)
    return conn, ns262.native(conn)


def test_libpath_order(setup):
    _conn, nat, trusted = setup
    nat.kill("^ArnoAccel", "lib_path")
    assert nat.classMethodValue("Graph.KG.ArnoAccel", "LibPath") == DEFAULT
    assert nat.classMethodValue("Graph.KG.ArnoAccel", "LibPath", "/x/libarno.so") == "/x/libarno.so"
    # a recorded path in the manager directory is taken; a named one still wins over it
    nat.set(trusted, "^ArnoAccel", "lib_path")
    assert nat.classMethodValue("Graph.KG.ArnoAccel", "LibPath") == trusted
    assert nat.classMethodValue("Graph.KG.ArnoAccel", "LibPath", "/x/libarno.so") == "/x/libarno.so"
    # a recorded path anywhere else is ignored (R311): the default, and a named one still wins
    nat.set("/recorded/libarno.so", "^ArnoAccel", "lib_path")
    assert nat.classMethodValue("Graph.KG.ArnoAccel", "LibPath") == DEFAULT
    assert nat.classMethodValue("Graph.KG.ArnoAccel", "LibPath", "/x/libarno.so") == "/x/libarno.so"


def test_a_named_path_reaches_both_loaders(setup):
    conn, nat = _fresh_process()  # a named path is the caller's choice: /tmp is loaded when named
    try:
        assert int(nat.classMethodValue("Graph.KG.ArnoAccel", "Load", LIB)) == 1
        assert int(nat.classMethodValue("Graph.KG.NKGAccel", "Load", LIB)) == 1
        assert nat.classMethodValue("Graph.KG.ArnoAccel", "LoadedPath") == LIB
        assert nat.classMethodValue("Graph.KG.NKGAccel", "LoadedPath") == LIB
    finally:
        conn.close()


def _load_with_no_argument(nat, recorded):
    nat.set(recorded, "^ArnoAccel", "lib_path")
    conn, fresh = _fresh_process()
    try:
        assert int(fresh.classMethodValue("Graph.KG.NKGAccel", "Load")) == 1  # the BFS loader first, on purpose
        assert int(fresh.classMethodValue("Graph.KG.ArnoAccel", "Load")) == 1
        return (fresh.classMethodValue("Graph.KG.NKGAccel", "LoadedPath"),
                fresh.classMethodValue("Graph.KG.ArnoAccel", "LoadedPath"))
    finally:
        conn.close()


def test_with_no_argument_both_loaders_open_the_recorded_file_in_the_manager_directory(setup):
    _conn, nat, trusted = setup
    assert _load_with_no_argument(nat, trusted) == (trusted, trusted)


def test_with_no_argument_both_loaders_ignore_a_recorded_file_outside_the_instance_directories(setup):
    _conn, nat, _trusted = setup
    if not int(nat.classMethodValue("%File", "Exists", DEFAULT)):
        pytest.skip(f"{DEFAULT} is not on the server")
    assert LIB != DEFAULT and not LIB.startswith("/usr/irissys/mgr/"), LIB
    assert _load_with_no_argument(nat, LIB) == (DEFAULT, DEFAULT)


def test_the_engine_passes_ivg_arno_lib_to_both_loaders_and_status_names_it(setup, monkeypatch):
    from iris_vector_graph import IRISGraphEngine

    _conn, nat, _trusted = setup
    nat.set("/nonexistent/libarno_callout.so", "^ArnoAccel", "lib_path")  # IVG_ARNO_LIB must win over it
    monkeypatch.setenv("IVG_ARNO_LIB", LIB)
    monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    conn = ns262.connect(NS)
    try:
        engine = IRISGraphEngine(conn, embedding_dimension=768)
        status = engine.status()
        assert status.arno.lib_path == LIB, status.arno
        assert f"Arno library:    {LIB} (loaded)" in status.report().splitlines()
        assert status.arno.loaded is True
        assert nat.get("^ArnoAccel", "lib_path") == LIB  # the successful load recorded it
    finally:
        conn.close()


def test_with_nothing_named_or_recorded_both_loaders_open_the_default(setup, monkeypatch):
    """The documented default path, exercised: no IVG_ARNO_LIB, no ^ArnoAccel("lib_path")."""
    from iris_vector_graph import IRISGraphEngine

    _conn, nat, _trusted = setup
    if not int(nat.classMethodValue("%File", "Exists", DEFAULT)):
        pytest.skip(f"{DEFAULT} is not on the server")
    nat.kill("^ArnoAccel", "lib_path")
    monkeypatch.delenv("IVG_ARNO_LIB", raising=False)
    monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    conn, fresh = _fresh_process()
    try:
        assert int(fresh.classMethodValue("Graph.KG.NKGAccel", "Load")) == 1
        assert int(fresh.classMethodValue("Graph.KG.ArnoAccel", "Load")) == 1
        assert fresh.classMethodValue("Graph.KG.NKGAccel", "LoadedPath") == DEFAULT
        assert fresh.classMethodValue("Graph.KG.ArnoAccel", "LoadedPath") == DEFAULT
    finally:
        conn.close()
    nat.kill("^ArnoAccel", "lib_path")
    conn = ns262.connect(NS)
    try:
        status = IRISGraphEngine(conn, embedding_dimension=768).status()
        assert status.arno.loaded is True and status.arno.lib_path == DEFAULT
        print("\n" + "\n".join(line for line in status.report().splitlines() if line.startswith(("Accel", "Arno"))))
    finally:
        conn.close()
