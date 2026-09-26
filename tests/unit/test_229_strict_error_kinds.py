"""Spec 229 US3 — "Fail when..." TCK scenarios under the strict error step.

A step such as ``Then a TypeError should be raised at runtime: InvalidArgumentValue``
now has to match the error kind, the phase and the detail. Each test translates the
scenario's query (with its parameters) and checks what the translator raised with the
harness's own classifier, ``tests.tck.steps.errors``. A scenario that expects a
runtime error accepts one raised at compile time (the translator binds parameters and
folds constants), so a static check in the translator satisfies it.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.tck.steps.errors import COMPILE, RUNTIME, ANY_TIME, classify, mismatch


def tr(cypher, params=None, procedures=None):
    return translate_to_sql(parse_query(cypher), params or {}, procedures=procedures)


def raised(cypher, params=None, procedures=None):
    try:
        tr(cypher, params, procedures)
    except Exception as exc:  # noqa: BLE001 - classified below
        return classify(exc)
    pytest.fail(f"translated without an error: {cypher}")


def assert_error(cypher, kind, phase, detail, params=None, procedures=None):
    obs = raised(cypher, params, procedures)
    why = mismatch(kind, phase, detail, obs)
    assert why is None, why


# ---------------------------------------------------------------------------
# Compile-time SyntaxErrors
# ---------------------------------------------------------------------------


class TestCall1InvalidAggregation:
    """Call1 [16]: an aggregate in an in-query procedure call's arguments."""

    PROCS = {
        "test.labels": {
            "args": [{"name": "in", "type": "INTEGER?"}],
            "outputs": [{"name": "label", "type": "STRING?"}],
            "rows": [],
        }
    }

    def test_count_argument(self):
        assert_error(
            "MATCH (n) CALL test.labels(count(n)) YIELD label RETURN label",
            "SyntaxError", COMPILE, "InvalidAggregation", procedures=self.PROCS,
        )


class TestWith4ColumnNameConflict:
    """With4 [4]: WITH 1 AS a, 2 AS a."""

    def test_duplicate_alias(self):
        assert_error("WITH 1 AS a, 2 AS a RETURN a", "SyntaxError", COMPILE, "ColumnNameConflict")

    def test_distinct_aliases_translate(self):
        tr("WITH 1 AS a, 2 AS b RETURN a, b")


class TestReturn2UnknownFunction:
    """Return2 [18]: RETURN foo(a)."""

    def test_unknown_function(self):
        assert_error("MATCH (a) RETURN foo(a)", "SyntaxError", COMPILE, "UnknownFunction")

    @pytest.mark.parametrize(
        "q",
        [
            "RETURN toUpper('a') AS x",
            "RETURN abs(-1) AS x",
            "RETURN sqrt(4.0) AS x",
            "RETURN exp(1.0) AS x",
            "RETURN log(2.0) AS x",
            "RETURN sin(1.0) AS x",
            "RETURN coalesce(null, 1) AS x",
            "RETURN timestamp() AS x",
            "RETURN rand() AS x",
            "RETURN randomUUID() AS x",
        ],
    )
    def test_known_functions_still_translate(self, q):
        tr(q)


class TestList5InOnAMap:
    """List5 [42]: `1 IN <non-list literal>`, including a map literal."""

    @pytest.mark.parametrize("rhs", ["true", "123", "123.4", "'foo'", "{x: []}"])
    def test_non_list_literal(self, rhs):
        assert_error(f"RETURN 1 IN {rhs}", "SyntaxError", COMPILE, "InvalidArgumentType")

    def test_list_translates(self):
        tr("RETURN 1 IN [1, 2] AS x")


class TestMatchWhere1:
    """MatchWhere1 [14] (property of a path) and [15] (aggregate in WHERE)."""

    def test_property_of_a_path(self):
        assert_error(
            "MATCH (n) MATCH r = (n)-[*]->() WHERE r.name = 'apa' RETURN r",
            "SyntaxError", COMPILE, "InvalidArgumentType",
        )

    def test_aggregation_in_where(self):
        assert_error(
            "MATCH (a) WHERE count(a) > 10 RETURN a", "SyntaxError", COMPILE, "InvalidAggregation"
        )

    def test_aggregate_alias_in_with_where_translates(self):
        tr("MATCH (a) WITH a, count(*) AS c WHERE c > 10 RETURN a")


class TestGraph4TypeOnANode:
    """Graph4 [7]: type() of a node variable."""

    def test_type_of_node(self):
        assert_error("MATCH (r) RETURN type(r)", "SyntaxError", COMPILE, "InvalidArgumentType")

    def test_type_of_relationship_translates(self):
        tr("MATCH ()-[r]->() RETURN type(r)")


class TestReturnOrderBy6UndefinedVariable:
    """ReturnOrderBy6 [4]: ORDER BY uses a variable the aggregating RETURN dropped."""

    def test_not_returned_variable(self):
        assert_error(
            "MATCH (me: Person)--(you: Person) RETURN count(you.age) AS agg "
            "ORDER BY me.age + count(you.age)",
            "SyntaxError", COMPILE, "UndefinedVariable",
        )

    def test_returned_property_translates(self):  # ReturnOrderBy6 [3]
        tr(
            "MATCH (me: Person)--(you: Person) RETURN me.age AS age, count(you.age) AS cnt "
            "ORDER BY me.age + count(you.age)"
        )

    def test_complex_expression_still_ambiguous(self):  # ReturnOrderBy6 [5]
        assert_error(
            "MATCH (me: Person)--(you: Person) RETURN me.age + you.age, count(*) AS cnt "
            "ORDER BY me.age + you.age + count(*)",
            "SyntaxError", COMPILE, "AmbiguousAggregationExpression",
        )


