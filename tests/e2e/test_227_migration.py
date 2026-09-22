"""Spec 227 US5 — the 3.2.0 → 4.0.0 embedding migration, run on a real 3.2.0 schema.

The migration's job is a shape change: `id VARCHAR PRIMARY KEY` becomes
`(graph_id, node_id)`, and a row whose graph nobody can name goes to the quarantine
instead of to the default graph. Every guarantee in that sentence lives in column
declarations, constraints and stored-procedure compilation, so this gate runs against
`ivg-iris-enterprise` and rebuilds the 3.2.0 shape from the frozen fixture rather than
from a hand-written `CREATE TABLE` — see
`tests/unit/test_old_release_fixtures.py::test_v320_freezes_the_statements_that_declared_its_tables`.

The four scenarios are US5 1–4:

1. a single-graph install places every vector and rewrites its registry row (FR-027);
2. an ambiguous node ID is quarantined and reported by count and by ID (FR-028);
3. a resolver is offered the ambiguous rows, and its declines are quarantined (FR-030);
4. an interrupted run re-runs to the same placement (SC-006, FR-029).

Each test starts from a rebuilt 3.2.0 namespace and leaves the namespace at 4.0.0, so
the rest of the 227 suite — which needs `graph_id` on the embedding tables — can run
after it in the same session.
"""

from __future__ import annotations

import contextlib
import os
import re

import pytest

from iris_vector_graph.migrations import migrate_to_graph_scoped_embeddings
from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator
from iris_vector_graph.schema import GraphSchema
from tests.e2e.fixtures.old_releases import release

#: Graph IDs private to this file, so a leftover row cannot be mistaken for a fixture's.
GRAPH = "ivg227mig-a"
OTHER = "ivg227mig-b"

#: The width 3.2.0's frozen DDL declares. Its `emb VECTOR(DOUBLE, 8)` is the column
#: every row here is written through, so the test cannot pick a different one.
DIM = 8

#: A route the migration creates is named from a hash, not from the graph ID.
ROUTE = re.compile(r"^kg_emb_[0-9a-f]{16}$")

#: Dropped before `nodes`, because each one holds a foreign key into it and IRIS
#: refuses to drop a referenced table.
_DEPENDENTS = (
    "rdf_labels",
    "rdf_props",
    "kg_NodeEmbeddings",
    "kg_NodeEmbeddings_optimized",
    "rdf_edges",
)

#: `initialize_schema` step 5b builds these over the `Graph_KG` tables, and IRIS refuses
#: to drop a table a view references (SQLCODE -321). They are the Python PPR fallback's
#: unqualified names, nothing the migration reads, and `_restore_400` rebuilds them.
_VIEWS = ("nodes", "rdf_edges", "rdf_labels", "rdf_props")


# --- the 3.2.0 namespace ------------------------------------------------------------


def _quietly(cursor, sql, params=None):
    """Run a statement whose failure is an acceptable answer.

    The 3.2.0 replay is deliberately not idempotent — its `ALTER`s are that release's
    own upgrade path — so "already exists" and "no such constraint" are both expected
    while the shape converges. What the shape actually became is asserted afterwards,
    not inferred from these.
    """
    with contextlib.suppress(Exception):
        cursor.execute(sql, list(params or []))


def _quietly_indexes(cursor) -> None:
    """The indexes a real 3.2.0 install has, none of which the captured DDL describes."""
    from iris_vector_graph.schema import GraphSchema

    with contextlib.suppress(Exception):
        GraphSchema.ensure_indexes(cursor)


def _step(cursor, sql, log, params=None) -> None:
    """Run a rebuild statement and record what it did.

    Same tolerance as `_quietly`, but the outcome is kept: when the rebuilt shape turns
    out wrong, the failure message can say which statement is responsible instead of
    only that the end state is not 3.2.0.
    """
    try:
        cursor.execute(sql, list(params or []))
        log.append(("ok", sql.split("(")[0].strip()[:70]))
    except Exception as exc:  # noqa: BLE001 - the text is the diagnostic
        first = str(exc).replace("\n", " ")[:150]
        log.append((first, sql.split("(")[0].strip()[:70]))


