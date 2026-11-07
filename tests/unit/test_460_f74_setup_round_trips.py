"""4.6.0 fixes74: setup over a slow link takes about a minute, not hours.

Round 3's IRIS-operator walkthrough ran initialize_schema() from a laptop against a server 83 ms away: it logged "IVG
ready" after a minute and had not returned after 40. legacy.grants_on read `%Library.SQLCatalogPriv:SQLAllPrivileges`
through a Native API %ResultSet, one round trip per column of every row it returned (about 140 rows for one object,
most of them other objects'), once for each of the 63 read-path routines and rdf_props: about 100,000 round trips,
measured as 23,191 in the first ten minutes. The grants are now read in one SQL statement for every object, which the
DB-API fetches in batches, so the number of round trips no longer grows with the number of objects.
"""

from __future__ import annotations

import collections
import re

import pytest

from iris_vector_graph._engine import legacy


class _Cursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, params=None):
        self.conn.statements.append((sql, list(params or [])))
        self.rows = self.conn.answer(sql, list(params or []))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class _Conn:
    """A DB-API connection answering the catalog queries setup's grant steps ask, counting every statement."""

    def __init__(self, routines, privileges=(), roles=None):
        self.routines, self.privileges, self.roles = list(routines), list(privileges), roles or {}
        self.statements = []

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass

    def answer(self, sql, params):
        if "INFORMATION_SCHEMA.ROUTINES" in sql:
            return [(r,) for r in self.routines]
        if "SQLAllPrivileges" in sql:
            wanted = {p.upper() for p in params[1:]}
            return [r for r in self.privileges if str(r[0]).upper() in wanted]
        if "SQLUserRole" in sql:
            return [(r,) for r in self.roles.get(params[0], ())]
        return []


def _grant(obj, subject, privilege, via, subject_type="ROLE", by=None):
    return (obj, subject, subject_type, privilege, "No", via, by)


class TestOneStatementForEveryObject:
    def test_the_grants_of_many_objects_are_one_statement(self):
        names = [f"Graph_KG.F{i}" for i in range(64)]
        conn = _Conn([], [_grant("GRAPH_KG.F3", "R1", "EXECUTE", "Direct"), _grant("Graph_KG.F9", "R2", "EXECUTE",
                                                                                     "Schema Privilege")])
        got = legacy.grants_by_object(conn, names)
        assert len(conn.statements) == 1, conn.statements
        assert set(got) == set(names)  # keyed by the names asked, matched without regard to case
        assert [g["subject"] for g in got["Graph_KG.F3"]] == ["R1"]
        assert got["Graph_KG.F9"][0]["via"] == "Schema Privilege"
        assert all(got[n] == [] for n in names if n not in ("Graph_KG.F3", "Graph_KG.F9"))

    def test_a_grantor_list_is_read(self):
        # GRANTED_BY of a grant a user made is a $List of grantors (_grantors), read as %INTERNAL text
        conn = _Conn([], [_grant("Graph_KG.F1", "U1", "EXECUTE", "Direct", "USER", "\x07\x01ADMIN")])
        [g] = legacy.grants_on(conn, "Graph_KG.F1")
        assert g == {"subject": "U1", "subject_type": "USER", "privilege": "EXECUTE", "with_grant": False,
                     "via": "Direct", "granted_by": ["ADMIN"]}

    @pytest.mark.parametrize("raw, names", [
        (b"\t\x01_SYSTEM", ["_SYSTEM"]),  # the DB-API hands %INTERNAL(GRANTED_BY) back as bytes (Community 2026.1)
        (b"\x06\x01F74U", ["F74U"]),  # a grant user F74U made
        (b"_SYSTEM", ["_SYSTEM"]),  # a row the query makes up (SuperUser, Owner Privilege): the name as text
        ("_SYSTEM", ["_SYSTEM"]),
    ])
    def test_a_grantor_read_as_bytes_is_its_name(self, raw, names):
        conn = _Conn([], [_grant("Graph_KG.F1", "R", "EXECUTE", "Direct", by=raw)])
        assert legacy.grants_on(conn, "Graph_KG.F1")[0]["granted_by"] == names

    def test_no_object_asks_nothing(self):
        conn = _Conn([])
        assert legacy.grants_by_object(conn, []) == {}
        assert conn.statements == []


