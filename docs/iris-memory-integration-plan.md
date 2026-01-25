# IRIS Vector Graph + Memory Systems Integration Plan

**Date**: 2026-01-18
**Goal**: Integrate IRIS vector/graph capabilities with advanced memory systems and compound knowledge base

## 🎯 Vision

Combine three powerful concepts:
1. **IRIS Graph KG** (from kg-ticket-resolver) - Vector search + graph relationships
2. **Brain-Inspired Memory** (Larimar, KV-cache systems) - Episodic memory with efficient retrieval
3. **Compound Knowledge Base** (from oh-my-opencode-slim-compound) - Accumulated learnings

Create a system where:
- Agents remember past conversations (episodic memory)
- Knowledge compounds over time (solution patterns)
- Graph relationships reveal hidden connections
- Vector search enables semantic similarity
- Memory retrieval is hippocampus-style (keys) + neocortex-style (values)

## 📚 Research Findings

### 1. IRIS Graph Usage in kg-ticket-resolver

**Key Components:**

#### IRISVectorClient (`utils/clients/iris_vector_client.py`)
- **REST API Client** for IRIS Vector Search
- **Features**:
  - `search_similar_tickets()` - Semantic similarity search
  - `get_vector_embedding()` - Text → vector embeddings
  - `add_ticket_to_vector_index()` - Index new documents
  - `find_similar_entities()` - Entity-based similarity

**Configuration**:
```python
{
    "enabled": True,
    "url": "http://localhost:8080/api/v1",
    "namespace": "USER",
    "collection": "ticket_embeddings",
    "username": "...",
    "password": "..."
}
```

#### IRISVectorMemoryClient (`utils/clients/iris_vector_memory_client.py`)
- **LangGraph Integration** for agent memory
- **Memory Types**:
  - `query` - Past queries and results
  - `strategy` - Successful strategies
  - `reflection` - Agent reflections

**Key Methods**:
```python
# Store memories
await memory.remember_query(agent_name, query, results, metadata)
await memory.remember_strategy(agent_name, strategy_name, strategy_data)
await memory.remember_reflection(agent_name, reflection_text, subject)

# Retrieve memories
await memory.get_similar_queries(agent_name, query_text, limit=3)
await memory.get_similar_reflections(agent_name, subject, limit=3)
await memory.get_relevant_strategies(agent_name, query_context, limit=3)
```

**Architecture**:
- Uses **Solr MCP** for document storage
- Uses **embeddings service** for vector generation
- Stores memories with metadata: `{memory_type, agent, content, timestamp, embedding}`

### 2. Brain-Inspired Memory Systems

#### SimpleMem: Semantic Lossless Compression (aiming-lab/SimpleMem, 1.3k ⭐)
**Concept**: "Efficient Lifelong Memory for LLM Agents" through semantic compression

**Three-Stage Pipeline**:

1. **Semantic Structured Compression**
   - Transforms raw dialogue into "atomic entries"
   - Self-contained facts with resolved references
   - Absolute timestamps (no ambiguity)
   - Example: "He'll meet Bob tomorrow" → "Alice meets Bob at Starbucks on 2025-11-16T14:00:00"

2. **Structured Multi-View Indexing**
   - **Semantic**: Dense vector embeddings (1024-d) for conceptual similarity
   - **Lexical**: Sparse BM25-style indices for exact term matching
   - **Symbolic**: Metadata filtering (timestamps, entities, persons)

3. **Complexity-Aware Adaptive Retrieval**
   - Simple queries: Minimal headers (~100 tokens)
   - Complex queries: Detailed atomic contexts (~1000 tokens)
   - Dynamic depth based on query complexity

**Performance** (LoCoMo-10 benchmark):
- 43.24% F1 (+26.4% vs Mem0, +75.6% vs LightMem)
- 388.3s retrieval (32.7% faster than LightMem)
- 12.5× faster total processing than A-Mem

**MCP Integration**: Available at `mcp.simplemem.cloud`

#### MemoBrain: Executive Memory for Reasoning (arXiv:2601.08079)
**Concept**: "Executive Memory as an Agentic Brain" - Active cognitive control vs passive accumulation

