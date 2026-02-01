import pytest
import os
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

@pytest.fixture(scope="session")
def iris_connection():
    """Establish connection to IRIS for integration tests"""
    import irisnative
    
    host = os.getenv("IRIS_HOST", "localhost")
    port = int(os.getenv("IRIS_PORT", 1972))
    namespace = os.getenv("IRIS_NAMESPACE", "USER")
    username = os.getenv("IRIS_USERNAME", "_SYSTEM")
    password = os.getenv("IRIS_PASSWORD", "SYS")
    
    try:
        # Use irisnative directly to avoid shadowing issues in idt helper
        conn = irisnative.createConnection(host, port, namespace, username, password)
        
        # T013: Initialize schema and ensure Graph_KG is used
        cursor = conn.cursor()
        from iris_vector_graph.schema import GraphSchema
        from iris_vector_graph.utils import _split_sql_statements
        
        # Create schema if not exists
        try:
            cursor.execute("CREATE SCHEMA Graph_KG")
        except: pass
        
        # Deploy base schema
        sql = GraphSchema.get_base_schema_sql()
        statements = _split_sql_statements(sql)
        for stmt in statements:
            if not stmt.strip(): continue
            try:
                cursor.execute(stmt)
            except Exception as e:
                if "already exists" not in str(e).lower() and "already has a" not in str(e).lower():
                    print(f"Schema setup warning: {e}")
                    print(f"Statement: {stmt[:100]}...")

        
        # Ensure Graph_KG schema is used for the session
        try:
            cursor.execute("SET OPTION DEFAULT_SCHEMA = Graph_KG")
        except:
            try:
                cursor.execute("SET SCHEMA Graph_KG")
            except:
                pass
            
        yield conn
        conn.close()
    except Exception as e:
        # Fallback to idt if native fails
        from iris_devtester.utils.dbapi_compat import get_connection
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
