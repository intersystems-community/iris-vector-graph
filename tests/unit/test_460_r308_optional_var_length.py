"""4.6.0 R308: an OPTIONAL MATCH over a variable-length hop that the translator leaves to the engine, from a start an
earlier clause binds, is refused with a ValueError naming the shape and the rewrite. The engine's labeled route
answered it with rows of the wrong width and the start nulled. The live answers are
tests/e2e/test_460_r308_optional_var_length_e2e.py."""

from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

HOP = "-[:R*1..2]->"
REFUSED = {
    "where on the new end": f"MATCH (a:L {{name: 'cat'}}) OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'eve' "
    "RETURN a.name, b.name",
    "where reads both ends": f"MATCH (a:L) OPTIONAL MATCH (a){HOP}(b) WHERE b.name > a.name RETURN a.name, b.name",
    "a map on the new end": f"MATCH (a:L) OPTIONAL MATCH (a){HOP}(b {{name: 'dan'}}) RETURN a.name, b.name",
    "labels and a map on the new end": f"MATCH (a:L) OPTIONAL MATCH (a){HOP}(b:X {{name: 'eve'}}) "
    "RETURN a.name, b.name",
    "the start's labels": f"MATCH (a:L) OPTIONAL MATCH (a:L){HOP}(b) RETURN a.name, b.name",
    "the start's map": f"MATCH (a:L) OPTIONAL MATCH (a {{name: 'cat'}}){HOP}(b) RETURN a.name, b.name",
    "inside CALL": f"MATCH (a:L) CALL {{ WITH a OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'eve' RETURN b }} "
    "RETURN a.name, b.name",
    "inside CALL, a map": f"MATCH (a:L) CALL {{ WITH a OPTIONAL MATCH (a){HOP}(b {{name: 'dan'}}) RETURN b }} "
    "RETURN a.name, b.name",
    "an id-bound start": f"MATCH (a {{node_id: 'x'}}) OPTIONAL MATCH (a){HOP}(b) WHERE b.name = 'eve' "
    "RETURN a.name, b.name",
    "the relationship list": f"MATCH (a:L) OPTIONAL MATCH (a)-[r:R*1..2]->(b) WHERE b.name = 'eve' "
    "RETURN a.name, size(r)",
    "another hop": f"MATCH (a:L) OPTIONAL MATCH (a){HOP}(b)-[:R]->(c) RETURN a.name, b.name, c.name",
}
#: SQL or the engine's routes answer these right (live: the leading-OPTIONAL cases of the E2E file)
ANSWERED = {
    "a leading OPTIONAL from an id": f"OPTIONAL MATCH (a {{node_id: 'x'}}){HOP}(b) RETURN a.name, b.name",
    "a leading OPTIONAL, a where": f"OPTIONAL MATCH (a:L {{name: 'cat'}}){HOP}(b) WHERE b.name = 'eve' RETURN b.name",
    "a leading OPTIONAL, a map on the new end": f"OPTIONAL MATCH (a:L){HOP}(b {{name: 'dan'}}) RETURN b.name",
}


def _vl0(query):
    sq = translate_to_sql(parse_query(query), {})
    assert sq.var_length_paths, f"the translator answered {query!r} in SQL"
    return sq, sq.var_length_paths[0]


def _engine():
    from iris_vector_graph.engine import IRISGraphEngine

    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._schema_prefix = "Graph_KG"
    eng._store = MagicMock()
    eng._nkg_dirty = False
    eng._table_mapping_cache = {}  # no label is mapped to a table of its own
    return eng


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_an_optional_hop_from_a_bound_start_is_refused_with_the_rewrite(name):
    from iris_vector_graph._engine.query import optional_hop_refusal

    _sq, vl0 = _vl0(REFUSED[name])
    message = optional_hop_refusal(vl0)
    assert message is not None, name
    assert message.startswith("OPTIONAL MATCH (a)-[") and "R*1..2]->(" in message
    assert "WITH a, b WHERE" in message and "null" in message