@pytest.mark.parametrize("n", [5, 60])
def test_the_read_path_grants_take_the_same_round_trips_for_any_number_of_routines(monkeypatch, n):
    names = [f"CY_R{i}" for i in range(n)]
    monkeypatch.setattr(legacy, "read_path_routines", lambda: names)
    conn = _Conn(names)  # a fresh namespace: every routine there, no grant on any
    assert legacy.grant_read_path(conn, None, "Graph_KG", dry_run=False) == []
    assert len(conn.statements) == 2, conn.statements  # the routines in the schema, then every grant at once


def test_a_select_holder_still_gets_the_read_path_in_one_read(monkeypatch):
    names = [f"CY_R{i}" for i in range(40)]
    monkeypatch.setattr(legacy, "read_path_routines", lambda: names)
    conn = _Conn(names, [_grant("Graph_KG.rdf_props", "SELROLE", "SELECT", "Direct")])
    out = legacy.grant_read_path(conn, None, "Graph_KG", dry_run=False)
    assert sorted(r["routine"] for r in out) == sorted(f"Graph_KG.{n}" for n in names)
    grants = [s for s, _ in conn.statements if s.startswith("GRANT ")]
    reads = [s for s, _ in conn.statements if not s.startswith("GRANT ")]
    assert len(grants) == 40 and len(reads) == 2, reads


def test_a_user_whose_role_qualifies_is_left_to_the_role(monkeypatch):
    monkeypatch.setattr(legacy, "read_path_routines", lambda: ["CY_A"])
    conn = _Conn(["CY_A"], [_grant("Graph_KG.rdf_props", "READERS", "SELECT", "Direct"),
                            _grant("Graph_KG.rdf_props", "ALICE", "SELECT", "Direct", "USER")],
                 roles={"ALICE": ["READERS"]})
    out = legacy.grant_read_path(conn, None, "Graph_KG", dry_run=False)
    assert [r["subject"] for r in out] == ["READERS"]


def test_revoking_legacy_arno_grants_reads_every_copy_at_once():
    copies = [{"schema": "SQLUser", "name": f"ivg_arno_f{i}"} for i in range(19)]
    conn = _Conn([])
    out, warnings = legacy.revoke_legacy_arno(conn, None, "Graph_KG", copies, dry_run=False)
    assert out == [] and warnings == []
    assert len([s for s, _ in conn.statements if "SQLAllPrivileges" in s]) == 1, conn.statements


def test_no_native_result_set_is_opened_for_grants():
    src = open(legacy.__file__).read()
    assert not re.search(r'%ResultSet.*SQLAllPrivileges', src), "the grants are read in SQL, not cell by cell"


