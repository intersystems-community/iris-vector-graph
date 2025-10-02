# Phase 0 Research: GraphQL API Endpoint

**Feature**: GraphQL API with type-safe schema and DataLoader batching
**Date**: 2025-10-02
**Status**: Complete

---

## 1. GraphQL Library Selection

### Decision
**Strawberry GraphQL** (https://strawberry.rocks/)

### Rationale
- **Native FastAPI integration**: First-class support for ASGI via `strawberry.fastapi.GraphQLRouter`
- **Python type hints**: Schema defined using Python type hints (no SDL files required)
- **Async/await support**: Built-in async resolver support for FastAPI async patterns
- **DataLoader integration**: Native support for `strawberry.dataloader` pattern
- **WebSocket subscriptions**: Built-in subscription support via FastAPI WebSocket
- **Active development**: Modern library with frequent updates and strong community

### Alternatives Considered
1. **Graphene-Python**:
   - Pros: Mature library, large ecosystem
   - Cons: Less intuitive type definition, weaker FastAPI integration, no native async DataLoader
   - Rejected: Strawberry provides better developer experience with type hints

2. **Ariadne**:
   - Pros: Schema-first approach, ASGI support
   - Cons: SDL files separate from Python code, less type safety
   - Rejected: Code-first approach with type hints is more maintainable

3. **Tartiflette**:
   - Pros: Performance-focused, async-first
   - Cons: Less mature, smaller community, complex API
   - Rejected: Strawberry provides better balance of features and simplicity

---

## 2. DataLoader Batching Patterns

### Decision
Use `strawberry.dataloader.DataLoader` with async batch functions that generate SQL IN queries

### Rationale
- **Mandatory requirement**: Spec requires DataLoader batching to reduce N+1 queries to ≤2 SQL queries
- **Strawberry native**: `strawberry.dataloader` provides async/await batch loading
- **SQL efficiency**: Batch functions generate `SELECT * FROM nodes WHERE id IN (?, ?, ?)` queries
- **Caching**: DataLoader automatically caches results within a single request
- **Context isolation**: Each GraphQL request gets its own DataLoader instance

### Implementation Pattern
```python
from strawberry.dataloader import DataLoader

async def load_proteins(keys: list[str]) -> list[Protein]:
    """Batch load proteins by ID using SQL IN query"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM nodes WHERE id IN ({})".format(','.join(['?']*len(keys))),
        keys
    )
    rows = cursor.fetchall()
    # Return results in same order as keys
    return [rows_dict[key] for key in keys]

# Usage in resolver
@strawberry.field
async def protein(self, info, id: str) -> Protein:
    loader: DataLoader = info.context["protein_loader"]
    return await loader.load(id)
```

### Batch Scenarios Covered
1. **Node lookups**: `SELECT * FROM nodes WHERE id IN (...)`
2. **Property fetches**: `SELECT * FROM rdf_props WHERE node_id IN (...)`
3. **Relationship fetches**: `SELECT * FROM rdf_edges WHERE source_id IN (...)`
4. **Label fetches**: `SELECT * FROM rdf_labels WHERE node_id IN (...)`

---

## 3. Schema Design

### Decision
Use **Node interface** with type-specific implementations (Protein, Gene, Pathway)

### Rationale
- **Type safety**: GraphQL interfaces enforce common fields across entity types
- **Extensibility**: Easy to add new entity types (Drug, Disease, Variant)
- **Query flexibility**: Clients can query `Node` interface or specific types
- **Fragment support**: Enables GraphQL fragments for shared field selection

### Schema Structure
```graphql
interface Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!
}

type Protein implements Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!
  name: String!
  function: String
  organism: String
  confidence: Float
  interactsWith(first: Int, offset: Int): [Protein!]!
  similar(limit: Int, threshold: Float): [SimilarProtein!]!
}

type Gene implements Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!
  name: String!
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
  name: String!
  description: String
  proteins: [Protein!]!
  genes: [Gene!]!
}
```

### Custom Scalar Types
- **JSON**: For arbitrary properties from rdf_props table
- **DateTime**: ISO 8601 timestamps for createdAt fields

---

## 4. Resolver Implementation

### Decision
Three-tier resolver architecture: Query, Mutation, Subscription

### Query Resolvers
```python
@strawberry.type
class Query:
    @strawberry.field
    async def protein(self, info, id: str) -> Protein:
        """Lookup protein by ID"""
        loader = info.context["protein_loader"]
        return await loader.load(id)

    @strawberry.field
    async def proteins(
        self,
        info,
        where: Optional[ProteinFilter] = None,
        first: int = 10,
        offset: int = 0
    ) -> list[Protein]:
        """List proteins with filtering and pagination"""
        # Generate SQL with WHERE clause
        # Use DataLoader for batch loading
        pass
```

### Mutation Resolvers
```python
@strawberry.type
class Mutation:
    @strawberry.mutation
    async def createProtein(
        self,
        info,
        input: CreateProteinInput
    ) -> Protein:
        """Create new protein with FK validation"""
        # Validate input
        # Insert into nodes, rdf_labels, rdf_props, kg_NodeEmbeddings
        # Invalidate caches
        # Publish subscription event
        pass

    @strawberry.mutation
    async def updateProtein(
        self,
        info,
        id: str,
        input: UpdateProteinInput
    ) -> Protein:
        """Update protein properties"""
        # Validate FK constraints
        # Update rdf_props
        # Invalidate caches
        # Publish subscription event
        pass
```

### Subscription Resolvers
```python
@strawberry.type
class Subscription:
    @strawberry.subscription
    async def proteinCreated(
        self,
        info
    ) -> AsyncGenerator[Protein, None]:
        """Subscribe to protein creation events"""
        async for protein in info.context["event_bus"].subscribe("protein.created"):
            yield protein

    @strawberry.subscription
    async def proteinUpdated(
        self,
        info,
        id: str
    ) -> AsyncGenerator[Protein, None]:
        """Subscribe to updates for specific protein"""
        async for protein in info.context["event_bus"].subscribe(f"protein.{id}.updated"):
            yield protein
```

---

## 5. WebSocket Integration for Subscriptions

### Decision
Use `strawberry.subscriptions` with FastAPI WebSocket support

### Rationale
- **Real-time updates**: WebSocket provides bidirectional communication for subscriptions
- **Native Strawberry support**: `GraphQLRouter` includes WebSocket endpoint
- **Event bus pattern**: In-memory event bus for pub/sub (upgradeable to Redis)
- **Connection limit**: 1000 concurrent WebSocket connections (configurable)

### Implementation Pattern
```python
from strawberry.fastapi import GraphQLRouter
from fastapi import FastAPI

app = FastAPI()

graphql_app = GraphQLRouter(
    schema,
    subscription_protocols=[
        "graphql-transport-ws",  # Modern protocol
        "graphql-ws"             # Legacy protocol
    ]
)

app.include_router(graphql_app, prefix="/graphql")
```

### Event Bus Design
```python
class EventBus:
    """In-memory event bus for subscriptions"""
    def __init__(self):
        self.subscribers: dict[str, list[asyncio.Queue]] = {}

    async def publish(self, channel: str, message: Any):
        """Publish event to all subscribers"""
        for queue in self.subscribers.get(channel, []):
            await queue.put(message)

    async def subscribe(self, channel: str) -> AsyncGenerator[Any, None]:
        """Subscribe to channel events"""
        queue = asyncio.Queue()
        self.subscribers.setdefault(channel, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self.subscribers[channel].remove(queue)
```

---

## 6. Vector Search Integration

### Decision
Implement `similar` field resolver using HNSW index via `kg_KNN_VEC` SQL operator

### Rationale
- **Existing infrastructure**: Leverages existing HNSW index on kg_NodeEmbeddings table
- **Performance**: <10ms for k=10 queries with ACORN-1 optimization
- **GraphQL native**: Field resolver pattern feels natural for GraphQL queries
- **Composable**: Can combine with graph traversal in single query

### Implementation Pattern
```python
@strawberry.type
class Protein:
    @strawberry.field
    async def similar(
        self,
        info,
        limit: int = 10,
        threshold: float = 0.7
    ) -> list[SimilarProtein]:
        """Find similar proteins using vector embeddings"""
        cursor = info.context["db"].cursor()
        cursor.execute("""
            SELECT
                n.id,
                VECTOR_DOT_PRODUCT(e1.embedding, e2.embedding) as similarity
            FROM kg_NodeEmbeddings e1
            JOIN kg_NodeEmbeddings e2 ON e2.node_id != e1.node_id
            JOIN nodes n ON n.id = e2.node_id
            WHERE e1.node_id = ?
              AND VECTOR_DOT_PRODUCT(e1.embedding, e2.embedding) >= ?
            ORDER BY similarity DESC
            LIMIT ?
        """, (self.id, threshold, limit))

        rows = cursor.fetchall()
        return [
            SimilarProtein(
                protein=await info.context["protein_loader"].load(row[0]),
                similarity=row[1]
            )
            for row in rows
        ]
```

### Hybrid Vector+Graph Query Example
```graphql
query {
  protein(id: "PROTEIN:TP53") {
    name
    similar(limit: 5, threshold: 0.8) {
      protein {
        name
        interactsWith(first: 3) {
          name
        }
      }
      similarity
    }
  }
}
```

---

## 7. Caching Strategies

### Decision
Resolver-level caching with 60-second TTL and manual invalidation on mutations

### Rationale
- **Performance**: Reduces redundant database queries for frequently accessed data
- **Freshness**: 60-second TTL balances performance and data staleness
- **Manual invalidation**: Mutations explicitly invalidate affected caches
- **Request-scoped**: DataLoader provides automatic request-scoped caching

### Implementation Layers
1. **DataLoader cache**: Automatic within single GraphQL request
2. **Resolver cache**: Cross-request cache with TTL
3. **Cache invalidation**: Mutations trigger cache clears

### Implementation Pattern
```python
from functools import lru_cache
from datetime import datetime, timedelta

class CacheEntry:
    def __init__(self, value, ttl_seconds=60):
        self.value = value
        self.expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)

    def is_expired(self):
        return datetime.utcnow() > self.expires_at

# Global resolver cache (in production: use Redis)
resolver_cache: dict[str, CacheEntry] = {}

@strawberry.field
async def protein(self, info, id: str) -> Protein:
    """Cached protein lookup"""
    cache_key = f"protein:{id}"

    # Check cache
    if cache_key in resolver_cache:
        entry = resolver_cache[cache_key]
        if not entry.is_expired():
            return entry.value

    # Cache miss or expired
    protein = await info.context["protein_loader"].load(id)
    resolver_cache[cache_key] = CacheEntry(protein, ttl_seconds=60)
    return protein

@strawberry.mutation
async def updateProtein(self, info, id: str, input: UpdateProteinInput) -> Protein:
    """Update protein and invalidate cache"""
    # Update database
    protein = await perform_update(id, input)

    # Invalidate cache
    cache_key = f"protein:{id}"
    if cache_key in resolver_cache:
        del resolver_cache[cache_key]

    return protein
```

---

## 8. Query Complexity Limits

### Decision
Depth-based complexity algorithm with 10-level max depth (configurable)

### Rationale
- **DoS prevention**: Prevents expensive deeply nested queries
- **Configurable**: Max depth adjustable via environment variable
- **Simple algorithm**: Depth counting is straightforward and predictable
- **Spec requirement**: Meets spec requirement for depth-based complexity

### Implementation Pattern
```python
from strawberry.extensions import Extension

class QueryComplexityExtension(Extension):
    """Enforce query depth limits"""

    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth

    def on_request_start(self):
        """Validate query complexity before execution"""
        depth = self.calculate_depth(self.execution_context.query)
        if depth > self.max_depth:
            raise GraphQLError(
                f"Query depth {depth} exceeds maximum {self.max_depth}",
                extensions={"code": "MAX_DEPTH_EXCEEDED"}
            )

    def calculate_depth(self, query) -> int:
        """Calculate query depth using AST traversal"""
        # Traverse AST and count nesting levels
        pass
```

### Configuration
```python
schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
    extensions=[
        QueryComplexityExtension(max_depth=10)
    ]
)
```

---

## Summary

All research topics resolved. Key decisions:
1. **Strawberry GraphQL** for native FastAPI integration and type safety
2. **DataLoader batching** with SQL IN queries (mandatory)
3. **Node interface** for extensible schema design
4. **Three-tier resolvers** (query, mutation, subscription)
5. **WebSocket subscriptions** via FastAPI WebSocket
6. **HNSW vector search** via field resolvers
7. **60-second resolver caching** with manual invalidation
8. **Depth-based complexity** limits (10 levels max)

No remaining ambiguities. Ready for Phase 1 design.
