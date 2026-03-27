# Tasks: GraphQL API Endpoint

**Feature**: GraphQL API with type-safe schema and DataLoader batching
**Branch**: `003-add-graphql-endpoint`
**Prerequisites**: plan.md, research.md, data-model.md, contracts/ (schema.graphql, example_queries.graphql, example_mutations.graphql, example_subscriptions.graphql), quickstart.md

---

## Execution Flow (main)
```
1. Setup project structure and dependencies
2. Create contract tests (MUST FAIL before implementation)
3. Implement core Strawberry types
4. Implement DataLoader batching (MANDATORY before resolvers)
5. Implement resolvers (queries, mutations, subscriptions)
6. Integrate GraphQL schema with FastAPI
7. Add complexity limits and caching
8. Run integration and performance tests
9. Update documentation
```

---

## Phase 3.1: Setup (T001-T003)

- [ ] **T001** Create project structure per implementation plan
  - Create directories:
    - `/Users/tdyar/ws/iris-vector-graph/api/graphql/`
    - `/Users/tdyar/ws/iris-vector-graph/api/graphql/resolvers/`
    - `/Users/tdyar/ws/iris-vector-graph/api/models/`
    - `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/graphql/`
    - `/Users/tdyar/ws/iris-vector-graph/tests/contract/`
    - `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/`
    - `/Users/tdyar/ws/iris-vector-graph/scripts/performance/`
  - Create `__init__.py` files in all Python package directories

- [ ] **T002** Initialize Python dependencies for GraphQL
  - Add to `/Users/tdyar/ws/iris-vector-graph/pyproject.toml`:
    - `strawberry-graphql[fastapi]>=0.234.0` (GraphQL schema with FastAPI integration)
    - `uvicorn[standard]>=0.30.0` (ASGI server with WebSocket support)
    - `pydantic>=2.0.0` (input validation)
    - `pytest-asyncio>=0.23.0` (async test support)
  - Run: `uv sync` to install dependencies
  - Dependencies: None

- [X] **T003 [P]** Configure linting and type checking
  - Update `/Users/tdyar/ws/iris-vector-graph/pyproject.toml`:
    - Add `api/` and `iris_vector_graph/graphql/` to mypy paths
    - Add `tests/integration/graphql/` to pytest paths
    - Configure async test timeout: 30 seconds
  - Dependencies: T002

---

## Phase 3.2: Contract Tests First (TDD) - T004-T006 ⚠️ MUST COMPLETE BEFORE IMPLEMENTATION

**CRITICAL: These tests MUST be written and MUST FAIL before ANY implementation**

- [X] **T004 [P]** Write failing GraphQL schema validation test
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/contract/test_graphql_schema.py`
  - Test: Schema introspection validates all types from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql`
  - Test: Node interface implemented by Protein, Gene, Pathway types
  - Test: Custom scalars (JSON, DateTime) registered correctly
  - Test: Query, Mutation, Subscription root types defined
  - Test: Field signatures match contract (e.g., `protein(id: ID!): Protein`)
  - Expected: **FAIL** (schema not implemented yet)
  - Dependencies: T003

- [X] **T005 [P]** Write failing contract test for Node interface introspection
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/contract/test_graphql_schema.py` (append)
  - Test: Node interface introspection returns `id`, `labels`, `properties`, `createdAt` fields
  - Test: Protein type introspection shows Node interface implementation
  - Test: All Node implementers (Protein, Gene, Pathway, Variant) include Node fields
  - Expected: **FAIL** (Node interface not implemented yet)
  - Dependencies: T003

- [X] **T006 [P]** Write failing contract tests for example queries
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/contract/test_graphql_queries.py`
  - Test queries from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_queries.graphql`:
    - Query 1: Simple protein lookup (GetProtein)
    - Query 2: Nested interactions (ProteinWithInteractions)
    - Query 4: Vector similarity (SimilarProteins)
    - Query 10: Graph statistics (Stats)
  - Test: Query validation passes (syntax check only, execution expected to fail)
  - Expected: **VALIDATION PASS, EXECUTION FAIL** (resolvers not implemented yet)
  - Dependencies: T003

---

## Phase 3.3: Core Strawberry Types - T007-T014 [P]

**CRITICAL: All type implementations MUST come AFTER contract tests (T004-T006)**

- [X] **T007 [P]** Create Node interface
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py`
  - Implement Strawberry interface:
    ```python
    @strawberry.interface
    class Node:
        id: strawberry.ID
        labels: List[str]
        properties: JSON
        created_at: DateTime
    ```
  - Dependency: T004, T005

