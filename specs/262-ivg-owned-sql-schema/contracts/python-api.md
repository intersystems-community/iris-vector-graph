<!-- markdownlint-disable MD013 -->

# Contract: Python API (spec 262)

Everything here is public and covered by unit tests (shape, validation, error text) and E2E tests (database state).

## `IRISGraphEngine(conn, ..., schema_prefix=None)`

- `schema_prefix`: `Optional[str]`, default `None`. Resolution: the argument if given, else `IVG_SCHEMA_PREFIX` if set
  and non-empty, else `"Graph_KG"`. `IRIS_SCHEMA_PREFIX` is never read.
- Validation (before any database call): `ValueError("schema_prefix 'X' is not a valid IVG Schema name: <rule>")` when
  empty, not `[A-Za-z][A-Za-z0-9_]*`, equal to `SQLUser` (any case), or starting `%`.
- The 4.5.x default argument value `"Graph_KG"` keeps working unchanged.

## First database access (any method that reads or writes)

Raises `IVGSchemaError` (a `RuntimeError` subclass, exported from `iris_vector_graph`), with nothing executed first, when:

| Condition | Message names |
| --- | --- |
| no record and no 4.6.0 objects, and an object of the legacy manifest (FR-006a) | "set up by an earlier IVG release", `initialize_schema()` and that it writes, `initialize_schema(dry_run=True)` |
| no record, and none of IVG's classes, tables or globals (4.6.0 R201) | "has never been set up for IVG", `initialize_schema()` and that it writes, `initialize_schema(dry_run=True)` |
| no record and no 4.6.0 objects, IVG's classes, tables or globals, no legacy object (4.6.0 R201) | "setup has not finished", `initialize_schema()` and that it writes, `initialize_schema(dry_run=True)` |
| record `S` ≠ the engine's `T` (FR-006) | `S`, `T`, "one IVG Schema per namespace", "use a separate namespace" |

## `engine.initialize_schema(auto_deploy_objectscript=True, dry_run=False) -> dict`

New keys in the returned dict:

| Key | Value |
| --- | --- |
| `schema` | the IVG Schema recorded (or, under `dry_run`, that would be recorded) |
| `schema_recorded` | `"new"`, `"existing"`, `"inferred"` (one schema held IVG tables), or `"explicit"` |
| `legacy_objects` | count of manifest objects still present |
| `grants_copied` | list of `{"object", "replacement", "subject", "privilege", "with_grant"}` |
| `dry_run` | bool |

Raises `IVGSchemaError` when IVG tables are in several schemas and no `schema_prefix` was given, listing each schema
and its row counts (FR-009); and for a package clash (research R2), naming the clashing class.

`dry_run=True` performs no write of any kind (DDL, rows, globals, grants), proven by a before/after snapshot.

## `engine.remove_legacy_objects(dry_run=True) -> list[dict]`

Default `dry_run=True`. Each item: `{"kind", "sql_name", "class_name", "present", "grants", "removed"}`. Only manifest
entries present in the namespace are listed. `dry_run=False` drops exactly those (SQL object, then its class) and sets
`removed`. Never touches a name that is not on the manifest.

## `engine.status()`

`EngineStatus` gains `schema: Optional[str]` and `legacy_objects: int`; `report()` prints
`IVG Schema: <name>` and, when `legacy_objects > 0`, `Legacy objects: <n> (remove_legacy_objects() lists them)`.

## Deprecations

- `iris_vector_graph.cypher.translator.set_schema_prefix` / `get_schema_prefix` (module-global, superseded in 2.5.1 by
  per-engine state): unchanged in 4.6.0; documented as internal.
