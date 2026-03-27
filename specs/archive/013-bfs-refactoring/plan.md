# Implementation Plan: BFS Traversal Refactoring

**Branch**: `013-bfs-refactoring` | **Date**: 2026-01-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/013-bfs-refactoring/spec.md`

## Summary

The primary goal is to refactor the `BFS_JSON` method in `Traversal.cls` to improve readability and maintainability by reducing deep nesting (currently 5+ levels) to at most 3 levels. This will be achieved by extracting traversal logic into specialized internal helper methods (`_traverse_with_predicate` and `_traverse_all_predicates`). Additionally, the implementation will move from slow JSON serialization to direct `%DynamicObject` instantiation, which is expected to yield a >20% improvement in object creation latency. A benchmarking suite will be introduced to verify performance against synthetic graphs of up to 100k nodes.

## Technical Context

**Language/Version**: Python 3.11 (Embedded in InterSystems IRIS via `Language = python`) and ObjectScript  
**Primary Dependencies**: `intersystems-irispython`, `json`, `time` (for benchmarking)  
**Storage**: InterSystems IRIS (Globals)  
**Testing**: `pytest` for functional verification; custom benchmark script for performance  
**Target Platform**: InterSystems IRIS 2025.1+  
**Project Type**: Single project (IRIS Class refactoring)  
**Performance Goals**: < 5% regression in total traversal time; > 20% improvement in object instantiation latency  
**Constraints**: Must maintain backward compatibility; max nesting <= 3 levels  
**Scale/Scope**: Refactoring `iris_src/src/Graph/KG/Traversal.cls`; adding `tests/benchmarks/bfs_benchmark.py`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1. **Test-First**: Refactoring will be preceded by establishing a performance baseline and functional verification tests. (PASS)
2. **Library-First**: Uses native IRIS `%DynamicArray` and `%DynamicObject` for high-performance data structures. (PASS)
3. **Observability**: Benchmarking script provides metrics on latency and throughput. (PASS)
4. **Simplicity**: Reducing nesting complexity is the core objective. (PASS)

**Status**: PASS

## Project Structure

### Documentation (this feature)

```text
specs/013-bfs-refactoring/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (N/A)
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
iris_src/src/
└── Graph/
    └── KG/
        └── Traversal.cls

tests/
└── benchmarks/
    └── bfs_benchmark.py
```

**Structure Decision**: Confined to the existing `Traversal.cls` for implementation and a new `tests/benchmarks/` directory for performance verification.

## Complexity Tracking

No violations.
