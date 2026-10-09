<!-- markdownlint-disable MD013 MD060 -->

# Least-privilege spike: what IVG needs from a restricted IRIS role

Measurement spike, 2026-10-09. Nothing here is a feature. Every claim below was observed on the
container named in "Environment"; the "how" is given next to the claim. The scratch scripts were
kept under `/tmp` (`ivgspike_*.py`) and are not part of the repo.

## Environment

| Item           | Value                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------ |
| IRIS           | `IRIS for UNIX (Ubuntu Server LTS for ARM64 Containers) 2026.3.0AI (Shelves 9290919 8246870) (Build 113U)` (`$ZV`) |
| Container      | `ivg-iris-enterprise`, superserver `localhost:31972`                                                               |
| Namespace      | `IVG236` (globals and routines database `IVG236`, `/usr/irissys/mgr/ivg236/`, no global or package mappings)       |
| DB resource    | `%DB_%DEFAULT` (read from `SYS.Database.ResourceName`; shared with every other database on the default resource)   |
| Client         | `intersystems-irispython` 5.4.0, repo `.venv` (Python 3.11), `IRISGraphEngine` on an `iris.dbapi` connection       |
| Test principal | users and roles prefixed `ivgspike_`, created from `%SYS` with `Security.Roles` / `Security.Users`, deleted at end |

Row counts of the `Graph_KG` tables before the spike: every table 0 except `ledger_revisions` 2 and
`ledger_stats` 1. They are the same after cleanup (see "Cleanup").

## Method

1. Seed five `Person` nodes (`ivgspike_a`..`ivgspike_e`), five edges, five 768-dimension embeddings,
   one `docs` row, a BM25 index and a VecIndex, all as `_SYSTEM`.
2. Run a slice of about 60 engine operations as `_SYSTEM` and record each result (the baseline).
3. Run the same slice as a role-restricted user. A result is "same" only when it equals the baseline;
   an operation that returns an empty value instead of raising is counted as a failure. After each
   write the harness checks the table or global from a separate `_SYSTEM` connection.
4. On each failure, add the one smallest grant that fixes it. Where a grant looked like it could be
   narrower, bisect: revoke one item, re-run, restore.

One harness change was needed and is itself a finding (see Surprise 1): `IRISGraphEngine._iris_obj()`
is overridden to `iris.createIRIS(<the restricted user's own connection>)`.

## Order of failures and fixes

First run: a user whose role holds nothing.

| #   | Operation                                                                                                                                                  | Failure observed                                                                                                                                                                                                                                                                                    | Smallest fix applied                                                                                                                                             |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `dbapi.connect` and `iris.connect` to `IVG236`                                                                                                             | `<COMMUNICATION ERROR> Invalid Message received; Details: Access Denied` (both clients)                                                                                                                                                                                                             | role resource `%DB_%DEFAULT:R`. `%Service_Native:U` alone did not fix it; `%DB_%DEFAULT:R` alone did (also with `%Service_Object:U` added, which was not needed) |
| 2   | any Cypher `MATCH`, `get_node`, `get_nodes`, counts                                                                                                        | `SQLCODE -99 Privilege violation ... User ivgspike_user is not privileged for the operation`                                                                                                                                                                                                        | `GRANT SELECT ON SCHEMA Graph_KG TO role`                                                                                                                        |
| 3   | any `classMethodValue` (BFS, PageRank, Subgraph, BM25, VecIndex, ledger meta)                                                                              | `<PROTECT> *Resource %Native_ClassExecution required`                                                                                                                                                                                                                                               | role resource `%Native_ClassExecution:U`                                                                                                                         |
| 4   | `SELECT * FROM Graph_KG.kg_KNN_VEC(...)`, `kg_TXT`, `kg_RRF_FUSE`, `Graph_KG.kg_PPR(...)`, `Graph_KG.kg_BM25(...)`, `CALL ivg.ppr`, `CALL ivg.bm25.search` | `SQLCODE -99 Privilege violation`                                                                                                                                                                                                                                                                   | `GRANT EXECUTE ON SCHEMA Graph_KG TO role`                                                                                                                       |
| 5   | Cypher `percentileCont(...)`                                                                                                                               | `SQLCODE -99 Privilege violation`                                                                                                                                                                                                                                                                   | `GRANT EXECUTE ON SCHEMA IVG TO role` and `GRANT EXECUTE ON SQLUser.JSON_ARRAYLENGTH` (both needed)                                                              |
| 6   | Cypher functions (`split`, `properties`, `ORDER BY`, `reverse`, `head`/`tail`, `randomUUID`, `rand`, `=~`, `{map}.key`, `OPTIONAL MATCH` with `*1..2`)     | `SQLCODE -99 Privilege violation`                                                                                                                                                                                                                                                                   | `GRANT EXECUTE ON SQLUser.<fn>` for the functions in the table below                                                                                             |
| 7   | read operations at database `R`: Cypher variable-length paths, `khop`, `betweenness_centrality()`, `kg_PERSONALIZED_PAGERANK`                              | `<PROTECT>BFSFastJsonSorted+21^Graph.KG.TraversalBFS.1 ^ArnoKG("bfs_r",...)` (a write by a read operation, answer still correct); `<PROTECT>Put+2^Graph.KG.AlgoCache.1 ^IVG.AlgoCache(...)` (answer still correct); `khop` and `betweenness_centrality()` returned empty results and raised nothing | database resource `%DB_%DEFAULT:RW` (see "READ mode: two options")                                                                                               |

