"""4.6.0, live: a Cypher write with no USE GRAPH writes each matched node in its own graph, and nothing else.

Found by fixes63: `MATCH (n {node_id: $id}) SET n.p = $v`, with no USE GRAPH, where two graphs hold the id, failed
SQLCODE -119 on rdf_props pk_props: the SET's INSERT wrote the default graph's row once per graph holding the id. The
same for `SET n += map`, `SET n = map`, `SET n:L` and MERGE's ON MATCH SET. The writes that did not fail read the ids
through a MATCH whose label and property joins were on node_id alone, so a graph whose copy of the id does not match
the pattern was written too. CONTEXT.md, All-graphs read: a node is its own graph's node; a write applies to each node
the MATCH binds, in that node's graph.

By hand, the fixture: in each of three graphs a node a, b and c, and an edge a -R {w: 1}-> b.

* the default graph and r65a: a is (:A {k: 1}), b is :B, c is :C, so `(n:A {node_id: 'r65:a'})` matches there;
* r65x: the same ids, every node labelled X, so no pattern below with a label matches there.

Each write, worked out per graph (its rows in rdf_props, rdf_labels, rdf_edges and nodes):

* no USE GRAPH: the default graph and r65a change as the write says; r65x does not change. The one pattern with no
  label (`MATCH (n {node_id: $id}) SET n.p = $v`, fixes63's) matches in all three, and all three change.
* `USE GRAPH 'r65a'`: r65a changes, the others do not.
* the default graph only (no r65a): the default graph changes; r65x changes only for the unlabelled pattern.
* MERGE names no graph, so it matches and creates in the default graph, as CREATE writes it: its ON MATCH SET writes
  the default graph's a (r65a's under `USE GRAPH 'r65a'`), never another graph's copy of the id.

Namespace: an IVG460FX* scratch namespace. Native on and off (writes go through SQL either way).
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

DG, GA, GX = "", "r65a", "r65x"
A, B, C = "r65:a", "r65:b", "r65:c"
IDS = {"id": A, "b": B, "c": C}


def _graph_rows(graph: str, labels: tuple) -> str:
    use = f"USE GRAPH '{graph}' " if graph else ""
    la, lb, lc = labels
    return (
        f"{use}CREATE (:{la} {{node_id: '{A}', k: 1}})-[:R {{w: 1}}]->(:{lb} {{node_id: '{B}'}}), "
        f"(:{lc} {{node_id: '{C}'}})"
    )


def _set_k(st):
    st["props"][(A, "k")] = "7"


def _set_z(st):
    st["props"][(A, "z")] = "7"


def _k_plus_one(st):
    st["props"][(A, "k")] = "2"


def _z_from_k(st):
    st["props"][(A, "z")] = "2"


def _w_from_rel(st):
    st["props"][(A, "w")] = "1"


def _c_z(st):
    st["props"][(C, "z")] = "1"


def _set_p(st):
    st["props"][(A, "p")] = "7"


def _merge_map(st):
    st["props"][(A, "z")] = "2"
    del st["props"][(A, "k")]


def _replace_map(st):
    st["props"] = {key: v for key, v in st["props"].items() if key[0] != A}
    st["props"][(A, "z")] = "3"


def _remove_k(st):
    del st["props"][(A, "k")]


def _label_l(st):
    st["labels"].add((A, "L"))


def _unlabel_a(st):
    st["labels"].discard((A, "A"))


def _rel_w(st):
    st["edges"][(A, "R", B)] = {"w": "9"}


def _rel_gone(st):
    del st["edges"][(A, "R", B)]


def _node_gone(node):
    def change(st):
        st["props"] = {key: v for key, v in st["props"].items() if key[0] != node}
        st["labels"] = {row for row in st["labels"] if row[0] != node}
        st["edges"] = {key: v for key, v in st["edges"].items() if node not in (key[0], key[2])}
        st["nodes"].discard(node)

    return change


def _merge_k(st):
    st["props"][(A, "k")] = "8"


def _merge_z(st):
    st["props"][(A, "z")] = "8"


#: name -> (query after the optional USE GRAPH, its parameters, the change in each graph it writes, the kind of match:
#: "labelled" (the default graph and r65a), "unlabelled" (every graph holding the id), "merge" (MERGE's own graph))
CASES = {
    "set_existing": ("MATCH (n:A {node_id: $id}) SET n.k = $v", {"v": 7}, _set_k, "labelled"),
    "set_new": ("MATCH (n:A {node_id: $id}) SET n.z = $v", {"v": 7}, _set_z, "labelled"),
    "set_expression": ("MATCH (n:A {node_id: $id}) SET n.k = n.k + 1", {}, _k_plus_one, "labelled"),
    "set_new_from_other_key": ("MATCH (n:A {node_id: $id}) SET n.z = n.k + 1", {}, _z_from_k, "labelled"),
    "set_from_rel_prop": ("MATCH (n:A {node_id: $id})-[r:R]->(:B) SET n.w = r.w", {}, _w_from_rel, "labelled"),
    "set_second_pattern": ("MATCH (:A {node_id: $id}), (n:C {node_id: $c}) SET n.z = 1", {}, _c_z, "labelled"),
    "set_unlabelled": ("MATCH (n {node_id: $id}) SET n.p = $v", {"v": 7}, _set_p, "unlabelled"),
    "set_merge_map": ("MATCH (n:A {node_id: $id}) SET n += {z: 2, k: null}", {}, _merge_map, "labelled"),
    "set_replace_map": ("MATCH (n:A {node_id: $id}) SET n = {z: 3}", {}, _replace_map, "labelled"),
    "remove_prop": ("MATCH (n:A {node_id: $id}) REMOVE n.k", {}, _remove_k, "labelled"),
    "set_label": ("MATCH (n:A {node_id: $id}) SET n:L", {}, _label_l, "labelled"),
    "remove_label": ("MATCH (n:A {node_id: $id}) REMOVE n:A", {}, _unlabel_a, "labelled"),
    "set_rel_prop": ("MATCH (:A {node_id: $id})-[r:R]->(:B) SET r.w = 9", {}, _rel_w, "labelled"),
    "delete_rel": ("MATCH (:A {node_id: $id})-[r:R]->(:B) DELETE r", {}, _rel_gone, "labelled"),
    "detach_delete": ("MATCH (n:B {node_id: $b}) DETACH DELETE n", {}, _node_gone(B), "labelled"),
    "delete": ("MATCH (n:C {node_id: $c}) DELETE n", {}, _node_gone(C), "labelled"),
    "merge_on_match_existing": ("MERGE (n:A {node_id: $id}) ON MATCH SET n.k = 8", {}, _merge_k, "merge"),
    "merge_on_match_new": ("MERGE (n:A {node_id: $id}) ON MATCH SET n.z = 8", {}, _merge_z, "merge"),
    "merge_on_match_label": ("MERGE (n:A {node_id: $id}) ON MATCH SET n:L", {}, _label_l, "merge"),
    "merge_by_props_on_match": ("MERGE (n:A {k: 1}) ON MATCH SET n.z = 8", {}, _merge_z, "merge"),
    "merge_on_match_map": ("MERGE (n:A {node_id: $id}) ON MATCH SET n += {z: 8}", {}, _merge_z, "merge"),
}

#: mode -> (the USE GRAPH prefix, the graphs set up, the graphs each kind of match writes)
MODES = {
    "all_graphs": ("", (DG, GA, GX), {"labelled": {DG, GA}, "unlabelled": {DG, GA, GX}, "merge": {DG}}),
    "use_graph": (f"USE GRAPH '{GA}' ", (DG, GA, GX), {"labelled": {GA}, "unlabelled": {GA}, "merge": {GA}}),
    "default_only": ("", (DG, GX), {"labelled": {DG}, "unlabelled": {DG, GX}, "merge": {DG}}),
}


@pytest.fixture(scope="module", params=["native", "sql"])
def engine(request):
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=request.param == "native")
    eng.initialize_schema()
    eng._native_privileges_cached = True
    eng.native_route_ms = 0
    try:
        yield eng
    finally:
        _erase(eng)
        conn.close()


def _erase(eng):
    eng.erase_graph(GA)
    eng.erase_graph(GX)
    eng.execute_cypher("MATCH (n) WHERE n.node_id IN [$id, $b, $c] DETACH DELETE n", IDS, raise_on_error=True)
    eng.conn.commit()


def _setup(eng, graphs):
    _erase(eng)
    for g in graphs:
        eng.execute_cypher(_graph_rows(g, ("X", "X", "X") if g == GX else ("A", "B", "C")), {}, raise_on_error=True)
    eng.conn.commit()


def _state(eng) -> dict:
    """Each graph's rows of the three ids, read with plain SQL (not through the code under test)."""
    cur = eng.conn.cursor()
    marks = ", ".join("?" for _ in IDS)
    ids = list(IDS.values())
    out: dict = {}

    def graph(g):
        return out.setdefault(g or "", {"props": {}, "labels": set(), "edges": {}, "nodes": set()})

    cur.execute(f'SELECT graph_id, s, "key", val FROM Graph_KG.rdf_props WHERE s IN ({marks})', ids)
    for g, s, k, v in cur.fetchall():
        if k != "node_id":
            graph(g)["props"][(s, k)] = str(v)
    cur.execute(f"SELECT graph_id, s, label FROM Graph_KG.rdf_labels WHERE s IN ({marks})", ids)
    for g, s, label in cur.fetchall():
        graph(g)["labels"].add((s, label))
    cur.execute(f"SELECT graph_id, s, p, o_id, qualifiers FROM Graph_KG.rdf_edges WHERE s IN ({marks})", ids)
    for g, s, p, o, q in cur.fetchall():
        graph(g)["edges"][(s, p, o)] = {k: str(v) for k, v in json.loads(q or "{}").items()}
    cur.execute(f"SELECT graph_id, node_id FROM Graph_KG.nodes WHERE node_id IN ({marks})", ids)
    for g, n in cur.fetchall():
        graph(g)["nodes"].add(n)
    return out


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("case", list(CASES))
def test_a_write_changes_each_matched_node_in_its_own_graph_and_nothing_else(engine, mode, case):
    prefix, graphs, writes = MODES[mode]
    query, params, change, kind = CASES[case]
    _setup(engine, graphs)
    before = _state(engine)
    assert set(before) == set(graphs)
    assert before[GX]["labels"] == {(A, "X"), (B, "X"), (C, "X")}

    r = engine.execute_cypher(prefix + query, {**IDS, **params}, raise_on_error=True)
    assert getattr(r, "error", None) is None, r.error
    engine.conn.commit()

    expected = copy.deepcopy(before)
    for g in writes[kind]:
        change(expected[g])
    after = _state(engine)
    for g in graphs:
        assert after.get(g) == expected[g], f"graph {g!r}"
    assert set(after) == set(graphs)


