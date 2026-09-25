"""Spec 203 — TCK scenarios that passed at 5fc72b9 (or v2.5.0) and regressed later.

Each class names the openCypher TCK scenario and the commit that broke it
(found by `git bisect`). The tests pin the translator behaviour so the
scenario cannot silently regress again.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


class TestWithOrderByAggregationScope:
    """WithOrderBy2 [25], WithOrderBy4 [19]; broken by a6e0d6d.

    The error used to come from IRIS rejecting the generated SQL. a6e0d6d made
    that SQL valid, so the query ran and returned rows instead of failing.
    """

    @pytest.mark.parametrize(
        "sort",
        [
            "count(1)",
            "count(n)",
            "count(1 + n.num1)",
            "max(n.num2) DESC",
            "max(n.num2), n.name",
            "n.name ASC, max(n.num2) DESC",
        ],
    )
    def test_aggregate_sort_in_a_non_aggregating_with_is_invalid(self, sort):
        with pytest.raises(SyntaxError, match="InvalidAggregation"):
            tr(f"MATCH (n) WITH n.num1 AS foo ORDER BY {sort} RETURN foo AS foo")

    def test_unprojected_variable_beside_an_aggregate_is_undefined(self):
        with pytest.raises(SyntaxError, match="UndefinedVariable"):
            tr(
                "MATCH (me:Person)--(you:Person) WITH count(you.age) AS agg "
                "ORDER BY me.age + count(you.age) RETURN *"
            )

    def test_sorting_an_aggregating_with_by_its_alias_translates(self):
        tr("MATCH (n) WITH n.name AS name, count(*) AS c ORDER BY c DESC RETURN name, c")

    def test_sorting_a_non_aggregating_with_by_an_unprojected_property_translates(self):
        tr("MATCH (n) WITH n.name AS name ORDER BY n.age RETURN name")


class TestRepeatedListPredicatesAreHoisted:
    """Precedence1 [23], [25]; broken by bcdd94b.

    bcdd94b's 3VL CASE repeats its condition, so `all(...) AND any(...)` puts
    four JSON_TABLE readers in the final SELECT. IRIS fails at Prepare with
    -400 <LIST>LoadTableFunction and the query returns no rows. Each repeated
    list predicate is now computed once, in a derived table that keeps the
    stage's name.
    """

    QUERY = (
        "UNWIND [true, false, null] AS a UNWIND [true, false, null] AS b "
        "WITH collect((a <= b IS NULL) = (a <= (b IS NULL))) AS eq, "
        "collect((a <= b IS NULL) <> ((a <= b) IS NULL)) AS neq "
        "RETURN all(x IN eq WHERE x) AND any(x IN neq WHERE x) AS result"
    )

    @pytest.mark.parametrize("comp", ["=", "<="])
    def test_repeated_list_predicates_are_computed_once(self, comp):
        sql = tr(self.QUERY.replace("<=", comp)).sql
        assert sql.count("JSON_TABLE(Stage1.eq") == 1
        assert sql.count("JSON_TABLE(Stage1.neq") == 1
        assert ") Stage1" in sql

    def test_a_list_predicate_used_once_is_not_hoisted(self):
        sql = tr(
            "UNWIND [1, 2] AS a WITH collect(a) AS xs RETURN any(x IN xs WHERE x > 1) AS r"
        ).sql
        assert "__lp" not in sql

    def test_a_repeated_in_list_subquery_is_not_hoisted(self):
        # List5 [3]: `x IN (SELECT ...)` is a row set, not a scalar column.
        sql = tr("WITH [1, 2, 3] AS list RETURN 3 IN list[0..1] AS r").sql
        assert "__lp" not in sql

    def test_plain_comparisons_keep_three_valued_logic(self):
        sql = tr(self.QUERY).sql
        assert "THEN 0 ELSE NULL END" in sql


class TestIntegerIdIsAProperty:
    """Comparison1 [10]; broken by 5332b6f.

    CREATE uses `id` as the node identifier only when it is a string; an
    integer `id` is stored as a property. 5332b6f made MATCH `{id: ...}`
    always compare node_id, so an integer id never matched its own node.
    """

    def test_integer_id_matches_the_property(self):
        sql = tr("MATCH (p:TheLabel {id: 4611686018427387905}) RETURN p.id").sql
        assert "node_id = 4611686018427387905" not in sql
        assert ".val = " in sql

    def test_string_id_still_matches_node_id(self):
        sql = tr("MATCH (p:TheLabel {id: 'abc'}) RETURN p.id").sql
        assert ".node_id = " in sql

    def test_integer_id_on_a_relationship_endpoint_matches_the_property(self):
        sql = tr("MATCH (a {id: 7})-[:R]->(b) RETURN b").sql
        assert "node_id = 7" not in sql


class TestWeekYearUsesMod:
    """Temporal5 [1]; broken by 074270f.

    The ISO weekYear expression used `%`, which IRIS SQL rejects at Prepare
    (-1), so any RETURN with `d.weekYear` returned no rows.
    """

    @pytest.mark.parametrize(
        "create", ["date({year: 1984, month: 10, day: 11})", "localdatetime('1984-10-11T12:00')"]
    )
    def test_week_year_sql_has_no_percent_modulo(self, create):
        sql = tr(f"WITH {create} AS d RETURN d.weekYear").sql
        assert " % 7" not in sql
        assert "MOD(" in sql


class TestLabelsRejectsNonNodes:
    """Graph3 [9]; broken by feb1056.

    `list[1]` used to fail to parse, which satisfied the scenario's TypeError by
    accident. feb1056 parses it, and labels() of the integer returned [].
    """

    def test_labels_of_a_literal_list_element_is_a_type_error(self):
        with pytest.raises(TypeError, match="InvalidArgumentValue"):
            tr("MATCH (a) WITH [a, 1] AS list RETURN labels(list[1]) AS l")

    def test_labels_of_a_scalar_literal_is_a_type_error(self):
        with pytest.raises(TypeError, match="InvalidArgumentValue"):
            tr("RETURN labels(1) AS l")

    @pytest.mark.parametrize(
        "cypher",
        [
            "MATCH (a) WITH [a, 1] AS list RETURN labels(list[0]) AS l",
            "MATCH (a) RETURN labels(a) AS l",
            "RETURN labels(null) AS l",
        ],
    )
    def test_labels_of_a_node_or_null_translates(self, cypher):
        tr(cypher)
