# IRIS Memory Integration - kg-ticket-resolver Team Handover

**Date**: 2026-01-18
**To**: kg-ticket-resolver development team
**From**: oh-my-opencode-slim-compound team
**Purpose**: Collaborate on IRIS-based memory system for compound engineering

---

## 🎯 Vision

Combine your GraphRAG + IRIS expertise with our compound engineering workflow to create an intelligent memory system where:

- Agents remember past conversations (episodic memory)
- Knowledge compounds over time (solution patterns)
- Graph relationships reveal hidden connections
- Vector search enables semantic similarity
- Memory improves agent reasoning

## 🤝 Your Discovery Aligns Perfectly

You independently discovered compound engineering and built an **EngineeringAnalystAgent** with GraphRAG. This is **exactly** the right direction! Your approach is superior to simple documentation because:

1. **Graph relationships** > flat documents (reveals connections)
2. **Vector similarity** > keyword search (semantic understanding)
3. **Structured knowledge** > unstructured notes (queryable, composable)

## 📋 Proposed Division of Responsibilities

### Your Team: Infrastructure & Tooling

**What you own**:
- IRIS instance deployment and maintenance
- Memory client libraries (Python)
- GraphRAG engine and EngineeringAnalystAgent
- API documentation and SDKs
- Optional: MCP server for IRIS

**What you deliver**:

#### 1. IRIS Infrastructure
```
- Deploy IRIS instance (cloud or on-prem)
- Manage vector collections: compound_solutions, agent_memories, patterns
- Monitor performance: query latency, storage, scaling
- Handle backups, upgrades, disaster recovery
```

#### 2. Core Memory Clients (Python)

**a) IRISVectorClient** (you already have this!)
```python
class IRISVectorClient:
    """REST API wrapper for IRIS vector search"""

    def search_similar_tickets(self, query_text: str, limit: int = 5, min_score: float = 0.7):
        """Semantic similarity search"""

    def get_vector_embedding(self, text: str) -> List[float]:
        """Get embedding from IRIS"""

    def add_ticket_to_vector_index(self, ticket_id: str, ticket_text: str, metadata: Dict):
        """Index new document"""
```

**b) IRISVectorMemoryClient** (you already have this!)
```python
class IRISVectorMemoryClient:
    """LangGraph integration for agent memory"""

    async def remember_query(self, agent_name: str, query: str, results: Any, metadata: Dict):
        """Store query memory"""

    async def remember_strategy(self, agent_name: str, strategy_name: str, strategy_data: Any):
        """Store successful strategy"""

    async def remember_reflection(self, agent_name: str, reflection_text: str, subject: str):
        """Store agent reflection"""

    async def get_similar_queries(self, agent_name: str, query_text: str, limit: int = 3):
        """Retrieve similar past queries"""
```

**c) IRISGraphClient** (new)
```python
class IRISGraphClient:
    """Graph traversal and relationship queries"""

    def create_node(self, node_type: str, properties: Dict) -> str:
        """Create graph node (Solution, Pattern, Memory, Agent)"""

    def create_relationship(self, from_id: str, to_id: str, rel_type: str, properties: Dict):
        """Create edge (RELATED_TO, DERIVED_FROM, USED_BY)"""

    def get_related_nodes(self, node_id: str, rel_type: str, depth: int = 1) -> List[Dict]:
        """Traverse graph from node"""

    def find_shortest_path(self, start_id: str, end_id: str) -> List[Dict]:
        """Find connection path between nodes"""
```

**d) SimpleMem IntegrationClient** (new - optional)
```python
class SimpleMemClient:
    """Semantic lossless compression layer"""

    def compress_dialogue(self, raw_text: str, timestamp: datetime) -> AtomicMemoryEntry:
        """Transform: 'He'll meet Bob tomorrow' → absolute atomic entry"""

    def adaptive_retrieve(self, query: str, complexity: str) -> List[MemoryEntry]:
        """Simple queries: headers (~100 tokens)
           Complex queries: full context (~1000 tokens)"""
```

**e) MemoBrainController** (new - optional)
```python
class MemoBrainController:
    """Executive memory control for reasoning trajectories"""

    def fold(self, trajectory: List[ThoughtUnit]) -> ThoughtUnit:
        """Collapse resolved sub-trajectories into summary"""

    def flush(self, thoughts: List[ThoughtUnit], budget: int) -> List[ThoughtUnit]:
        """Replace low-utility thoughts under context budget"""

    def prune(self, thoughts: List[ThoughtUnit]) -> List[ThoughtUnit]:
        """Remove invalid reasoning steps"""
```

