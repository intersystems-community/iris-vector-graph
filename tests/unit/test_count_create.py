"""CREATE runs once per incoming row (Create3 [1][2][3]).

`MATCH () CREATE ()` over two nodes creates two nodes, not one. When no clause
reads another's variables, only the number of rows matters, so the query runs as
a pipeline: each MATCH is counted, and each CREATE is run once per row.
"""

import pytest

from iris_vector_graph.cypher.count_create import plan_count_create
from iris_vector_graph.cypher.parser import parse_query


def _plan(q, params=None):
    return plan_count_create(parse_query(q), params or {})


def _kinds(plan):
    return [kind for kind, _q in plan]


class TestPlanShape:
    def test_match_then_create(self):
        plan = _plan("MATCH () CREATE ()")
        assert _kinds(plan) == ["count", "create"]

    def test_with_star_continues_the_pipeline(self):
        plan = _plan("MATCH () CREATE () WITH * CREATE ()")
        assert _kinds(plan) == ["count", "create", "create"]

    def test_second_match_multiplies_rows(self):
        plan = _plan("MATCH () CREATE () WITH * MATCH () CREATE ()")
        assert _kinds(plan) == ["count", "create", "count", "create"]

    def test_count_step_returns_count_star(self):
        from iris_vector_graph.cypher.translator import translate_to_sql

        (_k, q), _ = _plan("MATCH (:A) CREATE ()")
        t = translate_to_sql(q, {})
        assert not t.is_transactional
        assert "COUNT(" in str(t.sql).upper()

    def test_create_step_is_a_plain_create(self):
        _c, (_k, q) = _plan("MATCH () CREATE (:B {x: 1})")
        assert q.return_clause is None
        assert len(q.query_parts) == 1


class TestShapesKeptStatic:
    @pytest.mark.parametrize(
        "q",
        [
            # no MATCH before the CREATE: the static translation creates once
            "CREATE ()",
            # the CREATE uses a matched node
            "MATCH (a) CREATE (a)-[:R]->()",
            # the CREATE reads a matched property
            "MATCH (a) CREATE ({x: a.x})",
            # a RETURN reads the rows
            "MATCH () CREATE (b) RETURN b",
            # a WITH that projects, filters or aggregates
            "MATCH (a) CREATE () WITH a CREATE ()",
            "MATCH () CREATE () WITH * WHERE 1 = 1 CREATE ()",
            # WHERE on the MATCH
            "MATCH (a) WHERE a.x = 1 CREATE ()",
            "OPTIONAL MATCH () CREATE ()",
            # other clauses
            "MATCH () UNWIND [1, 2] AS x CREATE ()",
            "MATCH () MERGE ()",
        ],
    )
    def test_not_planned(self, q):
        assert _plan(q) is None


class TestEngineRunsThePipeline:
    """The engine counts each MATCH at its point in the pipeline and runs each
    CREATE once per row (Create3 [3]: 2 rows create 2 nodes, the second MATCH
    then sees 4 nodes per row, 8 rows create 8 more)."""

    @staticmethod
    def _fake(graph, calls):
        from iris_vector_graph.cypher import ast
        from iris_vector_graph.result import IVGResult

        class Fake:
            def _execute_parsed(self, q, params, procedures=None):
                if isinstance(q.query_parts[0].clauses[0], ast.CreateClause):
                    graph["nodes"] += 1
                    calls.append("create")
                    return IVGResult(columns=[], rows=[])
                calls.append("count")
                return IVGResult(columns=["__rows"], rows=[[graph["nodes"]]])

        return Fake()

    def test_rows_multiply_and_creates_repeat(self):
        from iris_vector_graph._engine.query import QueryMixin

        graph, calls = {"nodes": 2}, []
        steps = _plan("MATCH () CREATE () WITH * MATCH () CREATE ()")
        res = QueryMixin._execute_count_create(self._fake(graph, calls), steps, {})
        assert graph["nodes"] == 12  # 2 + 2 + 8
        assert calls.count("create") == 10
        assert res.rows == [] and res.columns == []

    def test_no_rows_creates_nothing(self):
        from iris_vector_graph._engine.query import QueryMixin

        graph, calls = {"nodes": 0}, []
        QueryMixin._execute_count_create(self._fake(graph, calls), _plan("MATCH () CREATE ()"), {})
        assert calls == ["count"]
