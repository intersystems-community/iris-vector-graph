"""4.7.0 row 33: `CALL ... YIELD ... WHERE` parses, and the WHERE filters the rows on every path a CALL takes.

Before 4.7.0 `CALL ivg.betweenness({}) YIELD node, score WHERE score > 0 RETURN node` raised CypherParseError
(`Expected EOF, got WHERE`). openCypher allows a WHERE straight after an explicit list of YIELD items (`oC_YieldItems`
ends in an optional `oC_Where`), not after `YIELD *` and not without a YIELD.

The paths:
- a procedure the translator answers (`translator._IVG_PROCEDURES`): the WHERE is the filter of a `WITH` of the
  yielded names, the query the KNOWN_ISSUES workaround spelled by hand;
- a procedure an engine handler answers (`db.*`, `dbms.*`, `apoc.*`, `gds.*`): the handler's rows are filtered in
  Python by `cypher.row_where`, whose answers here are worked by hand from openCypher's three-valued logic.

The live answers are in tests/e2e/test_470_call_yield_where_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph._engine.query import _handler_may_answer
from iris_vector_graph.cypher import ast
from iris_vector_graph.cypher.parser import CypherParseError, parse_query
from iris_vector_graph.cypher.row_where import handler_tail, row_passes, where_rows
from iris_vector_graph.cypher.translator import yield_where_as_with


def _where(q):
    return parse_query(q).procedure_call.yield_where


class TestParse:
    def test_where_after_yield_items_is_the_calls_filter(self):
        w = _where("CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0.5 RETURN node")
        assert isinstance(w, ast.WhereClause)
        assert w.expression.operator is ast.BooleanOperator.GREATER_THAN
        assert w.expression.operands == [ast.Variable("score"), ast.Literal(0.5)]

    def test_standalone_call_ends_in_the_where(self):
        q = parse_query("CALL db.labels() YIELD label WHERE label STARTS WITH 'A'")
        assert q.return_clause is None and q.query_parts == []
        assert q.procedure_call.yield_where.expression.operator is ast.BooleanOperator.STARTS_WITH

    def test_where_after_an_aliased_item(self):
        w = _where("CALL ivg.ppr(['a']) YIELD node AS n, score AS s WHERE s > 0 RETURN n")
        assert w.expression.operands[0] == ast.Variable("s")

    def test_in_query_call_takes_a_where(self):
        q = parse_query("MATCH (a:P) CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0 RETURN a, node")
        calls = [p.procedure_call for p in q.query_parts if p.procedure_call is not None]
        assert len(calls) == 1 and calls[0].yield_where is not None

    def test_where_then_with_then_return(self):
        q = parse_query("CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0 WITH node RETURN node")
        assert q.procedure_call.yield_where is not None
        assert q.query_parts and q.query_parts[0].with_clause is not None

    @pytest.mark.parametrize("q", [
        "CALL db.labels() YIELD * WHERE label = 'A'",  # openCypher: no WHERE after YIELD *
        "CALL db.labels() WHERE label = 'A'",  # nor without a YIELD
    ])
    def test_where_without_yield_items_stays_a_parse_error(self, q):
        with pytest.raises(CypherParseError):
            parse_query(q)


class TestTranslatorRewrite:
    def test_the_where_is_a_with_of_the_yielded_names(self):
        q = yield_where_as_with(parse_query("CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0.5 RETURN node"))
        assert q.procedure_call.yield_where is None
        w = q.query_parts[0].with_clause
        assert [i.expression for i in w.items] == [ast.Variable("node"), ast.Variable("score")]
        assert w.where_clause.expression.operands == [ast.Variable("score"), ast.Literal(0.5)]
        assert q.return_clause.items[0].expression == ast.Variable("node")

    def test_aliases_are_the_names_the_with_carries(self):
        q = yield_where_as_with(parse_query("CALL ivg.ppr(['a']) YIELD node AS n, score AS s WHERE s > 0 RETURN n"))
        assert [i.expression for i in q.query_parts[0].with_clause.items] == [ast.Variable("n"), ast.Variable("s")]

    def test_a_standalone_call_stays_standalone(self):
        """No RETURN is added: a standalone call answers the yielded names as one without the WHERE does (a node as
        its ID), read from the WITH stage."""
        q = yield_where_as_with(parse_query("CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0.5"))
        assert q.return_clause is None
        assert [p.clauses for p in q.query_parts] == [[], []] and q.query_parts[0].with_clause is not None

    def test_a_with_after_the_where_is_kept_after_it(self):
        q = yield_where_as_with(parse_query("CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0 WITH node "
                                            "RETURN node"))
        assert q.query_parts[0].with_clause.where_clause is not None
        assert q.query_parts[1].with_clause.where_clause is None
        assert [i.expression for i in q.query_parts[1].with_clause.items] == [ast.Variable("node")]

    def test_in_query_call_filters_every_name_in_scope(self):
        q = yield_where_as_with(parse_query("MATCH (a:P) CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0 "
                                            "RETURN a, node"))
        part = next(p for p in q.query_parts if p.procedure_call is not None)
        assert part.procedure_call.yield_where is None
        assert part.with_clause.star and part.with_clause.where_clause is not None

    def test_the_parsed_query_is_not_changed(self):
        parsed = parse_query("CALL ivg.ppr(['a']) YIELD node, score WHERE score > 0.5 RETURN node")
        yield_where_as_with(parsed)
        assert parsed.procedure_call.yield_where is not None and parsed.query_parts == []


class TestHandlerRouting:
    @pytest.mark.parametrize("q", [
        "CALL ivg.vector.search('L', 'emb', [1.0, 0.0], 2) YIELD node, score WHERE score > 0.5 RETURN node, score",
        "CALL ivg.shortestPath.weighted('a', 'b') YIELD path, totalCost WHERE totalCost < 2 RETURN path, totalCost",
    ])
    def test_a_procedure_the_translator_knows_goes_to_the_translator(self, q):
        assert _handler_may_answer(parse_query(q)) is False

    @pytest.mark.parametrize("q", [
        "CALL db.labels() YIELD label WHERE label = 'A' RETURN label",
        "CALL gds.pageRank.stream('g') YIELD nodeId, score WHERE score > 0.1 RETURN nodeId, score",
        "CALL gds.pageRank.stream('g') YIELD nodeId, score WHERE score > 0.1",
    ])
    def test_a_handler_procedure_stays_with_its_handler(self, q):
        assert _handler_may_answer(parse_query(q)) is True


ROW = {"label": "Alpha", "score": 0.25, "n": 3, "none": None, "ok": True, "tags": ["x", "y"]}


def _passes(expr_text, row=ROW, params=None):
    where = parse_query(f"CALL db.labels() YIELD label WHERE {expr_text}").procedure_call.yield_where
    return row_passes(where.expression, row, params or {})


class TestRowWhere:
    @pytest.mark.parametrize("expr, want", [
        ("score > 0.2", True), ("score > 0.25", False), ("score >= 0.25", True), ("score < 1", True),
        ("score <= 0.25", True), ("n = 3", True), ("n = 3.0", True), ("n <> 3", False), ("label = 'Alpha'", True),
        ("label = 'alpha'", False), ("label < 'B'", True),
        ("label IN ['Beta', 'Alpha']", True), ("label IN []", False), ("n IN [1, 2]", False),
        ("none IS NULL", True), ("none IS NOT NULL", False), ("label IS NULL", False),
        ("ok = true", True), ("ok = 1", False),  # a boolean is not the integer 1
        ("label = 1", False),  # = across types is false, not null
        ("score > 0.2 AND n = 3", True), ("score > 0.3 OR n = 3", True), ("score > 0.2 XOR n = 3", False),
        ("NOT score > 0.3", True), ("score > $t", True), ("label = $l", True),
        ("score > -1", True),
    ])
    def test_answers_as_opencypher(self, expr, want):
        assert _passes(expr, params={"t": 0.1, "l": "Alpha"}) is want

    @pytest.mark.parametrize("expr", [
        # a comparison with null is null, and a row is kept only on true
        "score > 1", "NOT (score > 1)", "score = 1", "score <> 1", "NOT score = 1",
        "label = 'Alpha' AND score > 1", "label > 1",  # ordering across types is null
        "n IN [1, null]", "score IN [0.25]",
    ])
    def test_null_drops_the_row(self, expr):
        assert _passes(expr, row=dict(ROW, score=None)) is False

    @pytest.mark.parametrize("expr, want", [
        ("score > 1 OR n = 3", True),  # null OR true is true
        ("score > 1 AND n = 4", False),  # null AND false is false
        ("NOT (score > 1 AND n = 4)", True),
        ("score > 1 XOR n = 3", False),  # null XOR anything is null
        ("n IN [3, null]", True),
        ("score IS NULL", True),
    ])
    def test_three_valued_logic(self, expr, want):
        assert _passes(expr, row=dict(ROW, score=None)) is want

    def test_an_undefined_name_raises(self):
        with pytest.raises(ValueError, match="nope"):
            _passes("nope = 1")

    @pytest.mark.parametrize("expr", [
        "toLower(label) = 'alpha'", "score * 2 > 0.4", "size(tags) = 2",
        "label STARTS WITH 'Al'", "label ENDS WITH 'ha'", "label CONTAINS 'lph'", "label =~ 'A.*'",
        "label IN $names", "'x' IN tags", "tags = ['x', 'y']",
    ])
    def test_an_expression_it_cannot_read_raises(self, expr):
        with pytest.raises(ValueError, match="WHERE after YIELD"):
            _passes(expr, params={"names": ["Alpha"]})

    def test_where_rows_filters_and_keeps_order_and_columns(self):
        from iris_vector_graph.engine import IVGResult

        call = parse_query("CALL gds.pageRank.stream('g') YIELD nodeId, score WHERE score > 0.3 "
                           "RETURN nodeId").procedure_call
        res = IVGResult(columns=["nodeId", "score"], rows=[["c", 0.5], ["a", 0.2], ["b", 0.4], ["d", None]])
        out = where_rows(res, call, {})
        assert out.columns == ["nodeId", "score"] and out.rows == [["c", 0.5], ["b", 0.4]]

    def test_where_rows_reads_an_alias(self):
        from iris_vector_graph.engine import IVGResult

        call = parse_query("CALL db.labels() YIELD label AS l WHERE l = 'B' RETURN l").procedure_call
        res = IVGResult(columns=["label"], rows=[["A"], ["B"]])
        assert where_rows(res, call, {}).rows == [["B"]]

    def test_where_rows_does_not_see_a_column_not_yielded(self):
        from iris_vector_graph.engine import IVGResult

        call = parse_query("CALL gds.pageRank.stream('g') YIELD nodeId WHERE score > 0 RETURN nodeId").procedure_call
        res = IVGResult(columns=["nodeId", "score"], rows=[["c", 0.5]])
        with pytest.raises(ValueError, match="score"):
            where_rows(res, call, {})


class TestHandlerTail:
    """The clauses after a handler procedure's YIELD: applied, or a raise naming the one that is not (before 4.7.0
    a WITH, MATCH or a RETURN expression after `CALL db.labels() YIELD label` was dropped, and every label came
    back)."""

    @pytest.mark.parametrize("q, conditions, names", [
        ("CALL db.labels() YIELD label", 0, None),
        ("CALL db.labels() YIELD label RETURN label", 0, ["label"]),
        ("CALL db.labels() YIELD label WHERE label = 'A' RETURN label", 1, ["label"]),
        ("CALL db.labels() YIELD label WITH label WHERE label = 'A' RETURN label", 1, ["label"]),
        ("CALL db.labels() YIELD label WHERE label <> 'B' WITH label WHERE label = 'A' RETURN label", 2, ["label"]),
        ("CALL db.labels() YIELD label AS l WITH l RETURN l ORDER BY l LIMIT 1", 0, ["l"]),
        ("CALL dbms.components() YIELD name, edition RETURN edition", 0, ["edition"]),
    ])
    def test_shapes_it_applies(self, q, conditions, names):
        got_conditions, got_names = handler_tail(parse_query(q))
        assert len(got_conditions) == conditions and got_names == names

    @pytest.mark.parametrize("q, clause", [
        ("CALL db.labels() YIELD label MATCH (n) RETURN label", "MATCH"),
        ("CALL db.labels() YIELD label OPTIONAL MATCH (n) RETURN label", "MATCH"),
        ("CALL db.labels() YIELD label WITH label ORDER BY label LIMIT 1 RETURN label", "WITH that does more"),
        ("CALL db.labels() YIELD label WITH DISTINCT label RETURN label", "WITH that does more"),
        ("CALL db.labels() YIELD label WITH label AS x RETURN x", "WITH label AS x"),
        ("CALL db.labels() YIELD label WITH label, 1 AS one RETURN label", "WITH"),
        ("CALL db.labels() YIELD label WITH label MATCH (n) RETURN label", "MATCH"),
        ("CALL db.labels() YIELD label WITH label WITH label RETURN label", "second WITH"),
        ("CALL db.labels() YIELD label RETURN count(label)", "RETURN"),
        ("CALL db.labels() YIELD label RETURN toLower(label)", "RETURN"),
        ("CALL db.labels() YIELD label RETURN DISTINCT label", "RETURN DISTINCT"),
        ("CALL db.labels() YIELD label RETURN label AS l", "RETURN label AS l"),
        ("CALL db.labels() YIELD label WITH label RETURN 1 AS one", "RETURN"),
    ])
    def test_a_clause_it_cannot_apply_raises_naming_it(self, q, clause):
        with pytest.raises(ValueError, match=clause):
            handler_tail(parse_query(q))
