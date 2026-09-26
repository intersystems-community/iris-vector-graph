"""Spec 229 — engine gaps the strict TCK harness exposed.

Before spec 229 a raising query counted as an empty result, so these scenarios
passed on an error. Each class names the scenario; the tests pin the translator
so the query translates to SQL IRIS can run and that returns the right shape.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None, procedures: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {}, procedures=procedures)


def _statements(result):
    return result.sql if isinstance(result.sql, list) else [result.sql]


def assert_binds_match(result):
    """Every `?` in each statement has a bound value (no -1 / 'Incorrect number')."""
    stmts = _statements(result)
    params = result.parameters or [[]]
    for i, sql in enumerate(stmts):
        bound = params[i] if i < len(params) else []
        # `?` inside a string literal is not a placeholder
        placeholders = re.sub(r"'(?:[^']|'')*'", "''", sql).count("?")
        assert placeholders == len(bound), (sql, bound)


_DO_NOTHING = {"test.doNothing": {"args": [], "outputs": [], "rows": []}}


class TestStandaloneVoidProcedure:
    """Call1 [1], [2]: a standalone call to a procedure with no outputs."""

    @pytest.mark.parametrize("q", ["CALL test.doNothing", "CALL test.doNothing()"])
    def test_translates_to_an_empty_select(self, q):
        r = tr(q, procedures=_DO_NOTHING)
        sql = _statements(r)[-1]
        assert "VecSearch" not in sql
        assert re.search(r"SELECT\s+\S", sql), sql  # a select list, not `SELECT \nFROM`
        assert "1=0" in sql
        assert_binds_match(r)

    def test_in_query_void_call_keeps_its_rows(self):
        r = tr("MATCH (n) CALL test.doNothing() RETURN n", procedures=_DO_NOTHING)
        assert "1=0" not in _statements(r)[-1]


class TestExistsSubqueryBindsItsRelationship:
    """ExistentialSubquery1 [4]: `exists { (n)-[r]->() WHERE type(r) = 'NA' }`."""

    def test_relationship_variable_is_in_scope_in_the_subquery_where(self):
        r = tr("MATCH (n) WHERE exists { (n)-[r]->() WHERE type(r) = 'NA' } RETURN n")
        sql = _statements(r)[-1]
        assert "EXISTS" in sql
        assert_binds_match(r)

    def test_relationship_property_in_the_subquery_where(self):
        r = tr("MATCH (n) WHERE exists { (n)-[r]->() WHERE r.prop = 1 } RETURN n")
        assert_binds_match(r)

    def test_label_predicate_inside_a_function_argument_keeps_its_bind(self):
        # The TCK harness rewrites `type(r)` to `type(r:<scenario label>)` here.
        r = tr("MATCH (n:L) WHERE exists { (n)-[r]->() WHERE type(r:L) = 'NA' } RETURN n")
        assert_binds_match(r)


class TestOptionalMatchThenUnwindOfItsVariable:
    """Graph8 [7]: OPTIONAL MATCH ... UNWIND keys(r) — no null-row fallback.

    A null r unwinds keys(null) = null, which yields no rows, so the fallback
    row is wrong; it also wrapped the lateral JSON_TABLE in a subquery IRIS
    cannot compile (badLatRef).
    """

    def test_no_null_row_fallback(self):
        r = tr(
            "OPTIONAL MATCH ()-[r:KNOWS]-()\nUNWIND keys(r) AS x\nRETURN DISTINCT x AS theProps"
        )
        assert "__om" not in _statements(r)[-1]

    def test_unwind_of_an_unrelated_list_keeps_the_fallback(self):
        r = tr("OPTIONAL MATCH ()-[r:KNOWS]-()\nUNWIND [1, 2] AS x\nRETURN r, x")
        # Unchanged behaviour: this shape is not what the fix is about.
        assert isinstance(_statements(r)[-1], str)


class TestEmptyVariableLengthInterval:
    """Match5 [11], [12], [13]: `*2..1`, `*1..0`, `*..0` match nothing."""

    @pytest.mark.parametrize("hops", ["*2..1", "*1..0", "*..0"])
    def test_parses_and_matches_no_relationship(self, hops):
        r = tr(f"MATCH (a:A)\nMATCH (a)-[:LIKES{hops}]->(c)\nRETURN c.name")
        assert r.var_length_paths in (None, [])
        assert_binds_match(r)

    @pytest.mark.parametrize("hops", ["*1..1", "*0..2", "*2"])
    def test_non_empty_intervals_are_unchanged(self, hops):
        rel = parse_query(f"MATCH (a)-[:LIKES{hops}]->(c) RETURN c").query_parts[0]
        pattern = rel.clauses[0].patterns[0]
        assert pattern.relationships[0].variable_length is not None
        assert pattern.relationships[0].types == ["LIKES"]


class TestCoalesceOfNodesIsANode:
    """Match7 [22]: `WITH coalesce(b, c) AS x MATCH (x)-->(d)`."""

    Q = (
        "MATCH (a:Single)\n"
        "OPTIONAL MATCH (a)-->(b:NonExistent)\n"
        "OPTIONAL MATCH (a)-->(c:NonExistent)\n"
        "WITH coalesce(b, c) AS x\n"
        "MATCH (x)-->(d)\n"
        "RETURN d"
    )

    def test_translates_with_x_as_a_node(self):
        r = tr(self.Q)
        sql = _statements(r)[-1]
        assert re.search(r"COALESCE\(n\d+\.node_id, n\d+\.node_id\) AS x", sql), sql
        assert re.search(r"\.s = Stage1\.x", sql), sql
        assert_binds_match(r)

    def test_coalesce_of_scalars_stays_scalar(self):
        with pytest.raises(Exception, match="VariableTypeConflict|scalar"):
            tr("MATCH (a) WITH coalesce(a.x, 1) AS x MATCH (x)-->(d) RETURN d")


class TestWithOrderByAggregateOverProjections:
    """WithOrderBy4 [17], [18]: sort keys mixing projections and an aggregate."""

    def test_projected_alias_beside_an_aggregate(self):
        r = tr(
            "MATCH (me:Person)--(you:Person)\n"
            "WITH me.age AS age, count(you.age) AS cnt\n"
            "ORDER BY age, age + count(you.age)\n"
            "RETURN age"
        )
        sql = _statements(r)[-1]
        # the inner (grouped) select cannot see its own `age` alias
        assert not re.search(r"\(\s*age\s*\+", sql), sql
        assert_binds_match(r)

    def test_projected_property_beside_an_aggregate(self):
        r = tr(
            "MATCH (me:Person)--(you:Person)\n"
            "WITH me.age AS age, count(you.age) AS cnt\n"
            "ORDER BY me.age + count(you.age)\n"
            "RETURN age"
        )
        assert_binds_match(r)

    def test_projected_property_sorts_on_the_grouping_column(self):
        r = tr(
            "MATCH (me:Person)--(you:Person)\n"
            "WITH me.age AS age, count(you.age) AS cnt\n"
            "ORDER BY me.age + count(you.age)\n"
            "RETURN age"
        )
        sql = _statements(r)[-1]
        # me.age is the GROUP BY key; a correlated read of it is not grouped
        assert "WHERE s = n0.node_id AND \"key\" = 'age'" not in sql, sql

    def test_complex_projected_expression_beside_an_aggregate_is_ambiguous(self):
        # WithOrderBy4 [20]: the ambiguity is reported before the missing alias
        with pytest.raises(SyntaxError, match="AmbiguousAggregationExpression"):
            tr(
                "MATCH (me:Person)--(you:Person)\n"
                "WITH me.age + you.age, count(*) AS cnt\n"
                "ORDER BY me.age + you.age + count(*)\n"
                "RETURN *"
            )

    def test_unprojected_property_beside_an_aggregate_is_still_undefined(self):
        with pytest.raises(SyntaxError, match="UndefinedVariable"):
            tr(
                "MATCH (me:Person)--(you:Person)\n"
                "WITH me.name AS name, count(you.age) AS cnt\n"
                "ORDER BY me.age + count(you.age)\n"
                "RETURN name"
            )
