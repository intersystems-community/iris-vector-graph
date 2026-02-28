import pytest
import json
from fastapi.testclient import TestClient
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.gql import create_app

@pytest.mark.requires_database
def test_gql_semantic_search(iris_connection, iris_master_cleanup):
    """
    Test semantic search via GraphQL.
    """
    # Use 768 dimension to match the table created by conftest
    dim = 768
    engine = IRISGraphEngine(iris_connection, embedding_dimension=dim)
    engine.initialize_schema()
    
    # 1. Setup test data
    node_id = "GQL:SEARCH:NODE1"
    engine.create_node(node_id, labels=["Searchable"], properties={"name": "Search Target"})
    
    # Create valid vector of correct dimension
    vec = [0.0] * dim
    vec[0] = 0.1
    vec[1] = 0.2
    vec[2] = 0.3
    engine.store_embedding(node_id, vec)
    
    try:
        # 2. Create app with a mock embedder
        def mock_embedder(text):
            return vec
            
        app = create_app(engine, embedder=mock_embedder)
        client = TestClient(app)
        
        # 3. Test semantic search with raw text
        query_text = "diabetes medication"
        query = """
        query($query: String!) {
          semanticSearch(query: $query, label: "Searchable") {
            score
            node {
              id
              ... on Searchable {
                name
              }
            }
          }
        }
        """
        response = client.post("/graphql", json={"query": query, "variables": {"query": query_text}})
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
        
        results = data["data"]["semanticSearch"]
        assert len(results) >= 1
        assert results[0]["node"]["id"] == node_id
        assert results[0]["score"] > 0.9
        
    finally:
        # Cleanup
        cursor = iris_connection.cursor()
        cursor.execute("DELETE FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?", [node_id])
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = ?", [node_id])
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = ?", [node_id])
        iris_connection.commit()
        cursor.close()
