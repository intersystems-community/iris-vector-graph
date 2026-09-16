# Upgrading from an older IVG release

Two upgrade paths reach a v3.x install, and they fail in different ways.

- **Portability** — snapshot on the old version, `restore_snapshot()` onto a new
  install. Writes into the _current_ schema, so no migration runs. What is at risk
  is old _content_ surviving a shape change.
- **In place** — install the new package over an existing database. Nothing is
  re-shaped. `^KG` stays exactly where the old code left it until a migration moves
  it, and every current reader is graph-scoped.

Both paths are covered end-to-end against archives and globals that shipped
releases actually wrote: `tests/e2e/test_upgrade_snapshot_restore_e2e.py` and
`tests/e2e/test_upgrade_migrate_globals_e2e.py`, with the frozen artifacts under
`tests/fixtures/snapshots/` (v2.16.0 and v2.20.0). Regenerate them with
`scripts/fixtures/generate_old_snapshot.py`.

## In place: run the migration

```objectscript
Write ##class(Graph.KG.TemporalIndex).MigrateToGraphScoped()
```

Until this runs, a pre-3.0 database's temporal history is present on disk and
invisible through every reader — `get_edges_in_window()` returns `[]` and
`get_temporal_aggregate()` returns `0`, with no error. The migration relocates
`tout`, `tin`, `bucket`, `tagg` and `edgeprop` into the default graph (`0`),
preserves weights and HLL sketches byte for byte, and reports `0` on a second run.

Coming from **pre-2.17** (before spec-214 scoped the structural globals) you need
the structural upgrade too — the temporal migration does not touch `^KG("out")`,
`^KG("in")` or `^KG("deg")`. Two hazards apply to those databases:

- `^KG("deg", node)` stays unscoped, so degree caches read as missing. `sync()`
  rebuilds them.
- `^KG("out", 0, ...)` is a pre-214 _shard_ subscript, and pre-214 `BuildKG`
  ignored `graph_id` — so named-graph edges sit under that `0` too. Read as a graph
  key, a default-graph reader reports named-graph content as its own. Nothing
  detects this; rebuild adjacency from `rdf_edges` with `sync()` rather than
  carrying the old tree forward.

## Portability: what old archives do not contain

Restoring an old archive is lossy in ways the result dict now reports but cannot
fix:

- **Archives written before v3.0 carry no temporal index.** Those releases exported
  `out` and `in` only, so all five temporal subtrees — plus `label`, `prop` and
  `labelset` — were dropped at export time. No restore can recover them, and unlike
  structural adjacency there is no relational source to rebuild from. If you need
  temporal history across an upgrade, take the in-place path.
- **Archives written by a 2.17–2.20 release omit the default graph's structural
  globals.** The global export seeded its `$Order` walk with `0` and so could never
  emit `0` — the default graph key. `sync()` rebuilds adjacency from `rdf_edges`
  after the restore. Fixed in v3.x exports.
- **A v2.16 archive spells the default graph `NULL` in `rdf_edges.graph_id`.** The
  live column is required and ADR-0003 spells that graph `''`; the restore now
  translates. It previously dropped those rows silently — 1 of 4 edges landed.

`restore_snapshot()` returns `failed_rows`: per-table counts of what the archive
held and the database refused. Empty means a complete restore. Check it — a row
count alone cannot tell a dropped row from an archive that never held it.
