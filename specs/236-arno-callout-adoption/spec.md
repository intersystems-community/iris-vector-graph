# Feature Specification: Arno Callout Adoption

**Feature Branch**: `236-arno-callout-adoption`
**Created**: 2026-10-03
**Status**: Draft
**Input**: ivg adopts the new arno callout build (`arno-callout 1.1.0+3b2123b8e7bb`)
without dropping support for older builds. Background:
`docs/research/zf-heap-and-exstr.md`, `docs/research/arno-rzf-paths-forward.md`.
Glossary: **Owner**, **Native Reader**, **Reader Revision** in `CONTEXT.md`.

## Scope

### In scope

- Per-algorithm routing to a Native Reader (pagerank, ppr, wcc, cdlp, subgraph),
  gated on the Reader Revision the loaded library reports.
- An evidence gate that proves each Native Reader against its Owner before
  routing may use it, recorded in a committed proof file.
- A size gate so BFS never sends the Arno library an argument it cannot take.
- A shared, graph-scoped result cache for algorithm answers.
- Updating `docs/KNOWN_ISSUES.md`, which still describes the 2026-07-04 build.

### Out of scope

- ivg 4.1.1 deploy/recompile hardening (`IVG_SKIP_CLASS_DEPLOY`, the
  `kg_node_stores` skip list). Separate spec.
- Named-graph and inbound Rust BFS.
- Resident adjacency and EXSTR entries (follow-ups, asks to arno).
- An out-of-process Arno server and an mmap snapshot file.
- Tuning `zfheap`.
- Routing betweenness to a Native Reader. Betweenness keeps its exact directed
  Owner.

## User Scenarios & Testing _(mandatory)_

### User Story 1 - Old and new library builds both work (Priority: P1)

An operator runs ivg against whichever Arno library build is installed: an older
one with no capability report, a new one that reports Reader Revisions, or none
at all. Every algorithm call returns a correct answer in every case. A Native
Reader is used only where the library reports a revision at or above the
minimum ivg has proven for that algorithm. Otherwise the Owner answers.

**Why this priority**: It is the compatibility question that decides whether the
new build can ship at all. Old and new builds coexist in the field, so a wrong
default breaks existing installs.

**Independent Test**: Feed the routing decision each library report: no entry
(old build), an empty reader map, a reader below its proven revision, a reader
at the proven revision, a reader above it, and a malformed report. Assert the
chosen path for every algorithm, with no container and no library file.

**Acceptance Scenarios**:

1. **Given** a library with no capability entry, **When** any in-scope algorithm
   runs, **Then** the Owner answers and no error reaches the caller.
2. **Given** a library reporting pagerank at revision 1 and the proof file
   requiring 1, **When** PageRank runs on the default graph, **Then** the Native
   Reader answers.
3. **Given** a library reporting cdlp at revision 1 and the proof file requiring
   2, **When** CDLP runs, **Then** the Owner answers while other algorithms
   whose revisions are sufficient still use their Native Readers.
4. **Given** a library whose capability report is unparseable, **When** any
   algorithm runs, **Then** the Owner answers and a diagnostic is logged.
5. **Given** the library version string changes but every reader revision is
   unchanged, **When** algorithms run, **Then** routing is unchanged.

---

### User Story 2 - A Native Reader is used only after it is proven (Priority: P1)

A maintainer preparing a release runs a gate that compares each Native Reader
with its Owner across a fixed set of graphs and repeated calls. Passing
algorithms are recorded, with the library build they passed on, in a committed
proof file. Routing takes its minimum revisions only from that file. An
algorithm that fails stays on its Owner without blocking the rest.

**Why this priority**: The old workaround existed because the raw readers
disagreed with the Owners and with themselves across calls. Removing it without
evidence repeats that defect.

**Independent Test**: Run the gate against the installed library on the
enterprise container. Separately, tamper with the proof file (claim a higher
revision than the library reports) and assert the gate fails.

**Acceptance Scenarios**:

1. **Given** a path, a star, a dense cluster, a multi-component graph and a graph
   with deliberate score and label ties, **When** the gate runs, **Then** each
   in-scope algorithm is compared with its Owner on each graph.
2. **Given** a Native Reader called five times on one graph, **When** the five
   answers are compared, **Then** they are identical. Any difference fails that
   algorithm.