- [X] **T008 [P]** Create Protein type implementing Node interface
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (append)
  - Implement Strawberry type:
    ```python
    @strawberry.type
    class Protein(Node):
        # Node interface fields
        id: strawberry.ID
        labels: List[str]
        properties: JSON
        created_at: DateTime

        # Protein-specific fields
        name: str
        function: Optional[str]
        organism: Optional[str]
        confidence: Optional[float]

        # Relationship fields (stubs - resolvers added later)
        @strawberry.field
        async def interacts_with(self, first: int = 10, offset: int = 0) -> List["Protein"]:
            raise NotImplementedError("Resolver not implemented")

        # Vector similarity field (stub)
        @strawberry.field
        async def similar(self, limit: int = 10, threshold: float = 0.7) -> List["SimilarProtein"]:
            raise NotImplementedError("Resolver not implemented")
    ```
  - Dependency: T004, T007

- [X] **T009 [P]** Create Gene type implementing Node interface
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (append)
  - Implement Strawberry type matching `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql` Gene type
  - Include stubs for `encodes`, `variants` relationship fields
  - Dependency: T004, T007

- [X] **T010 [P]** Create Pathway type implementing Node interface
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (append)
  - Implement Strawberry type matching `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql` Pathway type
  - Include stubs for `proteins`, `genes` relationship fields
  - Dependency: T004, T007

- [X] **T011 [P]** Create Variant type implementing Node interface
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (append)
  - Implement Strawberry type matching `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql` Variant type
  - Dependency: T004, T007

- [X] **T012 [P]** Create Interaction and SimilarProtein types
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (append)
  - Implement:
    ```python
    @strawberry.type
    class Interaction:
        source: Node
        target: Node
        type: str
        confidence: Optional[float]
        qualifiers: Optional[JSON]

    @strawberry.type
    class SimilarProtein:
        protein: Protein
        similarity: float
        distance: Optional[float]
    ```
  - Dependency: T004

- [X] **T013 [P]** Create ProteinNeighborhood, Path, GraphStats result types
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (append)
  - Implement types matching `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/schema.graphql`
  - Dependency: T004

- [X] **T014 [P]** Create Input types (CreateProteinInput, UpdateProteinInput, ProteinFilter)
  - File: `/Users/tdyar/ws/iris-vector-graph/api/models/graphql.py`
  - Implement Pydantic models for input validation:
    ```python
    from pydantic import BaseModel, Field, validator
    from typing import Optional, List

    class CreateProteinInput(BaseModel):
        id: str = Field(..., min_length=1)
        name: str = Field(..., min_length=1)
        function: Optional[str] = None
        organism: Optional[str] = None
        embedding: Optional[List[float]] = Field(None, min_items=768, max_items=768)

        @validator('embedding')
        def validate_embedding_dimension(cls, v):
            if v is not None and len(v) != 768:
                raise ValueError('Embedding must be 768-dimensional')
            return v
    ```
  - Also implement UpdateProteinInput and ProteinFilter
  - Dependency: T004

---

## Phase 3.4: DataLoader Implementation - T015-T018 [P] ⚠️ MANDATORY BEFORE RESOLVERS

**CRITICAL: DataLoader batching is MANDATORY per spec (NFR-004). Must be implemented BEFORE query resolvers (T019-T022) to prevent N+1 queries.**

