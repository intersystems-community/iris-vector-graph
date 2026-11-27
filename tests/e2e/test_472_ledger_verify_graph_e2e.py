"""4.7.2: verify() reads nodes, labels and properties by graph, and a qualifier's number the way genesis spells it.

Live, against the tables: each case builds a state, enables the ledger (genesis), changes the tables outside the ledger
or commits through it, and asserts what `verify()` says and what the tables hold after every call. The same cases
passed "equal" on 4.7.1 where the states differed (verify read `nodes`, `rdf_labels` and `rdf_props` without `graph_id`,
and the replay held one node per id), and a graph bulk-loaded with a fractional qualifier diverged right after
`enable()` with nothing written in between.

The ledger writes the default graph's rows (R281, R290) and records a named graph's node with that graph, in the
genesis and in an adoption. `verify(adopt=True)` records a difference and never touches the tables.

The namespace is `IVG_NAMESPACE` (an IVG470*/IVG463*/IVG460*/IVG262* scratch namespace with IVG set up). The ledger is
reset before and after every case (`Graph.KG.Ledger.PurgeAll`); the ids are all `v472*`.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

G = "g472"
PREFIX = "v472"


def _like(sql_col: str) -> str:
    return f"{sql_col} LIKE '{PREFIX}%'"


class World:
    def __init__(self, engine, conn, native, schema):
        self.engine, self.conn, self.native, self.schema = engine, conn, native, schema

    def sql(self, sql, params=None):
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params or [])
            try:
                return [tuple(r) for r in cur.fetchall()]
            except Exception:
                return []
        finally:
            cur.close()

    def tables(self):
        """Every row of the node, label, property and edge tables the cases own, graph included."""
        s = self.schema

        def q(sql):
            return sorted(tuple("" if v is None else str(v) for v in r) for r in self.sql(sql))

        return {
            "nodes": q(f"SELECT node_id, graph_id FROM {s}.nodes WHERE {_like('node_id')}"),
            "labels": q(f"SELECT s, label, graph_id FROM {s}.rdf_labels WHERE {_like('s')}"),
            "props": q(f'SELECT s, "key", val, graph_id FROM {s}.rdf_props WHERE {_like("s")} AND "key" <> \'id\''),
            "edges": q(f"SELECT s, p, o_id, graph_id, qualifiers FROM {s}.rdf_edges WHERE {_like('s')}"),
        }

    def records(self, seq_from=1, seq_to=1000):
        """(seq, op, kind, id, attr, graph) of the ledger's records."""
        rows = self.sql(f"SELECT * FROM {self.schema}.ledger_records(?, ?, 1, 5000)", [seq_from, seq_to])
        return [
            (r[0], r[2], r[3], r[4], r[5] or "", (r[10] or "").replace("\x00", ""))
            for r in rows
            if r[3] != "node" or str(r[4]).startswith(PREFIX)  # a scratch namespace may hold other ids
        ]

    def verify(self, **kw):
        return self.engine.ledger.verify(**kw)

    def wipe(self):
        s = self.schema
        for nid, graph in self.sql(f"SELECT node_id, graph_id FROM {s}.nodes WHERE {_like('node_id')}"):
            self.engine.delete_node(nid, graph=graph if graph not in (None, "", "\x00") else None)
        for table, col in (("rdf_edges", "s"), ("rdf_props", "s"), ("rdf_labels", "s"), ("nodes", "node_id")):
            self.sql(f"DELETE FROM {s}.{table} WHERE {_like(col)}")
        self.conn.commit()

    def reset(self):
        self.native.classMethodValue("Graph.KG.Ledger", "PurgeAll")
        self.engine.ledger._refresh_guard()
        self.wipe()


def diffs(report):
    """What a verification reports: (kind, id, graph, attr, before, after), nodes only unless asked."""
    return [(d.entity_kind, d.entity_id, d.graph, d.attr, d.before, d.after) for d in report.differences]


@pytest.fixture(scope="module")
def world():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    native = iris.createIRIS(conn)
    schema = native.get("^IVG.Deploy", "schema") or "Graph_KG"
    engine = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    w = World(engine, conn, native, schema)
    w.reset()
    yield w
    try:
        w.reset()
    finally:
        conn.close()


@pytest.fixture()
def w(world):
    world.reset()
    yield world
    world.reset()


# --------------------------------------------------------------------------- #
# node only in a named graph
# --------------------------------------------------------------------------- #


