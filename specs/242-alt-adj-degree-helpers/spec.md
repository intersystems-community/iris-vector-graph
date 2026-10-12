<!-- markdownlint-disable MD013 -->

# Feature Specification: Adjacency degree helpers (Graph.KG.Adj)

**Feature Branch**: `alt/adj-degree-helpers` (exploratory, not adopted)
**Governing decisions**: ADR-0008, ADR-0010; CONTEXT.md terms Degree, Graph Key
**Evidence**: `docs/research/spikes/functional-index-adjacency/DEG-AUDIT.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Degree is counted from the adjacency, not stored (Priority: P1)

A caller asks for the number of outgoing edges of a node, optionally for one predicate, in one graph. The answer
is counted from the `^KG("out", g, s, ...)` entries, so a rolled-back write cannot leave a wrong number behind.

**Acceptance Scenarios**:

1. **Given** node `a` with two distinct targets, **When** `Degree(g, "a")` is called, **Then** it returns 2.
2. **Given** a multigraph triple `(a, KNOWS, b)` with three live edges (`"#" = 3`), **When** `Degree(g, "a")` is
   called, **Then** it returns 3 (the leaf multiplicity, not 1).
3. **Given** `a` has edges only under `KNOWS`, **When** `Degree(g, "a", "LIKES")` is called, **Then** it returns 0.
4. **Given** an empty source or a source with no entries, **When** `Degree` is called, **Then** it returns 0.
5. **Given** a stored `^KG("deg")` or `^KG("degp")` is absent or wrong, **When** `Degree` is called, **Then** the
   answer is unchanged, because neither is read.

### User Story 2 - Enumeration and existence come from the adjacency (Priority: P1)

Readers that list "which nodes have an outgoing edge" iterate the sources with `NextSource`, and readers that test
"does this node or predicate have edges" call `HasEdges`.

**Acceptance Scenarios**:

1. **Given** sources `a`, `b`, `c` in graph `g` and a sink `x`, **When** `NextSource` is iterated from `""`, **Then**
   it yields `a`, `b`, `c` and then `""`; `x` is not yielded.
2. **Given** an edge only in graph `g2`, **When** `NextSource(g, "")` is called, **Then** the result is `""`.
3. **Given** `a` has a `KNOWS` edge, **When** `HasEdges(g, "a")` and `HasEdges(g, "a", "KNOWS")` are called, **Then**
   both return 1, and `HasEdges(g, "a", "LIKES")` returns 0.

### User Story 3 - Analytics readers keep their values (Priority: P2)

PageRank (both variants), Subgraph PPR, Centrality, WCC, CDLP and the Subgraph existence check return the same
results as before, but no longer read `^KG("deg")` or `^KG("degp")`.

**Acceptance Scenarios**:

1. **Given** a graph with parallel edges, **When** PageRank runs, **Then** the divisor for each node is its edge
   count with multiplicity, as before the migration.
2. **Given** a graph, **When** degree centrality runs for direction `out`, **Then** each score is the counted
   out-degree divided by `n - 1`.

### Edge Cases

- A node that is only a target (sink) has degree 0 and is not a source.
- `pSrc = ""` is treated as "no node": `Degree` and `HasEdges` return 0 and do not reference the global.
- A predicate that does not exist returns 0 and `HasEdges` returns 0.
- Excluded predicates in PageRank (spec 235) keep their own kept-edge count; the exclusion logic is unchanged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `Graph.KG.Adj` declares exactly three ClassMethods: `Degree(pGraph, pSrc, pPred="") As %Integer`,
  `NextSource(pGraph, pSrc="") As %String`, `HasEdges(pGraph, pSrc, pPred="") As %Boolean`.
- **FR-002**: `Degree` never reads `^KG("deg")` or `^KG("degp")`. It sums, per target `o`, the `"#"` multiplicity
  when `$Data(^KG("out",g,s,p,o,"#"))` holds, otherwise 1.
- **FR-003**: `NextSource` is `Quit $Order(^KG("out", pGraph, pSrc))`.
- **FR-004**: `HasEdges` returns 1 when `^KG("out",g,s)` (or `^KG("out",g,s,p)` with a predicate) has data.
- **FR-005**: The four classes `Algorithms.cls`, `Centrality.cls`, `PageRank.cls`, `Subgraph.cls` read no stored
  counter. Existence and enumeration use `NextSource`/`HasEdges`; value readers use `Degree`, memoised per node
  inside the iteration so each node is counted once per call.
- **FR-006**: The graph subscript each class already uses is kept (`pGraph = 0` in Algorithms, Centrality and
  Subgraph; `GraphKey.ForIndex` in PageRank.Run).

### Key Entities

- **Adjacency entry**: `^KG("out", g, s, p, o)` leaf, with `^KG("out", g, s, p, o, "#")` as the live edge count
  when a triple has two or more edges.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The source scan finds no `^KG("deg"` or `^KG("degp"` outside comments in the four classes.
- **SC-002**: The Python reference model agrees with the row count for 40 random multigraphs (unit tests).
- **SC-003**: On IRIS (lead runs it): `Degree` equals the SQL row count per node and per predicate, and
  PageRank and Centrality equal values computed from the SQL rows (`tests/e2e/test_adj_degree_live.py`).
- **SC-004**: Nothing under `iris_src/src` outside the four classes and the new `Adj.cls` is changed.
