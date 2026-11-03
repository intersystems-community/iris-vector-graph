<!-- markdownlint-disable MD013 -->

# Feature Specification: One IVG Schema per namespace

**Feature Branch**: `262-ivg-owned-sql-schema`
**Created**: 2026-11-03
**Status**: Draft
**Ships in**: IVG 4.6.0, with the `read_only` security fix and the documentation restructure
**Decisions**: ADR-0016 (`docs/adr/0016-one-ivg-schema-per-namespace.md`); `/grill-with-docs` session with Tom, 2026-11-03
**Input**: IVG's SQL footprint leaves the default schema. In 4.5.1, `initialize_schema()` creates about 47 SQL functions in
`SQLUser` with generic names (`RAND`, `NEWID`, `JSON_VALUE`, `STR_SPLIT`, `CY_*`, `CypherFn_*`, `ivg_arno_zf_*`), their
53 `User.func*` classes and four `SQLUser` views (`nodes`, `rdf_edges`, `rdf_labels`, `rdf_props`); the tables follow
`schema_prefix` while the ObjectScript classes name `Graph_KG` 283 times in 32 classes; and two benchmark classes ship
to every namespace. An IRIS operator cannot tell IVG's objects from the application's own.

## Background

Found by a newcomer walkthrough of 4.5.1 (an IRIS expert following the docs, `~/.cache/ivg-bench/novice/REPORT.md`):
the docs never say where IVG's objects go, `SQLUser` gains generic names that can collide with an application's
functions, and `SQLUser.nodes` (`SELECT node_id, created_at`) leaves out `graph_id`, so a reader of it sees every
graph's nodes with no way to tell them apart.

A non-default `schema_prefix` is used in practice: hipporag2-pipeline builds its engine on `RAG`, writes edges and runs
PPR. In 4.5.1 that splits one install across three schemas: tables in `RAG`, functions in `SQLUser`, and every
ObjectScript feature (erase, temporal index, ledger, the ObjectScript algorithms, the adjacency index) reading
`Graph_KG`.

iris-vector-rag (specs 074 and 079) lets a schema prefix run several instances in one namespace. IVG cannot copy that:
its globals (`^KG`, `^NKG`, `^IVG.*`) belong to the namespace, so two IVG schemas in one namespace would share one
adjacency index and one ledger. The namespace is IVG's isolation unit; the schema name exists so IVG fits a namespace
whose names are taken or prescribed.

## Clarifications

### Session 2026-11-03

- Q: What happens to EXECUTE grants operators made on the 4.5.x `SQLUser` objects? → A: The upgrade copies every grant on each legacy object to its replacement in the IVG Schema; the dry run lists the grants it would copy.
- Q: Is renaming a namespace's IVG Schema in scope for 4.6.0? → A: No. The refusal error and the docs give the manual route: choose one schema with `schema_prefix=`, then move or drop the other schema's tables with SQL the docs supply.
- Q: How much may following the recorded schema cost in speed? → A: No write, erase or read path more than 5% slower than 4.5.2, on the default schema and on a non-default one alike; a benchmark gate checks both.
- Q: What does a 4.6.0 engine do on a namespace 4.5.x set up and nobody has re-initialized? → A: On first access it checks for the schema record and the 4.6.0 objects; if either is missing it raises one error telling the user to run `initialize_schema()` (dry run first), and runs nothing else.

## User Scenarios & Testing _(mandatory)_

### User Story 1 - An operator finds all of IVG in one schema (Priority: P1)

An IRIS operator sets IVG up in a new namespace and inspects it in the Management Portal and an `iris session`. Every
table, function, procedure and view IVG created is in one schema, `Graph_KG`, and every class IVG compiled is in IVG's
own packages. `SQLUser` and the `User` package hold nothing of IVG's.

**Why this priority**: it is the complaint that started this spec, and every other story builds on the one schema.

**Independent Test**: initialize a new namespace, then list every schema, routine, view and class it holds; compare
against the published inventory.

**Acceptance Scenarios**:

1. **Given** a new namespace, **When** `initialize_schema()` runs, **Then** `SQLUser` holds no function, procedure or
   view IVG created, and the `User` package holds no class IVG created.
2. **Given** the same namespace, **When** the operator compares its contents with the ADMIN_GUIDE inventory, **Then**
   every object IVG created is listed there with its purpose, and nothing listed is missing.