def test_1_a_node_only_a_named_graph_holds_is_recorded_in_that_graph_and_verifies_equal(w):
    w.engine.create_node("v472a", labels=["L"], properties={"k": "v"}, graph=G)
    before = w.tables()
    assert before["nodes"] == [("v472a", G)]
    w.engine.ledger.enable()
    assert w.tables() == before
    assert sorted((op, i, attr, g) for _, op, _, i, attr, g in w.records()) == [
        ("add_label", "v472a", "L", G),
        ("create_node", "v472a", "", G),
        ("set_prop", "v472a", "k", G),
    ]
    report = w.verify()
    assert (report.result, report.differences) == ("equal", [])


def test_2_a_named_node_written_outside_the_ledger_diverges_and_adoption_records_it_in_its_graph(w):
    w.engine.ledger.enable()
    assert w.verify().result == "equal"
    w.engine.create_node("v472a", labels=["L"], properties={"k": "v"}, graph=G)  # strict mode is off
    before = w.tables()
    assert before["nodes"] == [("v472a", G)]
    report = w.verify()
    assert report.result == "diverges" and report.classification == "unrecorded_writes"
    assert diffs(report) == [
        ("node", "v472a", G, "", None, ""),
        ("node", "v472a", G, "label:L", None, ""),
        ("node", "v472a", G, "prop:k", None, "v"),
    ]
    assert w.tables() == before  # verify wrote nothing
    adopted = w.verify(adopt=True)
    assert adopted.result == "diverges" and adopted.adoption_revision is not None
    assert w.tables() == before  # adoption records, it does not write the tables
    seq = adopted.adoption_revision.seq
    assert sorted((op, i, attr, g) for _, op, _, i, attr, g in w.records(seq, seq)) == [
        ("add_label", "v472a", "L", G),
        ("create_node", "v472a", "", G),
        ("set_prop", "v472a", "k", G),
    ]
    again = w.verify()
    assert (again.result, again.differences) == ("equal", [])


# --------------------------------------------------------------------------- #
# the same id in two graphs
# --------------------------------------------------------------------------- #


def _both(w):
    w.engine.create_node("v472b", labels=["L"], properties={"k": "default"})
    w.engine.create_node("v472b", labels=["L", "M"], properties={"k": "named", "j": "only-named"}, graph=G)


def test_3_the_same_id_in_two_graphs_is_two_nodes_to_verify(w):
    _both(w)
    w.engine.ledger.enable()
    assert w.verify().result == "equal"
    # a label only the named copy holds, added outside the ledger to the DEFAULT copy
    w.sql(f"INSERT INTO {w.schema}.rdf_labels (s, label, graph_id) VALUES ('v472b', 'M', '')")
    w.conn.commit()
    report = w.verify()
    assert report.result == "diverges"
    assert diffs(report) == [("node", "v472b", None, "label:M", None, "")]
    w.sql(f"DELETE FROM {w.schema}.rdf_labels WHERE s = 'v472b' AND label = 'M' AND COALESCE(graph_id, '') = ''")
    w.conn.commit()
    assert w.verify().result == "equal"
    # a property value changed in the named copy only
    w.sql(f"UPDATE {w.schema}.rdf_props SET val = 'changed' WHERE s = 'v472b' AND \"key\" = 'k' AND graph_id = ?", [G])
    w.conn.commit()
    assert diffs(w.verify()) == [("node", "v472b", G, "prop:k", "named", "changed")]


def test_4_the_default_copy_deleted_outside_the_ledger_is_seen_while_the_named_copy_stays(w):
    _both(w)
    w.engine.ledger.enable()
    assert w.verify().result == "equal"
    assert w.engine.delete_node("v472b", graph=None)
    after_delete = w.tables()
    assert after_delete["nodes"] == [("v472b", G)]
    assert [r for r in after_delete["labels"]] == [("v472b", "L", G), ("v472b", "M", G)]
    report = w.verify()
    assert report.result == "diverges"
    assert diffs(report) == [("node", "v472b", None, "", "", None)]
    adopted = w.verify(adopt=True)
    assert w.tables() == after_delete
    assert [(op, g) for _, op, _, i, _, g in w.records(adopted.adoption_revision.seq, adopted.adoption_revision.seq)] == [
        ("delete_node", "")
    ]
    assert w.verify().result == "equal"


def test_5_the_named_copy_deleted_outside_the_ledger_is_seen_while_the_default_copy_stays(w):
    _both(w)
    w.engine.ledger.enable()
    assert w.engine.delete_node("v472b", graph=G)
    assert w.tables()["nodes"] == [("v472b", "")]
    report = w.verify()
    assert report.result == "diverges"
    assert diffs(report) == [("node", "v472b", G, "", "", None)]  # the default copy is untouched
    w.verify(adopt=True)
    assert w.verify().result == "equal"


