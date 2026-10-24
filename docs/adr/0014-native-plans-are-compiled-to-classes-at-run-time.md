<!-- markdownlint-disable MD013 -->

# ADR-0014 — Native plans are compiled to classes at run time

**Status**: Accepted
**Date**: 2026-10-24
**Specs**: 257 (compiled native plans), 253 (native Cypher executor)
**Relates to**: ADR-0008

## Decision

The native Cypher executor writes the walk of each plan shape as a class of its own, `IVGC.P<n>` (extending
`Graph.KG.Cypher.Exec`), and compiles it in the namespace that runs the read, while IVG is running. Before 4.4.0, code
in a namespace changed only on a deploy.

- `Graph.KG.Cypher.Gen` owns the generated code, the map `^IVG.CypherCompiled(hash)` from a shape to its class, and
  the modes: `job` (the default: a new shape is answered by the interpreter while a background job compiles it),
  `sync` and `off`. `^IVG.CypherGen("mode")` is the namespace default.
- A compiled class answers exactly as the interpreter does. The interpreter stays, and answers every shape that is not
  compiled, failed to compile, or cannot be recorded.
- An entry records the `rdf_props` stamp and the build times of `Exec` and `Gen`. A stale entry is never run; its shape
  is built again.
- A deploy that installs classes, and `Gen.DropAll()`, delete every compiled class and entry. Nothing else prunes them.

## Context

On SNB SF1 the interpreter spends its time in the calls each match passes through (`Walk`, `Neighbours`, `NodeOK`,
`Emit`, `Group`), each reading the plan back out of `pQ`; removing any one gains little. A hand-written routine for
BI18 answered in 24 ms where the interpreter took about 90, with the same answers on 8 of 8 parameter sets
(`specs/257-compiled-native-plans/spec.md`). The plan's shape, not its parameter values, decides what that routine
contains, so one class per shape serves every read of that shape.

## Alternatives considered

- **Interpreter only.** No new operational behaviour, and the per-match calls stay: BI18, BI04 and BI14 stay 3-8x
  behind Neo4j on SF1.
- **Hand-written routines for the slowest shapes only.** Fast where written, and each one is a second implementation of
  the executor to keep equal to the first. Spec 257's open question 3 chose the whole of plan families A and B instead.
- **Indirection or `XECUTE` inside the interpreter.** No class is written, but a property read through indirection
  measured 0.93 us against 0.53 direct, which keeps most of the cost that compiling removes.
- **Compile at deploy time.** No code changes after a deploy, but the shapes are the queries callers send, which a
  deploy does not know.

## Consequences

- A namespace now holds classes no deploy wrote. A site under change control, or one whose role or database may not
  be written (a read-only database, an async reporting member), sets mode `off`; a process that cannot record a shape
  is interpreted rather than failing (ADMIN_GUIDE, "Classes IVG writes at run time").
- Each new shape in mode `job` starts one short-lived process, and the package grows by one class per distinct shape
  until the next deploy.
- The speed callers come to rely on depends on this path: mode `off` keeps the answers, not the times.
- The compiled walk is held to the interpreter case by case (`tests/e2e/test_257_compiled_plans_e2e.py`); the TCK,
  run in mode `sync`, compiles few of its reads.
