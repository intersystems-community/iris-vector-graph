"""The SQL `VectorOptimizer` sends must match the 4.0.0 embedding tables.

`vector_utils.VectorOptimizer` is a public helper whose every method wraps its
work in `except Exception` and answers with a dict, and the only tests it had
asserted `isinstance(result, dict)` — which holds just as well when every
statement fails. Post-227 it was sending three kinds of statement that cannot
work:

* unqualified table names, so `kg_NodeEmbeddings_optimized` resolved in the
  caller's default schema rather than `Graph_KG` and simply was not found;
* `SELECT id`, which is the implicit RowID after the `(graph_id, node_id)`
  re-key — an integer where the caller expects a node ID;
* `TO_VECTOR(?)` with no dtype, which IRIS rejects with `SQLCODE -259`
  ("vectors of different datatypes") against a `VECTOR(DOUBLE, n)` column at
  query open, so even an empty table errors.

These tests read the SQL back off the mock cursor, which is the only way to see
statements whose failures are swallowed.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.vector_utils import VectorOptimizer

OPT = "Graph_KG.kg_NodeEmbeddings_optimized"


def _conn(rows=None, one=(100,), declared=768):
    """A connection whose `emb` column is declared at `declared`.

    The width has to come from somewhere the code can read. It used to be the module
    constant `_DIM = 768`; it is now the column's declaration, so this fake answers
    `%Dictionary.CompiledProperty` and the assertions below keep naming 768 because that
    is what this fake's column says — not because the helper assumes it.
    """
    conn, cursor = MagicMock(), MagicMock()
    default_all = rows if rows is not None else []

    def execute(sql, params=None):
        if "CompiledProperty" in str(sql):
            cursor.fetchall.return_value = [(f"MAXLEN,255,LEN,{declared},DATATYPE,double",)]
        else:
            cursor.fetchall.return_value = default_all
        return None

    cursor.execute.side_effect = execute
    cursor.fetchall.return_value = default_all
    cursor.fetchmany.return_value = []
    cursor.fetchone.return_value = one
    conn.cursor.return_value = cursor
    return conn, cursor


def _statements(cursor):
    return [c[0][0] for c in cursor.execute.call_args_list if c[0]]


def test_hnsw_probe_qualifies_the_table_and_types_the_query_vector():
    conn, cursor = _conn()
    result = VectorOptimizer(conn).check_hnsw_availability()
    sql = "\n".join(_statements(cursor))
    assert OPT in sql
    assert "node_id" in sql
    assert "TO_VECTOR(?, DOUBLE, 768)" in sql
    assert result["available"] is True


def test_hnsw_probe_does_not_select_the_rowid():
    conn, cursor = _conn()
    VectorOptimizer(conn).check_hnsw_availability()
    probe = [s for s in _statements(cursor) if "VECTOR_COSINE" in s][0]
    assert "TOP 5 id," not in probe


def test_benchmark_qualifies_both_tables_and_types_the_query_vector():
    conn, cursor = _conn()
    VectorOptimizer(conn).benchmark_vector_search(test_vectors=[[0.1] * 768], iterations=1)
    sql = "\n".join(_statements(cursor))
    assert OPT in sql
    assert "Graph_KG.kg_NodeEmbeddings " in sql or "Graph_KG.kg_NodeEmbeddings\n" in sql
    assert "TO_VECTOR(?, DOUBLE, 768)" in sql


def test_migration_carries_the_graph_id():
    """`fk_emb_node_opt` is `(graph_id, node_id)`; a copy that drops the graph
    either lands in the wrong graph or is refused as `SQLCODE -121`."""
    conn, cursor = _conn(one=(2,))
    # 768 wide: the helper skips any row whose width is not the declared one.
    cursor.fetchmany.side_effect = [[("g1", "n1", ",".join(["0.1"] * 768))], []]
    result = VectorOptimizer(conn).migrate_to_optimized()
    stmts = _statements(cursor)
    # The migration now reads the target's declared width from the class dictionary first,
    # so pick its own source read rather than "the first SELECT".
    select = [
        s
        for s in stmts
        if "SELECT" in s and "COUNT" not in s and "Graph_KG.kg_NodeEmbeddings" in s
    ][0]
    assert "graph_id" in select and "node_id" in select
    insert = [s for s in stmts if "INSERT" in s]
    assert insert, f"nothing inserted: {result}"
    assert "(graph_id, node_id, emb)" in insert[0]
    assert "TO_VECTOR(?, DOUBLE, 768)" in insert[0]


def test_migration_does_not_create_a_differently_shaped_target():
    """The target's shape belongs to `initialize_schema`.

    This helper used to `CREATE TABLE … (id VARCHAR(256) PRIMARY KEY, emb
    VECTOR(FLOAT, 768))` when the target was missing — a different key and a
    different dtype from the table `schema.py` declares, so every later read
    comparing against a `DOUBLE` vector failed with `SQLCODE -259`.
    """
    conn, cursor = _conn(one=(2,))
    cursor.fetchmany.side_effect = [[], []]
    VectorOptimizer(conn).migrate_to_optimized()
    creates = [s for s in _statements(cursor) if "CREATE TABLE" in s.upper()]
    assert creates == []


def test_statistics_qualifies_the_table():
    conn, cursor = _conn(one=(5,))
    VectorOptimizer(conn).get_vector_statistics()
    assert all(OPT in s for s in _statements(cursor))


def test_an_unknown_table_is_still_refused():
    conn, _ = _conn()
    with pytest.raises(Exception):
        VectorOptimizer(conn).get_vector_statistics("; DROP TABLE nodes --")