- [X] **T015 [P]** Write failing unit tests for ProteinLoader
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/unit/test_graphql_dataloader.py`
  - Test: `test_protein_loader_batch_load_by_id`
    - Load 10 proteins with different IDs
    - Verify single SQL query executed: `SELECT * FROM nodes WHERE id IN (?, ?, ...)`
    - Verify results returned in same order as input keys
  - Test: `test_protein_loader_caching`
    - Load same protein ID twice within request
    - Verify only 1 SQL query executed (DataLoader caching)
  - Marker: `@pytest.mark.requires_database`
  - Expected: **FAIL** (ProteinLoader not implemented yet)
  - Dependencies: T014

- [X] **T016 [P]** Implement ProteinLoader with SQL batching
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/loaders.py`
  - Implement DataLoader using strawberry.dataloader:
    ```python
    from strawberry.dataloader import DataLoader
    from typing import List
    import iris

    class ProteinLoader(DataLoader):
        def __init__(self, db_connection):
            super().__init__()
            self.db = db_connection

        async def batch_load_fn(self, keys: List[str]) -> List[Optional[Protein]]:
            cursor = self.db.cursor()
            placeholders = ','.join(['?' for _ in keys])
            cursor.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})",
                keys
            )
            rows = cursor.fetchall()

            # Return in same order as keys
            row_dict = {row['id']: row for row in rows}
            return [row_dict.get(key) for key in keys]
    ```
  - Dependencies: T015

- [X] **T017 [P]** Write failing unit tests for EdgeLoader, PropertyLoader, LabelLoader
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/unit/test_graphql_dataloader.py` (append)
  - Test: `test_edge_loader_batch_load_by_source`
    - Load edges for 5 source nodes
    - Verify single SQL query: `SELECT * FROM rdf_edges WHERE source_id IN (...)`
    - Verify results grouped by source_id
  - Test: `test_property_loader_batch_load`
    - Load properties for 5 nodes
    - Verify single SQL query: `SELECT * FROM rdf_props WHERE node_id IN (...)`
    - Verify key-value pairs aggregated into dictionaries
  - Test: `test_label_loader_batch_load`
    - Load labels for 5 nodes
    - Verify single SQL query: `SELECT * FROM rdf_labels WHERE node_id IN (...)`
    - Verify labels grouped by node_id
  - Marker: `@pytest.mark.requires_database`
  - Expected: **FAIL** (loaders not implemented yet)
  - Dependencies: T015

- [X] **T018 [P]** Implement EdgeLoader, PropertyLoader, LabelLoader with SQL batching
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/loaders.py` (append)
  - Implement DataLoaders following pattern from T016:
    - EdgeLoader: Batch load edges by source_id, return list of lists
    - PropertyLoader: Batch load properties, return list of dictionaries
    - LabelLoader: Batch load labels, return list of lists
  - Follow data model from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/data-model.md` (lines 295-419)
  - Dependencies: T017

---

## Phase 3.5: Query Resolver Implementation - T019-T024

**CRITICAL: Resolvers MUST use DataLoaders (T016, T018) to prevent N+1 queries**

- [ ] **T019** Write failing integration test for protein query resolver
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/test_graphql_queries.py`
  - Test: `test_protein_query_simple_lookup`
    - Query: `protein(id: "PROTEIN:TP53") { id, name, function }`
    - Verify result matches expected data from IRIS database
    - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Test: `test_protein_query_not_found`
    - Query: `protein(id: "PROTEIN:NONEXISTENT") { id }`
    - Verify returns null with NOT_FOUND error
  - Expected: **FAIL** (resolver not implemented yet)
  - Dependencies: T018 (DataLoaders must be implemented first)

