import pytest
import os
import uuid
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


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
