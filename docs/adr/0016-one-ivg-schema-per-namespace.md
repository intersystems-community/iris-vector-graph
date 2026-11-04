<!-- markdownlint-disable MD013 -->

# ADR-0016 — One IVG Schema per namespace, chosen at install and followed by everything

**Status**: Accepted (design decision, implementation pending: spec 262)
**Date**: 2026-11-03
**Evidence**: the 4.5.1 newcomer walkthrough (`~/.cache/ivg-bench/novice/REPORT.md`); iris-vector-rag specs 074 and 079 (`IRIS_SCHEMA_PREFIX`)
**Relates to**: ADR-0001

## Decision

Every SQL object IVG creates (tables, functions, procedures, views) lives in one schema, the **IVG Schema**, which defaults to `Graph_KG`. A user may choose another name, through `IRISGraphEngine(schema_prefix=...)` or the `IVG_SCHEMA_PREFIX` environment variable (the argument wins), validated as `[A-Z][A-Z0-9_]*`. The first `initialize_schema()` in a namespace records the name in the namespace, and from then on the ObjectScript classes read it there rather than naming `Graph_KG`. An engine that asks for a different name in a namespace that already has one is refused with an error naming both; renaming is an explicit migration.

## Context

In 4.5.1 the tables followed `schema_prefix` while the functions and views went to `SQLUser` and the ObjectScript classes named `Graph_KG` 283 times, so a non-default prefix split one install across three schemas. An IRIS operator could not tell IVG's generic function names (`RAND`, `NEWID`, `JSON_VALUE`) from the application's own.

iris-vector-rag lets a schema prefix run several instances in one namespace. IVG cannot: `^KG`, `^NKG` and `^IVG.*` belong to the namespace, so two IVG schemas in one namespace would share one adjacency index and one ledger. For IVG the namespace is the isolation unit, and the override exists to fit a namespace whose schema names are taken or prescribed. That is why the name is fixed per namespace rather than per engine, and why one copy of each function is enough.

IVG reads its own variable, not `IRIS_SCHEMA_PREFIX`, because iris-vector-rag and IVG are loaded in one process (hipporag2-pipeline) with different schemas.

## Consequences

- Spec 262: the functions and views leave `SQLUser`; the translator, the compiled native plans and the ObjectScript classes take the schema from the install record; existing namespaces get an upgrade step.
- No class package's SQL name is remapped (4.6.0 R084). The classes that project SQL (one forwarding class for every `[ SqlProc ]`, and the two ledger tables) are compiled into the IVG Schema's own package, `IVGALT.SqlProcs` for `IVGALT`, so DDL in a schema and every IVG function in it share that schema's package. Remapping `Graph.KG` onto the schema made IRIS compile a non-default schema's DDL into `Graph.KG`, beside another schema's table classes.
- An existing namespace keeps the `SQLUser` objects 4.5.x created, frozen and unused by IVG, so SQL that calls them keeps working; `remove_legacy_sql_objects(dry_run=True)` lists them by a frozen 4.5.x manifest (never by name pattern, so an application's own `SQLUser.RAND` is never touched) and `dry_run=False` drops them. 5.0 removes them on upgrade.
- An existing namespace with no record gets one from its first 4.6.0 `initialize_schema()` when IVG's tables are in one schema; when they are in two (`Graph_KG` and another), setup stops, listing both with their row counts, until one `schema_prefix=` records the choice. A dry run reports what would be recorded.
- A process that held two engines with different prefixes in one namespace (hipporag2-pipeline: `RAG` beside `Graph_KG`) now gets an error naming both schemas the first time the second engine touches the namespace, instead of sharing globals silently.
