"""Spec 230 US5 gate — the 3.2.0 → 4.0.0 migration accounts for every row, twice.

Two claims, both of which only IRIS can answer:

* **every row is placed or quarantined** (SC-007, FR-018/FR-019) — a `docs` row's
  graph is the graph holding the node its ID names, and an edge vector's is the graph
  *asserting the triple*. Both answers are rows in tables, and the interesting cases
  — an ID two graphs hold, a triple no graph asserts — are exactly the ones a fake
  store would have to be told the answer to. Nothing is deleted and nothing is
  placed by guess: the count before equals placed + quarantined after, and every
  placed row's graph is re-derived here from the same rows the migration read.
* **a second run changes nothing** (SC-008) — the hazard is specific. Re-running
  `docs_finish_sql` or the edge prepare→place→finish sequence against an install
  that is *already* 4.0.0 rebuilds both tables and re-derives every row's graph from
  the current `nodes`/`rdf_edges`, which is not a no-op — it is a second migration
  of migrated data. `upgrade_to_4_0_0` is the step that has to notice, and only the
  catalog can tell it.

The placement tests build a genuine pre-migration fixture in a scratch schema rather
than migrating `Graph_KG`: this install is already 4.0.0, so its `docs` has no
unplaced row and every accounting assertion would pass over an empty set. IRIS
preserves a schema name's case as written, so the scratch schemas below are the names
`INFORMATION_SCHEMA` reports and the ones the migration's own probes match on.

`SKIP_IRIS_TESTS=true` fails rather than skips, for the reason the other spec 230
E2E files give: a skipped scope test is indistinguishable from a passing one.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytestmark = [pytest.mark.e2e]

#: One scratch schema per test. Two tests migrate a pre-migration fixture and the
#: first one's finish renames the tables into the 4.0.0 shape, so sharing a schema
#: would leave the second test measuring the first one's output.
ACCOUNTING_SCHEMA = "IVG230MigAcct"
IDEMPOTENCE_SCHEMA = "IVG230MigIdem"

GRAPH_A = "ivg230mig:a"
GRAPH_B = "ivg230mig:b"

#: The scratch edge vectors' declared width. Small on purpose: the assertions are
#: about which row lands where, and a wide vector only slows the fixture down.
DIM = 8

PRED = "IVG230MIG_LINKS"

#: `docs` rows, one per outcome the placement can reach.
DOC_DEFAULT = "ivg230mig:doc:default"  # node in the default graph only → placed ''
DOC_A = "ivg230mig:doc:a"  # node in graph A only → placed A
DOC_SHARED = "ivg230mig:doc:shared"  # node in A and B → ambiguous_graph
DOC_ORPHAN = "ivg230mig:doc:orphan"  # no node anywhere → no_node

#: Edge vectors, same three outcomes. `no_edge` rather than `no_node`: the row names
#: a triple, and "no graph asserts it" is a different fact from a missing node.
EDGE_PLACED = ("ivg230mig:e:s1", PRED, "ivg230mig:e:o1")
EDGE_SHARED = ("ivg230mig:e:s2", PRED, "ivg230mig:e:o2")
EDGE_ORPHAN = ("ivg230mig:e:s3", PRED, "ivg230mig:e:o3")


# ---------------------------------------------------------------------------
# The pre-migration fixture
# ---------------------------------------------------------------------------


def _exec(cursor, sql, params=None):
    cursor.execute(sql, params or [])


def _drop_schema(conn, schema: str) -> None:
    """Remove every table this file creates in `schema`, in any half-built state."""
    cursor = conn.cursor()
    try:
        for table in (
            "docs",
            "docs_ivg400",
            "docs_quarantine",
            "kg_EdgeEmbeddings",
            "kg_EdgeEmbeddings_ivg400",
            "edge_vector_quarantine",
            "nodes",
            "rdf_props",
            "rdf_labels",
            "rdf_edges",
        ):
            with contextlib.suppress(Exception):
                _exec(cursor, f"DROP TABLE {schema}.{table}")
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _vector_literal(fill: float) -> str:
    return "[" + ",".join(str(float(fill)) for _ in range(DIM)) + "]"


def _build_pre_migration(conn, schema: str) -> None:
    """A 3.2.0-shaped `docs` and `kg_EdgeEmbeddings`, plus the rows they are read against.

    `docs` is keyed on `id` alone with no `graph_id` column, and `kg_EdgeEmbeddings`
    on the triple alone — the two shapes 4.0.0 replaces. `nodes` and `rdf_edges`
    arrive already graph-scoped, because they are what the placement reads: an
    install whose `nodes` had no `graph_id` could not be migrated at all.
    """
    _drop_schema(conn, schema)
    cursor = conn.cursor()
    try:
        _exec(
            cursor,
            f"CREATE TABLE {schema}.nodes (\n"
            "  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
            "  node_id  VARCHAR(256) %EXACT NOT NULL,\n"
            "  CONSTRAINT pk_nodes PRIMARY KEY (graph_id, node_id)\n"
            ")",
        )
        _exec(
            cursor,
            f"CREATE TABLE {schema}.rdf_edges (\n"
            "  graph_id VARCHAR(256) %EXACT NOT NULL DEFAULT '',\n"
            "  s    VARCHAR(256) %EXACT NOT NULL,\n"
            "  p    VARCHAR(512) %EXACT NOT NULL,\n"
            "  o_id VARCHAR(256) %EXACT NOT NULL\n"
            ")",
        )
        # 3.2.0's shape: `id` is the whole key and means whatever the writer chose.
        _exec(
            cursor,
            f"CREATE TABLE {schema}.docs (\n"
            "  id   VARCHAR(256) %EXACT NOT NULL PRIMARY KEY,\n"
            "  text VARCHAR(4000) %EXACT\n"
            ")",
        )
        _exec(
            cursor,
            f"CREATE TABLE {schema}.kg_EdgeEmbeddings (\n"
            "  s    VARCHAR(256) %EXACT NOT NULL,\n"
            "  p    VARCHAR(512) %EXACT NOT NULL,\n"
            "  o_id VARCHAR(256) %EXACT NOT NULL,\n"
            f"  emb  VECTOR(DOUBLE, {DIM}),\n"
            "  CONSTRAINT pk_edge_emb PRIMARY KEY (s, p, o_id)\n"
            ")",
        )

        for graph, node_id in (
            ("", DOC_DEFAULT),
            (GRAPH_A, DOC_A),
            (GRAPH_A, DOC_SHARED),
            # The same node ID in a second graph — the row spec 227's
            # `UNIQUE (graph_id, node_id)` made possible and the reason a document
            # cannot be placed by ID alone.
            (GRAPH_B, DOC_SHARED),
        ):
            _exec(
                cursor,
                f"INSERT INTO {schema}.nodes (graph_id, node_id) VALUES (?, ?)",
                [graph, node_id],
            )

        for graph, triple in (
            (GRAPH_A, EDGE_PLACED),
            (GRAPH_A, EDGE_SHARED),
            (GRAPH_B, EDGE_SHARED),
        ):
            _exec(
                cursor,
                f"INSERT INTO {schema}.rdf_edges (graph_id, s, p, o_id) VALUES (?, ?, ?, ?)",
                [graph, *triple],
            )

        for doc_id in (DOC_DEFAULT, DOC_A, DOC_SHARED, DOC_ORPHAN):
            _exec(
                cursor,
                f"INSERT INTO {schema}.docs (id, text) VALUES (?, ?)",
                [doc_id, f"text of {doc_id}"],
            )

        for fill, triple in enumerate((EDGE_PLACED, EDGE_SHARED, EDGE_ORPHAN), start=1):
            # The literal is inlined rather than bound: TO_VECTOR's source is parsed,
            # not bound, on the IRIS builds this suite runs against.
            _exec(
                cursor,
                f"INSERT INTO {schema}.kg_EdgeEmbeddings (s, p, o_id, emb) "
                f"VALUES (?, ?, ?, TO_VECTOR('{_vector_literal(fill)}', DOUBLE, {DIM}))",
                list(triple),
            )
        conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _rows(conn, sql, params=None):
    cursor = conn.cursor()
    try:
        _exec(cursor, sql, params)
        return [tuple(r) for r in (cursor.fetchall() or [])]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _count(conn, sql) -> int:
    rows = _rows(conn, sql)
    return int(rows[0][0]) if rows else 0


def _snapshot(conn, schema: str) -> dict:
    """Everything the two placement steps can change, in one comparable value."""
    return {
        "docs": sorted(_rows(conn, f"SELECT graph_id, id, text FROM {schema}.docs")),
        "docs_quarantine": sorted(
            _rows(conn, f"SELECT doc_id, text, reason FROM {schema}.docs_quarantine")
        ),
        "edges": sorted(
            _rows(conn, f"SELECT graph_id, s, p, o_id FROM {schema}.kg_EdgeEmbeddings")
        ),
        "edge_quarantine": sorted(
            _rows(conn, f"SELECT s, p, o_id, reason FROM {schema}.edge_vector_quarantine")
        ),
    }


@pytest.fixture
def live(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 230 US5 asserts that a migration accounts for every row and that a "
            "second run changes nothing. Both are facts about the catalog and the "
            "rows in it. SKIP_IRIS_TESTS=true is not an acceptable outcome — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: a pre-migration table shape cannot be built, "
            "let alone migrated, from Python alone."
        )
    return iris_connection


# ---------------------------------------------------------------------------
# T066 — every row is placed or quarantined (SC-007)
# ---------------------------------------------------------------------------


def test_every_row_is_placed_or_quarantined(live):
    """Placed + quarantined equals what was there, and no row moved on a guess."""
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    schema = ACCOUNTING_SCHEMA
    _build_pre_migration(live, schema)
    try:
        docs_before = _count(live, f"SELECT COUNT(*) FROM {schema}.docs")
        edges_before = _count(live, f"SELECT COUNT(*) FROM {schema}.kg_EdgeEmbeddings")
        assert (docs_before, edges_before) == (4, 3)

        report = upgrade_to_4_0_0(
            live,
            steps=("docs", "edge_vectors"),
            edge_dimension=DIM,
            schema=schema,
        )

        docs = report["docs"].report
        edges = report["edge_vectors"].report
        assert not report["docs"].skipped
        assert not report["edge_vectors"].skipped

        # Every row decided, none twice.
        assert docs.rows_accounted == docs_before
        assert edges.rows_accounted == edges_before

        # Placed where the rows say, quarantined by name where they do not.
        assert docs.rows_placed == {"": 1, GRAPH_A: 1}
        assert docs.rows_quarantined == {"ambiguous_graph": 1, "no_node": 1}
        assert sorted(docs.quarantined_keys) == sorted([DOC_SHARED, DOC_ORPHAN])

        assert edges.rows_placed == {GRAPH_A: 1}
        assert edges.rows_quarantined == {"ambiguous_graph": 1, "no_edge": 1}

        # Nothing deleted: the rows are in the table or in the quarantine.
        assert (
            _count(live, f"SELECT COUNT(*) FROM {schema}.docs")
            + _count(live, f"SELECT COUNT(*) FROM {schema}.docs_quarantine")
            == docs_before
        )
        assert (
            _count(live, f"SELECT COUNT(*) FROM {schema}.kg_EdgeEmbeddings")
            + _count(live, f"SELECT COUNT(*) FROM {schema}.edge_vector_quarantine")
            == edges_before
        )

        # A quarantined document keeps its text — the record an operator places it
        # from. A quarantined vector keeps its vector, for the same reason.
        quarantined = dict(
            (row[0], row[1])
            for row in _rows(live, f"SELECT doc_id, text FROM {schema}.docs_quarantine")
        )
        assert quarantined[DOC_SHARED] == f"text of {DOC_SHARED}"
        assert (
            _count(
                live,
                f"SELECT COUNT(*) FROM {schema}.edge_vector_quarantine WHERE emb IS NOT NULL",
            )
            == 2
        )

        # Nothing placed by guess: re-derive each placed row's graph from the rows
        # the migration read, and refuse to accept a graph that does not claim it.
        for graph_id, doc_id, _text in _rows(
            live, f"SELECT graph_id, id, text FROM {schema}.docs"
        ):
            claimants = [
                r[0] or ""
                for r in _rows(
                    live,
                    f"SELECT graph_id FROM {schema}.nodes WHERE node_id = ?",
                    [doc_id],
                )
            ]
            assert claimants == [graph_id or ""], f"{doc_id} placed in {graph_id!r}"

        for graph_id, s, p, o_id in _rows(
            live, f"SELECT graph_id, s, p, o_id FROM {schema}.kg_EdgeEmbeddings"
        ):
            claimants = [
                r[0] or ""
                for r in _rows(
                    live,
                    f"SELECT graph_id FROM {schema}.rdf_edges "
                    "WHERE s = ? AND p = ? AND o_id = ?",
                    [s, p, o_id],
                )
            ]
            assert claimants == [graph_id or ""]

        # And the rebuilt tables are the 4.0.0 shape, not the 3.2.0 one with a column
        # bolted on: `docs.graph_id` is NOT NULL once every row has a graph.
        nullable = _rows(
            live,
            "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = 'docs' "
            "AND COLUMN_NAME = 'graph_id'",
        )
        assert nullable and str(nullable[0][0]).upper() in ("NO", "0", "FALSE")
    finally:
        _drop_schema(live, schema)


# ---------------------------------------------------------------------------
# T065 — the second run changes nothing (SC-008)
# ---------------------------------------------------------------------------


def test_a_second_placement_pass_changes_nothing(live):
    """The hazard in the flesh: run the placement twice over a 3.2.0 install.

    A second pass that re-prepared and re-finished would rebuild both tables and
    re-derive every row's graph from the current rows — a migration of migrated data,
    not a no-op. `upgrade_to_4_0_0` has to read the catalog and decline.
    """
    from iris_vector_graph.migrations import upgrade_to_4_0_0

    schema = IDEMPOTENCE_SCHEMA
    _build_pre_migration(live, schema)
    try:
        first = upgrade_to_4_0_0(
            live, steps=("docs", "edge_vectors"), edge_dimension=DIM, schema=schema
        )
        after_first = _snapshot(live, schema)

        second = upgrade_to_4_0_0(
            live, steps=("docs", "edge_vectors"), edge_dimension=DIM, schema=schema
        )
        after_second = _snapshot(live, schema)

        assert after_second == after_first
        for name in ("docs", "edge_vectors"):
            assert not first[name].skipped, f"{name} had work to do the first time"
            assert second[name].skipped, f"{name} ran twice: {second[name]!r}"
            assert second[name].report is None
            assert "4.0.0" in second[name].skipped_because
    finally:
        _drop_schema(live, schema)


def test_full_migration_is_idempotent(live):
    """Every step, against the real install, twice.

    This install is already 4.0.0, so the two placements decline and the two steps
    that are re-runnable by construction do their work again: spec 227's embeddings
    pass reports the install's state, and the `^KG` re-key kills and rebuilds. Both
    must land in the same place the second time, and the re-key's second pass must
    find exactly the entries its first pass wrote — the proof that the rebuild reads
    its own layout rather than accumulating beside it.
    """
    from iris_vector_graph.migrations import UPGRADE_STEPS, upgrade_to_4_0_0

    first = upgrade_to_4_0_0(live)
    second = upgrade_to_4_0_0(live)

    assert [step.name for step in first.steps] == list(UPGRADE_STEPS)
    assert [step.name for step in second.steps] == list(UPGRADE_STEPS)

    for name in ("docs", "edge_vectors"):
        assert first[name].skipped, f"{name} is not in the 4.0.0 shape on this install"
        assert second[name].skipped

    emb_first = first["embeddings"].report
    emb_second = second["embeddings"].report
    assert emb_second.rows_placed == emb_first.rows_placed
    assert emb_second.rows_quarantined == emb_first.rows_quarantined
    assert emb_second.structural_blockers == emb_first.structural_blockers

    kg_first = first["kg_node_stores"].report
    kg_second = second["kg_node_stores"].report
    assert kg_second.entries_rebuilt == kg_first.entries_rebuilt
    assert kg_second.entries_dropped == sum(
        sum(per_graph.values()) for per_graph in kg_first.entries_rebuilt.values()
    )