@pytest.mark.parametrize("name", sorted(ANSWERED))
def test_a_leading_optional_hop_is_left_to_the_routes_that_answer_it(name):
    from iris_vector_graph._engine.query import optional_hop_refusal

    sq = translate_to_sql(parse_query(ANSWERED[name]), {})
    assert all(optional_hop_refusal(vl) is None for vl in sq.var_length_paths or [])


def test_the_engine_raises_before_it_reads_anything():
    sq, _vl0_ = _vl0(REFUSED["where on the new end"])
    eng = _engine()
    with pytest.raises(ValueError, match=r"OPTIONAL MATCH \(a\)-\[:R\*1\.\.2\]->\(b\)"):
        eng._route_var_length(sq, {})
    assert not eng._store.method_calls and not eng.conn.method_calls


def test_a_shortest_path_under_optional_is_not_refused():
    from iris_vector_graph._engine.query import optional_hop_refusal

    _sq, vl0 = _vl0("MATCH (a:L), (c:L) OPTIONAL MATCH p = shortestPath((a)-[:R*1..3]->(c)) RETURN length(p)")
    assert optional_hop_refusal(vl0) is None


# ---- the fix: the engine walks from the exact starts, and the statement LEFT JOINs those paths as one group ----



NO_MAP = sorted(REFUSED)


def _rel(tree):
    from iris_vector_graph._engine.query import _optional_hop_rel

    return _optional_hop_rel(tree)


@pytest.mark.parametrize("name", NO_MAP)
def test_the_translator_records_the_walk_from_the_exact_starts(name):
    sq, vl0 = _vl0(REFUSED[name])
    sql, params = vl0["optional_paths_sql"]
    assert "CY_VLP_PATHS(st.sid" in sql and "SELECT DISTINCT" in sql
    assert sql.count("?") == len(params)
    # the starts are the rows of the clauses before the OPTIONAL MATCH: the earlier MATCH's label is read
    if "MATCH (a:L)" in REFUSED[name]:
        assert "L" in params


def test_the_walk_takes_the_start_named_by_id_and_its_graph():
    _sq, vl0 = _vl0(REFUSED["an id-bound start"])
    sql, params = vl0["optional_paths_sql"]
    assert "x" in params or "'x'" in sql
    assert "st.gid" in sql  # each start walks its own graph


def test_a_hop_with_a_map_is_left_to_the_checked_route():
    _sq, vl0 = _vl0(f"MATCH (a:L) OPTIONAL MATCH (a)-[:R*1..2 {{w: 1}}]->(b) WHERE b.name = 'eve' RETURN b.name")
    assert not vl0.get("optional_paths_sql")


PATHS = [
    {"s": "c", "g": "", "t": "d", "l": 1, "n": '["d"]', "y": '["R"]', "r": '[{"type":"R","props":{}}]',
     "k": "\x02c\x01R\x01d\x02"},
    {"s": "c", "g": "", "t": "e(?)", "l": 2, "n": '["d","e(?)"]', "y": '["R","R"]',
     "r": '[{"type":"R","props":{}},{"type":"R","props":{}}]', "k": "\x02c\x01R\x01d\x02d\x01R\x01e(?)\x02"},
]


def _over_paths(query, paths=PATHS):
    tree = copy.deepcopy(parse_query(query))  # the parse is cached: a copy keeps the paths off later parses
    _rel(tree).variable_length.literal_paths = json.dumps(paths)
    return translate_to_sql(tree, {"n": "eve"})


@pytest.mark.parametrize("name", NO_MAP)
def test_over_the_paths_the_hop_is_one_left_join_group(name):
    sq = _over_paths(REFUSED[name])
    assert not sq.var_length_paths, name
    sql = sq.sql
    # one group holds the paths, the far node and the conditions (the start's labels or map join inside it too)
    group = sql[sql.index("LEFT OUTER JOIN ("):]
    assert "JSON_TABLE(" in group.split(") ON ")[0], sql
    # the literal's ? ( ) are escaped: every ? left is a bind
    assert sql.count("?") == len(sq.parameters[0]), sql
    assert "e(?)" not in sql


