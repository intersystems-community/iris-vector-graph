# GraphQL Endpoint Design for IRIS Vector Graph

**Date**: 2025-10-02
**Status**: Design phase
**Timeline**: Can run parallel to Phase 2 (openCypher) or immediately after NodePK
**Integration**: Works alongside SQL, Cypher, REST APIs

---

## Executive Summary

Add a **GraphQL endpoint** to IRIS Vector Graph, providing a modern, flexible query interface that complements SQL and Cypher. GraphQL excels at client-driven queries, reducing over-fetching, and providing strong typing - making it ideal for web/mobile applications querying the knowledge graph.

**Key Differentiator**:
> "The only knowledge graph database offering SQL, Cypher, GraphQL, AND vector search with ACID guarantees"

**Why GraphQL?**
- ✅ **Client-driven queries**: Fetch exactly what you need, nothing more
- ✅ **Strong typing**: Schema introspection, auto-generated documentation
- ✅ **Single endpoint**: No need for multiple REST endpoints
- ✅ **Real-time subscriptions**: Live graph updates via WebSockets
- ✅ **Developer experience**: GraphQL Playground for exploration

**GraphQL vs Cypher vs SQL**:
- **SQL**: Best for raw performance, complex JOINs, bulk operations
- **Cypher**: Best for graph pattern matching, multi-hop traversals
- **GraphQL**: Best for web/mobile clients, nested queries, real-time updates

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Client Layer                                                │
│  - Web UI (GraphQL Playground)                              │
│  - React/Vue apps (Apollo Client)                           │
│  - Mobile apps (GraphQL clients)                            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  GraphQL Layer (NEW)                                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  GraphQL Schema (SDL)                                │  │
│  │  - type Protein { id, name, interactions }          │  │
│  │  - type Query { protein(id: ID!): Protein }         │  │
│  │  - type Mutation { createProtein(...): Protein }    │  │
│  │  - type Subscription { proteinUpdated: Protein }    │  │
│  └──────────────────────────────────────────────────────┘  │
│                          ↓                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  GraphQL Resolvers                                   │  │
│  │  - protein(id) → SQL query to nodes + rdf_labels    │  │
│  │  - interactions() → SQL query to rdf_edges          │  │
│  │  - vectorSimilar() → HNSW vector search             │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Data Access Layer (EXISTING)                               │
│  - SQL queries (SELECT, INSERT, UPDATE)                    │
│  - Vector search (VECTOR_DOT_PRODUCT)                      │
│  - Graph traversal (rdf_edges JOINs)                       │
│  - NodePK validation (FK constraints)                      │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  IRIS Storage Layer (EXISTING)                             │
│  - nodes, rdf_edges, rdf_labels, rdf_props                │
│  - kg_NodeEmbeddings (HNSW index)                          │
└─────────────────────────────────────────────────────────────┘
```

---

## GraphQL Schema Design

### Type Definitions (SDL)

```graphql
# Core node type (maps to nodes table + rdf_labels)
interface Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!
}

# Specific node types (from rdf_labels)
type Protein implements Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!

  # Protein-specific properties (from rdf_props)
  name: String
  function: String
  organism: String
  confidence: Float

  # Relationships (from rdf_edges)
  interactsWith(first: Int = 10): [Protein!]!
  regulatedBy(first: Int = 10): [Gene!]!
  participatesIn(first: Int = 10): [Pathway!]!

  # Vector similarity
  similar(limit: Int = 10, threshold: Float = 0.8): [SimilarProtein!]!

  # Embedding (from kg_NodeEmbeddings)
  embedding: [Float!]
}

type Gene implements Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!

  name: String
  chromosome: String
  position: Int

  encodes: [Protein!]!
  variants: [Variant!]!
}

type Pathway implements Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!

  name: String
  description: String

  proteins: [Protein!]!
  genes: [Gene!]!
}

# Relationship types (from rdf_edges)
type Interaction {
  id: ID!
  source: Node!
  target: Node!
  type: String!
  confidence: Float
  qualifiers: JSON
}

# Vector similarity result
type SimilarProtein {
  protein: Protein!
  similarity: Float!
  distance: Float!
}

