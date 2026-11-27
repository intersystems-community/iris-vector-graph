"""4.7.2: verify() is graph-aware for nodes, labels and properties, and reads a qualifier's number the way genesis does.

The ledger keeps nodes by graph. A ledger commit writes the default graph (R281, R290); the genesis and an adoption
write a named graph's node with that graph on its records. Before 4.7.2 `read_canonical` read the node, label and
property tables without `graph_id` and the replay held one node per id, so a node that exists only in a named graph,
the same id in two graphs, a node deleted from the default graph while a named copy stayed, and a node moved from a
named graph to the default graph all verified "equal". The live check is
tests/e2e/test_472_ledger_verify_graph_e2e.py; these cases pin the comparison with no database.

Qualifiers: genesis spells a JSON number the way IRIS does (`.8`, `-.25`, `1` for 1.0, `.00001`, `10000000000000000000000`
for 1e22) and `read_canonical` spelled it as Python does (`0.8`, `1.0`, `1e-05`), so a graph bulk-loaded with a
fractional weight diverged on every such statement with nothing written in between.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.ledger import replay as R


def rec(op, kind, eid, attr="", new=None, prior=None, graph=None, seq=1, ordinal=1):
    return R.MutationRecord(seq, ordinal, op, kind, eid, attr, prior, new, graph)


class FakeConn:
    """A connection whose cursor answers each table's SELECT from a dict, and keeps the SQL it was sent."""

    def __init__(self, tables):
        self.tables = tables
        self.sql = []

    def cursor(self):
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        for table, rows in self.conn.tables.items():
            if f".{table}" in sql:
                self._rows = rows
                return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


def tables(nodes=(), labels=(), props=(), edges=()):
    return {"nodes": list(nodes), "rdf_labels": list(labels), "rdf_props": list(props), "rdf_edges": list(edges)}


def canonical(**kw):
    return R.read_canonical(FakeConn(tables(**kw)), "Graph_KG")


def replay(*records):
    st = R.GraphState()
    for i, r in enumerate(records, 1):
        st.apply(r)
    return st


def verdict(replayed, canon):
    entries, _ = R.compare(replayed, canon)
    return [(e.entity_kind, e.entity_id, e.graph, e.attr, e.before, e.after) for e in entries]


# --------------------------------------------------------------------------- #
# the replay keeps a node per graph
# --------------------------------------------------------------------------- #


def test_a_named_graphs_node_is_not_the_default_graphs():
    st = replay(
        rec("create_node", "node", "n", new=""),
        rec("create_node", "node", "n", new="", graph="g"),
        rec("add_label", "node", "n", "L", graph="g"),
        rec("set_prop", "node", "n", "k", new="v", graph="g"),
    )
    assert set(st.nodes) == {"n"} and st.nodes["n"].labels == set() and st.nodes["n"].props == {}
    assert st.graph_nodes[("g", "n")].labels == {"L"} and st.graph_nodes[("g", "n")].props == {"k": "v"}
    assert st.entity_count == 2


def test_deleting_the_default_node_leaves_the_named_one():
    st = replay(
        rec("create_node", "node", "n", new=""),
        rec("create_node", "node", "n", new="", graph="g"),
        rec("delete_node", "node", "n", prior="{}"),
    )
    assert "n" not in st.nodes and ("g", "n") in st.graph_nodes


def test_deleting_the_named_node_leaves_the_default_one():
    st = replay(
        rec("create_node", "node", "n", new=""),
        rec("create_node", "node", "n", new="", graph="g"),
        rec("delete_node", "node", "n", prior="{}", graph="g"),
    )
    assert "n" in st.nodes and st.graph_nodes == {}


def test_a_default_graph_write_to_a_node_only_a_named_graph_holds_is_replayed_as_the_ledger_applied_it():
    """LedgerApply checks NodeExists in every graph and writes the default graph's rows: an orphan label or property."""
    st = replay(
        rec("create_node", "node", "n", new="", graph="g"),
        rec("add_label", "node", "n", "L"),
        rec("set_prop", "node", "n", "k", new="v"),
    )
    assert st.nodes["n"].labels == {"L"} and st.nodes["n"].props == {"k": "v"}
    assert st.graph_nodes[("g", "n")].labels == set()


