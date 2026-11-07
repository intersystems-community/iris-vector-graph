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
        elif "SQLAllPrivileges" in text:
            self._rows = []  # no grant on any of them
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


class Native:
    def __init__(self):
        self.deleted = []

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


class GrantCursor:
    """SQLAllPrivileges as the catalog answers it for one function: the SuperUser, a direct grant, the owner."""

    ROWS = [
        ("SQLUser.CY_CMP", "_SYSTEM", "USER", "EXECUTE", "1", "SuperUser", ""),
        ("SQLUser.CY_CMP", "app_role", "ROLE", "EXECUTE", "Yes", "Direct", ""),
        ("SQLUser.CY_CMP", "_SYSTEM", "USER", "EXECUTE", "Yes", "Owner Privilege", ""),
    ]

    def __init__(self, seen):
        self.seen, self.rows = seen, []

    def execute(self, sql, params=None):
        self.seen.append((sql, list(params or [])))
        self.rows = [r for r in self.ROWS if r[0].upper() in [str(p).upper() for p in (params or [])[1:]]]

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


def test_the_grants_are_read_in_sql_on_the_connection():
    """The 4.0.0 upgrade under irispython died in direct_grants (IVG 4.6.0 E2E) when it opened a %ResultSet through
    ``iris.cls`` (``SystemError: Cannot modify a string currently used``). Since fixes74 the grants are one SQL
    statement on the connection, the same under irispython (EmbeddedConnection's cursor) as over TCP, and only the
    ``Direct`` row is a grant made on the object."""
    from iris_vector_graph._engine.legacy import direct_grants

    seen = []
    conn = type("C", (), {"cursor": lambda self: GrantCursor(seen)})()
    grants = direct_grants(conn, "SQLUser.CY_CMP")
    assert grants == [{"subject": "app_role", "subject_type": "ROLE", "privilege": "EXECUTE", "with_grant": True}]
    [(sql, params)] = seen
    assert '"%Library".SQLAllPrivileges(?)' in sql and params == ["", "SQLUSER.CY_CMP"]


def test_the_leftovers_notice_is_info_and_logged_once_per_namespace(caplog):
    """4.6.0: WARNING is for what an operator must act on. Removing the 4.5.x objects is optional until 5.0, so the
    notice is INFO, and three engines set up on one namespace (the README's order) log it once, not three times."""
    import logging

    from iris_vector_graph._engine import legacy

    legacy._LEFTOVERS_LOGGED.clear()
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        for _ in range(3):
            legacy.log_leftovers(52, "IVGA")
        legacy.log_leftovers(4, "IVGB")
        legacy.log_leftovers(0, "IVGC")
    notices = [r for r in caplog.records if "still holds" in r.getMessage()]
    assert [(r.levelname, r.getMessage().split(" object(s)")[0][-2:]) for r in notices] == [
        ("INFO", "52"), ("INFO", " 4")]
