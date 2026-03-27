# Research: Auto-Generating GraphQL Layer

This document consolidates research findings for the dynamic GraphQL layer over IRIS.

## Decisions

### 1. Dynamic Strawberry Schema
- **Decision**: Use the Python `type()` factory with `@strawberry.type` and `strawberry.tools.create_type` during startup.
- **Rationale**: Provides a fully introspectable, typed schema (e.g., `Protein`, `Gene`) based on actual graph content at startup. Supports IDE autocompletion and schema validation.
- **Alternatives considered**:
    - **Fully dynamic resolvers (generic types)**: Rejected because it loses type-safe selection and introspection benefits.
    - **Compile-time generation (codegen)**: Rejected to maintain "zero-config" startup story.

### 2. Connection Pooling (IRIS 5-Connection Limit)
- **Decision**: Implement an `AsyncConnectionPool` using `asyncio.Queue` and `asyncio.Semaphore(5)`.
- **Rationale**: Strictly enforces the 5-connection limit of IRIS Community Edition while allowing concurrent ASGI requests to wait for available connections without crashing.
- **Alternatives considered**:
    - **Standard SQLAlchemy/DBAPI pooling**: Often too heavy for simple native IRIS connections or doesn't respect the hard 5-limit gracefully in an async environment.

### 3. Cypher/SQL to JSON Serialization
- **Decision**: Recursive post-processor for IRIS results to detect JSON aggregate strings and normalize types (datetime, Decimal).
- **Rationale**: Standardizes complex graph structures for GraphQL consumption and avoids IRIS-specific scalar representation issues.
- **Alternatives considered**:
    - **Native SQL JSON_OBJECT**: Rejected due to potential complexity and performance issues with deeply nested graph data in older IRIS versions.

### 4. Property Keyword Handling
- **Decision**: Prefix reserved GraphQL/System keywords with `p_`.
- **Rationale**: Prevents schema generation errors when nodes have properties like `id`, `labels`, or `__typename`.