def test_a_default_graph_write_to_a_node_no_graph_holds_is_still_ignored():
    st = replay(rec("add_label", "node", "ghost", "L"))
    assert st.nodes == {} and st.graph_nodes == {}


def test_the_blind_projection_is_what_the_export_has_always_listed():
    st = replay(
        rec("create_node", "node", "only_g", new="", graph="g"),
        rec("add_label", "node", "only_g", "L", graph="g"),
        rec("create_node", "node", "both", new=""),
        rec("set_prop", "node", "both", "k", new="default"),
        rec("create_node", "node", "both", new="", graph="g"),
        rec("set_prop", "node", "both", "k", new="named", graph="g"),
        rec("set_prop", "node", "both", "j", new="named-only", graph="g"),
    )
    blind = st.blind_nodes()
    assert sorted(blind) == ["both", "only_g"]
    assert blind["only_g"].labels == {"L"}
    assert blind["both"].props == {"k": "default", "j": "named-only"}  # the default graph's value wins


# --------------------------------------------------------------------------- #
# the tables are read by graph
# --------------------------------------------------------------------------- #


def test_read_canonical_selects_the_graph_of_every_node_row():
    conn = FakeConn(tables())
    R.read_canonical(conn, "Graph_KG")
    by_table = {t: s for s in conn.sql for t in ("nodes", "rdf_labels", "rdf_props") if f"Graph_KG.{t}" in s}
    assert all("graph_id" in sql for sql in by_table.values()) and len(by_table) == 3, conn.sql


def test_read_canonical_files_each_row_under_its_graph():
    st = canonical(
        nodes=[("a", ""), ("a", "g"), ("b", "g"), ("c", None), ("d", "\x00")],
        labels=[("a", "L", "g"), ("c", "K", "")],
        props=[("a", "k", "v", "g"), ("a", "id", "a", "g"), ("b", "k", None, "g")],
    )
    assert sorted(st.nodes) == ["a", "c", "d"]
    assert sorted(st.graph_nodes) == [("g", "a"), ("g", "b")]
    assert st.graph_nodes[("g", "a")].labels == {"L"} and st.graph_nodes[("g", "a")].props == {"k": "v"}
    assert st.graph_nodes[("g", "b")].props == {"k": ""}
    assert st.nodes["c"].labels == {"K"} and st.nodes["a"].labels == set()


# --------------------------------------------------------------------------- #
# compare: equal exactly when the states are
# --------------------------------------------------------------------------- #

CREATE_G = [rec("create_node", "node", "n", new="", graph="g")]


def test_a_node_only_in_a_named_graph_is_equal_when_the_ledger_has_it_there():
    assert verdict(replay(*CREATE_G), canonical(nodes=[("n", "g")])) == []


def test_a_node_the_ledger_has_in_the_default_graph_but_the_tables_hold_in_a_named_one_diverges():
    """What a genesis before 4.7.2 recorded for a named graph's node: no graph. It verified equal."""
    got = verdict(replay(rec("create_node", "node", "n", new="")), canonical(nodes=[("n", "g")]))
    assert got == [("node", "n", None, "", "", None), ("node", "n", "g", "", None, "")]


def test_a_named_node_written_outside_the_ledger_diverges():
    assert verdict(replay(), canonical(nodes=[("n", "g")])) == [("node", "n", "g", "", None, "")]


def test_the_same_id_in_two_graphs_is_two_nodes():
    rep = replay(rec("create_node", "node", "n", new=""))
    got = verdict(rep, canonical(nodes=[("n", ""), ("n", "g")]))
    assert got == [("node", "n", "g", "", None, "")]


def test_the_default_copy_deleted_outside_the_ledger_is_seen_while_the_named_copy_stays():
    rep = replay(rec("create_node", "node", "n", new=""), *CREATE_G)
    got = verdict(rep, canonical(nodes=[("n", "g")]))
    assert got == [("node", "n", None, "", "", None)]


def test_a_node_moved_from_a_named_graph_to_the_default_graph_diverges_both_ways():
    got = verdict(replay(*CREATE_G), canonical(nodes=[("n", "")]))
    assert got == [("node", "n", None, "", None, ""), ("node", "n", "g", "", "", None)]


