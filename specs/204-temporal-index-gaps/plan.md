# Implementation Plan: TemporalIndex API Gaps

**Branch**: `204-temporal-index-gaps` | **Date**: 2026-08-31 | **Spec**: [spec.md](spec.md)

## Summary

Close four API gaps in `Graph.KG.TemporalIndex` that force opsreview to
manipulate `^KG` globals it doesn't own. Changes: new `PurgeRawBefore`
ObjectScript classmethod + Python wrapper; `suppressReverseIndex` optional
parameter on `InsertEdge` + `BulkInsert`; `InternLabelSet`/`ResolveLabelSet`
classmethods; `TSUNIT`/`BUCKETMS` parameter + bucket-boundary fix in purge
paths. All changes are additive or backward-compatible defaults. Version 2.8.0.

## Technical Context

**Language/Version**: Python 3.10+ (3.13 verified); ObjectScript (IRIS 2024.1+)  
**Primary Dependencies**: `intersystems-irispython` (DBAPI); `iris-devtester>=1.14.0`; `pytest>=7.4.0`  
**Storage**: InterSystems IRIS — `^KG` globals via `Graph.KG.TemporalIndex`; `iris_sql_store._call_classmethod` bridge  
**Testing**: pytest — unit (mocks) + integration (`ivg-iris-enterprise`, port 31972, namespace USER)  
**Target Platform**: IRIS 2024.1+ (ObjectScript); Python 3.10+ library  
**Project Type**: Single library  
**Performance Goals**: `PurgeRawBefore` ≤ same wall-clock as `PurgeBefore` for equal row counts; `InternLabelSet` ≤ 2× single `InsertEdge` latency  
**Constraints**: Backward-compatible defaults; no schema changes; no SQL table changes  
**Scale/Scope**: SC-002 target: 10,000 edges; SC-003: 1,000 calls × 10 label sets

## Constitution Check

**Principle I (Library-First)**: All changes are in the library codebase — ObjectScript in `iris_src/src/`, Python wrappers in `iris_vector_graph/_engine/temporal.py` and `stores/iris_sql_store.py`. No application workarounds.

**Principle II (Compatibility-First)**: All new parameters have `= 0` / `= ""` defaults. `PurgeRawBefore` is a new classmethod. `TSUNIT = ""` is opt-in. All existing callers unaffected.

**Principle III (Test-First)**: Unit tests written before implementation in every phase. Non-negotiable.

**Principle IV (IRIS-Backend)**: ✅

- [x] Named container `ivg-iris-enterprise` (31972) managed by `scripts/enterprise-container.sh`
- [x] Explicit e2e test phase (each user story has integration phase gate)
- [x] `SKIP_IRIS_TESTS` defaults `"false"` in all new test files
- [x] No hardcoded ports — env var `IVG_PORT=31972` or `IVG_TEST_CONTAINER=ivg-iris-enterprise`

**Principle V (Simplicity)**: No new abstractions. New methods added to existing class. Python wrappers follow the established `_call_classmethod` pattern in `iris_sql_store.py`.

**Principle VI (Grounding)**: Container name `ivg-iris-enterprise` verified from `docker-compose.yml`. Port 31972 verified from same file. Schema prefix `Graph_KG` verified from `engine.py`. Bridge pattern verified from `iris_sql_store.py:723-754`.

**Principle VII**: Not applicable — no translator changes.

## Project Structure

### Documentation (this feature)

```text
specs/204-temporal-index-gaps/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── objectscript.md  # ObjectScript signatures
│   └── python.md        # Python wrapper signatures
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code

```text
iris_src/src/Graph/KG/
└── TemporalIndex.cls    # Add: PurgeRawBefore, suppressReverseIndex,
                         #      InternLabelSet, ResolveLabelSet,
                         #      Parameter TSUNIT, bucket-boundary fixes

iris_vector_graph/_engine/
└── temporal.py          # Add: purge_raw_before, suppress_reverse_index
                         #      in create_edge_temporal/bulk_create_edges_temporal,
                         #      intern_label_set, resolve_label_set

iris_vector_graph/stores/
└── iris_sql_store.py    # Add: purge_raw_before, intern_label_set,
                         #      resolve_label_set store methods;
                         #      suppress_reverse to write_temporal_edge/bulk_write

iris_vector_graph/
└── store_protocol.py    # Add: purge_raw_before, intern_label_set,
                         #      resolve_label_set to GraphStore protocol

tests/unit/
└── test_temporal_index_gaps.py   # New: unit tests for all 4 gaps (mocks)

tests/integration/
└── test_temporal_index_gaps_e2e.py  # New: e2e tests vs ivg-iris-enterprise
```

**Structure Decision**: Single-project, additive changes to existing files. New test files only.
