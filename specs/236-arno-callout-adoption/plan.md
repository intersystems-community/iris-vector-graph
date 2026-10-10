# Implementation Plan: Arno Callout Adoption

**Branch**: `236-arno-callout-adoption` | **Date**: 2026-10-03 |
**Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/236-arno-callout-adoption/spec.md`

## Summary

Route five algorithms (pagerank, ppr, wcc, cdlp, subgraph) to Rust Native
Readers only when the loaded library reports a reader revision at or above the
minimum recorded in a committed proof file; otherwise the ObjectScript Owners
answer. Add a size gate so BFS never overruns the library argument limit, and a
shared result cache validated by `^KG("__version")` after a write-path audit
and five fixes. Research (`research.md`) settled the two open unknowns: the
`capabilities` call form (`$ZF(-4,3)` + `$ZF(-5)`, not `-6`) and the token
(`^KG("__version")`, hardened).

## Technical Context

**Language/Version**: Python 3.10+ (`.venv` 3.11, 3.13 verified); ObjectScript on
IRIS 2024.1+ (test image `irishealth:2026.3.0AI.113.0`)
**Primary Dependencies**: `intersystems-irispython` (DB-API + Native API),
already required; stdlib `json`/`hashlib`. No new runtime dependency.
**Storage**: new global `^IVG.AlgoCache`; existing `^KG` (token and adjacency),
`^ArnoAccel`; committed `docs/arno-reader-proof.json`. No SQL schema change.
**Testing**: pytest; unit tier with fake capability payloads (no container);
enterprise tier on `ivg-iris-enterprise` (31972) via `arno_iris_connection`
and the existing integration fixtures
**Target Platform**: IRIS Enterprise (Arno) and Community (Owners only)
**Project Type**: single library (`iris_vector_graph/`, `iris_src/src/`)
**Performance Goals**: repeat request on an unchanged graph served from cache
(no recompute); shared-hit vs PPG benchmark gate 2x (FR-021)
**Constraints**: adjacency argument <= 34,791 chars to the library; token check
one `$Get` per hit; no minimum library version
**Scale/Scope**: five algorithms, one BFS gate, 32 audited write paths,
~29 FR-019 tests

## Constitution Check

| Principle              | Status | Note                                                                                                                                                    |
| ---------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- | --- | ----- |
| I Library-first        | Pass   | all in library and ObjectScript classes                                                                                                                 |
| II Compatibility-first | Pass   | additive; old builds behave as today (SC-008); no public signature changes                                                                              |
| III Test-first         | Pass   | tasks put tests before code; fail-first required                                                                                                        |
| IV IRIS e2e            | Pass   | e2e phase gate per story on `ivg-iris-enterprise`; `SKIP_IRIS_TESTS` default `false`; ports from `IVG_PORT`                                             |
| V Simplicity           | Pass   | reuses `^KG("__version")` instead of a new counter; PPG layer deferred and benchmark-gated                                                              |
| VI Grounding           | Pass   | container `ivg-iris-enterprise`/31972 verified in `tests/conftest.py:12,410`; `ivg-iris` not used                                                       |
| VII Cypher gates       | N/A    | no translator or `_engine/` SQL-generating change                                                                                                       |
| VIII Spec hygiene      | Pass   | Arno tests keep `arno_iris_connection` skip exemption; gate test is the exception and fails rather than skips where the library should be present; no ` |     | true` |

Re-check after design: unchanged. One tension recorded: Gate 1 exempts Arno
fixtures from `pytest.fail`, but FR-010 says the proof gate must not skip when
the library is absent where it should run. Resolved by a dedicated
`IVG_REQUIRE_ARNO=1` switch (set by the pre-release step) that turns the skip
into a failure.

## Project Structure

### Documentation (this feature)

```text
specs/236-arno-callout-adoption/
├── spec.md
├── plan.md
├── research.md
├── research-capabilities.md     # raw evidence
├── research-token-audit.md      # raw evidence
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── arno-reader-proof.schema.json
│   ├── reader-routing.md
│   └── algo-cache.md
├── checklists/requirements.md
└── tasks.md                     # /speckit.tasks
```

### Source Code (repository root)

```text
iris_src/src/Graph/KG/
├── ArnoAccel.cls          # ReaderRevisions(); BumpVersion -> $Increment
├── TraversalBuild.cls     # BuildKG multigraph + Catch bump
├── EdgeScan.cls           # keyed primitives bump
├── Loader.cls             # stop resetting the counter
├── AlgoCache.cls          # new: Get/Put/Invalidate (shared cache)
├── Eraser.cls             # kill cache subtree on erase
└── NKGAccelTraversal.cls  # BFS size gate

iris_vector_graph/
├── _engine/algorithms.py        # route via reader_routing + cache
├── _engine/snapshot.py          # bump after import, and on merge
├── stores/iris_sql_store.py     # BFS adapter selection unchanged; routing calls
├── arno_readers.py              # new: capability parse, proof load, route()
└── (uses existing _call_classmethod)

docs/arno-reader-proof.json      # committed proof record
docs/KNOWN_ISSUES.md             # Arno section refreshed
scripts/arno_reader_gate.py      # evidence gate runner (writes proof file)
scripts/enterprise-container.sh  # install pinned library build
PRE_RELEASE_CHECKLIST.md         # gate step

tests/unit/test_236_reader_routing.py
tests/unit/test_236_proof_file.py
tests/unit/test_236_cache_keys.py
tests/integration/test_236_capabilities.py
tests/integration/test_236_token_write_paths.py
tests/integration/test_236_bfs_size_gate.py
tests/integration/test_236_algo_cache.py
tests/e2e/test_236_reader_gate.py
```

**Structure Decision**: single-project library layout. New pure-Python module
`arno_readers.py` keeps the routing decision testable without a container
(FR-012). ObjectScript changes stay inside existing `Graph.KG.*` classes plus
one new `AlgoCache` class.

## Phase order (test-first, each phase gated by its e2e)

1. Capability read and routing (Story 1): unit tests with fake payloads, then
   `ReaderRevisions`, then `arno_readers.route`.
2. Token hardening (Story 4 prerequisite): write-path tests first (fail), then
   the five fixes.
3. Result cache (Story 4): cache tests, then `AlgoCache` and wiring.
4. BFS size gate (Story 3).
5. Evidence gate and proof file (Story 2): fixtures, runner, library install,
   first proof file.
6. Docs (Story 5) and release checklist step.

Story 1 routing ships inert until the proof file has entries (missing entry
means Owner), so phases 1-4 can merge before phase 5 proves any reader.

## Complexity Tracking

No constitution violations to justify.