class TestVariableTypeConflict:
    """Match1 [10] #20 and Match2 [9] #22: the variable is first seen in a pattern
    whose last node is already bound."""

    def test_path_reused_as_a_node(self):
        assert_error(
            "MATCH (x), r = (s)-[p]->(t)<-[]-(b), (r)-[q]-(b) RETURN r",
            "SyntaxError", COMPILE, "VariableTypeConflict",
        )

    def test_node_reused_as_a_relationship(self):
        assert_error(
            "MATCH (s)-[]-(t), (r)-[]-(t) MATCH ()-[r]-() RETURN r",
            "SyntaxError", COMPILE, "VariableTypeConflict",
        )

    def test_node_reused_as_node_translates(self):
        tr("MATCH (s)-[]-(t), (r)-[]-(t) MATCH (r)-[]-() RETURN r")


# ---------------------------------------------------------------------------
# TypeErrors whose operand types are known at translate time
# ---------------------------------------------------------------------------


class TestGraph4TypeOnAMixedList:
    """Graph4 [6]: [x IN [r, <invalid>] | type(x)]."""

    @pytest.mark.parametrize("invalid", ["0", "1.0", "true", "''", "[]"])
    def test_non_relationship_element(self, invalid):
        assert_error(
            f"MATCH p = (n)-[r:T]->() RETURN [x IN [r, {invalid}] | type(x) ] AS list",
            "TypeError", RUNTIME, "InvalidArgumentValue",
        )

    def test_relationships_only_translate(self):
        tr("MATCH p = (n)-[r:T]->() RETURN [x IN [r, r] | type(x) ] AS list")


class TestList1IndexingANonList:
    """List1 [6] (literal) and [7] (parameter): list[idx] on a non-list."""

    @pytest.mark.parametrize("expr", ["true", "123", "4.7", "'1'"])
    def test_literal(self, expr):
        assert_error(
            f"WITH {expr} AS list, 0 AS idx RETURN list[idx]",
            "TypeError", ANY_TIME, "InvalidArgumentType",
        )

    @pytest.mark.parametrize("value", [True, 123, 4.7, "1"])
    def test_parameter(self, value):
        assert_error(
            "WITH $expr AS list, $idx AS idx RETURN list[idx]",
            "TypeError", ANY_TIME, "*", params={"expr": value, "idx": 0},
        )

    def test_list_parameter_translates(self):
        tr("WITH $expr AS list, $idx AS idx RETURN list[idx]", {"expr": [1, 2], "idx": 0})

    def test_list_literal_translates(self):
        tr("WITH [1, 2] AS list, 0 AS idx RETURN list[idx]")


class TestMap2DynamicAccess:
    """Map2 [7] (map indexed by a non-string) and [8] (non-map indexed)."""

    def test_map_indexed_by_float(self):
        assert_error(
            "WITH $expr AS expr, $idx AS idx RETURN expr[idx]",
            "TypeError", RUNTIME, "MapElementAccessByNonString",
            params={"expr": {"name": "Apa"}, "idx": 12.3},
        )

    def test_integer_indexed(self):
        assert_error(
            "WITH $expr AS expr, $idx AS idx RETURN expr[idx]",
            "TypeError", RUNTIME, "InvalidArgumentType",
            params={"expr": 100, "idx": 0},
        )

    def test_map_indexed_by_string_translates(self):
        tr(
            "WITH $expr AS expr, $idx AS idx RETURN expr[idx]",
            {"expr": {"name": "Apa"}, "idx": "name"},
        )


class TestSet1ListOfMaps:
    """Set1 [10]: SET a.maplist = [{num: 1}]."""

    def test_list_of_maps(self):
        assert_error(
            "CREATE (a) SET a.maplist = [{num: 1}]", "TypeError", RUNTIME, "InvalidPropertyType"
        )

    def test_map_value(self):
        assert_error("CREATE (a) SET a.m = {num: 1}", "TypeError", RUNTIME, "InvalidPropertyType")

    def test_list_of_scalars_translates(self):
        tr("CREATE (a) SET a.l = [1, 2]")


# ---------------------------------------------------------------------------
# percentileDisc as an aggregate (Aggregation6 [5])
# ---------------------------------------------------------------------------


class TestPercentileGroupsByTheOtherColumns:
    """Aggregation6 [5]: RETURN percentileDisc(0.90, deg), deg groups by deg, so the
    percentile argument is a per-group value the IVG.Percentile_PDISC UDF checks."""

    Q = (
        "MATCH (n:S) WITH n, size([(n)-->() | 1]) AS deg WHERE deg > 2 "
        "WITH deg LIMIT 100 RETURN percentileDisc(0.90, deg), deg"
    )

    def _sql(self, q):
        r = tr(q)
        return r.sql[-1] if isinstance(r.sql, list) else r.sql

    def test_keeps_the_stages_and_both_columns(self):
        sql = self._sql(self.Q)
        assert "Stage2 AS (" in sql
        assert "IVG.Percentile_PDISC(" in sql
        assert "GROUP BY Stage2.deg" in sql
        assert "Stage2.deg AS deg" in sql

    def test_ungrouped_percentile_is_one_aggregate(self):
        sql = self._sql("MATCH (n) RETURN percentileDisc(n.price, 0.5) AS p")
        assert "IVG.Percentile_PDISC(JSON_ARRAYAGG(" in sql
        assert "GROUP BY" not in sql

    def test_grouped_percentile(self):
        sql = self._sql("MATCH (n) RETURN n.name AS name, percentileCont(n.price, 0.5) AS p")
        assert "IVG.Percentile_PCONT(JSON_ARRAYAGG(" in sql
        assert "GROUP BY" in sql