3. **Given** a namespace where the application has its own `SQLUser.RAND` and `User.funcRAND`, **When**
   `initialize_schema()` runs, **Then** both are unchanged and IVG's Cypher still answers correctly.
4. **Given** IVG set up in the namespace, **When** a Cypher query that uses every helper function the translator can
   emit is run, **Then** it returns its known answer, and the SQL it ran names no object outside the IVG Schema.

---

### User Story 2 - An existing namespace upgrades without breaking anyone's SQL (Priority: P1)

An operator upgrades a namespace that 4.5.x set up. IVG stops using its old `SQLUser` objects, but SQL the
application wrote against them (for example `SELECT SQLUser.STR_SPLIT(...)`) keeps working. When the operator is ready,
one call lists exactly what IVG left behind, and a second removes it.

**Why this priority**: a minor release must not break user SQL, and an operator must be able to reach the clean state.

**Independent Test**: build a 4.5.1 namespace from frozen DDL, upgrade it, run the application's legacy SQL, then list
and remove the leftovers.

**Acceptance Scenarios**:

1. **Given** a 4.5.1 namespace, **When** `initialize_schema()` runs under 4.6.0, **Then** IVG's own queries use only the
   IVG Schema, the old `SQLUser` objects are still present and answer as they did, and IVG logs once that they are
   there and how to remove them.
2. **Given** that namespace, **When** `remove_legacy_objects(dry_run=True)` runs, **Then** it returns every object 4.5.x
   created in `SQLUser` and `User` (functions, procedures, views and their classes) plus the retired benchmark
   classes, and changes nothing.
3. **Given** the same list, **When** `remove_legacy_objects(dry_run=False)` runs, **Then** exactly those objects are
   gone and nothing else in the namespace changed.
4. **Given** an application object whose name matches a 4.5.x name pattern but is not on the 4.5.x manifest (for
   example `SQLUser.CY_MINE`), **When** either call runs, **Then** it is neither listed nor removed.
5. **Given** a namespace that was upgraded from 3.2.0 to 4.x earlier, **When** it upgrades to 4.6.0, **Then** the
   result is the same as for a 4.5.1 namespace.

---

### User Story 3 - A user chooses the IVG Schema's name (Priority: P1)

A developer installs IVG into a namespace where `Graph_KG` is taken, or where policy prescribes schema names. They
name the schema once, through `IRISGraphEngine(schema_prefix=...)` or `IVG_SCHEMA_PREFIX`, and every IVG feature works
there: Cypher, writes, erase, temporal edges, the ledger, the algorithms, PPR, text search, embeddings, FHIR graphs.

**Why this priority**: Tom's requirement, and the only way hipporag2-pipeline's `RAG` engine becomes fully wired.

**Independent Test**: initialize a namespace with `schema_prefix="IVGALT"`, run a test per existing write, delete and
read path, and confirm that no `Graph_KG` schema exists in the namespace afterwards.

**Acceptance Scenarios**:

1. **Given** a new namespace and `schema_prefix="IVGALT"`, **When** `initialize_schema()` runs, **Then** every IVG table,
   function, procedure and view is in `IVGALT`, and no `Graph_KG` schema exists.
2. **Given** that namespace, **When** each write, delete and read path runs (one case per path), **Then** each gives its
   known answer and touches no `Graph_KG` object.
3. **Given** `IVG_SCHEMA_PREFIX=ENVNAME` and `schema_prefix="ARGNAME"`, **When** an engine is built, **Then** `ARGNAME`
   is used.
4. **Given** `IVG_SCHEMA_PREFIX` set and `IRIS_SCHEMA_PREFIX` set to something else in the same process, **When** IVG
   and iris-vector-rag are both used, **Then** IVG uses only `IVG_SCHEMA_PREFIX`.
5. **Given** a name that is empty, starts with a digit, holds characters other than letters, digits and underscore, or
   is reserved (`SQLUser`, anything starting `%`), **When** an engine is built, **Then** it raises a `ValueError` naming
   the rule, before touching the database.

---

### User Story 4 - A namespace's schema is fixed once recorded (Priority: P2)

The first `initialize_schema()` records the IVG Schema in the namespace. An engine that later asks for a different one
is refused with an error naming both, the first time it touches the namespace, instead of sharing the namespace's
globals with the recorded schema. An existing namespace with no record gets one automatically when the answer is
unambiguous.