Write slice, starting from the read set with database `RW`:

| #   | Operation                                                                                       | Failure observed                                                                    | Smallest fix applied                                             |
| --- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| 8   | `create_node`, `create_edge`, `bulk_create_*`, `store_embedding`, Cypher `CREATE`, raw `INSERT` | `SQLCODE -99`; `create_node` and `create_edge` return `False`                       | `GRANT INSERT ON Graph_KG.<table>` (tables below)                |
| 9   | Cypher `SET`                                                                                    | `SQLCODE -99`                                                                       | `GRANT UPDATE ON Graph_KG.rdf_props`                             |
| 10  | `delete_edge`, Cypher `DELETE`, raw `DELETE`                                                    | `SQLCODE -99`; `delete_edge` returns `False`                                        | `GRANT DELETE ON Graph_KG.<table>` (tables below)                |
| 11  | the same slice with the database at `R` instead of `RW`                                         | `<PROTECT>` on `^KG`, `^NKG`, `^BM25Idx`, `^VecIdx`, `^EmbedQueue` (see Surprise 4) | `%DB_%DEFAULT:RW` is required for every global-writing operation |

## Findings by bucket

### (a) SQL table privileges on `Graph_KG`

A schema-level grant is possible and sufficient. `GRANT SELECT ON SCHEMA Graph_KG TO role` was
accepted and made the whole read slice work. It also covers a table created after the grant
(`Graph_KG.ivgspike_t`, created by `_SYSTEM` afterwards, was readable by the role).
`GRANT INSERT|UPDATE|DELETE ON SCHEMA Graph_KG` is also accepted and works.

Per-table minimum, found by revoking one table (then one privilege) at a time:

| Table               | Needed by reads (SELECT)                               | Needed by writes                                                    |
| ------------------- | ------------------------------------------------------ | ------------------------------------------------------------------- |
| `nodes`             | all Cypher `MATCH`, counts                             | INSERT (create). DELETE only for Cypher `DELETE` and raw SQL delete |
| `rdf_edges`         | `MATCH` of edges, variable-length paths, counts        | INSERT, DELETE (`delete_edge`)                                      |
| `rdf_labels`        | labels, `CALL ivg.*`, vector search                    | INSERT. DELETE only for Cypher `DELETE`                             |
| `rdf_props`         | properties, `CALL ivg.ppr`, `CALL ivg.bm25.search`     | INSERT, UPDATE (Cypher `SET`). DELETE only for Cypher `DELETE`      |
| `kg_NodeEmbeddings` | `kg_KNN_VEC` procedure, `get_embedding`, vector `CALL` | INSERT (`store_embedding`). DELETE only for Cypher `DELETE`         |
| `docs`              | `kg_TXT`, `kg_RRF_FUSE` procedure                      | none in the slice (nothing in the engine slice writes `docs`)       |

No read or write in the slice needed `embedding_registry`, `kg_EdgeEmbeddings`,
`rdf_reifications`, `ledger_*`, `rdf_namespaces`, `relationship_mappings`, `table_mappings`,
`code_crosswalk`, `fhir_bridges`, `embedding_quarantine`, `kg_NodeEmbeddings_optimized`. UPDATE on
`nodes`, `rdf_edges`, `rdf_labels`, `kg_NodeEmbeddings` was never needed, including when
`create_node`, `create_edge` and `store_embedding` were called a second time on the same key
(`w25`).

