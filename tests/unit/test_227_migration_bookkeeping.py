"""Spec 227 — the migration accounts for every pre-upgrade row (T059, FR-026/028/030).

A migration that moves vectors is trusted or it is not run, and the only thing that
makes it checkable from outside is arithmetic: every row a 3.2.0 install held is now in
a graph's table or in the quarantine, and the report says which. So these tests are
mostly counting, deliberately.

The rule the counting protects is FR-028: **an unplaceable row is never given the
default graph.** Defaulting would be the silent version of this migration — every KNN
in the namespace would keep returning it, which is the leak spec 227 exists to close.
A row whose graph nobody can name is quarantined, by name, with the reason.

`dry_run=True` is tested against the same install as the wet run rather than on its own,
because its only useful property is agreeing with the run it predicts.
"""

import re

import pytest

from tests.unit.migration_fakes_227 import legacy_install, migration_conn
from iris_vector_graph.migrations import (
    AmbiguousVector,
    migrate_to_graph_scoped_embeddings,
)
from iris_vector_graph.routing import route_table_name

LEGACY = "kg_NodeEmbeddings"


def mixed_install(**kwargs):
    """Three vectors: one in a named graph, one in another, one in the default graph."""
    return legacy_install(
        node_graphs={"a": ["graph-a"], "b": ["graph-b"], "c": [""]},
        **kwargs,
    )


def run(registry, **kwargs):
    return migrate_to_graph_scoped_embeddings(migration_conn(registry), **kwargs)


# --- the arithmetic ------------------------------------------------------------------


def test_every_pre_upgrade_row_is_placed_or_quarantined():
    registry = mixed_install()
    before = len(registry.rows_in(LEGACY))

    report = run(registry)

    assert report.rows_accounted == before, (
        f"{before} rows went in, {report.rows_accounted} are accounted for: "
        f"placed={report.rows_placed} quarantined={report.rows_quarantined}"
    )


def test_a_named_graph_row_moves_to_that_graphs_route():
    registry = mixed_install()

    run(registry)

    route = route_table_name("graph-a", None)
    assert registry.placed_in(route) == [("graph-a", "a")]
    assert registry.rows_in(route)[0]["emb"] == "<a>", "the wrong vector was moved"


def test_a_default_graph_row_stays_in_the_legacy_table():
    """FR-015. The pair `('', None)` reads and writes `kg_NodeEmbeddings`, so moving
    the default graph's rows into a hashed route would make them unreachable by the
    read path the 4.0.0 engine uses for exactly that pair."""
    registry = mixed_install()

    run(registry)

    assert registry.placed_in(LEGACY) == [("", "c")]


def test_the_report_keys_a_placement_by_graph_and_table():
    registry = mixed_install()

    report = run(registry)

    assert report.rows_placed[("graph-a", route_table_name("graph-a", None))] == 1
    assert report.rows_placed[("graph-b", route_table_name("graph-b", None))] == 1
    assert report.rows_placed[("", LEGACY)] == 1


# --- nobody is defaulted -------------------------------------------------------------


def test_an_ambiguous_row_is_quarantined_rather_than_defaulted():
    """Two graphs hold a node with this ID, so no evidence names one (FR-028)."""
    registry = legacy_install(node_graphs={"shared": ["graph-a", "graph-b"]})

    report = run(registry)

    assert report.rows_quarantined == {"ambiguous_graph": 1}
    assert report.rows_placed == {}
    assert registry.placed_in(LEGACY) == []
    quarantined = registry.quarantined()
    assert [r["node_id"] for r in quarantined] == ["shared"]


def test_a_row_whose_node_no_graph_holds_is_quarantined_as_no_node():
    """A vector for a deleted node. `no_node` and `ambiguous_graph` are different
    operator problems — one is a dangling row to drop, the other is a graph to name."""
    registry = legacy_install(node_graphs={}, vectors=["orphan"])

    report = run(registry)

    assert report.rows_quarantined == {"no_node": 1}
    assert [r["reason"] for r in registry.quarantined()] == ["no_node"]


def test_no_resolver_and_a_declining_resolver_reach_the_same_placement():
    """FR-030. The difference is only in the reason recorded, never in where the row
    lands: a resolver that answers `None` has declined, not consented to a default."""
    without = legacy_install(node_graphs={"shared": ["graph-a", "graph-b"]})
    declining = legacy_install(node_graphs={"shared": ["graph-a", "graph-b"]})

    no_resolver = run(without)
    declined = run(declining, resolver=lambda vector: None)

    assert no_resolver.rows_placed == declined.rows_placed == {}
    assert sum(no_resolver.rows_quarantined.values()) == 1
    assert declined.rows_quarantined == {"resolver_declined": 1}


