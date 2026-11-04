"""4.6.0 R274 (the empty-string relationship property), live: a relationship property that is the empty string reads
as the empty string from every reader.

A relationship's properties are one JSON text (`rdf_edges.qualifiers`). `SET r.u = ''` stores "\\u0000" there (R269
keeps it: IVG's JSON_VALUE reads that back as SQL's ''), a CREATE or create_edge stores "". Found by fixes38 on the
release head, and with it:
* properties(r), RETURN r, [r.u] and collect(r.u) answered "\\u0000" ("\\x00" once parsed): the JSON texts carry the
  stored text, and IRIS's JSON functions spell SQL's '' that way too (collect() of '' did, as did [n.e] and
  properties(n) of a node property set to '');
* r.s of a CREATE's {s: ''} answered null, and `WHERE r.s = ''` matched nothing: the readers took the JSON "" for
  null.

The stored forms do not change. Worked by hand, graph r274e: (a:E {name: 'a'})-[:R {s: '', t: 'x'}]->(b:E
{name: 'b'}), then SET r.u = '' and SET a.e = '', and an edge c-[:Q]->d by create_edge with qualifiers {q: ''}:
* r.s, r.u, r.t are '', '', 'x'; size(r.s) 0, toString(r.u) '';
* properties(r) is {s: '', t: 'x', u: ''}; RETURN r's properties the same; [r.s, r.u] ['', ''], collect(r.u) [''];
* `WHERE r.s = ''` and `WHERE r.u = ''` each count 1;
* [a.e] is [''], properties(a) {name: 'a', e: ''}; collect() of UNWIND ['', 'x'] ['', 'x'];
* the create_edge edge's q is '', its properties {q: ''}.

Namespace: an IVG460F* scratch namespace. Native on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

G = "r274e"
U = f"USE GRAPH '{G}' "
M = U + "MATCH ()-[r:R]->() "

CASES = [
    ("scalar reads", M + "RETURN r.s, r.u, r.t, size(r.s), toString(r.u)", [["", "", "x", 0, ""]]),
    ("properties(r)", M + "RETURN properties(r)", [[{"s": "", "t": "x", "u": ""}]]),
    ("RETURN r", M + "RETURN r", [[{"type": "R", "props": {"s": "", "t": "x", "u": ""}}]]),
    ("in a list", M + "RETURN [r.s, r.u], collect(r.u)", [[["", ""], [""]]]),
    ("a WHERE on the CREATE's ''", M + "WHERE r.s = '' RETURN count(*)", [[1]]),
    ("a WHERE on the SET's ''", M + "WHERE r.u = '' RETURN count(*)", [[1]]),
    ("a node's '' in a list and its properties",
     U + "MATCH (a:E {name: 'a'}) RETURN [a.e], properties(a)", [[[""], {"name": "a", "e": ""}]]),
    ("collect() of ''", "UNWIND ['', 'x'] AS v RETURN collect(v)", [[["", "x"]]]),
    ("create_edge's ''", U + "MATCH ()-[q:Q]->() RETURN q.q, properties(q)", [["", {"q": ""}]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(U + "CREATE (a:E {name: 'a'})-[:R {s: '', t: 'x'}]->(b:E {name: 'b'}), "
                       "(:E {id: 'r274c'}), (:E {id: 'r274d'})", {}, raise_on_error=True)
    nat.execute_cypher(M + "SET r.u = ''", {}, raise_on_error=True)
    nat.execute_cypher(U + "MATCH (a:E {name: 'a'}) SET a.e = ''", {}, raise_on_error=True)
    nat.create_edge("r274c", "Q", "r274d", qualifiers={"q": ""}, graph=G)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _value(v):
    if isinstance(v, str) and v[:1] and v[:1] in "[{":
        return json.loads(v)
    return v


@pytest.mark.parametrize("path", ["native", "sql"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_an_empty_string_reads_as_one(engines, path, name, query, want):
    result = engines[path].execute_cypher(query, {}, raise_on_error=True)
    assert getattr(result, "error", None) is None, result.error
    assert [[_value(v) for v in r] for r in result.rows] == want


def test_the_stored_forms_are_unchanged(engines):
    """Readers decode; what is on disk stays: the CREATE's "" and the SET's "\\u0000"."""
    cur = engines["sql"].conn.cursor()
    cur.execute("SELECT qualifiers FROM Graph_KG.rdf_edges WHERE graph_id = ? AND p = 'R'", [G])
    assert json.loads(cur.fetchone()[0]) == {"s": "", "t": "x", "u": "\x00"}