### (b) SQL procedure and function EXECUTE

Routines in IVG236 (from `INFORMATION_SCHEMA.ROUTINES`, excluding the system `%...` schemas):

- `Graph_KG`: `kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE` (procedures); `kg_PPR`, `kg_BM25`, `kg_IVF`,
  `kg_Betweenness`, `kg_Closeness`, `kg_DegreeCentrality`, `kg_Eigenvector`, `kg_KCore`, `kg_Leiden`,
  `kg_SCC`, `kg_TriangleCount`, `DijkstraPath`, `MatchEdges`, `MatchEdgesAllGraphs`,
  `rebuild_edge_indices`, `ledger_records`, the `*_Extent` procedures, and iFind helper functions
  (`docs_idxdocstextifind{Find,Highlight,Rank}`, `rdfprops_idxpropsvalifind{Find,Highlight,Rank}`).
- `iris_vector_graph`: `kg_GRAPH_PATH`, `kg_KNN_VEC`, `kg_RRF_FUSE`, `kg_TXT`,
  `GraphOperators_ValidateK` (not exercised).
- `IVG`: `Percentile_PCONT`, `Percentile_PDISC`.
- `SQLUser`: 45 functions (listed below).

`GRANT EXECUTE ON SCHEMA Graph_KG` and `GRANT EXECUTE ON SCHEMA IVG` are accepted and sufficient.
A function created later in `Graph_KG` was callable at once; `CREATE OR REPLACE FUNCTION` did not
remove an existing grant, schema-level or per-function (both re-checked after the replace).

Per-routine bisect (schema grant replaced by 43 per-routine grants, one revoked at a time). Needed
by the slice: `kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE` (SQL callers), `kg_PPR` (`CALL ivg.ppr`),
`kg_BM25` (`CALL ivg.bm25.search`), `kg_DegreeCentrality`, `kg_Betweenness`, `kg_Closeness`,
`kg_Eigenvector`, `kg_Leiden`, `kg_SCC`, `kg_TriangleCount` (the matching `CALL ivg.*`). Not needed
by anything exercised: `DijkstraPath`, `MatchEdges*`, `kg_IVF`, `kg_KCore`, `rebuild_edge_indices`,
`ledger_records`, the iFind functions, the `*_Extent` procedures.

Engine methods such as `kg_KNN_VEC()`, `kg_TXT()`, `kg_RRF_FUSE()` and `degree_centrality()` run
table SQL from Python and did not need the stored routines; the stored routines are needed only when
the routine is called by name (SQL clients, `CALL ivg.*` in Cypher).

`SQLUser` functions cannot be covered by a schema grant without also covering anything else in
`SQLUser`. Revoking one function at a time while running 20 Cypher forms showed:

| Function                                         | Needed by                                                                                |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `CY_PROPS_MAP`                                   | `properties(n)`                                                                          |
| `CY_REVERSE`                                     | `reverse(list)`                                                                          |
| `CY_SORT_KEY`                                    | `ORDER BY`                                                                               |
| `CY_VLP_OPT`, `CY_VLP_PATHS`                     | `OPTIONAL MATCH` over `*1..2`                                                            |
| `JSON_ARRAYGET`, `JSON_ARRAYLENGTH`, `LIST_TAIL` | `head`/`tail`/`last`/`reverse` list functions (`JSON_ARRAYLENGTH` also `percentileCont`) |
| `JSON_VALUE`                                     | `{a:1,b:[1,2]}.b` map access                                                             |
| `NEWID`, `RAND`                                  | `randomUUID()`, `rand()`                                                                 |
| `REGEX_MATCH`                                    | `=~`                                                                                     |
| `STR_SPLIT`                                      | `split()`                                                                                |

The other 32 functions were granted but no query in the slice proved them necessary; the grant list
below keeps all 45 because the Cypher translator can emit any of them. `GRANT EXECUTE ON SCHEMA
SQLUser` was accepted by IRIS but I did not test it alone.

### (c) Class-method execution of `Graph.KG.*`

Needs the resource `%Native_ClassExecution:U`. Without it every `classMethodValue` over the
connection fails with `<PROTECT> *Resource %Native_ClassExecution required`, and so do BFS,
PageRank, Subgraph, BM25, VecIndex and ledger calls. `%Development` was never needed (no operation
in the slice compiles anything). SQL-only operations (Cypher `MATCH`, `get_node`, the SQL functions)
work without `%Native_ClassExecution`: they passed after step 2 and before step 3.

