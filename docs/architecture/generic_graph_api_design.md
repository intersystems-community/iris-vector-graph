# Generic Graph API Design

**Created**: 2025-10-02
**Status**: Proposal
**Context**: Current GraphQL implementation uses domain-specific types (Protein, Gene, Pathway). User feedback: "we are making a graph db, that is GOOD for biomedical, but still I think it should be an example app"

---

## Architecture Context: Multiple Query Engines

This repository implements **IRIS Vector Graph** - a hybrid vector + graph database with **multiple query interfaces**:

```
┌─────────────────────────────────────────────────────────────┐
│                    Query Engines (User-Facing)              │
├─────────────────────────────────────────────────────────────┤
│  openCypher API          │  GraphQL API     │  SQL API      │
│  (graph patterns)        │  (type-safe)     │  (direct)     │
│                          │                  │               │
│  MATCH (p:Protein)       │  protein(id) {   │  SELECT *     │
│  WHERE p.name='TP53'     │    name          │  FROM nodes   │
│  RETURN p                │  }               │  WHERE ...    │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Core Graph Database (Schema-Agnostic)          │
├─────────────────────────────────────────────────────────────┤
│  • NodePK Schema: nodes, rdf_edges, rdf_labels, rdf_props  │
│  • Vector Embeddings: kg_NodeEmbeddings with HNSW index    │
│  • FK Constraints: Referential integrity enforcement       │
│  • IRIS SQL: Native InterSystems IRIS storage engine       │
└─────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│               Domain Schemas (Optional Layers)              │
├─────────────────────────────────────────────────────────────┤
│  Biomedical    │  Social Network  │  E-commerce  │  Custom  │
│  (example)     │  (example)       │  (example)   │  (yours) │
└─────────────────────────────────────────────────────────────┘
```

**Key Insight**: openCypher and GraphQL are **query engines**, not the database itself. The database is **generic** (NodePK schema). Domain-specific types (Protein, Gene) are **optional convenience layers** that work the same way across both query engines.

---

## Problem Statement

The current GraphQL API is **hardcoded to biomedical entities**:
- `Protein`, `Gene`, `Pathway`, `Variant` types
- Biomedical-specific fields: `chromosome`, `organism`, `rsId`
- Biomedical relationships: `interactsWith`, `encodes`, `regulatedBy`

**Issue**: This limits **both GraphQL and openCypher** to biomedical use cases, even though the underlying database (NodePK schema with rdf_labels/rdf_props/rdf_edges) is **generic** and supports any domain.

**User Expectation**: Generic graph database with biomedical as an **example application**, not the core API.

**Impact on Query Engines**:
- **openCypher**: Currently assumes biomedical labels (`:Protein`, `:Gene`)
- **GraphQL**: Hardcoded to biomedical types (Protein, Gene, Pathway)
- **Both should support**: Any domain via generic Node/Edge model + domain examples

---

## Current Implementation (Domain-Specific)

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

  # Domain-specific fields
  name: String!
  function: String
  organism: String
  confidence: Float

  # Domain-specific relationships
  interactsWith(first: Int, offset: Int): [Protein!]!
  regulatedBy(first: Int, offset: Int): [Gene!]!
  participatesIn(first: Int, offset: Int): [Pathway!]!

  # Vector similarity
  similar(limit: Int, threshold: Float): [SimilarProtein!]!
}

type Gene implements Node { ... }
type Pathway implements Node { ... }
```

### Pros
✅ **Strong typing**: IDE autocomplete, compile-time validation
✅ **Domain-specific**: Perfect fit for biomedical apps
✅ **Simple queries**: `protein { name, function }` vs generic `node { properties }`
✅ **Self-documenting**: GraphQL Playground shows exact schema

### Cons
❌ **Not reusable**: Can't query non-biomedical entities without schema changes
❌ **Hardcoded relationships**: `interactsWith` only for proteins, not generic edges
❌ **Schema bloat**: Need new type for every domain (e.g., Movie, Person, Organization)
❌ **Maintenance burden**: Change entity types → change GraphQL schema

---

## Proposed Generic API (Option 1: Pure Generic)

### Schema Structure
```graphql
interface Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!
}