def test_a_resolver_naming_a_candidate_places_the_row():
    registry = legacy_install(node_graphs={"shared": ["graph-a", "graph-b"]})

    report = run(registry, resolver=lambda vector: "graph-b")

    route = route_table_name("graph-b", None)
    assert registry.placed_in(route) == [("graph-b", "shared")]
    assert report.rows_quarantined == {}
    assert report.rows_placed == {("graph-b", route): 1}


def test_a_resolver_is_offered_the_candidates_and_the_width():
    """It cannot answer without them, and `source_table` is what distinguishes the
    same node ID appearing in both legacy tables."""
    registry = legacy_install(node_graphs={"shared": ["graph-b", "graph-a"]})
    offered = []

    run(registry, resolver=lambda vector: offered.append(vector) or None)

    assert len(offered) == 1
    assert isinstance(offered[0], AmbiguousVector)
    assert offered[0].node_id == "shared"
    assert offered[0].source_table == LEGACY
    assert offered[0].candidate_graphs == ("graph-a", "graph-b")
    assert offered[0].dimension == 4


def test_a_resolver_naming_a_graph_that_does_not_hold_the_node_is_a_decline():
    """Honouring it would invent a membership no `nodes` row supports, and the
    route's FK to `(graph_id, node_id)` would refuse the INSERT anyway."""
    registry = legacy_install(node_graphs={"shared": ["graph-a", "graph-b"]})

    report = run(registry, resolver=lambda vector: "graph-elsewhere")

    assert report.rows_quarantined == {"resolver_declined": 1}
    assert registry.placed_in(route_table_name("graph-elsewhere", None)) == []


def test_an_unplaced_row_is_named_not_just_counted():
    """US5-2: "reported by count and ID". A count tells an operator something is wrong
    and nothing about which row to go and look at."""
    registry = legacy_install(
        node_graphs={"shared": ["graph-a", "graph-b"], "fine": ["graph-a"]}
    )

    report = run(registry)

    assert report.quarantined_ids == ["shared"]


def test_the_quarantine_row_carries_what_a_later_placement_needs():
    """`place_quarantined` has to check the vector's width against the route it is
    being placed into, so the width travels with the row rather than being re-derived
    from a table that no longer exists in 3.2.0's shape."""
    registry = legacy_install(node_graphs={}, vectors=["orphan"], dimension=8)

    run(registry)

    row = registry.quarantined()[0]
    assert row["source_table"] == LEGACY
    assert row["dimension"] == 8
    assert row["dtype"] == "DOUBLE"
    assert row["emb"] == "<orphan>"
    assert "graph_id" not in row, "the quarantine must not name a graph (FR-028)"


# --- a single-graph install is not a guess -------------------------------------------


def test_a_single_graph_install_places_every_vector_in_that_graph():
    """US5-1 and FR-027. One graph in the install means there is nothing to resolve:
    every node in it is in that graph, so this is evidence, not a default."""
    registry = legacy_install(node_graphs={"a": ["g1"], "b": ["g1"], "c": ["g1"]})

    report = run(registry)

    assert sorted(registry.placed_in(LEGACY)) == [("g1", "a"), ("g1", "b"), ("g1", "c")]
    assert report.rows_quarantined == {}
    assert report.rows_accounted == 3


def test_a_single_graph_install_rewrites_its_registry_row_instead_of_copying_rows():
    """T066/FR-031. The table already holds exactly that graph's vectors, so naming
    the graph in the registry row is the whole migration for it — creating a hashed
    route and copying every vector across would be a rewrite of the data to reach the
    same state."""
    registry = legacy_install(node_graphs={"a": ["g1"], "b": ["g1"]})

    run(registry)

    assert registry.row_for(LEGACY, "g1") is not None, (
        f"the registry still describes the legacy table at '': {registry.rows}"
    )
    assert registry.row_for(LEGACY, "") is None
    assert route_table_name("g1", None) not in registry.created_tables


def test_a_single_graph_install_still_quarantines_a_node_less_row():
    """Its graph is not ambiguous and its node is still missing."""
    registry = legacy_install(
        node_graphs={"a": ["g1"]}, vectors=["a", "orphan"]
    )

    report = run(registry)

    assert report.rows_quarantined == {"no_node": 1}
    assert registry.placed_in(LEGACY) == [("g1", "a")]


# --- dry run --------------------------------------------------------------------------


def test_dry_run_reports_the_same_numbers_as_the_run_it_predicts():
    predicted = run(mixed_install(), dry_run=True)
    performed = run(mixed_install())

    assert predicted.rows_placed == performed.rows_placed
    assert predicted.rows_quarantined == performed.rows_quarantined
    assert predicted.quarantined_ids == performed.quarantined_ids
    assert predicted.tables_created == performed.tables_created


