"""4.6.0 R311 [SECURITY], live: a value or name built to add SQL is one value or name, and the query answers right.

tests/unit/test_460_r311_translator_quote_state.py proves the statements' quotes balance; this file runs one
injection-shaped value per site on IRIS and checks the answer: the statement IVG meant, not one the text rewrote.

By hand: (:Z311 {id: 'z1'}), (:Z311 {id: "q'1"}), (:Z311 {id: 'z3'}) and (:`x'` {id: 'z4'}); z1 -[:T311 {`k'`: 1,
`a.b`: 'dot', `a b`: 'space'}]-> q'1; q'1 -[:T311 {`k'`: 2}]-> z3. On the release-4.6.0 head each query below raised
an SQL error or, given the right text, answered every row (`x' OR '1'='1` as an anchor id under IVG_EDGESCAN=1).

The namespace is `IVG_NAMESPACE` (an IVG460* scratch namespace with IVG set up); this file's labels are removed before
and after.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

KEY = "k'"  # a relationship property key holding a quote
OR_TRUE = "x' OR '1'='1"


def _clear(eng):
    eng.execute_cypher("MATCH (n:Z311) DETACH DELETE n", raise_on_error=True)
    eng.execute_cypher("MATCH (n:`x'`) DETACH DELETE n", raise_on_error=True)


@pytest.fixture(scope="module")
def engine():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = alt.connect()
    schema = iris.createIRIS(conn).get("^IVG.Deploy", "schema") or "Graph_KG"
    eng = IRISGraphEngine(conn, namespace=alt.NS, schema_prefix=schema)
    _clear(eng)
    eng.execute_cypher(
        "CREATE (a:Z311 {id: 'z1'}), (b:Z311 {id: $q}), (c:Z311 {id: 'z3'}), (:`x'` {id: 'z4'}), "
        "(a)-[:T311 {`k'`: 1, `a.b`: 'dot', `a b`: 'space'}]->(b), (b)-[:T311 {`k'`: 2}]->(c)",
        {"q": "q'1"},
        raise_on_error=True,
    )
    yield eng
    try:
        _clear(eng)
    finally:
        conn.close()


def rows(eng, query, params=None):
    return eng.execute_cypher(query, params or {}, raise_on_error=True).rows


# H1: the parameter map's keys
def test_keys_of_a_parameter_map_are_its_keys(engine):
    key = "a') OR 1=1 --"
    got = rows(engine, "RETURN keys($m) AS k", {"m": {key: 1}})
    assert [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in got] == [[key]]
    assert rows(engine, "RETURN size(keys($m)) AS k", {"m": {key: 1, "b": 2}}) == [[2]]


# H10: the anchor id under IVG_EDGESCAN=1
@pytest.mark.parametrize("edgescan", ["0", "1"])
def test_an_anchor_id_is_one_value(engine, monkeypatch, edgescan):
    monkeypatch.setenv("IVG_EDGESCAN", edgescan)
    q = "MATCH (a:Z311 {id: $p})-[:T311]->(b) RETURN b.id"
    assert rows(engine, q, {"p": OR_TRUE}) == []
    assert rows(engine, q, {"p": "q'1"}) == [["z3"]]
    assert rows(engine, "MATCH (a:Z311 {id: 'q\\'1'})-[:T311]->(b) RETURN b.id") == [["z3"]]


# H2-H5: relationship property keys
@pytest.mark.parametrize(
    "query, want",
    [
        ("MATCH (:Z311)-[r:T311]->() WHERE r.`k'` = 1 RETURN count(r)", [[1]]),
        ("MATCH (:Z311)-[r:T311 {`k'`: 2}]->(b) RETURN b.id", [["z3"]]),
        ("MATCH (:Z311)-[r:T311]->() WITH r RETURN r.`k'` AS k ORDER BY k", [[1], [2]]),
        ("MATCH (:Z311)-[r:T311]->() WITH r RETURN r.`k'` AS k ORDER BY r.`k'` DESC", [[2], [1]]),
        ("MATCH (:Z311)-[r:T311]->() RETURN r.`a.b`, r.`a b`", [["dot", "space"]]),
        ("MATCH (:Z311)-[r:T311]->() WHERE r.`a.b` = 'dot' RETURN r.`k'`", [[1]]),
    ],
)
def test_a_relationship_key_is_its_own_text(engine, query, want):
    got = rows(engine, query)
    if "RETURN r.`a.b`" in query:
        got = [r for r in got if r[0] is not None]
    assert got == want


def test_an_unaliased_key_column_is_named_by_the_query(engine):
    result = engine.execute_cypher("MATCH (:Z311 {id: 'z1'})-[r:T311]->() RETURN r.`k'`", raise_on_error=True)
    assert result.columns == ["r.`k'`"] and result.rows == [[1]]


# H6: a label of an OR list
def test_a_label_in_an_or_list_is_one_label(engine):
    assert rows(engine, "MATCH (n:Z311|`x'`) RETURN count(n)") == [[4]]
    assert rows(engine, "MATCH (n:Z311|`x') OR 1=1 --`) RETURN count(n)") == [[3]]


# H7/H8: time zones written in the query
def test_a_zone_name_is_one_literal(engine):
    paris = rows(engine, "RETURN toString(datetime({year: 2020, month: 7, day: 1, timezone: 'Europe/Paris'})) AS d")
    assert paris == [["2020-07-01T00:00+02:00[Europe/Paris]"]]


@pytest.mark.parametrize(
    "query, text",
    [
        # on 4.6.0 the first three were SQLCODE -25 and the fourth answered '2020-01-01T00:00xy': SQL's || ran
        ("RETURN datetime({year: 2020, timezone: 'x\\' OR 1=1 --'}) AS d", "x' OR 1=1 --"),
        ("RETURN time({hour: 1, timezone: 'x\\' OR 1=1 --'}) AS d", "x' OR 1=1 --"),
        ("RETURN datetime('2020-01-01T00:00[x\\' OR 1=1 --]') AS d", "x' OR 1=1 --"),
        ("RETURN datetime({year: 2020, timezone: 'x\\'||\\'y'}) AS d", "x'||'y"),
    ],
)
def test_an_injected_zone_stays_text(engine, query, text):
    """The zone is not a real one, so the value is not a real datetime; what matters is that it is the text written."""
    result = engine.execute_cypher(query)
    assert result.error is None, result.error
    assert len(result.rows) == 1 and text in str(result.rows[0][0]), result.rows


# H9: a name that is not an identifier is refused, and nothing runs
@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Z311) RETURN n.id AS `a FROM Graph_KG.nodes --`",
        "MATCH (`n) DETACH DELETE (n`:Z311) RETURN 1 AS x",
        "UNWIND [1] AS `x FROM t --` RETURN 1 AS y",
    ],
)
def test_a_name_that_is_not_an_identifier_runs_nothing(engine, query):
    with pytest.raises(Exception, match="UnsupportedName"):
        engine.execute_cypher(query, raise_on_error=True)
    assert rows(engine, "MATCH (n:Z311) RETURN count(n)") == [[3]]


# a relationship type with a bind marker, a named-parameter spelling and a quote, through the CY_VLP_PATHS walks
QTYPE = "`T?:1'Z`"


@pytest.fixture
def qtype_graph(engine):
    engine.execute_cypher(
        f"CREATE (:Z311 {{id: 'qa', name: 'ann'}})-[:{QTYPE}]->(:Z311 {{id: 'qb', name: 'bob'}})-[:{QTYPE}]->"
        "(:Z311 {id: 'qc', name: 'eve'})",
        raise_on_error=True,
    )
    yield
    engine.execute_cypher("MATCH (n:Z311) WHERE n.id IN ['qa', 'qb', 'qc'] DETACH DELETE n", raise_on_error=True)


@pytest.mark.parametrize(
    "query, want",
    [
        (f"MATCH (a:Z311 {{id: 'qa'}}) OPTIONAL MATCH (a)-[:{QTYPE}*1..2]->(b) WHERE b.name = $n RETURN b.name",
         [["eve"]]),
        (f"MATCH (a:Z311) WHERE a.id IN ['qa', 'z3'] OPTIONAL MATCH (a)-[:{QTYPE}*1..2]->(b) WHERE b.name = 'eve' "
         "RETURN a.id, b.name ORDER BY a.id", [["qa", "eve"], ["z3", None]]),
        (f"MATCH (a:Z311 {{id: 'qa'}})-[:{QTYPE}*1..2]->(b) RETURN b.id ORDER BY b.id", [["qb"], ["qc"]]),
        (f"MATCH (a:Z311 {{id: 'qa'}}) RETURN size([(a)-[:{QTYPE}*1..2]->(b) | b]) AS n", [[2]]),
    ],
)
def test_a_type_with_a_bind_marker_walks_its_relationships(engine, qtype_graph, query, want):
    assert rows(engine, query, {"n": "eve"}) == want
