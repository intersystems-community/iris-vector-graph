"""idx_props_key_valkey is an index on (key, val_key), also when it is built on an install that predates val_key.

IRIS accepts `CREATE INDEX ... (key, val_key)` on a table with no `val_key` column and builds the index over `val`. The
base DDL ran that statement before `ensure_props_value_key` had added the column, so initialize_schema() on a pre-4.3
install either failed it (`SQLCODE -400 ... <SUBSCRIPT>` for one long value, `ERROR #5017: Too many errors` for many; the
statement is rolled back, and the migration then built the right index, so the warning was noise) or, when no value was
long, kept the wrong index under the right name, which the migration skipped and which fails every later insert of a
long value.

Each test puts `rdf_props` back to a pre-4.3 shape (no `val_key`, no index), so run against a scratch namespace:
`IVG_NAMESPACE=IVGFIX20`.
"""

from __future__ import annotations

import logging
import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

INDEX = "idx_props_key_valkey"
EMOJI = "\U0001F600"


def _index_columns(cursor):
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.INDEXES "
        f"WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_props' AND INDEX_NAME = '{INDEX}'"
    )
    return {str(r[0]).lower() for r in cursor.fetchall()}


def _make_pre_43(cursor):
    """rdf_props as a 4.2 install has it: no val_key column and no index on it."""
    for ddl in (
        f"DROP INDEX {INDEX} ON Graph_KG.rdf_props",
        "ALTER TABLE Graph_KG.rdf_props DROP COLUMN val_key",
    ):
        try:
            cursor.execute(ddl)
        except Exception:  # already gone; the assertion below says whether it is
            pass
    cursor.execute(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = 'rdf_props' AND COLUMN_NAME = 'val_key'"
    )
    assert cursor.fetchone() is None
    assert _index_columns(cursor) == set()


def _insert(cursor, tag, n_short, n_long):
    rows = [("", f"{tag}_s{i}", f"{tag}_k", f"short value {i}") for i in range(n_short)]
    rows += [("", f"{tag}_L{i}", f"{tag}_k", f"{i}-" + "x" * 3000) for i in range(n_long)]
    cursor.executemany("INSERT INTO Graph_KG.rdf_props (graph_id, s, key, val) VALUES (?,?,?,?)", rows)


@pytest.fixture
def pre_43_props(iris_connection, iris_master_cleanup):
    cursor = iris_connection.cursor()
    tag = f"vkx_{uuid.uuid4().hex[:8]}"
    GraphSchema.ensure_indexes(cursor)  # a known starting point
    _make_pre_43(cursor)
    yield cursor, tag
    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE key LIKE ?", [f"{tag}%"])
    GraphSchema.ensure_props_value_key(cursor)  # leave the namespace with the column and the right index


def _initialize(conn):
    engine = IRISGraphEngine(conn, embedding_dimension=768)
    engine.initialize_schema()
    return engine


def test_initialize_schema_on_a_pre_43_table_with_many_long_values_runs_no_failing_index_statement(
    iris_connection, pre_43_props, caplog
):
    cursor, tag = pre_43_props
    _insert(cursor, tag, n_short=200, n_long=300)  # past the point where IRIS reports "Too many errors"
    with caplog.at_level(logging.WARNING):
        _initialize(iris_connection)
    noisy = [r.getMessage() for r in caplog.records if "valkey" in r.getMessage() or "val_key" in r.getMessage()]
    assert noisy == [], noisy
    columns = _index_columns(cursor)
    assert "val_key" in columns and "val" not in columns, columns


def test_a_long_value_can_be_written_after_the_upgrade_when_none_existed_before(iris_connection, pre_43_props):
    """The old path kept an index on (key, val) here, and the first long value written afterwards was a <SUBSCRIPT>."""
    cursor, tag = pre_43_props
    _insert(cursor, tag, n_short=50, n_long=0)
    _initialize(iris_connection)
    columns = _index_columns(cursor)
    assert "val_key" in columns and "val" not in columns, columns
    _insert(cursor, f"{tag}b", n_short=0, n_long=3)  # raises on an index over val
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE key = ?", [f"{tag}b_k"])
    assert int(cursor.fetchone()[0]) == 3


def test_an_index_of_that_name_on_val_is_replaced(iris_connection, pre_43_props):
    """An install the old base DDL already damaged: the right name, over (key, val)."""
    cursor, tag = pre_43_props
    _insert(cursor, tag, n_short=20, n_long=0)
    # What the old base DDL did: IRIS builds this over val because the column does not exist yet.
    cursor.execute(f"CREATE INDEX {INDEX} ON Graph_KG.rdf_props (key, val_key)")
    assert "val" in _index_columns(cursor) and "val_key" not in _index_columns(cursor)
    status = GraphSchema.ensure_props_value_key(cursor)
    assert status["index"].startswith("rebuilt"), status
    assert "val_key" in _index_columns(cursor) and "val" not in _index_columns(cursor)
    _insert(cursor, f"{tag}b", n_short=0, n_long=2)  # a long value is writable again
    assert GraphSchema.ensure_props_value_key(cursor)["index"] == "present"


def test_every_row_with_a_value_key_is_found_through_key_and_val_key(iris_connection, pre_43_props):
    cursor, tag = pre_43_props
    _insert(cursor, tag, n_short=100, n_long=40)
    _initialize(iris_connection)
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE key = ?", [f"{tag}_k"])
    by_key = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE key = ? AND val_key IS NOT NULL", [f"{tag}_k"])
    assert by_key == int(cursor.fetchone()[0]) == 140
    cursor.execute(
        "SELECT COUNT(*) FROM Graph_KG.rdf_props WHERE key = ? AND val_key = ? AND val = ?",
        [f"{tag}_k", ("7-" + "x" * 3000)[:128], "7-" + "x" * 3000],
    )
    assert int(cursor.fetchone()[0]) == 1


def test_a_value_of_astral_characters_is_found_by_a_cypher_equality(iris_connection, iris_master_cleanup):
    """val_key counts UTF-16 units; the translator used to write the first 128 code points, which matched no row."""
    cursor = iris_connection.cursor()
    GraphSchema.ensure_indexes(cursor)
    engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
    node = f"astral_{uuid.uuid4().hex[:8]}"
    for label, value in (("A", EMOJI * 100), ("B", "a" + EMOJI * 100), ("C", EMOJI * 10 + "z" * 200)):
        engine.create_node(f"{node}_{label}", properties={"astralnote": value})
    assert engine._props_value_key is True
    for label, value in (("A", EMOJI * 100), ("B", "a" + EMOJI * 100), ("C", EMOJI * 10 + "z" * 200)):
        result = engine.execute_cypher(
            "MATCH (n) WHERE n.astralnote = $v RETURN n.id", {"v": value}, raise_on_error=True
        )
        assert [list(r) for r in result.rows] == [[f"{node}_{label}"]], label
