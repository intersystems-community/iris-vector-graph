<!-- markdownlint-disable MD013 -->

# Data model: One IVG Schema per namespace (spec 262)

## IVG Schema

The SQL schema that holds every table, function, procedure and view IVG creates in a namespace (CONTEXT.md).

| Attribute | Rule |
| --- | --- |
| name | `[A-Za-z][A-Za-z0-9_]*`; not `SQLUser`; not starting `%`; default `Graph_KG` |
| package | the name with `_` turned into `.` (IRIS's mapping, research R2) |
| identity | case-insensitive: compared upper-cased |
| constraint | its package is not, and is not under, `Graph.KG`, `IVG` or `IVGC`, unless the name is `Graph_KG`; the package holds no class IVG did not create |

## Schema record

`^IVG.Deploy("schema")` = name as given. `^IVG.Deploy("schema", "recorded")` = the text `"<UTC timestamp> UTC <ivgVersion>"` (iris_vector_graph/schema_name.py, `write_record`).

States and transitions:

```text
            first initialize_schema(), new namespace
 (absent) ──────────────────────────────────────────▶ recorded(name)
    │
    │ initialize_schema() on a 4.5.x namespace
    ├── IVG tables in exactly one schema S, S the engine's ▶ recorded(S)
    ├── IVG tables in exactly one schema S, not the engine's ▶ (absent) + error naming S (FR-009)
    ├── IVG tables in several schemas, no name given ─▶ (absent)  + error listing schemas and row counts
    └── IVG tables in several schemas, name S given ──▶ recorded(S) + the others reported, untouched

 recorded(S) ── engine with schema T ≠ S, first access ──▶ recorded(S) + error naming S and T (nothing written)
 recorded(S) ── rename ─────────────────────────────────▶ out of scope (manual route in ADMIN_GUIDE)
```

A dry run of any transition reports the target state and writes nothing.

## Legacy manifest

A frozen constant in the package (research R6). Each entry:

| Field | Example |
| --- | --- |
| kind | `function`, `procedure`, `view`, `class` |
| sql_name | `SQLUser.STR_SPLIT` (empty for a class-only entry) |
| class_name | `User.funcSTRSPLIT`, `Graph.KG.Benchmark` |
| replacement | `STR_SPLIT` in the IVG Schema; empty for a view and a benchmark class |
| first_release / last_release | `4.0.0` / `4.5.1` |

## Legacy object (in a namespace)

A manifest entry found in the namespace. `remove_legacy_objects` returns, for each:

| Field | Meaning |
| --- | --- |
| entry | the manifest entry |
| present | the SQL object and/or its class exists |
| grants | the `Direct` grants on it (research R4): subject, subject type, privilege, with-grant-option |
| copied | for the upgrade's report: whether each grant was copied to the replacement |
| removed | for `dry_run=False`: whether it was dropped |

## Inventory

The published list of what a fresh namespace holds (FR-017). Each row: kind (package, table, function, procedure,
global, class), name, purpose, optional (and what makes it active, for `Graph.KG.Service` and
`Graph.KG.FHIRGraphSyncTask`), in a backup (yes/no). Kept as a table in ADMIN_GUIDE and as a machine-readable copy the
inventory test reads (`docs/inventory.json`, generated from the table so the two cannot disagree).
