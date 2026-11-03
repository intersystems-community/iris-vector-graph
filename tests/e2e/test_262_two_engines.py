"""Spec 262: two engines on two IVG Schemas in one process each read and write only their own.

Engine A works in IVG262ALT on schema `IVGALT`; engine B in IVG262UPG on `Graph_KG` (hipporag2-pipeline's shape: two
engines, two schemas, one process). Both are built before either is used, A first, so under the old process-wide schema
(the last engine built won) A's statements named `Graph_KG`, which IVG262ALT does not have. Then writes and reads
interleave, and each engine's answers and tables hold only its own rows.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS_A, SCHEMA_A = "IVG262ALT", "IVGALT"
NS_B, SCHEMA_B = "IVG262UPG", "Graph_KG"


@pytest.fixture(scope="module")
def pair():
    from iris_vector_graph import IRISGraphEngine

    out = {}
    for key, ns, schema in (("a", NS_A, SCHEMA_A), ("b", NS_B, SCHEMA_B)):
        ns262.recreate(ns)
        conn = ns262.connect(ns)
        IRISGraphEngine(conn, embedding_dimension=4, namespace=ns, schema_prefix=schema).initialize_schema()
        out[key] = {"conn": conn, "ns": ns, "schema": schema}
    # Both built before either is used, A first: the order the old process-wide schema got wrong for A.
    for key in ("a", "b"):
        side = out[key]
        side["engine"] = IRISGraphEngine(side["conn"], embedding_dimension=4, namespace=side["ns"],
                                         schema_prefix=side["schema"], native_cypher=False)
    yield out
    for side in out.values():
        side["conn"].close()


def _cy(side, q, params=None):
    r = side["engine"].execute_cypher(q, params or {}, raise_on_error=True)
    assert ns262.names_outside(r.sql or "", side["schema"]) == [], r.sql
    return [list(row) for row in r.rows]


def _labels(side, node_id):
    """The labels the engine's store reads for `node_id`, one list per row."""
    rows = side["engine"]._store.get_nodes([node_id]).rows
    return [json.loads(r[1]) if isinstance(r[1], str) else list(r[1]) for r in rows]


def _count(side, sql):
    return ns262.rows(side["conn"], sql.format(s=side["schema"]))[0][0]


def test_interleaved_writes_land_in_each_engines_own_schema(pair):
    a, b = pair["a"], pair["b"]
    a["engine"].create_node("two:a1", labels=["TwoA"], properties={"name": "a1"})
    b["engine"].create_node("two:b1", labels=["TwoB"], properties={"name": "b1"})
    _cy(a, "CREATE (:TwoA {name: 'a2'})")
    _cy(b, "CREATE (:TwoB {name: 'b2'})")
    a["engine"].create_edge("two:a1", "TWO_A", "two:a1")
    b["engine"].bulk_create_nodes([{"id": "two:b3", "labels": ["TwoB"], "properties": {"name": "b3"}}])

    assert _count(a, "SELECT COUNT(*) FROM {s}.rdf_labels WHERE label = 'TwoA'") == 2
    assert _count(a, "SELECT COUNT(*) FROM {s}.rdf_labels WHERE label = 'TwoB'") == 0
    assert _count(b, "SELECT COUNT(*) FROM {s}.rdf_labels WHERE label = 'TwoB'") == 3
    assert _count(b, "SELECT COUNT(*) FROM {s}.rdf_labels WHERE label = 'TwoA'") == 0
    assert _count(a, "SELECT COUNT(*) FROM {s}.rdf_edges WHERE p = 'TWO_A'") == 1
    # IVG262ALT holds no Graph_KG schema for a wrong statement to have landed in
    assert "GRAPH_KG" not in {s.upper() for s in ns262.schemas(a["conn"])}


def test_interleaved_reads_answer_from_each_engines_own_rows(pair):
    a, b = pair["a"], pair["b"]
    for _ in range(2):
        assert sorted(_cy(a, "MATCH (n:TwoA) RETURN n.name")) == [["a1"], ["a2"]]
        assert sorted(_cy(b, "MATCH (n:TwoB) RETURN n.name")) == [["b1"], ["b2"], ["b3"]]
        assert _cy(a, "MATCH (n:TwoB) RETURN count(n)") == [[0]]
        assert _cy(b, "MATCH (n:TwoA) RETURN count(n)") == [[0]]
        assert a["engine"].get_labels().count("TwoA") == 1 and "TwoB" not in a["engine"].get_labels()
        assert b["engine"].get_labels().count("TwoB") == 1 and "TwoA" not in b["engine"].get_labels()
        assert _labels(a, "two:a1") == [["TwoA"]]
        assert _labels(b, "two:a1") in ([], [[]])  # B's store knows no such node


def test_interleaved_deletes_remove_only_each_engines_rows(pair):
    a, b = pair["a"], pair["b"]
    _cy(a, "MATCH (n:TwoA {name: 'a2'}) DETACH DELETE n")
    b["engine"]._store.delete_nodes(["two:b3"])
    assert sorted(_cy(a, "MATCH (n:TwoA) RETURN n.name")) == [["a1"]]
    assert sorted(_cy(b, "MATCH (n:TwoB) RETURN n.name")) == [["b1"], ["b2"]]
    assert _count(a, "SELECT COUNT(*) FROM {s}.nodes") == 1
    assert _count(b, "SELECT COUNT(*) FROM {s}.nodes") == 2