`%Native_ClassExecution` is a single resource, not per class or per package. I did not test whether
the restricted role can call classes outside `Graph.KG`.

### (d) Globals and database resource

- `IVG236` globals, routines and classes live in the one database `IVG236`; its resource is
  `%DB_%DEFAULT`. There are no mappings, so `^KG`, `^NKG`, `^BM25Idx`, `^VecIdx`, `^IVG.*`,
  `^EmbedQueue`, `^ArnoKG`, `^ArnoAccel` are all in it.
- Reading needs `R` on the database resource just to log in (step 1). The `R` was enough for every
  read except the cache writes below. `IRISTEMP` (process-private globals, result-set temp storage)
  needed nothing: the resource `%DB_IRISTEMP` has public permission `RW` on this instance, and the
  read slice (including CTE and `JSON_TABLE` queries) ran at `R`.
- Read operations that write globals, observed as `<PROTECT>` under `R`:
  `Graph.KG.TraversalBFS.BFSFastJsonSorted` writes `^ArnoKG("bfs_r",...)` (used by the Cypher
  variable-length path code, which then fell back and returned a correct answer);
  `Graph.KG.AlgoCache.Put` writes `^IVG.AlgoCache` (a PPR answer cache; the PPR itself still
  returned the right answer). Also at `R`: the engine constructor logs "Arno callout not runnable
  in this process (`<THROW>`)", `status().adjacency.bfs_path` differs from the `_SYSTEM` run, `khop`
  returns `{'nodes': [], 'edges': []}` (the `_SYSTEM` run and the `RW` run return the four
  neighbours) and `betweenness_centrality()` returns `[]` with `<PROTECT> *Resource
%Native_GlobalAccess required` in the log. I did not isolate why `khop` is empty at `R`.
- Writing needs `RW`. With `R` only, `WriteAdjacency` (`^KG("out",...)`), `DeleteAdjacency`,
  `Graph.KG.Eraser` (`^KG("deg",...)`), `BuildKG` (`^KG("__version")`), `DropNKG` (`^NKG`),
  `BM25Index.Insert/Build`, `VecIndex.Insert/Create`, `EmbedQueue` enqueue, and
  `TemporalIndex.WriteStores` all failed with `<PROTECT>`.
- Direct Native API global access (`get`, `set`, `kill`, `isDefined`, `nextSubscript`) needs
  `%Native_GlobalAccess:U`; the engine does not need it on the paths in the slice. It is reached
  from fallback and admin paths: `deploy_packaged_classes` (class-deploy marker), snapshot and
  restore, migrations, `get_centrality_warnings`, and the `status()` and algorithm fallbacks when
  `^NKG` is not built. I observed it for `deploy_packaged_classes` and, when `^NKG` was unbuilt,
  for `engine.leiden_communities()` and the `status().adjacency` fallback. I did not exercise
  snapshot or migrations.

### (e) Embedded Python

No privilege was needed. There is no `%Service_EmbeddedPython` resource or service on this build:
`Security.Resources.Get("%Service_EmbeddedPython")` and `Security.Services` list nothing by that
name. The Python-language class methods ran under the restricted role with the grants above and
returned the same answer as `_SYSTEM`: `Graph.KG.PyOps.getVectorTable`, `getExpectedDimension`,
`Graph.KG.Communities.ClosenessJsonPy`, `LeidenJsonPy`, `PageRankEmbedded.ComputePageRank`, and the
engine's `closeness_centrality()` / `leiden_communities()` on top of them. They use the caller's SQL
privileges (they read through the SQL grants in (a)). Not exercised: `Graph.KG.MCPTools` (the class
is not shipped, per `tests/conftest.py`), `IVG.CypherEngine` (instance methods).

### (f) The connection

- `%Service_SQL` has public permission `U` on this instance, `%Service_Native` and `%Service_Object`
  do not. Both `iris.dbapi` and `iris.createIRIS` connected with neither granted, so the only
  connection-level requirement observed was `%DB_%DEFAULT:R`.
- `%Service_Bindings` exists as a service (`Security.Services`: `Enabled=1`, `AutheEnabled=32`,
  password) but is not a resource here: adding `%Service_Bindings:U` to a role was rejected
  (`Security.Roles.Modify` returned an error that names `Security.Resources.Exists`). The task's
  expectation of `%Service_Bindings:U` does not hold on 2026.3.0AI.