def test_a_label_or_property_in_the_other_graph_is_a_difference():
    rep = replay(rec("create_node", "node", "n", new=""), rec("create_node", "node", "n", new="", graph="g"),
                 rec("add_label", "node", "n", "L"), rec("set_prop", "node", "n", "k", new="v"))
    same = canonical(nodes=[("n", ""), ("n", "g")], labels=[("n", "L", "")], props=[("n", "k", "v", "")])
    assert verdict(rep, same) == []
    moved = canonical(nodes=[("n", ""), ("n", "g")], labels=[("n", "L", "g")], props=[("n", "k", "v", "g")])
    assert verdict(rep, moved) == [
        ("node", "n", None, "label:L", "", None),
        ("node", "n", "g", "label:L", None, ""),
        ("node", "n", None, "prop:k", "v", None),
        ("node", "n", "g", "prop:k", None, "v"),
    ]


def test_a_default_graph_write_to_a_named_graphs_node_verifies_equal_against_its_orphan_rows():
    rep = replay(*CREATE_G, rec("add_label", "node", "n", "L"), rec("set_prop", "node", "n", "k", new="v"))
    orphan = canonical(nodes=[("n", "g")], labels=[("n", "L", "")], props=[("n", "k", "v", "")])
    assert verdict(rep, orphan) == []


def test_default_graph_only_ledgers_compare_as_before():
    rep = replay(rec("create_node", "node", "a", new=""), rec("add_label", "node", "a", "L"))
    entries, cls = R.compare(rep, canonical(nodes=[("a", "")], labels=[("a", "L", "")]))
    assert (entries, cls) == ([], None)
    entries, cls = R.compare(rep, canonical(nodes=[("a", ""), ("b", "")], labels=[("a", "L", "")]))
    assert [(e.entity_id, e.graph) for e in entries] == [("b", None)] and cls == "unrecorded_writes"


# --------------------------------------------------------------------------- #
# the bare node rows an edge of a named graph needs
# --------------------------------------------------------------------------- #

TUPLE_G = '{"graph":"g","o":"b","p":"R","s":"a"}'
DEFAULT_AB = [rec("create_node", "node", "a", new=""), rec("create_node", "node", "b", new="")]


def test_the_bare_endpoint_rows_of_a_named_graphs_edge_are_part_of_the_edge():
    """LedgerApply.EnsureEndpoint and a temporal write make them, and the ledger records neither."""
    rep = replay(*DEFAULT_AB, rec("create_rel", "rel", "1", new=TUPLE_G, graph="g"))
    tables_ = dict(nodes=[("a", ""), ("b", ""), ("a", "g"), ("b", "g")], edges=[("a", "R", "b", "g", None)])
    assert verdict(rep, canonical(**tables_)) == []


def test_they_stay_part_of_the_edge_after_the_ledger_deletes_it():
    rep = replay(
        *DEFAULT_AB,
        rec("create_rel", "rel", "1", new=TUPLE_G, graph="g"),
        rec("delete_rel", "rel", "1", prior=TUPLE_G, graph="g", ordinal=2),
    )
    assert verdict(rep, canonical(nodes=[("a", ""), ("b", ""), ("a", "g"), ("b", "g")])) == []


def test_a_temporal_edges_marked_row_needs_its_endpoint_rows_too():
    rep = replay(*DEFAULT_AB)
    marked = [("a", "T", "b", "g", '{"temporal":true}')]
    assert verdict(rep, canonical(nodes=[("a", ""), ("b", ""), ("a", "g"), ("b", "g")], edges=marked)) == []


def test_a_bare_row_no_edge_of_its_graph_ever_named_is_a_node():
    rep = replay(*DEFAULT_AB, rec("create_rel", "rel", "1", new=TUPLE_G, graph="g"))
    got = verdict(rep, canonical(nodes=[("a", ""), ("b", ""), ("a", "g"), ("b", "g"), ("c", "g")],
                                 edges=[("a", "R", "b", "g", None)]))
    assert got == [("node", "c", "g", "", None, "")]


def test_an_endpoint_row_with_a_label_or_property_is_a_node():
    rep = replay(*DEFAULT_AB, rec("create_rel", "rel", "1", new=TUPLE_G, graph="g"))
    got = verdict(rep, canonical(nodes=[("a", ""), ("b", ""), ("a", "g"), ("b", "g")], labels=[("a", "Out", "g")],
                                 props=[("b", "k", "v", "g")], edges=[("a", "R", "b", "g", None)]))
    assert got == [
        ("node", "a", "g", "", None, ""),
        ("node", "a", "g", "label:Out", None, ""),
        ("node", "b", "g", "", None, ""),
        ("node", "b", "g", "prop:k", None, "v"),
    ]