class TestClassLoadRoundTrips:
    """The class deploy loads each class in the fewest calls LoadStream allows: the stream's %New, one Write, Rewind and
    LoadStream (measured on 2026.3 over the 83 ms link's stand-in: 67 classes, 5.6 calls each before). A load that
    succeeded is told by its status, 1, without asking the server."""

    def _counting(self):
        from tests.unit.test_class_deploy import FakeIRIS

        fake, calls = FakeIRIS(), []
        for name in ("classMethodObject", "classMethodValue", "get", "set", "kill", "isDefined"):
            inner = getattr(fake, name)
            setattr(fake, name, lambda *a, _i=inner, _n=name: calls.append((_n, a[:2])) or _i(*a))
        return fake, calls

    def test_every_packaged_class_is_one_write(self):
        from iris_vector_graph._engine import class_deploy as cd

        sources = cd.read_class_sources(cd.packaged_class_dir())
        biggest = max(sources, key=lambda n: len(sources[n]))
        assert len(sources[biggest]) <= cd.STREAM_CHUNK, biggest
        assert cd.STREAM_CHUNK < 3_641_144  # IRIS's longest string

    def test_a_clean_deploy_asks_the_server_nothing_to_read_a_status(self):
        from iris_vector_graph._engine import class_deploy as cd
        from tests.unit.test_class_deploy import FakeStream

        writes = []
        real_invoke = FakeStream.invoke

        def invoke(self, method, *args):
            writes.append(method)
            return real_invoke(self, method, *args)

        sources = {f"A.C{i}": f"Class A.C{i}\n{{\n}}\n" for i in range(30)}
        fake, calls = self._counting()
        FakeStream.invoke = invoke
        try:
            result = cd.upload_and_compile(fake, "USER", sources)
        finally:
            FakeStream.invoke = real_invoke
        assert result.deployed and not result.errors
        assert not [c for c in calls if c[1] == ("%SYSTEM.Status", "IsOK")], "a status of 1 is OK as it is"
        per_class = (len([c for c in calls if c[1] in (("%Stream.GlobalCharacter", "%New"),
                                                       ("%SYSTEM.OBJ", "LoadStream"))]) + len(writes)) / len(sources)
        assert per_class == 4  # %New, Write, Rewind, LoadStream


def test_a_status_that_is_not_1_is_still_read_by_the_server():
    from iris_vector_graph._engine import class_deploy as cd

    asked = []

    class Native:
        def classMethodValue(self, cls, method, status):
            asked.append(status)
            return 0

    assert cd._ok(Native(), 1) and cd._ok(Native(), "1") and asked == []
    assert not cd._ok(Native(), "0 $lb(...)") and asked == ["0 $lb(...)"]


# ------------------------------------------------------------------ the whole setup, counted on a fresh namespace


class _CountingStream:
    def __init__(self, trips):
        self.trips, self.text = trips, []

    def invoke(self, method, *args):
        self.trips.append(("stream", method))
        if method == "Write":
            self.text.append(args[0])


class _CountingResultSet:
    """``%Library.SQLCatalogPriv:SQLAllPrivileges`` opened over the Native API: every call a round trip."""

    COLUMNS = ["OBJECT_NAME", "SUBJECT_NAME", "SUBJECT_TYPE", "PRIVILEGE_NAME", "HAS_GRANT_OPTION", "GRANTED_VIA"]

    def __init__(self, trips, grants):
        self.trips, self.grants, self.at, self.obj = trips, grants, 0, ""

    def invoke(self, method, *args):
        self.trips.append(("resultset", method))
        if method == "Execute":
            self.obj = args[0] if args else ""
            return 1
        if method == "GetColumnCount":
            return len(self.COLUMNS)
        if method == "GetColumnName":
            return self.COLUMNS[args[0] - 1]
        if method == "Next":
            self.at += 1
            return int(self.at <= self.grants)
        if method == "GetData":
            return [self.obj, f"ROLE{self.at}", "ROLE", "EXECUTE", "No", "Direct"][args[0] - 1]
        if method == "Close":
            return 1
        raise AssertionError(method)


class _CountingNative:
    """The Native API as a fresh namespace answers setup: no global, no class until one is loaded. Every call is one
    round trip, counted; a class method this fake does not know raises as an absent class does."""

    def __init__(self, ns, trips, grants_per_object=0):
        self.ns, self.trips, self.g, self.grants_per_object = ns, trips, {}, grants_per_object

    def get(self, *subs):
        self.trips.append(("get",))
        return self.g.get(subs)

    def set(self, value, *subs):
        self.trips.append(("set",))
        self.g[subs] = value

    def kill(self, *subs):
        self.trips.append(("kill",))
        self.g.pop(subs, None)

    def isDefined(self, *subs):
        self.trips.append(("isDefined",))
        return 10 if subs in self.g else 0

    def classMethodObject(self, cls, method, *args):
        self.trips.append(("object", cls, method))
        if (cls, method) == ("%Stream.GlobalCharacter", "%New"):
            return _CountingStream(self.trips)
        if (cls, method) == ("%SYS.Python", "Import"):
            return object()
        if (cls, method) == ("%ResultSet", "%New"):  # how 4.6.0 before fixes74 read the grants, cell by cell
            return _CountingResultSet(self.trips, self.grants_per_object)
        raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

    def classMethodValue(self, cls, method, *args):
        self.trips.append(("value", cls, method))
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return "IVGPIN"
        if (cls, method) == ("%Dictionary.CompiledClass", "%ExistsId"):
            return int(args[0] in self.ns.classes)
        if (cls, method) == ("%SYSTEM.OBJ", "LoadStream"):
            self.ns.classes.add("".join(args[0].text).split()[1])
            return 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            return 1
        raise RuntimeError(f"<CLASS DOES NOT EXIST> *{cls}")

    def classMethodVoid(self, cls, method, *args):
        self.trips.append(("void", cls, method))


