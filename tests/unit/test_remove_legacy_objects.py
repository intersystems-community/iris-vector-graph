"""Spec 262 T032: `remove_legacy_objects` lists and removes by the frozen manifest, never by pattern (FR-011, FR-012).
4.6.3: and only what IVG shipped. A manifest name whose body, view query or class source is in none of the releases'
texts (`iris_vector_graph.legacy_bodies`) is listed with ``ivg_owned`` False and a note, and kept.

The cursor below answers the catalog reads the function makes and records every other statement; the native object
records class deletions and answers class-source reads. Both raise on anything else, so a call the code makes that
this file does not expect fails. The fixture's "IVG" texts are IVG's because the fixture puts their fingerprints in
the module's tables; `tests/unit/test_legacy_bodies.py` checks the real tables against real release bodies.
"""

import pytest

from iris_vector_graph import legacy_bodies
from iris_vector_graph._engine import legacy
from iris_vector_graph._engine.legacy import remove_legacy_objects
from iris_vector_graph.legacy_text import class_fingerprint, routine_fingerprint, view_fingerprint, view_query

#: Bodies of the namespace's IVG functions, as INFORMATION_SCHEMA.ROUTINES.ROUTINE_DEFINITION holds them.
IVG_BODY = {
    "CY_CMP": "\r\n\tSet a = 1 Quit a ",
    "STR_SPLIT": '\r\n    If s = "" { Return "[]" }\r\n    Return s',
    "CypherFn_IVGRANGE": '\r\n    If stepVal = 0 { Return "[]" }',
}
#: The operator's own JSON_VALUE in SQLUser (reads a nested path) and RAND: names on the manifest, bodies IVG never had.
USER_BODY = {
    "JSON_VALUE": '\r\n\tQuit ##class(%Library.DynamicObject).%FromJSON(j).%Get($Piece(p, ".", 3)) ',
    "RAND": "\r\n\tQuit 0.5 ",
}
#: What the namespace holds in SQLUser: three 4.5.x functions, the operator's two, and the application's CY_MINE...
ROUTINES = list(IVG_BODY.items()) + list(USER_BODY.items()) + [("CY_MINE", "Quit 7")]
IVG_VIEW = '(node_id,created_at)\r\n SELECT node_id, created_at FROM Graph_KG.nodes /*#OPTIONS {"xDBC":1} */'
VIEWS = [("nodes", IVG_VIEW)]
#: ...and these classes: three 4.5.x, the application's User.funcCYMINE, the operator's generated two.
CLASSES = [("User.funcCYCMP",), ("User.funcSTRSPLIT",), ("User.CypherFn",), ("User.funcCYMINE",), ("User.nodes",),
           ("User.funcJSONVALUE",), ("User.funcRAND",)]
CYPHERFN_SOURCE = "Class User.CypherFn Extends %RegisteredObject\r\n{\r\n}\r\n"
SOURCES = {"User.CypherFn": CYPHERFN_SOURCE}


class Cursor:
    def __init__(self, routines=None, views=None, classes=None):
        self.statements = []
        self._rows = []
        self.routines = ROUTINES if routines is None else routines
        self.views = VIEWS if views is None else views
        self.classes = CLASSES if classes is None else classes

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if "INFORMATION_SCHEMA.ROUTINES" in text:
            self._rows = self.routines
        elif "INFORMATION_SCHEMA.VIEWS" in text:
            self._rows = self.views
        elif "%Dictionary.ClassDefinition" in text:
            self._rows = [c for c in self.classes if c[0] in (params or [])]
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
    def __init__(self, **kw):
        self.cur = Cursor(**kw)
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


class Native:
    def __init__(self, sources=None):
        self.deleted = []
        self.sources = SOURCES if sources is None else sources

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "Delete"):
            self.deleted.append(args[0])
            return 1
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return 1 if args[0] == 1 else 0
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return args[0][2:]
        if (cls, method) == ("%Compiler.UDL.TextServices", "GetTextAsString"):
            _namespace, name, ref = args
            if name not in self.sources:
                return "0 Class does not exist"
            ref.setValue(self.sources[name])
            return 1
        raise AssertionError(f"unexpected {cls}.{method}{args}")