def _tables(cursor) -> list:
    cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'"
    )
    return [str(r[0]) for r in cursor.fetchall() or []]


def _columns(cursor, table: str) -> set:
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ?",
        [table],
    )
    return {str(r[0]) for r in cursor.fetchall() or []}


def _order(cursor, table: str) -> list:
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
        [table],
    )
    return [str(r[0]) for r in cursor.fetchall() or []]


def _constraints(cursor, table: str) -> set:
    cursor.execute(
        "SELECT CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND TABLE_NAME = ?",
        [table],
    )
    return {str(r[0]) for r in cursor.fetchall() or []}


def _foreign_keys(cursor) -> list:
    cursor.execute(
        "SELECT TABLE_NAME, CONSTRAINT_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE TABLE_SCHEMA = 'Graph_KG' AND CONSTRAINT_TYPE = 'FOREIGN KEY'"
    )
    return [(str(r[0]), str(r[1])) for r in cursor.fetchall() or []]


def _procedures(cursor) -> set:
    """The stored procedures in the `Graph_KG` schema, minus the class projections.

    IRIS projects a `<Class>_Extent` class query for every persistent class, so the
    routine list carries *class* names as well as procedure names. The migration
    drains into `kg_NodeEmbeddings_ivg400` and renames the SQL table into place,
    which moves the table name but not the class name — so a migrated install
    answers `kgNodeEmbeddingsivg400_Extent` where a fresh one answers
    `kgNodeEmbeddings_Extent`. Every reader in the package resolves a table's class
    through `GraphSchema.resolve_table_class`, i.e. from the dictionary rather than
    from the name, so the difference is inert; it is recorded in
    docs/KNOWN_ISSUES.md rather than compared here, because comparing it would make
    the procedure check fail on a fact about a name nobody calls.
    """
    cursor.execute(
        "SELECT ROUTINE_NAME FROM INFORMATION_SCHEMA.ROUTINES "
        "WHERE ROUTINE_SCHEMA = 'Graph_KG'"
    )
    return {str(r[0]) for r in cursor.fetchall() or [] if not str(r[0]).endswith("_Extent")}


def _registry(cursor) -> dict:
    cursor.execute(
        "SELECT table_name, graph_id, dimension FROM Graph_KG.embedding_registry"
    )
    return {
        str(r[0]): (str(r[1] or ""), int(r[2] or 0)) for r in cursor.fetchall() or []
    }


def _tear_down_tables(cursor, log: list) -> None:
    """Drop `nodes` and everything that references it, whatever shape they are in.

    IRIS refuses to drop a referenced table (SQLCODE -320), so the five foreign keys
    go first, by name: that is what makes this independent of the release that declared
    them. One pass is not enough, though. `rdf_edges` has been observed to report a
    successful `DROP TABLE` and still stand, because releasing its own key first failed
    with `<CLASS DOES NOT EXIST>Open+50^%apiDDL *Graph.KG.Edge` — a stale compiled-class
    reference `%apiDDL` cannot open. (`Graph.KG.Edge` is deleted as of spec 227, and
    `deploy_objectscript_classes` now deletes it from the namespace too, so that exact
    text should not recur; the retry stays because the shape of the failure — a drop
    reporting success and leaving the table standing — is not specific to it.)
    The table does drop on the next attempt, and only
    then does `nodes` become droppable. So this repeats, re-reading the keys each round
    and leaving `nodes` for last, until `nodes` is gone.
    """
    for _ in range(3):
        for table, constraint in _foreign_keys(cursor):
            _step(cursor, f"ALTER TABLE Graph_KG.{table} DROP CONSTRAINT {constraint}", log)
        standing = set(_tables(cursor))
        for table in _DEPENDENTS:
            if table in standing:
                _step(cursor, f"DROP TABLE Graph_KG.{table}", log)
        if "nodes" not in _tables(cursor):
            return
        _step(cursor, "DROP TABLE Graph_KG.nodes", log)
        if "nodes" not in _tables(cursor):
            return


