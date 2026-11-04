<!-- markdownlint-disable MD013 -->

# Upgrading from an older IVG release

For IRIS operators and application developers moving a namespace to a newer IVG. Find the release the namespace runs
now, and do every step from there down to 4.6.0, in order. Each release's full changes are in the
[CHANGELOG](../../CHANGELOG.md).

**One rule covers most releases:** after `pip install -U iris-vector-graph`, run `engine.initialize_schema()` once in
each namespace. The package and the ObjectScript classes deploy separately, and a class fix reaches IRIS only when
`initialize_schema()` compiles the new sources. It is idempotent, and `initialize_schema(dry_run=True)` reports what
it would do without writing.

| The namespace runs | Do                                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------ |
| 4.5.x              | [To 4.6.0](#to-460-the-ivg-schema)                                                                     |
| 4.3.x or 4.4.x     | [To 4.4 and 4.5](#to-44-and-45-redeploy-the-classes), then 4.6.0                                       |
| 4.1.x or 4.2.x     | [To 4.2](#to-42-upgrade-each-bm25-index) and [to 4.3](#to-43-the-adjacency-index), then the rows above |
| 4.0.x              | [To 4.1](#to-41-from-400), then the rows above                                                         |
| 3.x                | [To 4.0.0](#to-400-graph-scoped-embeddings-and-nodes) first: it moves rows                             |
| before 3.0         | [Before 3.0](#before-30-the-temporal-and-structural-globals), then 3.x                                 |

Upgrading by snapshot instead (save on the old release, `restore_snapshot()` on a new install) runs no migration;
read [Portability](#portability-what-old-archives-do-not-contain) for what old archives lack.

## To 4.6.0: the IVG Schema

4.6.0 puts every SQL object IVG creates in one schema per namespace, the **IVG Schema** (`Graph_KG` by default), and
creates nothing in `SQLUser` or the `User` package. The full guide is [Migrating to 4.6.0](v4.6.0.md). In short:

1. Look first: `engine.initialize_schema(dry_run=True)` reports the schema it would record, the 4.5.x objects left in
   `SQLUser` and `User`, and the grants it would copy.
2. Run `engine.initialize_schema()`. Until it has run, the 4.6.0 package refuses the namespace: the first database
   call raises `IVGSchemaError`, before any SQL. `engine.status()` does not raise and reports `schema: None`.
3. The 4.5.x `SQLUser` functions and views stay in place and keep answering SQL that calls them.
   `engine.remove_legacy_objects()` lists them; `remove_legacy_objects(dry_run=False)` removes them once your own SQL
   names the IVG Schema.

A namespace whose IVG tables are in a schema other than `Graph_KG`, or in two schemas, needs the steps in
[that section of the guide](v4.6.0.md#a-namespace-whose-ivg-tables-are-in-another-schema-or-in-two).

Answers that change in 4.6.0, with no action needed beyond the redeploy:

- **Global PageRank** is textbook PageRank: scores change wherever a graph has a node with no out-edges or a parallel
  edge, and they now sum to 1.
- **WCC** runs until its labels settle; on a graph whose diameter is over 100 one component used to come back as
  several. `kg_WCC(max_iterations=...)` defaults to `None`.
- **CDLP** breaks a tie between a number label and a string label by collation (numbers first).
- **`execute_cypher(read_only=True)`** refuses a write in any statement of a `;`-separated query, and with
  `read_only=False` a failing statement in such a query raises instead of being dropped.

`initialize_schema()` also builds the new index `idx_edges_graph_oid` on `rdf_edges` (a single pass, 0.3 s for
300,000 rows when measured).

## To 4.4 and 4.5: redeploy the classes

No table changes; run `initialize_schema()` after each upgrade to deploy the changed classes.

- **4.4.0** deploys `Graph.KG.Cypher.Gen` and `Graph.KG.Cypher.SortKey`. From then on IVG writes and compiles classes
  in the namespace at run time (`IVGC.P<n>`, one per native plan shape); a namespace under change control sets
  `^IVG.CypherGen("mode") = "off"` ([Admin guide](../ADMIN_GUIDE.md)). Until the redeploy, reads run on the 4.3.0
  executor with the same answers.
- **4.4.1** restores the namespace after every Arno library call; before it, a namespace other than USER could have
  its two-hop statistics written into USER's `^KG`.
- **4.4.2** fixes `USE GRAPH` reads that read other graphs for some query shapes: **upgrade if you rely on
  `USE GRAPH` to isolate tenants.**
- **4.5.0**: callers that test `create_edge` (or `store_node`, `store_edge`, the store's `write_nodes` and
  `write_edges`) for `False` must catch `GraphWriteError`: a refused write now raises and leaves nothing behind.
  Temporal-only predicates (ADR-0015) are new; register predicates before the upgrade's backfill if they should
  write no structural rows.

## To 4.3: the adjacency index

Run `initialize_schema()` once per namespace. On a large graph it takes minutes: it deploys the classes, declares the
**Adjacency Index** on `rdf_edges` and `rdf_labels` and builds it over every existing row, converts `rdf_props.val`
back to `EXACT` collation where an earlier `ensure_indexes` altered it (IRIS rebuilds the `val` indexes: 254 s at 2.9
million rows when measured), adds and backfills `rdf_props.val_key`, and runs `backfill_temporal_rows()`. Until the
classes match the package, every read takes the SQL path, with one warning. Several defaults and answers changed in
4.3.0 (the native executor is on by default); read its **Changed** section in the CHANGELOG.

## To 4.2: upgrade each BM25 index

Each existing BM25 index needs `engine.bm25_upgrade(name)` once (one pass) before `Insert` or `Delete`; `Search`
works before it. 4.2.0 also stopped the engine's dedicated connection from logging in as `_SYSTEM`.

## To 4.1 from 4.0.0

No data moves. Run `initialize_schema()`: it adds `rdf_edges.ekey` (every existing row gets `ekey = 0`) and the
`code_crosswalk` table, in resumable steps; `^KG` and the ledger are untouched. The FHIR tables are created by the
first `fhir_graph_register`, and only in a namespace that has a FHIR repository. If 4.1.0's `upgrade_to_4_0_0`
migrated the namespace, run `initialize_schema()` again on 4.1.1 or later to restore `kg_RRF_FUSE`.

Check call sites for the behaviour changes: `store.delete_nodes` and `delete_edges` and `kg_PERSONALIZED_PAGERANK` stay
in one graph, new boolean writes store `'true'` and `'false'`, and `properties(n)` returns a Cypher map. A snapshot
holds neither `fhir_graphs` nor `code_crosswalk`
([known issue](../KNOWN_ISSUES.md#a-snapshot-restore-does-not-bring-back-a-fhir-graphs-registration-or-crosswalk)).
Details: the [4.1.0 release note](../releases/v4.1.0.md#upgrading-from-400).

## To 4.0.0: graph-scoped embeddings and nodes

4.0.0 moves rows: embeddings become graph-scoped and per model, a node ID is unique per graph rather than per
namespace, and `kg_KNN_VEC`'s fourth argument changes meaning without changing type. Run the migration, after a dry
run, with `upgrade_to_4_0_0` from `iris_vector_graph.migrations`; the order relative to `initialize_schema()`
matters. Everything is in [Migrating to 4.0.0](v4.0.0.md).

## To 3.2.0: embedding identity is enforced

`initialize_schema()` creates `Graph_KG.embedding_registry` and adopts an identity for each embedding table from what
its vector column already declares: width and dtype, with `model_key` left `NULL` because a model cannot be recovered
from the vectors. No re-embedding, no operator action, and the adoption is idempotent.

The width is enforced from the first write onward, so **an existing width contradiction can surface as a new refusal
on upgrade**: a writer configured at a width other than the column's raises `EmbeddingIdentityConflict` where it used
to log `needs_manual_migration` and then attempt an `INSERT` the column could not take. Read `embedding_registry` for
the recorded width, then write at that width or re-embed. Downgrading is safe: a release before 3.2.0 ignores the
registry table.

## Before 3.0: the temporal and structural globals

In place, run the temporal migration once:

<!-- doc-test: skip reason="migrates a pre-3.0 temporal layout; the doc-test namespace has none" -->

```objectscript
Write ##class(Graph.KG.TemporalIndex).MigrateToGraphScoped()
```

Until it runs, a pre-3.0 database's temporal history is on disk and invisible to every reader:
`get_edges_in_window()` returns `[]` and `get_temporal_aggregate()` returns `0`, with no error. The migration moves
`tout`, `tin`, `bucket`, `tagg` and `edgeprop` into the default graph (`0`), keeps weights and HLL sketches byte for
byte, and reports `0` on a second run.

From **before 2.17** (before spec 214 scoped the structural globals) the structural adjacency needs rebuilding too:
the temporal migration does not touch `^KG("out")` or `^KG("in")`. A pre-214 `^KG("out", 0, ...)` is a shard
subscript, and pre-214 `BuildKG` ignored `graph_id`, so named-graph edges sit under that `0`; read as a Graph Key, a
default-graph reader would report them as its own, and nothing detects it. Rebuild adjacency from `rdf_edges` with
`sync()` rather than carrying the old tree forward.

Both paths are tested end to end against archives and globals that shipped releases wrote:
`tests/e2e/test_upgrade_snapshot_restore_e2e.py` and `tests/e2e/test_upgrade_migrate_globals_e2e.py`, with the frozen
artifacts in `tests/fixtures/snapshots/` (v2.16.0 and v2.20.0).

## Deprecated, due for removal in 5.0

| Deprecated                                                | Since | Use instead                                                                   |
| --------------------------------------------------------- | ----- | ----------------------------------------------------------------------------- |
| `engine.drop_graph()`                                     | 3.1.0 | `engine.erase_graph()`, already its implementation                            |
| `engine.rebuild_kg()`, `engine.rebuild_nkg()`             | 3.1.0 | `engine.sync()`; both emit a `DeprecationWarning`                             |
| `fhir_bridge.get_kg_anchors`, `unified_clinical_pipeline` | 4.1.0 | `fhir_resolve_concepts`, `fhir_concept_ppr` ([FHIR graph](../FHIR_GRAPH.md))  |
| `POST /fhir-event`                                        | 4.1.0 | a registered FHIR graph, which reads the repository itself                    |
| the 4.5.x objects in `SQLUser` and `User`                 | 4.6.0 | the IVG Schema's; `remove_legacy_objects(dry_run=False)` removes the old ones |

Removed in 4.0.0: `embedding_dimension` on `GraphSchema.get_procedures_sql_list` (it had been ignored since 3.2.0,
ADR-0005), and `kg_KNN_VEC`'s fourth argument as a model name. A 3.2.0 caller passing a model name there still
compiles, and searches a graph of that name.

## Portability: what old archives do not contain

Restoring an old archive is lossy in ways the result reports but cannot repair:

- **Archives written before 3.0 carry no temporal index.** Those releases exported `out` and `in` only, so the
  temporal subtrees, `label`, `prop` and `labelset` were dropped at export time, and there is no relational source to
  rebuild them from. To keep temporal history across an upgrade, upgrade in place.
- **Archives written by 2.17 to 2.20 omit the default graph's structural globals** (the export's `$Order` walk could
  never emit the key `0`). `sync()` rebuilds the adjacency from `rdf_edges` after the restore.
- **A 2.16 archive spells the default graph `NULL` in `rdf_edges.graph_id`.** The restore translates it to `''`.

`restore_snapshot()` returns `failed_rows`: per table, what the archive held and the database refused. Empty means a
complete restore. Check it: a row count alone cannot tell a dropped row from one the archive never held.