def test_dry_run_moves_no_row_and_creates_no_table():
    registry = mixed_install()

    report = run(registry, dry_run=True)

    assert registry.moves == 0, "a dry run moved a row"
    assert registry.created_tables == []
    assert registry.dropped == []
    assert registry.renamed == []
    assert registry.quarantined() == []
    assert registry.rows_in(LEGACY), "the source table was drained by a dry run"
    assert report.rows_accounted == 3, "it predicted nothing while writing nothing"


def test_dry_run_leaves_the_registry_alone():
    registry = legacy_install(node_graphs={"a": ["g1"], "b": ["g1"]})

    run(registry, dry_run=True)

    assert registry.row_for(LEGACY, "") is not None
    assert registry.row_for(LEGACY, "g1") is None


# --- how the rows travel -------------------------------------------------------------


def test_a_vector_is_never_read_into_python():
    """A `VECTOR(DOUBLE, 768)` fetched into Python and re-bound is reshaped by the
    driver's idea of the value, and ADR-0005 is that no code path may reshape a
    stored vector. `INSERT ... SELECT` keeps every vector inside IRIS.

    The registry's own inserts are excluded: a registry row is metadata about a table,
    carries no `emb` column, and is bound from values by design (spec 226).
    """
    registry = mixed_install()

    run(registry)

    for sql, _params in registry.statements:
        upper = sql.upper()
        if upper.startswith("SELECT") and " EMB" in upper.replace(",", " "):
            pytest.fail(f"a vector was selected into Python: {sql}")
    moves = [
        s
        for s, _ in registry.statements
        if s.upper().startswith("INSERT INTO")
        and "EMBEDDING_REGISTRY" not in s.upper()
    ]
    assert moves and all(" SELECT " in s.upper() for s in moves), (
        "rows must move by INSERT ... SELECT, not by binding values back: " + str(moves)
    )


def test_the_legacy_table_ends_up_in_the_400_shape():
    """The reshape is the reason the migration cannot be an `UPDATE`: 3.2.0 declares
    `id VARCHAR PRIMARY KEY` and 4.0.0 needs `(graph_id, node_id)`, which no ALTER
    reaches from a VARCHAR primary key."""
    registry = mixed_install()

    run(registry)

    assert "graph_id" in registry.columns[LEGACY]
    assert "node_id" in registry.columns[LEGACY]
    assert LEGACY in registry.dropped, "the 3.2.0 table was left in place"
    assert any(new == LEGACY for _old, new in registry.renamed), (
        f"nothing was renamed into {LEGACY}: {registry.renamed}"
    )


def test_the_rebuilt_default_route_references_nodes():
    """FR-008: the table the upgrade builds points at `nodes` the way a route does.

    Every generated route declares `fk_{route} FOREIGN KEY (graph_id, node_id)`, and the
    legacy tables are the default route. Without the same reference the default route
    would accept an embedding for a node that does not exist, and the node behind one
    could be deleted — which is what 3.2.0's `fk_emb_node` prevented.
    """
    registry = mixed_install()

    run(registry)

    creates = [s for s in registry.ddl if s.upper().startswith("CREATE TABLE")]
    assert creates
    for sql in creates:
        assert re.search(
            r"CONSTRAINT\s+\S+\s+FOREIGN\s+KEY\s*\(\s*graph_id\s*,\s*node_id\s*\)\s+"
            r"REFERENCES\s+\S*nodes\s*\(\s*graph_id\s*,\s*node_id\s*\)",
            sql,
            re.IGNORECASE,
        ), sql
    staged = [s for s in creates if LEGACY in s]
    assert staged and "fk_emb_node" in staged[0], (
        "the default route keeps 3.2.0's constraint name, re-pointed: " + str(staged)
    )


def test_a_refused_rekey_reshapes_nothing():
    """A blocked re-key leaves `nodes` keyed by node_id alone.

    The composite reference the rebuilt table declares has nothing to point at then
    (SQLCODE -121), and reshaping anyway would leave a default route with no reference
    while every generated route has one. So the pass writes nothing and says why.
    """
    registry = legacy_install(
        node_graphs={"a": ["graph-a"], "b": ["graph-b"]},
        labels=[{"s": "ghost", "label": "Thing"}],
    )

    report = run(registry)

    assert report.structural_blockers == {"rdf_labels": ["ghost"]}
    assert registry.moves == 0, "a blocked pass moved a row"
    assert registry.created_tables == []
    assert registry.dropped == []
    assert registry.renamed == []
    assert registry.rows_in(LEGACY), "the source table was drained by a blocked pass"