def _rebuild_320(conn) -> None:
    """Put the namespace back in 3.2.0's shape, whatever shape it is in now."""
    cursor = conn.cursor()
    log: list = []
    try:
        for table in _tables(cursor):
            if ROUTE.match(table) or table.endswith("_ivg400"):
                _step(cursor, f"DROP TABLE Graph_KG.{table}", log)
        _quietly(cursor, "DELETE FROM Graph_KG.embedding_quarantine")
        _quietly(cursor, "DELETE FROM Graph_KG.embedding_registry")
        # The procedure is dropped so its reinstatement is observable: a 3.2.0 install
        # already has a `kg_KNN_VEC`, and asserting "it exists" against that one would
        # pass with the migration's procedure install deleted.
        _quietly(cursor, "DROP PROCEDURE Graph_KG.kg_KNN_VEC")
        for view in _VIEWS:
            _step(cursor, f"DROP VIEW SQLUser.{view}", log)
        _tear_down_tables(cursor, log)
        for statement in release("v3.2.0").ddl_statements():
            _step(cursor, statement, log)
        # The captured DDL is tables and columns only — `--ddl` reads
        # INFORMATION_SCHEMA, which does not describe indexes — so a replayed 3.2.0
        # schema carries none, while a real one has everything `ensure_indexes`
        # installs. That difference is not inert: `idx_props_val_ifind` projects three
        # stored procedures (`rdfprops_idxpropsvalifind{Find,Highlight,Rank}`), so
        # without this the upgraded namespace looks like it is missing procedures the
        # migration never owned. Every statement in `ensure_indexes` predates 227 and
        # is idempotent, so it cannot reshape the 3.2.0 tables `_assert_is_320` checks.
        _quietly_indexes(cursor)
        # `save_snapshot` at 3.2.0 does not export `embedding_registry`, so the rows a
        # 3.2.0 install holds — `adopted`, unscoped, one per embedding table — are
        # written here. They are what the migration reads a width and a graph off.
        for table in ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized"):
            _quietly(
                cursor,
                "INSERT INTO Graph_KG.embedding_registry "
                "(table_name, graph_id, dimension, dtype, set_at, set_by) "
                "VALUES (?, '', ?, 'DOUBLE', CURRENT_TIMESTAMP, 'adopted')",
                [table, DIM],
            )
        with contextlib.suppress(Exception):
            conn.commit()
        # The replay tolerates per-statement failure, so the only honest check is the
        # shape it produced. Report the whole sequence when it is wrong: which statement
        # failed is the difference between "IRIS refused a drop" and "the fixture DDL
        # is stale", and that is not recoverable from the assertion in `_assert_is_320`.
        wrong = [
            complaint
            for complaint, bad in (
                ("kg_NodeEmbeddings is not keyed `id`", "id" not in _columns(cursor, "kg_NodeEmbeddings")),
                ("rdf_labels already declares graph_id", "graph_id" in _columns(cursor, "rdf_labels")),
                ("nodes is not 3.2.0-keyed", "uq_nodes_nodeid" not in _constraints(cursor, "nodes")),
                # 3.2.0 reaches `graph_id` on rdf_edges by its own ALTER, so the column
                # is last there and second on a 4.0.0 install. Position is the only
                # tell that a 4.0.0 rdf_edges survived the drop — its column *names*
                # are identical — and a survivor makes `_rekey_children` early-return.
                (
                    "rdf_edges is still 4.0.0-shaped (graph_id is not its last column)",
                    _order(cursor, "rdf_edges")[-1:] != ["graph_id"],
                ),
            )
            if bad
        ]
        if wrong:
            raise AssertionError(
                "rebuilding the 3.2.0 schema left it in the wrong shape ("
                + "; ".join(wrong)
                + "):\n"
                + "\n".join(f"  {outcome}  <-  {sql}" for outcome, sql in log)
            )
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _assert_is_320(conn) -> None:
    """Refuse to run the scenarios against anything but a 3.2.0 schema.

    Without this the whole file passes vacuously on an already-migrated namespace: the
    migration would find nothing to do and every "it placed the rows" assertion would
    be reading rows the fixture itself wrote in the 4.0.0 shape.
    """
    cursor = conn.cursor()
    try:
        embeddings = _columns(cursor, "kg_NodeEmbeddings")
        assert "id" in embeddings, f"kg_NodeEmbeddings is not 3.2.0-shaped: {embeddings}"
        assert "node_id" not in embeddings, "kg_NodeEmbeddings was already reshaped"
        assert "graph_id" not in embeddings, "kg_NodeEmbeddings was already reshaped"
        assert "uq_nodes_nodeid" in _constraints(cursor, "nodes"), (
            "nodes is not 3.2.0-keyed; the re-key this migration performs has already "
            "happened, so its refusal path cannot be observed"
        )
        assert "graph_id" not in _columns(cursor, "rdf_labels")
        assert "kg_KNN_VEC" not in _procedures(cursor), (
            "a kg_KNN_VEC survived the rebuild, so its reinstatement is unobservable"
        )
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


