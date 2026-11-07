"""4.6.0 R153: `arno_bridge` creates its SQL wrappers only when R133's record says they are missing or changed.

`_ensure_zf_call_function` ran `CREATE OR REPLACE` on its 16 `ivg_arno_zf_*` functions on first use in every
connection (and `build_kg_adjacency_chunked` / `_build_kg_adjacency_serverside` theirs), recompiling each function's
class and discarding the cached queries that call it, although nothing had changed. It now keeps the same record
setup keeps for its routines (R133, `^IVG.Deploy("routines")`, `{ROUTINE NAME: sha256(schema, DDL)}`): a wrapper
whose fingerprint is recorded and that `INFORMATION_SCHEMA.ROUTINES` still lists is skipped. One record, so setup's
entries and the bridge's sit side by side, and neither drops the other's.

The namespace is a stand-in shared by every connection (its routines and globals); each test's "new connection" is a
new object, so the bridge's per-connection cache does not answer.
"""

from __future__ import annotations

import json
import re

import pytest

from iris_vector_graph.stores import arno_bridge
from iris_vector_graph._engine.schema import ROUTINES_MARKER, routine_fingerprint, routine_name

_CREATE = re.compile(r"CREATE\s+OR\s+REPLACE\s+(?:FUNCTION|PROCEDURE)\s+\w+\.(\w+)", re.I)


class _Namespace:
    def __init__(self):
        self.routines: set = set()
        self.globals: dict = {}
        self.created: list = []


class _Cursor:
    def __init__(self, ns):
        self.ns, self._rows = ns, []

    def execute(self, sql, params=None):
        self._rows = []
        if "INFORMATION_SCHEMA.ROUTINES" in sql:
            self._rows = [(n,) for n in sorted(self.ns.routines)]
            return
        m = _CREATE.search(sql)
        if m:
            self.ns.created.append(m.group(1).upper())
            self.ns.routines.add(m.group(1).upper())

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Native:
    def __init__(self, ns):
        self.ns = ns

    def get(self, *subs):
        return self.ns.globals.get(subs)

    def set(self, value, *subs):
        self.ns.globals[subs] = value


class _Conn:
    """A connection carrying its native object as `conn.iris` (host_for uses it)."""

    def __init__(self, ns, native=True):
        self.ns = ns
        if native:
            self.iris = _Native(ns)

    def cursor(self):
        return _Cursor(self.ns)

    def commit(self):
        pass


@pytest.fixture(autouse=True)
def _fresh_cache():
    arno_bridge.clear_probe_cache()
    yield
    arno_bridge.clear_probe_cache()


def _use(ns, schema, native=True):
    """First use of the bridge on a new connection: the per-connection cache cannot answer (it is keyed by id(conn),
    and a freed connection's id can come back)."""
    arno_bridge.clear_probe_cache()
    arno_bridge._ensure_zf_call_function(_Conn(ns, native=native), schema=schema)


def _wrappers(schema="Graph_KG"):
    # the bridge caches by id(conn), and a freed connection's id can come back
    arno_bridge.clear_probe_cache()
    created = _Namespace()
    arno_bridge._ensure_zf_call_function(_Conn(created, native=False), schema=schema)
    return created.created


def test_the_first_use_creates_every_wrapper_and_records_it():
    ns = _Namespace()
    _use(ns, "Graph_KG")
    assert ns.created == _wrappers() and len(ns.created) == len(set(_wrappers()))
    record = json.loads(ns.globals[ROUTINES_MARKER])
    assert set(record) == set(ns.created)


def test_a_new_connection_creates_none_of_the_unchanged_wrappers():
    ns = _Namespace()
    _use(ns, "Graph_KG")
    ns.created.clear()
    _use(ns, "Graph_KG")
    assert ns.created == []


def test_a_dropped_wrapper_is_created_again_alone():
    ns = _Namespace()
    _use(ns, "Graph_KG")
    ns.routines.discard("IVG_ARNO_ZF_PROBE")
    ns.created.clear()
    _use(ns, "Graph_KG")
    assert ns.created == ["IVG_ARNO_ZF_PROBE"]


def test_a_changed_wrapper_is_created_again():
    ns = _Namespace()
    _use(ns, "Graph_KG")
    record = json.loads(ns.globals[ROUTINES_MARKER])
    record["IVG_ARNO_ZF_SCC_RUN"] = "an older text's fingerprint"
    ns.globals[ROUTINES_MARKER] = json.dumps(record)
    ns.created.clear()
    _use(ns, "Graph_KG")
    assert ns.created == ["IVG_ARNO_ZF_SCC_RUN"]


def test_another_schema_is_another_fingerprint():
    ns = _Namespace()
    _use(ns, "Graph_KG")
    ns.created.clear()
    _use(ns, "IVGALT")
    assert len(ns.created) == len(_wrappers())


def test_setups_entries_stay_beside_the_bridges():
    ns = _Namespace()
    ns.routines.add("KG_KNN_VEC")
    ns.globals[ROUTINES_MARKER] = json.dumps({"KG_KNN_VEC": "setup's fingerprint"})
    _use(ns, "Graph_KG")
    record = json.loads(ns.globals[ROUTINES_MARKER])
    assert record["KG_KNN_VEC"] == "setup's fingerprint" and len(record) == 17


def test_without_the_native_api_every_wrapper_is_created_each_time():
    ns = _Namespace()
    for _ in range(2):
        arno_bridge.clear_probe_cache()
        ns.created.clear()
        _use(ns, "Graph_KG", native=False)
        assert len(ns.created) == len(_wrappers())
    assert ROUTINES_MARKER not in ns.globals


def test_the_record_holds_each_wrappers_own_fingerprint():
    ns = _Namespace()
    seen = []

    class _Recording(_Cursor):
        def execute(self, sql, params=None):
            if _CREATE.search(sql):
                seen.append(sql)
            super().execute(sql, params)

    conn = _Conn(ns)
    conn.cursor = lambda: _Recording(ns)
    arno_bridge._ensure_zf_call_function(conn, schema="Graph_KG")
    record = json.loads(ns.globals[ROUTINES_MARKER])
    assert record == {routine_name(s): routine_fingerprint(s, "Graph_KG") for s in seen}


def test_the_serverside_adjacency_functions_use_the_record_too():
    ns = _Namespace()

    class _Builder(_Cursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "ivg_arno_build_adj(" in sql and sql.lstrip().upper().startswith("SELECT"):
                self._rows = [("OK:0:0:0",)]

    conn = _Conn(ns)
    conn.cursor = lambda: _Builder(ns)
    arno_bridge._build_kg_adjacency_serverside(conn, schema="Graph_KG")
    assert ns.created == ["IVG_ARNO_BUILD_ADJ", "IVG_ARNO_GET_NODE_CHUNK"]
    arno_bridge.clear_probe_cache()
    ns.created.clear()
    conn2 = _Conn(ns)
    conn2.cursor = lambda: _Builder(ns)
    arno_bridge._build_kg_adjacency_serverside(conn2, schema="Graph_KG")
    assert ns.created == []