**Dependency-Aware Memory Graph**:
- Each reasoning episode → compact "thought" unit
- Encodes: subproblem, tools used, conclusions
- Discards: transient execution artifacts
- Builds global dependency graph

**Executive Operations**:
- **FOLD**: Collapse resolved sub-trajectories into summary thoughts
- **FLUSH**: Replace low-utility thoughts with compact representations
- **PRUNE**: Remove invalid reasoning steps

**Key Innovation**: Explicit cognitive control over reasoning trajectories
- Maintains coherent reasoning across long-horizon tasks
- Preserves high-salience reasoning backbone under fixed context budget
- Tested on GAIA, WebWalker, BrowseComp-Plus benchmarks

#### MemOS: Memory Operating System (arXiv:2507.03724)
**Concept**: "Memory OS for AI System" - Treats memory as manageable system resource

**MemCube Abstraction**:
- Encapsulates memory content + metadata (provenance, versioning)
- Can be composed, migrated, fused over time
- Enables transitions between memory types

**Unified Memory Framework**:
- Plaintext memory (structured knowledge fragments)
- Activation memory (contextual inference state)
- Parameter memory (knowledge in model weights)

**Performance** (LoCoMo benchmark):
- 159% improvement in temporal reasoning vs OpenAI global memory
- 38.97% accuracy gain
- 60.95% reduction in token overhead

#### Larimar Episodic Memory (from X bookmark)
**Concept**: "Your brain doesn't erase memories — it just loses the keys that unlock them"

**Key-Value Architecture**:
- **Keys** → Hippocampus (quick access, efficient retrieval)
- **Values** → Neocortex (high-fidelity storage)
- Uses **autoencoders (AEs)** to pass memory in latent space

**Challenge**: "Memories became finicky with multiple slots"

**Application**:
- Fast retrieval keys (semantic embeddings)
- Detailed storage values (full context)
- Episodic chunks (conversation segments)

### 3. Compound Knowledge Base

**From oh-my-opencode-slim-compound**:

**Structure**:
```
~/.config/opencode/compound-knowledge/
├── solutions/              # 9 categories
│   ├── build-errors/
│   ├── integration-issues/
│   └── ... (7 more)
├── patterns/
│   ├── successful/
│   └── anti-patterns/
└── learnings/
    ├── architecture/
    ├── frameworks/
    └── tools/
```

**Solution Documents**:
- YAML frontmatter (category, tags, severity, time_to_solve)
- Problem symptom
- Investigation steps
- Root cause
- Solution with code examples
- Prevention strategies
- Related solutions (cross-references)

**Time Savings**: First time 90 min → Next time 10 min (89% reduction)

## 🏗️ Proposed Integration Architecture

### Layer 1: IRIS Vector/Graph Foundation

**Purpose**: Dual storage - vector similarity + graph relationships

**Components**:
1. **IRIS Vector Store**
   - Embedding-based similarity search
   - Collections: `agent_memories`, `compound_solutions`, `patterns`
   - Fast semantic retrieval

2. **IRIS Graph Store**
   - Entity-relationship modeling
   - Node types: `Solution`, `Pattern`, `Memory`, `Agent`, `Concept`
   - Edge types: `RELATED_TO`, `DERIVED_FROM`, `USED_BY`, `SIMILAR_TO`

**Why Both**:
- **Vector** for "find similar problems"
- **Graph** for "what's connected to this solution"

### Layer 2: Hybrid Memory System (SimpleMem + MemoBrain + Larimar)

**Concept**: Combine SimpleMem's compression, MemoBrain's executive control, and Larimar's KV architecture