#### 3. GraphRAG Engine

Your existing EngineeringAnalystAgent work:
- Knowledge graph construction from tickets/docs
- Entity extraction and relationship mapping
- Graph-enhanced retrieval pipelines
- Document the API and integration points

#### 4. Package Publishing

```toml
# Publish to PyPI as 'iris-clients'
[project]
name = "iris-clients"
version = "1.0.0"
dependencies = [
    "requests>=2.31.0",
    "pydantic>=2.0.0",
    # your other deps
]
```

We'll import it:
```python
from iris_clients import IRISVectorClient, IRISVectorMemoryClient, IRISGraphClient
```

#### 5. Documentation

- REST API specs (OpenAPI/Swagger)
- Python client library API docs
- Example notebooks showing usage
- Performance benchmarks
- Troubleshooting guide

### Our Team: Agent Integration & Knowledge Base

**What we own**:
- oh-my-opencode-slim agent enhancements
- Compound knowledge base integration
- Agent memory management
- User workflows and CLI commands
- Dashboard for visualization

**What we do**:
- Import your `iris-clients` package
- Integrate into workflow-planner and workflow-compounder agents
- Index our existing compound KB (~/.config/opencode/compound-knowledge/) into IRIS
- Build auto-indexing pipeline (new solutions → IRIS)
- Create memory-aware agent mixin
- Build user-facing tools and documentation

## 🔄 Integration Points

### 1. Shared Configuration Format

```yaml
# config/iris_config.yaml (agree on this format)
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

### 2. Memory Schema Agreement

```python
# Define this in iris-clients package
class CompoundMemory:
    """Shared memory schema for all systems"""
    id: str
    memory_type: str  # solution, pattern, query, strategy, reflection, thought
    agent: Optional[str]  # Which agent owns this (None = global)
    content: Dict  # Flexible content based on memory_type
    embedding: List[float]  # Vector embedding
    metadata: Dict  # Additional metadata
    timestamp: datetime  # When created
    tags: List[str]  # Searchable tags
```

### 3. Graph Schema Agreement

**Node Types**:
- `Solution` - Documented problem solutions
- `Pattern` - Reusable patterns (successful/anti-patterns)
- `Memory` - Agent episodic memories
- `Agent` - Agent instances
- `Concept` - High-level concepts
- `Entity` - Named entities (people, systems, etc.)

**Relationship Types**:
- `RELATED_TO` - General relationship
- `DERIVED_FROM` - Pattern extracted from solution
- `USED_BY` - Agent used this memory/solution
- `SIMILAR_TO` - Semantic similarity
- `DEPENDS_ON` - Dependency relationship
- `RESOLVES` - Solution resolves problem

## 📅 Migration Path

### Phase 1: Setup (Week 1)
**kg-ticket-resolver**:
- [ ] Deploy IRIS instance (or provide access to existing)
- [ ] Create initial collections (compound_solutions, agent_memories)
- [ ] Publish `iris-clients` v0.1.0 to PyPI (or private registry)
- [ ] Provide configuration example

**Our team**:
- [ ] Install `iris-clients` package
- [ ] Test connection to IRIS instance
- [ ] Verify basic vector search works

### Phase 2: Initial Integration (Week 2)
**kg-ticket-resolver**:
- [ ] Document `IRISVectorClient` API
- [ ] Provide example: indexing + searching
- [ ] Support questions and issues

**Our team**:
- [ ] Integrate `IRISVectorClient` into workflow-planner
- [ ] Enable read-only KB search from agents
- [ ] Test semantic similarity vs grep

### Phase 3: Indexing Pipeline (Week 3)
**Our team**:
- [ ] Build script to index existing compound KB into IRIS
- [ ] Test indexing performance (should handle 100+ solutions)
- [ ] Verify search quality

**kg-ticket-resolver**:
- [ ] Support schema validation
- [ ] Performance tuning if needed
- [ ] Add bulk indexing API if helpful

### Phase 4: Memory Operations (Week 4)
**kg-ticket-resolver**:
- [ ] Document `IRISVectorMemoryClient` API
- [ ] Provide examples: per-agent memory stores
- [ ] Support integration questions

**Our team**:
- [ ] Integrate into workflow-compounder for write operations
- [ ] Auto-store solved problems
- [ ] Test memory retrieval

### Phase 5: GraphRAG Integration (Month 2+)
**kg-ticket-resolver**:
- [ ] Document `IRISGraphClient` API
- [ ] Share EngineeringAnalystAgent learnings
- [ ] Provide graph traversal examples

**Our team**:
- [ ] Add graph traversal to workflow-planner
- [ ] Build relationship visualization
- [ ] Test cross-solution discovery

## 🎨 Memory System Architecture

### Layer 1: IRIS Vector/Graph (your expertise!)
```
IRIS Vector Store                 IRIS Graph Store
├── semantic similarity          ├── Solution nodes
├── embeddings (1024-d)          ├── Pattern nodes
└── collections                  ├── Memory nodes
                                 ├── Agent nodes
                                 └── relationships
