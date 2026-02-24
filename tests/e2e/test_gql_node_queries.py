import pytest
from fastapi.testclient import TestClient
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.gql import create_app

@pytest.mark.requires_database
def test_gql_node_queries(iris_connection):
    """
    Test that the auto-generated GraphQL server can query nodes and properties.
    """
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    engine.initialize_schema()
    
    # 1. Setup test data
    node_id = "GQL:QUERY:NODE1"
    properties = {"name": "Test Node", "priority": "High", "id": "internal_id"}
    engine.create_node(node_id, labels=["Entity"], properties=properties)
    
    try:
        # 2. Create app (introspects graph)
        app = create_app(engine)
        client = TestClient(app)
        
        # 3. Test node lookup by ID
        query_node = """
        query($id: ID!) {
          node(id: $id) {
            id
            labels
            properties {
              key
              value
            }
          }
        }
        """
        response = client.post("/graphql", json={"query": query_node, "variables": {"id": node_id}})
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["node"]["id"] == node_id
        assert "Entity" in data["data"]["node"]["labels"]
        
        # 4. Test label query with auto-generated property fields
        # Note: 'id' property should be prefixed as 'p_id'
        # Strawberry converts p_id to pId by default
        query_nodes = """
        {
          nodes(label: "Entity") {
            id
            ... on Entity {
              name
              priority
              pId
            }
          }
        }
        """
        response = client.post("/graphql", json={"query": query_nodes})
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
        nodes = data["data"]["nodes"]
        assert len(nodes) >= 1
        target = next(n for n in nodes if n["id"] == node_id)
        assert target["name"] == "Test Node"
        assert target["priority"] == "High"
        assert target["pId"] == "internal_id"
        
    finally:
        # Cleanup
        cursor = iris_connection.cursor()
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s = ?", [node_id])
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = ?", [node_id])
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [node_id])
        iris_connection.commit()
        cursor.close()