3. **Given** PageRank or PPR results, **When** compared with the Owner, **Then**
   the node sets match, each score is within 1e-6 relative, and ranking agrees
   wherever scores are not tied.
4. **Given** WCC results, **When** compared with the Owner, **Then** the
   partitions are identical regardless of component names.
5. **Given** subgraph results, **When** compared with the Owner, **Then** the node
   set and edge set are exactly equal.
6. **Given** CDLP results, **When** compared, **Then** five native calls are
   identical and partition similarity against the Owner meets the recorded
   threshold. A graph on which the Owner is itself unstable is dropped from the
   comparison and reported.
7. **Given** one algorithm fails its comparison, **When** the gate finishes,
   **Then** the proof file omits or holds back that algorithm and the others are
   still recorded.
8. **Given** the proof file claims a revision higher than the installed library
   reports, **When** the gate runs where the library should be present, **Then**
   it fails. It does not skip because the library is missing there.
9. **Given** a machine with no enterprise container, **When** the suite runs,
   **Then** the behavioural gate skips, as the existing Arno tests do, and the
   routing tests from Story 1 still run.

---

### User Story 3 - BFS never overruns the library's argument limit (Priority: P2)

A user runs a traversal on a graph whose adjacency is too large for one
library argument. The traversal returns the correct result through the
ObjectScript path instead of failing with a heap error or returning nothing.

**Why this priority**: The failure is real and measured. Arno's BFS entry
fails outright at 34,792 characters of adjacency, and the library rejects
anything from 65,536 up.

**Independent Test**: Build graphs whose serialized adjacency is just under,
exactly at, and just over the limit, and well past the hard cap. Assert that
each returns the ObjectScript answer and raises no heap error.

**Acceptance Scenarios**:

1. **Given** adjacency of 34,791 characters or fewer, **When** BFS runs on the
   default graph outbound, **Then** the existing Arno path may answer.
2. **Given** adjacency of 34,792 characters or more, **When** BFS runs, **Then**
   the Owner answers with the same result the Native path gives on a smaller
   graph of the same shape, and no heap error appears.
3. **Given** a named graph or an inbound or undirected traversal, **When** BFS
   runs, **Then** routing is unchanged from today.
4. **Given** a future library build whose `kg_bfs_compute` is proven correct,
   **When** this spec is revisited, **Then** adopting it is a new decision. It is
   not enabled by this spec.

---

### User Story 4 - Repeat calls come from a shared cache (Priority: P2)

A user or service asks for the same algorithm with the same parameters on the
same graph more than once. After the first answer, repeat calls return the
stored answer from any connection until the graph version changes. After any
change to that graph, the next call recomputes. The version is shared across
graphs, so a change to another graph may also cause a recompute, which is safe
but not minimal. `subgraph` answers are not cached. Nothing stale is ever
returned, including
after erasure.

**Why this priority**: It speeds Owners and Native Readers alike and is
independent of the library build. It is only safe if invalidation is proven for
every way a graph can change.

**Independent Test**: For each write path, run the algorithm, make the change,
run it again, and assert the answer reflects the change. Also assert a repeat
call with no change is served from the cache.

**Acceptance Scenarios**:

1. **Given** an algorithm run twice with no change between, **When** the second
   call returns, **Then** it is served from the cache and equals the first.
2. **Given** two separate connections, **When** one computes an answer, **Then**
   the other's identical request is a cache hit.
3. **Given** a change through any supported write path (SQL edge write,
   adjacency write, temporal insert, delete, erasure, snapshot restore, index
   rebuild), **When** the algorithm runs again, **Then** the answer reflects the
   change.
4. **Given** content that was erased, **When** any algorithm is run afterwards,
   **Then** no cached answer contains the erased content.
5. **Given** a write path that cannot update the graph-version token, **When**
   that path is used, **Then** the cache bypasses itself for that graph rather
   than risk a stale answer.
6. **Given** two different graphs, **When** each is queried, **Then** neither
   sees the other's cached answers.
7. **Given** different parameters for the same algorithm, **When** each is
   requested, **Then** each has its own entry.

---

### User Story 5 - Known issues describe the current build (Priority: P3)

A reader of `docs/KNOWN_ISSUES.md` finds the Arno section describing the
`1.1.0` build and what is verified, not the 2026-07-04 build.

**Why this priority**: Stale known-issues text sends people chasing fixed
defects and hides open ones.

