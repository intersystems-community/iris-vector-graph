import pytest
from fastapi.testclient import TestClient
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.gql import create_app

@pytest.mark.requires_database
def test_gql_cypher_passthrough(iris_connection):
    """
    Test raw Cypher passthrough via GraphQL.
    """
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    engine.initialize_schema()
    
    # 1. Setup test data
    node_id = "GQL:CYPHER:NODE1"
    engine.create_node(node_id, labels=["Entity"], properties={"name": "Cypher Target"})
    
    try:
        # 2. Create app
        app = create_app(engine)
        client = TestClient(app)
        
        # 3. Test Cypher query
        query = """
        query($cypher: String!) {
          cypher(query: $cypher) {
            columns
            rows
          }
        }
        """
        cypher_str = "MATCH (n {name: 'Cypher Target'}) RETURN n.id as id, n.name as name"
        response = client.post("/graphql", json={"query": query, "variables": {"cypher": cypher_str}})
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
        
        result = data["data"]["cypher"]
        assert "id" in result["columns"]
        assert "name" in result["columns"]
        
        # result["rows"] is a list of lists (serialized values)
        # matching: [[node_id, 'Cypher Target']]
        found = False
        for row in result["rows"]:
            if node_id in row:
                found = True
                assert "Cypher Target" in row
        assert found
        
    finally:
        # Cleanup
        cursor = iris_connection.cursor()
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s = ?", [node_id])
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = ?", [node_id])
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [node_id])
        iris_connection.commit()
        cursor.close()