- The failure for a missing database `R` is the unqualified `Access Denied`; the user's
  `InvalidLoginStatus` showed `<READ>Logon+118^%SYS.DBSRV.1` when I had set a start routine by
  mistake, which is a different cause (my user-creation arguments), not a privilege result.

## Final sets

Role setup (run in `%SYS`; replace `<DBRES>` with the database resource of the namespace's globals
database, `%DB_%DEFAULT` in IVG236; look it up with `SYS.Database.%OpenId(dir).ResourceName`):

```objectscript
// READ role
Set sc = ##class(Security.Roles).Create("ivg_reader", "IVG read", "<DBRES>:R,%Native_ClassExecution:U")
// WRITE role
Set sc = ##class(Security.Roles).Create("ivg_writer", "IVG write", "<DBRES>:RW,%Native_ClassExecution:U")
// attach to a user
Set sc = ##class(Security.Users).Create("svc_ivg", "ivg_reader", "<password>", "IVG service")
```

### READ set (verified)

```sql
-- in the IVG namespace, as a user that may GRANT
GRANT SELECT ON SCHEMA Graph_KG TO ivg_reader;
GRANT EXECUTE ON SCHEMA Graph_KG TO ivg_reader;
GRANT EXECUTE ON SCHEMA IVG TO ivg_reader;
-- one statement per function (SQLUser); the list is in "Appendix: SQLUser functions"
GRANT EXECUTE ON SQLUser.STR_SPLIT TO ivg_reader;
```

Resources: `<DBRES>:R`, `%Native_ClassExecution:U`. Nothing else.

### WRITE set (verified): READ set, plus

Resources: `<DBRES>:RW` instead of `:R`.

```sql
GRANT INSERT, DELETE ON Graph_KG.nodes TO ivg_writer;
GRANT INSERT, DELETE ON Graph_KG.rdf_edges TO ivg_writer;
GRANT INSERT, DELETE ON Graph_KG.rdf_labels TO ivg_writer;
GRANT INSERT, UPDATE, DELETE ON Graph_KG.rdf_props TO ivg_writer;
GRANT INSERT, DELETE ON Graph_KG.kg_NodeEmbeddings TO ivg_writer;
```

Or, simpler and wider: `GRANT INSERT, UPDATE, DELETE ON SCHEMA Graph_KG TO ivg_writer` (verified
working earlier in the order table).

### READ mode: two options

| Option                  | Database | Behaviour measured                                                                                                                                                                                                                                                   |
| ----------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Strict read-only        | `R`      | cannot write any global or table. `khop` and `betweenness_centrality()` return empty results without an error; PPR answers are not cached; Arno is not loaded; Cypher variable-length returns the real predicate name (`KNOWS`) where the `_SYSTEM` run returned `R` |
| Reads with cache writes | `RW`     | all reads identical to `_SYSTEM`, **but the role can then write every IVG global** through class methods (see Surprise 3), so it is read-only only by convention                                                                                                     |

The "READ set" above is the strict one. If `khop`, `betweenness_centrality()` and the caches must
work, the database must be `RW`, which makes the role a writer in practice.

## Verification

Fresh roles holding exactly the sets above, in the same namespace:

| Role / slice                     | Result                                                                                                                                                                                                                                                                                                                   |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| READ role, read slice (70 ops)   | 58 identical to `_SYSTEM`. Different: `khop` (empty), `betweenness_centrality()` (empty), variable-length predicate name (`KNOWS`, not `R`), status `probe_ms` and `bfs_path`, engine init (Arno log). The other 6 fail or error for `_SYSTEM` too (4 engine methods that do not exist, Cypher `kcore`, Cypher `date()`) |
| READ role, write slice (26 ops)  | every op failed or no-oped: SQL writes raised `SQLCODE -99`; global writers raised `<PROTECT>`; `create_node`/`create_edge`/`delete_*`/`set_edge_weight`/`sync` returned `False` or `None`. `nodes` and `rdf_edges` stayed at 5 and 5. Temporal edge: `(False, False)`, `<PROTECT> ^KG("tout",...)`                      |
| READ role, DDL                   | `CREATE TABLE`, `DROP TABLE`, `INSERT`, `DELETE` all `SQLCODE -99`                                                                                                                                                                                                                                                       |
| WRITE role, read slice (70 ops)  | 62 identical; the 8 others are status timing, engine init, and the same 6 that fail for `_SYSTEM`                                                                                                                                                                                                                        |
| WRITE role, write slice (26 ops) | 24 pass with identical database state. The two failures are the direct Native API probes (`set`/`kill` on `^KG`), which need `%Native_GlobalAccess` and are not IVG operations                                                                                                                                           |
| WRITE role, extras               | `create_edge_temporal` passes; ledger `enable`, `commit`, `history`, `stats`, `verify`, `disable` pass with no further grant (below)                                                                                                                                                                                     |
| WRITE role, DDL                  | `CREATE TABLE`, `DROP TABLE`, `INSERT` into a table without a grant, `initialize_schema()`: denied (`%ALTER_TABLE`, `%CREATE_PROCEDURE`); `deploy_packaged_classes`: `%Native_GlobalAccess`. These are deployer actions                                                                                                  |

