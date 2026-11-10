"""4.7.0 row 26b: a snapshot carries the namespace's configuration tables, and a restore matches them on the key.

On 4.6.5 `save_snapshot` exported none of `rdf_namespaces` (the RDF prefixes), `table_mappings` and
`relationship_mappings` (the SQL tables mapped as nodes and relationships) and `fhir_bridges` (the deprecated FHIR
code bridges), so a restore elsewhere brought back none of them. As `fhir_graphs` and `code_crosswalk` (row 26), they
survive `erase_all()` and are matched on their primary keys: a replacing restore writes the archive's row over the one
there, and a merge keeps the row there and adds the missing ones. The known answer is the rows the test wrote.

    IVG_R470_WR_NS=IVG470WR pytest tests/e2e/test_470_snapshot_config_e2e.py
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from pathlib import Path

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R470_WR_NS", "")
SCRATCH = Path.home() / ".cache" / "ivg-bench" / "rel-470" / "txn-scratch"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R470_WR_NS"),
]

#: table -> (columns, key columns, the column a test changes, rows for a uid)
TABLES = {
    "rdf_namespaces": (
        ("prefix", "uri"), ("prefix",), "uri",
        lambda u: [(f"{u}a", f"http://example.org/{u}/a#"), (f"{u}b", f"http://example.org/{u}/b#")],
    ),
    "table_mappings": (
        ("label", "sql_table", "id_column", "prop_columns", "registered_at"), ("label",), "id_column",
        lambda u: [(f"L{u}a", "SQLUser.Patients", "pid", '["name", "dob"]', "2026-11-10 08:30:00"),
                   (f"L{u}b", "SQLUser.Visits", "vid", None, "2026-11-09 17:45:12")],
    ),
    "relationship_mappings": (
        ("predicate", "source_label", "target_label", "target_fk", "via_table", "via_source", "via_target"),
        ("source_label", "predicate", "target_label"), "target_fk",
        lambda u: [("HAD_VISIT", f"L{u}a", f"L{u}b", "pid", None, None, None),
                   ("SEES", f"L{u}a", f"L{u}a", None, "SQLUser.Referrals", "from_id", "to_id")],
    ),
    "fhir_bridges": (
        ("fhir_code", "kg_node_id", "fhir_code_system", "bridge_type", "confidence", "source_cui"),
        ("fhir_code", "kg_node_id"), "bridge_type",
        lambda u: [(f"C{u}", f"{u}n1", "ICD10CM", "icd10_to_mesh", 0.75, "C0011849"),
                   (f"C{u}", f"{u}n2", "ICD10CM", "icd10_to_mesh", 1.0, None)],
    ),
}
#: table -> (a key column, what each of this test's values in it starts with before the uid)
OWN_ROWS = {"rdf_namespaces": ("prefix", ""), "table_mappings": ("label", "L"),
            "relationship_mappings": ("source_label", "L"), "fhir_bridges": ("fhir_code", "C")}


def _connect():
    import iris

    return iris.connect("localhost", rr.PORT, NS, "_SYSTEM", "SYS")


@pytest.fixture
def eng():
    from iris_vector_graph import IRISGraphEngine

    conn = _connect()
    engine = IRISGraphEngine(conn, embedding_dimension=rr.DIM)
    u = "t470c" + uuid.uuid4().hex[:8]
    yield engine, u
    _delete_all(conn, u)
    conn.close()


def _norm(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, str) and len(v) >= 19 and v[4] == "-" and v[10] in " T" and v[13] == ":":
        return v[:10] + " " + v[11:19]
    if hasattr(v, "__float__") and not isinstance(v, int):
        return float(v)
    return v


def _match(table, u) -> str:
    col, lead = OWN_ROWS[table]
    return f"{col} %STARTSWITH '{lead}{u}'"


def _normed(rows):
    return sorted(tuple(_norm(v) for v in r) for r in rows)


def _rows(conn, table, u):
    cols = TABLES[table][0]
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(cols)} FROM Graph_KG.{table} WHERE {_match(table, u)}")
    return _normed(cur.fetchall())


def _want(table, u, changed=None):
    cols, _, column, rows = TABLES[table]
    out = [list(r) for r in rows(u)]
    for i, value in (changed or {}).items():
        out[i][cols.index(column)] = value
    return _normed(out)


def _write_all(conn, u):
    cur = conn.cursor()
    for table, (cols, _, _, rows) in TABLES.items():
        for r in rows(u):
            cur.execute(f"INSERT INTO Graph_KG.{table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        list(r))
    conn.commit()


def _delete_all(conn, u):
    cur = conn.cursor()
    for table in TABLES:
        cur.execute(f"DELETE FROM Graph_KG.{table} WHERE {_match(table, u)}")
    conn.commit()


def _key_where(table, u, i):
    cols, keys, _, rows = TABLES[table]
    row = rows(u)[i]
    return " AND ".join(f"{k} = ?" for k in keys), [row[cols.index(k)] for k in keys]


def _change(conn, table, u, i, value):
    where, params = _key_where(table, u, i)
    column = TABLES[table][2]
    conn.cursor().execute(f"UPDATE Graph_KG.{table} SET {column} = ? WHERE {where}", [value, *params])
    conn.commit()


def _delete(conn, table, u, i):
    where, params = _key_where(table, u, i)
    conn.cursor().execute(f"DELETE FROM Graph_KG.{table} WHERE {where}", params)
    conn.commit()


def _snapshot(engine, u) -> str:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = str(SCRATCH / f"{u}.ivgsnap")
    engine.save_snapshot(path, layers=["sql"])
    return path


def _assert_restored(engine, u, result, changed=None):
    for table in TABLES:
        assert _rows(engine.conn, table, u) == _want(table, u, changed), table
        assert f"Graph_KG.{table}" not in result["failed_rows"], result["failed_rows"]


def test_the_known_rows_are_what_the_test_wrote(eng):
    engine, u = eng
    _write_all(engine.conn, u)
    for table in TABLES:
        assert _rows(engine.conn, table, u) == _want(table, u), table


@pytest.mark.parametrize("merge", (False, True), ids=("replace", "merge"))
def test_a_restore_into_a_namespace_without_them_brings_them_back(eng, merge):
    engine, u = eng
    _write_all(engine.conn, u)
    path = _snapshot(engine, u)
    _delete_all(engine.conn, u)
    result = engine.restore_snapshot(path, merge=merge)
    _assert_restored(engine, u, result)


def test_a_replacing_restore_writes_the_archives_rows_over_the_ones_there(eng):
    engine, u = eng
    _write_all(engine.conn, u)
    path = _snapshot(engine, u)
    for table in TABLES:
        _change(engine.conn, table, u, 0, "changed")
    result = engine.restore_snapshot(path, merge=False)
    _assert_restored(engine, u, result)


def test_a_merge_restore_keeps_the_rows_there_and_adds_the_missing_ones(eng):
    """The second row of each table shares its first key column with the first in relationship_mappings and
    fhir_bridges: matched on that column alone, it would be skipped."""
    engine, u = eng
    _write_all(engine.conn, u)
    path = _snapshot(engine, u)
    for table in TABLES:
        _change(engine.conn, table, u, 0, "changed")
        _delete(engine.conn, table, u, 1)
    result = engine.restore_snapshot(path, merge=True)
    _assert_restored(engine, u, result, {0: "changed"})
