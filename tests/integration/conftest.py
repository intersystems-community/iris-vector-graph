import pytest
import os
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

# NOTE: iris_connection is provided by tests/conftest.py (top-level).
# It manages a dedicated test container via iris-devtester.
# Do NOT define iris_connection here — it would override the managed container
# fixture and connect to a random host IRIS instance (e.g. opsreview-iris on
# port 1972), breaking test isolation and schema assumptions.


@pytest.fixture
def fraud_test_data(iris_connection):
    """Setup fraud dataset test data for cypher tests"""
    cursor = iris_connection.cursor()

    # Clean up existing test data (order: dependents first, then parents)
    try:
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'TXN:%' OR s LIKE 'ACCOUNT:%'")
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'TXN:%' OR s LIKE 'ACCOUNT:%'")
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'TXN:%' OR s LIKE 'ACCOUNT:%'")
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'TXN:%' OR node_id LIKE 'ACCOUNT:%'")
        iris_connection.commit()
    except Exception:
        iris_connection.rollback()

    # Create test nodes (include specific names expected by tests)
    accounts = ["ACCOUNT:MULE1", "ACCOUNT:MULE2", "ACCOUNT:acc_0", "ACCOUNT:acc_1", "ACCOUNT:acc_2"]
    transactions = ["TXN:MULE1_IN1", "TXN:MULE1_OUT1"] + [f"TXN:txn_{i}" for i in range(8)]

    for node_id in accounts + transactions:
        cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [node_id])

    # Create labels
    for acc in accounts:
        cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [acc, "Account"])
    for txn in transactions:
        cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [txn, "Transaction"])

    # Create edges: transactions -> accounts (FROM_ACCOUNT, TO_ACCOUNT)
    for i, txn in enumerate(transactions):
        from_acc = accounts[i % len(accounts)]
        to_acc = accounts[(i + 1) % len(accounts)]
        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [txn, "FROM_ACCOUNT", from_acc])
        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [txn, "TO_ACCOUNT", to_acc])
        # Add amount property to transactions
        cursor.execute("INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)", [txn, "amount", str(100 + i * 50)])

    iris_connection.commit()

    yield iris_connection

    # Cleanup
    try:
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'TXN:%' OR s LIKE 'ACCOUNT:%'")
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s LIKE 'TXN:%' OR s LIKE 'ACCOUNT:%'")
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s LIKE 'TXN:%' OR s LIKE 'ACCOUNT:%'")
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'TXN:%' OR node_id LIKE 'ACCOUNT:%'")
        iris_connection.commit()
    except Exception:
        iris_connection.rollback()


@pytest.fixture
def execute_cypher(fraud_test_data):
    """Helper fixture to parse, translate, and execute Cypher queries"""
    conn = fraud_test_data  # fraud_test_data yields the connection

    def _execute(query, params=None):
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql

        ast = parse_query(query)
        sql_query = translate_to_sql(ast, params=params)

        cursor = conn.cursor()

        if sql_query.is_transactional:
            cursor.execute("START TRANSACTION")
            try:
                stmts = sql_query.sql if isinstance(sql_query.sql, list) else [sql_query.sql]
                all_params = sql_query.parameters
                rows = []
                for i, stmt in enumerate(stmts):
                    p = all_params[i] if i < len(all_params) else []
                    cursor.execute(stmt, p)
                    if cursor.description:
                        rows = cursor.fetchall()
                cursor.execute("COMMIT")

                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                return {"columns": columns, "rows": rows}
            except Exception as e:
                cursor.execute("ROLLBACK")
                raise e
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
