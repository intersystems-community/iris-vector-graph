"""The 4.0.0 structural re-key, against what a real 3.2.0-era install held.

Three defects found migrating an install of ~1.6M props and ~243k labels:

* **It could not resume.** ``_rekey_children`` returned early as soon as
  ``rdf_labels.graph_id`` existed — the *first* side effect of step 3. A pass killed
  anywhere after that (the ``rdf_props`` re-key, a primary-key swap, a step-4 foreign
  key) left a re-run that did nothing and reported success.
* **Duplicate rows blocked the composite primary keys.** The install carried 18
  duplicate ``rdf_labels`` rows and 305 duplicate ``rdf_props`` rows. Exact copies are
  collapsed to the lowest ``%ID``; a prop whose copies *disagree on the value* is not a
  duplicate but a conflict, and the re-key refuses and names it rather than keeping
  one value at random.
* **A legacy ``PRIMARY KEY (node_id)`` survived.** Step 2 dropped ``uq_nodes_nodeid``
  by name. A 2.x-born ``nodes`` table carries its key as ``NODES_PKEY1`` (the name IRIS
  gives an inline ``node_id ... PRIMARY KEY``), which stayed — so the catalog looked
  4.0.0 while a node ID still could not be written into a second graph (-119).

The fake in ``rekey_fakes.py`` holds the catalog and the rows and refuses what IRIS
refuses, so these assertions are about the state the re-key leaves, not about how its
statements are spelled.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator
from iris_vector_graph.schema import GraphSchema
from tests.unit.rekey_fakes import Killed, RekeyDB

G = "g1"


def migrator(db: RekeyDB, *, dry_run: bool = False) -> _Migrator:
    m = _Migrator(db, resolver=None, dry_run=dry_run)
    m.engine = SimpleNamespace(_t=lambda name: f"Graph_KG.{name}", _schema_prefix="Graph_KG")
    return m


def rekey(db: RekeyDB, **kwargs):
    return migrator(db, **kwargs).rekey(db.cursor())


def install(**kwargs) -> RekeyDB:
    """A 3.x install: every node in the default graph ('' — IRIS stores it as $c(0))."""
    kwargs.setdefault("nodes", {"a": [""], "b": [""], "c": [""]})
    kwargs.setdefault("labels", [("a", "Patient"), ("b", "Patient")])
    kwargs.setdefault("props", [("a", "name", "Ann"), ("b", "name", "Bob")])
    return RekeyDB(**kwargs)


# --- BUG 2: a killed pass resumes -----------------------------------------------------


@pytest.mark.parametrize(
    "killed_at",
    [
        "UPDATE Graph_KG.rdf_labels",  # right after rdf_labels.graph_id was added
        "ALTER TABLE Graph_KG.rdf_props ADD COLUMN graph_id",  # the rdf_props re-key
        "ALTER TABLE Graph_KG.rdf_props ADD CONSTRAINT pk_props",  # a PK swap
        "ADD CONSTRAINT fk_edges_dest",  # a step-4 foreign key
    ],
)
def test_a_pass_killed_after_labels_gained_graph_id_resumes_to_the_full_rekey(killed_at):
    db = install(fail_once_on=killed_at)
    with pytest.raises(Killed):
        rekey(db)
    assert "graph_id" in db.columns["rdf_labels"], "the kill landed before the tripwire"

    outcome = rekey(db)

    assert not outcome.refused
    assert db.is_fully_rekeyed() == []


def test_a_resumed_pass_is_the_same_install_as_a_clean_one():
    killed = install(
        labels=[("a", "Patient"), ("a", "Patient")],
        props=[("a", "name", "Ann"), ("a", "name", "Ann")],
        fail_once_on="ALTER TABLE Graph_KG.rdf_props ADD COLUMN graph_id",
    )
    with pytest.raises(Killed):
        rekey(killed)
    rekey(killed)

    clean = install(
        labels=[("a", "Patient"), ("a", "Patient")],
        props=[("a", "name", "Ann"), ("a", "name", "Ann")],
    )
    rekey(clean)

    assert killed.constraints == clean.constraints
    assert killed.rows == clean.rows
    assert killed.columns == clean.columns


def test_a_completed_rekey_is_not_redone():
    """Re-running a finished re-key would drop and re-add both composite primary keys
    and every structural foreign key — on 1.6M rows — to arrive where it started."""
    db = install()
    rekey(db)
    db.statements.clear()

    outcome = rekey(db)

    assert db.writes() == [], db.writes()
    assert outcome.duplicates_removed == {}


def test_the_backfill_claims_only_rows_with_no_graph_yet():
    """The backfill runs again on every resume, so it must be a no-op on rows an
    earlier pass already placed."""
    for sql in GraphSchema.get_graph_scope_migration_sql():
        if sql.upper().startswith("UPDATE"):
            assert re.search(r"WHERE\s+c\.graph_id\s+IS\s+NULL\s+AND", sql), sql


# --- BUG 3: duplicate label and prop rows ---------------------------------------------


def test_exact_duplicate_labels_collapse_to_the_lowest_id():
    db = install(labels=[("a", "Patient"), ("a", "Patient"), ("a", "Patient"), ("b", "X")])

    outcome = rekey(db)

    assert [(r["%ID"], r["s"], r["label"]) for r in db.rows["rdf_labels"]] == [
        (1, "a", "Patient"),
        (4, "b", "X"),
    ]
    assert outcome.duplicates_removed == {"rdf_labels": 2}
    assert db.is_fully_rekeyed() == []


def test_exact_duplicate_props_collapse_including_null_values():
    db = install(
        props=[
            ("a", "name", "Ann"),
            ("a", "name", "Ann"),
            ("a", "nick", None),
            ("a", "nick", None),
            ("b", "name", "Bob"),
        ]
    )

    outcome = rekey(db)

    kept = [(r["%ID"], r["s"], r["key"], r["val"]) for r in db.rows["rdf_props"]]
    assert kept == [(3, "a", "name", "Ann"), (5, "a", "nick", None), (7, "b", "name", "Bob")]
    assert outcome.duplicates_removed == {"rdf_props": 2}
    assert db.is_fully_rekeyed() == []


@pytest.mark.parametrize(
    "first,second",
    [("Ann", "Anne"), ("Ann", None), ("", None)],
    ids=["different-values", "value-vs-null", "empty-vs-null"],
)
def test_props_that_disagree_on_the_value_refuse_the_rekey(first, second):
    db = install(props=[("a", "name", first), ("a", "name", second), ("b", "name", "Bob")])
    before = [dict(r) for r in db.rows["rdf_props"]]

    outcome = rekey(db)

    assert outcome.refused
    assert outcome.key_conflicts == {"rdf_props": [("a", "name")]}
    assert db.writes() == [], "a refused re-key wrote something"
    assert db.rows["rdf_props"] == before, "a conflicting value was discarded"


def test_a_stream_valued_prop_column_collapses_nothing_it_cannot_compare():
    """If `val` were a stream, `=` could not prove two values equal — so every
    duplicate key is a conflict, and none is collapsed on a guess."""
    db = install(props=[("a", "name", "Ann"), ("a", "name", "Ann")], props_val_type="stream")

    outcome = rekey(db)

    assert outcome.refused
    assert outcome.key_conflicts == {"rdf_props": [("a", "name")]}
    assert len(db.rows["rdf_props"]) == 2


def test_a_dry_run_predicts_the_duplicates_and_writes_nothing():
    db = install(labels=[("a", "Patient"), ("a", "Patient")])

    outcome = rekey(db, dry_run=True)

    assert outcome.duplicates_removed == {"rdf_labels": 1}
    assert db.writes() == []


def test_the_dedupe_runs_after_not_null_and_before_the_primary_key():
    statements = GraphSchema.get_graph_scope_migration_sql()

    def index(pattern):
        return next(i for i, s in enumerate(statements) if re.search(pattern, s))

    for table, pk in (("rdf_labels", "pk_labels"), ("rdf_props", "pk_props")):
        delete = index(rf"DELETE FROM Graph_KG\.{table}\b")
        assert index(rf"{table} ALTER COLUMN graph_id NOT NULL") < delete
        assert delete < index(rf"{table} ADD CONSTRAINT {pk}\b")
        assert "MIN(g.%ID)" in statements[delete], "keep the lowest %ID"
    props_delete = statements[index(r"DELETE FROM Graph_KG\.rdf_props\b")]
    assert "k.val = d.val" in props_delete, "only exact copies are collapsed"


# --- BUG 4: a legacy key over node_id alone -------------------------------------------


def test_a_legacy_primary_key_on_node_id_is_replaced():
    db = install(node_keys={"NODES_PKEY1": ("PRIMARY KEY", ["node_id"])})

    outcome = rekey(db)

    assert outcome.legacy_node_keys == ["NODES_PKEY1"]
    assert db.node_id_only_keys() == []
    assert db.keys_of("nodes")["pk_nodes_graph"] == ("PRIMARY KEY", ["node_id", "graph_id"])
    assert db.is_fully_rekeyed() == []
    db.insert_node("a", "g2")  # the same ID in a second graph: -119 before the fix


def test_every_unique_key_on_node_id_alone_is_dropped_whatever_its_name():
    db = install(
        node_keys={
            "pk_nodes_graph": ("PRIMARY KEY", ["node_id", "graph_id"]),
            "uq_nodes_nodeid": ("UNIQUE", ["node_id"]),
            "ux_nodes_legacy": ("UNIQUE", ["node_id"]),
        }
    )

    outcome = rekey(db)

    assert sorted(outcome.legacy_node_keys) == ["uq_nodes_nodeid", "ux_nodes_legacy"]
    assert db.node_id_only_keys() == []
    assert db.is_fully_rekeyed() == []
    assert db.can_insert_node("a", "g2")


def test_a_legacy_key_that_is_the_idkey_refuses_the_rekey():
    """An IDKEY cannot be dropped by `ALTER TABLE`; the table would have to be
    rebuilt. Better refused before anything is written than failed half way."""
    db = install(idkeys=("NODES_PKEY1",))

    outcome = rekey(db)

    assert outcome.refused
    assert "NODES_PKEY1" in outcome.node_key_blockers
    assert db.writes() == []


def test_an_unknown_foreign_key_holding_the_legacy_key_refuses_the_rekey():
    """The re-key re-points the five references it knows. Anything else pointing at a
    node_id-only key would have to be dropped with no way to put it back."""
    db = install(extra_fks={("fhir_bridges", "fk_bridge_node"): (["kg_node_id"], "NODES_PKEY1")})

    outcome = rekey(db)

    assert outcome.refused
    assert "NODES_PKEY1" in outcome.node_key_blockers
    assert "fk_bridge_node" in outcome.node_key_blockers["NODES_PKEY1"]
    assert db.writes() == []


def test_the_statement_list_drops_a_named_legacy_key_between_the_fks_and_the_new_pk():
    statements = GraphSchema.get_graph_scope_migration_sql(legacy_node_keys=["NODES_PKEY1"])

    def index(pattern):
        return next(i for i, s in enumerate(statements) if re.search(pattern, s))

    drop = index(r"nodes DROP CONSTRAINT NODES_PKEY1$")
    for fk in ("fk_labels_node", "fk_edges_source", "fk_edges_dest"):
        assert index(rf"DROP CONSTRAINT {fk}$") < drop
    add = index(r"nodes ADD CONSTRAINT pk_nodes_graph PRIMARY KEY \(node_id, graph_id\)")
    assert drop < add


def test_the_replacement_primary_key_matches_a_fresh_install():
    ddl = GraphSchema.get_base_schema_sql(embedding_dimension=8)
    assert "CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id)" in ddl
    assert any(
        s.endswith("ADD CONSTRAINT pk_nodes_graph PRIMARY KEY (node_id, graph_id)")
        for s in GraphSchema.get_graph_scope_migration_sql()
    )


def test_a_3_2_0_nodes_table_keeps_its_composite_primary_key():
    """3.2.0 already declared `pk_nodes_graph (node_id, graph_id)`: it is not a
    node_id-only key and must survive; the re-add is refused as already there."""
    db = install(
        node_keys={
            "pk_nodes_graph": ("PRIMARY KEY", ["node_id", "graph_id"]),
            "uq_nodes_nodeid": ("UNIQUE", ["node_id"]),
        }
    )

    rekey(db)

    assert db.is_fully_rekeyed() == []