def test_the_where_on_the_new_end_is_in_the_groups_on():
    sql = _over_paths(REFUSED["where on the new end"]).sql
    group = sql[sql.index("LEFT OUTER JOIN (JSON_TABLE("):]
    tail = group.split("\nWHERE ")[1] if "\nWHERE " in group else ""
    assert "'eve'" in group.split("\nWHERE ")[0] and "'eve'" not in tail, sql


def test_a_json_table_join_parses():
    from iris_vector_graph.cypher.translator import _mpt_parse_left_join

    jc = ("LEFT OUTER JOIN JSON_TABLE('[{\"s\":\"a)b\",\"t\":\"x''y\"}]', '$[*]' COLUMNS(s VARCHAR(512) PATH '$.s')) "
          "vlp3 ON vlp3.s = n0.node_id AND 1=1")
    src, alias, cond = _mpt_parse_left_join(jc)
    assert src.startswith("JSON_TABLE(") and src.endswith("))") and alias == "vlp3"
    assert cond == "vlp3.s = n0.node_id AND 1=1"


def test_the_engine_walks_then_runs_the_query_over_the_paths():
    from iris_vector_graph._engine.query import IVGResult

    sq, _vl = _vl0(REFUSED["where on the new end"])
    sq.parsed_query = parse_query(REFUSED["where on the new end"])
    sq.return_items = list(sq.parsed_query.return_clause.items)
    eng = _engine()
    walked = IVGResult(columns=list("sgtlnyrk"), rows=[[p[c] for c in "sgtlnyrk"] for p in PATHS])
    answered = IVGResult(columns=["a_name", "b_name"], rows=[["cat", "eve"]])
    eng._store.execute_sql.side_effect = [walked, answered]
    out = eng._route_var_length(sq, {})
    (walk_sql, _), (final_sql, final_params) = [c.args for c in eng._store.execute_sql.call_args_list]
    assert "CY_VLP_PATHS(st.sid" in walk_sql
    assert "LEFT OUTER JOIN (JSON_TABLE(" in final_sql and final_sql.count("?") == len(final_params)
    assert [list(r) for r in out.rows] == [["cat", "eve"]]


def test_a_walk_too_large_for_one_statement_raises():
    from iris_vector_graph._engine import query as q
    from iris_vector_graph._engine.query import IVGResult

    sq, _vl = _vl0(REFUSED["where on the new end"])
    sq.parsed_query = parse_query(REFUSED["where on the new end"])
    eng = _engine()
    big = [dict(PATHS[0], t=f"n{i}") for i in range(q._OPTIONAL_PATHS_MAX + 1)]
    eng._store.execute_sql.side_effect = [IVGResult(columns=list("sgtlnyrk"),
                                                    rows=[[p[c] for c in "sgtlnyrk"] for p in big])]
    with pytest.raises(ValueError, match="WITH a, b WHERE"):
        eng._route_var_length(sq, {})


def test_the_literal_is_escaped_for_a_statement():
    from iris_vector_graph.cypher.translator import _vlp_literal_sql

    text = _vlp_literal_sql(json.dumps([{"t": "o'k(?)"}]))
    assert text.startswith("'") and text.endswith("'")
    assert "?" not in text and "(" not in text and ")" not in text and "o''k" in text



def test_size_of_a_missed_optional_relationship_list_is_null():
    """`size(r)` of an OPTIONAL hop that matched nothing: r is null, so null; JSON_ARRAYLENGTH(NULL) failed the query
    (<INVALID OREF>, SQLCODE -149) on the SQL route and over literal paths alike."""
    sql = translate_to_sql(parse_query("MATCH (a:L) OPTIONAL MATCH (a)-[r:R*1..2]->(b) RETURN a.name, size(r)"), {}).sql
    assert "JSON_ARRAYLENGTH" in sql
    assert all(f"IS NULL THEN NULL ELSE {part}" in sql for part in ["Graph_KG.JSON_ARRAYLENGTH("]), sql


NEW_START = "MATCH (x:L {name: 'cat'}) OPTIONAL MATCH (a:L)-[:R*1..2]->(x) RETURN a.name, x.name"


