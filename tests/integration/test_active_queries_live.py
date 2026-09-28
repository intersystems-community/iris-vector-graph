"""Live: `list_active_queries` returns rows instead of crashing the process.

The old statement named two columns `%SYS.ProcessQuery` does not have and ended
`FETCH FIRST n ROWS ONLY`; IRIS 2026.3 SIGSEGVs the driver on that Prepare error.
See `tests/unit/test_active_queries_sql.py`.
"""

from iris_vector_graph.engine import IRISGraphEngine


def test_lists_its_own_statement(iris_connection):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    rows = eng.list_active_queries(limit=50)
    # The listing statement is itself running while it reads the view.
    assert any("CURRENT_STATEMENTS" in r["command"].upper() for r in rows), rows
    assert all(r["id"].isdigit() for r in rows)
