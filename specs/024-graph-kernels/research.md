# Research: Graph Analytics Kernels

**Phase 0 Output** | **Date**: 2026-03-19

No unknowns — all three algorithms are textbook graph kernels with a proven implementation pattern.

## Decision Log

### 1. Global PageRank: Uniform vs Seed-Biased
**Decision**: Add `PageRankGlobalJson` to existing `PageRank.cls` with uniform initialization.
**Rationale**: 95% code reuse from `RunJson`. Only change: `ranks(node) = 1/nodeCount` for all nodes instead of `1/|seeds|` for seed nodes.

### 2. WCC: Label Propagation vs Union-Find
**Decision**: Iterative label propagation (min-label adoption).
**Rationale**: Natural fit for `$ORDER` over `^KG`. Each node adopts minimum neighbor label. Converges in O(diameter) iterations. Union-find requires pointer chasing less natural over globals.

### 3. CDLP: Tie-Breaking Strategy
**Decision**: Most-frequent neighbor label wins. Ties broken by lexicographically smallest label.
**Rationale**: Matches LDBC Graphalytics CDLP specification. Smallest-label tie-breaking ensures deterministic results across runs.

### 4. WCC Bidirectional Traversal
**Decision**: Traverse both `^KG("out",node,p,o)` and `^KG("in",node,p,s)` for each node.
**Rationale**: "Weakly connected" means ignoring edge direction. A→B and B→A are the same component even if only one direction exists in the data.
