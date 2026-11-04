"""4.6.0 R153, live: a second connection's first use of the Arno bridge creates none of its unchanged wrappers.

`_ensure_zf_call_function` ran `CREATE OR REPLACE` on its 16 functions on each connection's first use. Now it keeps
R133's record (`^IVG.Deploy("routines")`). In `IVG_NAMESPACE` (IVG Schema RAG): one connection's first use leaves
the 16 listed in INFORMATION_SCHEMA.ROUTINES and recorded with their fingerprints; a second connection's first use
runs no CREATE; dropping one wrapper by hand makes the next connection create that one alone. The connections are
wrapped to count the statements they send; the wrapper carries the connection's Native API object as `conn.iris`,
which is how the bridge's host finds it.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

_CREATE = re.compile(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+\w+\.(\w+)", re.I)


class _Counting:
    def __init__(self, conn):
        import iris

        self._conn, self.iris, self.created = conn, iris.createIRIS(conn), []

    def cursor(self):
        outer, cur = self, self._conn.cursor()

        class _Cur:
            def execute(self, sql, params=None):
                m = _CREATE.search(sql)
                if m:
                    outer.created.append(m.group(1).upper())
                return cur.execute(sql, params) if params is not None else cur.execute(sql)

            def __getattr__(self, name):
                return getattr(cur, name)

        return _Cur()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _first_use():
    from iris_vector_graph.stores import arno_bridge

    arno_bridge.clear_probe_cache()
    conn = _Counting(alt.connect())
    arno_bridge._ensure_zf_call_function(conn, schema=alt.SCHEMA)
    conn.close()
    return conn.created


def _listed(conn) -> set:
    return {str(r[0]).upper() for r in alt.rows(
        conn, "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = ? "
              "AND ROUTINE_NAME LIKE 'ivg_arno_zf_%'", [alt.SCHEMA])}


@pytest.fixture(scope="module")
def ns():
    import iris

    alt.make_namespace()
    conn = alt.connect()
    alt.engine(conn).initialize_schema()
    yield conn, iris.createIRIS(conn)
    conn.close()


def test_a_second_connection_creates_no_unchanged_wrapper(ns):
    conn, nat = ns
    first = _first_use()
    listed = _listed(conn)
    assert len(listed) == 16 and set(first) <= listed, (first, listed)
    record = json.loads(nat.get("^IVG.Deploy", "routines"))
    assert listed <= set(record)
    assert _first_use() == []


def test_a_dropped_wrapper_is_created_again_alone(ns):
    conn, _ = ns
    _first_use()
    cur = conn.cursor()
    cur.execute(f"DROP FUNCTION {alt.SCHEMA}.ivg_arno_zf_scc_run")
    conn.commit()
    assert _first_use() == ["IVG_ARNO_ZF_SCC_RUN"]
    assert "IVG_ARNO_ZF_SCC_RUN" in _listed(conn)