**Why this priority**: without it, a second schema in one namespace corrupts the adjacency index and the ledger
silently.

**Independent Test**: record `Graph_KG`, then use an engine built with `RAG`; separately, upgrade namespaces holding
IVG tables in one schema, and in two.

**Acceptance Scenarios**:

1. **Given** a namespace recorded as `Graph_KG`, **When** an engine built with `schema_prefix="RAG"` first reads or
   writes, **Then** it raises an error naming `Graph_KG` and `RAG` and suggesting a separate namespace, and no global
   or row changed.
2. **Given** a 4.5.1 namespace whose IVG tables are all in `RAG`, **When** 4.6.0's `initialize_schema()` runs with
   `schema_prefix="RAG"`, **Then** `RAG` is recorded, and the ObjectScript features read `RAG` from then on.
3. **Given** a namespace with IVG tables in both `Graph_KG` and `RAG`, **When** `initialize_schema()` runs with no
   explicit name, **Then** it stops with an error listing both schemas and their row counts, and records nothing.
4. **Given** the same namespace, **When** `initialize_schema(schema_prefix="RAG")` runs, **Then** `RAG` is recorded and
   the `Graph_KG` tables are reported, untouched.
5. **Given** any of these namespaces, **When** the dry run runs, **Then** it reports what would be recorded and writes
   nothing (proven by a before/after snapshot of the whole namespace).

---

### User Story 5 - Production namespaces carry no benchmark code (Priority: P2)

`Graph.KG.Benchmark` and `Graph.KG.BenchFormat` are not in the wheel and are not deployed. `Graph.KG.Service` (a REST
dispatcher) and `Graph.KG.FHIRGraphSyncTask` (a Task Manager task) still ship, and the inventory says each is inert
until a web application or a task schedule names it.

**Why this priority**: an operator reviewing a namespace should not find benchmark code; low risk once US2's
manifest exists.

**Independent Test**: build the wheel, install it, initialize a namespace, and list the deployed classes.

**Acceptance Scenarios**:

1. **Given** the 4.6.0 wheel, **When** its contents are listed, **Then** neither benchmark class is in it.
2. **Given** a new namespace, **When** `initialize_schema()` runs, **Then** neither benchmark class exists there, and
   `Graph.KG.Service` and `Graph.KG.FHIRGraphSyncTask` do.
3. **Given** the test suite, **When** a test needs a benchmark class, **Then** it deploys it itself from the development
   sources, and the suite passes from the installed wheel.

---

### User Story 6 - A project that vendors IVG keeps working (Priority: P3)

opsreview copies IVG's whole `iris_src/src` into its repository each release, with local fixes, and keeps its own
`User.*` classes that define `SQLUser` functions. Re-vendoring 4.6.0 works on a namespace that still holds those, and
the 4.6.0 migration notes say what moved.

**Why this priority**: one known consumer; IVG cannot edit it, so the notes and a test are what it gets.

**Independent Test**: a namespace holding 4.5.1's objects plus a hand-made `User.funcSTRSPLIT`, upgraded to 4.6.0.

**Acceptance Scenarios**:

1. **Given** that namespace, **When** 4.6.0 is deployed and initialized, **Then** IVG answers correctly and the
   hand-made class is unchanged.
2. **Given** the 4.6.0 release, **When** a consumer reads the migration notes, **Then** they list every moved, renamed
   and retired object, `remove_legacy_objects`, and that a namespace that stays on `Graph_KG` needs no SQL changes.
3. **Given** 4.6.0 is published, **When** the release is complete, **Then** opsreview has been sent an agent-bus message
   pointing to the notes (no edits in opsreview).

### Edge Cases

- **Package names.** IRIS derives a class package from the schema name (`Graph_KG` becomes `Graph.KG`). A chosen name
  whose package already holds application classes, or maps onto another schema's package, is refused at
  `initialize_schema()` with the clash named.
- **Case.** SQL schema names are not case-sensitive in IRIS and package names are; `ivgalt` and `IVGALT` are the same
  schema and must be recorded and compared as one.
- **A function named like a table.** No IVG function may share a name with an IVG table or view in the IVG Schema; a
  unit test checks the generated names.
- **Compiled native plans and cached queries.** Plans compiled against the 4.5.x names are dropped and recompiled; a
  cached query that names `SQLUser.*` is never reused by IVG after the upgrade.