def test_a_new_start_to_a_bound_end_walks_back_from_the_end():
    """`(a:L)-[*]->(x)` with x bound and a new: the walk starts at x's rows and goes the other way; it raised
    "Undefined variable: a"."""
    _sq, vl0 = _vl0(NEW_START)
    sql, params = vl0["optional_paths_sql"]
    assert "'in'" in sql and "cat" in sql + repr(params)
    sq = _over_paths(NEW_START)
    assert not sq.var_length_paths and "LEFT OUTER JOIN (JSON_TABLE(" in sq.sql
    assert sq.sql.count("?") == len(sq.parameters[0])


@pytest.mark.parametrize("query", [
    "MATCH (x:L) OPTIONAL MATCH (a:L)-[r:R*1..2]->(x) RETURN a.name, size(r)",
    "MATCH (x:L) OPTIONAL MATCH p = (a:L)-[:R*1..2]->(x) RETURN a.name, length(p)",
])
def test_a_walk_back_that_a_path_or_relationship_list_reads_is_refused(query):
    """A list or path read in the pattern's order would come back reversed: refused, naming the rewrite."""
    sq = translate_to_sql(parse_query(query), {})
    vl0 = (sq.var_length_paths or [{}])[0]
    assert not vl0.get("optional_paths_sql")
    from iris_vector_graph._engine.query import optional_hop_refusal

    assert optional_hop_refusal(vl0) is not None


def test_the_hop_is_found_when_a_parameter_shares_a_variables_name():
    """`$a` beside a variable `a`: the translator reads a renamed twin of the tree (which copies only what the renaming
    changes); the walk still finds its hop in the copy it translates again."""
    from iris_vector_graph._engine.query import _optional_hop_rel

    query = "MATCH (a:L) WHERE a.name = $a OPTIONAL MATCH (a)-[:R*1..2]->(b) WHERE b.name = 'eve' RETURN a.name, b.name"
    parsed = parse_query(query)
    sq = translate_to_sql(parsed, {"a": "cat"})
    vl0 = sq.var_length_paths[0]
    assert vl0["optional_paths_sql"]
    rel = _optional_hop_rel(copy.deepcopy(parsed), vl0["rel_id"], parsed)
    assert rel is not None and rel.variable_length.max_hops == 2


def test_a_path_walked_across_every_graph_ends_at_its_id_in_any_graph():
    """A start whose graph the row does not carry is walked across every graph, and its paths say so (`ga`: 1): the
    far node is matched by id alone there. A null `g` cannot say it: JSON_TABLE reads "" (the default graph) as null
    too, so a default-graph path ended at the id in every graph."""
    sql = _over_paths(REFUSED["where on the new end"], [dict(PATHS[0], g=None, ga=1)]).sql
    assert "ga INTEGER PATH '$.ga'" in sql
    assert ".ga = 1 OR COALESCE(" in sql and ".g IS NULL OR" not in sql


def test_the_engine_marks_a_walk_across_every_graph():
    from iris_vector_graph._engine.query import IVGResult

    sq, _vl = _vl0(REFUSED["where on the new end"])
    sq.parsed_query = parse_query(REFUSED["where on the new end"])
    eng = _engine()
    rows = [[None if c == "g" else PATHS[0][c] for c in "sgtlnyrk"], [PATHS[0][c] for c in "sgtlnyrk"]]
    eng._store.execute_sql.side_effect = [IVGResult(columns=list("sgtlnyrk"), rows=rows),
                                          IVGResult(columns=["a_name", "b_name"], rows=[])]
    eng._route_var_length(sq, {})
    final_sql = eng._store.execute_sql.call_args_list[1].args[0]
    text = final_sql.replace(" ", "")
    assert '"g":"","ga":1' in text and text.count('"ga":1') == 1, final_sql


def test_a_start_with_its_graph_walks_in_it():
    _sq, vl0 = _vl0(REFUSED["where on the new end"])
    sql, _params = vl0["optional_paths_sql"]
    assert "COALESCE(n0.graph_id, '') AS gid" in sql and "0, st.gid" in sql


# ---- an OPTIONAL MATCH's WHERE ordering two stored values: CY_CMP, not a cast to DOUBLE ----

