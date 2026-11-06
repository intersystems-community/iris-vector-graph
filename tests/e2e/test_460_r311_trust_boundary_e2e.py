"""4.6.0 R311 [SECURITY], live: a role with the namespace's database at RW cannot make another process load a library
or compile code by writing IVG's globals.

ADMIN_GUIDE section 3's writing role (and "reading, fully") holds the database resource at RW and
`%Native_GlobalAccess:U`, so it may set any global in that database over the Native API. On the release-4.6.0 head:

- `^ArnoAccel("lib_path")` set to a file of one's own was the library `LibPath()` answered, so the next process to use
  Arno with no path named (`IsAvailable`, an admin's, holding `%System_CallOut`) loaded it, and `RequireCallout`, the
  R288 check, allowed exactly that file.
- an `^IVG.CypherCompiled(h)` entry set "pending", with lines of one's own under `(h, "src")`, was compiled by
  `Graph.KG.Cypher.Gen.Build(h)` into `IVGC.P<h>`, whose Walk1 then runs in the process of whoever runs that shape.

This file writes those globals as such a role would (it runs as _SYSTEM: the point is what the classes do with the
values, not who may write them) and restores them.

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG's classes deployed).
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

DEFAULT = "/usr/irissys/mgr/libarno_callout.so"
H = "r311test"
PLANTED_CLASS = f"IVGC.P{H}"


@pytest.fixture(scope="module")
def native():
    import iris

    conn = alt.connect()
    yield iris.createIRIS(conn)
    conn.close()


@pytest.fixture
def recorded_lib_path(native):
    old = native.get("^ArnoAccel", "lib_path")
    yield
    if old is None:
        native.kill("^ArnoAccel", "lib_path")
    else:
        native.set(old, "^ArnoAccel", "lib_path")


def _lib_path(native, path):
    native.set(path, "^ArnoAccel", "lib_path")
    return native.classMethodValue("Graph.KG.ArnoAccel", "LibPath")


@pytest.mark.parametrize("planted", ["/tmp/r311_planted.so", "/usr/irissys/mgr/../../tmp/r311_planted.so",
                                     "mgr/libarno_callout.so", "/usr/irissys/mgr/user/libarno_callout.so"])
def test_a_recorded_library_outside_the_instance_directories_is_not_the_library(native, recorded_lib_path, planted):
    assert _lib_path(native, planted) == DEFAULT
    with pytest.raises(Exception, match="refused"):
        native.classMethodValue("Graph.KG.ArnoAccel", "RequireCallout", planted, "version")


def test_a_recorded_library_in_the_manager_directory_is_the_library(native, recorded_lib_path):
    mgr = native.classMethodValue("%SYSTEM.Util", "ManagerDirectory")
    path = f"{mgr}libarno_callout_r311.so"
    assert _lib_path(native, path) == path
    assert _lib_path(native, DEFAULT) == DEFAULT


@pytest.fixture
def planted_plan(native):
    import iris

    def clean():
        native.classMethodValue("%SYSTEM.OBJ", "Delete", PLANTED_CLASS, "-d")
        native.kill("^IVG.CypherCompiled", H)
        native.kill("^IVG.R311Ran")

    clean()
    entry = iris.IRISList()
    for v in ("", "shape", "stamp", "pending", 0):
        entry.add(v)
    native.set(entry.getBuffer(), "^IVG.CypherCompiled", H)
    native.set("\tSet ^IVG.R311Ran = $Username", "^IVG.CypherCompiled", H, "src", 1)
    native.set(1, "^IVG.CypherCompiled", H, "src")
    yield
    clean()


def _status(native):
    import iris

    lst = iris.IRISList(native.getBytes("^IVG.CypherCompiled", H))
    return lst.get(4)


@pytest.mark.parametrize("digest", [None, "", "AAAA"])
def test_lines_written_to_the_global_are_not_compiled(native, planted_plan, digest):
    args = (H,) if digest is None else (H, digest)
    native.classMethodValue("Graph.KG.Cypher.Gen", "Build", *args)
    assert not native.classMethodValue("%Dictionary.CompiledClass", "%ExistsId", PLANTED_CLASS)
    status = _status(native)
    assert str(status).startswith("refused"), status
    assert native.get("^IVG.R311Ran") is None