# Query root
type Query {
  # Node lookups
  node(id: ID!): Node
  protein(id: ID!): Protein
  gene(id: ID!): Gene
  pathway(id: ID!): Pathway

  # Search by label
  proteins(first: Int = 10, offset: Int = 0): [Protein!]!
  genes(first: Int = 10, offset: Int = 0): [Gene!]!
  pathways(first: Int = 10, offset: Int = 0): [Pathway!]!

  # Search by property
  proteinsByName(name: String!): [Protein!]!

  # Vector search
  similarProteins(
    queryVector: [Float!]!
    limit: Int = 10
    threshold: Float = 0.8
  ): [SimilarProtein!]!

  # Hybrid search (vector + graph)
  proteinNeighborhood(
    id: ID!
    hops: Int = 1
    limit: Int = 20
  ): ProteinNeighborhood!

  # Graph traversal
  shortestPath(
    fromId: ID!
    toId: ID!
    maxHops: Int = 5
    relationshipTypes: [String!]
  ): Path

  # Statistics
  stats: GraphStats!
}

# Mutation root
type Mutation {
  # Create operations
  createProtein(input: CreateProteinInput!): Protein!
  createInteraction(input: CreateInteractionInput!): Interaction!

  # Update operations
  updateProtein(id: ID!, input: UpdateProteinInput!): Protein!

  # Delete operations
  deleteProtein(id: ID!): Boolean!

  # Batch operations
  createProteins(inputs: [CreateProteinInput!]!): [Protein!]!
}

# Subscription root (real-time updates)
type Subscription {
  proteinCreated: Protein!
  proteinUpdated(id: ID): Protein!
  interactionCreated: Interaction!
}

# Input types
input CreateProteinInput {
  id: ID!
  name: String!
  function: String
  organism: String
  embedding: [Float!]
}

input UpdateProteinInput {
  name: String
  function: String
  confidence: Float
}

# Complex result types
type ProteinNeighborhood {
  center: Protein!
  neighbors: [Protein!]!
  interactions: [Interaction!]!
}

type Path {
  nodes: [Node!]!
  edges: [Interaction!]!
  length: Int!
}

type GraphStats {
  totalNodes: Int!
  totalEdges: Int!
  nodesByLabel: [LabelCount!]!
  edgesByType: [TypeCount!]!
}

type LabelCount {
  label: String!
  count: Int!
}

type TypeCount {
  type: String!
  count: Int!
}

# Custom scalars
scalar JSON
scalar DateTime
```

---

## Resolver Implementation

### Technology Stack

**Option 1: Python (Recommended)**
- **Framework**: Strawberry GraphQL (modern, type-safe)
- **Alternative**: Graphene (mature, widely used)
- **IRIS Connection**: iris.connect()

**Option 2: ObjectScript**
- **Framework**: Custom GraphQL parser/executor
- **Integration**: Direct IRIS class methods

**Decision**: Use **Strawberry GraphQL (Python)** for rapid development and strong typing

### Resolver Examples

```python
import strawberry
from typing import List, Optional
import iris

# Type definitions
@strawberry.type
class Protein:
    id: str
    name: Optional[str]
    function: Optional[str]
    confidence: Optional[float]

    @strawberry.field
    def interacts_with(self, first: int = 10) -> List['Protein']:
        """Resolve interactions (1-hop traversal)."""
        cursor = get_iris_cursor()
        cursor.execute("""
            SELECT DISTINCT n2.node_id, p.val AS name
            FROM rdf_edges e
            INNER JOIN nodes n2 ON e.o_id = n2.node_id
            LEFT JOIN rdf_props p ON n2.node_id = p.s AND p.key = 'name'
            WHERE e.s = ? AND e.p = 'INTERACTS_WITH'
            LIMIT ?
        """, [self.id, first])

        return [
            Protein(id=row[0], name=row[1])
            for row in cursor.fetchall()
        ]

    @strawberry.field
    def similar(self, limit: int = 10, threshold: float = 0.8) -> List['SimilarProtein']:
        """Resolve vector similarity search."""
        cursor = get_iris_cursor()

        # Get this protein's embedding
        cursor.execute(
            "SELECT emb FROM kg_NodeEmbeddings WHERE id = ?",
            [self.id]
        )
        my_embedding = cursor.fetchone()[0]

        # Find similar proteins
        cursor.execute("""
            SELECT TOP ?
                e.id,
                VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) AS similarity
            FROM kg_NodeEmbeddings e
            INNER JOIN nodes n ON e.id = n.node_id
            INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
            WHERE e.id != ?
              AND VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) > ?
            ORDER BY similarity DESC
        """, [limit, my_embedding, self.id, my_embedding, threshold])

        return [
            SimilarProtein(
                protein=Protein(id=row[0]),
                similarity=row[1]
            )
            for row in cursor.fetchall()
        ]