# --------------------------------------------------------------------------- #
# a node that moves graph
# --------------------------------------------------------------------------- #


def test_6_a_node_adopted_in_a_named_graph_and_moved_to_the_default_graph_outside_the_ledger_diverges(w):
    w.engine.ledger.enable()
    w.engine.create_node("v472m", labels=["L"], properties={"k": "v"}, graph=G)
    w.verify(adopt=True)
    assert w.verify().result == "equal"
    # moved: the named graph's rows are deleted, the default graph's are written
    assert w.engine.delete_node("v472m", graph=G)
    w.engine.create_node("v472m", labels=["L"], properties={"k": "v"})
    moved = w.tables()
    assert moved["nodes"] == [("v472m", "")] and moved["labels"] == [("v472m", "L", "")]
    report = w.verify()
    assert report.result == "diverges"
    assert sorted((d[2] or "", d[3]) for d in diffs(report)) == [
        ("", ""), ("", "label:L"), ("", "prop:k"), (G, ""),
    ]  # the default copy is unrecorded; the named copy the ledger holds is gone
    w.verify(adopt=True)
    assert w.tables() == moved
    assert w.verify().result == "equal"


# --------------------------------------------------------------------------- #
# ledger operations on an adopted node
# --------------------------------------------------------------------------- #


def test_7_ledger_operations_on_a_node_only_a_named_graph_holds_verify_equal_each_step(w):
    from iris_vector_graph.ledger import Changeset

    w.engine.create_node("v472n", labels=["L"], properties={"k": "named"}, graph=G)
    w.engine.ledger.enable()
    assert w.verify().result == "equal"

    def commit(build):
        cs = Changeset(actor="t472", actor_type="test")
        build(cs)
        w.engine.ledger.commit(cs)

    named = [("v472n", "L", G)]
    commit(lambda cs: cs.set_property("v472n", "k", "default-copy"))
    t = w.tables()
    assert t["nodes"] == [("v472n", G)] and ("v472n", "k", "default-copy", "") in t["props"]
    assert ("v472n", "k", "named", G) in t["props"]
    assert w.verify().result == "equal"

    # a label cannot be the default graph's: rdf_labels' foreign key wants the default graph's node row, so the commit
    # is refused whole (as on 4.7.1) and the tables and the verdict stay as they were
    from iris_vector_graph.ledger.errors import ChangesetOperationError

    before = w.tables()
    with pytest.raises(ChangesetOperationError):
        commit(lambda cs: cs.add_label("v472n", "K"))
    assert w.tables() == before
    assert w.verify().result == "equal"

    commit(lambda cs: cs.remove_property("v472n", "k"))
    assert ("v472n", "k", "default-copy", "") not in w.tables()["props"]
    assert ("v472n", "k", "named", G) in w.tables()["props"]
    assert w.tables()["labels"] == named
    assert w.verify().result == "equal"  # nothing of the default graph's is left

    commit(lambda cs: cs.set_property("v472n", "later", "x"))
    commit(lambda cs: cs.delete_node("v472n"))
    t = w.tables()
    assert t["nodes"] == [("v472n", G)] and t["labels"] == named  # the named copy is not the ledger's to delete
    assert ("v472n", "k", "named", G) in t["props"] and not [p for p in t["props"] if p[3] == ""]
    assert w.verify().result == "equal"


def test_8_a_ledger_node_in_the_default_graph_beside_a_named_copy_verifies_equal_through_changes(w):
    from iris_vector_graph.ledger import Changeset

    _both(w)
    w.engine.ledger.enable()
    cs = Changeset(actor="t472", actor_type="test")
    cs.set_property("v472b", "k", "new-default")
    cs.add_label("v472b", "N")
    w.engine.ledger.commit(cs)
    t = w.tables()
    assert ("v472b", "k", "new-default", "") in t["props"] and ("v472b", "k", "named", G) in t["props"]
    assert w.verify().result == "equal"
    cs = Changeset(actor="t472", actor_type="test")
    cs.delete_node("v472b")
    w.engine.ledger.commit(cs)
    assert w.tables()["nodes"] == [("v472b", G)]
    assert w.verify().result == "equal"


# --------------------------------------------------------------------------- #
# a relationship in a named graph needs its endpoints' rows in that graph
# --------------------------------------------------------------------------- #


