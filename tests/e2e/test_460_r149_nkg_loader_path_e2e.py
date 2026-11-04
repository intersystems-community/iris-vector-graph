"""4.6.0 R149, live: `Graph.KG.NKGAccelLoader.Load` answers for the path it is asked to load, not a handle from another.

`Load` returned 1 whenever this process held a handle, from whatever file: `Load("/b")` after `Load("/a")` answered
1 and BFS went on calling /a's build. R132 made `Graph.KG.ArnoAccel.LibPath` the one order (the argument, then
`^ArnoAccel("lib_path")`, then the default), and `ArnoAccel.Load` loads from it on every call. So the BFS loader does
the same: a handle from another path is replaced by one from the resolved path, a failed load leaves none, and a
successful one records its path, so `Load()` with no argument keeps it.

One `iris session` is one process, so each sequence below runs in one. The two builds are the test container's: the
pinned one at /tmp/libarno_callout.so and the one at the default path (docker-compose mounts both, R132); the test
skips when either is missing. `^ArnoAccel("lib_path")` is saved and put back.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

A = "/tmp/libarno_callout.so"
B = "/usr/irissys/mgr/libarno_callout.so"
MISSING = "/nonexistent/r149/libarno_callout.so"
L = "##class(Graph.KG.NKGAccelLoader)"


@pytest.fixture(scope="module")
def ns():
    alt.make_namespace()
    conn = alt.connect()
    alt.engine(conn).initialize_schema()
    import iris

    nat = iris.createIRIS(conn)
    for path in (A, B):
        if not int(nat.classMethodValue("%File", "Exists", path)):
            pytest.skip(f"{path} is not on the server")
    saved = nat.get("^ArnoAccel", "lib_path")
    yield nat
    if saved is None:
        nat.kill("^ArnoAccel", "lib_path")
    else:
        nat.set(saved, "^ArnoAccel", "lib_path")
    conn.close()


def _run(steps: list) -> list:
    """Each step's `Load` answer and `LoadedPath` afterwards, as 'answer|path', in one process."""
    lines = []
    for i, arg in enumerate(steps):
        call = f'{L}.Load("{arg}")' if arg is not None else f"{L}.Load()"
        lines.append(f'write "<<S{i}:",{call},"|",{L}.LoadedPath(),"|",{L}.IsLoaded(),":S{i}>>",!')
    out = alt.session(lines)
    return [alt.marked(out, f"S{i}") for i in range(len(steps))]


def test_a_load_naming_another_path_loads_that_path(ns):
    assert _run([A, B]) == [f"1|{A}|1", f"1|{B}|1"]


def test_a_failed_reload_leaves_no_handle_from_the_old_path(ns):
    a, missing = _run([A, MISSING])
    assert a == f"1|{A}|1"
    assert missing == "0||0", missing


def test_a_load_with_no_argument_keeps_the_path_last_loaded(ns):
    ns.kill("^ArnoAccel", "lib_path")
    assert _run([A, None]) == [f"1|{A}|1", f"1|{A}|1"]
    assert ns.get("^ArnoAccel", "lib_path") == A


def test_the_same_path_twice_is_one_load(ns):
    assert _run([B, B]) == [f"1|{B}|1", f"1|{B}|1"]
