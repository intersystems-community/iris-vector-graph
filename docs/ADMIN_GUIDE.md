<!-- markdownlint-disable MD013 MD036 -->

# IVG Admin Guide

**Audience**: the IRIS operator or DBA who runs the IRIS instance IVG lives in. It answers what IVG installs and
where, what privileges it needs, what is optional, how to back it up, upgrade it, inspect it and fix it. The
[User Guide](USER_GUIDE.md) covers writing applications on IVG; every method's signature is in the
[API Reference](API_REFERENCE.md); open problems are in [Known issues](KNOWN_ISSUES.md). Working on IVG itself
(test containers, test variables, releases) is in [CONTRIBUTING.md](../CONTRIBUTING.md).

The examples run against a namespace set up with IVG. The Python examples share one session, opened in
[What setup does](#what-setup-does); each answer shown after `# →` or under "It prints" was worked out by hand, and
the docs tests check it.

---

## 1. Before you start

| Requirement       | Minimum          | Recommended          |
| ----------------- | ---------------- | -------------------- |
| InterSystems IRIS | 2025.1 Community | 2026.1 or later      |
| Python            | 3.10             | 3.11+                |
| RAM               | 4GB              | 16GB+ (large graphs) |

**Which IRIS versions IVG has been run on (checked 2026-10-24).**

- 2026.1 Community: every push, every pull request and a weekly run, through the repository's `docker-compose.yml`
  image (`.github/workflows/quickstart.yml`).
- 2026.3 AI (`irishealth:2026.3.0AI.113.0`): where IVG's integration and end-to-end suites run. 2026.2 AI builds were
  also used; they crash in `%qaqpre` on `FETCH FIRST` over a multi-table `JOIN`, so the engine probes for that and
  emits `TOP` instead.
- 2025.1 Community: the 133-scenario Cypher catalog passed 99.2% there (CHANGELOG v1.77.0, May 2026), and the
  [benchmarks](BENCHMARKS.md) were measured on it. No later run on it is recorded.
- 2024.1 to 2024.3: **never run.** Nothing in the code is known to need more than 2024.1 for its SQL (`VECTOR`,
  `TO_VECTOR`, `VECTOR_COSINE` and `JSON_TABLE` arrived in 2024.1), but vector search was experimental in 2024.1 and
  generally available from 2024.3.

The HNSW approximate-nearest-neighbour index needs 2025.1; without one, vector search scans the table. Filtered
search over an HNSW index (ACORN-1) arrived in 2026.1, as did the `CROSS JOIN LATERAL` that `CALL { ... }` subqueries
returning several columns compile to. Sources: the InterSystems release notes "New in InterSystems IRIS" for
[2024.1](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GCRN_new20241),
[2025.1](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GCRN_new20251) and
[2026.1](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GCRN_new20261), and the
[prerelease software table](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=PAGE_rel_streams).

IVG is a Python package. It installs on the machine that runs the application, not on the IRIS server:

<!-- doc-test: skip reason="an install; layer 3 (the newcomer walkthroughs) runs it on the built wheel" -->

```bash
pip install iris-vector-graph              # core
pip install iris-vector-graph[communities] # + Leiden community detection
pip install iris-vector-graph[full]        # everything
```

For a first look, the repository's `docker-compose.yml` starts IRIS Community Edition with the SuperServer on host
port 1972 and the Management Portal on 52773 (`IVG_IRIS_PORT=11972 IVG_WEB_PORT=15773 docker compose up -d` when an
IRIS on the machine already holds those ports). It is a laptop setup: it un-expires the `_SYSTEM` password `SYS` and
publishes the ports on every interface. Compose names the container itself; `docker compose ps` shows it, and
`docker compose exec iris iris session IRIS -U USER` opens a terminal in it.

---

## 2. What IVG installs and where

### What setup does

IVG has no separate ObjectScript installation and nothing to copy onto the server. One call from Python sets up a
namespace:

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
status = engine.initialize_schema()
status["schema"]
# → 'Graph_KG'
```

`initialize_schema()` works in **the namespace the connection is in**. It:

1. creates IVG's tables, indexes, functions and procedures in one SQL schema of that namespace, the **IVG Schema**
   (`Graph_KG` unless you choose another name, below);
2. sends IVG's ObjectScript classes from the Python package over the connection (`%SYSTEM.OBJ.LoadStream`) and
   compiles them into that namespace's routine database, skipping any the server cannot compile (the table
   [below](#classes-setup-skips));
3. declares the adjacency index on `rdf_edges` and `rdf_labels`, which keeps `^KG` current on every SQL write;
4. records the IVG Schema in the namespace, in `^IVG.Deploy("schema")`.

Nothing goes into `%SYS`, and nothing is shared between namespaces: each namespace you set up has its own copy of the
classes, its own tables and its own globals. Setup returns a status dictionary; `objectscript_skipped` in it names each
class it skipped and why, and `warnings` lists anything you must act on.

### One IVG Schema per namespace

The **namespace**, not the SQL schema, is IVG's unit of isolation. IVG's globals (`^KG`, `^NKG`, `^IVG.*`) belong
to the namespace, so two IVG schemas in one namespace would share one adjacency index and one ledger. A namespace
therefore holds exactly one IVG Schema. To run two graphs completely apart, use two namespaces; to run several graphs
side by side in one, use named graphs ([User Guide](USER_GUIDE.md#4-named-graphs)).

**Choosing a name.** The default is `Graph_KG`. Choose another when that name is taken or your naming rules prescribe
one: `IRISGraphEngine(conn, schema_prefix="RAG")`, or `IVG_SCHEMA_PREFIX=RAG` in the application's environment (the
argument wins; IVG never reads iris-vector-rag's `IRIS_SCHEMA_PREFIX`). The name must match `[A-Za-z][A-Za-z0-9_]*`,
must not be `SQLUser` or start with `%`, and its class package (the name with `_` turned into `.`, so `MY_APP` is
package `MY.APP`) must not be one of IVG's own packages (`Graph.KG`, `IVG`, `IVGC`) or already hold someone else's
classes. A name that breaks a rule raises `ValueError` before any database call. Choose before the first setup: a
recorded name cannot be changed in 4.6.0.

**The record.** The first `initialize_schema()` in a namespace writes the name to `^IVG.Deploy("schema")`, with
`^IVG.Deploy("schema","recorded")` saying when and by which IVG version. Every later engine in that namespace is
checked against it:

```python
native = iris.createIRIS(conn)
native.get("^IVG.Deploy", "schema")
# → 'Graph_KG'
engine.initialize_schema(dry_run=True)["schema_recorded"]
# → 'existing'
```

`initialize_schema(dry_run=True)` writes nothing and reports what a run would do: the schema it would record and how
it chose it (`schema_recorded`: `new`, `existing`, `inferred` or `explicit`), the 4.5.x objects still in `SQLUser` and
`User` (`legacy_objects`), the grants it would copy, and any other schema holding IVG tables (`other_schemas`).

**The errors.** An engine's first database call checks the namespace, before it sends any other SQL, and raises
`IVGSchemaError` when the namespace has no record and lacks 4.6.0's SQL objects, or records another schema. None of
them writes anything. With no record and no 4.6.0 objects, the message says which of three namespaces it is:

- _IVG was never set up there_ (none of IVG's classes, tables or globals): "Namespace … has never been set up for
  IVG … Run initialize_schema() on it, which writes …". Run setup ([What setup does](#what-setup-does)).
- _An earlier release set it up_ (it holds objects 4.5.x or before made in `SQLUser` and `User`): "Namespace … was set
  up by an earlier IVG release and has not been upgraded for this one … Run initialize_schema() on it, which writes
  …". Run setup (see [Upgrading](#6-upgrading)).
- _Setup did not finish_ (IVG's classes, tables or globals, and nothing an earlier release made): "… setup has not
  finished here …". Run setup again.

And whatever the namespace holds:

- _The engine asks for another schema than the record_: "Namespace … records Graph_KG as its IVG Schema, and this
  engine asks for RAG …". Build the engine with the recorded name, or use another namespace for the other schema.

`engine.status()` is never refused: on a refused namespace it reports `schema` (`None` with no record) and the reason.
Setup itself raises a third error when a namespace has no record and IVG's tables sit in another schema than the
engine's, or in several: it lists each schema with its row counts and records nothing until you name the schema to
keep with `schema_prefix=`.
[Migrating to 4.6.0](migration/v4.6.0.md#a-namespace-whose-ivg-tables-are-in-another-schema-or-in-two) shows how to
end with one schema, SQL included; `tests/e2e/test_262_not_upgraded.py` runs that SQL as written.

### Where the classes are, and how to check

The classes are in the namespace setup ran in, in the packages `Graph.KG`, `Graph.KG.Cypher` and `IVG`, and in
`IVGC` once the native executor compiles plans ([optional components](#4-optional-components)). Check from a
terminal **in that namespace**:

```objectscript
USER>write ##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.NKGAccel")
1
```

The same command in `%SYS`, or in a namespace you have not set up, prints `0`, which is expected. `Graph.KG.NKGAccel`
is ordinary ObjectScript and is always deployed; what is optional is the native library it can load
([Arno](#arno-the-native-accelerator)). From SQL, the same check is
`SELECT COUNT(*) FROM %Dictionary.CompiledClass WHERE Name = 'Graph.KG.Eraser'` (1 in a set-up namespace).

A namespace whose tables were made by DDL alone (`CREATE TABLE` scripts, or
`initialize_schema(auto_deploy_objectscript=False)`) has the tables and none of the behaviour: no adjacency index, no
per-graph erase, no ledger, no temporal index. `Graph.KG.Eraser` declares no table, so its presence tells the two apart.
Run `initialize_schema()` there before writing data. Mapping `^KG` from another database (a CPF `[Map.<namespace>]`
entry) brings the data, not the classes.

### The inventory

<!-- inventory-schema: Graph_KG -->

Everything a namespace holds because of IVG, for the default IVG Schema `Graph_KG`. Under another schema the tables,
functions and procedures have the same names in that schema, and the classes IRIS makes for them are in that schema's
package. "Present" says whether setup creates it, the first use of a feature does, or only an operator's action.
`tests/e2e/test_262_inventory.py` compares this list with a namespace that has seen nothing but one
`initialize_schema()`, both ways. The same list, generated from these tables, is `docs/inventory.json`, for a script
of yours that checks a namespace.

**Packages and SQL objects**

<!-- inventory -->

| Kind      | Names                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Present      | Purpose                                                                                                                                                                                                                | Optional, and how it is activated                          | Back up                                                   |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------- |
| package   | `Graph.KG`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | after setup  | IVG's ObjectScript code; for `Graph_KG` also the IVG Schema's own package: the classes IRIS generates for its tables, functions and procedures, and `Graph.KG.SqlProcs`, which projects IVG's class-based SQL routines | no                                                         | routine database; setup re-creates the code, not the rows |
| package   | `Graph.KG.Cypher`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | after setup  | the native Cypher executor and its plan compiler                                                                                                                                                                       | no                                                         | routine database; setup re-creates it                     |
| package   | `IVG`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | after setup  | `IVG.Percentile` (percentile aggregates) and `IVG.CypherEngine` (an embedded-Python entry point)                                                                                                                       | no                                                         | routine database; setup re-creates it                     |
| package   | `Graph.KG.docs`, `Graph.KG.rdfprops`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | after setup  | classes IRIS generates for the iFind indexes on `docs.text` and `rdf_props.val`, in the package named after each table's class                                                                                         | no                                                         | no: IRIS re-creates them with the index                   |
| package   | `IVGC`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | on first use | compiled native plans, one class `IVGC.P<n>` per read shape, written and compiled at run time                                                                                                                          | yes: `^IVG.CypherGen("mode") = "off"` stops it             | no: rebuilt on first use; a stale one never runs          |
| table     | `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | after setup  | the graph: nodes, edges, labels and properties, each row keyed by its graph                                                                                                                                            | no                                                         | yes                                                       |
| table     | `rdf_reifications`, `docs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | after setup  | edge reification (statements about edges); text documents attached to nodes (`kg_TXT`)                                                                                                                                 | no                                                         | yes                                                       |
| table     | `kg_NodeEmbeddings`, `kg_EdgeEmbeddings`, `kg_NodeEmbeddings_optimized`                                                                                                                                                                                                                                                                                                                                                                                                                                                | after setup  | node and edge vectors of the default route, at the engine's `embedding_dimension`; `kg_NodeEmbeddings_optimized` is a 3.x layout kept for old installs                                                                 | no                                                         | yes                                                       |
| table     | `embedding_registry`, `embedding_quarantine`                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | after setup  | which model and width each embedding table holds, checked on every write; vectors the 4.0.0 migration could not place in a graph                                                                                       | no                                                         | yes                                                       |
| table     | `rdf_namespaces`, `relationship_mappings`, `table_mappings`                                                                                                                                                                                                                                                                                                                                                                                                                                                            | after setup  | RDF prefixes; the mappings that expose your own SQL tables as a graph                                                                                                                                                  | no                                                         | yes                                                       |
| table     | `fhir_bridges`, `code_crosswalk`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | after setup  | links from FHIR codes to graph nodes; code-system crosswalks                                                                                                                                                           | no                                                         | yes                                                       |
| table     | `fhir_graphs`, `fhir_canonical_refs`, `fhir_unresolved`, `fhir_definitions`                                                                                                                                                                                                                                                                                                                                                                                                                                            | on first use | the FHIR graph: registered repositories and how their resources link ([FHIR graph](FHIR_GRAPH.md)), made by the first `fhir_graph_register`                                                                            | yes: only if you register a FHIR repository                | yes                                                       |
| table     | `ledger_revisions`, `ledger_stats`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | after setup  | the revision ledger's commits and counters (classes `Graph.KG.LedgerRevision`, `Graph.KG.LedgerStats`)                                                                                                                 | no                                                         | yes                                                       |
| table     | `kg_emb_<hash>`, `kg_eemb_<hash>`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | on first use | one table of node (or edge) vectors per graph and embedding model, made by the first write that names the pair, with an HNSW index when IRIS accepts one                                                               | yes: only for writes that name a graph and model           | yes                                                       |
| function  | `CY_CMP`, `CY_DURATION_FIELD`, `CY_EXP_ORDKEY`, `CY_EXP_ORDVAL`, `CY_FLOAT_TEXT`, `CY_JSON_TYPE`, `CY_PROPS_MAP`, `CY_REL_PROP`, `CY_REL_TOSTRING`, `CY_RETYPE_MAP`, `CY_REVERSE`, `CY_SORT_KEY`, `CY_TEMPORAL_ARITH`, `CY_TEMPORAL_FIELD`, `CY_TOSTRING`, `CY_VALUE_TYPE`                                                                                                                                                                                                                                             | after setup  | Cypher expression helpers the SQL path calls: comparison, sort keys, maps, temporal arithmetic, number text, property types                                                                                            | no                                                         | no: setup re-creates them                                 |
| function  | `CY_VLP_CHAIN`, `CY_VLP_DISJOINT`, `CY_VLP_ENDS`, `CY_VLP_HAS`, `CY_VLP_OPT`, `CY_VLP_PATHS`                                                                                                                                                                                                                                                                                                                                                                                                                           | after setup  | variable-length path walks on the SQL path                                                                                                                                                                             | no                                                         | no: setup re-creates them                                 |
| function  | `JSON_ARRAYGET`, `JSON_ARRAYLENGTH`, `JSON_VALUE`, `JSON_KEYS`, `LIST_CONCAT`, `LIST_REVERSE`, `LIST_TAIL`, `NEWID`, `RAND`, `REGEX_MATCH`, `STR_SPLIT`                                                                                                                                                                                                                                                                                                                                                                | after setup  | JSON, list and string helpers for Cypher's functions (`keys()`, `split()`, `=~`, `rand()`, `randomUUID()` …)                                                                                                           | no                                                         | no: setup re-creates them                                 |
| function  | `CypherFn_IVGJSONARRAYLENGTH`, `CypherFn_IVGJSONKEYS`, `CypherFn_IVGJSONREMOVE`, `CypherFn_IVGJSONSET`, `CypherFn_IVGJSONVALUE`, `CypherFn_IVGLISTGET`, `CypherFn_IVGLISTHEAD`, `CypherFn_IVGLISTLAST`, `CypherFn_IVGLISTREVERSE`, `CypherFn_IVGLISTTAIL`, `CypherFn_IVGMAPGET`, `CypherFn_IVGNEWID`, `CypherFn_IVGRAND`, `CypherFn_IVGRANGE`, `CypherFn_IVGREGEXMATCH`, `CypherFn_IVGSTRSPLIT`, `CypherFn_IVGTYPEERROR`, `Percentile_PCONT`, `Percentile_PDISC`                                                       | after setup  | the same family written in ObjectScript (`Graph.KG.CypherFn`, `IVG.Percentile`), projected by `Graph.KG.SqlProcs`                                                                                                      | no                                                         | no: setup re-creates them                                 |
| function  | `kg_PPR`, `kg_Betweenness`, `kg_Closeness`, `kg_DegreeCentrality`, `kg_Eigenvector`, `kg_KCore`, `kg_Leiden`, `kg_SCC`, `kg_TriangleCount`                                                                                                                                                                                                                                                                                                                                                                             | after setup  | graph algorithms callable from SQL and from Cypher's `CALL ivg.*`; all but `kg_PPR` and `kg_DegreeCentrality` read `^NKG` and raise once a named graph has an edge, as their `CALL` does                               | no                                                         | no: setup re-creates them                                 |
| function  | `kg_BM25`, `kg_IVF`, `MatchEdges`, `MatchEdgesAllGraphs`, `DijkstraPath`, `backfill_temporal_rows`, `rebuild_edge_indices`                                                                                                                                                                                                                                                                                                                                                                                             | after setup  | BM25 and IVF search, edge scans of `^KG`, weighted shortest path, and two upgrade helpers                                                                                                                              | no                                                         | no: setup re-creates them                                 |
| procedure | `kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE`, `ledger_records`                                                                                                                                                                                                                                                                                                                                                                                                                                                                | after setup  | vector search, text search and their rank fusion; the ledger's records as a result set                                                                                                                                 | no                                                         | no: setup re-creates them                                 |
| function  | `docs_idxdocstextifindFind`, `docs_idxdocstextifindHighlight`, `docs_idxdocstextifindRank`, `rdfprops_idxpropsvalifindFind`, `rdfprops_idxpropsvalifindHighlight`, `rdfprops_idxpropsvalifindRank`                                                                                                                                                                                                                                                                                                                     | after setup  | generated by IRIS for the two iFind indexes (`docs.text`, `rdf_props.val`)                                                                                                                                             | no                                                         | no: IRIS re-creates them with the index                   |
| procedure | `nodes_Extent`, `rdfedges_Extent`, `rdflabels_Extent`, `rdfprops_Extent`, `rdfreifications_Extent`, `docs_Extent`, `kgNodeEmbeddings_Extent`, `kgEdgeEmbeddings_Extent`, `kgNodeEmbeddingsoptimized_Extent`, `embeddingregistry_Extent`, `embeddingquarantine_Extent`, `rdfnamespaces_Extent`, `relationshipmappings_Extent`, `tablemappings_Extent`, `fhirbridges_Extent`, `codecrosswalk_Extent`, `LedgerRevision_Extent`, `LedgerStats_Extent`                                                                      | after setup  | generated by IRIS for every table: the extent query of the table's class, named from the class                                                                                                                         | no                                                         | no: IRIS re-creates them with the table                   |
| procedure | `fhirgraphs_Extent`, `fhircanonicalrefs_Extent`, `fhirunresolved_Extent`, `fhirdefinitions_Extent`                                                                                                                                                                                                                                                                                                                                                                                                                     | on first use | the same for the FHIR graph tables, and one for each `kg_emb_<hash>` route table                                                                                                                                       | yes: with their tables                                     | no                                                        |
| function  | `ivg_arno_adj_append`, `ivg_arno_build_adj`, `ivg_arno_get_node_chunk`, `ivg_arno_zf_call_2s`, `ivg_arno_zf_call_kg_kcore`, `ivg_arno_zf_call_kg_leiden`, `ivg_arno_zf_call_kg_scc`, `ivg_arno_zf_call_kg_triangle`, `ivg_arno_zf_get_result_chunk`, `ivg_arno_zf_get_result_len`, `ivg_arno_zf_kcore_json`, `ivg_arno_zf_kcore_run`, `ivg_arno_zf_leiden_json`, `ivg_arno_zf_leiden_run`, `ivg_arno_zf_probe`, `ivg_arno_zf_scc_json`, `ivg_arno_zf_scc_run`, `ivg_arno_zf_triangle_json`, `ivg_arno_zf_triangle_run` | on first use | calls into the Arno library, made the first time k-core, Leiden, SCC or triangle counting asks for it, whether or not the library is installed                                                                         | yes: `IVG_DISABLE_ARNO=1` in the application's environment | no: re-created on use                                     |

IVG creates no views, and nothing in `SQLUser` or the `User` package. A namespace upgraded from 4.5.x still holds
the objects 4.5.x put there until you remove them ([Upgrading](#6-upgrading)).

**Globals**

All in the namespace's default globals database unless you map them elsewhere.

<!-- inventory -->

| Kind    | Names                                                                                             | Present        | Purpose                                                                                                                                                                                                                                          | Optional, and how it is activated                                     | Back up                                                                 |
| ------- | ------------------------------------------------------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| global  | `^IVG.Deploy`                                                                                     | after setup    | the IVG Schema record (`"schema"`), the fingerprint of the classes last compiled (`"fingerprint"`) and those of the functions and procedures setup created (`"routines"`), which let a re-run skip an unchanged class set and unchanged routines | no                                                                    | yes; without it the engine refuses the namespace until setup runs again |
| storage | `^Graph.KG.LedgerRevisionD`, `^Graph.KG.LedgerRevisionI`, `^Graph.KG.LedgerStatsD`, `^<hash>.<n>` | after setup    | the rows and indexes of the tables above. DDL tables keep each in a global named from a hash of the class (`^<hash>.1` the rows, the others its indexes); a table's class storage names them                                                     | no                                                                    | yes: they are the tables                                                |
| global  | `^KG`                                                                                             | after setup    | setup writes its version (`"__version"`); then the adjacency index (`"out"`, `"in"`, `"label"`, degrees) kept from the rows, and the temporal index (`"tout"`, `"tin"`, `"bucket"`, `"tagg"`, `"edgeprop"`), which has no SQL copy               | no                                                                    | yes: the temporal part exists nowhere else                              |
| global  | `^NKG`                                                                                            | on first use   | the integer-keyed copy of `^KG` the graph algorithms and BFS read, built by `engine.sync()`                                                                                                                                                      | no                                                                    | no: `engine.sync()` rebuilds it (minutes on a large graph)              |
| global  | `^IVG.Ledger`                                                                                     | when activated | the revision ledger: head, mutation records, statement identities                                                                                                                                                                                | yes: `engine.ledger.enable()`                                         | yes: canonical history, not rebuildable                                 |
| global  | `^IVG.TemporalStructural`, `^IVG.GraphMode`, `^IVG.CypherGen`                                     | when activated | settings: temporal-only predicates, multigraph graphs, the compiled-plan mode                                                                                                                                                                    | yes: `set_temporal_structural`, `set_multigraph`, a `Set` of the mode | yes: they are configuration                                             |
| global  | `^IVG.AlgoCache`                                                                                  | on first use   | cached answers of PageRank, PPR and other algorithms, keyed by the graph's version                                                                                                                                                               | yes: `IVG_DISABLE_ALGO_CACHE=1`                                       | no: a cache                                                             |
| global  | `^IVG.CypherCompiled`                                                                             | on first use   | one entry per compiled native plan shape (`IVGC.*`), with its status                                                                                                                                                                             | yes: `^IVG.CypherGen("mode") = "off"`                                 | no: a cache                                                             |
| global  | `^IVG.BuiltFrom`, `^IVG.warnings`, `^Graph.KG.Meta`                                               | on first use   | which write a derived store was built from; warnings centrality runs left; build flags                                                                                                                                                           | no                                                                    | no                                                                      |
| global  | `^BM25Idx`, `^VecIdx`, `^IVF`, `^PLAID`                                                           | on first use   | BM25 text, VecIndex, IVF and PLAID indexes, made by their `*_build` calls                                                                                                                                                                        | yes: only if you build one                                            | yes, or rebuild them from the tables                                    |
| global  | `^EmbedQueue`, `^IVG.EmbedQueueTask`                                                              | on first use   | the embedding queue (`enqueue_for_embedding`) and the id of the Task Manager task that works it                                                                                                                                                  | yes: `enqueue_for_embedding`, `start_background_embedding`            | the queue yes; the task id no                                           |
| global  | `^ArnoAccel`                                                                                      | when activated | the path of the Arno library the namespace loads (`"lib_path"`), written when a load succeeds; where the library is at a path the engine tries, setup's own engine loads it, so it is there after setup                                          | yes: the library on the server ([Arno](#arno-the-native-accelerator)) | no                                                                      |
| global  | `^ArnoKG`                                                                                         | on first use   | caches of the adjacency in the shape the Arno library and the BFS reader take                                                                                                                                                                    | no                                                                    | no: a cache                                                             |
| global  | `^bcParScore`, `^bcParSrc`, `^bcBitAdj`                                                           | on first use   | work areas of a parallel betweenness run, one subtree per run's process (its worker processes read them), deleted when it ends; a killed run's are deleted by the next run                                                                       | no                                                                    | no                                                                      |

Not listed: process-private globals (`^||…`), which no other process sees and nothing backs up; IVG's work areas in
`IRISTEMP` (`^IRIS.Temp.IVG.Cypher`, `^IRIS.Temp.IVG.VL`); and lock names that hold no data (`^IVG.AdjW`,
`^IVG.NKGBuild`, `^IVG.FHIRGraph`).

#### Classes setup skips

Setup compiles only what the server can run. A server without embedded Python, or without the `%AI` classes of IRIS's
AI Hub builds, gets these classes skipped, each with its reason in `status["objectscript_skipped"]`. Everything else
works without them.

<!-- inventory: skipped -->

| Class                       | Skipped when the server has no | Reason setup gives                            |
| --------------------------- | ------------------------------ | --------------------------------------------- |
| `Graph.KG.PyOps`            | embedded Python                | `needs embedded Python`                       |
| `Graph.KG.Communities`      | embedded Python                | `needs embedded Python`                       |
| `Graph.KG.PageRankEmbedded` | embedded Python                | `needs embedded Python`                       |
| `IVG.CypherEngine`          | embedded Python                | `needs embedded Python`                       |
| `Graph.KG.Service`          | embedded Python                | `depends on Graph.KG.PyOps`                   |
| `Graph.KG.MCPTools`         | embedded Python, or `%AI`      | `needs embedded Python`, `needs %AI`          |
| `Graph.KG.MCPToolSet`       | `%AI`                          | `needs %AI`, `depends on Graph.KG.MCPTools`   |
| `Graph.KG.MCPService`       | `%AI`                          | `needs %AI`, `depends on Graph.KG.MCPToolSet` |

On IRIS Community 2026.1, which has embedded Python and no `%AI`, setup skips the three `MCP` classes.
`IVG_EMBEDDED_PYTHON=0` (or `1`) in the application's environment overrides the embedded-Python probe.

### Running setup again

Run `initialize_schema()` again after every upgrade of the package, after restoring a namespace, or when a class or
function is missing. A re-run changes no row and duplicates nothing:

```python
engine.execute_cypher("CREATE (:Server {name: 'a'})-[:CALLS]->(:Server {name: 'b'})")
engine.initialize_schema()["schema_recorded"]
# → 'existing'
engine.execute_cypher("MATCH (s:Server)-[:CALLS]->(t) RETURN s.name, t.name").rows
# → [['a', 'b']]
```

Measured on a namespace that had run setup once (`tests/e2e/test_262_inventory.py`), a re-run with the same package
rewrites no class:

- it creates a function or procedure only when `INFORMATION_SCHEMA.ROUTINES` lacks it, or when its DDL text, with
  the IVG Schema, differs from the fingerprint recorded in `^IVG.Deploy("routines")` (as after a package upgrade that
  changes it). An unchanged re-run skips all 44 routines, so their classes are not recompiled and the cached queries
  that call them stay; one dropped by hand is created again on its own. `status["routines"]` lists those created and
  counts those unchanged; `IVG_FORCE_CLASS_DEPLOY=1` creates every one. On a small scratch namespace on the
  enterprise test container, a re-run took 0.6 s, and 1.0 s when it re-created all of them (39 when measured; three runs each);
- it compiles IVG's own classes only when their sources differ from the fingerprint in `^IVG.Deploy("fingerprint")`, as
  after an upgrade. `IVG_FORCE_CLASS_DEPLOY=1` compiles them anyway. A deploy that compiles also deletes the compiled
  plans (`IVGC.*`). The ledger table class declares its index `idx_ledger_correlation`, so reloading the class keeps
  it (before 4.6.0 a reload dropped it until the next setup); setup builds it when the ledger holds rows written
  before the index existed;
- it checks every table, column and index, creates only what is missing (`rdf_edges.graph_id` is altered only when it
  is not yet `NOT NULL DEFAULT ''`), and runs `TUNE TABLE` on the core tables (`status["tuned"]`).

Run it as a job outside busy hours when an upgrade brings a migration ([Upgrading](#6-upgrading) says which do).

---

## 3. Privileges

**Setup** is a deployer's job: it creates SQL tables, indexes, functions and procedures, compiles classes into the
routine database over the Native API, and writes `^IVG.Deploy`. Run it as a user with `%All` in the namespace. A
user without a DDL privilege it needs is stopped at the first statement that needs it, with `IVGSchemaError` naming
the user, the schema and the privilege (`%CREATE_TABLE`, `%CREATE_FUNCTION`, `%CREATE_PROCEDURE`); when that
statement was the first, nothing is created. The smallest deployer role has not been measured. It is known to need the
DDL privileges above, `%ALTER_TABLE` (indexes), write access to the namespace's databases, and the resources
`%Native_ClassExecution:U` and `%Native_GlobalAccess:U`.

**Use** needs much less. The table was measured on 2026.3.0AI with a role that held only what it lists
([least-privilege notes](dev/notes/research/least-privilege-spike.md), October 2026). The notes predate 4.6.0 and
grant each function in `SQLUser`; since 4.6.0 every IVG function is in the IVG Schema, so the schema grants below
replace those:

| For            | Resources                                            | SQL, in the namespace                                                                 |
| -------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------- |
| reading        | `<database resource>:R`, `%Native_ClassExecution:U`  | `GRANT SELECT ON SCHEMA Graph_KG TO role`, `GRANT EXECUTE ON SCHEMA Graph_KG TO role` |
| reading, fully | `<database resource>:RW`, `%Native_ClassExecution:U` | the same                                                                              |
| writing        | `<database resource>:RW`, `%Native_ClassExecution:U` | the same, and `GRANT INSERT, UPDATE, DELETE ON SCHEMA Graph_KG TO role`               |

- `<database resource>` is the resource of the namespace's globals database (`%DB_%DEFAULT` unless you set one). Without
  `:R` a login fails with `Access Denied`.
- With the database at `R`, reads that write a cache (`^IVG.AlgoCache`, `^ArnoKG`) answer correctly without caching, but
  `khop` and `betweenness_centrality()` answered empty, and Arno does not load. That is what the "reading, fully" row
  is for.
- On builds that have the resource `%Service_EmbeddedPython` (2026.3.0AI build 154 and later), add
  `%Service_EmbeddedPython:U` for the Python-language classes (`Graph.KG.PyOps`, Leiden, closeness).
- **SQL privileges do not gate IVG's class methods.** A role with the database at `RW` and
  `%Native_ClassExecution` can rewrite or erase `^KG`, `^NKG` and the index globals through them (`sync()`,
  `erase_graph`, the index builders) whatever its table grants. A read-only role is one whose database resource is `R`;
  `execute_cypher(read_only=True)` and the HTTP server's `IVG_READ_ONLY` are application-level guards on top.
- Compiled native plans write classes (`IVGC.*`) into the routine database and `^IVG.CypherCompiled`. A process that
  may not write them runs its reads interpreted, with the same answers, and says so in `messages.log` once.

---

## 4. Optional components

### Arno, the native accelerator

Arno is a native library (`libarno_callout.so`) that runs some graph algorithms in compiled code. **It is not
distributed with IVG, and IVG is complete without it**: every algorithm has an ObjectScript implementation, which is
what answers when the library is absent. As of arno-callout 1.2.1 with IVG 4.6.0, global PageRank is the one gated
algorithm the library answers; PPR, WCC, CDLP and subgraph answer from ObjectScript whether or not it is loaded,
because the library's readers have not passed IVG's reader gate
([known-issues history](dev/notes/known-issues-history.md#arno-callout-defects-re-verified-for-spec-236)).

- **It is loaded per process.** A `$ZF(-4)` handle is valid only in the process that opened it, so there is nothing to
  load at startup. Do not call it from `%ZSTART`: that runs in `%SYS`, where IVG's classes do not exist
  (`<CLASS DOES NOT EXIST>`), and would load the library into a process that never runs IVG. The engine loads the
  library in each process the first time an algorithm needs it.
- **Where it looks.** The library must be on the IRIS server's file system. Both loaders (`Graph.KG.ArnoAccel.Load`
  and the BFS loader `Graph.KG.NKGAccel.Load`) resolve one path, in this order: `IVG_ARNO_LIB` from the application's
  environment, which the engine passes to both; else the path the last successful load in the namespace recorded in
  `^ArnoAccel("lib_path")`; else `/usr/irissys/mgr/libarno_callout.so`. Put the file at that default path. Nothing is
  loaded from `/tmp` unless a path names it. `##class(Graph.KG.ArnoAccel).LibPath()` answers the path a load with no
  argument would use.
- **Which file loaded.** `engine.status().arno.lib_path` is the file the engine's server process loaded, or the path
  it tried when nothing loaded, and `engine.status().report()` prints it under `Acceleration`:

```text
Acceleration:    Arno 1.2.1+844e5ff75f45
Arno library:    /usr/irissys/mgr/libarno_callout.so (loaded)
```

In a terminal, `##class(Graph.KG.ArnoAccel).LoadedPath()` and `##class(Graph.KG.NKGAccel).LoadedPath()` give the file
each loader opened in that process ("" when it opened none).

- **A missing library is not an error.** `Load` returns 0 and keeps the reason; in a terminal, which is a new process:

```objectscript
USER>write ##class(Graph.KG.NKGAccel).Load("/nonexistent/libarno_callout.so")
0
USER>write ##class(Graph.KG.NKGAccel).IsLoaded()
0
```

`##class(Graph.KG.NKGAccel).LoadError()` gives the reason for the last failed `Load` in that process.
`engine.status().report()` shows what answers your engine's calls: `Acceleration: ObjectScript (Arno not loaded)` or
`Acceleration: Arno <version>`. `IVG_DISABLE_ARNO=1` in the application's environment keeps the engine from loading it.

### The REST dispatcher `Graph.KG.Service`

A `%CSP.REST` class with three routes (`/vectorSearch`, `/hybridSearch`, `/metaPath`). It is deployed with the other
classes (where embedded Python is present) and does nothing until a web application names it as its dispatch class.
`Graph.KG.Service.CreateWebApp(path)` creates one (run it as a user who may create roles and applications). Every
user the application authenticates gets exactly one role, `IVG_KG_<NAMESPACE>`, which the call creates or resets. The
role can only read:

- Read on the resource of each database that holds the namespace's globals and routines, and of any database the
  `KG`, `NKG` or `IVG` globals are mapped to (no route reads `^IVG.Ledger` or another `^IVG.*` global). A database
  created without a resource of its own uses `%DB_%DEFAULT`, so the role then reads every database on that
  resource; give the namespace's database its own resource if that matters at your site.
- In the IVG Schema: EXECUTE on `kg_KNN_VEC`, `kg_RRF_FUSE` and `kg_TXT`, and SELECT on `kg_NodeEmbeddings`,
  `rdf_labels`, `docs` and `embedding_registry`.

Running `CreateWebApp` again resets the role and the application to exactly this. The role loses any other SQL
privilege it holds in the IVG Schema, whoever granted it, and any role granted to it. The application gets this
namespace, `Graph.KG.Service`, `MatchRoles ":IVG_KG_<NAMESPACE>"`, password authentication and is enabled. An
application at that path whose dispatch class is not `Graph.KG.Service` is refused and left as it is.

Before 4.6.0 it set `MatchRoles ":%All"`, so every authenticated user of the application ran with `%All`. An
application made by an earlier release keeps that until you run `CreateWebApp` again with the same path, as a user
who may create roles and applications, in the namespace it serves. The IVG HTTP server (`ivg server start`) is
separate ([section 5](#5-security-settings-for-the-http-server)).

### Task Manager tasks

`Graph.KG.FHIRGraphSyncTask` and `Graph.KG.EmbedQueueTask` are `%SYS.Task.Definition` classes. Deploying them
schedules nothing. A task exists only after `engine.fhir_graph_schedule(graph)` (one per FHIR graph, at least every 60
seconds; `fhir_graph_unschedule` deletes it) or `engine.start_background_embedding()` (one per namespace). They show in
the Portal's Task Manager as "IVG FHIR sync" followed by the graph and "IVG EmbedQueue Worker" followed by the namespace.

### Compiled native plans (`IVGC.*`)

The native Cypher executor writes one class per read shape into the package `IVGC` of the namespace (`IVGC.P<n>`),
compiles it in a background job and records it in `^IVG.CypherCompiled`. So, by default, **code in the namespace
changes at run time, outside a deploy**. The mode decides:

| `^IVG.CypherGen("mode")` | Effect                                                                                                      |
| ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| unset, or `"job"`        | a new shape answers interpreted while a background job compiles its class; later runs use the class         |
| `"sync"`                 | a new shape is compiled before its first run                                                                |
| `"off"`                  | nothing is compiled; every read is interpreted. Same answers, slower: median 1.12x, at most 2.6x on SNB SF1 |

A namespace under change control sets `Set ^IVG.CypherGen("mode") = "off"`. The mode in effect:

```objectscript
USER>write ##class(Graph.KG.Cypher.Gen).Effective()
job
```

A shape whose class does not compile stays interpreted, and the failure is written to `messages.log` once;
`##class(Graph.KG.Cypher.Gen).Entries()` lists every shape with its status. A deploy that compiles IVG's classes, or
`##class(Graph.KG.Cypher.Gen).DropAll()`, deletes all of them; nothing else prunes them, so there is one class per
distinct read shape. `IVG_NATIVE_CYPHER=0` sends every Cypher read to the SQL path instead, compiling nothing.

### The MCP classes

`Graph.KG.MCPService`, `MCPToolSet` and `MCPTools` expose IVG as MCP tools on IRIS builds with `%AI` (AI Hub). They are
deployed where the server has `%AI` and embedded Python, and inert until an MCP service is configured to use them.

---

## 5. Security settings for the HTTP server

These apply to `ivg server start` (`iris_vector_graph.cypher_api`), not to a Python application that holds an engine.
The details and every route are in the [HTTP server reference](ADMIN_API.md).

- **`IVG_API_KEY`.** Unset, the server asks for no key at all, and it listens on every interface (`0.0.0.0:8200`).
  Set, every route needs the key (`X-Api-Key` or `Authorization: Bearer`), except `/health` and the API docs; a
  route added later needs it too. Bolt clients send it as the `credentials` of `HELLO` or `LOGON`. Set it on any server
  another machine can reach.
- **`IVG_READ_ONLY=true`.** Refuses every write, after the key check: `POST`, `PUT`, `PATCH` and `DELETE` under
  `/admin/`, Cypher that names a write on the Cypher, Neo4j HTTP, GraphQL and Bolt routes, and FHIR event writes
  (`IVG_ALLOW_FHIR_WRITES=true` lets those through). Reads run as before.
- **`/health`** answers status only (`{"status": "ok"}` or `503`), with no count and no error text, and needs no key.
- **`IVG_FHIR_ALLOWED_BASES`** lists the FHIR base URLs a request may name; any other is refused, so a client cannot
  aim the server's requests at an internal address.
- The server connects to IRIS as `IRIS_USERNAME` (default `_SYSTEM`); give it a user with the privileges in
  [section 3](#3-privileges), not a superuser.

---

## 6. Upgrading

1. `pip install -U iris-vector-graph` where the application runs.
2. Look first: `engine.initialize_schema(dry_run=True)` writes nothing and reports what setup would do.
3. Run `engine.initialize_schema()` once in each namespace. Until it has run, a 4.6.0 engine refuses a namespace an
   earlier release set up ([the errors](#one-ivg-schema-per-namespace)).

Each release's steps, from whatever you run now, are in
[Upgrading from an older release](migration/upgrading-from-older-releases.md); 4.6.0's are in
[Migrating to 4.6.0](migration/v4.6.0.md). Releases whose first setup does long work (4.3.0 rebuilds the adjacency
index over every row, and 4.0.0 moves embeddings) say so there; run that setup as a job.

**The objects 4.5.x left in `SQLUser` and `User`.** 4.6.0 no longer uses the functions, views and classes 4.5.x
created there, and leaves them in place so SQL of yours that calls them keeps answering. Setup copied every grant on
them to their replacements in the IVG Schema. List them, then remove them once your own SQL names the IVG Schema:

```python
engine.remove_legacy_objects()
# → []
```

The list is empty here because this namespace never ran 4.5.x. Each item says what it is (`kind`, `sql_name`,
`class_name`) and the grants it holds; `remove_legacy_objects(dry_run=False)` removes exactly those. It works from a
fixed list of 4.5.x names, so a function of yours is never touched unless it has one of those names (a `User.SQL`
class of your own, say); read the list before removing. `engine.status().legacy_objects` counts them.

---

## 7. Inspecting a namespace

**In the Management Portal**, choose the namespace first (the namespace selector at the top of each page):

- _System Explorer > SQL_: schema `Graph_KG` (or yours) holds every IVG table, function and procedure.
- _System Explorer > Classes_: packages `Graph.KG`, `Graph.KG.Cypher`, `IVG` and, once plans compile, `IVGC`.
- _System Explorer > Globals_: `^KG`, `^NKG`, `^IVG.*` and the tables' storage globals.
- _System Operation > Task Manager_: any IVG tasks ([optional components](#task-manager-tasks)).

**From a terminal** (`iris session IRIS -U <namespace>`, or `zn "<namespace>"` first):

```objectscript
USER>write ##class(%Dictionary.CompiledClass).%ExistsId("Graph.KG.Eraser")
1
USER>write ^IVG.Deploy("schema")
Graph_KG
```

**From SQL**:

```sql
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME IN ('nodes', 'rdf_edges') ORDER BY TABLE_NAME
```

<!-- doc-test: output -->

```text
nodes
rdf_edges
```

IVG makes no views. Count them through `INFORMATION_SCHEMA.TABLES`, not `INFORMATION_SCHEMA.VIEWS`: on 2026.3,
`COUNT(*)` over `INFORMATION_SCHEMA.VIEWS` with that `WHERE` answers 18 in a namespace where `SELECT TABLE_NAME` over
the same view returns no row.

```sql
SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_TYPE = 'VIEW'
```

<!-- doc-test: output -->

```text
0
```

**From Python**, `engine.status()` reads the namespace and what answers the engine's calls:

```python
s = engine.status()
s.schema
# → 'Graph_KG'
s.tables.nodes, s.tables.edges
# → (2, 1)
s.adjacency.kg_edge_count
# → 1
s.ready_for_bfs
# → True
s.legacy_objects
# → 0
```

| Field                     | What it means                                                        |
| ------------------------- | -------------------------------------------------------------------- |
| `schema`                  | the namespace's recorded IVG Schema; `None` when nothing is recorded |
| `ready_for_bfs`           | `^KG` holds the adjacency and the graph has edges: traversals work   |
| `ready_for_vector_search` | the default embedding table holds vectors                            |
| `ready_for_full_text`     | a BM25 index exists                                                  |
| `adjacency.kg_edge_count` | edges in `^KG`; equals the edges in `rdf_edges`                      |
| `adjacency.nkg_populated` | `^NKG` is built (`engine.sync()`); the algorithms need it            |
| `adjacency.bfs_path`      | `"arno"` when the library answers BFS, `"objectscript"` otherwise    |
| `pending_sync`            | a bulk load left `^NKG` stale; run `engine.sync()`                   |
| `legacy_objects`          | 4.5.x objects still in `SQLUser` and `User`                          |
| `missing_routines`        | functions and procedures setup declares that the IVG Schema lacks    |

`print(engine.status().report())` prints the same as a short report. A routine setup could not create (the server
refused its `CREATE`) is not fatal: setup logs a warning with the server's error, names it at the end of its INFO
summary (`1 routine not created: kg_RRF_FUSE (…)`), and the report lists it under `Routines missing`. Running
`initialize_schema()` again tries it again.

---

## 8. Backup and restore

IVG's data is IRIS data: back up the namespace's globals database with your usual IRIS backup (online or external)
and you have all of it. The inventory's "Back up" column says what each global is worth when you choose what to keep:

- **Must keep**: the tables' storage globals; `^KG` (its temporal subscripts have no SQL copy); `^IVG.Ledger` when the
  ledger is on (canonical history); `^IVG.Deploy`; the settings `^IVG.TemporalStructural`, `^IVG.GraphMode`,
  `^IVG.CypherGen`; the index globals you built (`^BM25Idx`, `^VecIdx`, `^IVF`, `^PLAID`) and `^EmbedQueue`.
- **Rebuildable**: `^NKG` (`engine.sync()`), the caches `^IVG.AlgoCache`, `^IVG.CypherCompiled` with the `IVGC.*`
  classes, `^ArnoKG`, `^IVG.BuiltFrom`; `^ArnoAccel` (the next load writes it).
- **The code** is in the namespace's routine database. Running `initialize_schema()` re-creates it, and the functions
  and procedures, from the package.

After a restore, run `initialize_schema()` once (it re-creates any code the restore did not bring and checks the
indexes), then `engine.sync()` if `engine.status().adjacency.nkg_populated` is `False`, and `engine.ledger.verify()`
when the ledger is on. IVG's own snapshots (`save_snapshot`, `restore_snapshot`) copy a namespace's IVG tables and
globals to a file and back ([User Guide](USER_GUIDE.md)); a snapshot made through the HTTP server goes to
`IVG_SNAPSHOT_DIR`, the temporary directory unless you set it
([known issue](KNOWN_ISSUES.md#snapshots-made-through-the-rest-api-land-in-the-temporary-directory)).

**Stop IRIS before its container.** IRIS data survives a container restart cleanly only when IRIS was stopped first;
otherwise it recovers on the next start, which takes from 30 s to several minutes. With the compose file:

<!-- doc-test: skip reason="stops the IRIS container; the doc tests run in a shared test container" -->

```bash
docker compose exec iris iris stop IRIS quietly
docker compose stop
```

In production, make the orchestrator run `iris stop` before it kills the container.

---

## 9. Maintenance

### The adjacency indexes `^KG` and `^NKG`

`^KG` is kept from the rows by functional indexes on `rdf_edges` and `rdf_labels`, which setup declares: every SQL
write keeps it current in the same transaction (the engine's writers, the bulk loaders, Cypher writes, ledger commits,
and your own `INSERT`, `UPDATE` and `DELETE`). It never needs rebuilding, except in a namespace without IVG's
classes, where no index can be declared.

`^NKG`, the integer-keyed copy the algorithms and BFS read, is not kept from the rows. After a bulk load, or when
`engine.status().pending_sync` is `True`, sync it:

```python
engine.sync()
# → True
engine.status().adjacency.nkg_populated
# → True
```

`engine.sync()` rebuilds `^KG` from the rows, then `^NKG` from `^KG`, in place: a reader during the rebuild never sees
it empty. One rebuild runs at a time (lock `^IVG.NKGBuild`). It took 422 s for LDBC SF10. A variable-length Cypher
query that would read a stale `^NKG` raises `IndexNotSyncedError` rather than answer from it. `bulk_ingest_edges()`
syncs before returning unless you pass `auto_sync=False`, which suits a load in several batches with one `sync()` at
the end. From a terminal, `Do ##class(Graph.KG.Traversal).BuildNKG()` rebuilds `^NKG` alone.

### Table statistics

Setup runs `TUNE TABLE` over the core tables. After a large load into a namespace that was small when it was tuned,
tune again; `bulk_load_session` does it itself when a session wrote 10,000 rows or more:

```python
from iris_vector_graph.schema import GraphSchema

cursor = engine.conn.cursor()
GraphSchema.tune_tables(cursor, tables=["nodes"])
# → {'Graph_KG.nodes': True}
engine.conn.commit()
```

Without it, `EXPLAIN` of a graph-scoped read says `Warning: Table Graph_KG.nodes is not tuned.`, and
`WHERE node_id = ? AND graph_id = ?` can plan as a scan of the whole table.

### Bulk loads: choose a chunk size

`Graph.KG.EdgeScan.BulkIngestEdgesSQL` and `BulkIngestNodesSQL` load the batch you hand them in one transaction, and a
transaction's cost per row grows with its size. Measured on the enterprise test container, 10,000 edges:

| Rows per call | Edges/s |
| ------------- | ------- |
| 250           | 13,803  |
| 500           | 13,123  |
| 1,000         | 10,989  |
| 2,000         | 11,093  |
| 5,000         | 7,885   |
| 10,000        | 6,426   |

A few hundred rows per call is the fast shape; one enormous call is the slowest, and its failure rolls back the most.

### BM25 indexes built before 4.2

A BM25 index built before 4.2 is schema 1: it can be searched, but inserts and deletes raise until it is converted
once with `engine.bm25_upgrade(name)` (one pass; 2.9 s for 34,000 documents). An index built since is schema 2:

```python
engine.bm25_build("servers", ["name"])
engine.bm25_info("servers")["schema"]
# → 2
engine.bm25_upgrade("servers")
# → {'upgraded': 0, 'already': 1}
```

Writers are refused while an upgrade runs; `Search` keeps working.

### Embedding tables, the registry and the quarantine

Every embedding write is checked against `embedding_registry`, which records each embedding table's model and width.
Read it when a write is refused with `EmbeddingIdentityConflict`:

```sql
SELECT table_name, dimension, set_by FROM Graph_KG.embedding_registry WHERE table_name = 'kg_NodeEmbeddings'
```

<!-- doc-test: output -->

```text
kg_NodeEmbeddings | 768 | adopted
```

`set_by` is `adopted` (taken from the column's declaration at setup: the width is enforced, the model unknown),
`claimed` (a writer declared a model; both are enforced from then on) or `forced` (an operator overrode it with
`set_embedding_identity(..., force=True)`, which leaves every vector already in the table scored against a model that
did not make it: re-embed after forcing). `engine.embedding_inventory()` reports each route with its row count (counted
inside its graph) and whether IRIS holds an HNSW index on it:

```python
row = [r for r in engine.embedding_inventory() if r.table_name == "kg_NodeEmbeddings"][0]
row.dimension, row.row_count, row.index_state
# → (768, 0, 'absent')
```

`index_state` is `present`, `refused` (IRIS would not build one; `index_error` says why) or `absent`. A route without
an index is not broken: `kg_KNN_VEC` scans it with `VECTOR_COSINE`, correct and linear in its rows. Setup builds no
index on `kg_NodeEmbeddings`; a `kg_emb_<hash>` route gets one when it is created, if IRIS accepts it. 50 graphs x 2
models (100 routes) is the tested ceiling, measured on the enterprise test container (`tests/e2e/test_227_scale.py`):
creating them took 12.4 s, one inventory read 1.49 s, erasing them 2.0 s. Nothing caps the number of routes, so watch
it in the inventory. Each route also records the recall of its scoped search against an exact scan
(`recall_measured`, `recall_measured_at` in the registry); `NULL` means never measured, not perfect.
`tests/e2e/test_227_recall.py` measured recall@10 of 1.0 on two indexed routes. Measure again after a bulk load or an
index rebuild.

`embedding_quarantine` holds vectors the 4.0.0 migration could not place in a graph. No search reaches them; an
operator places each one, naming its graph:

```python
engine.list_quarantine()
# → []
```

`engine.place_quarantined(q_rowid, graph=..., model_key=...)` moves one row, and refuses (leaving it there) when the
pair has no route, the widths disagree, or the node is not in that graph. The 4.0.0 upgrade is described in
[Migrating to 4.0.0](migration/v4.0.0.md).

### Retiring temporal nodes

A temporal edge asserts its two endpoint nodes and a marked structural row (ADR-0009). The purge deletes the row when
the triple's last timestamp goes and, unless asked, keeps the nodes: one edgeless node per distinct target. A node is
**bare** when no edge names it and it has no label, property, document or embedding.

- **Going forward**, pass the option to the purge: `engine.purge_raw_before(ts_end, graph=g, retire_bare_nodes=True)`
  (`purge_before(ts, retire_bare_nodes=True)` for the default graph). It costs about 53 µs a retired node.
- **For nodes already there**, sweep, counting first:

```python
counted = engine.erase_bare_nodes(graph="g", dry_run=True)
counted.bare, counted.erased, counted.dry_run
# → (0, 0, True)
```

then `engine.erase_bare_nodes(graph="g", batch_size=10000, min_age_seconds=60)`, or
`Graph.KG.Eraser.EraseBareNodes(graph, batch, dryRun, minAgeSeconds)` from ObjectScript: 17,600 nodes a second. It is
idempotent, and `kept` counts nodes found bare and left because an edge, label or embedding arrived since the scan.
The sweep is a plain `DELETE`, which the foreign keys refuse while a dependant exists, so it cannot erase a node a
writer just used; `rdf_props` and `docs` have no foreign key, and `min_age_seconds` keeps the sweep off nodes young
enough to be getting one.

### Temporal-only predicates

For a predicate you never traverse (one observation per sample), the structural row each temporal edge implies costs 6
to 8 times the write, and one node per target for ever. Register it **temporal-only** and its temporal edges write the
temporal stores and nothing else ([ADR-0015](adr/0015-temporal-structural-rows-opt-out.md)):

```python
engine.set_temporal_structural("OBSERVED_AT", False, graph="tenant1")   # per graph and predicate; the default is True
engine.list_temporal_structural_opt_outs(graph="tenant1")
# → ['OBSERVED_AT']
```

- **Still answered**: window reads, buckets and aggregates, latest-event, distinct counts, velocity, bursts, the purge,
  the temporal-window Cypher walk, and anything that walks `^KG("tout", ...)` or `^KG("tin", ...)` directly.
- **No longer answered**: BFS, k-hop, shortest path, PPR, centrality, degree, and Cypher `MATCH` of that predicate. An
  empty `execute_cypher` or `execute_bfs` that names it logs one warning per graph and predicate.
- **Before an upgrade** from a release before 4.3.0, register first: setup's backfill then skips the predicate.
- **After**, the rows an earlier release wrote stay until you drop them. Register every predicate first (a still
  structural one raises `ValueError` before anything is deleted), count, then drop in a quiet period:

```python
preds = ["OBSERVED_AT", "EMITTED_AT", "METRIC_AT"]
for p in preds:
    engine.set_temporal_structural(p, False, graph="tenant1")
dry = engine.drop_temporal_structural_rows(preds, graph="tenant1", dry_run=True)
dry.rows, dry.dry_run
# → (0, True)
engine.drop_temporal_structural_rows(preds, graph="tenant1", batch_size=10000).rows
# → 0
```

The dry run reads no row: two counts per predicate through the indexes (0.6 to 0.9 s for 1.8 million rows). The real
run deletes about 10,000 rows a second, only rows that still carry the temporal marker, and the endpoint nodes that
leaves bare (`retire_bare_nodes=False` keeps them). It is idempotent and resumable. To go back,
`set_temporal_structural(p, True)` then `backfill_temporal_structural_rows(p, graph=...)` rewrites the rows from the
temporal stores. `erase_graph` keeps the registration; `erase_all` removes it.

---

## 10. Environment variables

The variables an application or the HTTP server reads. They are read by the Python process, not by IRIS. The HTTP
server's connection variables (`IRIS_HOST`, `IRIS_PORT`, `IRIS_NAMESPACE`, `IRIS_USERNAME`, `IRIS_PASSWORD`) and Bolt's
are in the [HTTP server reference](ADMIN_API.md#starting-the-server); the test suite's are in
[CONTRIBUTING.md](../CONTRIBUTING.md).

| Variable                     | Default                               | Effect                                                                                                        |
| ---------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `IVG_SCHEMA_PREFIX`          | `Graph_KG`                            | the IVG Schema an engine uses when `schema_prefix=` is not given                                              |
| `IVG_API_KEY`                | unset                                 | the HTTP server's key; unset, it asks for none ([section 5](#5-security-settings-for-the-http-server))        |
| `IVG_READ_ONLY`              | unset                                 | `true`: the HTTP server refuses every write                                                                   |
| `IVG_ALLOW_FHIR_WRITES`      | unset                                 | `true`: FHIR event writes pass `IVG_READ_ONLY`                                                                |
| `IVG_FHIR_ALLOWED_BASES`     | unset                                 | comma-separated FHIR base URLs a request may name                                                             |
| `IVG_URL`                    | `http://localhost:8200`               | the server the `ivg` command and `IVGClient` talk to                                                          |
| `IVG_SNAPSHOT_DIR`           | the system temporary directory        | where `POST /admin/snapshot` writes; set a durable directory                                                  |
| `IVG_NATIVE_CYPHER`          | `1`                                   | `0` sends every Cypher read to the SQL path (`native_cypher=False` for one engine)                            |
| `IVG_NATIVE_PARALLEL_MIN`    | `1500`                                | estimated work at which a native read is split across worker processes; `0` always, a negative value never    |
| `IVG_NATIVE_ROUTE_MS`        | `50`                                  | a shape native answers faster than this is also timed on SQL, and the faster path keeps it; `0` never times   |
| `IVG_CYPHER_SHADOW`          | unset                                 | `1` runs each native read on both paths and raises `NativeMismatch` where they differ; doubles the work       |
| `IVG_EDGESCAN`               | unset                                 | `1` makes a directed hop on the SQL path scan `^KG` through `MatchEdges` (the plan before 4.3.0)              |
| `IVG_DISABLE_ALGO_CACHE`     | unset                                 | `1` stops caching algorithm answers in `^IVG.AlgoCache`; every call computes                                  |
| `IVG_DISABLE_ARNO`           | unset                                 | `1` keeps the engine from loading the Arno library                                                            |
| `IVG_ARNO_LIB`               | `/usr/irissys/mgr/libarno_callout.so` | the library's path on the IRIS server, for both loaders, ahead of the path the last load recorded             |
| `IVG_FORCE_CLASS_DEPLOY`     | unset                                 | `1` makes setup compile IVG's classes and create its functions and procedures even when they are unchanged    |
| `IVG_EMBEDDED_PYTHON`        | probed                                | `0` or `1` overrides setup's check for embedded Python                                                        |
| `IVG_STRICT_NAMESPACE`       | unset                                 | `1` raises where an engine's `namespace=` names another namespace than its connection's (a warning otherwise) |
| `IVG_IGNORE_NAMESPACE_CHECK` | unset                                 | `1` silences that warning                                                                                     |
| `IVG_PROBE_IDLE_SECONDS`     | `30`                                  | seconds a connection may sit idle before `execute_cypher` checks it is alive; `0` checks on every call        |

---

## 11. Troubleshooting

**`IVGSchemaError` on an engine's first call.** The namespace was set up by an earlier release, was never set up,
or did not finish setup; or the engine asks for another schema than the namespace records. The message says which. See
[the errors](#one-ivg-schema-per-namespace).

**`IVGSchemaError` from setup naming a privilege.** The user running setup lacks that SQL privilege
([Privileges](#3-privileges)). Grant it and run setup again; it finishes what an earlier run started.

**`<CLASS DOES NOT EXIST> *Graph.KG.…`.** The call ran in a namespace IVG was not set up in: `%SYS` (a `%ZSTART`
routine, for instance) or another namespace. Run it in the namespace setup ran in, or run setup there.

**Algorithms answer `[]` for every node.** `^NKG` is not built. Run `engine.sync()`; then
`engine.status().adjacency.nkg_populated` is `True`.

**Algorithms are slower than the published numbers.** The published fast numbers were measured with the Arno library
loaded, which IVG does not ship; check `engine.status().report()`'s `Acceleration:` line. As of arno-callout 1.2.1
only global PageRank answers from the library ([Arno](#arno-the-native-accelerator)).

**An embedding write fails with a dimension mismatch or `EmbeddingIdentityConflict`.** The table's `emb` column is
declared for another width, or the registry records another width or model than the writer declares. Read the
registry ([Embedding tables](#embedding-tables-the-registry-and-the-quarantine)), then write at that width or re-embed.
Setup alters the column only while the table is empty; on a populated table it names the table in
`status["needs_manual_migration"]`, with a matching entry in `status["warnings"]`, and you must clear or drop it first.

**Setup's log shows `CREATE INDEX idx_props_key_valkey ... SQLCODE -400 ... ERROR #5017: Too many errors` (or
`<SUBSCRIPT>`) after an upgrade from 4.3.0 to 4.4.1.** Harmless: those releases built that index over `val` before
`val_key` existed, and a later step built the right one. Check that
`SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.INDEXES WHERE TABLE_NAME = 'rdf_props' AND INDEX_NAME = 'idx_props_key_valkey'`
lists `val_key`; if it lists `val`, run setup with 4.4.2 or later, which rebuilds it.

**Reads are interpreted, and `messages.log` says a process could not write `^IVG.CypherCompiled`.** The role may not
write the database or the routine database. The answers are right; set `^IVG.CypherGen("mode") = "off"` to stop the
attempt, or give the role write access ([compiled plans](#compiled-native-plans-ivgc)).

**A graph-scoped read is slow.** Look for `Warning: Table … is not tuned.` in `EXPLAIN`, and tune
([Table statistics](#table-statistics)).

**A temporal purge is slow.** About 40 µs per purged edge is the known rate
([known issue](KNOWN_ISSUES.md#a-temporal-purge-costs-about-40-µs-per-purged-edge)).

---

## Further reading

- [README: where IVG lives in a namespace](../README.md#where-ivg-lives-in-a-namespace)
- [User Guide](USER_GUIDE.md) and [API Reference](API_REFERENCE.md)
- [HTTP server reference](ADMIN_API.md), [ledger metrics for Prometheus](ledger-prometheus-hook.md)
- [Architecture](architecture/ARCHITECTURE.md): the globals' layout, the SQL schema, the classes
- [Benchmarks](BENCHMARKS.md#graph-algorithms): algorithm latency against networkx and Neo4j GDS
- [Architecture decision records](adr/): why the IVG Schema, the adjacency index and the temporal rows are as they are
- [Glossary](../CONTEXT.md): what the terms in these guides (IVG Schema, Graph Key, Native Reader, Owner …) mean