#### SimpleMem Integration: Semantic Compression Layer
```python
class AtomicMemoryEntry:
    """Compressed, self-contained fact with resolved references"""
    content: str                # Disambiguated, absolute fact
    timestamp: datetime         # Absolute timestamp
    entities: List[str]         # Extracted entities

    # Multi-view indices
    semantic_embedding: List[float]  # 1024-d vector
    lexical_tokens: List[str]        # BM25 sparse index
    symbolic_metadata: Dict          # Entity/person/time filters

class SimpleMem CompressorLayer:
    def compress_dialogue(self, raw_text: str) -> AtomicMemoryEntry:
        """Transform: 'He'll meet Bob tomorrow' → atomic entry"""
        return AtomicMemoryEntry(
            content="Alice meets Bob at Starbucks on 2025-11-16T14:00:00",
            timestamp=datetime(2025, 11, 16, 14, 0),
            entities=["Alice", "Bob", "Starbucks"]
        )

    def adaptive_retrieve(self, query: str, complexity: str) -> List[MemoryEntry]:
        """Simple queries: headers only (~100 tokens)
           Complex queries: full atomic context (~1000 tokens)"""
        if complexity == "simple":
            return self.retrieve_headers(query, limit=5)
        else:
            return self.retrieve_full_context(query, limit=3)
```

#### MemoBrain Integration: Executive Memory Control
```python
class ThoughtUnit:
    """Compact reasoning episode abstraction"""
    subproblem: str             # What was being solved
    tools_used: List[str]       # Tools invoked
    conclusion: str             # Final result
    dependencies: List[str]     # Dependent thought IDs
    salience: float            # Importance score

class ExecutiveMemoryController:
    """Active cognitive control over reasoning trajectories"""

    def fold(self, trajectory: List[ThoughtUnit]) -> ThoughtUnit:
        """Collapse resolved sub-trajectories into summary"""
        return ThoughtUnit(
            subproblem="Completed: " + trajectory[0].subproblem,
            tools_used=[],
            conclusion=trajectory[-1].conclusion,
            dependencies=[t.id for t in trajectory]
        )

    def flush(self, thoughts: List[ThoughtUnit], budget: int) -> List[ThoughtUnit]:
        """Replace low-utility thoughts with compact representations"""
        # Sort by salience, keep top N under budget
        return sorted(thoughts, key=lambda t: t.salience, reverse=True)[:budget]

    def prune(self, thoughts: List[ThoughtUnit]) -> List[ThoughtUnit]:
        """Remove invalid reasoning steps"""
        return [t for t in thoughts if t.conclusion != "ERROR"]
```

#### Larimar Integration: Hippocampus/Neocortex KV Architecture
```python
class MemoryKey:
    """Hippocampus: Fast retrieval keys"""
    embedding: List[float]      # Vector for similarity
    memory_type: str            # query, strategy, reflection, solution, thought
    agent: str                  # Which agent owns this
    timestamp: datetime         # When created
    summary: str                # Brief description (compressed via SimpleMem)
    tags: List[str]             # Searchable tags
    salience: float            # MemoBrain importance score

class MemoryValue:
    """Neocortex: High-fidelity storage"""
    full_content: Dict          # Complete data
    atomic_entries: List[AtomicMemoryEntry]  # SimpleMem compressed facts
    reasoning_graph: List[ThoughtUnit]       # MemoBrain thought dependencies
    context: Dict               # Surrounding context
    metadata: Dict              # Additional info
    cross_references: List[str] # Related memory IDs
```

**Storage**:
- **Keys** → IRIS Vector (fast similarity search)
- **Values** → IRIS SQL/Object store (detailed retrieval)

#### Unified Retrieval Flow:
1. Query comes in (e.g., "How do I fix auth timeout?")
2. Generate embedding → Search hippocampus (keys)
3. Get top K similar keys
4. Fetch full values from neocortex
5. Return memories with similarity scores

### Layer 3: Compound Knowledge Integration

**Purpose**: Bridge compound KB with IRIS memory system

#### Indexing Pipeline:
```python
# When solution is documented
solution = {
    "title": "OneNote Graph API Permissions",
    "category": "integration-issues",
    "tags": ["microsoft-graph", "onenote", "azure-ad"],
    "content": "...",  # Full markdown
    "code_examples": ["..."],
    "time_to_solve": "90min"
}

# 1. Create hippocampus key
key = create_memory_key(
    embedding=embed(solution["title"] + " " + solution["tags"]),
    memory_type="solution",
    summary=solution["title"],
    tags=solution["tags"]
)

# 2. Store in IRIS vector
await iris.store_memory_key(key)

# 3. Store full value in IRIS
await iris.store_memory_value(solution)

# 4. Create graph relationships
await iris.create_solution_node(solution)
await iris.create_relationships(
    solution_id=solution["id"],
    related_patterns=[...],
    related_solutions=[...]
)
```