type GenericNode implements Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!

  # Generic property accessors
  property(key: String!): String

  # Generic relationships
  edges(
    type: String
    direction: EdgeDirection
    first: Int
    offset: Int
  ): [Edge!]!

  neighbors(
    relationshipType: String
    labels: [String!]
    first: Int
    offset: Int
  ): [GenericNode!]!

  # Vector similarity (generic)
  similar(limit: Int, threshold: Float): [SimilarNode!]!
}

type Edge {
  id: ID!
  source: GenericNode!
  target: GenericNode!
  type: String!
  properties: JSON
}

enum EdgeDirection {
  OUTGOING
  INCOMING
  BOTH
}

type Query {
  node(id: ID!): GenericNode
  nodes(
    labels: [String!]
    where: PropertyFilter
    first: Int
    offset: Int
  ): [GenericNode!]!

  search(
    query: String!
    labels: [String!]
    limit: Int
  ): [GenericNode!]!
}
```

### Example Queries (Generic)
```graphql
# Query protein by ID
query {
  node(id: "PROTEIN:TP53") {
    labels  # ["Protein"]
    properties  # {name: "Tumor protein p53", function: "..."}
    property(key: "name")  # "Tumor protein p53"

    # Generic neighbors
    neighbors(relationshipType: "INTERACTS_WITH", first: 5) {
      property(key: "name")
    }
  }
}

# Search nodes by label and property
query {
  nodes(labels: ["Protein"], where: {key: "organism", value: "Homo sapiens"}) {
    property(key: "name")
    property(key: "function")
  }
}
```

### Pros
✅ **Fully generic**: Works for any domain (biomedical, social network, e-commerce)
✅ **No schema changes**: Add new entity types by inserting data, not changing code
✅ **True graph database**: Reflects underlying NodePK schema directly
✅ **Flexible**: Query any property, any relationship, any label

### Cons
❌ **Weak typing**: `properties: JSON` loses type safety (no autocomplete for `name` field)
❌ **Verbose queries**: `property(key: "name")` vs `name`
❌ **Poor DX**: Clients must know property keys, no schema validation
❌ **No relationship names**: `neighbors(relationshipType: "INTERACTS_WITH")` vs `interactsWith`

---

## Hybrid Approach (Option 2: Generic + Domain Extensions)

### Core Generic Schema + Domain-Specific Extensions

**Strategy**: Generic `Node` interface + domain-specific types that extend it.

```graphql
# Generic core
interface Node {
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!

  # Generic accessors
  property(key: String!): String
  edges(type: String, direction: EdgeDirection): [Edge!]!
  neighbors(relationshipType: String, labels: [String!]): [Node!]!
  similar(limit: Int, threshold: Float): [SimilarNode!]!
}

# Domain-specific extension (biomedical)
type Protein implements Node {
  # Node interface fields
  id: ID!
  labels: [String!]!
  properties: JSON!
  createdAt: DateTime!
  property(key: String!): String
  edges(type: String, direction: EdgeDirection): [Edge!]!
  neighbors(relationshipType: String, labels: [String!]): [Node!]!
  similar(limit: Int, threshold: Float): [SimilarNode!]!

  # Domain-specific convenience fields (aliases for properties)
  name: String!  # Alias for property(key: "name")
  function: String  # Alias for property(key: "function")
  organism: String

  # Domain-specific relationships (aliases for neighbors)
  interactsWith(first: Int): [Protein!]!  # Alias for neighbors(relationshipType: "INTERACTS_WITH", labels: ["Protein"])
  regulatedBy(first: Int): [Gene!]!
}

type Gene implements Node { ... }

# Generic query
type Query {
  # Generic queries
  node(id: ID!): Node
  nodes(labels: [String!], where: PropertyFilter): [Node!]!

  # Domain-specific queries (convenience)
  protein(id: ID!): Protein
  gene(id: ID!): Gene
  pathway(id: ID!): Pathway
}
```

### Example Queries (Hybrid)
```graphql
# Use domain-specific type for biomedical
query {
  protein(id: "PROTEIN:TP53") {
    name           # Convenience field
    function
    interactsWith(first: 5) {  # Convenience relationship
      name
    }
  }
}

# Use generic type for custom domain
query {
  node(id: "MOVIE:Inception") {
    labels  # ["Movie"]
    property(key: "title")  # "Inception"
    neighbors(relationshipType: "ACTED_IN", labels: ["Actor"]) {
      property(key: "name")
    }
  }
}

