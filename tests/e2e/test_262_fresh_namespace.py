"""Spec 262 US1 (T013-T015): an operator finds all of IVG in one schema.

A namespace with its own new database (IVG262ALT, dropped and created anew) gets an application function of its own,
`SQLUser.RAND` (class `User.funcRAND`), then IVG's `initialize_schema()` with the default IVG Schema `Graph_KG`.
Afterwards `SQLUser` and `User` hold only the application's objects, every IVG function is in `Graph_KG`, and Cypher
that reaches every helper family answers correctly through SQL that names nothing outside `Graph_KG`.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = "IVG262ALT"
SCHEMA = "Graph_KG"
APP_FN = "CREATE FUNCTION SQLUser.RAND() RETURNS INTEGER LANGUAGE OBJECTSCRIPT { Quit 42 }"


@pytest.fixture(scope="module")
def fresh():
    ns262.recreate(NS)
    conn = ns262.connect(NS)
    conn.cursor().execute(APP_FN)
    app_before = {
        "answer": ns262.rows(conn, "SELECT SQLUser.RAND()")[0][0],
        "class": ns262.rows(conn, "SELECT TimeChanged FROM %Dictionary.ClassDefinition WHERE Name = 'User.funcRAND'"),
    }
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(conn, embedding_dimension=4, namespace=NS, native_cypher=False)
    status = engine.initialize_schema()
    yield {"conn": conn, "engine": engine, "status": status, "app_before": app_before}
    conn.close()


def test_sqluser_holds_only_the_applications_function(fresh):
    assert ns262.routines(fresh["conn"], "SQLUser") == {"RAND"}


def test_sqluser_holds_no_view(fresh):
    assert ns262.views(fresh["conn"], "SQLUser") == set()


#: `User.Exec` (Xecute on any string, called only by tests) leaves the wheel in T055 with the tests that use it; until
#: then it is the one IVG class left in `User`. T055 empties this set.
UNTIL_T055 = {"User.Exec"}


def test_the_user_package_holds_only_the_applications_class(fresh):
    assert ns262.classes(fresh["conn"], "User.") == {"User.funcRAND"} | UNTIL_T055


def test_no_ivg_class_without_a_package(fresh):
    assert {c for c in ns262.classes(fresh["conn"]) if "." not in c} == set()


def test_every_ivg_function_is_in_the_ivg_schema(fresh):
    from iris_vector_graph.schema import GraphSchema

    ddl = "\n".join(GraphSchema.get_procedures_sql_list(table_schema=SCHEMA))
    wanted = set(re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+\w+\.(\w+)", ddl))
    wanted |= {"STR_SPLIT", "JSON_KEYS", "CypherFn_IVGJSONSET", "CypherFn_IVGRANGE", "Percentile_PDISC"}
    assert wanted - ns262.routines(fresh["conn"], SCHEMA) == set()


def test_list_head_and_list_last_are_retired(fresh):
    """4.5.x created both and nothing called them (their bodies fail when called); 4.6.0 creates neither."""
    for schema in (SCHEMA, "SQLUser"):
        assert {"LIST_HEAD", "LIST_LAST"} & ns262.routines(fresh["conn"], schema) == set()


def test_the_applications_function_is_unchanged(fresh):
    conn = fresh["conn"]
    assert ns262.rows(conn, "SELECT SQLUser.RAND()")[0][0] == fresh["app_before"]["answer"] == 42
    assert ns262.rows(
        conn, "SELECT TimeChanged FROM %Dictionary.ClassDefinition WHERE Name = 'User.funcRAND'"
    ) == fresh["app_before"]["class"]


# --------------------------------------------------------------------------- T014: every helper family

@pytest.fixture(scope="module")
def graph(fresh):
    engine = fresh["engine"]
    engine.execute_cypher(
        "CREATE (a:P262 {name: 'a', n: 1, d: '2020-01-01'})-[:K262 {since: 2}]->(b:P262 {name: 'b', n: 2})"
        "-[:K262 {since: 3}]->(c:P262 {name: 'c', n: 3})",
        raise_on_error=True,
    )
    return engine


CASES = [
    # (family, query, expected rows, a function the SQL must call)
    ("VLP", "MATCH (x:P262 {name: 'a'})-[:K262*1..2]->(y) RETURN y.name AS v ORDER BY v", [["b"], ["c"]], "CY_VLP"),
    ("regex", "MATCH (x:P262) WHERE x.name =~ 'b|c' RETURN x.name AS v ORDER BY v", [["b"], ["c"]], "REGEX"),
    ("string split", "MATCH (x:P262 {name: 'a'}) RETURN split(x.name + ',z', ',') AS v", [[["a", "z"]]], "STR_SPLIT"),
    ("string reverse", "MATCH (x:P262 {name: 'a'}) RETURN reverse(x.name + 'bc') AS v", [["cba"]], "REVERSE"),
    ("list", "MATCH (x:P262 {name: 'a'}) WITH [x.n, x.n + 1, x.n + 2] AS l RETURN head(l), last(l), tail(l)",
     [[1, 3, [2, 3]]], "JSON_ARRAY"),
    ("list concat", "MATCH (x:P262 {name: 'a'}) RETURN [x.n] + [x.n + 1] AS v", [[[1, 2]]], "LIST_CONCAT"),
    ("range", "MATCH (x:P262 {name: 'c'}) RETURN range(1, x.n) AS v", [[[1, 2, 3]]], "CypherFn_IVGRANGE"),
    ("JSON property", "MATCH (:P262 {name: 'a'})-[r:K262]->() RETURN r.since AS v", [[2]], "JSON_VALUE"),
    ("JSON keys", "MATCH (:P262 {name: 'a'})-[r:K262]->() RETURN keys(r) AS v", [[["since"]]], "JSON_KEYS"),
    ("comparison", "MATCH (x:P262) WHERE x.n > 1 RETURN x.name AS v ORDER BY v", [["b"], ["c"]], None),
    ("sort key", "MATCH (x:P262) RETURN x.n AS v ORDER BY v DESC", [[3], [2], [1]], None),
    ("temporal", "MATCH (x:P262 {name: 'a'}) RETURN toString(date(x.d) + duration('P1D')) AS v", [["2020-01-02"]],
     "CY_TEMPORAL"),
    ("percentile", "MATCH (x:P262) RETURN percentileDisc(x.n, 0.5) AS v", [[2]], "Percentile_PDISC"),
]


def _value(v):
    """A row value as Cypher means it: the SQL path returns lists as JSON text and numbers as text."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