def _merge_rows(eng, label: str) -> list:
    """(graph, label rows, k) of each node holding `label`'s property k, read with plain SQL."""
    cur = eng.conn.cursor()
    cur.execute(
        "SELECT n.graph_id, (SELECT COUNT(*) FROM Graph_KG.rdf_labels l WHERE l.s = n.node_id AND l.graph_id = "
        "n.graph_id AND l.label = ?), p.val FROM Graph_KG.nodes n JOIN Graph_KG.rdf_props p ON p.s = n.node_id AND "
        "p.graph_id = n.graph_id AND p.\"key\" = 'mk' ORDER BY 1",
        [label],
    )
    return sorted((g or "", int(c), str(v)) for g, c, v in cur.fetchall())


@pytest.fixture
def merge_clean(engine):
    def clean():
        engine.erase_graph(GA)
        engine.execute_cypher("MATCH (n:M65) DETACH DELETE n", {}, raise_on_error=True)
        engine.conn.commit()

    clean()
    yield engine
    clean()


def test_a_merge_under_use_graph_creates_in_its_graph_whatever_the_default_graph_holds(merge_clean):
    eng = merge_clean
    eng.execute_cypher(f"USE GRAPH '{GA}' MERGE (n:M65 {{mk: 1}})", {}, raise_on_error=True)
    eng.conn.commit()
    # node, label and property all in r65a (the label went to the default graph: SQLCODE -121)
    assert _merge_rows(eng, "M65") == [(GA, 1, "1")]
    eng.execute_cypher("CREATE (:M65 {mk: 2})", {}, raise_on_error=True)
    eng.execute_cypher(f"USE GRAPH '{GA}' MERGE (n:M65 {{mk: 2}})", {}, raise_on_error=True)
    eng.conn.commit()
    # the default graph's (:M65 {mk: 2}) is not r65a's: r65a gets its own
    assert _merge_rows(eng, "M65") == [("", 1, "2"), (GA, 1, "1"), (GA, 1, "2")]


