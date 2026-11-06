"""4.6.0 R050 W2: a string that starts with "P" sorts as a string, not as a duration, on both paths.

The sort key (Graph_KG.CY_SORT_KEY for the SQL path, Graph.KG.Cypher.SortKey.Key for the native executor) gave any
untyped stored value starting "P" the duration key "k" + v, and durations sort before strings ("l" + v). So
`MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY l` put Person before City and Company, and names
Zed, Pat, Ann sorted [Pat, Ann, Zed]. Only a well-formed ISO 8601 duration (P, then [-]digits[.digits] followed by
Y, M, W or D, then optionally T and components ending H, M or S) keeps the duration key, which from 4.6.0 R307
sorts by length (P1D sorted before PT1H by its text).

The answers, by hand (openCypher orders Duration before String):
    labels: City (1), Company (2), Person (3); names Ann, Pat, Zed; DESC the reverse.
    values P1D, Ann, Pat: P1D (a duration) first, then Ann, Pat.

The other places a leading "P" is read as a duration (4.6.0 R050, the lead's sweep): CY_DURATION_FIELD answered
`WITH n.name AS s RETURN s.years` with 0 for "Pat" where "Ann" gives null; now any value that is not a well-formed
duration gives null. Comparisons, min/max, concatenation and toString over "Pat" were right already and are held here
too; CY_TEMPORAL_ARITH validates its operands since R300.

Run with IVG_NAMESPACE set to an IVG460FX* scratch namespace that has IVG installed (scripts/dev/make_scratch_namespace.py).
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460FX"), reason="IVG_NAMESPACE must name an IVG460FX* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

#: value -> its key, by the rule in the module docstring
KEYS = {
    "Person": "lPerson",
    "Pat": "lPat",
    "P": "lP",
    "PT": "lPT",
    "P5": "lP5",
    "P1X": "lP1X",
    "PT1D": "lPT1D",
    # 4.6.0 R307: "k", the number key of the length in seconds without its "n" (a month 2629746 s, a year twelve),
    # $Char(1), the text: P1D is 86400 s, so "2" (positive), exponent 50004, digits 864
    "P1D": "k250004864\x01P1D",
    "PT1H30M": "k25000354\x01PT1H30M",
    "P1Y2M10DT12H45M30.25S": "k2500073772637425\x01P1Y2M10DT12H45M30.25S",
    "PT-0.5S": "k0500004~\x01PT-0.5S",
    "P2W": "k25000612096\x01P2W",
}

NODES = (("w2p1", "Person", "Zed"), ("w2p2", "Person", "Pat"), ("w2p3", "Person", "Ann"),
         ("w2c1", "City", "Boston"), ("w2co1", "Company", "Globex"), ("w2co2", "Company", "Acme"))
SCOPE = "WHERE n.node_id STARTS WITH 'w2'"
QUERIES = {
    f"MATCH (n) {SCOPE} RETURN labels(n)[0] AS l, count(*) AS c ORDER BY l": [["City", 1], ["Company", 2], ["Person", 3]],
    f"MATCH (n) {SCOPE} RETURN labels(n)[0] AS l, count(*) AS c ORDER BY l DESC": [["Person", 3], ["Company", 2], ["City", 1]],
    f"MATCH (n) {SCOPE} RETURN head(labels(n)) AS l, count(*) AS c ORDER BY l": [["City", 1], ["Company", 2], ["Person", 3]],
    f"MATCH (n) {SCOPE} WITH labels(n)[0] AS l, count(*) AS c RETURN l, c ORDER BY l": [["City", 1], ["Company", 2], ["Person", 3]],
    f"MATCH (n) {SCOPE} RETURN n.node_id AS id ORDER BY labels(n)[0], id":
        [["w2c1"], ["w2co1"], ["w2co2"], ["w2p1"], ["w2p2"], ["w2p3"]],
    f"MATCH (n) {SCOPE} RETURN n.node_id AS id ORDER BY head(labels(n)) DESC, id":
        [["w2p1"], ["w2p2"], ["w2p3"], ["w2co1"], ["w2co2"], ["w2c1"]],
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' RETURN n.name AS m ORDER BY m": [["Ann"], ["Pat"], ["Zed"]],
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' RETURN n.name AS m ORDER BY n.name DESC": [["Zed"], ["Pat"], ["Ann"]],
    "MATCH (n:W2D) RETURN n.v AS v ORDER BY v": [["P1D"], ["Ann"], ["Pat"]],
}
#: Anchored reads the native executor answers (its sort key is the class's); the rest above go to SQL either way.
NATIVE = {
    "MATCH (h {node_id: 'x2hub'})-[:KNOWS]->(b) RETURN b.name AS m ORDER BY b.name": [["Ann"], ["Pat"], ["Zed"]],
    "MATCH (h {node_id: 'x2hub'})-[:HAS]->(b) RETURN b.v AS v ORDER BY b.v DESC": [["Pat"], ["Ann"], ["P1D"]],
}
QUERIES.update(NATIVE)
#: Not sort keys, but other reads of a value starting "P": each answers as it does for a value that does not.
QUERIES.update({
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' RETURN min(n.name) AS lo, max(n.name) AS hi": [["Ann", "Zed"]],
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' AND n.name > 'O' RETURN n.name AS m ORDER BY m": [["Pat"], ["Zed"]],
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' WITH n.name AS s RETURN s + '!' AS x ORDER BY x":
        [["Ann!"], ["Pat!"], ["Zed!"]],
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' WITH n.name AS s RETURN toString(s) AS t ORDER BY t":
        [["Ann"], ["Pat"], ["Zed"]],
    "MATCH (n:Person) WHERE n.node_id STARTS WITH 'w2' WITH n.name AS s RETURN s.years AS y": [[None], [None], [None]],
    "WITH duration('P1Y2M') AS d RETURN d.months AS m, d.years AS y": [[14, 1]],
})
#: CY_DURATION_FIELD(v, f): null unless v is a well-formed duration
FIELDS = {
    ("Pat", "years"): None,
    ("Ann", "years"): None,
    ("PT", "years"): None,
    ("P", "days"): None,
    ("PT1D", "days"): None,
    ("P1D2", "days"): None,
    ("P1Y", "years"): 1,
    ("P30Y8M13D", "months"): 368,
    ("P1DT2H", "hours"): 2,
    ("PT-22H", "hours"): -22,
    ("P2W", "days"): 14,
}
QUERIES["MATCH (h {node_id: 'x2hub'})-[:IN]->(b) RETURN labels(b)[0] AS l, count(*) AS c ORDER BY l"] = [
    ["City", 1], ["Company", 2], ["Person", 3]]
EDGES = [("KNOWS", "w2p1"), ("KNOWS", "w2p2"), ("KNOWS", "w2p3"), ("HAS", "x2d1"), ("HAS", "x2d2"), ("HAS", "x2d3")] + [
    ("IN", n) for n, _, _ in NODES]


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    eng = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768)
    eng.initialize_schema()
    eng._install_procedures(conn.cursor())
    conn.commit()
    ids = [n for n, _, _ in NODES] + ["x2d1", "x2d2", "x2d3", "x2hub"]
    for n in ids:
        eng.delete_node(n, graph=None)
    for n, label, name in NODES:
        eng.create_node(n, labels=[label], properties={"name": name})
    for n, v in (("x2d1", "Pat"), ("x2d2", "P1D"), ("x2d3", "Ann")):
        eng.create_node(n, labels=["W2D"], properties={"v": v})
    eng.create_node("x2hub", labels=["W2Hub"])
    for p, o in EDGES:
        eng.create_edge("x2hub", p, o)
    conn.commit()
    yield eng
    for n in ids:
        eng.delete_node(n, graph=None)
    conn.commit()
    conn.close()


@pytest.mark.parametrize("value", sorted(KEYS))
def test_the_udf_key(engine, value):
    cur = engine.conn.cursor()
    cur.execute(f"SELECT {engine._ivg_schema}.CY_SORT_KEY(?, 'x')", [value])
    assert cur.fetchone()[0] == KEYS[value]


@pytest.mark.parametrize("value,field", sorted(FIELDS))
def test_the_duration_field(engine, value, field):
    cur = engine.conn.cursor()
    cur.execute(f"SELECT {engine._ivg_schema}.CY_DURATION_FIELD(?, ?)", [value, field])
    got = cur.fetchone()[0]
    assert (None if got in (None, "") else int(got)) == FIELDS[(value, field)]


@pytest.mark.parametrize("value", sorted(KEYS))
def test_the_class_key(engine, value):
    import iris

    assert iris.createIRIS(engine.conn).classMethodValue("Graph.KG.Cypher.SortKey", "Key", value, "x") == KEYS[value]


@pytest.mark.parametrize("native", [True, False], ids=["native-on", "native-off"])
@pytest.mark.parametrize("query", sorted(QUERIES))
def test_order_by_sorts(engine, query, native, monkeypatch):
    monkeypatch.setattr(engine, "native_cypher", native)
    assert [list(r) for r in engine.execute_cypher(query, raise_on_error=True).rows] == QUERIES[query]
    if native and query in NATIVE:
        # the native executor answered (not the SQL path behind it)
        before = getattr(engine, "_native_answers", 0)
        engine.execute_cypher(query, raise_on_error=True)
        assert getattr(engine, "_native_answers", 0) == before + 1
