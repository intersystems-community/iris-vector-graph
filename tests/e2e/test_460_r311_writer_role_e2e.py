"""4.6.0 R311 [SECURITY], live, as ADMIN_GUIDE section 3's writing role: what it can set, and what that no longer makes
an administrator's process do.

A user holding only the writing row (the database at RW, the routines database at R, %Native_ClassExecution,
%Native_GlobalAccess, and INSERT/UPDATE/DELETE/SELECT/EXECUTE on the IVG Schema) sets `^ArnoAccel("lib_path")` and a
"pending" `^IVG.CypherCompiled` entry with lines of its own over the Native API. Then _SYSTEM, as the next process to
use them, resolves the library and builds the entry. On the release-4.6.0 classes _SYSTEM's LibPath() answered the
planted file (and RequireCallout allowed it), and Build compiled the planted lines into IVGC.P<h>.

The last test measures the layout fact ADMIN_GUIDE section 3 states: where the namespace's routines are in the same
database as its globals, the same user may write the routine globals too.

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up); the role and user are made for this
file and deleted after it.
"""

from __future__ import annotations

import pytest

from tests.e2e import reading_role460 as rr

pytestmark = [pytest.mark.e2e, *rr.requires_namespace]

TAG = "R311W"
DEFAULT = "/usr/irissys/mgr/libarno_callout.so"
PLANTED_LIB = "/tmp/r311_writer_planted.so"
H = "r311writer"


@pytest.fixture(scope="module")
def writer():
    import iris

    user = rr.make_reader(TAG, rr.reading_resources(database="RW"),
                          sql_privileges=("SELECT", "EXECUTE", "INSERT", "UPDATE", "DELETE"))
    wconn = rr.connect(user, rr.PASSWORD)
    aconn = rr.connect()
    admin = iris.createIRIS(aconn)
    old_lib = admin.get("^ArnoAccel", "lib_path")
    try:
        yield iris.createIRIS(wconn), admin
    finally:
        if old_lib is None:
            admin.kill("^ArnoAccel", "lib_path")
        else:
            admin.set(old_lib, "^ArnoAccel", "lib_path")
        admin.classMethodValue("%SYSTEM.OBJ", "Delete", f"IVGC.P{H}", "-d")
        admin.kill("^IVG.CypherCompiled", H)
        admin.kill("^IVG.R311Ran")
        admin.kill("^ROUTINE", "ZR311W")
        wconn.close()
        aconn.close()
        rr.drop_reader(TAG)


def test_the_writer_may_set_the_library_path_and_it_is_not_the_library(writer):
    w, admin = writer
    w.set(PLANTED_LIB, "^ArnoAccel", "lib_path")
    assert admin.get("^ArnoAccel", "lib_path") == PLANTED_LIB  # the role can write it
    assert admin.classMethodValue("Graph.KG.ArnoAccel", "LibPath") == DEFAULT
    with pytest.raises(Exception, match="refused"):
        admin.classMethodValue("Graph.KG.ArnoAccel", "RequireCallout", PLANTED_LIB, "version")


def test_the_writer_may_plant_plan_lines_and_they_are_not_compiled(writer):
    import iris

    w, admin = writer
    entry = iris.IRISList()
    for v in ("", "shape", "stamp", "pending", 0):
        entry.add(v)
    w.set(entry.getBuffer(), "^IVG.CypherCompiled", H)
    w.set("\tSet ^IVG.R311Ran = $Username", "^IVG.CypherCompiled", H, "src", 1)
    w.set(1, "^IVG.CypherCompiled", H, "src")
    admin.classMethodValue("Graph.KG.Cypher.Gen", "Build", H)
    assert not admin.classMethodValue("%Dictionary.CompiledClass", "%ExistsId", f"IVGC.P{H}")
    assert admin.get("^IVG.R311Ran") is None


def test_with_one_database_the_writer_may_write_routine_code(writer):
    """ADMIN_GUIDE section 3: where routines and globals share the database, RW on it is RW on the routines."""
    if rr.routines_resource() != rr.database_resource():
        pytest.skip("this namespace keeps its routines in a database of their own")
    w, admin = writer
    w.set("ZR311W ; written by the writing role", "^ROUTINE", "ZR311W", 0, 1)
    assert admin.get("^ROUTINE", "ZR311W", 0, 1) == "ZR311W ; written by the writing role"