def test_8b_a_ledger_relationship_in_a_named_graph_and_its_endpoint_rows_verify_equal(w):
    from iris_vector_graph.ledger import Changeset

    w.engine.ledger.enable()
    cs = Changeset(actor="t472", actor_type="test")
    cs.create_node("v472x", labels=["L"])
    cs.create_node("v472y")
    cs.create_relationship("v472x", "R472", "v472y", graph=G)
    w.engine.ledger.commit(cs)
    t = w.tables()
    # the default graph's nodes, and the rows the relationship's foreign keys need in its own graph
    assert t["nodes"] == [("v472x", ""), ("v472x", G), ("v472y", ""), ("v472y", G)]
    assert t["labels"] == [("v472x", "L", "")] and [e[:4] for e in t["edges"]] == [("v472x", "R472", "v472y", G)]
    assert w.verify().result == "equal"

    cs = Changeset(actor="t472", actor_type="test")
    cs.delete_relationship(("v472x", "R472", "v472y", G))
    w.engine.ledger.commit(cs)
    after = w.tables()
    assert after["edges"] == []
    assert w.verify().result == "equal", (after["nodes"], diffs(w.verify()))


def test_8c_an_edge_deleted_outside_the_ledger_is_seen_and_so_is_the_label_an_endpoint_row_gains(w):
    from iris_vector_graph.ledger import Changeset

    w.engine.ledger.enable()
    cs = Changeset(actor="t472", actor_type="test")
    cs.create_node("v472x")
    cs.create_node("v472y")
    cs.create_relationship("v472x", "R472", "v472y", graph=G)
    w.engine.ledger.commit(cs)
    assert w.verify().result == "equal"
    # an endpoint row of the named graph gains a label outside the ledger: a difference in that graph
    w.sql(f"INSERT INTO {w.schema}.rdf_labels (s, label, graph_id) VALUES ('v472x', 'Out', ?)", [G])
    w.conn.commit()
    assert diffs(w.verify()) == [
        ("node", "v472x", G, "", None, ""),  # no longer a bare endpoint row
        ("node", "v472x", G, "label:Out", None, ""),
    ]
    w.sql(f"DELETE FROM {w.schema}.rdf_labels WHERE s = 'v472x' AND label = 'Out'")
    w.conn.commit()
    assert w.verify().result == "equal"
    # the edge deleted outside the ledger: the statement is the difference, and the endpoint rows stay
    assert w.engine.delete_edge("v472x", "R472", "v472y", graph=G)
    assert w.tables()["edges"] == [] and ("v472x", G) in w.tables()["nodes"]
    report = w.verify()
    assert report.result == "diverges"
    assert [(d.entity_kind, d.attr) for d in report.differences] == [("rel", "")]


# --------------------------------------------------------------------------- #
# strict mode
# --------------------------------------------------------------------------- #


def test_9_strict_mode_refuses_the_write_and_verify_stays_equal(w):
    from iris_vector_graph.ledger import LedgerStrictModeError

    w.engine.ledger.enable()
    w.engine.ledger.set_strict(True)
    try:
        before = w.tables()
        with pytest.raises(LedgerStrictModeError):
            w.engine.create_node("v472s", labels=["L"], graph=G)
        with pytest.raises(LedgerStrictModeError):
            w.engine.create_node("v472s")
        assert w.tables() == before == {"nodes": [], "labels": [], "props": [], "edges": []}
        assert w.verify().result == "equal"
        # a write that bypasses the engine's guard is still seen, in its own graph
        w.sql(f"INSERT INTO {w.schema}.nodes (node_id, graph_id) VALUES ('v472s', ?)", [G])
        w.conn.commit()
        assert diffs(w.verify()) == [("node", "v472s", G, "", None, "")]
    finally:
        w.engine.ledger.set_strict(False)


# --------------------------------------------------------------------------- #
# fractional qualifiers
# --------------------------------------------------------------------------- #

QUALS = {
    "w08": 0.8, "w05": 0.5, "one": 1, "onef": 1.0, "neg": -0.25, "tiny": 1e-5, "big": 123456789012,
    "huge": 1e22, "pi": 3.141592653589793, "str": "0.8", "flag": True,
    "nest": {"a": 1, "b": [1, 2.5, 0.5, "x"], "c": {"d": True}},
}


def _edge(w, quals=None, graph=None):
    for n in ("v472q1", "v472q2"):
        w.engine.create_node(n, graph=graph)
    w.engine.create_edge("v472q1", "R472", "v472q2", qualifiers=quals, graph=graph)


