"""4.6.0 R186: the writes of a query part read the ids its MATCH bound once, before the first of them.

Each statement re-ran the MATCH as its own subquery, so once one had changed what the MATCH filters on the later ones
found nothing: `MATCH (n:X {k: 1}) SET n.k = 2, n.q = 3` never wrote `q`, and `SET n.k = 2 DELETE n` deleted nothing.
The translator now emits one `__capture_ids__ <key>` select per bound variable ahead of the writes, and every write
names `__IDS_<key>__`, which the executor fills with what the capture read.

These pin the statements; tests/e2e/test_460_set_pinned_bindings_e2e.py checks the stored graph.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.unit._one_graph import ONE_GRAPH
from iris_vector_graph.stores.iris_sql_store import _expand_captured_ids

_SCHEMA_QUALIFIED = re.compile(r"\b\w+\.(rdf_props|rdf_edges|rdf_labels|nodes|kg_NodeEmbeddings)\b")
_WRITE = re.compile(r"\s*(WITH .*?\)\s*)?(UPDATE|INSERT|DELETE)\b", re.S)


def _stmts(q, params=None):
    # one graph: the shapes pinned here are a one-graph namespace's (a namespace of several: tests/e2e/
    # test_460_all_graphs_writes_e2e.py)
    r = translate_to_sql(parse_query(q), params or {}, engine=ONE_GRAPH)
    sql = r.sql if isinstance(r.sql, list) else [r.sql]
    return [(_SCHEMA_QUALIFIED.sub(r"\1", s), p) for s, p in zip(sql, r.parameters)]


def _captures(stmts):
    return [s.split("\n", 1)[0].split()[1] for s, _ in stmts if s.startswith("__capture_ids__ ")]


def _writes(stmts):
    return [(s, p) for s, p in stmts if _WRITE.match(s)]


_MARKER_ROWS = 'SELECT _rm.s FROM rdf_props _rm WHERE _rm."key" = ?'
_MARKER_CLEANUP = 'DELETE FROM rdf_props WHERE "key" = ?'


def _assert_pinned(stmts, n_captures=1):
    """`n_captures` captures, all ahead of the first write; every write reads one and none re-runs the MATCH.

    `SET n = {map}` and `SET n += {map}` select through the marker rows their first write (which reads the capture)
    inserted, and delete those rows last."""
    caps = _captures(stmts)
    assert len(caps) == n_captures, stmts
    first_write = next(i for i, (s, _) in enumerate(stmts) if _WRITE.match(s))
    assert all(i < first_write for i, (s, _) in enumerate(stmts) if s.startswith("__capture_ids__ ")), stmts
    for s, _ in _writes(stmts):
        assert any(f"__IDS_{k}__" in s for k in caps) or _MARKER_ROWS in s or s == _MARKER_CLEANUP, s
        assert not re.search(r"\bFROM nodes n\d+\b|\bFROM rdf_edges e\d+\b", s), s
    return caps


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:X {k: 1}) SET n.k = 2, n.q = 3",
        "MATCH (n:X {k: 1}) SET n.k = 2 REMOVE n.keep",
        "MATCH (n:X {k: 1}) SET n.k = 2 SET n.q = 3",
        "MATCH (n:X {k: 1}) SET n.k = 2, n += {q: 3}",
        "MATCH (n:X {k: 1}) SET n.k = 2, n:Y",
        "MATCH (n:X) REMOVE n:X SET n.q = 3",
        "MATCH (n:X {k: 1}) SET n.k = 2, n = {q: 3}",
    ],
)
def test_one_capture_for_every_write_of_a_node(q):
    _assert_pinned(_stmts(q))


def test_the_capture_reads_the_match():
    stmts = _stmts("MATCH (n:X {k: 1}) SET n.k = 2, n.q = 3")
    cap = next((s, p) for s, p in stmts if s.startswith("__capture_ids__ "))
    assert re.search(r"\nSELECT n0\.node_id\b", cap[0]) and "rdf_labels l1" in cap[0], cap
    assert cap[1] == ["X", "k"], cap
    # the values bind as before: 2 for k, 3 for q
    vals = [p for s, p in _writes(stmts) if s.startswith("UPDATE rdf_props SET val = Graph_KG.Prop_Set(%ID, val, ?)")]
    assert vals == [[2, "k"], [3, "q"]], vals


def test_a_cross_product_captures_each_variable():
    stmts = _stmts("MATCH (a:X {k: 1}), (b:X) SET a.k = 2, b.k = 3")
    caps = _assert_pinned(stmts, n_captures=2)
    a_writes = [s for s, _ in _writes(stmts) if f"__IDS_{caps[0]}__" in s]
    b_writes = [s for s, _ in _writes(stmts) if f"__IDS_{caps[1]}__" in s]
    assert len(a_writes) == 2 and len(b_writes) == 2, stmts


def test_a_relationship_is_captured_by_edge_id():
    stmts = _stmts("MATCH ()-[r:T {k: 1}]->() SET r.k = 2, r.q = 3")
    caps = _assert_pinned(stmts)
    cap = next(s for s, _ in stmts if s.startswith("__capture_ids__ "))
    assert re.search(r"\nSELECT e\d+\.edge_id\b", cap), cap
    ups = [s for s, _ in _writes(stmts)]
    assert len(ups) == 2 and all(f"WHERE edge_id IN (__IDS_{caps[0]}__)" in s for s in ups), ups


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:X {k: 1}) SET n.k = 2 DELETE n",
        "MATCH (n:X {k: 1}) SET n.k = 2 DETACH DELETE n",
        "MATCH ()-[r:T {k: 1}]->() SET r.k = 2 DELETE r",
    ],
)
def test_a_delete_after_a_set_reads_the_set_s_capture(q):
    stmts = _stmts(q)
    _assert_pinned(stmts)
    assert any(s.startswith("DELETE FROM") for s, _ in stmts), stmts


def test_merge_on_match_captures_before_it_creates():
    """ON MATCH writes the nodes that existed before MERGE: captured ahead of the node insert, so the node MERGE
    makes is not written, and the second item still finds the node after the first changed `k`."""
    stmts = _stmts("MERGE (n:X {k: 1}) ON MATCH SET n.k = 2, n.q = 3")
    caps = _captures(stmts)
    assert len(caps) == 1, stmts
    assert stmts[0][0].startswith("__capture_ids__ "), stmts
    # in the default graph, the MERGE's: graph_id = '' (and the INSERT's row carries it)
    on_match = [
        s
        for s, p in _writes(stmts)
        if s.startswith(("UPDATE rdf_props", "INSERT INTO rdf_props (graph_id, s, \"key\", val) SELECT '', node_id"))
    ]
    assert len(on_match) == 4 and all(f"__IDS_{caps[0]}__" in s and "graph_id = ''" in s for s in on_match), stmts


def test_merge_after_a_match_captures_after_it_creates():
    """`MATCH (p:Person) MERGE (c:City) ON MATCH SET c.name = p.bornIn`: the rows after the first match the City the
    first created, so ON MATCH writes it too (Merge3 [4], Merge4 [1]); the capture follows the inserts."""
    stmts = _stmts("MATCH (p:Person) MERGE (c:City) ON MATCH SET c.name = 'x', c.k = 1")
    caps = [i for i, (s, _) in enumerate(stmts) if s.startswith("__capture_ids__ ")]
    inserts = [i for i, (s, _) in enumerate(stmts) if s.startswith("INSERT INTO nodes")]
    assert len(caps) == 1 and inserts and caps[0] > max(inserts), stmts


@pytest.mark.parametrize(
    "q",
    [
        "MATCH (n:X) SET n.k = 2, n.q = 3",
        "MATCH ()-[r:T]->() SET r.k = 2",
        "MATCH (n:X) SET n += {k: 2}",
        "MATCH (n:X) REMOVE n.k",
        "MATCH (n) WHERE n.k = 1 SET n:Y",
    ],
)
def test_a_match_no_write_can_change_keeps_its_subquery(q):
    """A MATCH that reads no property (or no label, for a label write) selects the same rows after any write of the
    query: its writes keep selecting through it, one statement for however many nodes, and no ids are captured."""
    stmts = _stmts(q)
    assert not _captures(stmts), stmts
    assert all("__IDS_" not in s for s, _ in stmts), stmts


def test_a_set_after_a_delete_reads_the_rows_before_it():
    """`DELETE r` takes the MATCH's rows with it: the SET after it reads a capture taken before the DELETE."""
    stmts = _stmts("MATCH (a:X)-[r:T]->() DELETE r SET a.done = true")
    caps = _captures(stmts)
    first_write = next(i for i, (s, _) in enumerate(stmts) if _WRITE.match(s))
    assert caps and all(i < first_write for i, (s, _) in enumerate(stmts) if s.startswith("__capture_ids__ "))
    sets = [s for s, _ in _writes(stmts) if "rdf_props" in s]
    assert sets and all(any(f"__IDS_{k}__" in s for k in caps) for s in sets), stmts


def test_capture_tokens_expand_to_the_captured_ids():
    stmt = "UPDATE rdf_props SET val = ? WHERE s IN (__IDS_p0__) AND \"key\" = ?"
    assert _expand_captured_ids(stmt, {"p0": ["a", "b'c"]}) == [
        "UPDATE rdf_props SET val = ? WHERE s IN ('a', 'b''c') AND \"key\" = ?"
    ]
    assert _expand_captured_ids(stmt, {"p0": []}) == [
        "UPDATE rdf_props SET val = ? WHERE s IN (NULL) AND \"key\" = ?"
    ]