class Env320:
    """A 3.2.0 install, plus the writes a scenario needs on top of it."""

    graph = GRAPH
    other = OTHER
    dim = DIM

    def __init__(self, conn):
        self.conn = conn
        self.cursor = conn.cursor()

    # -- population --------------------------------------------------------
    def node(self, node_id: str, graph: str = GRAPH) -> None:
        self.cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id, graph_id) VALUES (?, ?)",
            [node_id, graph],
        )

    def label(self, node_id: str, label: str = "Patient") -> None:
        self.cursor.execute(
            "INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", [node_id, label]
        )

    def vector(self, node_id: str, fill: float = 0.25, table="kg_NodeEmbeddings") -> None:
        """One legacy row, written through the 3.2.0 column as a string.

        `TO_VECTOR` keeps the value inside IRIS (ADR-0005) and is checked against the
        declared width, so a row that lands here is a row 3.2.0 would have accepted.
        """
        literal = "[" + ",".join([str(float(fill))] * DIM) + "]"
        self.cursor.execute(
            f"INSERT INTO Graph_KG.{table} (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE, ?))",
            [node_id, literal, DIM],
        )

    def rekeyed(self) -> None:
        """Leave the schema where an interrupted migration leaves it.

        3.2.0 enforces `uq_nodes_nodeid`, so a node ID in two graphs is impossible on a
        pristine install. The one real state in which it can exist *while the embedding
        table is still legacy* is a migration killed between the re-key and the drain —
        which is also the state `_rekey_children` early-returns from. So the ambiguity
        scenarios start here rather than from a schema no release ever shipped.
        """
        for sql in GraphSchema.get_graph_scope_migration_sql():
            _quietly(self.cursor, sql)
        self.commit()

    def commit(self) -> None:
        self.conn.commit()

    # -- observation -------------------------------------------------------
    def placed(self, table="kg_NodeEmbeddings") -> set:
        self.cursor.execute(f"SELECT graph_id, node_id FROM Graph_KG.{table}")
        return {(str(r[0] or ""), str(r[1])) for r in self.cursor.fetchall() or []}

    def quarantined(self) -> set:
        self.cursor.execute(
            "SELECT node_id, source_table, reason FROM Graph_KG.embedding_quarantine"
        )
        return {
            (str(r[0]), str(r[1]), str(r[2])) for r in self.cursor.fetchall() or []
        }

    def registry(self) -> dict:
        return _registry(self.cursor)

    def routes(self) -> list:
        return sorted(t for t in _tables(self.cursor) if ROUTE.match(t))

    def columns(self, table: str) -> set:
        return _columns(self.cursor, table)

    def procedures(self) -> set:
        return _procedures(self.cursor)