## Optional extras

- Temporal edges (`create_edge_temporal`): no privilege beyond the WRITE set; database `RW` needed
  for `^KG("tout"...)` (`<PROTECT>` under `R`).
- Ledger (`engine.ledger.enable()`, `commit(Changeset)`, `history`, `stats`, `verify`, `disable`):
  passed under the WRITE role with no additional grant. `meta()` passes under the READ role. A
  commit records `conn_user = ivgspike_user` (the actual caller). Run in a guarded way: the
  `^IVG.Ledger` global and the two ledger tables were backed up first and restored after; the
  ledger tables are immutable by trigger, so restoring used `Graph.KG.Ledger.PurgeAll()` and
  re-inserting the original two rows.
- Python-language methods: nothing (see (e)).

## Surprises

1. **`engine.py` hard-codes `_SYSTEM` / `SYS` for its class-method connection.**
   `IRISGraphEngine._iris_obj()` (`iris_vector_graph/engine.py:1136`-`1178`) opens a second
   `iris.dbapi` connection with `username="_SYSTEM", password="SYS"` (lines 1167-1173) whenever the
   caller's connection exposes `hostname`, `port`, `namespace`. On this container `SYS` is valid, so
   every `engine.*` method that goes through that function runs as `_SYSTEM`, whatever user the
   caller connected as: `bm25_*`, `vec_*`, `ivf_*`, `set_edge_weight`, ledger, `status()`.
   Methods in the store (`stores/iris_sql_store.py:483`) use the caller's connection. Proof: with a
   role holding only database `R`, unmodified `engine.bm25_search`, `vec_search` and
   `ledger.meta()` returned the correct answer while `iris.createIRIS(sameConnection)
.classMethodValue(...)` on the same user raised `%Native_ClassExecution required`; after the
   harness override, those three raised too. Where `_SYSTEM`/`SYS` does not work the connect fails
   and the code falls back to the caller's connection, so a deployment where the default password was
   changed sees different behaviour from this container. This is probably part of why a naive
   restricted-role trial is hard to read.
2. **Privilege failures are swallowed.** With the role missing a grant: `create_node` and
   `create_edge` returned `False`, `get_node` returned `None`, `get_nodes` returned `[]`,
   `khop` returned `{'nodes': [], 'edges': []}`, `kg_KNN_VEC` and `kg_RRF_FUSE` returned `[]`,
   `sync()` returned `False`, and `get_centrality_warnings()` returns `[]` on error. The reason is
   only in the log at WARNING level. A caller cannot tell "no data" from "not privileged".
3. **SQL privileges do not gate the Graph.KG class methods.** With `SELECT` only on `Graph_KG`
   and database `RW` plus `%Native_ClassExecution`, `bm25_insert`, `bm25_delete`, `bm25_build`,
   `vec_insert`, `vec_create_index`, `sync()`, `set_edge_weight` and `enqueue_for_embedding`
   succeeded, and `delete_node`, `delete_nodes_by_prefix` and `erase_graph` succeeded with
   `INSERT` granted but no `DELETE` anywhere. SQL privilege errors only appeared for statements the
   Python side sends. A "SQL read-only" role with database `RW` can therefore rewrite or erase
   `^KG`, `^NKG` and the index globals: `erase_graph`, `delete_nodes_by_prefix`, `sync()` and
   the index writers were observed doing it. (`Graph.KG.Eraser.EraseAll` was not called by a
   restricted role.)
