"""4.6.0 R308, live: an OPTIONAL MATCH over a variable-length hop answers openCypher's rows, or raises naming its shape.

Found by oracle-cypher's differential (tests/e2e/test_460_r308_cypher_differential_e2e.py): the translator leaves an
OPTIONAL hop it cannot fit in one LEFT JOIN to the engine, and the engine's labeled route answered it with rows of the
wrong width and the start nulled. `MATCH (a:L {name: 'cat'}) OPTIONAL MATCH (a)-[:R*1..2]->(b) WHERE b.name = 'eve'
RETURN a.name, b.name` answered [['dan'], ['eve'], [None, None], [None, None]], where openCypher answers
[('cat', 'eve')].

The graph, worked by hand (node ids `f63:<name>`, every node has `name` and a number `rank`: ann 1, cat 9, dan 10,
eve 2, gus 11, hal 5, ivy 50):

    default graph:  ann :F63L        cat :F63L        dan :F63M        eve :F63X        gus :F63L
                    gus -F63R-> cat -F63R-> dan -F63R-> eve            (ann has no relationship)
    graph g63:      hal :F63L -F63R-> ivy :F63X        dan :F63M (the default graph's id; no relationship)

Walks of `-[:F63R*1..2]->` (every graph; a read without USE GRAPH reads them all): ann none; cat dan (1), eve (2);
gus cat (1), dan (2); hal ivy (1). Into cat (`<-[:F63R*1..2]-`): gus. Undirected from cat: dan, eve (cat-dan-eve),
gus. Every case's answer is below, worked from these walks; a start with no path that passes the WHERE keeps one row
with nulls for what the OPTIONAL MATCH binds. Row order is compared as a multiset.

The namespace is `IVG_NAMESPACE` (an IVG460F* scratch namespace with IVG set up). Native on and off: the native
executor takes no variable-length OPTIONAL hop, so both answer through SQL or the engine's routes.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

HOP = "-[:F63R*1..2]->"
CAT = "MATCH (a:F63L {name: 'cat'}) "
ALL = "MATCH (a:F63L) "


#: (name, query, the rows worked by hand)
CASES = [
    # a WHERE on the new end
    ("where on the new end", CAT + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'eve' RETURN a.name, b.name",
     [("cat", "eve")]),
    ("where on the new end, no match", CAT + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'zed' RETURN a.name, b.name",
     [("cat", None)]),
    ("where on the new end, every start", ALL + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'dan' RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("gus", "dan"), ("hal", None)]),
    ("where with a parameter", CAT + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = $n RETURN a.name, b.name",
     [("cat", "eve")]),
    # dan > cat, eve > cat; cat < gus, dan < gus; ivy > hal
    ("where reads both ends", ALL + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name > a.name RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("cat", "eve"), ("gus", None), ("hal", "ivy")]),
    # ranks, stored as numbers' text: cat 9 < dan 10 (as text '10' < '9'), eve 2; gus 11; hal 5 < ivy 50
    ("where orders two numbers", ALL + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.rank > a.rank RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("gus", None), ("hal", "ivy")]),
    ("single hop: where reads both ends", ALL + "OPTIONAL MATCH (a)-[:F63R]->(b) WHERE b.name > a.name "
     "RETURN a.name, b.name", [("ann", None), ("cat", "dan"), ("gus", None), ("hal", "ivy")]),
    ("single hop: where reads both ends, <", ALL + "OPTIONAL MATCH (a)-[:F63R]->(b) WHERE b.name < a.name "
     "RETURN a.name, b.name", [("ann", None), ("cat", None), ("gus", "cat"), ("hal", None)]),
    ("single hop: where orders two numbers", ALL + "OPTIONAL MATCH (a)-[:F63R]->(b) WHERE b.rank >= a.rank "
     "RETURN a.name, b.name", [("ann", None), ("cat", "dan"), ("gus", None), ("hal", "ivy")]),
    # gus -> cat -> dan: the second OPTIONAL MATCH's start is the first one's new node
    ("single hop: an optional start, where reads both ends", "MATCH (x:F63L {name: 'gus'}) OPTIONAL MATCH "
     "(x)-[:F63R]->(a) OPTIONAL MATCH (a)-[:F63R]->(b) WHERE b.name > a.name RETURN a.name, b.name",
     [("cat", "dan")]),
    ("a number and a string never order", CAT + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.rank > a.name "
     "RETURN a.name, b.name", [("cat", None)]),
    ("where, then count", ALL + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'dan' RETURN a.name, count(b)",
     [("ann", 0), ("cat", 1), ("gus", 1), ("hal", 0)]),
    ("where, incoming", CAT + "OPTIONAL MATCH (a)<-[:F63R*1..2]-(b) WHERE b.name = 'gus' RETURN a.name, b.name",
     [("cat", "gus")]),
    ("where, undirected", CAT + "OPTIONAL MATCH (a)-[:F63R*1..2]-(b) WHERE b.name <> 'dan' RETURN a.name, b.name",
     [("cat", "eve"), ("cat", "gus")]),
    ("where, the relationship list", CAT + "OPTIONAL MATCH (a)-[r:F63R*1..2]->(b) WHERE b.name = 'eve' "
     "RETURN a.name, b.name, size(r)", [("cat", "eve", 2)]),
    ("where, the relationship list, no match", CAT + "OPTIONAL MATCH (a)-[r:F63R*1..2]->(b) WHERE b.name = 'zed' "
     "RETURN a.name, b.name, size(r)", [("cat", None, None)]),
    ("where, a named path", CAT + f"OPTIONAL MATCH p = (a){HOP}(b) WHERE b.name = 'eve' RETURN a.name, length(p)",
     [("cat", 2)]),
    # a map or labels on the new end
    ("a map on the new end", ALL + f"OPTIONAL MATCH (a){HOP}(b {{name: 'dan'}}) RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("gus", "dan"), ("hal", None)]),
    ("a map on the new end, no match", CAT + f"OPTIONAL MATCH (a){HOP}(b {{name: 'zed'}}) RETURN a.name, b.name",
     [("cat", None)]),
    ("labels on the new end", ALL + f"OPTIONAL MATCH (a){HOP}(b:F63M) RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("gus", "dan"), ("hal", None)]),
    ("labels and a map on the new end", ALL + f"OPTIONAL MATCH (a){HOP}(b:F63X {{name: 'eve'}}) RETURN a.name, b.name",
     [("ann", None), ("cat", "eve"), ("gus", None), ("hal", None)]),
    # the start's labels or map, in the OPTIONAL MATCH
    ("the start's labels", ALL + f"OPTIONAL MATCH (a:F63L){HOP}(b) RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("cat", "eve"), ("gus", "cat"), ("gus", "dan"), ("hal", "ivy")]),
    ("the start's map", ALL + f"OPTIONAL MATCH (a {{name: 'cat'}}){HOP}(b) RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("cat", "eve"), ("gus", None), ("hal", None)]),
    ("a label the start lacks", CAT + f"OPTIONAL MATCH (a:F63M){HOP}(b) RETURN a.name, b.name", [("cat", None)]),
    # inside CALL {}
    ("inside CALL, a where", ALL + f"CALL {{ WITH a OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'eve' RETURN b }} "
     "RETURN a.name, b.name", [("ann", None), ("cat", "eve"), ("gus", None), ("hal", None)]),
    ("inside CALL, a map", ALL + f"CALL {{ WITH a OPTIONAL MATCH (a){HOP}(b {{name: 'dan'}}) RETURN b }} "
     "RETURN a.name, b.name", [("ann", None), ("cat", "dan"), ("gus", "dan"), ("hal", None)]),
    # one graph
    ("USE GRAPH, a where", "USE GRAPH 'g63' " + ALL + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'ivy' "
     "RETURN a.name, b.name", [("hal", "ivy")]),
    ("USE GRAPH, a where, no match", "USE GRAPH 'g63' " + ALL + f"OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'eve' "
     "RETURN a.name, b.name", [("hal", None)]),
    # a start named by id
    ("an id-bound start, a where", "MATCH (a {node_id: 'f63:cat'}) " + f"OPTIONAL MATCH (a){HOP}(b) "
     "WHERE b.name = 'eve' RETURN a.name, b.name", [("cat", "eve")]),
    ("a leading OPTIONAL from an id", f"OPTIONAL MATCH (a {{node_id: 'f63:cat'}}){HOP}(b) RETURN a.name, b.name",
     [("cat", "dan"), ("cat", "eve")]),
    ("a leading OPTIONAL from an id, no match", f"OPTIONAL MATCH (a {{node_id: 'f63:ann'}}){HOP}(b) "
     "RETURN a.name, b.name", [(None, None)]),
    ("a leading OPTIONAL from an id, a where", f"OPTIONAL MATCH (a {{node_id: 'f63:cat'}}){HOP}(b) "
     "WHERE b.name = 'eve' RETURN b.name", [("eve",)]),
    # a leading OPTIONAL MATCH: one row of nulls when nothing matches
    ("a leading OPTIONAL, a where", f"OPTIONAL MATCH (a:F63L {{name: 'cat'}}){HOP}(b) WHERE b.name = 'eve' "
     "RETURN a.name, b.name", [("cat", "eve")]),
    ("a leading OPTIONAL, a where, no match", f"OPTIONAL MATCH (a:F63L {{name: 'cat'}}){HOP}(b) WHERE b.name = 'zed' "
     "RETURN a.name, b.name", [(None, None)]),
    ("a leading OPTIONAL, a map on the new end", f"OPTIONAL MATCH (a:F63L){HOP}(b {{name: 'dan'}}) "
     "RETURN a.name, b.name", [("cat", "dan"), ("gus", "dan")]),
    ("a leading OPTIONAL from an id, a map on the new end, no match",
     f"OPTIONAL MATCH (a {{node_id: 'f63:cat'}}){HOP}(b {{name: 'zed'}}) RETURN a.name, b.name", [(None, None)]),
    # the OPTIONAL MATCH binds the start; its end is bound before
    ("a new start, a bound end", "MATCH (x:F63L {name: 'cat'}) OPTIONAL MATCH (a:F63L)-[:F63R*1..2]->(x) "
                 "RETURN a.name, x.name", [("gus", "cat")]),
    ("a new start, a bound end, no match", "MATCH (x:F63L {name: 'ann'}) OPTIONAL MATCH "
                 "(a:F63L)-[:F63R*1..2]->(x) RETURN a.name, x.name", [(None, "ann")]),
    # the default graph's dan walks back to cat (1), gus (2); g63's dan has no relationship
    ("a new start with a map, a bound end", "MATCH (x:F63M) OPTIONAL MATCH (a {name: 'gus'})-[:F63R*1..2]->(x) "
     "RETURN a.name, x.name", [("gus", "dan"), (None, "dan")]),
    ("a new start, its relationships read", "MATCH (x:F63L {name: 'cat'}) OPTIONAL MATCH (a:F63L)-[r:F63R*1..2]->(x) "
     "RETURN a.name, size(r)", [("gus", 1)]),
    ("the documented rewrite of it", "MATCH (x:F63L {name: 'cat'}) OPTIONAL MATCH (x)<-[r:F63R*1..2]-(a:F63L) "
     "RETURN a.name, size(r)", [("gus", 1)]),
    # an anonymous end with a label: cat -> dan (1), gus -> cat -> dan (2)
    ("labels on an anonymous end", ALL + "OPTIONAL MATCH (a)-[r:F63R*1..2]->(:F63M) RETURN a.name, size(r)",
     [("ann", None), ("cat", 1), ("gus", 2), ("hal", None)]),
    # another hop after the variable-length one: cat -> dan -> eve; gus -> cat -> dan, gus -> cat -> dan -> eve
    ("another hop in the pattern", ALL + f"OPTIONAL MATCH (a){HOP}(b)-[:F63R]->(c) RETURN a.name, b.name, c.name",
     [("ann", None, None), ("cat", "dan", "eve"), ("gus", "cat", "dan"), ("gus", "dan", "eve"),
      ("hal", None, None)]),
    # controls: shapes the translator already answered in SQL
    ("control: no where", ALL + f"OPTIONAL MATCH (a){HOP}(b) RETURN a.name, b.name",
     [("ann", None), ("cat", "dan"), ("cat", "eve"), ("gus", "cat"), ("gus", "dan"), ("hal", "ivy")]),
    ("control: size(r), a start with no path", ALL + "OPTIONAL MATCH (a)-[r:F63R*1..2]->(b) RETURN a.name, size(r)",
     [("ann", None), ("cat", 1), ("cat", 2), ("gus", 1), ("gus", 2), ("hal", 1)]),
    ("control: the documented rewrite", CAT + f"OPTIONAL MATCH (a){HOP}(b) WITH a, b WHERE b.name = 'eve' "
     "RETURN a.name, b.name", [("cat", "eve")]),
]

#: The shapes still refused (a ValueError naming the shape and the rewrite), until each answers its rows.
STILL_REFUSED = {
    # a new start walked back from its bound end: the relationships would read in the walk's order, not the pattern's
    "a new start, its relationships read",
}

NODES = [("ann", "F63L"), ("cat", "F63L"), ("dan", "F63M"), ("eve", "F63X"), ("gus", "F63L")]
RANKS = {"ann": 1, "cat": 9, "dan": 10, "eve": 2, "gus": 11, "hal": 5, "ivy": 50}
EDGES = [("gus", "cat"), ("cat", "dan"), ("dan", "eve")]


def _clean(engine):
    for name, _ in NODES:
        engine.delete_node(f"f63:{name}", graph=None)  # the default graph's, with its relationships
    for name in ("hal", "ivy", "dan"):
        engine.delete_node(f"f63:{name}", graph="g63")


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    _clean(sql)
    for name, label in NODES:
        sql.create_node(f"f63:{name}", labels=[label], properties={"name": name})
    for s, o in EDGES:
        sql.create_edge(f"f63:{s}", "F63R", f"f63:{o}")
    sql.create_node("f63:hal", labels=["F63L"], properties={"name": "hal"}, graph="g63")
    sql.create_node("f63:ivy", labels=["F63X"], properties={"name": "ivy"}, graph="g63")
    sql.create_edge("f63:hal", "F63R", "f63:ivy", graph="g63")
    for name, rank in RANKS.items():
        # a Cypher SET stores the number as a number (a create_node property reads back as text, KNOWN_ISSUES)
        use = "USE GRAPH 'g63' " if name in ("hal", "ivy") else ""
        sql.execute_cypher(f"{use}MATCH (n {{node_id: $id}}) SET n.rank = $r", {"id": f"f63:{name}", "r": rank},
                           raise_on_error=True)
    # dan's id in g63 too, with no relationship: a default-graph path ends at the default graph's dan only
    sql.create_node("f63:dan", labels=["F63M"], properties={"name": "dan"}, graph="g63")
    conn.commit()
    try:
        yield {"native": nat, "sql": sql}
    finally:
        _clean(sql)
        conn.commit()
        conn.close()


def _key(row):
    return tuple((v is None, "" if v is None else str(v)) for v in row)


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c.values[0] if hasattr(c, "values") else c[0] for c in CASES])
def test_an_optional_variable_length_hop_answers_its_rows(engines, path, name, query, want):
    if name in STILL_REFUSED:
        with pytest.raises(ValueError, match=r"OPTIONAL MATCH .* WITH a, b WHERE"):
            engines[path].execute_cypher(query, {"n": "eve"}, raise_on_error=True)
        return
    result = engines[path].execute_cypher(query, {"n": "eve"}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    got = [tuple(r) for r in result.rows]
    assert sorted(got, key=_key) == sorted(want, key=_key)