@pytest.mark.parametrize("family, query, expected, fn", CASES, ids=[c[0] for c in CASES])
def test_each_helper_family_answers_through_the_ivg_schema(graph, family, query, expected, fn):
    result = graph.execute_cypher(query, raise_on_error=True)
    assert [[_value(v) for v in r] for r in result.rows] == expected
    sql = result.sql or ""
    if fn:
        assert fn in sql, f"{family}: the SQL calls no {fn}: {sql[:300]}"
    assert ns262.names_outside(sql, SCHEMA) == []


def test_a_relationship_property_write_goes_through_the_ivg_schema(graph):
    result = graph.execute_cypher(
        "MATCH (:P262 {name: 'b'})-[r:K262]->() SET r.z = 5 RETURN r.z AS v", raise_on_error=True
    )
    # The SET statement itself answers [[None], [5]] here, at 4.5.1 too (measured on IVG262UPG): a translator defect
    # outside spec 262. The answer that counts is the stored value, read back below.
    assert ns262.names_outside(result.sql or "", SCHEMA) == []
    again = graph.execute_cypher("MATCH (:P262 {name: 'b'})-[r:K262]->() RETURN r.z AS v", raise_on_error=True)
    assert [[_value(v) for v in r] for r in again.rows] == [[5]]


# --------------------------------------------------------------------------- T015: text search and PPR fallback

@pytest.fixture(scope="module")
def text_graph(fresh):
    engine = fresh["engine"]
    engine.execute_cypher(
        "CREATE (z:Fish262 {name: 'zebrafish262'})-[:EATS262 {note: 'quaggamussel262'}]->(m:Fish262 {name: 'mussel262'})",
        raise_on_error=True,
    )
    return engine


def test_entity_name_search_finds_the_node_with_no_sqluser_views(fresh, text_graph):
    from iris_vector_graph.text_search import TextSearchEngine

    found = TextSearchEngine(fresh["conn"]).extract_entity_names("zebrafish262", k=5)
    assert [(name, score) for _, name, score in found] == [("zebrafish262", 1.0)]


def test_qualifier_search_finds_the_edge(fresh, text_graph):
    from iris_vector_graph.text_search import TextSearchEngine

    found = TextSearchEngine(fresh["conn"]).search_entity_qualifiers("quaggamussel262", k=5)
    assert [(f["predicate"], float(f["text_match_score"])) for f in found] == [("EATS262", 1.0)]


def test_context_search_returns_the_label(fresh, text_graph):
    from iris_vector_graph.text_search import TextSearchEngine

    found = TextSearchEngine(fresh["conn"]).search_with_context("mussel262", k=5)
    assert [(f["matched_text"], f["entity_type"]) for f in found] == [("mussel262", "Fish262")]


def test_the_python_ppr_fallback_ranks_the_chain(graph):
    a =graph.execute_cypher("MATCH (x:P262 {name: 'a'}) RETURN id(x) AS i", raise_on_error=True).rows[0][0]
    scores = graph._kg_PERSONALIZED_PAGERANK_python_fallback([a], return_top_k=3)
    names = {graph.execute_cypher("MATCH (x:P262) WHERE id(x) = $i RETURN x.name", {"i": k},
                                  raise_on_error=True).rows[0][0]: v for k, v in scores.items()}
    assert set(names) == {"a", "b", "c"}
    assert names["a"] > names["b"] > names["c"] > 0


# --------------------------------------------------------------------------- T021: native executor and compiled plans

def test_compiled_native_plans_name_nothing_outside_the_ivg_schema(fresh, graph):
    from iris_vector_graph import IRISGraphEngine

    native = IRISGraphEngine(fresh["conn"], embedding_dimension=4, namespace=NS, native_cypher=True)
    result = native.execute_cypher("MATCH (x:P262)-[:K262]->(y:P262) RETURN x.name AS s, y.name AS t ORDER BY s",
                                   raise_on_error=True)
    assert [list(r) for r in result.rows] == [["a", "b"], ["b", "c"]]
    bodies = ns262.rows(fresh["conn"], "SELECT parent, Implementation FROM %Dictionary.MethodDefinition "
                                       "WHERE parent %STARTSWITH 'IVGC.' OR parent %STARTSWITH 'Graph.KG.Cypher.'")
    assert bodies, "no native executor or plan class is deployed"
    assert [p for p, impl in bodies if impl and ("SQLUser" in str(impl) or "User.func" in str(impl))] == []