def test_a_merge_with_no_use_graph_matches_the_default_graph_only(merge_clean):
    eng = merge_clean
    eng.execute_cypher(f"USE GRAPH '{GA}' CREATE (:M65 {{mk: 1}})", {}, raise_on_error=True)
    eng.conn.commit()
    r = eng.execute_cypher("MERGE (n:M65 {mk: 1}) ON CREATE SET n.c = 1 RETURN n.mk", {}, raise_on_error=True)
    eng.conn.commit()
    # r65a's node does not stop the default graph's create, and the RETURN answers the default graph's node only
    assert [list(row) for row in r.rows] == [[1]]
    assert _merge_rows(eng, "M65") == [("", 1, "1"), (GA, 1, "1")]
    r = eng.execute_cypher("MERGE (n:M65 {mk: 1}) ON MATCH SET n.z = 5 RETURN n.z", {}, raise_on_error=True)
    eng.conn.commit()
    assert [list(row) for row in r.rows] == [[5]]
    cur = eng.conn.cursor()
    cur.execute(
        "SELECT graph_id, \"key\" FROM Graph_KG.rdf_props WHERE \"key\" IN ('c', 'z') AND s IN "
        "(SELECT s FROM Graph_KG.rdf_labels WHERE label = 'M65') ORDER BY 2"
    )
    assert [(g or "", k) for g, k in cur.fetchall()] == [("", "c"), ("", "z")]


def test_merge_on_match_set_a_node_to_a_map_is_refused(merge_clean):
    with pytest.raises(Exception, match="not supported"):
        merge_clean.execute_cypher("MERGE (n:M65 {mk: 1}) ON MATCH SET n = {z: 1}", {}, raise_on_error=True)


def test_a_set_then_return_answers_each_graphs_node(engine):
    _setup(engine, (DG, GA, GX))
    r = engine.execute_cypher(
        "MATCH (n:A {node_id: $id}) SET n.z = 4 RETURN n.z ORDER BY n.z", IDS, raise_on_error=True
    )
    engine.conn.commit()
    assert [list(row) for row in r.rows] == [[4], [4]]


def test_a_return_after_a_set_of_the_matched_key_answers_each_graphs_node(engine):
    _setup(engine, (DG, GA, GX))
    r = engine.execute_cypher("MATCH (n:A {node_id: $id, k: 1}) SET n.k = 5 RETURN n.k", IDS, raise_on_error=True)
    engine.conn.commit()
    assert [list(row) for row in r.rows] == [[5], [5]]
