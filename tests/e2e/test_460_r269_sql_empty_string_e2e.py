"""4.6.0 R269, live: an SQL-callable IVG method given SQL's empty string `''` answers as for an empty string.

IRIS hands SQL's `''` to an ObjectScript method as `$Char(0)` (SQL NULL arrives as `""`), so a method that tests an
argument `= ""` reads `''` as a one-character string. `MatchEdges(src, pred, 0, '')` scanned a graph named
`$Char(0)` and answered `[]`; R125 fixed the same thing in `DegreeCentralityJson`. Every method below was found by a
scan of the SQL-callable methods (the `[ SqlProc ]` forwarders in `Graph.KG.SqlProcs` and their targets) for a string
argument tested against `""` with no `$Char(0)` handling, and then asked with `''` from SQL: these are the ones whose
answer was wrong. (Those whose `''` answer was already the empty string's, through a `%FromJSON` failure or a
`Catch`, are not changed; nor `IVGLISTGET('', i)`, whose error is Cypher's for a subscript on a string, as for
`'abc'[0]`.)

Each case calls the SQL function with `''` and checks the answer worked out by hand from what the argument means:

* `MatchEdges` / `MatchEdgesAllGraphs`: `''` for the graph is the default graph; `''` for the predicate is every
  predicate (what `""` means over the Native API); `''` for the source is every source. The edges: default graph
  a -R-> b, a -S-> c; graph `g269` a -R-> d.
* `CypherFn_IVGREGEXMATCH` (`=~`): `'' =~ 'a*'` is true, `'' =~ '.+'` false, `'' =~ ''` true, `'abc' =~ ''` false.
* `CypherFn_IVGSTRSPLIT`: `split('', ',')` is `['']`; `split('a,b', '')` is the characters `['a', ',', 'b']`.
* `CypherFn_IVGMAPGET`: the key `''` reads the map's `""` entry.
* `CypherFn_IVGJSONSET`: `''` for the map is the empty map; for the type "not known" (`'5'` stored as the number 5);
  for the key the key `""`. The value `''` is still stored as `"\\u0000"`: `JSON_VALUE` reads that back as SQL's
  `''`, and `""` as NULL, so `SET r.k = ''` then `r.k = ''` holds only this way (pinned below through Cypher).
* `CypherFn_IVGJSONREMOVE`: `''` for the map is the empty map; for the key it removes the `""` entry.

Runs in an IVG460FX*/IVG460FY* scratch namespace (default IVG Schema), emptied first.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith(("IVG460FX", "IVG460FY")),
                       reason="IVG_NAMESPACE must name an IVG460FX*/IVG460FY* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]


@pytest.fixture(scope="module")
def conn():
    import iris

    from iris_vector_graph import IRISGraphEngine

    c = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(c, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    native = iris.createIRIS(c)
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.commit()
    eng.create_edge("a", "R", "b")
    eng.create_edge("a", "S", "c")
    eng.create_edge("a", "R", "d", graph="g269")
    c.commit()
    yield c
    native.classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.commit()
    c.close()


def _one(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    value = cur.fetchone()[0]
    cur.close()
    return value


def _edges(raw):
    return sorted((e["s"], e["p"], e["o"], e["g"]) for e in json.loads(raw))


AB, AC, AD = ("a", "R", "b", ""), ("a", "S", "c", ""), ("a", "R", "d", "g269")

MATCH_EDGES = {
    "MatchEdges('a', 'R', 0, '')": [AB],
    "MatchEdges('a', '', 0, 0)": [AB, AC],
    "MatchEdges('a', '', 0, '')": [AB, AC],
    "MatchEdges('', 'R', 0, 0)": [AB],
    "MatchEdges('a', 'R', 0, 'g269')": [AD],
}


@pytest.mark.parametrize("call", sorted(MATCH_EDGES))
def test_match_edges(conn, call):
    assert _edges(_one(conn, f"SELECT Graph_KG.{call}")) == MATCH_EDGES[call]


MATCH_EDGES_ALL = {
    "MatchEdgesAllGraphs('a', '', 0)": [AB, AC, AD],
    "MatchEdgesAllGraphs('', 'R', 0)": [AB, AD],
}


@pytest.mark.parametrize("call", sorted(MATCH_EDGES_ALL))
def test_match_edges_all_graphs(conn, call):
    assert _edges(_one(conn, f"SELECT Graph_KG.{call}")) == sorted(MATCH_EDGES_ALL[call])


REGEX = {("''", "'a*'"): 1, ("''", "'.+'"): 0, ("''", "''"): 1, ("'abc'", "''"): 0, ("NULL", "'.*'"): 0}


@pytest.mark.parametrize("s,p", sorted(REGEX))
def test_regex_match(conn, s, p):
    assert int(_one(conn, f"SELECT Graph_KG.CypherFn_IVGREGEXMATCH({s}, {p})")) == REGEX[(s, p)]


SPLIT = {("''", "','"): [""], ("'a,b'", "''"): ["a", ",", "b"], ("'a,b'", "','"): ["a", "b"]}


@pytest.mark.parametrize("s,d", sorted(SPLIT))
def test_str_split(conn, s, d):
    assert json.loads(_one(conn, f"SELECT Graph_KG.CypherFn_IVGSTRSPLIT({s}, {d})")) == SPLIT[(s, d)]


def test_map_get(conn):
    assert int(_one(conn, "SELECT Graph_KG.CypherFn_IVGMAPGET('{\"a\":1,\"\":2}', '')")) == 2


JSONSET = {
    "('', 'k', 'v', 'string')": {"k": "v"},
    "('{\"a\":1}', 'k', '', 'string')": {"a": 1, "k": "\x00"},
    "('{\"a\":1}', 'k', '5', '')": {"a": 1, "k": 5},
    "('{\"a\":1}', '', 'v', 'string')": {"a": 1, "": "v"},
}


@pytest.mark.parametrize("args", sorted(JSONSET))
def test_json_set(conn, args):
    assert json.loads(_one(conn, f"SELECT Graph_KG.CypherFn_IVGJSONSET{args}")) == JSONSET[args]


def test_an_empty_string_edge_property_reads_back_as_the_empty_string(conn):
    """Why IVGJSONSET's value keeps $Char(0): SET r.k = '' goes through it, and r.k reads JSON_VALUE."""
    from iris_vector_graph import IRISGraphEngine

    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    rows = eng.execute_cypher("MATCH (a {id: 'a'})-[r:S]->(c) SET r.k = '' RETURN r.k = '', r.k IS NULL",
                              raise_on_error=True).rows
    assert [tuple(r) for r in rows] == [(True, False)]


JSONREMOVE = {"('', 'a')": {}, "('{\"a\":1,\"\":2}', '')": {"a": 1}}


@pytest.mark.parametrize("args", sorted(JSONREMOVE))
def test_json_remove(conn, args):
    assert json.loads(_one(conn, f"SELECT Graph_KG.CypherFn_IVGJSONREMOVE{args}")) == JSONREMOVE[args]
