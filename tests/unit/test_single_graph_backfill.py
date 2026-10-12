"""The graph_id backfill of rdf_labels / rdf_props, when nodes holds one graph.

The 4.0.0 re-key gives each child row its node's graph with one ``UPDATE`` per table,
whose ``SET`` and ``WHERE`` each carry a correlated subquery over ``nodes``. When
``nodes`` holds exactly one graph every such row maps to that graph, so a single
constant ``UPDATE`` per table places the same rows. The shortcut applies only when
``nodes`` has one distinct, non-NULL graph_id and no child row names a node missing
from ``nodes``; otherwise the statement the list carries runs unchanged.

These tests drive :meth:`_Migrator.rekey` over :class:`RekeyDB` (the catalog-and-rows
fake the re-key tests use) with a cursor that also answers the two reads the shortcut
makes and the constant ``UPDATE`` it sends. The equivalence tests run each install
twice, once with the shortcut and once with it forced off, and compare what the
re-key leaves behind.
"""

from __future__ import annotations

import copy
import re
from types import SimpleNamespace

import pytest

from iris_vector_graph.migrations.graph_scoped_embeddings import _Migrator
from tests.unit.rekey_fakes import RekeyCursor, RekeyDB

G = "g1"

CORRELATED = "SET c.graph_id = (SELECT MIN(n.graph_id)"


class SingleGraphDB(RekeyDB):
    def cursor(self) -> "SingleGraphCursor":
        return SingleGraphCursor(self)


class SingleGraphCursor(RekeyCursor):
    """RekeyCursor plus the constant UPDATE the shortcut sends (its reads are in the base fake)."""

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        db = self.db
        m = re.fullmatch(
            r"UPDATE Graph_KG\.(\w+) SET graph_id = '((?:[^']|'')*)' WHERE graph_id IS NULL",
            flat,
        )
        if m:
            db.statements.append(flat)
            table, value = m.group(1), m.group(2).replace("''", "'")
            n = 0
            for r in db.rows[table]:
                if r.get("graph_id") is None:
                    r["graph_id"] = value
                    n += 1
            self._rows, self.rowcount = [], n
            return self
        return super().execute(sql, params)


def migrator(db: RekeyDB) -> _Migrator:
    m = _Migrator(db, resolver=None, dry_run=False)
    m.engine = SimpleNamespace(_t=lambda name: f"Graph_KG.{name}", _schema_prefix="Graph_KG")
    return m


def rekey(db: RekeyDB):
    return migrator(db).rekey(db.cursor())


def one_graph_install(graph: str = G, **kwargs) -> SingleGraphDB:
    """A 3.x install whose every node sits in one graph."""
    kwargs.setdefault("nodes", {"a": [graph], "b": [graph], "c": [graph]})
    kwargs.setdefault("labels", [("a", "Patient"), ("b", "Patient"), ("b", "Visit")])
    kwargs.setdefault("props", [("a", "name", "Ann"), ("b", "name", "Bob")])
    return SingleGraphDB(**kwargs)


def backfills(db: RekeyDB) -> list:
    """Every UPDATE the re-key sent that touches a child table's graph_id."""
    return [s for s in db.statements if s.startswith("UPDATE Graph_KG.rdf_")]


def constant_form(table: str, graph: str) -> str:
    literal = "'" + graph.replace("'", "''") + "'"
    return f"UPDATE Graph_KG.{table} SET graph_id = {literal} WHERE graph_id IS NULL"


# --- the shortcut is taken when it is equivalent -------------------------------------


def test_one_graph_backfills_each_child_table_with_one_constant_update():
    db = one_graph_install()

    rekey(db)

    assert backfills(db) == [
        constant_form("rdf_labels", G),
        constant_form("rdf_props", G),
    ]
    assert not any(CORRELATED in s for s in db.statements)


def test_one_graph_places_every_child_row_in_that_graph():
    db = one_graph_install()

    outcome = rekey(db)

    assert not outcome.refused
    assert {r["graph_id"] for r in db.rows["rdf_labels"]} == {G}
    assert {r["graph_id"] for r in db.rows["rdf_props"]} == {G}
    assert db.is_fully_rekeyed() == []


def test_default_graph_empty_string_is_written_as_an_empty_string_literal():
    # The default graph is '' (DEFAULT ''), so the shortcut must write '' and not NULL.
    db = one_graph_install(graph="")

    rekey(db)

    assert backfills(db)[0] == constant_form("rdf_labels", "")
    assert {r["graph_id"] for r in db.rows["rdf_labels"]} == {""}
    assert db.is_fully_rekeyed() == []


