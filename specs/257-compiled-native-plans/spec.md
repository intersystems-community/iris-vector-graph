<!-- markdownlint-disable MD013 -->

# Feature Specification: Native Cypher plans compiled to their own routines

**Feature Branch**: not created (draft)
**Created**: 2026-10-22
**Status**: Built for fixed-length chains (plan.md); variable-length hops still interpreted
**Input**: the alt-parity benchmarking of 2026-10-22. After the day's executor changes, the queries furthest behind
Neo4j are traversals whose cost is spread evenly over the native executor's per-match work: no step dominates, and
removing any one gains little. A hand-written loop for one of them shows what a specialised routine reaches.

## What the measurements say

`Graph.KG.Cypher.Exec` interprets a JSON plan. Every match passes through `Walk`, `Neighbours`, `NodeOK`, `Emit` and
`Group`, each reading the plan back out of `pQ` (`$List` of the node's labels, properties and comparisons, the hop's
types and direction), reading properties through name indirection (`@pQ("pidx")@(...)`), and calling one method per
step.

SNB BI18 at SF1 (mutual friends: `(p {sid: $x})-[:KNOWS]-(m:Person)-[:KNOWS]-(f:Person) WHERE f.sid <> $x RETURN f.sid,
count(DISTINCT m) ORDER BY ... LIMIT 20`), one process, about 11,400 matches and 5,100 distinct `f`, laptop under load:

| Variant | ms | Per match |
| ------- | -: | --------: |
| The executor, the full query | 90 | 7.9 us |
| ... without `f.sid <> $x` | 69 | |
| ... `count(*)` for `count(DISTINCT m)` | 79 | |
| ... no grouping (`count(*)` of the matches) | 50 | |
| ... the walk alone, tallied | 18 | |
| One hand-written routine for the full query (same answers, 8/8 parameter sets) | 24 | 2.1 us |

The bookkeeping is not the cost: a process-private global's `$Data`/`Set`/`$Increment` measured 0.15 us against a
local array's 0.13. A property read through indirection costs 0.93 us against 0.53 direct. What the routine removes is
the interpretation: the method calls per step, the plan read back per match, and the indirection.

Neo4j answers BI18 in 7.6 ms (SF1, quiet machine). The routine alone would take IVG from about 8x behind to about 2x;
the executor's parallel split, which halves BI18 today, would apply on top.

## The idea

When the executor first meets a plan shape (the plan with its parameter values removed, which `NativeRoute` already keys
on), generate an ObjectScript class for it: the anchor lookup, one `For` loop per hop with the types and direction
written in, each node's label and property checks inline, the property reads against the `rdf_props` storage globals
by name (from the class's storage definition at generation time), and the aggregate or projection written as local
array operations. Compile it once per namespace and shape, cache it (`^IVG.CypherCompiled(shape hash)` = class name,
dropped when `PLAN_VERSION` or the storage changes), and run it in place of `Exec.Run`'s interpretation. Shapes the
generator does not cover keep the interpreter.

## User Scenarios & Testing

### User Story 1 - A compiled plan answers as the interpreted one does (Priority: P1)

**Independent Test**: shadow mode, three-way: every native TCK scenario and every SNB/DRKG query answered by the
compiled routine, the interpreter and the SQL path; all three equal.

### User Story 2 - The compiled plan is faster where matches are many (Priority: P1)

**Independent Test**: SF1 on AWS, the 40 SNB queries, compiled against interpreted on the same instance: BI18, BI04,
BI14, IC2, IC7, IC8 and IC9 at least 2x faster; no query slower by more than 10%.

### User Story 3 - Compilation never costs a caller (Priority: P2)

A shape's first run is interpreted while its routine compiles in a background job; a failed compile leaves the shape
interpreted and is logged once. A deploy (`tcp-deploy`, `initialize_schema`) drops the compiled routines.

## Requirements (draft)

- **FR-001**: The generator covers family A and B plans as `Exec` runs them today: id and property anchors, label scans,
  fixed and variable-length hops (the latter through `Graph.KG.VarLength` as now), node checks, cross filters, grouped
  counts and count(DISTINCT), ORDER BY with LIMIT (the trim), DISTINCT projections.
- **FR-002**: The generated code reads `^KG` and the property storage directly, with no indirection and no plan read
  per match.
- **FR-003**: The parallel split (workers per anchor share, last-level VL split) runs the compiled routine in each
  worker.
- **FR-004**: The interpreter stays, as the fallback and as the shadow-mode reference.

## Open questions

1. Where generated classes live (a package such as `IVGC.*` per namespace) and how they are cleaned up.
2. Whether property reads go through the storage globals by name (fastest, tied to `rdf_props`'s storage) or a
   generated accessor per install.
3. Whether to start with the slowest SNB shapes only (BI18, BI04, BI14, the IC projections) or the whole of family A
   and B.

## Not in this spec

The SQL path; the variable-length walk itself (`VarLength`), which is already a tight loop (0.2 us per edge read).