#### Query Pipeline:
```python
# User asks: "How to handle OneNote permissions?"

# 1. Search hippocampus (IRIS vector)
similar_keys = await iris.search_memory_keys(
    query="OneNote permissions",
    memory_type="solution",
    limit=5
)

# 2. Fetch neocortex values
solutions = []
for key in similar_keys:
    value = await iris.get_memory_value(key.id)
    solutions.append({
        "similarity": key.score,
        "solution": value,
        "key": key
    })

# 3. Enhance with graph relationships
for solution in solutions:
    related = await iris.get_related_solutions(solution["solution"]["id"])
    solution["related"] = related

return solutions
```

### Layer 4: Agent Memory System

**Purpose**: Per-agent episodic memory like kg-ticket-resolver

**Agent Memory Types**:
1. **Query Memories**
   - Past user queries
   - Retrieved solutions
   - Success/failure outcomes

2. **Strategy Memories**
   - What approaches worked
   - What failed and why
   - Context where strategy applied

3. **Reflection Memories**
   - Agent's self-analysis
   - Learning insights
   - Improvement notes

4. **Compound Solution Memories**
   - Documented solutions
   - Pattern extractions
   - Cross-project learnings

**Storage**:
```python
# Hippocampus (keys)
agent_memory_keys = IRISCollection("agent_memory_keys")

# Neocortex (values)
agent_memory_values = IRISCollection("agent_memory_values")

# Graph relationships
Agent -[REMEMBERS]-> Memory
Memory -[RELATED_TO]-> Solution
Memory -[APPLIED]-> Pattern
```

## 🔧 Implementation Plan

### Phase 1: IRIS Foundation (Week 1)

**Tasks**:
1. Set up IRIS instance (local or cloud)
2. Create vector collections:
   - `compound_solution_keys`
   - `agent_memory_keys`
   - `pattern_keys`

3. Create graph schema:
   - Node types: `Solution`, `Pattern`, `Memory`, `Agent`
   - Relationship types: `RELATED_TO`, `DERIVED_FROM`, `USED_BY`

4. Port IRIS clients from kg-ticket-resolver:
   - `IRISVectorClient` → `iris_vector_client.py`
   - `IRISVectorMemoryClient` → `iris_memory_client.py`

**Deliverables**:
- `config/iris_config.yaml`
- `utils/iris_vector_client.py`
- `utils/iris_memory_client.py`
- `utils/iris_graph_client.py`

### Phase 2: Memory System (Week 2)

**Tasks**:
1. Implement Larimar-style KV architecture:
   - `MemoryKey` class (hippocampus)
   - `MemoryValue` class (neocortex)

2. Create memory managers:
   - `HippocampusManager` (fast retrieval)
   - `NeocortexManager` (detailed storage)

3. Implement retrieval functions:
   - `search_similar_memories(query, memory_type, limit)`
   - `get_related_memories(memory_id)`
   - `retrieve_memory_with_context(memory_id)`

4. Add agent memory support:
   - Per-agent memory spaces
   - Memory types: query, strategy, reflection, solution

**Deliverables**:
- `memory/hippocampus_manager.py`
- `memory/neocortex_manager.py`
- `memory/agent_memory.py`

### Phase 3: Compound KB Integration (Week 3)

**Tasks**:
1. Index existing compound KB:
   - Parse YAML frontmatter
   - Extract embeddings
   - Create IRIS keys
   - Store full values

2. Create indexing pipeline:
   - `index_solution(solution_doc)`
   - `index_pattern(pattern_doc)`
   - `index_learning(learning_doc)`

3. Build query interface:
   - `search_solutions(query, tags, category)`
   - `find_related_patterns(solution_id)`
   - `get_solution_history(solution_id)`