```

### Layer 2: Hybrid Memory (SimpleMem + MemoBrain + Larimar)
```
SimpleMem Compression             MemoBrain Executive Control
├── atomic entries               ├── thought units
├── multi-view indexing          ├── FOLD operation
└── adaptive retrieval           ├── FLUSH operation
                                 └── PRUNE operation

Larimar KV Architecture
├── Hippocampus (keys) → IRIS Vector
└── Neocortex (values) → IRIS SQL
```

### Layer 3: Compound KB Integration (our work)
```
~/.config/opencode/compound-knowledge/
├── solutions/ (9 categories)
├── patterns/ (successful/anti)
└── learnings/ (architecture/frameworks/tools)
     ↓
Auto-index to IRIS
     ↓
Graph relationships
     ↓
Agents search via your clients
```

## 📊 Success Metrics

**Shared KPIs**:
- Query latency: <100ms p95 for vector search
- Indexing throughput: >1000 documents/min
- Storage efficiency: <50MB per 1000 solutions
- Uptime: 99.9% IRIS availability

**Your metrics**:
- `iris-clients` adoption rate
- API error rate (<0.1%)
- Documentation completeness
- Performance benchmarks published

**Our metrics**:
- Time savings: 90% vs grep (89 min → 9 min)
- Agent memory utilization
- Compound returns (solutions reused)

## 💡 Research References

**SimpleMem**: aiming-lab/SimpleMem (1.3k ⭐)
- "Efficient Lifelong Memory for LLM Agents"
- Semantic lossless compression
- Multi-view indexing (semantic/lexical/symbolic)
- MCP integration available

**MemoBrain**: arXiv:2601.08079
- "Executive Memory as an Agentic Brain for Reasoning"
- Dependency-aware memory graph
- FOLD/FLUSH/PRUNE operations
- Long-horizon reasoning

**MemOS**: arXiv:2507.03724
- "Memory OS for AI System"
- MemCube abstraction
- Unifies plaintext/activation/parameter memories
- 159% improvement vs OpenAI memory

**Compound Engineering**: oh-my-opencode-slim-compound
- 89-94% time savings on repeated problems
- Knowledge base with YAML frontmatter
- workflow-planner + workflow-compounder agents

## 📞 Communication

**Meetings**:
- Weekly sync: Infrastructure updates, API changes, blockers
- Monthly review: Performance, scaling planning
- Ad-hoc: Breaking changes, urgent issues

**Channels**:
- Shared Slack channel (or equivalent)
- GitHub issues for bug reports
- Shared docs for API specs
- Email for formal changes

**Contacts**:
- Your team lead: [fill in]
- Our team lead: [fill in]
- IRIS infrastructure: [fill in]

## 🚀 Next Steps

**Immediate (This Week)**:
1. **Your team**: Review this document, provide feedback
2. **Your team**: Confirm IRIS instance availability
3. **Your team**: Share existing `IRISVectorClient` code
4. **Both teams**: Agree on memory schema
5. **Both teams**: Schedule weekly sync meeting

**Week 1**:
1. **Your team**: Publish `iris-clients` v0.1.0
2. **Our team**: Test connection and basic operations
3. **Both teams**: Align on configuration format

**Week 2-4**:
- Follow migration path (Phase 1-4)
- Regular syncs to unblock issues
- Documentation updates as we learn

## 📚 Full Technical Details

See complete integration plan: `/Users/tdyar/ws/iris-vector-graph/docs/iris-memory-integration-plan.md`

Includes:
- Research findings from your kg-ticket-resolver codebase
- Detailed architecture proposals
- Code examples for each layer
- 4-week implementation roadmap
- 1-day POC quick start guide
- Performance benchmarks
- Troubleshooting guide

---

**Questions?** Let's schedule a call to discuss!

**Excited to collaborate** on building the future of agent memory systems! 🚀