@pytest.fixture(scope="function")
def env320(iris_connection):
    """A freshly rebuilt 3.2.0 namespace, left at 4.0.0 afterwards.

    A missing container is a FAILURE, never a skip (constitution VIII gate 1): this
    test asserts a storage layout, and a skipped storage-layout test reads exactly like
    a passing one.
    """
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "the 3.2.0 → 4.0.0 migration is a shape change, so it cannot run without "
            "IRIS. Start ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: this gate reads column declarations and "
            "constraints, which mocks cannot observe."
        )

    _rebuild_320(iris_connection)
    _assert_is_320(iris_connection)
    env = Env320(iris_connection)
    yield env
    with contextlib.suppress(Exception):
        env.cursor.close()
    _restore_400(iris_connection)


def _restore_400(conn) -> None:
    """Leave the namespace at 4.0.0 for whatever runs next.

    Not cosmetic: every other 227 gate reads `graph_id` off the embedding tables, so a
    namespace left at 3.2.0 by this file fails them with a missing column.
    """
    from iris_vector_graph import IRISGraphEngine

    with contextlib.suppress(Exception):
        IRISGraphEngine(conn).initialize_schema(auto_deploy_objectscript=False)


# --- US5 scenario 1 -----------------------------------------------------------------


def test_a_single_graph_install_places_every_vector(env320):
    """One graph means nothing to guess: every node in the install is in that graph.

    So the whole migration for that table is to move its rows into the 4.0.0 shape
    under that graph and say so on the registry row (FR-027, FR-031) — no route table,
    no quarantine, no resolver.
    """
    env320.node("n1")
    env320.node("n2")
    env320.label("n1")
    env320.vector("n1", 0.1)
    env320.vector("n2", 0.2)
    env320.commit()

    report = migrate_to_graph_scoped_embeddings(env320.conn)

    assert report.rows_placed == {(GRAPH, "kg_NodeEmbeddings"): 2}, report.rows_placed
    assert report.rows_quarantined == {}
    assert report.quarantined_ids == []
    assert report.tables_created == [], (
        "a single-graph install needs no route: its one table *is* the graph's table"
    )
    assert env320.placed() == {(GRAPH, "n1"), (GRAPH, "n2")}
    assert env320.routes() == []


def test_the_registry_row_names_the_graph_its_rows_went_to(env320):
    """The registry is how a reader finds a graph's vectors, so an unrewritten row
    leaves the rows placed and unreachable."""
    env320.node("n1")
    env320.vector("n1")
    env320.commit()

    migrate_to_graph_scoped_embeddings(env320.conn)

    graph, dimension = env320.registry()["kg_NodeEmbeddings"]
    assert graph == GRAPH, "the registry row is still unscoped after the migration"
    assert dimension == DIM


def test_the_migrated_table_is_keyed_by_graph_and_node(env320):
    """The shape change itself: no `ALTER` reaches `(graph_id, node_id)` from a VARCHAR
    primary key, so this is the evidence the rows were moved rather than altered."""
    env320.node("n1")
    env320.vector("n1")
    env320.commit()

    migrate_to_graph_scoped_embeddings(env320.conn)

    columns = env320.columns("kg_NodeEmbeddings")
    assert {"graph_id", "node_id", "emb"} <= columns, columns
    assert "id" not in columns, "the 3.2.0 key column survived the migration"


def test_the_migration_installs_the_procedure_the_upgrade_had_to_defer(env320):
    """The live half of the ordering trap.

    `initialize_schema` on a 3.2.0 install cannot compile the 4.0.0 `kg_KNN_VEC` — its
    body reads `n.node_id` and `n.graph_id`, and the table has neither — so it defers
    the procedure and names this migration. The reshape is what makes it compilable, so
    if the migration does not declare it, an operator who followed the documented
    upgrade order ends up on 4.0.0 with no server-side vector search and nothing said.
    """
    env320.node("n1")
    env320.vector("n1")
    env320.commit()

    migrate_to_graph_scoped_embeddings(env320.conn)

    assert "kg_KNN_VEC" in env320.procedures()