- **An interrupted upgrade.** An upgrade that stops between creating the new objects and writing the record resumes on
  the next `initialize_schema()` and ends in the same state as an uninterrupted one.
- **Grants on legacy objects.** A role granted EXECUTE on `SQLUser.CY_CMP` in 4.5.x can still run Cypher after the upgrade, because the grant was copied to `CY_CMP` in the IVG Schema; a test runs a query as such a role, before and after.
- **Privileges.** An operator without privileges to create a schema gets an error naming the privilege, not a partial
  install.
- **Read-only and dry runs.** `remove_legacy_objects(dry_run=True)` and the record dry run are read-only for the whole
  operation (before/after snapshot).
- **Upgraded package, namespace not yet upgraded.** `pip install -U` to 4.6.0 without re-running `initialize_schema()`: the first Cypher query raises the FR-006a error, not a missing-function error; the 4.5.x objects keep serving SQL that calls them directly.
- **Mixed versions in one process.** A 4.6.0 engine and code calling a `SQLUser` legacy function in the same connection
  both work.
- **The `rdf_edges` compatibility view** that the 3.2.0 rescue drops and re-creates is no longer re-created in
  `SQLUser`; a rescue on a 3.2.0 namespace still restores every edge.
- **Embedded Python and Community Edition.** Classes skipped because the server lacks embedded Python or `%AI` keep
  being skipped, and the inventory says so.

## Requirements _(mandatory)_

### Functional Requirements

#### The IVG Schema

- **FR-001**: Every table, function, procedure and view IVG creates MUST be in the namespace's IVG Schema, whose default
  name is `Graph_KG`.
- **FR-002**: IVG MUST create no function, procedure, view or class in `SQLUser` or the `User` package.
- **FR-003**: The name MUST be settable by `IRISGraphEngine(schema_prefix=...)` and by `IVG_SCHEMA_PREFIX`, the argument
  taking precedence; IVG MUST NOT read `IRIS_SCHEMA_PREFIX`.
- **FR-004**: The name MUST match `[A-Za-z][A-Za-z0-9_]*`, MUST NOT be `SQLUser` or start with `%`, and an invalid name
  MUST raise `ValueError` before any database call.
- **FR-005**: The first `initialize_schema()` in a namespace MUST record the IVG Schema in the namespace, and every IVG
  ObjectScript class MUST read the schema from that record rather than naming `Graph_KG`.
- **FR-006**: An engine whose schema differs from the namespace's record MUST raise an error naming both, on its first
  database access, before any write.
- **FR-006a**: An engine whose namespace has no schema record, or lacks the 4.6.0 objects, MUST raise one error on its first database access that says the namespace was set up by an earlier release and names `initialize_schema()` and its dry run; it MUST run no other query first, and MUST NOT fall back to the 4.5.x objects.
- **FR-007**: The SQL the Cypher translator, the native executor, the compiled native plans and the fast paths generate
  MUST name only objects in the IVG Schema, fully qualified.
- **FR-008**: Every IVG query MUST qualify its tables; the `SQLUser` views (`nodes`, `rdf_edges`, `rdf_labels`,
  `rdf_props`) MUST NOT be created.

#### Existing namespaces

- **FR-009**: An existing namespace with no record MUST be recorded automatically when IVG's tables are in exactly one
  schema; when they are in more than one, `initialize_schema()` MUST stop with an error listing each schema and its row
  counts, and MUST record nothing until a name is passed explicitly.
- **FR-010**: Upgrading MUST leave the 4.5.x `SQLUser` and `User` objects in place and working, and MUST log once that
  they remain and how to remove them.
- **FR-010a**: Upgrading MUST copy every privilege granted on a 4.5.x legacy object (to a user or a role) onto its replacement in the IVG Schema, and the record dry run (FR-013) MUST list each grant it would copy.
- **FR-011**: `remove_legacy_objects(dry_run=True)` MUST list exactly the objects on a frozen 4.5.x manifest that exist
  in the namespace, and MUST change nothing; `dry_run=False` MUST remove exactly those.
- **FR-012**: The manifest MUST be a fixed list of names and classes taken from the 4.5.x releases, never a pattern.
- **FR-013**: A dry run of the record step MUST report what would be recorded without writing.
- **FR-014**: Compiled native plans built against 4.5.x names MUST be discarded on upgrade.

#### What ships

