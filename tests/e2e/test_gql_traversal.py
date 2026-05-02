import pytest
strawberry = pytest.importorskip("strawberry", reason="strawberry not installed")
from fastapi.testclient import TestClient
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.gql import create_app

@pytest.mark.requires_database
def test_gql_traversal(iris_connection, iris_master_cleanup):
    """
    Test bi-directional relationship traversal via GraphQL.
    """
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    engine.initialize_schema()
    
    # 1. Setup test data
    node1 = "GQL:TRAV:NODE1"
    node2 = "GQL:TRAV:NODE2"
    engine.create_node(node1, labels=["Person"], properties={"name": "Alice"})
    engine.create_node(node2, labels=["Person"], properties={"name": "Bob"})
    engine.create_edge(node1, "KNOWS", node2)
    
    try:
        # 2. Create app
        app = create_app(engine)
        client = TestClient(app)
        
        # 3. Test outgoing traversal (Alice -> Bob)
        query_out = """
        query($id: ID!) {
          node(id: $id) {
            id
            outgoing(predicate: "KNOWS") {
              predicate
              targetId
              node {
                id
                ... on Person {
                  name
                }
              }
            }
          }
        }
        """
        response = client.post("/graphql", json={"query": query_out, "variables": {"id": node1}})
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
        
        rel = data["data"]["node"]["outgoing"][0]
        assert rel["targetId"] == node2
        assert rel["node"]["name"] == "Bob"
        
        # 4. Test incoming traversal (Bob <- Alice)
        query_in = """
        query($id: ID!) {
          node(id: $id) {
            id
            incoming(predicate: "KNOWS") {
              predicate
              targetId
              node {
                id
                ... on Person {
                  name
                }
              }
            }
          }
        }
        """
        response = client.post("/graphql", json={"query": query_in, "variables": {"id": node2}})
        assert response.status_code == 200
        data = response.json()
        assert "errors" not in data, f"GraphQL errors: {data.get('errors')}"
        
        rel_in = data["data"]["node"]["incoming"][0]
        assert rel_in["targetId"] == node1
        assert rel_in["node"]["name"] == "Alice"
        
    finally:
        # Cleanup
        cursor = iris_connection.cursor()
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?", [node1, node1])
        cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s = ? OR o_id = ?", [node2, node2])
        cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s IN (?, ?)", [node1, node2])
        cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s IN (?, ?)", [node1, node2])
        cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id IN (?, ?)", [node1, node2])
        iris_connection.commit()
        cursor.close()