4. **A database `R` does not stop SQL writes, and then splits SQL from globals.** With database `R`
   and INSERT/UPDATE/DELETE granted, `create_node`, `bulk_create_*`, `store_embedding`, Cypher
   `CREATE`/`SET`/`DELETE`, and raw SQL writes all persisted (counted from a `_SYSTEM` connection),
   while the global half failed with `<PROTECT>`. `create_edge` returned `True` after `WriteAdjacency`
   raised `<PROTECT>` (the `^KG` adjacency write) and was swallowed: `rdf_edges` had the row, which
   I counted from the `_SYSTEM` connection; I did not read `^KG` back in that run. The database
   resource gates class-method and Native writes; the table privileges gate SQL writes; they are
   independent.
5. **Reads write caches.** `^ArnoKG("bfs_r"...)` (variable-length paths) and `^IVG.AlgoCache`
   (PPR) are written from read operations. At database `R` both failures were swallowed and the
   answers were still right, but `khop` and `betweenness_centrality()` returned empty answers
   (Surprise 2); I did not isolate the cause of those two.
6. **Missing `%DB_*:R` surfaces as `Access Denied` at login**, not as a privilege error, and a role
   that holds only service resources cannot connect.
7. **`%Service_Bindings` is not a grantable resource on 2026.3.0AI**, and `%Service_EmbeddedPython`
   does not exist; the expectation of `%Service_Bindings:U` in the task brief does not match this build.
8. The `-99` failures on `INSERT` carried a second error in the message
   (`<METHOD DOES NOT EXIST>Tokenize+6^Graph.KG.BM25Index.1 *Analyze,%iFind.Utils`) in two runs. I
   did not investigate it; `_SYSTEM` inserts passed in the same state.

## Could not determine

- Whether `GRANT EXECUTE ON SCHEMA SQLUser` alone is sufficient for the Cypher functions, and which
  of the 32 `SQLUser` functions not exercised are ever needed (`CY_CMP`, `CY_EXP_*`,
  `CY_RETYPE_MAP`, `CY_TEMPORAL_*`, `CY_VLP_CHAIN/DISJOINT/HAS`, `LIST_CONCAT/HEAD/LAST/REVERSE`,
  `JSON_KEYS`, `CypherFn_*`). Cypher `date(...)`/`duration(...)` failed as `_SYSTEM` too and gave no
  data.
- Privileges for the embed-queue worker (`process_embed_queue`, `start_background_embedding`), IVF
  and PLAID indexes, edge embeddings, named graphs and multigraph switches, reification tables,
  FHIR graph tables, `snapshot`/`restore`, schema migrations, and `bulk_ingest_edges`.
- Provisioning: `initialize_schema()` needs at least `%ALTER_TABLE` and `%CREATE_PROCEDURE`
  (observed denied); class deployment and compile need `%Native_GlobalAccess` (observed) and
  probably `%Development` (not reached). The minimal deployer role was not measured.
- Arno native acceleration: the callout library is not loaded in this container even for `_SYSTEM`
  (`ArnoAccel DLL not loaded`), so the Rust-callout path under a restricted role is unmeasured.
- A dedicated database for IVG: only the shared `%DB_%DEFAULT` resource was available; I did not
  create a database. The resource is shared with every other namespace on `%DEFAULT`, so granting
  `RW` on it grants far more than IVG.
- Whether `%Native_ClassExecution` can be narrowed (it is not class-scoped in what I saw).
- Behaviour on ECP/mirrored systems, other IRIS builds, or with `IRISTEMP` public permission removed.
- `Graph.KG.MCPTools` and `IVG.CypherEngine`.

## Design questions for a future `engine.grant_access(role, mode)`

1. Schema-level or per-table? Schema-level `SELECT`/`EXECUTE` on `Graph_KG` is sufficient, covers
   tables and routines added by later upgrades, and survived `CREATE OR REPLACE`. The measured
   per-table write set is five tables; is the narrower set worth breaking on the next new table?
2. `SQLUser` is not IVG's schema. Per-function grants (45 now) need maintaining as the translator
   adds functions. Should the UDFs move into an IVG-owned schema so one schema grant works?
3. Create the role or only grant to an existing one? The role needs `Security.Roles` rights
   (`%Admin_Secure`), which an IVG connection should not have. Probably `grant_access(role, mode)`
   should require the role to exist and only emit `GRANT`s.