@strawberry.type
class SimilarProtein:
    protein: Protein
    similarity: float

# Query resolvers
@strawberry.type
class Query:
    @strawberry.field
    def protein(self, id: str) -> Optional[Protein]:
        """Fetch protein by ID."""
        cursor = get_iris_cursor()
        cursor.execute("""
            SELECT
                n.node_id,
                p_name.val AS name,
                p_func.val AS function,
                p_conf.val AS confidence
            FROM nodes n
            INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
            LEFT JOIN rdf_props p_name ON n.node_id = p_name.s AND p_name.key = 'name'
            LEFT JOIN rdf_props p_func ON n.node_id = p_func.s AND p_func.key = 'function'
            LEFT JOIN rdf_props p_conf ON n.node_id = p_conf.s AND p_conf.key = 'confidence'
            WHERE n.node_id = ?
        """, [id])

        row = cursor.fetchone()
        if not row:
            return None

        return Protein(
            id=row[0],
            name=row[1],
            function=row[2],
            confidence=float(row[3]) if row[3] else None
        )

    @strawberry.field
    def similar_proteins(
        self,
        query_vector: List[float],
        limit: int = 10,
        threshold: float = 0.8
    ) -> List[SimilarProtein]:
        """Vector similarity search."""
        cursor = get_iris_cursor()

        vector_str = '[' + ','.join(map(str, query_vector)) + ']'

        cursor.execute("""
            SELECT TOP ?
                e.id,
                VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) AS similarity
            FROM kg_NodeEmbeddings e
            INNER JOIN nodes n ON e.id = n.node_id
            INNER JOIN rdf_labels l ON n.node_id = l.s AND l.label = 'Protein'
            WHERE VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?, 'DOUBLE', 768)) > ?
            ORDER BY similarity DESC
        """, [limit, vector_str, vector_str, threshold])

        return [
            SimilarProtein(
                protein=Protein(id=row[0]),
                similarity=row[1]
            )
            for row in cursor.fetchall()
        ]

    @strawberry.field
    def protein_neighborhood(
        self,
        id: str,
        hops: int = 1,
        limit: int = 20
    ) -> 'ProteinNeighborhood':
        """Hybrid query: Get protein + neighborhood."""
        cursor = get_iris_cursor()

        # Get center protein
        center = self.protein(id)

        # Get neighbors (n-hop)
        # For now, implement 1-hop; extend with recursive CTE for multi-hop
        cursor.execute("""
            SELECT DISTINCT
                n2.node_id,
                e.p AS interaction_type,
                e.qualifiers
            FROM rdf_edges e
            INNER JOIN nodes n2 ON e.o_id = n2.node_id
            INNER JOIN rdf_labels l ON n2.node_id = l.s AND l.label = 'Protein'
            WHERE e.s = ?
            LIMIT ?
        """, [id, limit])

        neighbors = []
        interactions = []

        for row in cursor.fetchall():
            neighbors.append(Protein(id=row[0]))
            interactions.append(Interaction(
                id=f"{id}-{row[0]}",
                source=center,
                target=Protein(id=row[0]),
                type=row[1],
                qualifiers=row[2]
            ))

        return ProteinNeighborhood(
            center=center,
            neighbors=neighbors,
            interactions=interactions
        )

@strawberry.type
class ProteinNeighborhood:
    center: Protein
    neighbors: List[Protein]
    interactions: List['Interaction']

@strawberry.type
class Interaction:
    id: str
    source: Protein
    target: Protein
    type: str
    qualifiers: Optional[str]

# Mutation resolvers
@strawberry.type
class Mutation:
    @strawberry.mutation
    def create_protein(self, input: 'CreateProteinInput') -> Protein:
        """Create new protein with FK validation."""
        cursor = get_iris_cursor()
        conn = get_iris_connection()

        # Insert into nodes table (NodePK)
        cursor.execute(
            "INSERT INTO nodes (node_id) VALUES (?)",
            [input.id]
        )

        # Insert label
        cursor.execute(
            "INSERT INTO rdf_labels (s, label) VALUES (?, 'Protein')",
            [input.id]
        )

        # Insert properties
        if input.name:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, 'name', ?)",
                [input.id, input.name]
            )
        if input.function:
            cursor.execute(
                "INSERT INTO rdf_props (s, key, val) VALUES (?, 'function', ?)",
                [input.id, input.function]
            )

        # Insert embedding if provided
        if input.embedding:
            vector_str = '[' + ','.join(map(str, input.embedding)) + ']'
            cursor.execute(
                "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, 'DOUBLE', 768))",
                [input.id, vector_str]
            )

        conn.commit()

        return Protein(
            id=input.id,
            name=input.name,
            function=input.function
        )