- **FR-015**: `Graph.KG.Benchmark` and `Graph.KG.BenchFormat` MUST NOT be in the wheel or deployed; they move to
  development sources the tests deploy themselves, and are on the legacy manifest.
- **FR-016**: `Graph.KG.Service` and `Graph.KG.FHIRGraphSyncTask` MUST keep shipping.

#### Inventory and documentation

- **FR-017**: ADMIN_GUIDE MUST list every package, table, function, procedure, view and global a fresh namespace
  holds, with its purpose, whether it is optional, and whether it belongs in a backup; `^IVG.AlgoCache` and
  `^ArnoAccel` included.
- **FR-018**: A test MUST compare that inventory with a freshly initialized namespace and fail on any difference in
  either direction.
- **FR-019**: The 4.6.0 migration notes MUST have a section for consumers that vendor IVG's sources, listing every
  moved, renamed and retired object.
- **FR-020**: The docs MUST state that the namespace, not the schema, is IVG's isolation unit, and that one namespace
  holds one IVG Schema.

#### Speed

- **FR-020a**: On the default IVG Schema and on a non-default one, no write, erase or read path MUST be more than 5% slower than in 4.5.2 (median of repeated runs on the same container), measured by a benchmark gate over at least: edge and node writes (row and bulk), bare-node retire and graph erase, temporal edge writes, a Cypher one-hop and a variable-length read, PageRank, and a ledger commit.

#### Tests

- **FR-021**: There MUST be one end-to-end case per existing write, delete and read path, run in a namespace whose IVG
  Schema is not `Graph_KG`, asserting database state and known answers.
- **FR-022**: Legacy namespaces in the upgrade tests MUST be built from frozen DDL (4.5.1, and 3.2.0 upgraded through
  4.x).

### Key Entities

- **IVG Schema**: the one SQL schema in a namespace that holds all of IVG's SQL objects (see `CONTEXT.md`).
- **Schema record**: the namespace's record of its IVG Schema, written once by the first `initialize_schema()`.
- **Legacy manifest**: the frozen list of objects 4.5.x created in `SQLUser` and `User`, plus the retired benchmark
  classes.
- **Inventory**: the published list of everything a fresh namespace holds after `initialize_schema()`.

## Success Criteria _(mandatory)_

### Measurable Outcomes

- **SC-001**: A freshly initialized namespace has 0 IVG objects in `SQLUser` and the `User` package (4.5.1: about 47
  functions, 4 views and 53 classes).
- **SC-002**: The inventory test reports 0 differences between ADMIN_GUIDE and a fresh namespace.
- **SC-003**: In a namespace whose IVG Schema is `IVGALT`, every write, delete and read path gives its known answer and
  the namespace holds no `Graph_KG` schema.
- **SC-004**: The IVG ObjectScript sources name `Graph_KG` in 0 places outside the default value (4.5.1: 283 in 32
  classes).
- **SC-005**: A 4.5.1 namespace upgrades with 0 changes to the application's SQL; `remove_legacy_objects(dry_run=True)`
  lists 100% of the manifest objects present and 0 others.
- **SC-006**: An engine with a mismatched schema is refused on 100% of first accesses, with 0 rows or globals changed.
- **SC-006a**: The benchmark gate reports every measured path within 5% of 4.5.2 on both `Graph_KG` and `IVGALT`.
- **SC-007**: The two newcomer walkthroughs of the 4.6.0 release gate (IRIS expert, Python developer) report no
  "blocker" or "wrong" item about where IVG's objects live.

## Assumptions

- The schema record lives in a namespace-wide IVG global next to the deploy fingerprint; the plan decides its name.
- "Every write, delete and read path" is the list the existing graph-scope tests enumerate; the plan reuses it.
- An application's own SQL that calls IVG's functions by an unqualified name keeps resolving to the legacy `SQLUser`
  copies until they are removed; after removal it must name the IVG Schema. The migration notes say so.
- hipporag2-pipeline and other consumers are informed through the release notes and, for opsreview, the agent bus; IVG
  edits no other repository.
- 5.0 removes the legacy objects on upgrade; that is out of scope here.
- Renaming a namespace's IVG Schema after it is recorded is out of scope for 4.6.0. The mixed-namespace error (FR-009) and ADMIN_GUIDE give the manual route: record one schema with `schema_prefix=`, then move or drop the other schema's IVG tables with the SQL ADMIN_GUIDE supplies; a test runs that SQL on a mixed namespace and checks the result.