- [ ] **T020** Implement protein, gene, pathway query resolvers
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/resolvers/query.py`
  - Implement Query root type:
    ```python
    @strawberry.type
    class Query:
        @strawberry.field
        async def protein(self, info: Info, id: strawberry.ID) -> Optional[Protein]:
            loader: ProteinLoader = info.context["protein_loader"]
            return await loader.load(id)

        @strawberry.field
        async def gene(self, info: Info, id: strawberry.ID) -> Optional[Gene]:
            loader: GeneLoader = info.context["gene_loader"]
            return await loader.load(id)

        @strawberry.field
        async def pathway(self, info: Info, id: strawberry.ID) -> Optional[Pathway]:
            loader: PathwayLoader = info.context["pathway_loader"]
            return await loader.load(id)
    ```
  - Dependencies: T019

- [ ] **T021** Write failing integration test for nested query with DataLoader batching verification
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/test_graphql_queries.py` (append)
  - Test: `test_nested_query_dataloader_batching`
    - Query from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_queries.graphql` (Query 2):
      ```graphql
      protein(id: "PROTEIN:TP53") {
        name
        interactsWith(first: 5) {
          name
          function
        }
      }
      ```
    - Verify ≤2 SQL queries executed (DataLoader batching working)
    - Verify nested proteins returned correctly
    - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Expected: **FAIL** (nested field resolvers not implemented yet)
  - Dependencies: T020

- [ ] **T022** Implement nested field resolvers (interactsWith, regulatedBy, participatesIn)
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (update Protein class)
  - Replace stub implementations from T008:
    ```python
    @strawberry.field
    async def interacts_with(self, info: Info, first: int = 10, offset: int = 0) -> List["Protein"]:
        edge_loader: EdgeLoader = info.context["edge_loader"]
        protein_loader: ProteinLoader = info.context["protein_loader"]

        # Load edges for this protein (batched)
        edges = await edge_loader.load(self.id)

        # Filter by type and apply pagination
        interaction_edges = [e for e in edges if e.type == "INTERACTS_WITH"]
        paginated_edges = interaction_edges[offset:offset+first]

        # Batch load target proteins
        target_ids = [e.target_id for e in paginated_edges]
        return await protein_loader.load_many(target_ids)
    ```
  - Also implement `regulated_by` and `participates_in` field resolvers
  - Dependencies: T021

---

## Phase 3.6: Vector Search Integration - T023-T024

- [ ] **T023** Write failing integration test for similar() field resolver using HNSW
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/test_graphql_vector_search.py`
  - Test: `test_vector_similarity_query`
    - Query from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_queries.graphql` (Query 4):
      ```graphql
      protein(id: "PROTEIN:TP53") {
        similar(limit: 10, threshold: 0.8) {
          protein { name }
          similarity
        }
      }
      ```
    - Verify HNSW index used (check SQL execution plan)
    - Verify query completes in <10ms
    - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Expected: **FAIL** (similar() resolver not implemented yet)
  - Dependencies: T022

- [ ] **T024** Implement similar() field resolver with kg_KNN_VEC SQL operator
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/types.py` (update Protein class)
  - Replace stub implementation from T008:
    ```python
    @strawberry.field
    async def similar(self, info: Info, limit: int = 10, threshold: float = 0.7) -> List[SimilarProtein]:
        cursor = info.context["db_connection"].cursor()

        # Get embedding for this protein
        cursor.execute(
            "SELECT embedding FROM kg_NodeEmbeddings WHERE node_id = ?",
            [self.id]
        )
        embedding_row = cursor.fetchone()
        if not embedding_row:
            return []

        # Use HNSW index via kg_KNN_VEC operator
        cursor.execute(
            """
            SELECT node_id, VECTOR_DOT_PRODUCT(embedding, ?) AS similarity
            FROM kg_NodeEmbeddings
            WHERE node_id != ?
            ORDER BY similarity DESC
            LIMIT ?
            """,
            [embedding_row['embedding'], self.id, limit]
        )
        rows = cursor.fetchall()

        # Load proteins and construct results
        protein_loader: ProteinLoader = info.context["protein_loader"]
        protein_ids = [row['node_id'] for row in rows if row['similarity'] >= threshold]
        proteins = await protein_loader.load_many(protein_ids)

        return [
            SimilarProtein(
                protein=protein,
                similarity=row['similarity'],
                distance=1.0 - row['similarity']
            )
            for protein, row in zip(proteins, rows) if row['similarity'] >= threshold
        ]
    ```
  - Dependencies: T023

---

## Phase 3.7: Mutation Resolver Implementation - T025-T027

- [ ] **T025** Write failing integration test for createProtein mutation with FK validation
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/test_graphql_mutations.py`
  - Test: `test_create_protein_success`
    - Mutation from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_mutations.graphql` (Mutation 1)
    - Verify protein inserted into nodes, rdf_labels, rdf_props tables
    - Verify embedding inserted into kg_NodeEmbeddings (if provided)
    - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Test: `test_create_protein_duplicate_id_fails`
    - Create protein with existing ID
    - Verify FK_CONSTRAINT_VIOLATION error returned
  - Expected: **FAIL** (mutation resolver not implemented yet)
  - Dependencies: T022

