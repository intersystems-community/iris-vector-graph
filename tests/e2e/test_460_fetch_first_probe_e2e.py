"""4.6.0 R130: a `from_connect` engine's FETCH FIRST probe leaves no table in any schema, and still answers what the
crash shape does on this build.

The probe (`engine._fetch_first_unsafe`, run the first time a query is translated) created `ivg_ff_probe_a` and
`ivg_ff_probe_b` unqualified, so in SQLUser, and never dropped them (spec 262 FR-002: nothing of IVG's outside the IVG
Schema). The fixture drops any such table a 4.5.x probe left, then a `from_connect` engine on `RAG` runs a LIMIT query.

The probe exists for the %qaqpre SIGSEGV (FETCH FIRST + a JOIN of VARCHAR-keyed tables kills the client: memory note
qaqpre_fetch_first_join_crash). The second test runs that shape itself, in a child process on tables of its own,
and the engine's answer must be the same: unsafe exactly when the child dies of a signal or hangs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

PROBE_TABLES = ("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE UPPER(TABLE_NAME) LIKE 'IVG_FF_PROBE%'")


@pytest.fixture(scope="module")
def probed():
    from iris_vector_graph import IRISGraphEngine

    alt.make_namespace()
    conn = alt.connect()
    alt.engine(conn).initialize_schema()
    cur = conn.cursor()
    for schema, table in alt.rows(conn, PROBE_TABLES):
        cur.execute(f'DROP TABLE "{schema}"."{table}"')
    conn.commit()
    assert alt.rows(conn, PROBE_TABLES) == []
    engine = IRISGraphEngine.from_connect("localhost", alt.PORT, alt.NS, "_SYSTEM", "SYS",
                                          embedding_dimension=alt.DIM, schema_prefix=alt.SCHEMA)
    engine.execute_cypher("MATCH (n)-[r]->(m) RETURN n.id LIMIT 1", raise_on_error=True)
    yield {"engine": engine, "conn": conn}
    engine.conn.close()
    conn.close()


def test_the_probe_ran(probed):
    assert probed["engine"]._fetch_first_unsafe_cached is not None


def test_no_probe_table_is_left_in_any_schema(probed):
    assert alt.rows(probed["conn"], PROBE_TABLES) == []


def test_the_probe_answers_what_the_crash_shape_does(probed):
    tag = uuid.uuid4().hex[:8]
    a, b = f"{alt.SCHEMA}.r130_shape_a_{tag}", f"{alt.SCHEMA}.r130_shape_b_{tag}"
    cur = probed["conn"].cursor()
    cur.execute(f"CREATE TABLE {a} (id VARCHAR(64))")
    cur.execute(f"CREATE TABLE {b} (sid VARCHAR(64))")
    probed["conn"].commit()
    params = dict(hostname="localhost", port=alt.PORT, namespace=alt.NS, username="_SYSTEM", password="SYS")
    script = (
        "import iris, json\n"
        f"c = iris.dbapi.connect(**json.loads({json.dumps(json.dumps(params))})); cur = c.cursor()\n"
        f"cur.execute('SELECT a.id FROM {a} a JOIN {b} b ON b.sid=a.id FETCH FIRST 1 ROWS ONLY'); cur.fetchall()\n"
        "print('SURVIVED')\n"
    )
    try:
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        crashed = r.returncode < 0 or r.returncode in (134, 139)
        assert crashed or "SURVIVED" in r.stdout, r.stderr[-2000:]
    except subprocess.TimeoutExpired:
        crashed = True
    finally:
        cur.execute(f"DROP TABLE {a}")
        cur.execute(f"DROP TABLE {b}")
        probed["conn"].commit()
    assert probed["engine"]._fetch_first_unsafe_cached is crashed