4. Implement graph relationships:
   - Link solutions to patterns
   - Cross-reference related solutions
   - Track pattern usage

**Deliverables**:
- `compound/indexer.py`
- `compound/query.py`
- `compound/graph_builder.py`

### Phase 4: oh-my-opencode-slim Integration (Week 4)

**Tasks**:
1. Enhance `workflow-planner` agent:
   - Query IRIS instead of grep
   - Use vector similarity for solution search
   - Fetch graph relationships

2. Enhance `workflow-compounder` agent:
   - Index solutions to IRIS
   - Create graph nodes
   - Link related memories

3. Add memory-aware agents:
   - Agents remember past interactions
   - Query similar past queries
   - Learn from strategies

4. Create dashboard:
   - Visualize memory graph
   - Show compound returns
   - Track agent learning

**Deliverables**:
- Enhanced `workflow-planner.ts`
- Enhanced `workflow-compounder.ts`
- `memory-aware-agent-mixin.ts`
- `compound-dashboard/` (web UI)

## 🤝 Team Collaboration Strategy

### Division of Responsibilities

#### kg-ticket-resolver Team: Infrastructure & Tooling
**Ownership**: IRIS deployment, memory clients, GraphRAG engine

**Deliverables**:
1. **IRIS Infrastructure**
   - Maintain IRIS instance (version, performance, backups)
   - Manage vector collections and graph schemas
   - Monitor storage, query performance, scaling
   - Handle upgrades and migrations

2. **Core Memory Clients** (Python libraries)
   - `IRISVectorClient` - REST API wrapper for vector search
   - `IRISVectorMemoryClient` - LangGraph integration for agent memory
   - `IRISGraphClient` - Graph traversal and relationship queries
   - `SimpleMem IntegrationClient` - Semantic compression layer
   - `MemoBrainController` - Executive memory operations

3. **GraphRAG Engine**
   - EngineeringAnalystAgent integration
   - Knowledge graph construction from tickets/docs
   - Entity extraction and relationship mapping
   - Graph-enhanced retrieval pipelines

4. **API Documentation & SDKs**
   - REST API specs (OpenAPI/Swagger)
   - Client library documentation
   - Example notebooks and tutorials
   - Performance benchmarks

5. **MCP Server** (optional)
   - IRIS MCP server for agent integration
   - Memory operations via MCP protocol
   - Compatible with Claude, Cursor, etc.

#### Your Team (oh-my-opencode-slim-compound): Agent Integration & KB Management
**Ownership**: OpenCode agents, compound KB, workflow orchestration

**Deliverables**:
1. **oh-my-opencode-slim Agent Enhancements**
   - Integrate IRIS clients into workflow-planner agent
   - Integrate IRIS clients into workflow-compounder agent
   - Add memory-aware agent mixin for all agents
   - Orchestrator memory management

2. **Compound Knowledge Base Integration**
   - Index existing KB into IRIS (one-time migration)
   - Auto-indexing pipeline (new solutions → IRIS)
   - Graph relationship building from YAML frontmatter
   - Cross-reference resolution

3. **Agent Memory Management**
   - Per-agent episodic memory stores
   - Query/strategy/reflection memory APIs
   - Memory lifecycle policies (retention, archiving)
   - Privacy and scoping rules

4. **Dashboard & Monitoring**
   - Compound returns visualization
   - Memory graph explorer
   - Agent learning metrics
   - Time savings tracking

5. **User Workflows**
   - CLI commands for KB search
   - Agent prompts for memory operations
   - Documentation and examples
   - User guides and best practices

### Integration Points

#### 1. Client Library Import
```python
# oh-my-opencode-slim agent
from kg_ticket_resolver.utils.clients import (
    IRISVectorClient,
    IRISVectorMemoryClient,
    IRISGraphClient
)

# Initialize in workflow-planner agent
iris_vector = IRISVectorClient(config)
solutions = await iris_vector.search_similar_tickets(
    query_text="OneNote permissions error",
    limit=5
)
```