- [ ] **T026** Implement createProtein, updateProtein, deleteProtein mutations
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/resolvers/mutation.py`
  - Implement Mutation root type:
    ```python
    @strawberry.type
    class Mutation:
        @strawberry.mutation
        async def create_protein(self, info: Info, input: CreateProteinInput) -> Protein:
            cursor = info.context["db_connection"].cursor()

            try:
                # Start transaction
                cursor.execute("START TRANSACTION")

                # Insert into nodes table
                cursor.execute(
                    "INSERT INTO nodes (id, created_at) VALUES (?, NOW())",
                    [input.id]
                )

                # Insert label
                cursor.execute(
                    "INSERT INTO rdf_labels (node_id, label) VALUES (?, 'Protein')",
                    [input.id]
                )

                # Insert properties
                for key, value in [("name", input.name), ("function", input.function), ("organism", input.organism)]:
                    if value is not None:
                        cursor.execute(
                            "INSERT INTO rdf_props (node_id, key, value) VALUES (?, ?, ?)",
                            [input.id, key, value]
                        )

                # Insert embedding if provided
                if input.embedding:
                    cursor.execute(
                        "INSERT INTO kg_NodeEmbeddings (node_id, embedding) VALUES (?, ?)",
                        [input.id, input.embedding]
                    )

                # Commit transaction
                cursor.execute("COMMIT")

                # Publish subscription event
                await info.context["pubsub"].publish("protein_created", input.id)

                # Load and return created protein
                loader: ProteinLoader = info.context["protein_loader"]
                return await loader.load(input.id)

            except Exception as e:
                cursor.execute("ROLLBACK")
                raise
    ```
  - Also implement `update_protein` and `delete_protein` mutations
  - Dependencies: T025

- [ ] **T027** Write failing integration test for batch createProteins mutation
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/test_graphql_mutations.py` (append)
  - Test: `test_batch_create_proteins`
    - Mutation from `/Users/tdyar/ws/iris-vector-graph/specs/003-add-graphql-endpoint/contracts/example_mutations.graphql` (Mutation 4)
    - Create 3 proteins in single request
    - Verify all inserted successfully
    - Verify transaction atomicity (all or nothing)
    - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Expected: **FAIL** (batch mutation not implemented yet)
  - Dependencies: T026

---

## Phase 3.8: Subscription Resolver Implementation - T028-T029

- [ ] **T028** Write failing integration test for proteinCreated subscription
  - File: `/Users/tdyar/ws/iris-vector-graph/tests/integration/graphql/test_graphql_subscriptions.py`
  - Test: `test_protein_created_subscription`
    - Subscribe to proteinCreated events via WebSocket
    - Create new protein via mutation
    - Verify subscription receives event within 100ms
    - Verify event payload matches created protein
    - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Expected: **FAIL** (subscription resolver not implemented yet)
  - Dependencies: T026

