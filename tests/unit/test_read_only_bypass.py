"""execute_cypher(read_only=True) refuses every write, however the query is spelled.

4.5.1 refused a lone CREATE but ran one placed after `CALL ...;`: the `;` split ran each
part through execute_cypher without read_only (or raise_on_error, or the USE GRAPH scope)
and before the mutation check, and swallowed every part's exception. A write inside a
UNION branch or a CALL { } body, and a query holding approx_count_distinct, also got past
the check. Each case here runs the real execute_cypher routing over a stub connection with
`_execute_parsed` (the call every statement reaches before SQL) recording what would have
run: a refused query must raise PermissionError having run nothing.

The database side of the same cases is tests/e2e/test_read_only_bypass_e2e.py.
"""

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _engine(answers=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    eng = IRISGraphEngine(conn, embedding_dimension=4)
    ran = []
    answers = list(answers or [])

    def _record(parsed, parameters, procedures=None):
        ran.append(parsed)
        if answers:
            return answers.pop(0)
        return IVGResult(columns=["x"], rows=[])

    eng._execute_parsed = _record
    # the count-only CREATE and row-bound MERGE paths call _execute_parsed too; nothing else may run
    return eng, ran


WRITES = {
    "reporter: CALL then ; then CREATE": "CALL db.labels(); CREATE (x:Probe {id:'probe-2'}) RETURN x",
    "lower case call/create": "call db.labels(); create (x:Probe {id:'p'}) return x",
    "newline after ;": "CALL db.labels() YIELD label RETURN label;\nCREATE (x:Probe {id:'p'}) RETURN x",
    "newline after CALL": "MATCH (n) RETURN n LIMIT 1; CALL\ndb.labels(); CALL db.labels(); CREATE (x:Probe)",
    "write first, CALL second": "CREATE (x:Probe {id:'p'}) RETURN x; CALL db.labels()",
    "three parts, write in the middle": "CALL db.labels(); MERGE (x:Probe {id:'p'}); CALL db.labels()",
    "USE GRAPH then split": "USE GRAPH g CALL db.labels(); CREATE (x:Probe {id:'p'}) RETURN x",
    "CALL in a string, then ;": "MATCH (n {name: 'CALL me'}) RETURN n; CREATE (x:Probe {id:'p'})",
    "DETACH DELETE after split": "CALL db.labels(); MATCH (n) DETACH DELETE n",
    "SET after split": "CALL db.labels(); MATCH (n) SET n.p = 1",
    "UNION with a write branch": (
        "MATCH (n) RETURN n.id AS id UNION MATCH (m) CREATE (x:Probe {id:'u'}) RETURN x.id AS id"
    ),
    "UNION ALL with a write branch": (
        "MATCH (n) RETURN n.id AS id UNION ALL CREATE (x:Probe {id:'u'}) RETURN x.id AS id"
    ),
    "CALL { CREATE } subquery": "CALL { CREATE (x:Probe {id:'s'}) RETURN x } RETURN x",
    "CALL { WITH n CREATE } subquery": "MATCH (n) CALL { WITH n CREATE (x:Probe {id:'s'}) } RETURN n",
    "MERGE after WITH": "MATCH (n) WITH n MERGE (x:Probe {id:'m'}) RETURN x",
    "SET after WITH": "MATCH (n) WITH n SET n.p = 1 RETURN n",
    "REMOVE after WITH": "MATCH (n) WITH n REMOVE n.p RETURN n",
    "DETACH DELETE after WITH": "MATCH (n) WITH n DETACH DELETE n",
    "FOREACH": "UNWIND [1] AS i FOREACH (j IN [1] | CREATE (:Probe {id:'f'}))",
    "approx_count_distinct beside a CREATE": (
        "MATCH (n) CREATE (x:Probe {id:'a'}) RETURN approx_count_distinct(n) AS c"
    ),
    "approx_count_distinct, CALL, ; and CREATE": (
        "MATCH (n) RETURN approx_count_distinct(n) AS c; CALL db.labels(); CREATE (x:Probe {id:'a'})"
    ),
}


@pytest.mark.parametrize("query", list(WRITES.values()), ids=list(WRITES))
def test_read_only_refuses_the_write_and_runs_nothing(query):
    eng, ran = _engine()
    with pytest.raises(PermissionError, match="Read-only mode"):
        eng.execute_cypher(query, {}, read_only=True)
    assert ran == [], f"ran {len(ran)} statement(s) before refusing: {ran}"


def test_read_only_reads_still_run_every_part():
    eng, ran = _engine(
        [IVGResult(columns=["label"], rows=[["A"]]), IVGResult(columns=["label"], rows=[["B"]])]
    )
    result = eng.execute_cypher(
        "CALL db.labels() YIELD label RETURN label; CALL db.labels() YIELD label RETURN label",
        {}, read_only=True,
    )
    assert [list(r) for r in result.rows] == [["A"], ["B"]]
    assert len(ran) == 2


def test_a_semicolon_inside_a_string_literal_does_not_split_the_statement():
    eng, ran = _engine([IVGResult(columns=["label"], rows=[["A"]])])
    query = "CALL db.labels() YIELD label WITH label WHERE label <> 'x; CREATE (p:Probe)' RETURN label"
    result = eng.execute_cypher(query, {}, read_only=True)
    assert len(ran) == 1, "one statement, not a split at the `;` in the literal"
    assert ran[0].procedure_call.procedure_name == "db.labels"
    assert [list(r) for r in result.rows] == [["A"]]


def test_use_graph_scopes_every_part():
    eng, ran = _engine()
    eng.execute_cypher("USE GRAPH 'g1' CALL db.labels(); MATCH (n) RETURN n", {})
    assert [p.graph_context for p in ran] == ["g1", "g1"]


def test_a_part_with_its_own_use_graph_keeps_it():
    eng, ran = _engine()
    eng.execute_cypher("USE GRAPH 'g1' CALL db.labels(); USE GRAPH 'g2' MATCH (n) RETURN n", {})
    assert [p.graph_context for p in ran] == ["g1", "g2"]


def test_raise_on_error_reaches_every_part():
    eng, ran = _engine(
        [IVGResult(columns=["label"], rows=[["A"]]), IVGResult(columns=[], rows=[], error="SQLCODE -1")]
    )
    with pytest.raises(RuntimeError, match="SQLCODE -1"):
        eng.execute_cypher("CALL db.labels(); MATCH (n) RETURN n", {}, raise_on_error=True)
    assert len(ran) == 2


def test_an_exception_in_a_part_is_not_swallowed():
    eng, ran = _engine()

    def _boom(parsed, parameters, procedures=None):
        ran.append(parsed)
        raise RuntimeError("part failed")

    eng._execute_parsed = _boom
    with pytest.raises(RuntimeError, match="part failed"):
        eng.execute_cypher("CALL db.labels(); MATCH (n) RETURN n", {})


def test_procedures_reach_every_part():
    eng, _ = _engine()
    seen = []

    def _record(parsed, parameters, procedures=None):
        seen.append(procedures)
        return IVGResult(columns=["x"], rows=[])

    eng._execute_parsed = _record
    procs = {"test.my.proc": {"args": [], "outputs": [], "rows": []}}
    eng.execute_cypher("CALL db.labels(); MATCH (n) RETURN n", {}, procedures=procs)
    assert seen == [procs, procs]


def test_the_ast_names_a_write_in_a_union_branch_and_a_subquery_body():
    from iris_vector_graph.cypher.parser import parse_query

    assert parse_query(WRITES["UNION with a write branch"]).writes
    assert parse_query(WRITES["CALL { CREATE } subquery"]).writes
    assert parse_query(WRITES["CALL { WITH n CREATE } subquery"]).writes
    assert not parse_query("MATCH (n) RETURN n.id AS id UNION MATCH (m) RETURN m.id AS id").writes
    assert not parse_query("MATCH (n) CALL { WITH n MATCH (n)-->(m) RETURN m } RETURN m").writes


# ---------------------------------------------------------------------------
# /graphql executeCypher under IVG_READ_ONLY. The REST /api/cypher route refuses a query that names a write
# (now cypher.writes_cypher, R113) and GraphQL's mutations refuse under _check_read_only, but the executeCypher
# *query* field translated and ran any Cypher, CREATE included.
# ---------------------------------------------------------------------------


def _graphql(query_text, monkeypatch, read_only):
    import asyncio

    from api.gql.schema import schema

    if read_only:
        monkeypatch.setenv("IVG_READ_ONLY", "true")
    else:
        monkeypatch.delenv("IVG_READ_ONLY", raising=False)
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.description = None
    cursor.fetchall.return_value = []
    document = "query($q: String!) { executeCypher(query: $q) }"
    result = asyncio.run(
        schema.execute(document, variable_values={"q": query_text}, context_value={"db_connection": conn})
    )
    assert result.errors is None, result.errors
    answer = result.data["executeCypher"]
    return (json.loads(answer) if isinstance(answer, str) else answer), cursor


@pytest.mark.parametrize(
    "query",
    [
        "CREATE (x:Probe {node_id: 'p'}) RETURN x",
        "MATCH (n) WITH n SET n.p = 1 RETURN n",
        "MATCH (n) DETACH DELETE n",
    ],
)
def test_graphql_execute_cypher_refuses_a_write_in_read_only_mode(query, monkeypatch):
    answer, cursor = _graphql(query, monkeypatch, read_only=True)
    assert "read-only" in answer["error"].lower()
    cursor.execute.assert_not_called()


def test_graphql_execute_cypher_runs_a_read_in_read_only_mode(monkeypatch):
    _, cursor = _graphql("MATCH (n) RETURN n.node_id AS id", monkeypatch, read_only=True)
    assert cursor.execute.called


def test_graphql_execute_cypher_runs_a_write_outside_read_only_mode(monkeypatch):
    _, cursor = _graphql("CREATE (x:Probe {node_id: 'p'}) RETURN x", monkeypatch, read_only=False)
    assert cursor.execute.called


@pytest.mark.parametrize("query", list(WRITES.values()), ids=list(WRITES))
def test_the_rest_route_read_only_check_names_every_write(query):
    """/api/cypher under IVG_READ_ONLY refuses on cypher.writes_cypher, the rule execute_cypher applies (R113;
    every route is in test_r113_read_only_one_rule.py); every spelling above is refused there too."""
    from iris_vector_graph.cypher import writes_cypher

    assert writes_cypher(query)
