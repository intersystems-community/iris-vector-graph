<!-- markdownlint-disable MD013 -->

# Feature Specification: Python side of removing the stored degree counters

**Feature Branch**: `alt/parity-py`
**Status**: Exploratory (alternative implementation, not adopted)
**Input**: `docs/research/spikes/functional-index-adjacency/DEG-AUDIT.md`, `docs/research/alt-impl/BRIEF-PARITY.md`. The
ObjectScript package (`parity-os`) removes `^KG("deg")` and `^KG("degp")` and adds `Graph.KG.Adj.Degree(pGraph, pSrc,
pPred = "")`, a generation-validated cache in `^KG("gen")` and `^KG("degc")`. This package makes no Python file read, write,
export or promise those counters.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Degree and node listing come from the adjacency (Priority: P1)

A caller of `LazyKG` lists the nodes of a graph and asks for a node's degree. Neither answer depends on a stored counter.

**Acceptance Scenarios**:

1. **Given** a graph whose `^KG("out", g, ...)` is populated and whose `deg` is absent, **When** `iter_nodes()` runs, **Then** it
   yields every source node (found by iterating `^KG("out", g)`), and reads nothing under `deg`.
2. **Given** a node, **When** `degree(node)` or `degree_for_predicate(node, pred)` is called, **Then** the answer is
   `Graph.KG.Adj.Degree(graphKey, node, pred)` called through the host, returned as an `int`, with the graph key derived by
   `graph_index_key` exactly as the other reads derive it; a node the server returns nothing for has degree 0.
3. **Given** the same node asked twice, **Then** the second answer comes from the instance cache and makes no second call.
4. **Given** the engine's namespace probe, **When** it asks whether `^KG` is populated, **Then** it tests `^KG("out")`.

### User Story 2 - Snapshots stop carrying the counters (Priority: P1)

A snapshot never exports `deg`, `degp`, `gen` or `degc`. A restore accepts an old snapshot that holds `deg`/`degp`, ignores
those entries, and leaves no stale generation or cache entry for any graph it wrote.

**Acceptance Scenarios**:

1. **Given** the export plan, **Then** `deg` and `degp` are not stores; `gen` and `degc` are `derived` with a recorded reason;
   `kg_export_subscripts()` names none of the four.
2. **Given** an archive with `deg`/`degp` lines, **When** it is restored, **Then** no `set` is made under those subscripts and
   the lines are not counted as restored.
3. **Given** an archive with `out`/`in` lines for graphs `0` and `acme`, **When** it is restored, **Then** `^KG("gen", g)` and
   `^KG("degc", g)` are killed for exactly those graphs, after every entry is written.
4. **Given** the frozen old-release archives, **Then** they still restore (their `deg` entries are ignored).

### User Story 3 - No Python dead code or promise remains (Priority: P2)

1. **Given** the package, **Then** no `.py` file under `iris_vector_graph` uses `"deg"` or `"degp"` as a `^KG` subscript
   except the one allow-listed back-compat ignore in `snapshot.py`.
2. `engine.backfill_degp()` has nothing left to backfill: it warns that it is deprecated, returns 0, and calls no server method.
3. The derived-store oracle (`tests/support/derived_oracle.py`) no longer treats `deg`/`degp` as stores to verify; it verifies
   that every valid `degc` entry equals the true count.
4. `docs/USER_GUIDE.md`, `docs/ADMIN_GUIDE.md` and `README.md` no longer promise stored degree counters.

## Requirements *(mandatory)*

- **FR-001**: `LazyKG.iter_nodes` MUST iterate `^KG("out", graph)`; `LazyKG.degree` and `degree_for_predicate` MUST call
  `host.call("Graph.KG.Adj", "Degree", graphKey, node, pred)` and return an `int`.
- **FR-002**: `IRISGraphStore._check_namespace` MUST probe `$Data(^KG("out"))`.
- **FR-003**: `snapshot.STORE_PLAN` MUST NOT list `deg` or `degp`; it MUST list `gen` and `degc` as `derived` with reasons.
- **FR-004**: `restore_snapshot` MUST skip `deg`, `degp`, `gen`, `degc` lines of a `^KG` import and MUST kill
  `^KG("degc", g)` then `^KG("gen", g)` for every graph key seen in an `out` or `in` line, after all lines are written.
- **FR-005**: A source-scan test MUST fail when a `.py` under `iris_vector_graph` holds a string constant `"deg"`/`"degp"` or a
  `^KG("deg"...` spelling, outside docstrings and the allow-list.
- **FR-006**: The Python package MUST NOT write `out`/`in` outside the restore path (checked); the restore path clears the
  derived cache as FR-004 says.

## Out of scope

ObjectScript (`parity-os`). Live (`@pytest.mark.e2e` / integration) tests are edited here but run by the lead.

## Success Criteria

- **SC-001**: Each site above has a unit test that fails on the previous code and passes now.
- **SC-002**: The unit suite for the touched files and `tests/unit/test_host.py` is green.
