"""4.6.0 R061: `shortestPath` over an incoming pattern, `(a)<-[*]-(b)`, answered no rows (4.5.x too).

The walkthrough's graph (as tests/e2e/test_shortest_path_projection_e2e.py builds it): A→B→C→D→E, A→F→E, E→G→H→I,
I→E, and the cycle J→K→L→J, every edge LINK. Worked by hand:

* `(a {name:'I'})<-[*]-(b {name:'A'})` walks the edges backwards from I: I←H←G←E←F←A, five relationships, the mirror
  of the outgoing A→F→E→G→H→I (A→B→C→D→E is longer). The path starts at `a`: nodes I, H, G, E, F, A.
* `(a {name:'J'})<-[*]-(b {name:'K'})`: K reaches J over K→L→J, so J←L←K, two relationships (outgoing, J→K is one).
* `(a {name:'A'})<-[*]-(b {name:'I'})`: nothing reaches A, so no path (an undirected walk would find one).

The ObjectScript routine walked an "in" direction over neither ^KG("out") nor ^KG("in").
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

LABEL = "SpIn460"
NAMES = list("ABCDEFGHIJKL")
EDGES = [
    ("A", "B"), ("B", "C"), ("C", "D"), ("D", "E"), ("A", "F"), ("F", "E"), ("E", "G"),
    ("G", "H"), ("H", "I"), ("I", "E"), ("J", "K"), ("K", "L"), ("L", "J"),
]


def _id(x: str) -> str:
    return f"spin460:{x}"


@pytest.fixture(scope="module")
def engine(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.bulk_create_nodes([{"id": _id(x), "labels": [LABEL], "properties": {"name": x}} for x in NAMES])
    e.bulk_create_edges([{"source_id": _id(s), "predicate": "LINK", "target_id": _id(t)} for s, t in EDGES])
    try:
        yield e
    finally:
        for x in NAMES:
            e.delete_node(_id(x), graph=None)


def _value(cell):
    return json.loads(cell) if isinstance(cell, str) and cell[:1] in "[{" else cell


def _answer(engine, cypher):
    r = engine.execute_cypher(cypher)
    assert not getattr(r, "error", None), r.error
    return r.columns, [[_value(c) for c in row] for row in r.rows]


def _by_name(a: str, b: str, arrow: str = "<-[*]-") -> str:
    return f"MATCH p = shortestPath((a:{LABEL} {{name:'{a}'}}){arrow}(b:{LABEL} {{name:'{b}'}})) "


def _by_id(a: str, b: str, arrow: str = "<-[*]-") -> str:
    return f"MATCH p = shortestPath((a {{node_id:'{_id(a)}'}}){arrow}(b {{node_id:'{_id(b)}'}})) "


MIRROR = ["I", "H", "G", "E", "F", "A"]


@pytest.mark.parametrize("match", [_by_name, _by_id], ids=["by-name", "by-id"])
class TestTheIncomingPathIsTheMirror:
    def test_length_and_names(self, engine, match):
        assert _answer(engine, match("I", "A") + "RETURN length(p) AS len, [n IN nodes(p) | n.name] AS names") == (
            ["len", "names"],
            [[5, MIRROR]],
        )

    def test_length_alone(self, engine, match):
        assert _answer(engine, match("I", "A") + "RETURN length(p)") == (["length(p)"], [[5]])

    def test_the_path(self, engine, match):
        assert _answer(engine, match("I", "A") + "RETURN p") == (
            ["p"],
            [[{"nodes": [_id(x) for x in MIRROR], "rels": ["LINK"] * 5}]],
        )

    def test_the_cycle_the_other_way_round(self, engine, match):
        assert _answer(engine, match("J", "K") + "RETURN length(p) AS len, [n IN nodes(p) | n.name] AS names") == (
            ["len", "names"],
            [[2, ["J", "L", "K"]]],
        )

    def test_no_path_against_the_edges(self, engine, match):
        assert _answer(engine, match("A", "I") + "RETURN length(p) AS len")[1] == []

    def test_the_outgoing_form_is_unchanged(self, engine, match):
        assert _answer(engine, match("A", "I", "-[*]->") + "RETURN length(p) AS len, [n IN nodes(p) | n.name] AS n") == (
            ["len", "n"],
            [[5, list(reversed(MIRROR))]],
        )


def test_projected_relationships_of_an_incoming_path(engine):
    # the projection reads each relationship's stored row: on an incoming path it runs from the later node
    cols, rows = _answer(engine, _by_id("J", "K") + "RETURN relationships(p) AS rs, b.name AS b")
    assert (cols, rows) == (["rs", "b"], [[[{"type": "LINK", "props": {}}] * 2, "K"]])
    # L-VIA->X (since 2020), X-VIA->Y: from Y backwards the path is Y<-X<-L, so X->Y comes first
    for x in ("X", "Y"):
        engine.create_node(_id(x), labels=[LABEL], properties={"name": x})
    try:
        engine.create_edge(_id("L"), "VIA", _id("X"), qualifiers={"since": 2020})
        engine.create_edge(_id("X"), "VIA", _id("Y"))
        cols, rows = _answer(
            engine,
            f"MATCH p = shortestPath((a {{node_id:'{_id('Y')}'}})<-[:VIA*]-(b {{node_id:'{_id('L')}'}})) "
            "RETURN relationships(p) AS rs, [n IN nodes(p) | n.name] AS names",
        )
        assert (cols, rows) == (
            ["rs", "names"],
            [[[{"type": "VIA", "props": {}}, {"type": "VIA", "props": {"since": 2020}}], ["Y", "X", "L"]]],
        )
    finally:
        for x in ("X", "Y"):
            engine.delete_node(_id(x), graph=None)
