# Research: Auto-Generating GraphQL Layer

**Feature**: Auto-Generating GraphQL Layer  
**Date**: 2026-02-24

## Research Summary

The goal is to evolve the current static GraphQL schema into a dynamic one that adapts to the data present in the InterSystems IRIS Vector Graph. This includes dynamic type generation, efficient connection management within IRIS Community Edition limits, and robust serialization of complex graph results.

---

## Research Item 1: Dynamic Strawberry Schema Generation

**Question**: How to dynamically create strawberry types and query fields at runtime based on sampled metadata (node labels, properties)?

**Decision**: Use a combination of Python `type()` factory and `strawberry.tools.create_type` during application startup.

**Rationale**: 
- **Metadata Sampling**: We can query `SELECT DISTINCT label FROM Graph_KG.rdf_labels` to identify "Classes" (Labels) and `SELECT DISTINCT "key" FROM Graph_KG.rdf_props WHERE s IN (SELECT s FROM Graph_KG.rdf_labels WHERE label = ?)` to identify "Fields" (Properties).
- **Type Creation**: For each label, a dynamic class is created: `DynamicType = type(label, (Node,), {"__annotations__": annotations})`. Applying `@strawberry.type` to this class makes it a valid GraphQL type.
- **Schema Composition**: Instead of a static `Query` class, we build the query fields map dynamically:
  ```python
  fields = {
      label.lower(): strawberry.field(resolver=create_resolver(label))
      for label in detected_labels
  }
  Query = type("Query", (CoreQuery,), fields)
  schema = strawberry.Schema(query=strawberry.type(Query))
  ```
- This approach preserves type safety and introspection while allowing the schema to grow with the data.

**Alternatives Considered**:
- **Fully Generic (Current)**: All nodes are `GenericNode`. Rejected because it requires clients to parse JSON properties manually and lacks autocomplete.
- **Static Domain Files**: Manual creation of `biomedical.py`, etc. Rejected as it doesn't scale to user-defined schemas.

---

## Research Item 2: Connection Pooling in ASGI/FastAPI

**Question**: How to implement a connection pool that respects IRIS native connection lifecycle (5-connection limit) within an ASGI environment?

**Decision**: Implement an `AsyncConnectionPool` using `asyncio.Queue` and `asyncio.Semaphore`.

**Rationale**:
- **Strict Limit**: IRIS Community Edition strictly enforces 5 concurrent connections. Standard pools might exceed this during race conditions.
- **Async Waiters**: Using a `Semaphore(5)` ensures that if 5 connections are in use, the 6th request asynchronously waits without blocking the event loop or failing.
- **Lifecycle**:
  ```python
  class AsyncConnectionPool:
      def __init__(self, size=5):
          self._queue = asyncio.Queue(maxsize=size)
          self._semaphore = asyncio.Semaphore(size)
      
      async def get_connection(self):
          await self._semaphore.acquire()
          return await self._queue.get()
          
      def release_connection(self, conn):
          self._queue.put_nowait(conn)
          self._semaphore.release()
  ```
- **FastAPI Integration**: Use a dependency `async def get_db()` that handles the acquire/release logic via a context manager.

**Alternatives Considered**:
- **Fresh Connection per Request (Current)**: Rejected. Re-opening connections is slow (handshake overhead) and risks license errors if `close()` isn't called fast enough.
- **Threaded Pooling**: Rejected. Harder to coordinate with FastAPI's async nature.

---

## Research Item 3: Cypher/SQL to JSON Serialization

**Question**: Best patterns for generic serialization of IRIS SQL/Cypher results to scalar GraphQL values.

**Decision**: Use a recursive post-processor with special handling for IRIS-specific JSON strings.

**Rationale**:
- **IRIS JSON Aggregates**: The Cypher translator uses `JSON_ARRAYAGG` to group labels and properties. These arrive in Python as strings (e.g., `'["Label1", "Label2"]'`).
- **Standardization**: A post-processor should:
    1. Check for columns ending in `_props` or `_labels` and `json.loads()` them.
    2. Convert `datetime.datetime` to ISO strings.
    3. Convert `decimal.Decimal` to `float` or `string`.
    4. Handle `iris.List` or other native types if they leak from the driver.
- **Example Implementation**:
  ```python
  def serialize_row(row, columns):
      data = dict(zip(columns, row))
      for k, v in data.items():
          if k.endswith(("_props", "_labels")) and isinstance(v, str):
              data[k] = json.loads(v)
          elif isinstance(v, (datetime, date)):
              data[k] = v.isoformat()
      return data
  ```

**Alternatives Considered**:
- **Native IRIS JSON**: Using `SELECT JSON_OBJECT(...)`. Rejected due to reported optimizer bugs in some IRIS versions when using JSON functions in subqueries.
- **Custom GraphQL Scalars**: Creating a scalar for every IRIS type. Rejected as it complicates the client-side consumption.

---

## Unknowns Resolved

- ✅ Strawberry supports fully dynamic class-based types.
- ✅ `asyncio.Semaphore` is the safest way to guard the 5-connection IRIS limit.
- ✅ Post-processing is more reliable than complex SQL for JSON assembly due to IRIS version variances.