**Independent Test**: Read the section; each entry names the build it was
verified against and the date.

**Acceptance Scenarios**:

1. **Given** the updated document, **When** read, **Then** the betweenness
   undirected behaviour, the BFS size limit, and the untested x86_64 build are
   each stated with the evidence that supports them.
2. **Given** an entry no longer true on the new build, **When** the document is
   updated, **Then** it moves to the ruled-out section with its evidence.

---

### Edge Cases

- The library loads but its capability call fails or hangs: treated as no
  readers.
- The capability report names a reader ivg does not know: ignored.
- A reader's revision is higher than the proof file's minimum: used.
- The proof file is missing or unparseable: every algorithm uses its Owner, and
  this is logged.
- A library is replaced while a process is running: the report is read again on
  the next load, not cached across a reload.
- The same algorithm requested with the cache disabled, or on a graph the cache
  has bypassed: computed every time, never stored stale.
- A cache entry's stored token differs from the current one: a miss, recomputed
  and overwritten.
- Erasure runs between a cache write and the next read of that entry.
- The Native Reader throws partway through: the Owner answers and the failure
  is logged, as the current fallback does.
- Two concurrent requests compute the same uncached answer: both are correct,
  and the last write wins.
- An uncacheable or very large result: not stored, and the call still succeeds.

## Requirements _(mandatory)_

### Functional Requirements

- **FR-001**: The system MUST decide per algorithm whether to use a Native
  Reader, based on the Reader Revision reported by the loaded library and the
  minimum revision recorded in the proof file.
- **FR-002**: The system MUST obtain Reader Revisions from the library's own
  capability report. It MUST NOT obtain them from any list ivg's ObjectScript
  layer hard-codes.
- **FR-003**: The system MUST treat a missing, failing or unparseable capability
  report as "no Native Readers" and answer from the Owners.
- **FR-004**: The system MUST NOT require any minimum library version. The
  library version string is used for logs and diagnostics only.
- **FR-005**: The system MUST keep every Owner in place as the fallback. No Owner
  is removed by this feature.
- **FR-006**: The in-scope Native Readers are pagerank, ppr, wcc, cdlp and
  subgraph. Betweenness MUST keep using its exact directed Owner.
- **FR-007**: The proof file MUST record, per algorithm, the minimum proven
  reader revision and the library build (version and checksum) it was proven on.
- **FR-008**: Routing MUST read minimum revisions only from the proof file. It
  MUST NOT carry revision numbers in code.
- **FR-009**: The evidence gate MUST compare each Native Reader with its Owner
  using the equivalence rule for that algorithm (Story 2 scenarios 3-6) on at
  least four graph shapes plus one tie-heavy graph, with five repeated calls
  per graph.
- **FR-010**: The evidence gate MUST fail when the proof file claims a revision
  above what the installed library reports, and MUST NOT skip because the
  library is absent on a machine that is supposed to have it. A machine is
  "supposed to have it" when the environment switch `IVG_REQUIRE_ARNO=1` is
  set, as the pre-release step does.
- **FR-011**: An algorithm that fails the gate MUST stay on its Owner and MUST
  NOT block other algorithms.
- **FR-012**: Routing MUST be testable without a container or library file, by
  supplying capability reports directly.
- **FR-013**: BFS MUST route to the Owner, before any library call, whenever the
  serialized adjacency would exceed 34,791 characters.
- **FR-014**: BFS routing by graph and direction MUST remain as today: the
  Native path only for the default graph, outbound.
- **FR-014a**: A Native Reader MUST be used only for the graph scope its proof
  covers. Until the proof file records named-graph coverage, every call with
  a named graph (ppr, subgraph) MUST go to the Owner. The Native Readers read
  an adjacency with no graph dimension, so a named-graph call would otherwise
  see every graph.
- **FR-015**: `kg_bfs_compute` MUST NOT be adopted by this feature. Adoption
  requires a separate decision after arno demonstrates correct results under
  the library call ivg uses.
- **FR-016**: Algorithm results MUST be cached in shared storage visible to every
  connection, scoped to one graph, and keyed by algorithm and parameters.
- **FR-017**: Every cache entry MUST carry a graph-version token, and the token
  MUST be compared with the current one on every read. A mismatch is a miss.
