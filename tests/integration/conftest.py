import pytest
import os
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

@pytest.fixture(scope="session")
def iris_connection():
    """Establish connection to IRIS for integration tests using iris-devtester"""
    from iris_devtester.utils.dbapi_compat import get_connection
    
    host = os.getenv("IRIS_HOST", "localhost")
    port = int(os.getenv("IRIS_PORT", 1972))
    namespace = os.getenv("IRIS_NAMESPACE", "USER")
    username = os.getenv("IRIS_USERNAME", "_SYSTEM")
    password = os.getenv("IRIS_PASSWORD", "SYS")
    
    conn = get_connection(host, port, namespace, username, password)
    yield conn
    conn.close()

@pytest.fixture
def execute_cypher(iris_connection):
    """Helper fixture to parse, translate, and execute Cypher queries"""
    def _execute(query, params=None):
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql
        
        ast = parse_query(query)
        sql_query = translate_to_sql(ast, params=params)
        
        cursor = iris_connection.cursor()
        
        if sql_query.is_transactional:
            cursor.execute("START TRANSACTION")
            try:
                stmts = sql_query.sql if isinstance(sql_query.sql, list) else [sql_query.sql]
                all_params = sql_query.parameters
                rows = []
                for i, stmt in enumerate(stmts):
                    p = all_params[i] if i < len(all_params) else []
                    print(f"DEBUG SQL: {stmt} with {p}")
                    cursor.execute(stmt, p)
                    if cursor.description:
                        rows = cursor.fetchall()
                cursor.execute("COMMIT")
                
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                return {"columns": columns, "rows": rows}
            except Exception as e:
                cursor.execute("ROLLBACK")
                print(f"DEBUG SQL ERROR: {e}")
                raise e
        else:
            sql_str = sql_query.sql if isinstance(sql_query.sql, str) else "\n".join(sql_query.sql)
            p = sql_query.parameters[0] if sql_query.parameters else []
            print(f"DEBUG SQL: {sql_str} with {p}")
            cursor.execute(sql_str, p)
            
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            
            return {
                "columns": columns,
                "rows": rows,
                "sql": sql_str,
                "params": p
            }

    return _execute
