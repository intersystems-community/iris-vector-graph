import pytest
from iris_vector_graph import IRISGraphEngine

@pytest.mark.requires_database
def test_initialize_schema_installs_procedures(iris_connection):
    """
    Test that initialize_schema() correctly installs the stored procedures.
    """
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    
    # 1. Drop existing procedure if any to ensure we are testing installation
    cursor = iris_connection.cursor()
    try:
        cursor.execute("DROP PROCEDURE iris_vector_graph.kg_KNN_VEC")
    except Exception:
        pass
    iris_connection.commit()
    
    # 2. Run initialization
    engine.initialize_schema()
    
    # 3. Verify procedure exists by attempting to call it (with empty vector)
    # If it doesn't exist, this will raise an exception in the SQL engine
    try:
        # Signature: queryInput, k, labelFilter, embeddingConfig
        cursor.execute("CALL iris_vector_graph.kg_KNN_VEC(?, ?, ?, ?)", ["[]", 1, "", ""])
        cursor.fetchall()
        # If we reached here, the procedure exists
    except Exception as e:
        pytest.fail(f"Stored procedure kg_KNN_VEC was not installed: {e}")
    finally:
        cursor.close()