# --- US5 scenario 2 -----------------------------------------------------------------


def test_an_ambiguous_node_id_is_quarantined_by_count_and_by_id(env320):
    """Two graphs hold this node ID, so no data says which graph the vector is from.

    `MIN(graph_id)` would be the silent version of this migration (FR-028): the row is
    quarantined and named instead, because an operator can only place it by hand if the
    report tells them which row to place.
    """
    env320.rekeyed()
    env320.node("shared", GRAPH)
    env320.node("shared", OTHER)
    env320.vector("shared")
    env320.commit()

    report = migrate_to_graph_scoped_embeddings(env320.conn)

    assert report.rows_quarantined == {"ambiguous_graph": 1}, report.rows_quarantined
    assert report.quarantined_ids == ["shared"]
    assert report.rows_placed == {}, report.rows_placed
    assert env320.quarantined() == {
        ("shared", "kg_NodeEmbeddings", "ambiguous_graph")
    }


def test_an_unplaceable_row_never_reaches_the_default_graph(env320):
    """The failure this migration exists to avoid, stated as its own assertion: a
    quarantined row must not also be sitting in `''` where a default-graph read would
    return it as that graph's vector."""
    env320.rekeyed()
    env320.node("shared", GRAPH)
    env320.node("shared", OTHER)
    env320.vector("shared")
    env320.commit()

    migrate_to_graph_scoped_embeddings(env320.conn)

    assert env320.placed() == set(), (
        "an ambiguous row was left in the migrated table, so a read of the default "
        "graph returns a vector nobody could attribute to a graph"
    )


def test_a_vector_whose_node_no_graph_holds_is_quarantined_as_such(env320):
    """A different reason, and the distinction matters to whoever reads the report: an
    orphan needs its node restored, an ambiguity needs a graph named.

    Written after the re-key because 3.2.0's `fk_emb_node` is what makes an orphan
    impossible while it stands — the driver refuses the INSERT outright. Step 1 of the
    re-key drops that foreign key and nothing re-adds it centrally (a routed table
    declares its own), so an interrupted migration is where orphans become reachable.
    """
    env320.rekeyed()
    env320.vector("orphan")
    env320.commit()

    report = migrate_to_graph_scoped_embeddings(env320.conn)

    assert report.rows_quarantined == {"no_node": 1}, report.rows_quarantined
    assert env320.quarantined() == {("orphan", "kg_NodeEmbeddings", "no_node")}


# --- US5 scenario 3 -----------------------------------------------------------------


def test_a_resolver_is_offered_the_ambiguous_row_with_its_candidates(env320):
    """The callback has to be given enough to answer with: which node, from which
    table, at what width, and which graphs could claim it."""
    env320.rekeyed()
    env320.node("shared", GRAPH)
    env320.node("shared", OTHER)
    env320.vector("shared")
    env320.commit()
    offered = []

    def resolver(ambiguous):
        offered.append(ambiguous)
        return None

    migrate_to_graph_scoped_embeddings(env320.conn, resolver=resolver)

    assert [a.node_id for a in offered] == ["shared"]
    assert offered[0].source_table == "kg_NodeEmbeddings"
    assert offered[0].dimension == DIM
    assert sorted(offered[0].candidate_graphs) == sorted([GRAPH, OTHER])


def test_a_resolver_that_declines_leaves_the_row_quarantined(env320):
    """Declining and having no resolver reach the same placement and differ only in the
    recorded reason (FR-030) — which is what tells an operator a human already looked."""
    env320.rekeyed()
    env320.node("shared", GRAPH)
    env320.node("shared", OTHER)
    env320.vector("shared")
    env320.commit()

    report = migrate_to_graph_scoped_embeddings(
        env320.conn, resolver=lambda ambiguous: None
    )

    assert report.rows_quarantined == {"resolver_declined": 1}, report.rows_quarantined
    assert report.quarantined_ids == ["shared"]
    assert env320.quarantined() == {
        ("shared", "kg_NodeEmbeddings", "resolver_declined")
    }
    assert env320.placed() == set()