- [ ] **T029** Implement subscription resolvers (proteinCreated, proteinUpdated, interactionCreated)
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/resolvers/subscription.py`
  - Implement Subscription root type:
    ```python
    import asyncio
    from typing import AsyncGenerator

    @strawberry.type
    class Subscription:
        @strawberry.subscription
        async def protein_created(self, info: Info) -> AsyncGenerator[Protein, None]:
            pubsub = info.context["pubsub"]
            async for protein_id in pubsub.subscribe("protein_created"):
                loader: ProteinLoader = info.context["protein_loader"]
                protein = await loader.load(protein_id)
                if protein:
                    yield protein

        @strawberry.subscription
        async def protein_updated(self, info: Info, id: Optional[strawberry.ID] = None) -> AsyncGenerator[Protein, None]:
            pubsub = info.context["pubsub"]
            channel = f"protein_updated:{id}" if id else "protein_updated"
            async for protein_id in pubsub.subscribe(channel):
                if id is None or protein_id == id:
                    loader: ProteinLoader = info.context["protein_loader"]
                    protein = await loader.load(protein_id)
                    if protein:
                        yield protein
    ```
  - Also implement `interaction_created` subscription
  - Dependencies: T028

---

## Phase 3.9: Schema Integration & Complexity - T030-T032

- [ ] **T030** Implement GraphQL schema composition with Strawberry
  - File: `/Users/tdyar/ws/iris-vector-graph/api/graphql/schema.py`
  - Compose schema from Query, Mutation, Subscription types:
    ```python
    import strawberry
    from api.graphql.resolvers.query import Query
    from api.graphql.resolvers.mutation import Mutation
    from api.graphql.resolvers.subscription import Subscription
    from api.graphql.types import JSON, DateTime

    schema = strawberry.Schema(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        scalar_overrides={
            JSON: strawberry.scalar(JSON),
            DateTime: strawberry.scalar(DateTime)
        }
    )
    ```
  - Dependencies: T020, T026, T029

- [ ] **T031** Implement query complexity extension (depth-based, 10-level max)
  - File: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/graphql/complexity.py`
  - Implement depth-based complexity calculator:
    ```python
    from strawberry.extensions import Extension
    from graphql import GraphQLError

    class QueryDepthLimitExtension(Extension):
        def __init__(self, max_depth: int = 10):
            self.max_depth = max_depth

        def on_parse(self):
            def check_depth(node, depth=0):
                if depth > self.max_depth:
                    raise GraphQLError(
                        f"Query depth {depth} exceeds maximum {self.max_depth}",
                        extensions={"code": "MAX_DEPTH_EXCEEDED"}
                    )
                # Recursively check child selections
                if hasattr(node, 'selection_set') and node.selection_set:
                    for selection in node.selection_set.selections:
                        check_depth(selection, depth + 1)

            check_depth(self.execution_context.document)
    ```
  - Add to schema in `/Users/tdyar/ws/iris-vector-graph/api/graphql/schema.py`:
    ```python
    schema = strawberry.Schema(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        extensions=[QueryDepthLimitExtension(max_depth=10)]
    )
    ```
  - Dependencies: T030

- [ ] **T032** Implement resolver-level caching (60s TTL with manual invalidation)
  - File: `/Users/tdyar/ws/iris-vector-graph/iris_vector_graph/graphql/cache.py`
  - Implement caching decorator:
    ```python
    import asyncio
    from functools import wraps
    from typing import Optional, Dict, Any
    from datetime import datetime, timedelta

    class ResolverCache:
        def __init__(self, ttl_seconds: int = 60):
            self.ttl_seconds = ttl_seconds
            self.cache: Dict[str, tuple[Any, datetime]] = {}

        def get(self, key: str) -> Optional[Any]:
            if key in self.cache:
                value, expires_at = self.cache[key]
                if datetime.now() < expires_at:
                    return value
                del self.cache[key]
            return None

        def set(self, key: str, value: Any):
            expires_at = datetime.now() + timedelta(seconds=self.ttl_seconds)
            self.cache[key] = (value, expires_at)

        def invalidate(self, pattern: str):
            keys_to_delete = [k for k in self.cache.keys() if pattern in k]
            for key in keys_to_delete:
                del self.cache[key]

    def cached(ttl_seconds: int = 60):
        def decorator(func):
            @wraps(func)
            async def wrapper(self, info, *args, **kwargs):
                cache: ResolverCache = info.context["resolver_cache"]
                cache_key = f"{func.__name__}:{self.id}:{args}:{kwargs}"

                cached_value = cache.get(cache_key)
                if cached_value is not None:
                    return cached_value

                result = await func(self, info, *args, **kwargs)
                cache.set(cache_key, result)
                return result
            return wrapper
        return decorator
    ```
  - Update mutation resolvers to invalidate cache on write operations
  - Dependencies: T030

---

## Phase 3.10: FastAPI Integration - T033-T034

- [ ] **T033** Implement FastAPI + Strawberry GraphQL integration
  - File: `/Users/tdyar/ws/iris-vector-graph/api/main.py`
  - Integrate GraphQL endpoint:
    ```python
    from fastapi import FastAPI
    from strawberry.fastapi import GraphQLRouter
    from api.graphql.schema import schema
    from api.dependencies import get_iris_connection, get_graphql_context

    app = FastAPI(title="IRIS Vector Graph API")

    # GraphQL endpoint
    graphql_app = GraphQLRouter(
        schema,
        context_getter=get_graphql_context,
        graphiql=True  # Enable GraphQL Playground
    )

    app.include_router(graphql_app, prefix="/graphql")

    @app.get("/health")
    async def health():
        return {"status": "ok"}
    ```
  - GraphQL Playground accessible at: http://localhost:8000/graphql
  - Dependencies: T030, T031, T032