@strawberry.input
class CreateProteinInput:
    id: str
    name: str
    function: Optional[str] = None
    organism: Optional[str] = None
    embedding: Optional[List[float]] = None

# Schema
schema = strawberry.Schema(query=Query, mutation=Mutation)
```

---

## REST API Endpoint

### FastAPI Integration

```python
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

app = FastAPI()

# GraphQL endpoint
graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")

# GraphQL Playground (interactive UI)
@app.get("/")
def graphql_playground():
    return """
    <!DOCTYPE html>
    <html>
      <head>
        <title>IRIS Vector Graph - GraphQL Playground</title>
      </head>
      <body>
        <div id="root"></div>
        <script src="https://unpkg.com/graphql-playground-react/build/static/js/middleware.js"></script>
      </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

**Access**:
- GraphQL endpoint: `http://localhost:8000/graphql`
- Playground UI: `http://localhost:8000/`

---

## Example GraphQL Queries

### Query 1: Simple Protein Lookup

```graphql
query GetProtein {
  protein(id: "PROTEIN:TP53") {
    id
    name
    function
    confidence
  }
}
```

**Response**:
```json
{
  "data": {
    "protein": {
      "id": "PROTEIN:TP53",
      "name": "p53",
      "function": "tumor suppressor",
      "confidence": 0.95
    }
  }
}
```

### Query 2: Protein with Interactions (Nested)

```graphql
query ProteinWithInteractions {
  protein(id: "PROTEIN:TP53") {
    id
    name
    interactsWith(first: 5) {
      id
      name
      function
    }
  }
}
```

**Response**:
```json
{
  "data": {
    "protein": {
      "id": "PROTEIN:TP53",
      "name": "p53",
      "interactsWith": [
        {
          "id": "PROTEIN:MDM2",
          "name": "MDM2",
          "function": "E3 ubiquitin ligase"
        },
        {
          "id": "PROTEIN:BAX",
          "name": "BAX",
          "function": "apoptosis regulator"
        }
      ]
    }
  }
}
```

### Query 3: Vector Similarity Search

```graphql
query SimilarProteins {
  similarProteins(
    queryVector: [0.1, 0.2, ..., 0.9]  # 768-dim vector
    limit: 10
    threshold: 0.8
  ) {
    protein {
      id
      name
    }
    similarity
  }
}
```

### Query 4: Hybrid Query (Vector + Graph)

```graphql
query ProteinNeighborhood {
  proteinNeighborhood(id: "PROTEIN:TP53", hops: 2, limit: 20) {
    center {
      id
      name
    }
    neighbors {
      id
      name
      function
    }
    interactions {
      type
      qualifiers
    }
  }
}
```

### Mutation 1: Create Protein

```graphql
mutation CreateProtein {
  createProtein(input: {
    id: "PROTEIN:NEW123"
    name: "Novel Protein"
    function: "Unknown"
    embedding: [0.1, 0.2, ..., 0.9]
  }) {
    id
    name
    function
  }
}
```

---

## Implementation Phases

### Phase 1: Core GraphQL Setup (1-2 months)

**Deliverables**:
- [ ] Strawberry GraphQL setup with FastAPI
- [ ] Basic schema (Protein, Gene, Pathway types)
- [ ] Simple resolvers (node lookup by ID)
- [ ] GraphQL Playground deployed
- [ ] IRIS connection pooling

**Timeline**: 1-2 months
**Dependencies**: NodePK complete (FK constraints)

### Phase 2: Graph Resolvers (1-2 months)

**Deliverables**:
- [ ] Relationship resolvers (interactsWith, regulatedBy, etc.)
- [ ] Multi-hop traversal resolvers
- [ ] Pagination support (first, offset)
- [ ] Filter arguments (where clauses)

**Timeline**: 1-2 months
**Dependencies**: Phase 1

### Phase 3: Vector Search Integration (1 month)

**Deliverables**:
- [ ] similarProteins resolver (vector k-NN)
- [ ] Hybrid query resolvers (vector + graph)
- [ ] Vector similarity threshold filtering
- [ ] Performance optimization (HNSW index usage)

**Timeline**: 1 month
**Dependencies**: Phase 2

### Phase 4: Mutations & Subscriptions (2 months)

**Deliverables**:
- [ ] Create/Update/Delete mutations
- [ ] Batch mutations
- [ ] Real-time subscriptions (WebSocket)
- [ ] FK constraint validation in mutations

**Timeline**: 2 months
**Dependencies**: Phase 3

### Phase 5: Advanced Features (1-2 months)

**Deliverables**:
- [ ] DataLoader for batching (N+1 query prevention)
- [ ] Query complexity analysis
- [ ] Rate limiting
- [ ] Authentication/authorization
- [ ] Schema versioning

**Timeline**: 1-2 months
**Dependencies**: Phase 4

---

## Performance Considerations

### N+1 Query Problem

**Problem**: Nested GraphQL queries can trigger many SQL queries

**Example**:
```graphql
query {
  proteins(first: 100) {
    id
    name
    interactsWith {  # Triggers 100 separate SQL queries!
      name
    }
  }
}
```

**Solution**: Use DataLoader for batching

```python
from strawberry.dataloader import DataLoader

async def load_interactions(protein_ids: List[str]) -> List[List[Protein]]:
    """Batch load interactions for multiple proteins."""
    cursor = get_iris_cursor()

    # Single query for all proteins
    cursor.execute("""
        SELECT e.s, n2.node_id, p.val AS name
        FROM rdf_edges e
        INNER JOIN nodes n2 ON e.o_id = n2.node_id
        LEFT JOIN rdf_props p ON n2.node_id = p.s AND p.key = 'name'
        WHERE e.s IN (?)
          AND e.p = 'INTERACTS_WITH'
    """, [','.join(protein_ids)])

    # Group by source protein
    interactions_by_protein = {}
    for row in cursor.fetchall():
        source_id = row[0]
        if source_id not in interactions_by_protein:
            interactions_by_protein[source_id] = []
        interactions_by_protein[source_id].append(Protein(id=row[1], name=row[2]))

    # Return in same order as protein_ids
    return [interactions_by_protein.get(pid, []) for pid in protein_ids]

# Use in resolver
interaction_loader = DataLoader(load_fn=load_interactions)

@strawberry.field
async def interacts_with(self) -> List[Protein]:
    return await interaction_loader.load(self.id)
```

### Query Complexity Limits

Prevent expensive queries:

```python
from strawberry.extensions import QueryDepthLimiter

schema = strawberry.Schema(
    query=Query,
    extensions=[
        QueryDepthLimiter(max_depth=5),  # Max 5 levels of nesting
    ]
)
```

---

## Testing Strategy

### Unit Tests: Resolvers

```python
def test_protein_resolver():
    """Test protein lookup resolver."""
    query = Query()
    protein = query.protein(id="PROTEIN:TP53")

    assert protein is not None
    assert protein.id == "PROTEIN:TP53"
    assert protein.name == "p53"
```

### Integration Tests: GraphQL Queries

```python
from strawberry.test import BaseGraphQLTestClient

def test_protein_query():
    """Test GraphQL protein query."""
    client = BaseGraphQLTestClient(schema)

    result = client.query("""
        query {
          protein(id: "PROTEIN:TP53") {
            id
            name
            function
          }
        }
    """)

    assert result.errors is None
    assert result.data["protein"]["name"] == "p53"
```

### Performance Tests: N+1 Prevention

```python
def test_no_n_plus_1_queries():
    """Ensure DataLoader prevents N+1 queries."""
    with query_counter() as counter:
        client.query("""
            query {
              proteins(first: 100) {
                id
                interactsWith {
                  id
                }
              }
            }
        """)

    # Should execute 2 queries: 1 for proteins, 1 for all interactions
    assert counter.count <= 2, f"N+1 detected: {counter.count} queries"
```

---

## Comparison: GraphQL vs Cypher vs REST

| Feature | GraphQL | Cypher | REST |
|---------|---------|--------|------|
| **Client-driven queries** | ✅ Yes | ❌ No | ❌ No |
| **Single endpoint** | ✅ Yes | ✅ Yes | ❌ No (many endpoints) |
| **Strong typing** | ✅ Yes | ⚠️ Partial | ❌ No |
| **Nested queries** | ✅ Excellent | ✅ Good | ❌ Poor (over-fetching) |
| **Real-time updates** | ✅ Subscriptions | ❌ No | ⚠️ SSE/WebSocket |
| **Graph traversal** | ⚠️ Via resolvers | ✅ Native | ❌ Multiple requests |
| **Vector search** | ✅ Custom resolvers | ✅ Custom procedures | ✅ Endpoint |
| **Learning curve** | Medium | Medium-High | Low |
| **Best for** | Web/mobile apps | Graph analytics | Simple CRUD |

---

## Integration with Cypher and SQL

### GraphQL → SQL (Behind the Scenes)

GraphQL queries ultimately translate to SQL:

```graphql
query {
  protein(id: "PROTEIN:TP53") {
    name
    interactsWith {
      name
    }
  }
}
```

Executes SQL:
```sql
-- Resolver 1: Get protein
SELECT n.node_id, p.val AS name
FROM nodes n
LEFT JOIN rdf_props p ON n.node_id = p.s AND p.key = 'name'
WHERE n.node_id = 'PROTEIN:TP53'

-- Resolver 2: Get interactions
SELECT n2.node_id, p2.val AS name
FROM rdf_edges e
INNER JOIN nodes n2 ON e.o_id = n2.node_id
LEFT JOIN rdf_props p2 ON n2.node_id = p2.s AND p2.key = 'name'
WHERE e.s = 'PROTEIN:TP53' AND e.p = 'INTERACTS_WITH'
```

### GraphQL + Cypher Hybrid

Expose Cypher queries via GraphQL:

```graphql
type Query {
  executeCypher(query: String!, params: JSON): [JSON!]!
}
```

```graphql
query {
  executeCypher(
    query: "MATCH (p:Protein)-[:INTERACTS_WITH]->(target) WHERE p.id = $id RETURN target"
    params: {id: "PROTEIN:TP53"}
  )
}
```

---

## Deployment Options

### Option 1: FastAPI + Strawberry (Python)

**Pros**:
- Modern, type-safe
- Excellent developer experience
- Great GraphQL Playground

**Cons**:
- Separate process from IRIS
- Network latency

**Deployment**:
```bash
# Docker container
docker run -p 8000:8000 iris-graphql-api

# Access
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ protein(id: \"PROTEIN:TP53\") { name } }"}'
```

### Option 2: ObjectScript + Custom GraphQL

**Pros**:
- In-process (no network overhead)
- Native IRIS integration

**Cons**:
- More implementation work
- Limited GraphQL tooling

**Implementation**: Custom GraphQL parser in ObjectScript

---

## Timeline Summary

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| **Phase 1**: Core Setup | 1-2 months | Basic schema, resolvers, Playground |
| **Phase 2**: Graph Resolvers | 1-2 months | Relationship traversal, pagination |
| **Phase 3**: Vector Search | 1 month | Vector k-NN, hybrid queries |
| **Phase 4**: Mutations | 2 months | CRUD operations, subscriptions |
| **Phase 5**: Advanced | 1-2 months | DataLoader, auth, optimization |
| **Total** | **6-9 months** | Production-ready GraphQL API |

**Can run in parallel with Cypher Phase 2** (different codebases)

---

## Success Criteria

- [ ] 100+ GraphQL queries tested and passing
- [ ] N+1 queries eliminated (DataLoader)
- [ ] Query performance within 10% of direct SQL
- [ ] GraphQL Playground deployed and accessible
- [ ] Python client library published
- [ ] Documentation complete
- [ ] Real-time subscriptions working
- [ ] Authentication/authorization implemented

---

## Summary

**GraphQL complements SQL and Cypher**:
- **SQL**: Raw performance, complex analytics
- **Cypher**: Graph pattern matching, traversals
- **GraphQL**: Client-driven queries, web/mobile apps

**Unique Positioning**:
> "The only knowledge graph offering SQL + Cypher + GraphQL + Vector search with ACID guarantees"

**Timeline**: 6-9 months to production
**Investment**: ~1.5 FTE-years
**ROI**: Modern API for web/mobile developers, reduced over-fetching, strong typing

Ready to implement! 🚀