#### 2. Shared Configuration
```yaml
# config/iris_config.yaml (shared format)
iris:
  enabled: true
  url: "http://iris-server:8080/api/v1"
  namespace: "COMPOUND_KB"
  collections:
    solutions: "compound_solutions"
    memories: "agent_memories"
    patterns: "successful_patterns"
  auth:
    username: "${IRIS_USERNAME}"
    password: "${IRIS_PASSWORD}"
```

#### 3. Memory Schema Agreement
```python
# Shared schema defined by kg-ticket-resolver team
class CompoundMemory:
    id: str
    memory_type: str  # solution, pattern, query, strategy, reflection, thought
    agent: Optional[str]
    content: Dict
    embedding: List[float]
    metadata: Dict
    timestamp: datetime
    tags: List[str]

# Your team uses this schema in agents
memory = CompoundMemory(
    memory_type="solution",
    content=solution_doc,
    tags=["oauth", "authentication"],
    ...
)
await iris_client.store_memory(memory)
```

### Communication Channels

**1. Shared Repository**
- Mono-repo or separate repos with clear boundaries
- `iris-clients` package published to PyPI by kg-ticket-resolver team
- oh-my-opencode-slim imports `iris-clients` as dependency

**2. API Contracts**
- REST API specifications (OpenAPI)
- Python client library interface contracts
- Memory schema definitions (JSON Schema)
- Versioning strategy (semantic versioning)

**3. Regular Sync Meetings**
- Weekly: Infrastructure updates, API changes, blockers
- Monthly: Performance reviews, scaling planning
- Ad-hoc: Breaking changes, urgent issues

**4. Documentation**
- kg-ticket-resolver team: README for IRIS setup, client library docs
- Your team: README for agent integration, user workflows
- Shared: Integration examples, troubleshooting guide

### Dependency Management

```toml
# oh-my-opencode-slim-compound pyproject.toml
[tool.uv]
dependencies = [
    "iris-clients>=1.0.0",  # Delivered by kg-ticket-resolver team
    # ... other dependencies
]
```

**Version Pinning Strategy**:
- Use `>=1.0.0,<2.0.0` for minor updates (non-breaking)
- Coordinate major version upgrades (breaking changes)
- Test against kg-ticket-resolver staging before production

### Testing Strategy

**kg-ticket-resolver Team**:
- Unit tests for IRIS clients
- Integration tests against IRIS instance
- Performance benchmarks
- API contract tests

**Your Team**:
- Unit tests for agent memory operations (mocked IRIS clients)
- Integration tests against kg-ticket-resolver staging IRIS
- End-to-end workflow tests
- User acceptance tests

**Shared Testing**:
- Compatibility test suite (both teams run same tests)
- Staging environment with shared IRIS instance
- Smoke tests after deployments

### Deployment & Operations

**IRIS Infrastructure** (kg-ticket-resolver team):
- Deploy IRIS instance (cloud or on-prem)
- Monitor performance (latency, throughput, errors)
- Handle backups and disaster recovery
- Scale resources as needed

**oh-my-opencode-slim** (your team):
- Deploy OpenCode with IRIS clients
- Configure connection to IRIS instance
- Monitor agent memory usage
- Handle KB indexing and updates

**Shared Observability**:
- Metrics: Query latency, memory operations/sec, storage usage
- Logs: Structured logging with correlation IDs
- Alerts: SLA violations, error rate thresholds
- Dashboards: Grafana/Datadog for both teams

### Migration Path

**Phase 1: Setup (Week 1)**
- kg-ticket-resolver: Deploy IRIS, publish `iris-clients` v0.1.0
- Your team: Install `iris-clients`, test connection

**Phase 2: Initial Integration (Week 2)**
- kg-ticket-resolver: Deliver `IRISVectorClient` with docs
- Your team: Integrate into workflow-planner for read-only KB search

**Phase 3: Indexing Pipeline (Week 3)**
- Your team: Build KB → IRIS indexing script
- kg-ticket-resolver: Support with schema validation, performance tuning

**Phase 4: Memory Operations (Week 4)**
- kg-ticket-resolver: Deliver `IRISVectorMemoryClient` for agent memory
- Your team: Integrate into workflow-compounder for write operations