def test_a_resolver_naming_a_candidate_places_the_row_in_that_graphs_route(env320):
    """An answered ambiguity is not a default-graph row: the graph named is not the
    table's own graph, so the vector goes to that graph's routed table."""
    env320.rekeyed()
    env320.node("shared", GRAPH)
    env320.node("shared", OTHER)
    env320.vector("shared")
    env320.commit()

    report = migrate_to_graph_scoped_embeddings(
        env320.conn, resolver=lambda ambiguous: GRAPH
    )

    assert report.rows_quarantined == {}, report.rows_quarantined
    routes = env320.routes()
    assert len(routes) == 1, f"expected one route for {GRAPH}, got {routes}"
    assert report.rows_placed == {(GRAPH, routes[0]): 1}, report.rows_placed
    assert report.tables_created == routes
    assert env320.placed(routes[0]) == {(GRAPH, "shared")}
    assert env320.placed() == set(), "the row was placed twice"


def test_a_resolver_naming_a_graph_that_does_not_hold_the_node_has_declined(env320):
    """Honouring it would invent a membership no `nodes` row supports — and the route's
    composite key would refuse the INSERT anyway, halfway through the migration."""
    env320.rekeyed()
    env320.node("shared", GRAPH)
    env320.node("shared", OTHER)
    env320.vector("shared")
    env320.commit()

    report = migrate_to_graph_scoped_embeddings(
        env320.conn, resolver=lambda ambiguous: "a-graph-that-holds-nothing"
    )

    assert report.rows_quarantined == {"resolver_declined": 1}, report.rows_quarantined
    assert env320.routes() == []


# --- US5 scenario 4 (SC-006) -------------------------------------------------------


def test_an_interrupted_run_re_runs_to_the_same_placement(env320, monkeypatch):
    """The interruption this job actually suffers is being killed, so the fix has to be
    "run it again" (FR-029, SC-006).

    The drain is interrupted after its first row moves: the watermark it resumes from is
    read off its own destination, so the second pass owes exactly the rows the first
    pass did not move, and the final placement is the one a clean run produces.
    """
    env320.node("n1")
    env320.node("n2")
    env320.vector("n1", 0.1)
    env320.vector("n2", 0.2)
    env320.commit()

    real_move = _Migrator._move
    moves = {"count": 0}

    def killed_after_one(self, cursor, sql, params, **kwargs):
        moves["count"] += 1
        if moves["count"] > 1:
            raise RuntimeError("killed mid-drain (SC-006)")
        return real_move(self, cursor, sql, params, **kwargs)

    monkeypatch.setattr(_Migrator, "_move", killed_after_one)
    with pytest.raises(Exception):
        migrate_to_graph_scoped_embeddings(env320.conn)
    monkeypatch.undo()

    report = migrate_to_graph_scoped_embeddings(env320.conn)

    assert report.rows_placed == {(GRAPH, "kg_NodeEmbeddings"): 2}, report.rows_placed
    assert report.rows_quarantined == {}
    assert env320.placed() == {(GRAPH, "n1"), (GRAPH, "n2")}
    assert env320.registry()["kg_NodeEmbeddings"][0] == GRAPH


def test_a_second_complete_run_changes_nothing(env320):
    """It is a migration, not a sync: the shape probe is what makes the second pass a
    no-op, and a re-run that re-drained would double-count every row it re-placed."""
    env320.node("n1")
    env320.vector("n1")
    env320.commit()

    first = migrate_to_graph_scoped_embeddings(env320.conn)
    second = migrate_to_graph_scoped_embeddings(env320.conn)

    assert second.rows_placed == first.rows_placed
    assert second.rows_quarantined == first.rows_quarantined
    assert second.tables_created == []
    assert env320.placed() == {(GRAPH, "n1")}
