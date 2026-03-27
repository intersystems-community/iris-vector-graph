# Quickstart: Auto-Generating GraphQL Layer

Start a GraphQL server over your IRIS graph store in seconds.

## Usage

```python
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph import gql

# 1. Initialize your engine
engine = IRISGraphEngine(
    hostname="localhost",
    port=1972,
    namespace="USER",
    username="test",
    password="test"
)

# 2. Start the GraphQL server
# This will introspect the graph and start a FastAPI server
gql.serve(engine, host="0.0.0.0", port=8000)
```

## Exploring the Schema

Once the server is running, visit `http://localhost:8000/graphql` to open the interactive **GraphiQL** explorer.

### Example Queries

#### 1. Basic Node Lookup
```graphql
query {
  node(id: "PROTEIN:TP53") {
    id
    labels
    properties {
      key
      value
    }
  }
}
```

#### 2. Label-specific Query with Properties
If you have nodes labeled `Protein`, the auto-generator will expose top-level fields for discovered properties:

```graphql
query {
  nodes(label: "Protein", limit: 5) {
    id
    p_name  # Auto-generated property field
    p_function
  }
}
```

#### 3. Semantic Search
```graphql
query {
  semanticSearch(query: "diabetes medication", label: "Drug") {
    score
    node {
      id
      p_name
    }
  }
}
```

#### 4. Bi-directional Traversal
```graphql
query {
  node(id: "PROTEIN:TP53") {
    outgoing(predicate: "INTERACTS_WITH") {
      targetId
      node {
        p_name
      }
    }
    incoming(predicate: "REGULATED_BY") {
      targetId
      node {
        p_name
      }
    }
  }
}
```