# Mix both
query {
  nodes(labels: ["Protein"]) {
    ... on Protein {
      name  # Domain-specific field
    }
    property(key: "custom_annotation")  # Generic accessor
  }
}
```

### Pros
✅ **Best of both worlds**: Strong typing for known domains, generic for custom
✅ **Incremental adoption**: Start generic, add domain types as needed
✅ **Backward compatible**: Domain types are just convenience wrappers
✅ **Extensible**: Users can add their own domain types via plugins/config

### Cons
⚠️ **Schema duplication**: `name` field duplicates `property(key: "name")`
⚠️ **Complexity**: Two ways to query same data (generic vs domain-specific)
⚠️ **Maintenance**: Must keep domain fields in sync with property keys

---

## Cross-Query-Engine Domain Schemas

**Key Design Principle**: Domain schemas (biomedical, social network, etc.) should work **consistently across both openCypher and GraphQL**.

### Example: Biomedical Domain in Both Engines

**GraphQL Schema**:
```graphql
type Protein implements Node {
  id: ID!
  name: String!
  function: String
  interactsWith: [Protein!]!
}

query {
  protein(id: "PROTEIN:TP53") {
    name
    interactsWith { name }
  }
}
```

**Equivalent openCypher Query**:
```cypher
MATCH (p:Protein {id: "PROTEIN:TP53"})-[:INTERACTS_WITH]->(target:Protein)
RETURN p.name, collect(target.name) AS interactsWith
```

**Both map to**:
```sql
-- Underlying IRIS SQL (NodePK schema)
SELECT p.val AS name, t.val AS target_name
FROM rdf_props p
JOIN rdf_edges e ON e.s = p.s
JOIN rdf_props t ON t.s = e.o_id
WHERE p.s = 'PROTEIN:TP53'
  AND p.key = 'name'
  AND e.p = 'INTERACTS_WITH'
  AND t.key = 'name'
```

**Domain Schema Definition (Shared)**:
```yaml
# config/schemas/biomedical.yaml (used by BOTH engines)
types:
  - name: Protein
    label: Protein
    fields:
      name: {key: name, type: String!, required: true}
      function: {key: function, type: String}
    relationships:
      interactsWith: {type: INTERACTS_WITH, target: Protein}
