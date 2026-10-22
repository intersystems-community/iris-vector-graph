<!-- markdownlint-disable MD013 -->

# Implementation Plan: Native Cypher plans compiled to their own routines

**Spec**: [spec.md](spec.md) · **Created**: 2026-10-22

## The open questions, settled

1. **Where generated classes live.** `IVGC.P<n>` in the namespace that runs the plan, one per shape. Each extends
   `Graph.KG.Cypher.Exec`, so it calls the interpreter's own `Emit`, `Group` and `NodeOK` wherever it does not inline
   them. `^IVG.CypherCompiled(hash)` maps a shape to its class. `Graph.KG.Cypher.Gen.DropAll()` deletes the package and
   the map; a deploy calls it.
2. **Property reads.** Through `rdf_props`' storage globals by name, as `Graph.KG.Cypher.Props` resolves them. The names
   are written into the generated code, and the map entry records the `rdf_props` stamp and the executor's build time.
   An entry whose stamps differ from the running ones is stale, and the shape is built again.
3. **Coverage.** The walk of every family A and B plan whose hops are all fixed-length: one `For` loop per hop. The
   loops inline the types, the direction, the node checks (id, labels, property equalities and comparisons) and the
   grouping (count and count(DISTINCT)). Cross filters, projections, ORDER BY and DISTINCT call the interpreter's own
   methods from the generated code. Plans with a variable-length hop, and the label-scan fast paths `RunA` already
   specialises (`ScanGroups`), stay interpreted.

## What the generated walk does that the interpreter does by calls

- **Relationship identity.** A hop that must not reuse a relationship holds the one it took as locals: its endpoints,
  type and key. It is compared with each earlier such hop that shares a type. The interpreter hashes each relationship
  into a string of used ones.
- **Node checks, once per node.** Past the first hop, a node whose checks read properties is checked once per anchor's
  walk (`k<i>(node)`). A cross filter reads another node's binding, so it is checked every time, by `Exec.NodeOK`.
- **Grouping.** The group is found by the nodes its keys read (`pQ("cgc")`, for the run). A new one is found by the
  keys' values, read inline, as `GroupOf` finds it. count(DISTINCT) members are kept in the run's arrays
  (`pQ("cgd")`), and `Exec.SpillDistinct` moves them to `^||IVG.CypherRun("gd")` at 200,000, or before `Export` reads
  them.

## The seam

`RunA` asks `Gen.For(.tQ, anchor)` once per run, after the plan is compiled into `tQ`. The answer is a class name or
"". It then calls `$ClassMethod(class, "Walk1", ...)` for each anchor, in place of `..Walk(.tQ, 1, ...)`. Everything
before and after the walk (anchors, the parallel split, the trim, `EmitGroups`) is unchanged.

## Modes

`Gen.Mode()` is per process, set over the connection (`Graph.KG.Cypher.Gen.Mode("sync")`). The namespace default is
`^IVG.CypherGen("mode")`, otherwise `job`. `Parallel` passes the coordinator's mode to its workers in the plan.

- `job`: a compiled shape runs compiled. A new shape runs interpreted while a background job builds it.
- `sync`: a new shape is built before it runs (tests, benchmarks).
- `off`: always interpreted. This is the shadow reference.

A failed build records its error in the map entry and writes it to messages.log once; the shape stays interpreted.

## Tests (first)

- `tests/e2e/test_257_compiled_plans_e2e.py`: every family B case in `test_253_family_b_e2e.py`, plus cases for each
  generated branch: type-less hops, several types, `both` with a self-loop, parallel relationships (multiplicity),
  cycles that need relationship identity, tallied hops with and without the degree cache, an unordered LIMIT, DISTINCT,
  ordered projections, an id anchor, named graphs, cross filters and a missing grouping key. Each case asserts three
  things. The compiled answer equals the interpreted one row for row, including the order among ties. Shadow mode
  holds it to the SQL path. The compiled-run counter moved, wherever the shape is covered.
- Lifecycle: `job` mode answers interpreted first, then compiled once the job finishes. `DropAll` removes the classes.
  A stale stamp rebuilds the shape. A failed build leaves the shape interpreted.
- Benchmark: SF1, the 40 SNB queries, compiled against interpreted on one machine.