4. Do database resource grants belong in IVG? `Security.Roles` and the `%DB_*` resource are
   instance-level and need `%SYS` access. Option: IVG returns the statements and the resource names
   (`grant_plan(mode)`), the deployer runs them.
5. Is `read` honest while reads write caches? A strict read role loses `khop` silently. Should the
   cache globals live in a database or mapping the read role can write (for example `IRISTEMP`
   or a separate cache database), so `read` can mean read-only?
6. Should write privilege errors surface instead of returning `False`/`[]`? `grant_access` is hard
   to verify unless the engine can raise on `-99` and `<PROTECT>`.
7. Should the `_SYSTEM`/`SYS` fallback in `_iris_obj()` be removed or made opt-in? As written, a
   least-privilege deployment can be silently running as `_SYSTEM`.
8. `%Native_ClassExecution` is broad. Is there a way to expose the Graph.KG operations as SQL
   procedures only, so the role needs `EXECUTE` and no class-execution resource?
9. SQL write privileges and database `RW` are independent (Surprise 4). Should `write` mode
   verify both halves (a self-test that writes and reads back one node and one edge)?
10. Does `mode` need `deploy` (initialize_schema, class install) as a third level, or is that
    always a deployer task?

## Cleanup

Deleted `ivgspike_user`, `ivgspike_user_r`, `ivgspike_user_w`, `ivgspike_role`, `ivgspike_role_r`,
`ivgspike_role_w` (after explicit `REVOKE`s). Re-created a role with the same name and a user to
test for residue; it had none (`SELECT`, `EXECUTE` still denied), then deleted both. Dropped the
BM25 and Vec indexes, deleted the `docs` row, ran `Graph.KG.Eraser.EraseAll`, killed `^EmbedQueue`
and `^ivgspike`, dropped the throwaway `Graph_KG.ivgspike_t`, `Graph_KG.ivgspike_f` and
`SQLUser.ivgspike_g`. Checked afterwards: no user or role starting `ivgspike`; no routine or table
with `vgspike` in its name; no `Graph_KG` row with an `ivgspike` key; row counts as before (all
tables 0 except `ledger_revisions` 2 and `ledger_stats` 1); `^IVG.Ledger` restored (`head=2`,
`state=disabled`). Left behind: `^KG("__version")` and `^NKG("$meta","version")` counters
(incremented by writes), and `^BM25Idx("__stop__")`, the default stopword configuration written by
any BM25 call; I do not know whether it existed before the spike.

## Appendix: SQLUser functions granted (45)

`CypherFn_IVGJSONARRAYLENGTH`, `CypherFn_IVGJSONKEYS`, `CypherFn_IVGJSONREMOVE`,
`CypherFn_IVGJSONSET`, `CypherFn_IVGJSONVALUE`, `CypherFn_IVGLISTGET`, `CypherFn_IVGLISTHEAD`,
`CypherFn_IVGLISTLAST`, `CypherFn_IVGLISTREVERSE`, `CypherFn_IVGLISTTAIL`, `CypherFn_IVGMAPGET`,
`CypherFn_IVGNEWID`, `CypherFn_IVGRAND`, `CypherFn_IVGRANGE`, `CypherFn_IVGREGEXMATCH`,
`CypherFn_IVGSTRSPLIT`, `CypherFn_IVGTYPEERROR`, `CY_CMP`, `CY_DURATION_FIELD`, `CY_EXP_ORDKEY`,
`CY_EXP_ORDVAL`, `CY_PROPS_MAP`, `CY_RETYPE_MAP`, `CY_REVERSE`, `CY_SORT_KEY`,
`CY_TEMPORAL_ARITH`, `CY_TEMPORAL_FIELD`, `CY_VLP_CHAIN`, `CY_VLP_DISJOINT`, `CY_VLP_HAS`,
`CY_VLP_OPT`, `CY_VLP_PATHS`, `JSON_ARRAYGET`, `JSON_ARRAYLENGTH`, `JSON_KEYS`, `JSON_VALUE`,
`LIST_CONCAT`, `LIST_HEAD`, `LIST_LAST`, `LIST_REVERSE`, `LIST_TAIL`, `NEWID`, `RAND`,
`REGEX_MATCH`, `STR_SPLIT`.

```sql
GRANT EXECUTE ON SQLUser.CypherFn_IVGJSONARRAYLENGTH TO ivg_reader;
-- ... repeat for each name above
```