```

**How It Works**:
1. **GraphQL**: Strawberry types auto-generated from YAML
2. **openCypher**: Parser maps `:Protein` label to `Protein` domain type
3. **Both**: Query underlying `rdf_labels`, `rdf_props`, `rdf_edges` tables

**Benefit**: Users define domain schema **once** in YAML, get both GraphQL types and openCypher labels automatically.

---

## Recommended Approach: **Hybrid with Configuration**

### Architecture (Query Engine Agnostic)

1. **Core Generic API** (always available):
   ```graphql
   type Query {
     node(id: ID!): Node
     nodes(labels: [String!], where: PropertyFilter): [Node!]!
   }

   interface Node {
     id: ID!
     labels: [String!]!
     properties: JSON!
     property(key: String!): String
     neighbors(relationshipType: String): [Node!]!
   }
   ```

2. **Domain Schema Configuration** (optional, loaded from config file):
   ```yaml
   # config/schemas/biomedical.yaml
   types:
     - name: Protein
       label: Protein
       fields:
         name: {key: name, type: String!, required: true}
         function: {key: function, type: String}
         organism: {key: organism, type: String}
       relationships:
         interactsWith: {type: INTERACTS_WITH, target: Protein}
         regulatedBy: {type: REGULATED_BY, target: Gene}

     - name: Gene
       label: Gene
       fields:
         name: {key: name, type: String!}
         chromosome: {key: chromosome, type: String}
   ```

3. **Auto-Generated Types** (from config):
   ```python
   # api/gql/schemas/biomedical.py (auto-generated from config)
   @strawberry.type
   class Protein:
       # Generated from config.types[0].fields
       name: str
       function: Optional[str] = None

       @strawberry.field
       async def interacts_with(self, info) -> List["Protein"]:
           # Generated from config.types[0].relationships
           return await resolve_neighbors(self.id, "INTERACTS_WITH", "Protein")
   ```

### Implementation Path

**Phase 1: Generic Core (1 week)**
- Implement `Node`, `Edge`, `Query.node()`, `Query.nodes()`
- Generic property accessors
- Generic `neighbors()` resolver
- Remove hardcoded `Protein`/`Gene` types

**Phase 2: Biomedical Example (1 week)**
- Create `examples/biomedical/` directory
- Move `Protein`/`Gene` types to examples
- Document as reference implementation
- Tests validate both generic + biomedical

**Phase 3: Configuration System (2 weeks)**
- YAML schema config parser
- Auto-generate Strawberry types from config
- Plugin system for domain schemas
- CLI: `iris-graph schema generate config/biomedical.yaml`

**Phase 4: Documentation (1 week)**
- Generic API docs (quickstart with generic queries)
- Biomedical example docs (how to use biomedical schema)
- Schema configuration guide (create your own domain)

---

## Decision Matrix

| Criterion | Pure Generic | Pure Domain-Specific | Hybrid (Recommended) |
|-----------|--------------|----------------------|----------------------|
| **Flexibility** | ✅ Supports any domain | ❌ Biomedical only | ✅ Generic + domain |
| **Type Safety** | ❌ Weak (JSON properties) | ✅ Strong typing | ✅ Strong for domains |
| **Developer UX** | ❌ Verbose queries | ✅ Simple queries | ✅ Choice of style |
| **Maintenance** | ✅ No schema changes | ❌ Schema per domain | ⚠️ Config-driven |
| **Discoverability** | ❌ Poor (no field names) | ✅ Self-documenting | ✅ Schema shows both |
| **Implementation** | ✅ Simple | ✅ Current state | ⚠️ Medium complexity |
| **Performance** | ✅ No overhead | ✅ No overhead | ✅ No overhead |

---

## Example: Biomedical as Plugin

**Directory Structure**:
```
iris-vector-graph/
├── api/
│   └── gql/
│       ├── core/              # Generic graph API
│       │   ├── types.py       # Node, Edge, Query (generic)
│       │   └── resolvers.py
│       └── schemas/           # Domain-specific schemas
│           └── biomedical/    # Biomedical example
│               ├── types.py   # Protein, Gene, Pathway
│               ├── config.yaml
│               └── README.md
├── examples/
│   ├── biomedical/            # Biomedical example app
│   │   ├── data/sample_proteins.json
│   │   ├── queries/protein_interactions.graphql
│   │   └── README.md
│   ├── social_network/        # Social network example
│   │   └── config.yaml
│   └── ecommerce/             # E-commerce example
│       └── config.yaml
```

**Usage**:
```bash
# Use generic API only
uvicorn api.main:app

# Use generic API + biomedical schema
uvicorn api.main:app --schema=biomedical

# Use generic API + custom schema
uvicorn api.main:app --schema=examples/social_network/config.yaml
```

---

## Migration Plan (From Current State)

### Step 1: Create Generic Core (No Breaking Changes)
- Add `Node.property(key)`, `Node.neighbors()` to existing `Protein`/`Gene` types
- Add `Query.node()` alongside `Query.protein()`
- Tests validate both generic + specific resolvers work

### Step 2: Move Domain Types to Plugin
- Create `api/gql/schemas/biomedical/`
- Move `Protein`, `Gene`, `Pathway` types to biomedical plugin
- Update imports, keep existing tests working

### Step 3: Document as Example
- Update README: "Biomedical is an example domain schema"
- Add docs: "Creating Your Own Schema" guide
- Add examples: social network, e-commerce

### Step 4: (Optional) Configuration System
- Only if users request custom domains
- Phase 3 roadmap item

---

## Recommendation

**Adopt Hybrid Approach** with biomedical as **example plugin**:

1. **Immediate (this PR)**:
   - Add generic `Node.property()`, `Node.neighbors()` methods
   - Keep `Protein`/`Gene` types (don't break existing tests)
   - Document in README: "Generic graph API with biomedical example"

2. **Next PR**:
   - Refactor `Protein`/`Gene` to `examples/biomedical/` plugin
   - Add social network example (Person, Organization, Relationship)
   - Prove generic API works for multiple domains

3. **Future**:
   - YAML config system for schema generation (if requested)
   - Schema marketplace/registry (if ecosystem grows)

**Why**: Validates technical approach (current work not wasted), enables any domain, maintains type safety for known domains.