def test_a_named_graph_edge_is_not_an_endpoint_row_of_the_default_graph():
    rep = replay(rec("create_node", "node", "a", new=""))
    got = verdict(rep, canonical(nodes=[("a", ""), ("b", "")], edges=[("a", "R", "b", "", None)]))
    assert ("node", "b", None, "", None, "") in got  # a default-graph node is never implied


# --------------------------------------------------------------------------- #
# adoption names the graph
# --------------------------------------------------------------------------- #


def test_adoption_items_carry_the_graph_of_a_node_difference():
    canon = canonical(nodes=[("n", "g")], labels=[("n", "L", "g")], props=[("n", "k", "v", "g")])
    entries, _ = R.compare(R.GraphState(), canon)
    items = R._adoption_items(entries, R.GraphState())
    assert [(i["op"], i["entity_id"], i["attr"], i["graph"]) for i in items] == [
        ("create_node", "n", "", "g"),
        ("add_label", "n", "L", "g"),
        ("set_prop", "n", "k", "g"),
    ]


def test_adopting_what_compare_found_makes_the_replay_equal():
    canon = canonical(nodes=[("n", ""), ("n", "g"), ("m", "g")], labels=[("n", "L", "g")], props=[("m", "k", "v", "g")])
    rep = replay(rec("create_node", "node", "n", new="", graph=None))
    entries, _ = R.compare(rep, canon)
    for i, item in enumerate(R._adoption_items(entries, rep), 1):
        rep.apply(R.MutationRecord(2, i, item["op"], item["entity_kind"], item["entity_id"], item["attr"],
                                   item["prior"], item["new"], item["graph"]))
    assert verdict(rep, canon) == []


# --------------------------------------------------------------------------- #
# DiffEntry and history
# --------------------------------------------------------------------------- #


def test_a_diff_entry_has_no_graph_unless_it_names_one():
    plain = R.DiffEntry("node", "n", "", None, "")
    assert plain.graph is None and "graph" not in plain.to_wire()
    named = R.DiffEntry("node", "n", "", None, "", graph="g")
    assert named.to_wire()["graph"] == "g" and named.inverse().graph == "g"


def test_the_history_diff_keeps_two_graphs_nodes_apart():
    diff = R.compute_diff(
        [rec("create_node", "node", "n", new=""), rec("create_node", "node", "n", new="", graph="g"),
         rec("delete_node", "node", "n", prior="{}", graph="g", ordinal=3)]
    )
    assert [(e.entity_id, e.graph, e.attr) for e in diff.entries] == [("n", None, "")]


# --------------------------------------------------------------------------- #
# qualifier numbers
# --------------------------------------------------------------------------- #

EDGE = ("a", "R", "b", "", None)


def edge_canon(quals_json):
    return canonical(nodes=[("a", ""), ("b", "")], edges=[("a", "R", "b", "", quals_json)])


def stmt_replay(**quals):
    st = replay(rec("create_node", "node", "a", new=""), rec("create_node", "node", "b", new=""))
    stmt = R.StmtState("a", "R", "b", None, dict(quals))
    st.statements["1"] = stmt
    st._stmt_by_tuple[stmt.tuple] = "1"
    return st


@pytest.mark.parametrize(
    "json_text, genesis_text",
    [
        ('{"w": 0.8}', ".8"),
        ('{"w": 0.5}', ".5"),
        ('{"w": 1}', "1"),
        ('{"w": 1.0}', "1"),
        ('{"w": -0.25}', "-.25"),
        ('{"w": 1e-05}', ".00001"),
        ('{"w": 1e+22}', "10000000000000000000000"),
        ('{"w": 123456789012}', "123456789012"),
        ('{"w": 3.141592653589793}', "3.141592653589793"),
        ('{"w": 0}', "0"),
        ('{"w": 0.0}', "0"),
    ],
)
def test_a_json_number_matches_the_text_iris_genesis_made_of_it(json_text, genesis_text):
    assert verdict(stmt_replay(w=genesis_text), edge_canon(json_text)) == []


