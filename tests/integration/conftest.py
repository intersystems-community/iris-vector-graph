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
        ast = parse_query(query, params)
        sql_query = translate_to_sql(ast)
        
        cursor = iris_connection.cursor()
        cursor.execute(sql_query.sql, sql_query.parameters)
        
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        
        return {
            "columns": columns,
            "rows": rows,
            "sql": sql_query.sql,
            "params": sql_query.parameters
        }
    return _execute