TWO_PROPS_SINGLE = "MATCH (a:L) OPTIONAL MATCH (a)-[:R]->(b) WHERE b.name > a.name RETURN a.name, b.name"
TWO_PROPS_VL = "MATCH (a:L) OPTIONAL MATCH (a)-[:R*1..2]->(b) WHERE b.name > a.name RETURN a.name, b.name"


@pytest.mark.parametrize("translated", [
    lambda: translate_to_sql(parse_query(TWO_PROPS_SINGLE), {}),
    lambda: _over_paths(TWO_PROPS_VL),
], ids=["single hop", "variable-length"])
def test_an_optional_where_orders_two_stored_values_by_cypher_order(translated):
    """`b.name > a.name` in an OPTIONAL MATCH's WHERE cast both sides to DOUBLE (every name 0, never greater): IRIS
    refuses CY_CMP in an outer join's ON when it reads an outer-joined column beside an inner one (-94), so each side is
    read by a correlated subquery, which it takes."""
    sq = translated()
    text = sq.sql
    assert "AS DOUBLE) >" not in text and "AS DOUBLE) <" not in text, text
    assert "CY_CMP((SELECT val FROM" in text, text
    assert text.count("?") == len(sq.parameters[0]), text


def test_a_match_where_keeps_its_comparison():
    sql = translate_to_sql(parse_query("MATCH (a:L)-[:R]->(b) WHERE b.name > a.name RETURN a.name"), {}).sql
    assert "CY_CMP(" in sql and "CY_CMP((SELECT val FROM" not in sql


def test_a_named_paths_length_over_the_group_is_an_integer_column():
    """intersystems-irispython 5.4.1 read `(0 + vlp.l)` from inside the OPTIONAL group as text ('2'); 5.4.0 as 2."""
    sql = _over_paths("MATCH (a:L {name: 'cat'}) OPTIONAL MATCH p = (a)-[:R*1..2]->(b) WHERE b.name = 'eve' "
                      "RETURN a.name, length(p)").sql
    assert "CAST((0 + vlp" in sql and "AS INTEGER) AS length_p" in sql.replace("\n", " "), sql


# ---- an all-graphs read keeps each node of an OPTIONAL MATCH's join group in its graph ----

@pytest.mark.parametrize("query", [
    "MATCH (a:L) OPTIONAL MATCH (a)-[:R]->(b) WHERE b.name = 'dan' RETURN a.name, b.name",
    "MATCH (a:L) OPTIONAL MATCH (a)-[:R]->(b)-[:R]->(c) RETURN a.name, b.name, c.name",
])
def test_the_optional_group_correlates_graphs(query):
    """A node id held by two graphs: the group's joins were on node_id alone (the graph pass read the first table of a
    clause only), so `cat -> dan` answered twice where g63 holds a `dan` too (live, IVG460FXL63)."""
    sql = translate_to_sql(parse_query(query), {}).sql
    group = next(line for line in sql.split("\n") if line.startswith("LEFT OUTER JOIN ("))
    inner = group[len("LEFT OUTER JOIN ("):group.index(") ON ")]
    for alias, ref in __import__("re").findall(r"JOIN \S+ (\w+) ON \1\.node_id = (\w+)\.o_id", inner):
        assert f"{alias}.graph_id = {ref}.graph_id" in inner, group
    first = __import__("re").match(r"\S+ (\w+) ", inner).group(1)
    assert f"{first}.graph_id = n0.graph_id" in group, group


def test_the_walked_groups_second_hop_correlates_graphs():
    sql = _over_paths("MATCH (a:L) OPTIONAL MATCH (a)-[:R*1..2]->(b)-[:R]->(c) RETURN a.name, b.name, c.name").sql
    group = next(line for line in sql.split("\n") if line.startswith("LEFT OUTER JOIN ("))
    re_ = __import__("re")
    edge = re_.search(r"JOIN \S+rdf_edges (\w+) ON", group).group(1)
    assert re_.search(rf"\b{edge}\.graph_id = n\d+\.graph_id|\bn\d+\.graph_id = {edge}\.graph_id", group), group
