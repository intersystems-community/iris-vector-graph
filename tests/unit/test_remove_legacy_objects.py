"""Spec 262 T032: `remove_legacy_objects` lists and removes by the frozen manifest, never by pattern (FR-011, FR-012).

The cursor below answers the catalog reads the function makes and records every other statement; the native object
records class deletions. Both raise on anything else, so a call the code makes that this file does not expect fails.
"""

import pytest

from iris_vector_graph._engine.legacy import remove_legacy_objects

#: What the namespace holds in SQLUser: two 4.5.x functions, one 4.5.x view, and the application's CY_MINE and RAND...
ROUTINES = [("CY_CMP",), ("STR_SPLIT",), ("CY_MINE",), ("CypherFn_IVGRANGE",)]
VIEWS = [("nodes",)]
#: ...and these classes: two 4.5.x, the application's User.funcCYMINE.
CLASSES = [("User.funcCYCMP",), ("User.funcSTRSPLIT",), ("User.CypherFn",), ("User.funcCYMINE",), ("User.nodes",)]


class Cursor:
    def __init__(self):
        self.statements = []
        self._rows = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if "INFORMATION_SCHEMA.ROUTINES" in text:
            self._rows = ROUTINES
        elif "INFORMATION_SCHEMA.VIEWS" in text:
            self._rows = VIEWS
        elif "%Dictionary.ClassDefinition" in text:
            self._rows = [c for c in CLASSES if c[0] in (params or [])]
        else:
            self.statements.append(text)
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class Conn:
    def __init__(self):
        self.cur = Cursor()
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class EmptyGrants:
    """%ResultSet over SQLAllPrivileges answering no rows."""

    def invoke(self, method, *args):
        return {"Execute": 1, "GetColumnCount": 0, "Next": 0}[method]


class Native:
    def __init__(self):
        self.deleted = []

    def classMethodObject(self, cls, method, *args):
        assert (cls, method, args) == ("%ResultSet", "%New", ("%Library.SQLCatalogPriv:SQLAllPrivileges",))
        return EmptyGrants()

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "Delete"):
            self.deleted.append(args[0])
            return 1
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return 1 if args[0] == 1 else 0
        raise AssertionError(f"unexpected {cls}.{method}{args}")


@pytest.fixture
def world():
    return Conn(), Native()


def test_the_dry_run_lists_only_manifest_objects_present(world):
    conn, native = world
    listed = remove_legacy_objects(conn, native, dry_run=True)
    names = {i["sql_name"] or i["class_name"] for i in listed}
    assert names == {"SQLUser.CY_CMP", "SQLUser.STR_SPLIT", "SQLUser.CypherFn_IVGRANGE", "SQLUser.nodes"}
    assert all(i["present"] and i["removed"] is False for i in listed)


def test_the_dry_run_changes_nothing(world):
    conn, native = world
    remove_legacy_objects(conn, native, dry_run=True)
    assert conn.cur.statements == [] and native.deleted == []


def test_an_application_function_matching_a_pattern_is_never_listed_or_removed(world):
    conn, native = world
    listed = remove_legacy_objects(conn, native, dry_run=False)
    assert "SQLUser.CY_MINE" not in {i["sql_name"] for i in listed}
    assert not [s for s in conn.cur.statements if "CY_MINE" in s.upper()]
    assert "User.funcCYMINE" not in native.deleted


def test_removal_drops_exactly_the_listed_objects(world):
    conn, native = world
    listed = remove_legacy_objects(conn, native, dry_run=False)
    assert all(i["removed"] for i in listed)
    assert sorted(conn.cur.statements) == sorted([
        "DROP FUNCTION SQLUser.CY_CMP", "DROP FUNCTION SQLUser.STR_SPLIT", "DROP VIEW SQLUser.nodes",
    ])
    # A class holding SqlProc projections goes as a class, once, however many functions it projects.
    assert native.deleted == ["User.CypherFn"]
