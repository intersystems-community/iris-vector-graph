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

IVG is a Python package. It installs on the machine that runs the application, not on the IRIS server (code that
runs IVG inside IRIS is the exception: [IVG's Python package inside IRIS](#ivgs-python-package-inside-iris)):

<!-- doc-test: skip reason="an install; layer 3 (the newcomer walkthroughs) runs it on the built wheel" -->

```bash
pip install iris-vector-graph              # core
pip install iris-vector-graph[communities] # + igraph, leidenalg, networkx for Leiden in this Python process
pip install iris-vector-graph[full]        # everything
```

The core install runs every algorithm, Leiden included: `leiden_communities()` answers from IRIS. The `[communities]`
extra is used only where IRIS's route cannot, as when the namespace has a named graph with edges and the Arno library
is not loaded ([Communities](COMMUNITIES.md), the order of the routes); without it that case raises, naming the
packages.

For a first look, the repository's `docker-compose.yml` starts IRIS Community Edition with the SuperServer on host
port 1972 and the Management Portal on 52773 (`IVG_IRIS_PORT=11972 IVG_WEB_PORT=15773 docker compose up -d` when an
IRIS on the machine already holds those ports). It is a laptop setup: it un-expires the `_SYSTEM` password `SYS` and
publishes the ports on every interface. Compose names the container itself; `docker compose ps` shows it, and
`docker compose exec iris iris session IRIS -U USER` opens a terminal in it. Community Edition limits the cores it runs
on and ignores a mounted `iris.key`: on a host with more (64 cores, measured) it logs `Too many Cores (64) for
InterSystems IRIS Community License` and shuts down. Pin it with `cpuset: "0-7"` on the `iris` service in a
`compose.override.yml` ([Quickstart](setup/QUICKSTART.md), step 1), or run a licensed image.

**A namespace of your own.** Several sections below use a second namespace, or one whose routines are in a database
apart from its globals. IVG makes neither: create it as any IRIS namespace, in the Management Portal (System
Administration > Configuration > System Configuration > Namespaces) or in a `%SYS` terminal. The `Config.*` classes
take their properties as a local array passed by reference, which the Native API cannot pass, so run them through
`iris session`, not from Python. For a namespace `MYGRAPH` on a database of the same name, in the compose container:

<!-- doc-test: skip reason="creates a namespace and a database; run on ivg-iris-enterprise for 4.6.5" -->

```bash
docker compose exec -T iris iris session IRIS -U %SYS <<'EOF'
do ##class(%File).CreateDirectoryChain("/usr/irissys/mgr/mygraph/")
set db=##class(SYS.Database).%New(), db.Directory="/usr/irissys/mgr/mygraph/", sc=db.%Save() write sc,!
kill p set p("Directory")="/usr/irissys/mgr/mygraph/" set sc=##class(Config.Databases).Create("MYGRAPH",.p) write sc,!
kill p set p("Globals")="MYGRAPH",p("Routines")="MYGRAPH" set sc=##class(Config.Namespaces).Create("MYGRAPH",.p) write sc,!
halt
EOF
```

Each line writes `1`; anything else is an IRIS status to read with `$system.Status.GetErrorText(sc)`. For routines
apart from globals, create two databases the same way and name them in `p("Routines")` and `p("Globals")`. Then
connect to `MYGRAPH` and call `initialize_schema()` as in the README.

---

## 2. What IVG installs and where

### What setup does

IVG has no separate ObjectScript installation and nothing to copy onto the server. One call from Python sets up a
namespace. Run it from any machine with Python 3.10 or later and the package installed that reaches the IRIS
SuperServer port (1972 by default), as a user with `%All` in the namespace ([Privileges](#3-privileges)); nothing
needs to run on the IRIS host. Setup makes a fixed number of round trips (667 on a fresh namespace), so the link sets
its time: from a laptop 24 ms from the server a fresh setup took 44 to 62 s, on 2026.3.0AI and on Community 2026.1:

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
class it skipped and why, `objectscript_failed` each class that failed to load or compile, with the server's error, and
`warnings` lists anything you must act on. [What setup replies](#what-setup-replies) gives every key.

Setup loads only the classes in the Python package. When that deploy fails, it loads nothing in their place (no
directory on the server is read) and says why in `warnings`; fix the cause and run `initialize_schema()` again.

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
`User` (`legacy_objects`), the grants it would copy, any other schema holding IVG tables (`other_schemas`), and what
the engine's `embedding_dimension` would do to the vector tables (`embedding_dimension`: the tables it would alter to
that width, those already at it, and those it cannot change because they hold vectors of another width,
`needs_manual_migration`). It also lists what the run would create or build, under the keys the run replies them in:
the tables (`tables_created`), the functions and procedures (`routines`), the classes it would compile and skip
(`objectscript_compiled`, `objectscript_skipped`), the adjacency indexes it would declare (`adjacency_index`), and
whether it would build `^KG` and `^NKG` from the rows in `rdf_edges` (`kg_built`). On a namespace never set up that is
18 tables, 57 routines and every class the server can compile. It reads the catalog, `^IVG.Deploy` and
`Graph.KG.Meta`, and nothing else.

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

| Kind      | Names                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Present      | Purpose                                                                                                                                                                                                                                           | Optional, and how it is activated                          | Back up                                                   |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------- |
| package   | `Graph.KG`                                                                                                                                                                                                                                                                                                                                                                                                                                                       | after setup  | IVG's ObjectScript code; for `Graph_KG` also the IVG Schema's own package: the classes IRIS generates for its tables, functions and procedures, and `Graph.KG.SqlProcs`, which projects IVG's class-based SQL routines                            | no                                                         | routine database; setup re-creates the code, not the rows |
| package   | `Graph.KG.Cypher`                                                                                                                                                                                                                                                                                                                                                                                                                                                | after setup  | the native Cypher executor and its plan compiler                                                                                                                                                                                                  | no                                                         | routine database; setup re-creates it                     |
| package   | `IVG`                                                                                                                                                                                                                                                                                                                                                                                                                                                            | after setup  | `IVG.Percentile` (percentile aggregates) and `IVG.CypherEngine` (an embedded-Python entry point)                                                                                                                                                  | no                                                         | routine database; setup re-creates it                     |
| package   | `Graph.KG.docs`, `Graph.KG.rdfprops`                                                                                                                                                                                                                                                                                                                                                                                                                             | after setup  | classes IRIS generates for the iFind indexes on `docs.text` and `rdf_props.val`, in the package named after each table's class                                                                                                                    | no                                                         | no: IRIS re-creates them with the index                   |
| package   | `IVGC`                                                                                                                                                                                                                                                                                                                                                                                                                                                           | on first use | compiled native plans, one class `IVGC.P<n>` per read shape, written and compiled at run time                                                                                                                                                     | yes: `^IVG.CypherGen("mode") = "off"` stops it             | no: rebuilt on first use; a stale one never runs          |
| table     | `nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`                                                                                                                                                                                                                                                                                                                                                                                                                  | after setup  | the graph: nodes, edges, labels and properties, each row keyed by its graph                                                                                                                                                                       | no                                                         | yes                                                       |
| table     | `rdf_reifications`, `docs`                                                                                                                                                                                                                                                                                                                                                                                                                                       | after setup  | edge reification (statements about edges); text documents attached to nodes (`kg_TXT`)                                                                                                                                                            | no                                                         | yes                                                       |
| table     | `kg_NodeEmbeddings`, `kg_EdgeEmbeddings`, `kg_NodeEmbeddings_optimized`                                                                                                                                                                                                                                                                                                                                                                                          | after setup  | node and edge vectors of the default route, at the engine's `embedding_dimension`; `kg_NodeEmbeddings_optimized` is a 3.x layout kept for old installs                                                                                            | no                                                         | yes                                                       |
| table     | `embedding_registry`, `embedding_quarantine`                                                                                                                                                                                                                                                                                                                                                                                                                     | after setup  | which model and width each embedding table holds, checked on every write; vectors the 4.0.0 migration could not place in a graph                                                                                                                  | no                                                         | yes                                                       |
| table     | `rdf_namespaces`, `relationship_mappings`, `table_mappings`                                                                                                                                                                                                                                                                                                                                                                                                      | after setup  | RDF prefixes; the mappings that expose your own SQL tables as a graph                                                                                                                                                                             | no                                                         | yes                                                       |
| table     | `fhir_bridges`, `code_crosswalk`                                                                                                                                                                                                                                                                                                                                                                                                                                 | after setup  | links from FHIR codes to graph nodes; code-system crosswalks                                                                                                                                                                                      | no                                                         | yes                                                       |
| table     | `fhir_graphs`, `fhir_canonical_refs`, `fhir_unresolved`, `fhir_definitions`                                                                                                                                                                                                                                                                                                                                                                                      | on first use | the FHIR graph: registered repositories and how their resources link ([FHIR graph](FHIR_GRAPH.md)), made by the first `fhir_graph_register`                                                                                                       | yes: only if you register a FHIR repository                | yes                                                       |
| table     | `ledger_revisions`, `ledger_stats`                                                                                                                                                                                                                                                                                                                                                                                                                               | after setup  | the revision ledger's commits and counters (classes `Graph.KG.LedgerRevision`, `Graph.KG.LedgerStats`)                                                                                                                                            | no                                                         | yes                                                       |
| table     | `kg_emb_<hash>`, `kg_eemb_<hash>`                                                                                                                                                                                                                                                                                                                                                                                                                                | on first use | one table of node (or edge) vectors per graph and embedding model, made by the first write that names the pair, with an HNSW index when IRIS accepts one                                                                                          | yes: only for writes that name a graph and model           | yes                                                       |
| function  | `CY_ARITH`, `CY_CMP`, `CY_DURATION_FIELD`, `CY_EXP_ORDKEY`, `CY_EXP_ORDVAL`, `CY_FLOAT_TEXT`, `CY_IS_FLOAT`, `CY_JCMP`, `CY_JSON_LISTMAP`, `CY_JSON_NUM`, `CY_JSON_TYPE`, `CY_JSON_TYPED`, `CY_LIST_ELEM`, `CY_LIST_HAS`, `CY_LIST_SRC`, `CY_LIST_VAL`, `CY_PROPS_MAP`, `CY_REL_PROP`, `CY_REL_TOSTRING`, `CY_RETYPE_MAP`, `CY_REVERSE`, `CY_SORT_KEY`, `CY_STR_SIZE`, `CY_TEMPORAL_ARITH`, `CY_TEMPORAL_FIELD`, `CY_TOSTRING`, `CY_TYPED`, `CY_VALUE_TYPE`      | after setup  | Cypher expression helpers the SQL path calls: comparison, sort keys, maps, temporal arithmetic, number text, property types                                                                                                                       | no                                                         | no: setup re-creates them                                 |
| function  | `CY_VLP_CHAIN`, `CY_VLP_DISJOINT`, `CY_VLP_ENDS`, `CY_VLP_HAS`, `CY_VLP_OPT`, `CY_VLP_PATHS`                                                                                                                                                                                                                                                                                                                                                                     | after setup  | variable-length path walks on the SQL path                                                                                                                                                                                                        | no                                                         | no: setup re-creates them                                 |
| function  | `JSON_ARRAYGET`, `JSON_ARRAYLENGTH`, `JSON_VALUE`, `JSON_KEYS`, `LIST_CONCAT`, `LIST_REVERSE`, `LIST_TAIL`, `NEWID`, `RAND`, `REGEX_MATCH`, `STR_SPLIT`                                                                                                                                                                                                                                                                                                          | after setup  | JSON, list and string helpers for Cypher's functions (`keys()`, `split()`, `=~`, `rand()`, `randomUUID()` …)                                                                                                                                      | no                                                         | no: setup re-creates them                                 |
| function  | `CypherFn_IVGJSONARRAYLENGTH`, `CypherFn_IVGJSONKEYS`, `CypherFn_IVGJSONREMOVE`, `CypherFn_IVGJSONSET`, `CypherFn_IVGJSONVALUE`, `CypherFn_IVGLISTGET`, `CypherFn_IVGLISTHEAD`, `CypherFn_IVGLISTLAST`, `CypherFn_IVGLISTREVERSE`, `CypherFn_IVGLISTTAIL`, `CypherFn_IVGMAPGET`, `CypherFn_IVGNEWID`, `CypherFn_IVGRAND`, `CypherFn_IVGRANGE`, `CypherFn_IVGREGEXMATCH`, `CypherFn_IVGSTRSPLIT`, `CypherFn_IVGTYPEERROR`, `Percentile_PCONT`, `Percentile_PDISC` | after setup  | the same family written in ObjectScript (`Graph.KG.CypherFn`, `IVG.Percentile`), projected by `Graph.KG.SqlProcs`                                                                                                                                 | no                                                         | no: setup re-creates them                                 |
| function  | `kg_PPR`, `kg_Betweenness`, `kg_Closeness`, `kg_DegreeCentrality`, `kg_Eigenvector`, `kg_KCore`, `kg_Leiden`, `kg_SCC`, `kg_TriangleCount`                                                                                                                                                                                                                                                                                                                       | after setup  | graph algorithms callable from SQL and from Cypher's `CALL ivg.*`; all but `kg_PPR` and `kg_DegreeCentrality` read `^NKG` and raise once a named graph has an edge, as their `CALL` does                                                          | no                                                         | no: setup re-creates them                                 |
| function  | `kg_BM25`, `kg_IVF`, `MatchEdges`, `MatchEdgesAllGraphs`, `DijkstraPath`, `backfill_temporal_rows`, `rebuild_edge_indices`                                                                                                                                                                                                                                                                                                                                       | after setup  | BM25 and IVF search, edge scans of `^KG`, weighted shortest path, and two upgrade helpers                                                                                                                                                         | no                                                         | no: setup re-creates them                                 |
| function  | `Txn_Begin`, `Txn_End`, `Txn_Undo`                                                                                                                                                                                                                                                                                                                                                                                                                               | after setup  | IVG's own part of a write, nested in a transaction the caller opened (4.6.4)                                                                                                                                                                      | no                                                         | no: setup re-creates them                                 |
| procedure | `kg_KNN_VEC`, `kg_TXT`, `kg_RRF_FUSE`, `ledger_records`                                                                                                                                                                                                                                                                                                                                                                                                          | after setup  | vector search, text search and their rank fusion; the ledger's records as a result set                                                                                                                                                            | no                                                         | no: setup re-creates them                                 |
| function  | `kg_VEC_ROUTE_GUARD`                                                                                                                                                                                                                                                                                                                                                                                                                                             | after setup  | the refusal `kg_KNN_VEC` (and `kg_RRF_FUSE` through it) raises for a named graph whose vectors have a route of their own                                                                                                                          | no                                                         | no: setup re-creates it                                   |
| function  | `docs_idxdocstextifindFind`, `docs_idxdocstextifindHighlight`, `docs_idxdocstextifindRank`, `rdfprops_idxpropsvalifindFind`, `rdfprops_idxpropsvalifindHighlight`, `rdfprops_idxpropsvalifindRank`                                                                                                                                                                                                                                                               | after setup  | generated by IRIS for the two iFind indexes (`docs.text`, `rdf_props.val`)                                                                                                                                                                        | no                                                         | no: IRIS re-creates them with the index                   |
| procedure | `nodes_Extent`, `rdfedges_Extent`, `rdflabels_Extent`, `rdfprops_Extent`, `rdfreifications_Extent`, `docs_Extent`, `kgNodeEmbeddings_Extent`, `kgEdgeEmbeddings_Extent`, `kgNodeEmbeddingsoptimized_Extent`, `embeddingregistry_Extent`, `embeddingquarantine_Extent`, `rdfnamespaces_Extent`, `relationshipmappings_Extent`, `tablemappings_Extent`, `fhirbridges_Extent`, `codecrosswalk_Extent`, `LedgerRevision_Extent`, `LedgerStats_Extent`                | after setup  | generated by IRIS for every table: the extent query of the table's class, named from the class                                                                                                                                                    | no                                                         | no: IRIS re-creates them with the table                   |
| procedure | `fhirgraphs_Extent`, `fhircanonicalrefs_Extent`, `fhirunresolved_Extent`, `fhirdefinitions_Extent`                                                                                                                                                                                                                                                                                                                                                               | on first use | the same for the FHIR graph tables, and one for each `kg_emb_<hash>` route table                                                                                                                                                                  | yes: with their tables                                     | no                                                        |
| function  | `ivg_arno_zf_call_2s`, `ivg_arno_zf_call_kg_kcore`, `ivg_arno_zf_call_kg_leiden`, `ivg_arno_zf_call_kg_scc`, `ivg_arno_zf_call_kg_triangle`, `ivg_arno_zf_get_result_chunk`, `ivg_arno_zf_get_result_len`, `ivg_arno_zf_kcore_json`, `ivg_arno_zf_kcore_run`, `ivg_arno_zf_leiden_json`, `ivg_arno_zf_leiden_run`, `ivg_arno_zf_probe`, `ivg_arno_zf_scc_json`, `ivg_arno_zf_scc_run`, `ivg_arno_zf_triangle_json`, `ivg_arno_zf_triangle_run`                   | on first use | calls into the Arno library, made the first time k-core, Leiden, SCC or triangle counting asks for it, whether or not the library is installed                                                                                                    | yes: `IVG_DISABLE_ARNO=1` in the application's environment | no: re-created on use                                     |
| function  | `ivg_arno_build_adj`, `ivg_arno_get_node_chunk`                                                                                                                                                                                                                                                                                                                                                                                                                  | on first use | the walk of `^KG` that uploads the adjacency to the Arno library, made the first time k-core, Leiden, SCC or triangle counting runs on the loaded library (measured on 2026.3.0AI); never made where the library is not loaded                    | yes: `IVG_DISABLE_ARNO=1` in the application's environment | no: re-created on use                                     |
| function  | `ivg_arno_adj_append`                                                                                                                                                                                                                                                                                                                                                                                                                                            | on first use | the same upload sent from Python in 30 KB chunks: made only by `build_kg_adjacency_chunked(conn, server_side=False)` in `iris_vector_graph.stores.arno_bridge`, which IVG itself does not call, and by setup when it copies a 4.5.x grant onto it | yes: `IVG_DISABLE_ARNO=1` in the application's environment | no: re-created on use                                     |

IVG creates no views, and nothing in `SQLUser` or the `User` package. A namespace upgraded from 4.5.x still holds
the objects 4.5.x put there until you remove them ([Upgrading](#6-upgrading)).

The four graph tables, for an operator reading them in SQL. `graph_id` is `''` for the default graph, never NULL.

- **`nodes`**: `node_id`, `graph_id`, `created_at` (a TIMESTAMP set when the row is written). A node ID is unique
  within its graph, not across graphs.
- **`rdf_labels`**: `graph_id`, `s` (the node ID), `label`: one row per label.
- **`rdf_props`**: `graph_id`, `s` (the node ID), `key` (the property name), `val` (the value, up to 64,000
  characters), `val_key` (the first 128 characters of `val`, computed by IRIS for the index on `key` and value). A
  node written by `create_node` has an `id` row too, holding its ID.
- **`rdf_edges`**: `edge_id` (an integer identity), `graph_id`, `s` (the source node ID), `p` (the relationship
  type), `o_id` (the target node ID), `qualifiers` (the edge's properties, a `%Library.DynamicObject`), `ekey` (0,
  except on the parallel edges of a multigraph, where it tells two edges of one type between the same two nodes
  apart). An edge's properties are its `qualifiers`; the node `reify_edge` attaches to an edge keeps its own in
  `rdf_props`, under that node's ID.

A node's properties, from the default graph:

```sql
SELECT "key", val
FROM Graph_KG.rdf_props
WHERE s = 'demo1' AND graph_id = ''
ORDER BY "key"
```

For the node `create_node("demo1", labels=["Gene"], properties={"name": "BRCA1", "year": 2014})` this answers `id
demo1`, `name BRCA1`, `year 2014`.

`kg_KNN_VEC` and `kg_RRF_FUSE` read `kg_NodeEmbeddings` only, which holds the default graph's vectors. A named
graph's vectors written through the API are in the graph's own route (a `kg_emb_<hash>` table), so for that graph
both procedures raise an error. The error names the route's table and the calls that do read it:
`engine.kg_KNN_VEC`, `engine.kg_RRF_FUSE`, `CALL ivg.retrieve` under `USE GRAPH`, and the `/vectorSearch` and
`/hybridSearch` routes. Before 4.6.0 they answered no rows for such a graph, or failed on the route's width. Every
user who calls `kg_KNN_VEC` (or `kg_RRF_FUSE`) needs EXECUTE on `kg_VEC_ROUTE_GUARD`, which `kg_KNN_VEC` calls.

**Globals**

All in the namespace's default globals database unless you map them elsewhere.

<!-- inventory -->

| Kind    | Names                                                                                                        | Present        | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Optional, and how it is activated                                     | Back up                                                                 |
| ------- | ------------------------------------------------------------------------------------------------------------ | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| global  | `^IVG.Deploy`                                                                                                | after setup    | the IVG Schema record (`"schema"`), the fingerprint of the classes last compiled (`"fingerprint"`) and those of the functions and procedures setup created (`"routines"`), which let a re-run skip an unchanged class set and unchanged routines                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | no                                                                    | yes; without it the engine refuses the namespace until setup runs again |
| storage | `^<hash>.<n>`                                                                                                | after setup    | the rows and indexes of the DDL tables above: each table keeps them in globals named from a hash of its class (`^<hash>.1` the rows, the others its indexes), which the class storage names                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | no                                                                    | yes: they are the tables                                                |
| storage | `^Graph.KG.LedgerRevisionD`, `^Graph.KG.LedgerRevisionI`, `^Graph.KG.LedgerStatsD`, `^Graph.KG.LedgerStatsI` | when activated | the rows and indexes of `ledger_revisions` and `ledger_stats`, named by their classes' storage, written when the ledger is enabled; `^Graph.KG.LedgerStatsI` stays empty, as that table's one index is its row ID                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | yes: `engine.ledger.enable()`                                         | yes: they are the tables                                                |
| global  | `^KG`                                                                                                        | after setup    | setup writes `"__version"` and nothing else, a counter and not a version number: the Graph-Version Token, which every write that can change an answer bumps by one (setup leaves 2), so it only goes up, and a rolled-back write leaves it bumped; a cached store (the Arno snapshot, `^NKG`, the two-hop statistics, cached algorithm answers) records the value it was built at and is stale once it has moved; every row written then keeps the adjacency index (`"out"`, `"in"`, `"label"`) and each source's generation (`"gen"`), degree reads cache degrees (`"degc"`, and the whole-graph stamp `"degw"` when setup or `sync()` warms them), and the temporal index (`"tout"`, `"tin"`, `"bucket"`, `"tagg"`, `"tlast"`, `"edgeprop"`) has no SQL copy | no                                                                    | yes: the temporal part exists nowhere else                              |
| global  | `^NKG`                                                                                                       | on first use   | the integer-keyed copy of `^KG` the graph algorithms and BFS read, built by `engine.sync()`, and once by setup: the first setup run that finds rows in `rdf_edges` builds `^KG` and `^NKG` from them (and marks it in `^Graph.KG.Meta`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | no                                                                    | no: `engine.sync()` rebuilds it (minutes on a large graph)              |
| global  | `^IVG.Ledger`                                                                                                | when activated | the revision ledger: head, mutation records, statement identities                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | yes: `engine.ledger.enable()`                                         | yes: canonical history, not rebuildable                                 |
| global  | `^IVG.TemporalStructural`, `^IVG.GraphMode`, `^IVG.CypherGen`                                                | when activated | settings: temporal-only predicates, multigraph graphs, the compiled-plan mode                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | yes: `set_temporal_structural`, `set_multigraph`, a `Set` of the mode | yes: they are configuration                                             |
| global  | `^IVG.AlgoCache`                                                                                             | on first use   | cached answers of PageRank, PPR and other algorithms, keyed by the graph's version                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | yes: `IVG_DISABLE_ALGO_CACHE=1`                                       | no: a cache                                                             |
| global  | `^IVG.CypherCompiled`                                                                                        | on first use   | one entry per compiled native plan shape (`IVGC.*`), with its status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | yes: `^IVG.CypherGen("mode") = "off"`                                 | no: a cache                                                             |
| global  | `^IVG.BuiltFrom`, `^IVG.warnings`, `^Graph.KG.Meta`                                                          | on first use   | which write a derived store was built from; warnings centrality runs left; build flags                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | no                                                                    | no                                                                      |
| global  | `^BM25Idx`, `^VecIdx`, `^IVF`, `^PLAID`                                                                      | on first use   | BM25 text, VecIndex, IVF and PLAID indexes, made by their `*_build` calls                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | yes: only if you build one                                            | yes, or rebuild them from the tables                                    |
| global  | `^EmbedQueue`, `^IVG.EmbedQueueTask`                                                                         | on first use   | the embedding queue (`enqueue_for_embedding`) and the id of the Task Manager task that works it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | yes: `enqueue_for_embedding`, `start_background_embedding`            | the queue yes; the task id no                                           |
| global  | `^ArnoAccel`                                                                                                 | when activated | the path of the Arno library the namespace loads (`"lib_path"`), written when a load succeeds; where the library is at a path the engine tries, setup's own engine loads it, so it is there after setup                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes: the library on the server ([Arno](#arno-the-native-accelerator)) | no                                                                      |
| global  | `^ArnoKG`                                                                                                    | on first use   | caches of the adjacency in the shape the Arno library and the BFS reader take                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | no                                                                    | no: a cache                                                             |
| global  | `^bcParScore`, `^bcParSrc`, `^bcBitAdj`                                                                      | on first use   | work areas of a parallel betweenness run, one subtree per run's process (its worker processes read them), deleted when it ends; a killed run's are deleted by the next run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | no                                                                    | no                                                                      |
| global  | `^IRIS.IF.TransfoB`, `^IRIS.IF.WordCP`, `^IRIS.IF.WordD`, `^IRIS.IF.WordI`, `^IRIS.IF.WordRI`                | on first use   | IRIS's iFind dictionary (the words and transformations its iFind indexes share), in the namespace's globals database. IRIS makes it for IVG's iFind indexes (`rdf_props.val`, `docs.text`) when the first node property is written, not at setup. IRIS lists these globals among its system globals                                                                                                                                                                                                                                                                                                                                                                                                                                                            | no                                                                    | yes, with the tables: the iFind indexes' entries refer to it            |

Not listed: process-private globals (`^||…`), which no other process sees and nothing backs up; IVG's work areas in
`IRISTEMP` (`^IRIS.Temp.IVG.Cypher`, `^IRIS.Temp.IVG.VL`); and lock names that hold no data (`^IVG.AdjW`,
`^IVG.NKGBuild`, `^IVG.FHIRGraph`).

#### Classes setup skips

Setup compiles only what the server can run. A server without embedded Python, or without the `%AI` classes of IRIS's
AI Hub builds, gets these classes skipped, each with its reason in `status["objectscript_skipped"]`, word for word as
below: a class with Python methods is skipped for that first, one naming `%AI` classes the server lacks for those,
and a class naming a skipped class for that. Everything else works without them.

<!-- inventory: skipped -->

| Class                       | Skipped when the server has no | Reason setup gives                                                                                                           |
| --------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `Graph.KG.PyOps`            | embedded Python                | `needs embedded Python, which this server does not have`                                                                     |
| `Graph.KG.Communities`      | embedded Python                | `needs embedded Python, which this server does not have`                                                                     |
| `Graph.KG.PageRankEmbedded` | embedded Python                | `needs embedded Python, which this server does not have`                                                                     |
| `IVG.CypherEngine`          | embedded Python                | `needs embedded Python, which this server does not have`                                                                     |
| `Graph.KG.Service`          | embedded Python                | `depends on Graph.KG.PyOps, which is skipped`                                                                                |
| `Graph.KG.MCPTools`         | embedded Python, or `%AI`      | `needs embedded Python, which this server does not have`, `needs %AI.Tool, not installed on this server`                     |
| `Graph.KG.MCPToolSet`       | `%AI`, or embedded Python      | `needs %AI.Policy.ConsoleAudit, %AI.ToolSet, not installed on this server`, `depends on Graph.KG.MCPTools, which is skipped` |
| `Graph.KG.MCPService`       | `%AI`, or embedded Python      | `needs %AI.MCP.Service, not installed on this server`, `depends on Graph.KG.MCPToolSet, which is skipped`                    |

On IRIS Community 2026.1, which has embedded Python and no `%AI`, setup skips the three `MCP` classes.
`IVG_EMBEDDED_PYTHON=0` (or `1`) in the application's environment overrides the embedded-Python probe.

### Running setup again

Run `initialize_schema()` again after every upgrade of the package, after restoring a namespace, or when a class or
function is missing. A re-run changes no row and duplicates nothing:

```python
engine.execute_cypher("CREATE (:Server {name: 'a'})-[:CALLS]->(:Server {name: 'b'})")
plan = engine.initialize_schema(dry_run=True)
plan["tables_created"], plan["routines"]["created"], plan["kg_built"]
# → ([], [], True)
status = engine.initialize_schema()
status["schema_recorded"], status["tables_created"], status["kg_built"]
# → ('existing', [], True)
engine.execute_cypher("MATCH (s:Server)-[:CALLS]->(t) RETURN s.name, t.name").rows
# → [['a', 'b']]
```

This is the first setup in the namespace that finds rows in `rdf_edges`, so it builds `^KG` and `^NKG` from them
(`kg_built`), and the dry run before it said it would. The next run builds nothing: `kg_built` is `False`.

Measured on a namespace that had run setup once (`tests/e2e/test_262_inventory.py`), a re-run with the same package
rewrites no class:

- it creates a function or procedure only when `INFORMATION_SCHEMA.ROUTINES` lacks it, or when its DDL text, with the
  IVG Schema, differs from the fingerprint recorded in `^IVG.Deploy("routines")` (as after a package upgrade that
  changes it). An unchanged re-run skips all 57 routines, so their classes are not recompiled and the cached queries
  that call them stay; one dropped by hand is created again on its own. `status["routines"]` lists those created and
  counts those unchanged; `IVG_FORCE_CLASS_DEPLOY=1` creates every one. On a small scratch namespace on the enterprise
  test container, a re-run took 0.6 s, and 1.0 s when it re-created all of them (39 when measured; three runs each);
- it compiles IVG's own classes only when their sources differ from the fingerprint in `^IVG.Deploy("fingerprint")`, as
  after an upgrade. `IVG_FORCE_CLASS_DEPLOY=1` compiles them anyway. A deploy that compiles also deletes the compiled
  plans (`IVGC.*`). The ledger table class declares its index `idx_ledger_correlation`, so reloading the class keeps
  it (before 4.6.0 a reload dropped it until the next setup); setup builds it when the ledger holds rows written
  before the index existed;
- it checks every table, column and index, creates only what is missing (`rdf_edges.graph_id` is altered only when it
  is not yet `NOT NULL DEFAULT ''`), and runs `TUNE TABLE` on the core tables (`status["tuned"]`).

Run it as a job outside busy hours when an upgrade brings a migration ([Upgrading](#6-upgrading) says which do).

### What setup replies

Every key of the dictionary `initialize_schema()` returns, and of the one `initialize_schema(dry_run=True)` returns.
"Returned by" says which: the run, the dry run, or both. In a dry run each key says what the run would do.

<!-- setup-status -->

| Key                      | Returned by | Meaning                                                                                                                                                                                                                                                                                          |
| ------------------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `schema`                 | both        | the IVG Schema: the one recorded in the namespace, else the engine's                                                                                                                                                                                                                             |
| `schema_recorded`        | both        | how it was chosen: `new` (nothing recorded and no IVG tables), `inferred` (IVG's tables in this schema alone), `explicit` (named with `schema_prefix=` beside another schema's IVG tables) or `existing` (already recorded). For the first three the run writes `^IVG.Deploy("schema")`          |
| `dry_run`                | both        | `True` for a dry run, `False` for a run                                                                                                                                                                                                                                                          |
| `tables_created`         | both        | the tables this run created, by name; `[]` when it created none, as on a re-run                                                                                                                                                                                                                  |
| `routines`               | both        | the functions and procedures setup creates with DDL, named as the catalog spells them (`kg_PPR`): `{"created": [names], "unchanged": count, "failed": {name: error}}`; `failed` holds an optional one whose `CREATE` the server refused (each also a warning)                                    |
| `objectscript_compiled`  | both        | the classes this run compiled; `[]` when their sources were unchanged since the last deploy, or the deploy did not run                                                                                                                                                                           |
| `objectscript_skipped`   | both        | `{class: reason}` for each class the server cannot compile ([Classes setup skips](#classes-setup-skips))                                                                                                                                                                                         |
| `objectscript_failed`    | run         | `{class: the server's error}` for each class that failed to load or compile                                                                                                                                                                                                                      |
| `objectscript_deployed`  | run         | `True` when IVG's classes are compiled in the namespace after the run, whether this run compiled them or found them unchanged                                                                                                                                                                    |
| `adjacency_index`        | both        | `{"idx_edges_adj": …, "idx_labels_adj": …}`, each `created` (declared by this run, which builds `^KG` from the rows already there) or `present`; `{"skipped": reason}` when it cannot be declared yet; and, from the run only, `temporal_rows_backfilled`: the temporal edges it gave rows first |
| `kg_built`               | both        | `True` when this run built `^KG` and `^NKG` from the rows in `rdf_edges`: the first setup in a namespace that finds rows does, once                                                                                                                                                              |
| `ifind_indexes`          | run         | `{index: searchable}` for the two iFind indexes; one that is not is also a warning                                                                                                                                                                                                               |
| `ledger_index`           | run         | `idx_ledger_correlation` on `ledger_revisions`: `present`, `created`, `skipped: …` or `not created: …` (also a warning)                                                                                                                                                                          |
| `tuned`                  | run         | `{table: True}` for each core table `TUNE TABLE` ran on                                                                                                                                                                                                                                          |
| `embedding_dimension`    | both        | the run: the width of the vector tables. The dry run: `{"configured", "alter", "unchanged", "needs_manual_migration"}`, the width and the tables it would alter to it, leave, or cannot change                                                                                                   |
| `needs_manual_migration` | run         | the vector tables holding rows at another width, which the run cannot alter (each also a warning)                                                                                                                                                                                                |
| `legacy_objects`         | both        | how many objects 4.5.x made in `SQLUser` and `User` are still there ([Upgrading](#6-upgrading))                                                                                                                                                                                                  |
| `grants_copied`          | both        | one record per direct grant on a 4.5.x object copied to its replacement in the IVG Schema                                                                                                                                                                                                        |
| `read_path_grants`       | both        | one record per EXECUTE grant on the Cypher read path: `{"routine", "subject", "privilege", "granted", "because"}` ([Privileges](#3-privileges))                                                                                                                                                  |
| `arno_wrappers_replaced` | both        | one record per Arno function in the IVG Schema replaced by the checked text: `{"routine", "because", "replaced"}`                                                                                                                                                                                |
| `legacy_arno_revoked`    | both        | one record per grant revoked on a 4.5.x Arno function in another schema                                                                                                                                                                                                                          |
| `legacy_arno_warnings`   | dry run     | the warnings the run would give for grants on those functions it cannot revoke; a run puts them in `warnings`                                                                                                                                                                                    |
| `ledger_history_copied`  | both        | `{table: rows}` copied from `Graph_KG`'s ledger tables into another IVG Schema's empty ones                                                                                                                                                                                                      |
| `other_schemas`          | both        | `{schema: {"nodes": rows, "rdf_edges": rows}}` for each other schema holding IVG's tables, left untouched                                                                                                                                                                                        |
| `warnings`               | run         | what you must act on, each also logged at WARNING                                                                                                                                                                                                                                                |

`routines` counts the 57 functions and procedures setup creates with `CREATE OR REPLACE` statements, and nothing else.
`INFORMATION_SCHEMA.ROUTINES` lists 112 in the IVG Schema after setup, as the inventory does: those 57; 31 that IVG's
classes project (`Graph.KG.SqlProcs`: the 17 `CypherFn_*`, `Percentile_PCONT`, `Percentile_PDISC`, `JSON_KEYS`,
`kg_BM25`, `kg_IVF`, `MatchEdges`, `MatchEdgesAllGraphs`, `DijkstraPath`, `backfill_temporal_rows`,
`rebuild_edge_indices`, `ledger_records`, and since 4.6.4 `Txn_Begin`, `Txn_End` and `Txn_Undo`), compiled with the
classes; 6 that IRIS makes for the two iFind indexes
(`docs_idxdocstextifind*` and `rdfprops_idxpropsvalifind*`: `Find`, `Highlight`, `Rank`); and 18 `*_Extent` procedures
IRIS makes for the tables. Before 4.6.0 (R050) the reply spelled them in upper case (`KG_PPR`).

---

## 3. Privileges

**Setup** is a deployer's job: it creates SQL tables, indexes, functions and procedures, compiles classes into the
routine database over the Native API, and writes `^IVG.Deploy`. Run it as a user with `%All` in the namespace. A
user without a DDL privilege it needs is stopped at the first statement that needs it, with `IVGSchemaError` naming
the user, the schema and the privilege (`%CREATE_TABLE`, `%CREATE_FUNCTION`, `%CREATE_PROCEDURE`); when that
statement was the first, nothing is created. The smallest deployer role has not been measured. It is known to need the
DDL privileges above, `%ALTER_TABLE` (indexes), write access to the namespace's databases, and the resources
`%Native_ClassExecution:U` and `%Native_GlobalAccess:U`.

**Use** needs much less. The table was first measured on 2026.3.0AI with a role that held only what it lists
([least-privilege notes](dev/notes/research/least-privilege-spike.md), October 2026). The notes predate 4.6.0 and
grant each function in `SQLUser`; since 4.6.0 every IVG function is in the IVG Schema, so the schema grants below
replace those. The reading row, as written here, is checked on 2026.3.0AI and on Community 2026.1 by
`tests/e2e/test_460_r202_reading_role_e2e.py`: right answers, every write refused, and no warning but one:
`betweenness_centrality` logs a WARNING carrying IRIS's `<PROTECT>` on every call (below).

| For            | Resources                                                                                                        | SQL, in the namespace                                                                 |
| -------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| reading        | `<database resource>:R`, `<routines database resource>:R`, `%Native_ClassExecution:U`, `%Native_GlobalAccess:U`  | `GRANT SELECT ON SCHEMA Graph_KG TO role`, `GRANT EXECUTE ON SCHEMA Graph_KG TO role` |
| reading, fully | `<database resource>:RW`, `<routines database resource>:R`, `%Native_ClassExecution:U`, `%Native_GlobalAccess:U` | the same                                                                              |
| writing        | `<database resource>:RW`, `<routines database resource>:R`, `%Native_ClassExecution:U`, `%Native_GlobalAccess:U` | the same, and `GRANT INSERT, UPDATE, DELETE ON SCHEMA Graph_KG TO role`               |

To make the reading row, open an `iris session` and run the following (the bullets below say how to look up the two
database resources; `<IVG Schema>` is `Graph_KG` unless you chose another name). The role and the user are made in
`%SYS`; the two `GRANT`s are SQL and run in the IVG namespace, where the schema is. A role and a user cannot share a
name (IRIS error `#942 Username and Role cannot have the same name`), and IRIS upper-cases both:

<!-- doc-test: skip reason="makes a role and a user, which live outside the scratch namespace" -->

```objectscript
%SYS>set sc = ##class(Security.Roles).Create("ivg_reader", "IVG reading role", "<database resource>:R,<routines database resource>:R,%Native_ClassExecution:U,%Native_GlobalAccess:U")
%SYS>write $system.Status.GetErrorText(sc)

%SYS>set sc = ##class(Security.Users).Create("ivg_reader_user", "ivg_reader", "<a password>", "IVG reader", "MYNS")
%SYS>write $system.Status.GetErrorText(sc)

%SYS>zn "MYNS"
MYNS>write ##class(%SQL.Statement).%ExecDirect(, "GRANT SELECT ON SCHEMA <IVG Schema> TO ivg_reader").%SQLCODE
0
MYNS>write ##class(%SQL.Statement).%ExecDirect(, "GRANT EXECUTE ON SCHEMA <IVG Schema> TO ivg_reader").%SQLCODE
0
```

An empty line after each `Create` means it worked, and `0` after a `GRANT` that it was made (`100` if the role already
held it). `MYNS` is your IVG namespace. Where its globals and routines are in two databases (the split layout) the
resource list holds both databases' resources, each with `R`, as written above; where they are one database, name its
resource once. For the "reading, fully" and writing rows change `:R` on the `<database resource>` to `:RW`, and for
writing add `GRANT INSERT, UPDATE, DELETE ON SCHEMA <IVG Schema> TO ivg_reader`. The user then connects to `MYNS` as
`ivg_reader_user` with that password.

- `<database resource>` is the resource of the namespace's globals database. Look it up rather than assume it: in the
  Portal (System Administration > Configuration > System Configuration > Local Databases, the database's _Resource_),
  or in `%SYS` with `write ##class(SYS.Database).%OpenId("<database directory>").ResourceName`. A database created
  without one has `%DB_%DEFAULT`; the repository's compose image's `USER` has `%DB_USER`. Without `:R` a login fails
  with `Access Denied`.
- `<routines database resource>` is the resource of the namespace's routines database, looked up the same way (the
  namespace's _Default Database for Routines_). Where globals and routines share one database, as in the compose
  image's `USER` and in a namespace made with one database, it is the same resource and the role holds it once. Where
  the routines are in a database of their own, the role needs `R` on that one too: every IVG class method the engine
  calls over the Native API runs code from the routines database, and IRIS refuses it without `R` there. Measured with
  the routines database left out, on 2026.3.0AI (`tests/e2e/test_460_r202_reading_role_e2e.py`) and, by the expert
  walkthrough of November 2026, on Community 2026.1: the calls that run an IVG class method over the Native API raise
  `<PROTECT>` naming the routines database (`<PROTECT>^%SYS.DBSRV.1 *^|^^<routines directory>|Graph.KG.PageRank.1`):
  `kg_PAGERANK()`, `degree_centrality()`, `sync()`, `ppr()` and
  `kg_PERSONALIZED_PAGERANK()` (these two on 2026.3.0AI and, by the walkthrough, on Community 2026.1 too). A Cypher `shortestPath` answers no rows with the `<PROTECT>` in `result.error` (it raises with
  `raise_on_error=True`). These answer right: Cypher reads on the SQL path,
  IVG's SQL functions such as `kg_PPR`, the `CALL ivg.*` procedures that run them (`ivg.betweenness`, `ivg.closeness`,
  `ivg.degreeCentrality`, `ivg.ppr`), `closeness_centrality()`, and `betweenness_centrality()` from its Python walk,
  with the WARNING below. The class dictionary is kept in the routines database too, and refused it answers that no
  class exists; the engine then asks one of IVG's classes, and on `<PROTECT>` every call that needs to know what is
  deployed (`khop()` among them) raises `ClassesUnreadable`, naming the routines database and this section. `status()`
  lists it among its errors. Before 4.6.0, `khop()` answered no nodes. No row writes the routines database: compiled
  native plans would ([below](#compiled-native-plans-ivgc)), and without `W` there reads run interpreted.
- `%Native_GlobalAccess:U` lets the engine read the namespace's IVG Schema record (`^IVG.Deploy`) and the graph
  algorithms read `^KG` over the Native API. Without it every engine warns that it could not read the record and skips
  the check that the namespace's schema is the engine's, and betweenness, closeness, eigenvector, Leiden, k-core,
  strongly connected components and triangle counting raise `<PROTECT> *Resource %Native_GlobalAccess required`.
  With the database at `R` it writes nothing.
- With the database at `R`, `khop`, Cypher, PageRank, personalized PageRank (`ppr`, `kg_PERSONALIZED_PAGERANK`),
  `degree_centrality()` and the algorithms above answer as with `RW`, before and after any write to the graph. Reads
  that write a cache answer without caching: the algorithm cache `^IVG.AlgoCache` and Arno's `^ArnoKG` (said once, at
  INFO), and the degree cache `^KG("degc")`, whose entry for a node is used while it is current and otherwise counted
  again and not stored. Arno does not load. One read writes a work area and raises instead of answering:
  `kg_GRAPH_WALK` (`^ArnoKG("bfs_r")`), with a `RuntimeError` that names the work area and says the database resource
  is needed at `RW`. That is what the "reading, fully" row is for. Before 4.6.0 (R050) personalized PageRank and
  `degree_centrality()` raised too, as soon as they reached a node whose degree-cache entry was missing (a node with
  no out-edge) or older than the last write to it: after a `sync()` and a first run by a writer they answered, and
  they failed again after the next write. On Community 2026.1 `degree_centrality()` then answered from a slower Python
  walk, on 2026.3.0AI it raised. Measured on 2026.3.0AI by `tests/e2e/test_460_r050_reader_ppr_degree_e2e.py`.
  Personalized PageRank in SQL and Cypher writes nothing and answers at `R` with the scores it gives at `RW`: the SQL
  function (`SELECT Graph_KG.kg_PPR('["a"]', 0.85, 20, 0, 1.0, '')`, which reads `^KG` and sorts in a process-private
  global) and `CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score`, which runs it. `betweenness_centrality` answers the
  same list with either: the nodes on some shortest path, highest first. At `R` its ObjectScript tier cannot write its
  work area, so the slower Python walk answers, and a WARNING carrying IRIS's `<PROTECT>` says so on every call: a log
  alert on WARNING sees one for each call a reading role makes. A namespace where a named graph holds edges skips
  that tier at every role and says nothing: the Python walk answers there anyway, from the default graph.
- The class dictionary is not readable in SQL by these roles; IVG asks it over the Native API instead. That includes
  which ANN (HNSW) indexes the vector tables have: `status()` asks `Graph.KG.Meta.HnswIndexes`, a class method these
  roles may run, and reports the index state as it does for `_SYSTEM`.
- On builds that have the resource `%Service_EmbeddedPython` (2026.3.0AI build 154 and later), add
  `%Service_EmbeddedPython:U` for the Python-language classes (`Graph.KG.PyOps`, Leiden, closeness).
- Every `initialize_schema()` grants EXECUTE on the Cypher read path to each user or role holding SELECT on
  `Graph_KG.rdf_props` (on the table or the schema) or EXECUTE on one read-path function. A user that holds it only
  through one of its roles gets nothing of its own: the role gets the grants, and they serve its members. Before 4.6.0
  (R050) each member got them again as direct grants, which stayed when the role was taken away, since IRIS lists a
  role's privilege once more under each member. The read path is the functions that read nothing outside the caller's
  SQL privileges: `CY_*`, `CypherFn_*`, `JSON_*`, `LIST_*`, `STR_SPLIT`, `REGEX_MATCH`, `RAND`, `NEWID`,
  `Percentile_*`. After the next setup, a role granted SELECT on the graph tables and no EXECUTE can run the Cypher
  reads that translate to SQL: matches, optional matches, variable-length paths, filters and aggregates. It cannot run
  the `CALL ivg.*` procedures (`ivg.closeness`, `ivg.betweenness`, `ivg.degreeCentrality`, `ivg.ppr` and the others):
  they call the `kg_*` algorithm functions, which are not on the read path, so the role needs EXECUTE on those, or
  `GRANT EXECUTE ON SCHEMA Graph_KG` as the reading row has. A read refused for a missing privilege does not raise: it
  answers no rows, with `result.error` holding IRIS's SQLCODE -99 message (`User … is not privileged for the
operation`), and logs a WARNING (logger `iris_vector_graph.stores.iris_sql_store`, "execute_sql refused for a missing
  privilege (SQLCODE -99); the result's error holds it: …", `<PROTECT>` in place of the SQLCODE for a refused global
  read), so an alert on WARNING sees it; other failed reads are logged at DEBUG only. Check `result.error`, or pass
  `execute_cypher(query, raise_on_error=True)` to get a `RuntimeError`. Before that setup the same role gets real rows from the reads the native Cypher executor answers,
  which read `^KG` under the database resource (the boundary, next), and no rows, with `result.error` set, from those
  on the SQL path; which reads the native executor answers depends on the query (on 2026.3.0AI an unlabelled one-hop
  match). Measured on 2026.3.0AI by `tests/e2e/test_460_r050_select_only_role_e2e.py`, and the same on Community
  2026.1 by the expert walkthrough. The status's `read_path_grants` lists each grant and why it was made ([migration
  notes](migration/v4.6.0.md#your-own-sql-that-calls-ivgs-functions)).
- **What is the boundary.** IVG keeps the graph twice: in the tables, and in globals (`^KG`, `^NKG`, the BM25 and
  IVF indexes `^BM25Idx` and `^IVF`, the ledger `^IVG.Ledger`). SQL privileges gate the tables. IRIS checks a read of
  a global against the namespace database's resource only. So for IVG's global readers the database resource is the
  boundary, not SQL privileges: the native Cypher executor, Arno, and the algorithms and searches the engine runs over
  the Native API (with `%Native_ClassExecution` and `%Native_GlobalAccess`). A user who may use those reads the whole
  graph, whatever its table grants. IVG's SQL-callable functions that read a global also check SQL privileges: before
  reading, each checks that the caller holds SELECT on the table the global stands for, and otherwise fails with
  SQLCODE -99 naming it (`User APPUSER lacks SELECT on Graph_KG.rdf_edges`). These are `CY_VLP_ENDS` (when it walks
  `^KG`), the `kg_*` algorithm functions, `MatchEdges`, `MatchEdgesAllGraphs` and `DijkstraPath`, all against
  `rdf_edges`. `kg_BM25` checks `rdf_props`, `kg_IVF` the vector table its index was built from, and the procedure
  `ledger_records` checks `ledger_revisions`. The Arno wrappers that reach the graph check `rdf_edges`. SELECT on the
  table or its schema counts; SELECT on some of its columns does not.
- **The SQL functions that write check the write privilege.** `backfill_temporal_rows()` writes node and relationship
  rows for temporal edges that have none, through embedded SQL, which IRIS does not check for the caller. It first
  checks INSERT on `nodes` and `rdf_edges` and otherwise fails with SQLCODE -99
  (`User APPUSER lacks INSERT on Graph_KG.nodes`). `rebuild_edge_indices(schema)` runs `BUILD INDEX` on `nodes`,
  `rdf_labels`, `rdf_props` and `rdf_edges`, which IRIS refuses without `%BUILD_INDEX` (INSERT, UPDATE and DELETE on
  the tables are not enough). The refusal is raised, naming the table; before 4.6.0 it answered 0.
- **The Arno SQL functions run only Arno.** Each `ivg_arno_*` function takes the library path as an argument, and
  `ivg_arno_zf_call_2s` and `ivg_arno_zf_probe` a function name too. Each opens only the library
  `##class(Graph.KG.ArnoAccel).LibPath()` resolves on the server, and looks up only the library functions IVG calls
  (the class parameter `SQLCALLOUTS`). Anything else fails with SQLCODE -99 naming it
  (`Arno library /tmp/x.so refused: ...`, `Arno function init refused: ...`), before anything is loaded. Before 4.6.0
  a caller holding EXECUTE on them and `%System_CallOut` could load any library on the server and run any function in
  it. The 4.5.x copies do not check. 4.5.x made them in the instance's default SQL schema, which is `SQLUser` unless
  the instance is configured otherwise. Every `initialize_schema()` closes the hole those copies leave. It replaces
  any copy in the IVG Schema with the checked text, keeping its grants (`arno_wrappers_replaced` in its status). In
  every other schema it revokes EXECUTE on them from each grantee except a `%All` holder (`legacy_arno_revoked`). The
  functions stay. A grant held through `GRANT EXECUTE ON SCHEMA` is not revoked and is reported as a warning: revoke
  it yourself, or remove the copies (section 6, "The objects 4.5.x left in `SQLUser` and `User`"). See
  [migration/v4.6.0.md](migration/v4.6.0.md#the-45x-arno-functions).
- **SQL privileges do not gate IVG's class methods.** A role with the database at `RW` and
  `%Native_ClassExecution` can rewrite or erase `^KG`, `^NKG` and the index globals through them (`sync()`,
  `erase_graph`, the index builders) whatever its table grants. A read-only role is one whose database resource is `R`;
  `execute_cypher(read_only=True)` and the HTTP server's `IVG_READ_ONLY` are application-level guards on top.
- Compiled native plans write classes (`IVGC.*`) into the routine database and `^IVG.CypherCompiled`. A process that
  may not write them runs its reads interpreted, with the same answers. Whether it says so depends on how far it gets.
  The reading roles above never try: they cannot read the class dictionary in SQL, so the direct property reader
  (`Graph.KG.Cypher.Props.LayoutOK()` answers 0) is off for them and the planned walk is interpreted at once, with no
  `messages.log` line and nothing at DEBUG. A process that can read the dictionary in SQL (granted SELECT on the
  `%Dictionary` tables the reader checks) tries, and says so at severity 1: once per process, when the database is
  `R` (`IVG native plans not compiled in this process, interpreted instead: <PROTECT> ... ^IVG.CypherCompiled(...)`);
  or from the background job that builds the class, when the database is `RW` and the routines database `R`
  (`IVG native plan IVGC.P<n> did not compile, interpreted instead: ...`). Measured on 2026.3.0AI with the roles of
  the split namespace, with and without that grant.
- **What a database writer cannot make another process do.** The writing and "reading, fully" rows hold the database
  resource at `RW` and `%Native_GlobalAccess`, so they may set any global in the namespace's database over the Native
  API, IVG's own included. Two of those globals told other processes what to run, and until 4.6.0 (R311) a writer
  could use them to run code in the process of whoever came next, with that user's privileges. `^ArnoAccel("lib_path")`
  was the library a load with no path loaded, and the one the `ivg_arno_*` functions allowed: a writer could point it
  at any file on the server. It is now taken only when it names a file directly in the instance's manager or binary
  directory (`$System.Util.ManagerDirectory()`, `BinaryDirectory()`), or the default path; otherwise the default path
  is used. A path the application names (`IVG_ARNO_LIB`) is loaded as named, because its caller holds
  `%System_CallOut` and could load it anyway. `^IVG.CypherCompiled` holds each compiled plan's lines until a build
  compiles them: a writer could replace them with its own, and the build compiled them into an `IVGC.*` class that ran
  in the next process to run that query shape. The build now compiles only the lines the process that wrote them hands
  it by digest, and runs only the class named for its entry. Of the other globals IVG reads, those checked for R311
  (`^KG`, `^NKG`, the indexes, the caches, `^IVG.Deploy`, `^IVG.CypherGen`) hold data or settings: a writer can make
  answers wrong or slow through them, as through the tables. What malformed contents of `^ArnoKG`, the graph Arno's
  native code reads, would do there was not examined. A role that must not be able to do any of this needs the database
  at `R`.
- **Where routines and globals share a database, a writer can change code.** That is IRIS's default layout, the compose
  image's `USER`, and a namespace made with one database. There the database resource at `RW` covers the routine and
  class globals too, which live in that database (`^ROUTINE`, `^rOBJ`, `^oddDEF`): measured on 2026.3.0AI, a user
  holding only the writing row set `^ROUTINE` over the Native API (`tests/e2e/test_460_r311_writer_role_e2e.py`). Such a
  user can rewrite code that other users run, so in that layout the writing row is as powerful as code execution,
  whatever the two fixes above do. To keep writers from code, put the namespace's routines in a database of their own,
  as the `<routines database resource>` note above describes, and grant writers `R` on it. The fixes above are what that
  layout relies on: without them a writer there could still reach code through `^ArnoAccel` and `^IVG.CypherCompiled`.
- **Power the MCP tools have by design.** `Graph.KG.MCPTools` (section 4, "The MCP classes") is for a trusted MCP
  client. `RunSQL` prepares and runs any SQL statement it is given, as the MCP service's IRIS user, with that user's
  privileges: writes and DDL included. `LoadGraph` reads any file path on the server that the IRIS process can read and
  loads it into the graph. Give the MCP service a user with no more than the rows above, and do not expose the tools to
  a client that should not have that user's SQL access or read the server's files.
- **The cost of `=~`.** A regular expression in a Cypher query (`WHERE n.name =~ $p`) runs on the server, for each row
  it is compared with, in IRIS's regular-expression engine (`$MATCH`, through `REGEX_MATCH`). A pattern written to
  backtrack holds the process for as long as the backtracking takes, and IVG sets no limit on it: on 2026.3.0AI,
  `(a+)+$` against 26 `a`s and a `b` took 1.8 s, and each two more characters took about four times as long. Where
  queries come from users who are not trusted, refuse `=~` in the application or bound the request's time.

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
  `^ArnoAccel("lib_path")`, when that file is directly in the instance's manager or binary directory (section 3, "What
  a database writer cannot make another process do"); else `/usr/irissys/mgr/libarno_callout.so`. Put the file at that
  default path. Nothing is
  loaded from `/tmp` unless a path names it. `##class(Graph.KG.ArnoAccel).LibPath()` answers the path a load with no
  argument would use. It is also the one library the `ivg_arno_*` SQL functions open (section 3): when `IVG_ARNO_LIB`
  names another, the engine loads it first, which records it.
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

`##class(Graph.KG.NKGAccel).LoadError()` gives the reason for the last failed `Load` in that process
(`library file not found: <path>` when the file is not there). IVG does not ask IRIS to load a file that is not
there, so an absent library writes nothing to `messages.log`; before 4.6.0 each process that tried wrote two
severity-1 lines (`Failed to load dynamic library`, `Error loading dll`). A file that is there and fails to load still
writes them. `##class(Graph.KG.ArnoAccel).LibExists()` answers whether the file a load would use is on the server.
`engine.status().report()` shows what answers your engine's calls: `Acceleration: ObjectScript (Arno not loaded)` or
`Acceleration: Arno <version>`, and under `Arno library:` the path with `(loaded)`, `(not loaded)` or
`(not loaded: no such file on the server)`. `IVG_DISABLE_ARNO=1` in the application's environment keeps the engine
from loading it; the status then reads `Acceleration: ObjectScript (Arno disabled: IVG_DISABLE_ARNO=1)` and names no
library.

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
- In the IVG Schema: EXECUTE on `kg_KNN_VEC`, `kg_RRF_FUSE`, `kg_TXT` and `kg_VEC_ROUTE_GUARD`, and SELECT on
  `kg_NodeEmbeddings`, `rdf_labels`, `docs` and `embedding_registry`. If one of these routines is missing from the
  namespace, the call still makes the application and grants the rest. It then returns an error status that names the
  routine. The routes that call that routine fail until `initialize_schema()` creates it and you run `CreateWebApp`
  again.
- SELECT on each named graph's vector route (`kg_emb_<hash>`) that `embedding_registry` lists when `CreateWebApp`
  runs. `/vectorSearch` and `/hybridSearch` read a named graph's vectors there. A route made later, by a graph's first
  vector, is not granted: a search of that graph fails with a privilege error that names the table. Run
  `CreateWebApp` again to grant it. No route reads `kg_NodeEmbeddings_optimized` or `kg_EdgeEmbeddings`, so the role
  is granted neither (measured on 2026.3.0AI and on Community 2026.1).

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

Only a read the native executor runs is compiled ([User Guide](USER_GUIDE.md#where-a-query-runs)), and only one with
a hop: a native read with none is interpreted. A read on the SQL path compiles nothing and leaves no entry, in any
mode; an `OPTIONAL MATCH` is one. Since 4.6.0 an anchor `{node_id: …}` on a node pattern without a label, and a
`RETURN` of `node_id`, run natively and compile; before, both took the SQL path. On a graph a → b → c → d (`KNOWS`,
each node labelled `P` and named `A` to `D`), measured on Community 2026.1 and 2026.3
(`tests/e2e/test_460_r204_compiled_plans_job_e2e.py`, and `tests/e2e/test_460_r246_native_node_id_e2e.py` for more
reads of each kind):

| Read                                                                     | Compiled                         |
| ------------------------------------------------------------------------ | -------------------------------- |
| `MATCH (a:P {name: 'A'})-[:KNOWS]->(b)-[:KNOWS]->(c) RETURN c.name`      | yes                              |
| `MATCH (a:P {node_id: 'a'})-[:KNOWS]->(b) RETURN b.name`                 | yes                              |
| `MATCH (a:P)-[:KNOWS]->(b)-[:KNOWS]->(c) RETURN count(*)`                | yes                              |
| `MATCH (a {node_id: 'a'})-[:KNOWS]->(b) RETURN b.name`                   | yes                              |
| `MATCH (a:P {name: 'A'})-[:KNOWS]->(b) RETURN b.node_id`                 | yes                              |
| `MATCH (a:P {name: 'A'}) OPTIONAL MATCH (a)-[:KNOWS]->(b) RETURN b.name` | no: `OPTIONAL MATCH` is SQL-only |

In `job` mode each compiled read's class was built before its second run (under 2 s there), and both runs gave the
same answer.

A namespace under change control sets `Set ^IVG.CypherGen("mode") = "off"`. The mode in effect:

```objectscript
USER>write ##class(Graph.KG.Cypher.Gen).Effective()
job
```

A shape whose class does not compile stays interpreted, and the failure is written to `messages.log` once;
`##class(Graph.KG.Cypher.Gen).Entries()` returns every shape with its status as a JSON array (`hash`, `class`,
`status`, ...); in a terminal `write` it, since `do` discards the answer. A deploy that compiles IVG's classes, or
`##class(Graph.KG.Cypher.Gen).DropAll()`, deletes all of them; nothing else prunes them, so there is one class per
distinct read shape. `IVG_NATIVE_CYPHER=0` sends every Cypher read to the SQL path instead, compiling nothing.

### The MCP classes

`Graph.KG.MCPService`, `MCPToolSet` and `MCPTools` expose IVG as MCP tools on IRIS builds with `%AI` (AI Hub). They are
deployed where the server has `%AI` and embedded Python, and inert until an MCP service is configured to use them.
`MCPTools` runs IVG's Python package inside IRIS: see the next section.

### IVG's Python package inside IRIS

`IVG.CypherEngine.Local()`, `Graph.KG.MCPTools` and application code that runs in IRIS with `EmbeddedConnection`
([USER_GUIDE](USER_GUIDE.md)) import the `iris_vector_graph` package in IRIS's embedded Python. Setup compiles the
classes but does not put the package there, so until you do, their first call fails with
`ModuleNotFoundError: No module named 'iris_vector_graph'`. Install it, then its dependencies, into the directory
IRIS's Python reads (`<install-dir>/mgr/python`), as the user IRIS runs as:

<!-- doc-test: skip reason="installs into the IRIS server's embedded Python" -->

```bash
irispython -m pip install --target /usr/irissys/mgr/python --no-deps iris-vector-graph==<the application's version>
irispython -m pip install --target /usr/irissys/mgr/python "numpy>=1.24.0" "pydantic>=2.0.0" "requests>=2.28.0"
```

Install the same version the applications use, and again after each upgrade. `--no-deps` matters: IVG's other two
dependencies, `intersystems-iris` and `iris-embedded-python-wrapper`, each install a top-level `iris` module, and one
in `mgr/python` hides IRIS's own. A check, from an IRIS terminal in a namespace that has been set up:

<!-- doc-test: skip reason="runs in an IRIS terminal on the server" -->

```objectscript
USER>do ##class(IVG.CypherEngine).SmokeTest()
```

prints `SmokeTest PASSED` and the namespace's node count. Nothing else in IVG needs the package on the server
(`igraph` and `leidenalg` there are optional: [COMMUNITIES](COMMUNITIES.md)).

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
created there, and leaves them in place so SQL of yours that calls them keeps answering. Setup copied each grant on a
function to its replacement in the IVG Schema. A grant on one of the four `SQLUser` views (`nodes`, `rdf_edges`,
`rdf_labels`, `rdf_props`) is not copied, because a view has no replacement: the status's `grants_copied` lists it
with `copied` false, so grant on the IVG Schema's tables yourself. List them, then remove them once your own SQL names the IVG Schema:

```python
engine.remove_legacy_objects()
# → []
```

The list is empty here because this namespace never ran 4.5.x. Each item says what it is (`kind`, `sql_name`,
`class_name`) and the grants it holds; `remove_legacy_objects(dry_run=False)` removes exactly those. It works from a
fixed list of 4.5.x names, so a function of yours is never touched unless it has one of those names (a `User.SQL`
class of your own, say). Since 4.6.3 `remove_legacy_objects` keeps a same-named function, view or class whose body
IVG never shipped, and lists it with `ivg_owned` false and a `note` saying it is not IVG's. Read the list before
removing. `engine.status().legacy_objects` counts the ones that are IVG's.

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
| `unknown`                 | what the report could not tell (below)                               |

`print(engine.status().report())` prints the same as a short report. Where a probe failed, or did not run (on a
namespace that refuses the engine nothing is probed), the report prints `unknown (see Probe errors)` instead of a value
for the graph's size, the acceleration, the sync state, the vector and full-text indexes, the IVF and PLAID indexes and
the ledger, and lists each failure, with what it asked, under `Probe errors`; `status().unknown` names them
(`"graph"`, `"acceleration"`, `"sync"`, `"vector_index"`, `"fulltext_index"`, `"ivf_index"`, `"plaid_index"`,
`"ledger"`). Before 4.6.0 (R050) it printed `0 nodes` and `in sync` there, and `absent` and `not enabled` for an index
or ledger probe that failed. A routine setup could not create (the server
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
when the ledger is on. IVG's own snapshots (`save_snapshot`, `restore_snapshot`) copy the graph to a file and back
([User Guide](USER_GUIDE.md)), and are not a backup of what the "Must keep" list names. A snapshot holds the tables
`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`, `rdf_reifications`, `docs`, `embedding_registry`,
`fhir_unresolved`, `fhir_definitions`, `fhir_canonical_refs`, `kg_NodeEmbeddings`, `kg_EdgeEmbeddings` and each
routed embedding table the registry names, and the globals `^KG`, `^NKG`, `^BM25Idx`, `^IVF`, `^PLAID`, `^VecIdx`,
`^IVG.Ledger` with the ledger classes' storage, `^IVG.GraphMode` and `^IVG.TemporalStructural`; a global that is
empty is not written. It leaves out `^IVG.Deploy`, `^IVG.CypherGen`, `^EmbedQueue`, and the tables
`ledger_revisions` and `ledger_stats` as SQL (their storage globals are in it), `rdf_namespaces`,
`relationship_mappings`, `table_mappings`, `fhir_graphs` (the registered FHIR repositories), `fhir_bridges`,
`code_crosswalk` and `embedding_quarantine`, and `kg_NodeEmbeddings_optimized`, which the vector-index migration
rebuilds from `kg_NodeEmbeddings`. A table or global it cannot read (a login without SELECT on it, say) raises `SnapshotReadError`, a `RuntimeError` naming it and
IRIS's error, and no archive is written; an `fhir_*` table not created yet (no FHIR graph set up) is left out. Before
4.6.0 such a table was listed with 0 rows, so a restore from the archive emptied it. A snapshot made through the HTTP
server goes to
`IVG_SNAPSHOT_DIR`, the temporary directory unless you set it
([known issue](KNOWN_ISSUES.md#snapshots-made-through-the-rest-api-land-in-the-temporary-directory)).

**Stop IRIS before its container.** IRIS data survives a container restart cleanly only when IRIS was stopped first;
otherwise it recovers on the next start, which takes from 30 s to several minutes. With the compose file:

<!-- doc-test: skip reason="stops the IRIS container; the doc tests run in a shared test container" -->

```bash
docker compose exec iris iris stop IRIS quietly
docker compose stop
```

`docker compose ps` keeps showing `(healthy)` for up to about 30 seconds after `iris stop` (the health check fails
three times before the flag changes; before 4.6.0 it took about 100 seconds), then `(unhealthy)` until IRIS runs
again. In production, make the orchestrator run `iris stop` before it kills the container.

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

**The first `^NKG` read after an erase rebuilds it.** `erase_graph`, `delete_node`, `bulk_delete_nodes` and the deletion
of a named graph's last edge leave that graph's nodes in `^NKG`'s dictionary, so they mark `^NKG` invalidated or stale
(4.6.0 R268, R274). The next reader of `^NKG` (`Graph.KG.TraversalBuild.EnsureNKG`, called by the `^NKG` community and
centrality procedures) then runs one `BuildNKG` under the lock `^IVG.NKGBuild` before it answers, and a reader that
waits longer than the lock's timeout for another's rebuild raises. The rebuild walks the whole namespace, so its cost
is `BuildNKG`'s, whatever the erased graph's size: 167 s at 1.9M edges in the lab. To keep it off a user's query, run
`engine.sync()` after the erase (or after a batch of erases).

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

A graph grown by row-at-a-time writes (`create_edge`, Cypher `CREATE`, ledger commits) is never tuned again after
setup, which tuned it empty: at 20,000 edges each edge insert's existence check then plans as an index intersection,
about 6 ms an edge against 0.4 ms. Run `TUNE TABLE <schema>.rdf_edges` and `TUNE TABLE <schema>.nodes` once such a
graph has grown, and again after it grows by an order of magnitude ([KNOWN_ISSUES](KNOWN_ISSUES.md)).

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

**No `IVGC.*` class appears, and nothing is logged.** The reads ran on the SQL path, which compiles nothing (an
`OPTIONAL MATCH`, a `WHERE` on `node_id`, a write), or had no hop, which is interpreted. Before 4.6.0 an unlabelled
`{node_id: …}` anchor and a `RETURN` of `node_id` were SQL-path reads too. `Graph.KG.Cypher.Gen.Entries()` lists only
shapes the native executor was asked to compile ([compiled plans](#compiled-native-plans-ivgc)).

**A graph-scoped read is slow.** Look for `Warning: Table … is not tuned.` in `EXPLAIN`, and tune
([Table statistics](#table-statistics)).

**A read that groups over an `UNWIND` of a collected list is slow on IRIS 2026.3, and the log says "reading it by
position instead".** IRIS 2026.3 refused the read's plain SQL for a list over 511 characters, and IVG ran it again
reading the list once per element, which costs time quadratic in the list's length; the answer is right
([known issue](KNOWN_ISSUES.md#on-iris-20263-grouping-over-an-unwind-of-a-long-list-reads-the-list-once-per-element)).

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