@pytest.mark.parametrize(
    "json_text, ledger_text",
    [
        ('{"w": 0.8}', ".9"),
        ('{"w": 0.8}', "0.81"),
        ('{"w": 1}', "2"),
        ('{"w": 1}', ""),
        ('{"w": 1}', "one"),
        ('{"w": 0.8}', "true"),
    ],
)
def test_a_different_number_is_still_a_difference(json_text, ledger_text):
    got = verdict(stmt_replay(w=ledger_text), edge_canon(json_text))
    assert [(g[3], g[4]) for g in got] == [("qual:w", ledger_text)]


def test_a_string_qualifier_is_compared_as_text():
    """The tables hold the string "0.8" (a ledger commit writes strings), the ledger says .8: not the same value."""
    got = verdict(stmt_replay(w=".8"), edge_canon('{"w": "0.8"}'))
    assert [(g[3], g[4], g[5]) for g in got] == [("qual:w", ".8", "0.8")]
    assert verdict(stmt_replay(w="0.8"), edge_canon('{"w": "0.8"}')) == []


def test_a_boolean_is_not_the_number_one():
    got = verdict(stmt_replay(w="1"), edge_canon('{"w": true}'))
    assert [(g[3], g[4], g[5]) for g in got] == [("qual:w", "1", "true")]
    assert verdict(stmt_replay(w="true"), edge_canon('{"w": true}')) == []


def test_a_nested_value_is_compared_as_json_not_as_python_spells_it():
    ledger = '{"a":1,"b":[1,2.5,0.5,"x"],"c":{"d":true}}'  # IRIS's %ToJSON: compact
    assert verdict(stmt_replay(n=ledger), edge_canon('{"n": {"a": 1, "b": [1, 2.5, 0.5, "x"], "c": {"d": true}}}')) == []
    assert verdict(stmt_replay(n='{"a":2}'), edge_canon('{"n": {"a": 1}}'))[0][3] == "qual:n"
    assert verdict(stmt_replay(n='{"a":true}'), edge_canon('{"n": {"a": 1}}'))[0][3] == "qual:n"


def test_a_reported_difference_keeps_the_text_the_tables_hold():
    got = verdict(stmt_replay(w="2"), edge_canon('{"w": 0.8}'))
    assert got[0][5] == "0.8" and type(got[0][5]) is str


# --------------------------------------------------------------------------- #
# the genesis names each row's graph
# --------------------------------------------------------------------------- #


def _genesis_source() -> str:
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    return read_class_sources(packaged_class_dir())["Graph.KG.LedgerGenesis"]


def test_the_genesis_reads_graph_id_of_every_node_label_and_property_and_records_it():
    lines = [ln.strip() for ln in _genesis_source().splitlines()]
    scans = [ln for ln in lines if ".Run(" in ln and any(t in ln for t in (".nodes ", ".rdf_labels ", ".rdf_props "))]
    assert len(scans) == 3 and all("graph_id" in ln for ln in scans), scans
    recs = [ln for ln in lines if ln.startswith("Do ##class(Graph.KG.LedgerApply).Rec(") and '"node"' in ln]
    assert len(recs) == 3 and all("..GraphOf(" in ln for ln in recs), recs


def test_the_export_lists_one_line_per_id_whatever_the_graphs(tmp_path):
    """The export's node lines carry no graph, so a node is one line, as before the replay kept one per graph."""
    from iris_vector_graph.ledger.export import export_ndjson

    st = replay(
        rec("create_node", "node", "both", new=""),
        rec("set_prop", "node", "both", "k", new="default"),
        rec("create_node", "node", "both", new="", graph="g"),
        rec("add_label", "node", "both", "L", graph="g"),
        rec("create_node", "node", "only_g", new="", graph="g"),
        rec("set_prop", "node", "only_g", "j", new="v", graph="g"),
    )
    path = tmp_path / "out.ndjson"
    summary = export_ndjson(st, str(path))
    assert path.read_text().splitlines() == [
        '{"id":"both","labels":["L"],"props":{"k":"default"},"type":"node"}',
        '{"id":"only_g","labels":[],"props":{"j":"v"},"type":"node"}',
    ]
    assert (summary.nodes, summary.rels) == (2, 0)
