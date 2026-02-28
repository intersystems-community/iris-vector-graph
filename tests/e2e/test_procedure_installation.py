import json
import math
import pytest
from iris_vector_graph import IRISGraphEngine

@pytest.mark.requires_database
def test_initialize_schema_installs_procedures(iris_connection):
    """
    Test that initialize_schema() correctly installs the stored procedures.
    """
    engine = IRISGraphEngine(iris_connection, embedding_dimension=768)

    # 1. Drop existing procedure if any to ensure we are testing installation
    cursor = iris_connection.cursor()
    try:
        cursor.execute("DROP PROCEDURE Graph_KG.kg_KNN_VEC")
    except Exception:
        pass
    iris_connection.commit()

    # 2. Run initialization
    engine.initialize_schema()

    # 3. Verify procedure exists via INFORMATION_SCHEMA
    cursor.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES "
        "WHERE ROUTINE_SCHEMA = 'Graph_KG' AND ROUTINE_NAME = 'kg_KNN_VEC'"
    )
    row = cursor.fetchone()
    assert row and row[0] >= 1, "Stored procedure kg_KNN_VEC was not installed"

    # 4. Verify it can be called (empty table → empty results)
    query_vec = json.dumps([math.sin(i * 0.01) for i in range(768)])
    results = engine.kg_KNN_VEC(query_vec, k=5)
    assert isinstance(results, list), "kg_KNN_VEC must return a list"

    cursor.close()
