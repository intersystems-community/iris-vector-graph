# Implementation Plan: PyOps Vector Conversion Extraction

**Branch**: `011-pyops-vector-extraction` | **Date**: 2026-01-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/011-pyops-vector-extraction/spec.md`

## Summary

Extract duplicated vector conversion logic from PyOps.cls (VectorSearch and HybridSearch methods) into a single shared internal helper method. This refactoring eliminates code duplication, ensures consistent validation error messages, and defines the embedding dimension (768) as a compile-time class constant.

## Technical Context

**Language/Version**: Python 3.11 (embedded in InterSystems IRIS via `Language = python`)  
**Primary Dependencies**: `intersystems-irispython`, `json` (stdlib)  
**Storage**: N/A (refactoring existing code, no new storage)  
**Testing**: Manual validation via IRIS terminal; compare before/after behavior  
**Target Platform**: InterSystems IRIS 2025.1+  
**Project Type**: Single project (IRIS ObjectScript/Python classes)  
**Performance Goals**: No performance regression from current implementation  
**Constraints**: Must maintain backward compatibility with existing method signatures  
**Scale/Scope**: Single class file (PyOps.cls, ~77 lines)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution is a template without project-specific gates defined. No violations to check.

**Status**: PASS (no gates defined)

## Project Structure

### Documentation (this feature)

```text
specs/011-pyops-vector-extraction/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (N/A for this refactoring)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
iris_src/src/
└── Graph/KG/
    └── PyOps.cls        # Target file for refactoring
```

**Structure Decision**: This is a refactoring of an existing IRIS class file. No new directories or files needed. Changes are confined to `iris_src/src/Graph/KG/PyOps.cls`.

## Complexity Tracking

No violations requiring justification.