- [ ] **T034** Implement IRIS connection pooling and auth dependencies
  - File: `/Users/tdyar/ws/iris-vector-graph/api/dependencies.py`
  - Implement context factory for GraphQL:
    ```python
    import iris
    from typing import AsyncGenerator, Dict, Any
    from api.graphql.loaders import ProteinLoader, EdgeLoader, PropertyLoader, LabelLoader
    from iris_vector_graph.graphql.cache import ResolverCache

    async def get_iris_connection():
        """Get IRIS database connection from pool"""
        conn = iris.connect(
            hostname=os.getenv("IRIS_HOST", "localhost"),
            port=int(os.getenv("IRIS_PORT", "1972")),
            namespace=os.getenv("IRIS_NAMESPACE", "USER"),
            username=os.getenv("IRIS_USER", "_SYSTEM"),
            password=os.getenv("IRIS_PASSWORD", "SYS")
        )
        try:
            yield conn
        finally:
            conn.close()

    async def get_graphql_context(
        db_connection=Depends(get_iris_connection)
    ) -> Dict[str, Any]:
        """Create GraphQL execution context with DataLoaders and cache"""
        return {
            "db_connection": db_connection,
            "protein_loader": ProteinLoader(db_connection),
            "edge_loader": EdgeLoader(db_connection),
            "property_loader": PropertyLoader(db_connection),
            "label_loader": LabelLoader(db_connection),
            "resolver_cache": ResolverCache(ttl_seconds=60),
            "pubsub": PubSubManager()  # For subscriptions
        }
    ```
  - Dependencies: T033

---

## Phase 3.11: Performance & Documentation - T035-T037

- [ ] **T035 [P]** Write and run performance test for DataLoader N+1 prevention
  - File: `/Users/tdyar/ws/iris-vector-graph/scripts/performance/test_graphql_performance.py`
  - Test: Nested query executes ≤2 SQL queries
    - Query: `proteins(first: 10) { name, interactsWith(first: 5) { name } }`
    - Track SQL query count with database connection spy
    - Verify: 1 query for proteins, 1 batched query for all interactions
    - Expected: ≤2 SQL queries (95% reduction vs N+1)
  - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Dependencies: T022, T034

- [ ] **T036 [P]** Write and run performance test for vector similarity queries
  - File: `/Users/tdyar/ws/iris-vector-graph/scripts/performance/test_graphql_performance.py` (append)
  - Test: Vector similarity query completes in <10ms with HNSW
    - Query: `protein(id: "PROTEIN:TP53") { similar(limit: 10) { protein { name } similarity } }`
    - Measure execution time
    - Verify HNSW index usage via SQL EXPLAIN
    - Expected: <10ms with HNSW index (ACORN-1 optimization)
  - Test: GraphQL vs SQL overhead <10%
    - Compare GraphQL query execution time vs equivalent hand-written SQL
    - Expected: GraphQL overhead <10%
  - Marker: `@pytest.mark.requires_database`, `@pytest.mark.integration`
  - Dependencies: T024, T034

- [ ] **T037** Update CLAUDE.md with GraphQL-specific development guidance
  - File: `/Users/tdyar/ws/iris-vector-graph/CLAUDE.md`
  - Add sections:
    - **GraphQL Development Commands**:
      ```bash
      # Start GraphQL server
      uvicorn api.main:app --reload --port 8000

      # Run GraphQL tests
      pytest tests/integration/graphql/ -v

      # Performance benchmarks
      uv run python scripts/performance/test_graphql_performance.py
      ```
    - **GraphQL Architecture Overview**:
      - Strawberry GraphQL schema with FastAPI integration
      - DataLoader batching prevents N+1 queries (mandatory)
      - HNSW vector search via similar() field resolvers
      - WebSocket subscriptions for real-time updates
    - **Testing Requirements**:
      - All GraphQL tests use live IRIS database (@pytest.mark.requires_database)
      - Contract tests validate schema introspection
      - Integration tests verify DataLoader batching (≤2 SQL queries)
      - Performance tests validate <10ms vector queries with HNSW
  - Dependencies: T035, T036