def _fresh_setup(monkeypatch, grants_per_object=0):
    """initialize_schema() on test_460_r050's fake namespace, never set up: (round trips, the status). With
    ``grants_per_object``, the privilege catalog holds that many grants on every object setup asks about."""
    from unittest.mock import MagicMock

    from iris_vector_graph.engine import IRISGraphEngine
    from tests.unit.test_460_r050_setup_status import _Cursor as _CatalogCursor
    from tests.unit.test_460_r050_setup_status import _Namespace

    monkeypatch.setenv("IVG_EMBEDDED_PYTHON", "1")
    monkeypatch.delenv("IVG_FORCE_CLASS_DEPLOY", raising=False)
    ns, trips = _Namespace(), []

    class Cursor(_CatalogCursor):
        def execute(self, sql, params=None):
            trips.append(("sql", " ".join(sql.split())[:60]))
            routine = re.match(r"\s*CREATE (?:OR REPLACE )?(?:FUNCTION|PROCEDURE) \w+\.(\w+)", sql, re.I)
            if routine:  # the catalog lists what setup created, so its grant steps read every routine
                ns.routines.add(routine.group(1))
            if "SQLAllPrivileges" in sql:
                self._rows = [(name, f"ROLE{i}", "ROLE", "EXECUTE", "No", "Direct", "")
                              for name in (params or [])[1:] for i in range(grants_per_object)]
                return
            return super().execute(sql, params)

    native = _CountingNative(ns, trips, grants_per_object)
    conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    conn.cursor.side_effect = lambda: Cursor(ns, read_only=False)
    monkeypatch.setattr("iris.createIRIS", lambda c: native)
    status = IRISGraphEngine(conn, embedding_dimension=8).initialize_schema()
    return trips, status


#: Round trips a fresh setup makes on this fake, measured 2026-11-07 (fixes74): 515, of which 256 load the 64 classes
#: (4 each). The grant reads before fixes74 (a Native call per privilege cell) made 954 here, and 62,554 with 200
#: grants on each object; on dpgenai1 they made 23,191 in setup's first ten minutes. A release that ships more classes
#: or routines moves this by 4 a class and about 1 a routine: measure again and say so here.
FRESH_SETUP_ROUND_TRIPS = 515


def test_a_fresh_setup_makes_a_fixed_number_of_round_trips(monkeypatch):
    trips, status = _fresh_setup(monkeypatch)
    assert status["schema_recorded"] == "new" and status["objectscript_compiled"]
    loads = [t for t in trips if t in (("stream", "Write"), ("stream", "Rewind"), ("value", "%SYSTEM.OBJ", "LoadStream"),
                                       ("object", "%Stream.GlobalCharacter", "%New"))]
    assert len(loads) == 4 * len(status["objectscript_compiled"])
    assert len(trips) <= FRESH_SETUP_ROUND_TRIPS, sorted(collections.Counter(trips).items(), key=lambda kv: -kv[1])[:15]


def test_a_fresh_setup_makes_as_many_round_trips_whatever_grants_the_namespace_holds(monkeypatch):
    few, _ = _fresh_setup(monkeypatch, grants_per_object=0)
    many, _ = _fresh_setup(monkeypatch, grants_per_object=200)
    assert len(many) == len(few)