@pytest.fixture(autouse=True)
def shipped(monkeypatch):
    """The fixture's IVG texts are ones the releases shipped (the real tables are checked in test_legacy_bodies)."""
    monkeypatch.setitem(legacy_bodies.ROUTINE_BODIES, "CY_CMP", (routine_fingerprint(IVG_BODY["CY_CMP"]),))
    monkeypatch.setitem(legacy_bodies.ROUTINE_BODIES, "STR_SPLIT", (routine_fingerprint(IVG_BODY["STR_SPLIT"]),))
    monkeypatch.setitem(legacy_bodies.ROUTINE_BODIES, "CypherFn_IVGRANGE",
                        (routine_fingerprint(IVG_BODY["CypherFn_IVGRANGE"]),))
    monkeypatch.setitem(legacy_bodies.VIEW_QUERIES, "nodes", (view_fingerprint(view_query(IVG_VIEW)),))
    monkeypatch.setitem(legacy_bodies.CLASS_SOURCES, "User.CypherFn",
                        (class_fingerprint("User.CypherFn", CYPHERFN_SOURCE),))


@pytest.fixture
def world():
    return Conn(), Native()


def _by_name(listed):
    return {i["sql_name"] or i["class_name"]: i for i in listed}


def test_the_dry_run_lists_only_manifest_objects_present(world):
    conn, native = world
    listed = remove_legacy_objects(conn, native, dry_run=True)
    names = {i["sql_name"] or i["class_name"] for i in listed}
    assert names == {"SQLUser.CY_CMP", "SQLUser.STR_SPLIT", "SQLUser.CypherFn_IVGRANGE", "SQLUser.nodes",
                     "SQLUser.JSON_VALUE", "SQLUser.RAND"}
    assert all(i["present"] and i["removed"] is False for i in listed)


def test_the_dry_run_says_which_objects_are_ivgs(world):
    conn, native = world
    listed = _by_name(remove_legacy_objects(conn, native, dry_run=True))
    owned = {n for n, i in listed.items() if i["ivg_owned"]}
    assert owned == {"SQLUser.CY_CMP", "SQLUser.STR_SPLIT", "SQLUser.CypherFn_IVGRANGE", "SQLUser.nodes"}
    assert all(i["note"] == "" for n, i in listed.items() if n in owned)
    for name in ("SQLUser.JSON_VALUE", "SQLUser.RAND"):
        note = listed[name]["note"]
        assert listed[name]["ivg_owned"] is False
        assert "not IVG's" in note and "kept" in note and name in note, note


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


def test_removal_drops_exactly_the_ivg_objects(world):
    conn, native = world
    listed = _by_name(remove_legacy_objects(conn, native, dry_run=False))
    assert {n for n, i in listed.items() if i["removed"]} == {
        "SQLUser.CY_CMP", "SQLUser.STR_SPLIT", "SQLUser.CypherFn_IVGRANGE", "SQLUser.nodes"}
    assert sorted(conn.cur.statements) == sorted([
        "DROP FUNCTION SQLUser.CY_CMP", "DROP FUNCTION SQLUser.STR_SPLIT", "DROP VIEW SQLUser.nodes",
    ])
    # A class holding SqlProc projections goes as a class, once, however many functions it projects.
    assert native.deleted == ["User.CypherFn"]


def test_a_same_named_function_ivg_never_shipped_is_kept(world):
    """The operator's JSON_VALUE and RAND (4.5.x had overwritten theirs; they installed them again): no DROP names
    them, and their generated classes are not deleted."""
    conn, native = world
    listed = _by_name(remove_legacy_objects(conn, native, dry_run=False))
    assert listed["SQLUser.JSON_VALUE"]["removed"] is False and listed["SQLUser.RAND"]["removed"] is False
    assert not [s for s in conn.cur.statements if "JSON_VALUE" in s or "RAND" in s]
    assert not {"User.funcJSONVALUE", "User.funcRAND"} & set(native.deleted)