def test_10_a_graph_with_fractional_qualifiers_verifies_equal_right_after_enable(w):
    _edge(w, dict(QUALS))
    stored = json.loads(w.tables()["edges"][0][4])
    assert stored["w08"] == 0.8 and stored["onef"] == 1.0 and stored["tiny"] == 1e-5  # numbers, as bulk-loaded
    w.engine.ledger.enable()
    rows = w.sql(f"SELECT * FROM {w.schema}.ledger_records(1, 1, 1, 5000)")
    genesis = {r[5]: r[8] for r in rows if r[2] == "set_qual"}  # attr -> new
    assert genesis["w08"] == ".8" and genesis["onef"] == "1" and genesis["neg"] == "-.25"  # IRIS's spelling
    report = w.verify()
    assert (report.result, report.differences) == ("equal", [])


@pytest.mark.parametrize("graph", [None, G])
def test_11_a_changed_qualifier_is_still_a_difference(w, graph):
    _edge(w, {"w": 0.8, "n": {"a": 1}}, graph=graph)
    w.engine.ledger.enable()
    assert w.verify().result == "equal"

    def set_quals(text):
        w.sql(f"UPDATE {w.schema}.rdf_edges SET qualifiers = ? WHERE s = 'v472q1'", [text])
        w.conn.commit()

    set_quals('{"w": 0.80, "n": {"a": 1.0}}')  # the same numbers, spelled another way
    assert w.verify().result == "equal"
    set_quals('{"w": 0.9, "n": {"a": 1}}')
    report = w.verify()
    assert [(d.attr, d.before, d.after) for d in report.differences] == [("qual:w", ".8", "0.9")]
    set_quals('{"w": 0.8, "n": {"a": 2}}')
    assert [(d.attr, d.before, d.after) for d in w.verify().differences] == [("qual:n", '{"a":1}', '{"a":2}')]
    set_quals('{"w": "0.8", "n": {"a": 1}}')  # a string where there was a number: not the value genesis recorded
    assert [(d.attr, d.before, d.after) for d in w.verify().differences] == [("qual:w", ".8", "0.8")]
    set_quals('{"w": 0.8, "n": {"a": 1}}')
    assert w.verify().result == "equal"


def test_12_a_ledger_written_qualifier_and_a_number_loaded_later_stay_apart(w):
    from iris_vector_graph.ledger import Changeset

    _edge(w, {"w": 0.8})
    w.engine.ledger.enable()
    cs = Changeset(actor="t472", actor_type="test")
    cs.set_qualifier(("v472q1", "R472", "v472q2"), "w", "0.25")
    w.engine.ledger.commit(cs)
    assert json.loads(w.tables()["edges"][0][4])["w"] == "0.25"
    assert w.verify().result == "equal"


# --------------------------------------------------------------------------- #
# redact on these states
# --------------------------------------------------------------------------- #


def test_13_redact_a_named_graph_whose_nodes_the_genesis_recorded_leaves_verify_equal(w):
    w.engine.create_node("v472|r", labels=["L"], properties={"k": "secret"}, graph=G)
    w.engine.create_node("v472keep", labels=["L"])
    w.engine.ledger.enable()
    assert w.verify().result == "equal"
    assert w.engine.delete_node("v472|r", graph=G)  # erase first
    assert w.tables()["nodes"] == [("v472keep", "")]
    assert w.verify().result == "diverges"  # erased outside the ledger, not yet redacted
    receipt = w.engine.ledger.redact(G)
    assert receipt.complete and receipt.records_tombstoned == 3  # the named graph's create_node, add_label, set_prop
    assert w.tables()["nodes"] == [("v472keep", "")]
    report = w.verify()
    assert (report.result, report.differences) == ("equal", [])
    assert [r for r in w.records() if r[3] == "v472|r" and r[1] != "redacted"] == []


def test_14_redact_a_default_prefix_while_a_named_copy_lives_is_refused_and_changes_nothing(w):
    from iris_vector_graph.ledger.errors import RedactionTargetLiveError

    w.engine.create_node("v472|b", labels=["L"])
    w.engine.create_node("v472|b", labels=["L"], graph=G)
    w.engine.ledger.enable()
    assert w.engine.delete_node("v472|b", graph=None)
    before = w.records()
    with pytest.raises(RedactionTargetLiveError):
        w.engine.ledger.redact(None, prefix="v472")  # the named copy still holds an id of the prefix
    assert w.records() == before
    assert w.tables()["nodes"] == [("v472|b", G)]