**Phase 5: GraphRAG Integration (Month 2+)**
- kg-ticket-resolver: Deliver `IRISGraphClient` and EngineeringAnalystAgent integration
- Your team: Add graph traversal to workflow-planner
- Both: Collaborate on graph schema and relationship types

### Success Metrics

**Shared KPIs**:
- Query latency: <100ms p95 for vector search
- Indexing throughput: >1000 documents/min
- Storage efficiency: <50MB per 1000 solutions
- Uptime: 99.9% IRIS availability

**kg-ticket-resolver Team**:
- Client library adoption rate
- API error rate (<0.1%)
- Documentation completeness

**Your Team**:
- Time savings vs grep-based search (target: 90%)
- Agent memory utilization rate
- Compound returns (solutions reused)

## 📊 Expected Benefits

### 1. Faster Problem Solving
**Current**: grep-based search → Linear scan
**New**: Vector similarity → Instant semantic match

**Example**:
- Query: "OAuth permissions error"
- IRIS finds: OneNote Graph API solution (89% similar)
- Time: 2 seconds vs 30 seconds grep

### 2. Hidden Connection Discovery
**Current**: Manual cross-referencing
**New**: Graph traversal reveals relationships

**Example**:
- View OneNote solution
- Graph shows: 3 related Azure AD patterns
- Discover: Common permission architecture

### 3. Agent Learning
**Current**: Agents start fresh each session
**New**: Agents remember and improve

**Example**:
- Agent solves authentication bug
- Stores strategy: "Check admin consent first"
- Next auth bug: Agent tries admin consent immediately

### 4. Cross-Project Knowledge
**Current**: Knowledge siloed per project
**New**: Solutions accessible everywhere

**Example**:
- Fix in Project A documented
- Working in Project B, similar issue
- IRIS surfaces Project A solution automatically

### 5. Compounding Accelerated
**Current**: 90 min → 10 min (89% reduction)
**New**: 90 min → 2 min (98% reduction)

**Why**: Vector search faster than grep, graph shows related solutions instantly

## 🚀 Quick Start

### Minimal Viable Integration

**Goal**: Get IRIS + Compound KB working in 1 day

**Steps**:

1. **Install IRIS** (30 min)
   ```bash
   docker run -d -p 52773:52773 intersystemsdc/iris-community
   ```

2. **Create Vector Client** (1 hour)
   ```python
   # utils/iris_simple_client.py
   import requests

   class IRISSimpleClient:
       def search_solutions(self, query):
           # Embed query
           # Search IRIS vector
           # Return top 5
           pass
   ```

3. **Index Compound KB** (2 hours)
   ```python
   # scripts/index_compound_kb.py
   # Read ~/.config/opencode/compound-knowledge/solutions/**/*.md
   # Parse YAML frontmatter
   # Store in IRIS
   ```

4. **Update workflow-planner** (2 hours)
   ```typescript
   // Instead of grep, call IRIS
   const solutions = await iris.search_solutions(query);
   ```

5. **Test** (2 hours)
   - Index 2-3 existing solutions
   - Query for similar problem
   - Verify IRIS returns correct solution
   - Measure time improvement

**Total**: 1 work day for proof of concept

## 📝 Next Steps

1. **Decide on IRIS deployment**:
   - Local Docker?
   - Cloud instance?
   - InterSystems Cloud?

2. **Choose embedding model**:
   - OpenAI `text-embedding-3-small`?
   - Sentence transformers (local)?
   - Vertex AI embeddings?

3. **Define memory retention**:
   - Keep all memories forever?
   - Prune old memories?
   - Archive by age/relevance?

4. **Create proof of concept**:
   - Follow Quick Start above
   - Measure actual time savings
   - Validate architecture

5. **Iterate based on results**:
   - Add features incrementally
   - Document what works
   - Compound the learnings!

---

**Status**: Research complete, ready for implementation
**Next**: Choose IRIS deployment and create POC
**Time Estimate**: Full implementation 4 weeks, POC 1 day