def test_a_view_with_another_query_is_kept():
    conn = Conn(routines=[], views=[("nodes", "(id)\r\n SELECT id FROM MyApp.nodes")])
    listed = _by_name(remove_legacy_objects(conn, Native(), dry_run=False))
    assert listed["SQLUser.nodes"]["ivg_owned"] is False and listed["SQLUser.nodes"]["removed"] is False
    assert conn.cur.statements == []


def test_a_shipped_class_whose_source_ivg_never_shipped_is_kept():
    """An application's own User.SQL, a class name 4.5.x also shipped: deleting it as IVG's would lose it."""
    conn = Conn(routines=[], views=[], classes=[("User.SQL",)])
    native = Native(sources={"User.SQL": "Class User.SQL Extends %Persistent\r\n{\r\nProperty Text;\r\n}\r\n"})
    listed = _by_name(remove_legacy_objects(conn, native, dry_run=False))
    assert listed["User.SQL"]["ivg_owned"] is False and listed["User.SQL"]["removed"] is False
    assert "User.SQL" in listed["User.SQL"]["note"] and "kept" in listed["User.SQL"]["note"]
    assert native.deleted == []


def test_a_sqlproc_whose_class_ivg_never_shipped_is_kept():
    """CypherFn_IVGRANGE's body is IVG's, but its class holds code IVG never shipped: removing the function deletes
    the class, so neither goes."""
    conn = Conn(views=[])
    native = Native(sources={"User.CypherFn": CYPHERFN_SOURCE.replace("{\r\n}", "{\r\nClassMethod Mine()\r\n{\r\n"
                                                                                     "    Quit 1\r\n}\r\n}")})
    listed = _by_name(remove_legacy_objects(conn, native, dry_run=False))
    item = listed["SQLUser.CypherFn_IVGRANGE"]
    assert item["ivg_owned"] is False and item["removed"] is False and "User.CypherFn" in item["note"]
    assert "User.CypherFn" not in native.deleted


def test_a_class_whose_source_cannot_be_read_is_kept_and_says_why():
    conn = Conn(routines=[], views=[], classes=[("User.Exec",)])
    listed = _by_name(remove_legacy_objects(conn, Native(sources={}), dry_run=False))
    note = listed["User.Exec"]["note"]
    assert listed["User.Exec"]["ivg_owned"] is False and listed["User.Exec"]["removed"] is False
    assert "could not be read" in note and "Class does not exist" in note, note


def test_without_a_native_handle_no_class_is_ivgs():
    """No Native API, no class source: a shipped class is kept, never deleted unread."""
    conn = Conn(routines=[], views=[], classes=[("User.Exec",)])
    listed = _by_name(legacy.present_legacy_objects(conn, None))
    assert listed["User.Exec"]["ivg_owned"] is False and "could not be read" in listed["User.Exec"]["note"]


def test_the_leftovers_count_is_ivgs_objects_only(world):
    """The notice says the namespace holds objects IVG 4.5.x created: the operator's own two are not counted, so it
    stops once remove_legacy_objects has removed IVG's."""
    conn, native = world
    assert legacy.legacy_count(conn, native) == 4


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

    legacy._LEFTOVERS_LOGGED.clear()
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        for _ in range(3):
            legacy.log_leftovers(52, "IVGA")
        legacy.log_leftovers(4, "IVGB")
        legacy.log_leftovers(0, "IVGC")
    notices = [r for r in caplog.records if "still holds" in r.getMessage()]
    assert [(r.levelname, r.getMessage().split(" object(s)")[0][-2:]) for r in notices] == [
        ("INFO", "52"), ("INFO", " 4")]