- **FR-018**: The graph-version token MUST be chosen by an audit of every write
  path (SQL edge writes, adjacency writes, temporal inserts, delete, erasure,
  snapshot restore, index rebuild). The audit MUST show that each path changes
  the token, or that path MUST be fixed or MUST make the cache bypass itself for
  that graph.
- **FR-019**: There MUST be one test per audited write path: run the algorithm,
  change the graph through that path, run it again, and assert the answer
  changed.
- **FR-020**: Erasure MUST invalidate cached answers for the affected graph, so
  no answer outlives erased content (ADR-0004).
- **FR-021**: A process-private cache layer MUST be added only if a benchmark
  shows it beats a shared-cache hit by at least 2x with the shared token check
  kept. Otherwise it MUST NOT be added, and the reasons MUST be recorded.
- **FR-022**: The cache MUST be independent of the library build and MUST
  apply to Owner answers too.
- **FR-023**: `docs/KNOWN_ISSUES.md` MUST describe the current build and the
  evidence behind each open item.
- **FR-024**: Tests MUST be written before the code they test, and each user
  story MUST have an end-to-end test that passes before the next story's work
  starts.

### Key Entities

- **Owner**: the ObjectScript implementation of an algorithm; the reference and
  the fallback.
- **Native Reader**: the Rust implementation in the Arno library.
- **Reader Revision**: an integer per Native Reader reported by the library.
- **Proof Record**: per algorithm, the minimum proven revision and the library
  build it was proven on.
- **Cached Result**: an algorithm answer with its graph, algorithm, parameters
  and graph-version token.
- **Graph-Version Token**: a value that changes whenever a graph's content
  changes; used to validate a Cached Result.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: With any of {no library, old library, new library}, every in-scope
  algorithm returns a correct answer in 100% of routing test cases.
- **SC-002**: Zero algorithms use a Native Reader at a revision below the one
  recorded in the proof file, across all routing test cases.
- **SC-003**: For every algorithm recorded as proven, its Native Reader passes
  the comparison on 100% of the gate's graphs and gives identical answers on all
  five repeated calls.
- **SC-004**: BFS on a graph with adjacency over 34,791 characters completes
  with the correct result and no heap error in every tested size, including
  sizes past the 65,535-character cap.
- **SC-005**: For every audited write path, the answer after the write differs
  from the answer before it, in 100% of write-path tests.
- **SC-006**: No cached answer is returned after the content it depends on is
  erased, in 100% of erasure tests.
- **SC-007**: A repeat request for an unchanged graph is served from the cache in
  100% of tests, including from a second connection.
- **SC-008**: An installation with the old library build behaves exactly as it
  does today for all routed algorithms.
- **SC-009**: `docs/KNOWN_ISSUES.md` contains no statement about the Arno library
  that the recorded evidence contradicts.

## Assumptions

- The library's capability entry is callable from ivg's process. Research
  (`research.md` R1) found the working call form is `$ZF(-4,3,...)` then
  `$ZF(-5,...)`; `$ZF(-6)` fails for every entry, including the version call.
- ivg's Arno-availability probe (`_detect_arno`) keeps its current role of
  deciding whether the library is usable at all. This feature adds the
  per-algorithm decision on top of it.
- The proof file lives in the repository and is updated by the gate, not by hand.
- Erasure and snapshot restore are the write paths most likely to miss a token
  bump; the audit treats them first.
- Arno's betweenness is undirected by construction and ivg's Owner is exact
  directed, so the two are not interchangeable.
- The x86_64 library build has never run on IRIS. CI does not fetch the
  library, so CI will not run it either.

## Open Questions (recorded, not blocking)

- Will arno add a directed-betweenness option? ivg wants no change until then.
- The x86_64 library build is untested on IRIS. Running it needs a manual
  enterprise-tier run on x86_64 hardware, which Tom or the arno maintainers
  must schedule.
- `kg_bfs_global` keeps a frozen interface (arno spec 063). The real fix for the
  size limit is a future EXSTR spec in arno.
- Does `kg_bfs_compute` return correct results once arno runs its diagnostic for
  negative integer subscripts? Its result on the probed build was empty.
- Arno fixes the namespace to `USER`; ivg's engine is namespace-aware. How do
  the two reconcile?
- Which of the eleven asks for the arno maintainers in
  `docs/research/arno-rzf-paths-forward.md` does arno accept?
- The private-process caching measurement from the ASQ work is arno-side
  research in progress and unverified on the ivg side.
