import pytest
from fastapi.testclient import TestClient
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.gql import create_app

@pytest.mark.requires_database
def test_gql_autogen_startup(iris_connection, iris_master_cleanup):
    """
    Test that the auto-generated GraphQL server starts and discovers labels.
    """
    # 1. Setup test data (ensure at least one label exists)
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    engine.initialize_schema()
    engine.create_node("GQL:STARTUP:NODE1", labels=["TestLabel"])
    
    try:
        # 2. Create app
        app = create_app(engine)
        client = TestClient(app)
        
        # 3. Test introspection
        query = """
        {
          __schema {
            types {
              name
            }
          }
        }
        """
        response = client.post("/graphql", json={"query": query})
        assert response.status_code == 200
        data = response.json()
        
        # Verify TestLabel was discovered (IRIS may uppercase label values)
        types = [t["name"] for t in data["data"]["__schema"]["types"]]
        types_upper = [t.upper() for t in types]
        assert "TESTLABEL" in types_upper
        
    finally:
        # Cleanup
        cursor = iris_connection.cursor()
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = 'GQL:STARTUP:NODE1'")
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = 'GQL:STARTUP:NODE1'")
        iris_connection.commit()
        cursor.close()