---

## Dependencies

**Setup → Tests → Types → DataLoaders → Resolvers → Schema → Integration**

- T001 (setup) → T002 (dependencies) → T003 (config)
- T003 → T004, T005, T006 (contract tests - parallel)
- T004, T005, T006 → T007-T014 (types - parallel, depends on contract tests)
- T014 → T015, T017 (DataLoader tests - parallel)
- T015 → T016, T017 → T018 (DataLoader implementations - parallel)
- T018 → T019 (query tests depend on DataLoaders)
- T019 → T020 → T021 → T022 (query resolvers - sequential, same file)
- T022 → T023 → T024 (vector search - sequential)
- T022 → T025 → T026 → T027 (mutations - sequential, same file)
- T026 → T028 → T029 (subscriptions - sequential)
- T020, T026, T029 → T030 (schema composition)
- T030 → T031, T032 (complexity and caching - sequential, modify same schema)
- T030, T031, T032 → T033 → T034 (FastAPI integration - sequential)
- T034 → T035, T036 (performance tests - parallel)
- T035, T036 → T037 (documentation update)

---

## Parallel Execution Examples

```bash
# Example 1: Schema contract tests (independent files)
Task --parallel T004 T005 T006

# Example 2: Strawberry types (independent types, same file but no conflicts)
Task --parallel T007 T008 T009 T010 T011 T012 T013 T014

# Example 3: DataLoader tests (independent tests)
Task --parallel T015 T017

# Example 4: DataLoader implementations (independent loaders, same file)
Task --parallel T016 T018

# Example 5: Performance tests (independent test scenarios)
Task --parallel T035 T036
```

---

## Notes

- **TDD Order**: All contract tests (T004-T006) BEFORE implementation (T007+)
- **DataLoader MANDATORY**: T015-T018 MUST complete before query resolvers (T019-T022)
- **[P] marker**: Tasks touching different files or independent test scenarios
- **Sequential**: Tasks modifying same file (resolvers, schema integration)
- **@requires_database**: All integration/performance tests use live IRIS
- **HNSW requirement**: Vector queries require IRIS 2025.3+ with ACORN=1 optimization
- **Commit after each task**: Incremental progress with test validation

---

## Validation Checklist

**Contracts → Implementation → Integration**

- [x] All contracts have corresponding tests (T004-T006)
- [x] All entities have type tasks (T007-T014)
- [x] All tests come before implementation (TDD order enforced)
- [x] DataLoader tasks before resolver tasks (T015-T018 → T019-T022)
- [x] Parallel tasks truly independent (different files or scenarios)
- [x] Each task specifies exact file path (absolute paths)
- [x] No task modifies same file as another [P] task
- [x] Performance tests validate NFRs (≤2 SQL queries, <10ms vector search)
- [x] GraphQL-specific guidance added to CLAUDE.md (T037)

---

## Task Count Summary

- **Setup**: 3 tasks (T001-T003)
- **Contract Tests**: 3 tasks (T004-T006)
- **Core Types**: 8 tasks (T007-T014)
- **DataLoaders**: 4 tasks (T015-T018) - MANDATORY before resolvers
- **Query Resolvers**: 6 tasks (T019-T024)
- **Mutation Resolvers**: 3 tasks (T025-T027)
- **Subscription Resolvers**: 2 tasks (T028-T029)
- **Schema Integration**: 3 tasks (T030-T032)
- **FastAPI Integration**: 2 tasks (T033-T034)
- **Performance & Docs**: 3 tasks (T035-T037)

**Total**: 37 tasks (T001-T037)

**Parallel Groups**: 5 groups (12 parallel tasks total)
**Sequential Tasks**: 25 tasks (with dependencies)

---

## Critical Path

**T001 → T002 → T003 → T004-T006 → T007-T014 → T015-T018 → T019-T022 → T025-T027 → T028-T029 → T030 → T031-T032 → T033 → T034 → T035-T036 → T037**

**Estimated Completion**: 37 tasks following TDD principles with mandatory DataLoader implementation before resolvers.