def test_a_quote_in_the_graph_id_is_escaped_in_the_literal():
    graph = "it's"
    db = one_graph_install(graph=graph)

    rekey(db)

    assert backfills(db)[0] == constant_form("rdf_labels", graph)
    assert {r["graph_id"] for r in db.rows["rdf_labels"]} == {graph}


# --- the shortcut falls back when it is not equivalent --------------------------------


def test_two_graphs_fall_back_to_the_correlated_backfill():
    db = SingleGraphDB(
        nodes={"a": ["g1"], "b": ["g2"]},
        labels=[("a", "Patient"), ("b", "Patient")],
        props=[("a", "name", "Ann"), ("b", "name", "Bob")],
    )

    outcome = rekey(db)

    assert not outcome.refused
    assert len(backfills(db)) == 2
    assert all(CORRELATED in s for s in backfills(db))
    assert sorted((r["s"], r["graph_id"]) for r in db.rows["rdf_labels"]) == [
        ("a", "g1"),
        ("b", "g2"),
    ]


def test_a_node_id_in_two_graphs_falls_back_and_the_second_graph_is_not_guessed():
    # Two graphs, so the fallback runs; the node in both is refused as a blocker, so
    # nothing is written at all.
    db = SingleGraphDB(
        nodes={"a": ["g1", "g2"]},
        labels=[("a", "Patient")],
    )

    outcome = rekey(db)

    assert outcome.refused
    assert backfills(db) == []


def test_an_orphan_child_row_falls_back_even_though_nodes_is_one_graph():
    # A resumed pass: rdf_labels already has graph_id, the orphan 'zz' is placed, and
    # the NULL row 'a' is still to backfill. The orphan is not a blocker here, so the
    # shortcut's own check has to refuse the constant form.
    db = one_graph_install(labels=[("a", "Patient"), ("zz", "Ghost")], props=[])
    db.columns["rdf_labels"]["graph_id"] = "YES"
    for r in db.rows["rdf_labels"]:
        r["graph_id"] = G if r["s"] == "zz" else None

    outcome = rekey(db)

    assert not outcome.refused
    assert len(backfills(db)) == 2
    assert CORRELATED in backfills(db)[0]
    assert sorted(r["s"] for r in db.rows["rdf_labels"]) == ["a", "zz"]


def test_an_empty_nodes_table_falls_back():
    db = SingleGraphDB(nodes={}, labels=[], props=[])

    rekey(db)

    assert all(CORRELATED in s for s in backfills(db))


def test_a_node_with_a_null_graph_id_falls_back():
    db = one_graph_install()
    db.node_rows.append({"node_id": "x", "graph_id": None})

    rekey(db)

    assert all(CORRELATED in s for s in backfills(db))


# --- the two paths leave the same install -----------------------------------------


def _state(db: RekeyDB) -> tuple:
    return (
        copy.deepcopy(db.rows),
        copy.deepcopy(db.columns),
        copy.deepcopy(db.constraints),
        copy.deepcopy(db.node_rows),
    )


def _run(db: RekeyDB, *, shortcut: bool, monkeypatch) -> tuple:
    if not shortcut:
        monkeypatch.setattr(
            _Migrator, "_single_graph", lambda self, cursor, table: None, raising=False
        )
    outcome = rekey(db)
    return _state(db), outcome.refused, db.is_fully_rekeyed()


SCENARIOS = {
    "named graph": lambda: one_graph_install(),
    "default graph": lambda: one_graph_install(graph=""),
    "resumed with some rows placed": lambda: _resumed(),
    "duplicate label rows": lambda: one_graph_install(
        labels=[("a", "Patient"), ("a", "Patient"), ("b", "Visit")]
    ),
    "props with duplicate copies": lambda: one_graph_install(
        props=[("a", "name", "Ann"), ("a", "name", "Ann"), ("b", "name", "Bob")]
    ),
    "two graphs": lambda: SingleGraphDB(
        nodes={"a": ["g1"], "b": ["g2"]},
        labels=[("a", "Patient"), ("b", "Patient")],
        props=[("a", "name", "Ann")],
    ),
    "orphan row": lambda: one_graph_install(labels=[("a", "Patient"), ("zz", "Ghost")]),
}


def _resumed() -> SingleGraphDB:
    db = one_graph_install()
    db.columns["rdf_labels"]["graph_id"] = "YES"
    for i, r in enumerate(db.rows["rdf_labels"]):
        r["graph_id"] = G if i == 0 else None
    return db


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_shortcut_and_correlated_backfill_leave_the_same_install(scenario, monkeypatch):
    fast = _run(SCENARIOS[scenario](), shortcut=True, monkeypatch=monkeypatch)
    monkeypatch.undo()
    slow = _run(SCENARIOS[scenario](), shortcut=False, monkeypatch=monkeypatch)

    assert fast == slow
