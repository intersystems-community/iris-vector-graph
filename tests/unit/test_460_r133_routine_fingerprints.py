"""4.6.0 R133: a second `initialize_schema()` re-creates no unchanged function or procedure.

Setup ran `CREATE OR REPLACE` on each of its 39 routines on every run, which recompiles the class IRIS generated for
each and discards the cached queries that call them (T061 measured it). Each routine's DDL text, together with the IVG
Schema, now has a fingerprint kept in `^IVG.Deploy("routines")`; a run skips a routine whose fingerprint is recorded
and that `INFORMATION_SCHEMA.ROUTINES` still lists, and re-creates it when its text or the schema changes, or when it
is missing (dropped by hand). The live half is T061 (`tests/e2e/test_262_inventory.py`).
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.schema import ROUTINES_MARKER, declared_routine_name, routine_fingerprint, routine_name
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

_CREATE = re.compile(r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+\w+\.(\w+)", re.I)


class _Namespace:
    def __init__(self):
        self.routines: set = set()  # upper-case names INFORMATION_SCHEMA.ROUTINES lists
        self.globals: dict = {}
        self.created: list = []  # names CREATE OR REPLACE ran for, in order
        self.refuse: set = set()  # names whose CREATE fails


class _Cursor:
    def __init__(self, ns: _Namespace):
        self.ns, self._rows = ns, []

    def execute(self, sql, params=None):
        self._rows = []
        if "INFORMATION_SCHEMA.ROUTINES" in sql:
            assert params, "the schema is bound, not interpolated"
            self._rows = [(n,) for n in sorted(self.ns.routines)]
            return
        m = _CREATE.search(sql)
        if m:
            name = m.group(1).upper()
            if name in self.ns.refuse:
                raise RuntimeError("[SQLCODE: <-400>:<Fatal error occurred>] optional routine")
            self.ns.created.append(name)
            self.ns.routines.add(name)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Native:
    def __init__(self, ns: _Namespace):
        self.ns = ns

    def get(self, *subs):
        return self.ns.globals.get(subs)

    def set(self, value, *subs):
        self.ns.globals[subs] = value


def _engine(schema=None):
    conn = MagicMock()
    kw = {"schema_prefix": schema} if schema else {}
    return IRISGraphEngine(conn, embedding_dimension=4, **kw)


def _run(ns: _Namespace, schema=None, native=True, **kw):
    eng = _engine(schema)
    native_obj = _Native(ns) if native else None

    def iris_obj():
        if native_obj is None:
            raise TypeError("createIRIS() argument must be an IRIS connection")
        return native_obj

    with patch.object(eng, "_iris_obj", side_effect=iris_obj), \
            patch.object(eng, "_embeddings_await_migration", return_value=False):
        ns.created.clear()
        return eng._install_procedures(_Cursor(ns), **kw)


def _all_names(schema="Graph_KG"):
    return [routine_name(s) for s in GraphSchema.get_procedures_sql_list(table_schema=schema) if routine_name(s)]


def test_setup_has_the_54_routines():
    """T061 counted 39; R182 added CY_FLOAT_TEXT, CY_TOSTRING and CY_REL_TOSTRING, R210 CY_JSON_TYPE and
    CY_VALUE_TYPE, R218 CY_TYPED, R240 CY_ARITH, R248 CY_JSON_NUM, R250 CY_IS_FLOAT, R227 CY_LIST_ELEM, R273
    CY_LIST_VAL."""
    assert len(_all_names()) == 55  # 4.6.0 R265 added kg_VEC_ROUTE_GUARD, R307 CY_STR_SIZE, CY_LIST_SRC and CY_JSON_LISTMAP, R310 CY_JSON_TYPED
    assert len(set(_all_names())) == 55


def test_the_first_run_creates_every_routine_and_records_each_fingerprint():
    ns = _Namespace()
    result = _run(ns)
    assert sorted(ns.created) == sorted(_all_names())
    record = json.loads(ns.globals[ROUTINES_MARKER])
    statements = {routine_name(s): s for s in GraphSchema.get_procedures_sql_list() if routine_name(s)}
    assert record == {n: routine_fingerprint(s, "Graph_KG") for n, s in statements.items()}
    # the reply spells each as its DDL does, the catalog's spelling (R050 round 2: KG_PPR there, kg_PPR in the catalog)
    declared = [declared_routine_name(s) for s in statements.values()]
    assert result == {"created": sorted(declared, key=str.upper), "unchanged": 0, "failed": {}}


def test_a_second_run_re_creates_nothing():
    ns = _Namespace()
    _run(ns)
    result = _run(ns)
    assert ns.created == []
    assert result == {"created": [], "unchanged": 55, "failed": {}}


def test_a_routine_dropped_by_hand_is_re_created_alone():
    ns = _Namespace()
    _run(ns)
    ns.routines.discard("CY_CMP")
    result = _run(ns)
    assert ns.created == ["CY_CMP"]
    assert result["unchanged"] == 54


def test_a_routine_whose_text_changed_is_re_created_alone():
    ns = _Namespace()
    _run(ns)
    original = GraphSchema.get_procedures_sql_list

    def changed(table_schema="Graph_KG"):
        return [s.replace("$RANDOM(1000000)", "$RANDOM(1000001)") for s in original(table_schema=table_schema)]

    with patch.object(GraphSchema, "get_procedures_sql_list", staticmethod(changed)):
        _run(ns)
    assert ns.created == ["RAND"]


def test_the_fingerprint_covers_the_text_and_the_schema():
    stmt = "CREATE OR REPLACE FUNCTION Graph_KG.RAND() RETURNS DOUBLE LANGUAGE OBJECTSCRIPT { Quit 1 }"
    assert routine_fingerprint(stmt, "Graph_KG") == routine_fingerprint(stmt, "Graph_KG")
    assert routine_fingerprint(stmt, "Graph_KG") != routine_fingerprint(stmt, "IVGALT")
    assert routine_fingerprint(stmt, "Graph_KG") != routine_fingerprint(stmt.replace("1", "2"), "Graph_KG")


def test_a_record_for_another_schema_re_creates_every_routine():
    """A record keyed for one schema's text never matches another's: every name is re-created there."""
    ns = _Namespace()
    _run(ns)
    _run(ns, schema="IVGALT")
    assert sorted(ns.created) == sorted(_all_names("IVGALT"))


def test_a_failed_routine_is_not_recorded_so_the_next_run_tries_it_again():
    ns = _Namespace()
    ns.refuse.add("KG_TXT")  # optional: needs the full-text feature
    _run(ns)
    assert "KG_TXT" not in json.loads(ns.globals[ROUTINES_MARKER])
    ns.refuse.clear()
    _run(ns)
    assert ns.created == ["KG_TXT"]


def test_without_the_native_api_every_routine_runs_and_nothing_is_recorded():
    ns = _Namespace()
    _run(ns, native=False)
    _run(ns, native=False)
    assert sorted(ns.created) == sorted(_all_names())
    assert ns.globals == {}


def test_force_re_creates_every_routine():
    ns = _Namespace()
    _run(ns)
    _run(ns, force=True)
    assert sorted(ns.created) == sorted(_all_names())


def test_an_unreadable_record_re_creates_every_routine_and_rewrites_it():
    ns = _Namespace()
    _run(ns)
    ns.globals[ROUTINES_MARKER] = "not json"
    _run(ns)
    assert sorted(ns.created) == sorted(_all_names())
    assert json.loads(ns.globals[ROUTINES_MARKER])


@pytest.mark.parametrize("stmt,name", [
    ("\nCREATE OR REPLACE FUNCTION Graph_KG.CY_CMP(pa VARCHAR(32000)) RETURNS INTEGER", "CY_CMP"),
    ("CREATE OR REPLACE PROCEDURE IVGALT.kg_KNN_VEC(\n  queryInput", "KG_KNN_VEC"),
    ("CREATE SCHEMA iris_vector_graph", None),
])
def test_routine_name(stmt, name):
    assert routine_name(stmt) == name
