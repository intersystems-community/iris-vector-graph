import pytest
import os
import uuid
from iris_vector_graph.constants import VECTOR_TABLE_NAMES
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.engine import IRISGraphEngine
from tests.conftest import SESSION_EMBEDDING_DIM


def pytest_collection_modifyitems(items):
    """Run test_lazykg_fallback_forced tests last.

    These tests kill ^NKG which can contaminate the session-scoped iris_connection
    for subsequent tests that rely on NKG fast-path. Running them last avoids
    ordering-dependent failures.
    """
    forced = [i for i in items if "lazykg_fallback_forced" in str(getattr(i, "fspath", ""))]
    other = [i for i in items if "lazykg_fallback_forced" not in str(getattr(i, "fspath", ""))]
    items[:] = other + forced


def _cleanup_prefix(engine, prefix: str) -> None:
    cursor = engine.conn.cursor()
    pattern = f"{prefix}%"
    cursor.execute(f"DELETE FROM {engine._table('rdf_reifications')} WHERE edge_id IN (SELECT edge_id FROM {engine._table('rdf_edges')} WHERE s LIKE ? OR o_id LIKE ?)", [pattern, pattern])
    cursor.execute(f"DELETE FROM {engine._table('rdf_edges')} WHERE s LIKE ? OR o_id LIKE ?", [pattern, pattern])
    cursor.execute(f"DELETE FROM {engine._table('rdf_props')} WHERE s LIKE ?", [pattern])
    cursor.execute(f"DELETE FROM {engine._table('rdf_labels')} WHERE s LIKE ?", [pattern])
    cursor.execute(f"DELETE FROM {engine._table('nodes')} WHERE node_id LIKE ?", [pattern])
    engine.conn.commit()
    cursor.close()


def _clear_embedding_tables(conn) -> None:
    """Empty every embedding table, legacy and routed.

    `initialize_schema` can only ALTER a VECTOR column's width while the table is
    empty, so rows a previous test left behind make the next fixture's ALTER fail
    silently and the column keeps the old width. The failure then lands on whichever
    test writes a vector next — never on the one that polluted. Clearing on the way
    in as well as on the way out means one test's leftovers cannot choose the next
    test's width.
    """
    cursor = conn.cursor()
    tables = [f"Graph_KG.{name}" for name in VECTOR_TABLE_NAMES]
    try:
        cursor.execute("SELECT table_name FROM Graph_KG.embedding_registry")
        tables += [f"Graph_KG.{row[0]}" for row in cursor.fetchall() if row and row[0]]
    except Exception:
        pass  # no registry: a 3.2.0-or-earlier database, or nothing initialized yet
    for table in dict.fromkeys(tables):
        try:
            cursor.execute(f"DELETE FROM {table}")
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        pass
    cursor.close()


@pytest.fixture
def engine(iris_connection):
    """Create and initialize an IRISGraphEngine for testing."""
    _clear_embedding_tables(iris_connection)
    engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
    engine.initialize_schema(auto_deploy_objectscript=True)
    yield engine
    # Put the session width back so the next vector test finds the table it expects.
    _clear_embedding_tables(iris_connection)
    try:
        IRISGraphEngine(
            iris_connection, embedding_dimension=SESSION_EMBEDDING_DIM
        ).initialize_schema(
            auto_deploy_objectscript=False
        )
    except Exception:
        pass


@pytest.fixture
def fraud_test_data(iris_connection):
    cursor = iris_connection.cursor()
    pfx = f"CF_{uuid.uuid4().hex[:6]}"

    accounts = [f"{pfx}:ACCOUNT:MULE1", f"{pfx}:ACCOUNT:MULE2"] + [f"{pfx}:ACCOUNT:acc_{i}" for i in range(3)]
    transactions = [f"{pfx}:TXN:MULE1_IN1", f"{pfx}:TXN:MULE1_OUT1"] + [f"{pfx}:TXN:txn_{i}" for i in range(8)]

    for node_id in accounts + transactions:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [node_id])
        except Exception:
            pass

    for acc in accounts:
        cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [acc, "Account"])
    for txn in transactions:
        cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [txn, "Transaction"])

    for i, txn in enumerate(transactions):
        from_acc = accounts[i % len(accounts)]
        to_acc = accounts[(i + 1) % len(accounts)]
        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [txn, "FROM_ACCOUNT", from_acc])
        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [txn, "TO_ACCOUNT", to_acc])
        cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, ?, ?)", [txn, "amount", str(100 + i * 50)])
        cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, ?, ?)", [txn, "risk_score", str(0.01 + i * 0.02)])

    for acc in accounts:
        cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, ?, ?)", [acc, "risk_score", "0.05"])
        cursor.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES (?, ?, ?)", [acc, "name", acc.split(":")[-1]])

    iris_connection.commit()

    yield {"conn": iris_connection, "prefix": pfx, "accounts": accounts, "transactions": transactions}

    p = f"{pfx}%"
    try:
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE ?", [p])
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE ?", [p])
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE ?", [p])
        iris_connection.commit()
    except Exception:
        iris_connection.rollback()


@pytest.fixture
def execute_cypher(fraud_test_data):
    conn = fraud_test_data["conn"]
    from iris_vector_graph.engine import IRISGraphEngine

    # An engine, even though this fixture runs the SQL itself. `translate_to_sql` reads
    # `engine._fetch_first_unsafe` to decide between `TOP n` and `FETCH FIRST n ROWS ONLY`,
    # and on the IRIS AI builds this suite targets the second form SIGSEGVs in %qaqpre over
    # a multi-table JOIN on VARCHAR keys. That is a native fault, so translating without an
    # engine did not fail a test — it killed the pytest process and every test after it.
    engine = IRISGraphEngine(conn)

    def _execute(query, params=None):
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql

        ast = parse_query(query)
        sql_query = translate_to_sql(ast, params=params, engine=engine)

        cursor = conn.cursor()

        if sql_query.is_transactional:
            # The translator's mutation output is not plain SQL: it carries
            # `__capture_ids__` and `__constraint_check_delete_connected__` sentinels
            # and `__IDS_k__`/`__GRAPH_k__` tokens the executor fills in. Handing it
            # to IRIS answers `SQLCODE -51 SQL statement expected`, so run it through
            # the executor the engine uses rather than a copy of it.
            from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

            stmts = sql_query.sql if isinstance(sql_query.sql, list) else [sql_query.sql]
            res = IRISGraphStore(conn).execute_transaction(stmts, list(sql_query.parameters))
            return {"columns": list(res.columns), "rows": res.rows}
        else:
            sql_str = sql_query.sql if isinstance(sql_query.sql, str) else "\n".join(sql_query.sql)
            p = sql_query.parameters[0] if sql_query.parameters else []
            cursor.execute(sql_str, p)

            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

            return {
                "columns": columns,
                "rows": rows,
                "sql": sql_str,
                "params": p,
            }

    return _execute
