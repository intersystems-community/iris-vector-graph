# Implementation Plan: SQL Parameterization Security Fix

**Branch**: `012-sql-parameterization` | **Date**: 2026-01-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/012-sql-parameterization/spec.md`

## Summary

Eliminate SQL injection risks in `GraphOperators.cls` by replacing f-string SQL interpolation with parameterized queries. The primary focus is the `kgTXT` method where the `TOP {k}` clause is currently vulnerable. The plan includes implementing robust input validation for the `k` parameter (coercion, boundary checking, and defaulting) and ensuring consistent query patterns across the algorithm layer.

## Technical Context

**Language/Version**: Python 3.11 (Embedded Python in InterSystems IRIS)  
**Primary Dependencies**: `intersystems-irispython`  
**Storage**: InterSystems IRIS (globals and SQL)  
**Testing**: Integration tests in Docker environment verifying SQLi rejection and boundary cases  
**Target Platform**: InterSystems IRIS 2025.1+  
**Project Type**: Single project (IRIS algorithm layer)  
**Performance Goals**: Negligible impact; parameterization is generally faster due to statement caching  
**Constraints**: InterSystems IRIS SQL specific syntax for `TOP` parameterization  
**Scale/Scope**: Refactoring existing method in `iris_src/src/iris/vector/graph/GraphOperators.cls`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

1. **Test-First**: Implementation will follow a test-reproduce-fix-verify cycle.
2. **Simplicity**: No complex libraries; use built-in `iris.sql.exec` parameterization.
3. **Security-First**: Directly addresses a critical security vulnerability.

**Status**: PASS

## Project Structure

### Documentation (this feature)

```text
specs/012-sql-parameterization/
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
└── iris/
    └── vector/
        └── graph/
            └── GraphOperators.cls
```

**Structure Decision**: Confined to a single class in the core algorithm layer.

## Complexity Tracking

No violations.
