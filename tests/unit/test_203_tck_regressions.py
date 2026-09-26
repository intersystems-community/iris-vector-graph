"""Spec 203 — TCK scenarios that passed at 5fc72b9 (or v2.5.0) and regressed later.

Each class names the openCypher TCK scenario and the commit that broke it
(found by `git bisect`). The tests pin the translator behaviour so the
scenario cannot silently regress again.
"""

import re

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
        assert "CY_TEMPORAL_FIELD(" in sql


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


_LT = "localtime({hour: 12, minute: 31, second: 14, nanosecond: 645876123})"
_T = "time({hour: 12, minute: 31, second: 14, microsecond: 645876, timezone: '+01:00'})"
_D = "date({year: 1984, month: 10, day: 11})"
_LDT = (
    "localdatetime({year: 1984, week: 10, dayOfWeek: 3, hour: 12, minute: 31, "
    "second: 14, millisecond: 645})"
)
_DT = "datetime({year: 1984, month: 10, day: 11, hour: 12, timezone: '+01:00'})"


class TestTemporalProjectionFromVariables:
    """Temporal3 [3]-[11], never ported from worktree-agent-a96a31b.

    Main carried a96a31b's `_build_temporal_from_variable_map` but never called
    it; the older `_build_date_sql_from_dynamic_base` appended IANA zone names
    raw (`...Pacific/Honolulu`), dropped fractional seconds and the `Z` suffix.
    """

    @pytest.mark.parametrize(
        "with_, expr, expected",
        [
            (f"{_LT} AS other", "time(other)", "12:31:14.645876123Z"),
            (f"{_T} AS other", "time({time: other, timezone: '+05:00'})", "16:31:14.645876+05:00"),
            (
                f"{_LT} AS other",
                "localdatetime({year: 1984, month: 10, day: 11, time: other})",
                "1984-10-11T12:31:14.645876123",
            ),
            (
                f"{_D} AS otherDate, {_LDT} AS otherTime",
                "localdatetime({date: otherDate, time: otherTime})",
                "1984-10-11T12:31:14.645",
            ),
            (f"{_DT} AS other", "localdatetime({datetime: other})", "1984-10-11T12:00"),
            (
                f"{_D} AS other",
                "datetime({date: other, day: 28, hour: 10, minute: 10, second: 10, "
                "timezone: 'Pacific/Honolulu'})",
                "1984-10-28T10:10:10-10:00[Pacific/Honolulu]",
            ),
            (
                f"{_LT} AS other",
                "datetime({year: 1984, month: 10, day: 11, time: other})",
                "1984-10-11T12:31:14.645876123Z",
            ),
            (
                f"{_D} AS otherDate, {_LT} AS otherTime",
                "datetime({date: otherDate, time: otherTime, day: 28, second: 42, "
                "timezone: 'Pacific/Honolulu'})",
                "1984-10-28T12:31:42.645876123-10:00[Pacific/Honolulu]",
            ),
            (
                f"{_LDT} AS other",
                "datetime({datetime: other, day: 28, second: 42, timezone: 'Pacific/Honolulu'})",
                "1984-03-28T12:31:42.645-10:00[Pacific/Honolulu]",
            ),
        ],
    )
    def test_projection_result(self, iris_cursor, with_, expr, expected):
        t = tr(f"WITH {with_} RETURN {expr} AS result")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert iris_cursor.fetchall()[0][0] == expected


class TestDurationKeepsHoursPastADay:
    """Temporal7 [6]; the hours → days carry came in with 3b88ee5.

    A duration stores days and seconds separately, so `{days: 13, hours: 40}`
    is not `{days: 14, hours: 16}` and the two are not equal.
    """

    def test_hours_are_not_folded_into_days(self):
        sql = tr("RETURN duration({days: 13, hours: 40, minutes: 13, seconds: 10}) AS d").sql
        assert "'P13DT40H13M10S'" in sql

    def test_seconds_still_carry_into_minutes_and_hours(self):
        sql = tr("RETURN duration({days: 14, hours: 16, minutes: 12, seconds: 70}) AS d").sql
        assert "'P14DT16H13M10S'" in sql


class TestListMembershipIsOneReader:
    """Precedence1 [26], [28]; never ported from worktree-agent-a634adb.

    The 3VL CASE repeats its condition, so each nested `b IN c` over a JSON
    list was copied into the stage up to eight times, and IRIS failed at Prepare
    with -400 <LIST>LoadTableFunction. Membership is now one scalar subquery
    returning 1, 0 or NULL, which the CASE passes through.
    """

    QUERY = (
        "UNWIND [true, false, null] AS a UNWIND [true, false, null] AS b "
        "UNWIND [[], [true], [false], [null], [true, false], [true, false, null]] AS c "
        "WITH collect((a OP b IN c) = (a OP (b IN c))) AS eq, "
        "collect((a OP b IN c) <> ((a OP b) IN c)) AS neq "
        "RETURN all(x IN eq WHERE x) AND any(x IN neq WHERE x) AS result"
    )

    @pytest.mark.parametrize("op", ["=", "<>", "<", ">", "<=", ">=", "OR", "XOR", "AND"])
    def test_precedence_scenario_returns_true(self, iris_cursor, op):
        t = tr(self.QUERY.replace("OP", op))
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert iris_cursor.fetchall()[0][0] == 1

    def test_membership_is_not_repeated_by_the_3vl_case(self):
        sql = tr(
            "UNWIND [1, null] AS b UNWIND [[1], []] AS c WITH collect(b IN c) AS xs RETURN xs"
        ).sql
        assert sql.count("JSON_TABLE(u1.c") == 1

    @pytest.mark.parametrize(
        "b, c, expected",
        [
            ("null", "[]", 0),
            ("null", "[1]", None),
            ("1", "[2, null]", None),
            ("1", "[1, null]", 1),
            ("2", "[1]", 0),
        ],
    )
    def test_membership_three_valued_result(self, iris_cursor, b, c, expected):
        t = tr(f"WITH {b} AS b, {c} AS c RETURN b IN c AS r")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert iris_cursor.fetchall()[0][0] == expected


class TestListConcatFunctionsAreSchemaQualified:
    """Quantifier9-12 invariants; `list + x` at runtime.

    The runtime concat emitted a bare `JSON_VALUE`, which IRIS resolves
    against the default schema (Graph_KG) and rejects with -359, so every
    query that appends to a collected list returned no rows.
    """

    def test_runtime_concat_uses_the_sqluser_udf(self):
        sql = tr(
            "WITH [1, 2] AS l UNWIND l AS x WITH [y IN l | y] AS list, x "
            "RETURN list + x AS r"
        ).sql
        assert "SQLUser.LIST_CONCAT(" in sql
        assert " JSON_VALUE(" not in sql.replace("SQLUser.JSON_VALUE(", "")

    @pytest.mark.parametrize(
        "cypher, expected",
        [
            (
                "WITH [1, 2] AS l UNWIND l AS x WITH [y IN l | y] AS list, x RETURN list + x AS r",
                ["[1,2,1]", "[1,2,2]"],
            ),
            ("WITH [1, 'a'] AS a, [[2], {k: 1}] AS b RETURN a + b AS r", ['[1,"a",[2],{"k":1}]']),
            ("WITH [1] AS a UNWIND ['x', 'y'] AS s RETURN a + s AS r", ['[1,"x"]', '[1,"y"]']),
        ],
    )
    def test_runtime_concat_result(self, iris_cursor, cypher, expected):
        t = tr(cypher)
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert sorted(r[0].replace(" ", "") for r in iris_cursor.fetchall()) == expected


class TestQuantifierComparisonIsOneReaderEach:
    """Quantifier9 [3]-[5], Quantifier11 [3]-[6], Quantifier12 [3]-[5].

    `none(...) = (NOT any(...))` wrapped each quantifier in two nested 3VL
    CASEs, and a grouping WITH repeats the expression again, so the stage held
    twelve JSON_TABLE readers and IRIS failed at Prepare with -400
    <LIST>LoadTableFunction. A quantifier is already a 1/0/NULL scalar, so
    NOT and =/<> between such scalars are now arithmetic on it.
    """

    INV = "none(x IN list WHERE x = 2) = (NOT any(x IN list WHERE x = 2))"

    def test_each_quantifier_is_read_once(self):
        sql = tr(f"WITH [1, 2, 3] AS list RETURN {self.INV} AS result").sql
        assert sql.count("JSON_TABLE(") == 2

    @pytest.mark.parametrize(
        "cypher, expected",
        [
            (f"UNWIND [[1, 2, 3], [4], [null]] AS list WITH {INV} AS result, count(*) AS cnt RETURN result", [None, 1]),
            ("WITH [null] AS l RETURN NOT any(x IN l WHERE x = 2) AS r", [None]),
            ("WITH [2] AS l RETURN NOT any(x IN l WHERE x = 2) AS r", [0]),
            ("WITH [1] AS l RETURN any(x IN l WHERE x = 2) <> all(x IN l WHERE x = 1) AS r", [1]),
        ],
    )
    def test_quantifier_comparison_result(self, iris_cursor, cypher, expected):
        t = tr(cypher)
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        got = [r[0] for r in iris_cursor.fetchall()]
        assert sorted(got, key=lambda v: (v is not None, v)) == expected


class TestReverseReadsItsArgumentOnce:
    """Quantifier9-12 invariants; `reverse(list)` on a stage variable.

    The runtime string-or-list dispatch named the argument three times. IRIS
    inlines CTEs, so three chained `CASE ... reverse(list) ... + x` stages
    expanded to dozens of JSON_TABLE readers and failed at Prepare with
    -400 <LIST>LoadTableFunction. One UDF now handles both kinds.
    """

    def test_variable_argument_appears_once(self):
        sql = tr("WITH [1, 2] AS list RETURN reverse(list) AS r").sql
        assert sql.count("SQLUser.CY_REVERSE(") == 1
        assert "LIST_REVERSE" not in sql

    @pytest.mark.parametrize(
        "cypher, expected",
        [
            ("WITH [1, null, true, 'a', [2], {k: 1}] AS l RETURN reverse(l) AS r", '[{"k":1},[2],"a",true,null,1]'),
            ("WITH 'abc' AS s RETURN reverse(s) AS r", "cba"),
            ("RETURN reverse([1, false]) AS r", "[false,1]"),
        ],
    )
    def test_reverse_result(self, iris_cursor, cypher, expected):
        t = tr(cypher)
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert iris_cursor.fetchall()[0][0].replace(" ", "") == expected


class TestGroupedListPredicatesAreHoisted:
    """Quantifier9 [5], Quantifier10 [4], Quantifier11 [6], Quantifier12 [5].

    `none(...) = (size([x IN list WHERE ...]) = 0)` in a grouping WITH is
    repeated by the 3VL CASE and again by GROUP BY, which put twelve
    JSON_TABLE readers over an inlined chain of stages and failed at Prepare
    with -400 <LIST>LoadTableFunction. The hoist now reaches into the
    aggregating subquery and also computes repeated comprehensions once.
    """

    INV = "none(x IN list WHERE x = 2) = (size([x IN list WHERE x = 2 | x]) = 0)"

    def test_each_reader_is_computed_once(self):
        sql = tr(f"WITH [1, 2] AS list WITH {self.INV} AS result, count(*) AS cnt RETURN result").sql
        assert sql.count("JSON_TABLE(") == 2
        assert "__lp" in sql

    @pytest.mark.parametrize(
        "cypher, expected",
        [
            (f"UNWIND [[1, 2], [3], [null], []] AS list WITH {INV} AS result, count(*) AS cnt RETURN result", [None, 1]),
            ("UNWIND [[2], [3], [2, 2]] AS l WITH size([x IN l WHERE x = 2 | x]) AS s, count(*) AS c "
             "RETURN s, c", [(0, 1), (1, 1), (2, 1)]),
        ],
    )
    def test_grouped_result(self, iris_cursor, cypher, expected):
        t = tr(cypher)
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        rows = [r[0] if len(r) == 1 else tuple(r) for r in iris_cursor.fetchall()]
        assert sorted(rows, key=lambda v: (v is not None, v)) == expected


_DUR = "duration({years: 12, months: 5, days: 14, hours: 16, minutes: 12, seconds: 70, nanoseconds: 2})"
_DUR_FRAC = (
    "duration({years: 12.5, months: 5.5, days: 14.5, hours: 16.5, minutes: 12.5, "
    "seconds: 70.5, nanoseconds: 3})"
)


class TestTemporalArithmetic:
    """Temporal8 [1]-[7], WithOrderBy2 date-expression sorts.

    `x + d.dur` went to the list concat, `-`, `*` and `/` cast both operands
    to DOUBLE, so a temporal value plus or minus a duration, and a duration
    times a number, returned no rows. SQLUser.CY_TEMPORAL_ARITH does the
    calendar arithmetic when an operand is a duration string.
    """

    def test_fractional_years_carry_into_months(self):
        sql = tr(f"RETURN {_DUR_FRAC} AS d").sql
        assert "'P12Y11M29DT33H58M13.500000003S'" in sql

    def test_property_minus_property_checks_for_a_duration(self):
        sql = tr("MATCH (a:A), (b:B) RETURN a.d - b.d AS r").sql
        assert "SQLUser.CY_TEMPORAL_ARITH(" in sql

    @pytest.mark.parametrize(
        "x, expected_sum, expected_diff",
        [
            ("date({year: 1984, month: 10, day: 11})", "1997-03-25", "1972-04-27"),
            ("localtime({hour: 12, minute: 31, second: 14, nanosecond: 1})",
             "04:44:24.000000003", "20:18:03.999999999"),
            ("localdatetime({year: 1984, month: 10, day: 11, hour: 12, minute: 31, second: 14, nanosecond: 1})",
             "1997-03-26T04:44:24.000000003", "1972-04-26T20:18:03.999999999"),
        ],
    )
    def test_temporal_plus_minus_duration(self, iris_cursor, x, expected_sum, expected_diff):
        t = tr(f"WITH {x} AS x, {_DUR} AS d RETURN x + d AS s, x - d AS df")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert tuple(iris_cursor.fetchall()[0]) == (expected_sum, expected_diff)

    @pytest.mark.parametrize(
        "expr, expected",
        [
            ("d + d", "P24Y10M28DT32H26M20.000000004S"),
            ("d - d", "PT0S"),
            ("d * 0.5", "P6Y2M22DT13H21M8.000000001S"),
            ("d / 2", "P6Y2M22DT13H21M8.000000001S"),
        ],
    )
    def test_duration_arithmetic(self, iris_cursor, expr, expected):
        t = tr(f"WITH {_DUR} AS d RETURN {expr} AS r")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert iris_cursor.fetchall()[0][0] == expected

    @pytest.mark.parametrize(
        "expr, expected",
        [("a + b", 7), ("a - b", 3), ("a * b", 10), ("a / b", 2.5), ("s + t", "ab")],
    )
    def test_non_temporal_arithmetic_is_unchanged(self, iris_cursor, expr, expected):
        t = tr(f"WITH 5 AS a, 2 AS b, 'a' AS s, 'b' AS t RETURN {expr} AS r")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert iris_cursor.fetchall()[0][0] == expected


class TestAccessorsOnUntypedTemporalValues:
    """Temporal5 [3]-[7]: `MATCH (v) WITH v.date AS d RETURN d.hour`.

    A stage variable read from a property has no static temporal type, so
    `d.hour` became JSON_VALUE on a temporal string, which fails at runtime.
    The accessor now dispatches on the value's shape.
    """

    @pytest.mark.parametrize(
        "value, props, expected",
        [
            ("12:31:14.645876123", "d.hour, d.minute, d.second, d.millisecond, d.microsecond, d.nanosecond",
             (12, 31, 14, 645, 645876, 645876123)),
            ("1984-11-11T12:31:14.645876123+01:00", "d.year, d.month, d.day, d.hour, d.nanosecond",
             (1984, 11, 11, 12, 645876123)),
            ("1984-11-11", "d.year, d.month, d.day", (1984, 11, 11)),
            ("P1Y4M10DT1H1M1.001S", "d.years, d.months, d.days, d.hours, d.seconds",
             (1, 16, 10, 1, 3661)),
        ],
    )
    def test_accessor_on_a_stage_value(self, iris_cursor, value, props, expected):
        t = tr(f"WITH ['{value}'] AS l UNWIND l AS x WITH x AS d RETURN {props}")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert tuple(int(v) for v in iris_cursor.fetchall()[0]) == expected

    def test_map_value_still_uses_json_value(self, iris_cursor):
        t = tr("WITH [{hour: 3}] AS l UNWIND l AS x WITH x AS d RETURN d.hour")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert int(iris_cursor.fetchall()[0][0]) == 3


class TestCalendarAccessors:
    """Temporal5 [2], [5], [6]: ISO week, leap-year dayOfQuarter, zone and epoch.

    `{fn WEEK}` is not ISO, dayOfQuarter hardcoded non-leap offsets, and a
    `[Zone]` suffix broke timezone/offset parsing and the epoch ignored the
    offset. CY_TEMPORAL_FIELD computes all of them from the ISO string.
    """

    DT = "1984-11-11T12:31:14.645876123+01:00[Europe/Stockholm]"

    def _row(self, iris_cursor, q):
        t = tr(q)
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        return iris_cursor.fetchall()[0]

    def test_datetime_calendar_fields_with_zone_name(self, iris_cursor):
        row = self._row(
            iris_cursor,
            f"WITH ['{self.DT}'] AS l UNWIND l AS x WITH x AS d RETURN d.week, d.weekYear, "
            "d.ordinalDay, d.weekDay, d.dayOfQuarter, d.timezone, d.offset, d.offsetMinutes, "
            "d.offsetSeconds, d.epochSeconds, d.epochMillis",
        )
        assert tuple(row[:5]) == (45, 1984, 316, 7, 42)
        assert tuple(row[5:7]) == ("Europe/Stockholm", "+01:00")
        assert tuple(int(v) for v in row[7:]) == (60, 3600, 469020674, 469020674645)

    def test_typed_datetime_uses_offset_for_epoch(self, iris_cursor):
        row = self._row(
            iris_cursor,
            "WITH datetime('2015-07-21T21:40:32.142+0100') AS d RETURN d.epochSeconds",
        )
        assert int(row[0]) == 1437511232

    def test_last_iso_week_belongs_to_previous_year(self, iris_cursor):
        row = self._row(iris_cursor, "WITH date('1984-01-01') AS d RETURN d.weekYear, d.week")
        assert tuple(int(v) for v in row) == (1983, 52)

    def test_leap_year_day_of_quarter(self, iris_cursor):
        row = self._row(iris_cursor, "WITH date('1984-03-01') AS d RETURN d.dayOfQuarter")
        assert int(row[0]) == 61

    def test_negative_fractional_duration_floors_seconds(self, iris_cursor):
        """Temporal10 [1] @1.4: seconds is floored, nanosecondsOfSecond non-negative."""
        row = self._row(
            iris_cursor,
            "WITH duration.between(localdatetime('2018-01-02T10:00:00.1'), "
            "localdatetime('2018-01-01T10:00:00.2')) AS d RETURN d.seconds, d.nanosecondsOfSecond",
        )
        assert tuple(int(v) for v in row) == (-86400, 100000000)


class TestCurrentTemporalValues:
    """Temporal10 [12]: no-arg `date()` etc. read one statement clock, not NULL."""

    @pytest.mark.parametrize("fn", ["localtime", "time", "date", "localdatetime", "datetime"])
    def test_same_clock_gives_zero_duration(self, iris_cursor, fn):
        t = tr(f"RETURN duration.inSeconds({fn}(), {fn}()) AS duration")
        iris_cursor.execute(t.sql, t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters)
        assert iris_cursor.fetchall()[0][0] == "PT0S"

    def test_date_is_today(self, iris_cursor):
        import datetime as _dt

        t = tr("RETURN date() AS d")
        iris_cursor.execute(t.sql, t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters)
        assert iris_cursor.fetchall()[0][0] == _dt.datetime.now(_dt.timezone.utc).date().isoformat()


class TestDurationAcrossZoneAndLocal:
    """Temporal10 [8]: a local operand is read in the zoned operand's zone (DST day)."""

    Z0 = "datetime({year: 2017, month: 10, day: 29, hour: 0, timezone: 'Europe/Stockholm'})"
    Z4 = "datetime({year: 2017, month: 10, day: 29, hour: 4, timezone: 'Europe/Stockholm'})"

    @pytest.mark.parametrize(
        "lhs, rhs, expected",
        [
            (Z0, "localdatetime({year: 2017, month: 10, day: 29, hour: 4})", "PT5H"),
            (Z0, "localtime({hour: 4})", "PT5H"),
            ("localdatetime({year: 2017, month: 10, day: 29, hour: 0 })", Z4, "PT5H"),
            ("localtime({hour: 0 })", Z4, "PT5H"),
            ("date({year: 2017, month: 10, day: 29})", Z4, "PT5H"),
            (Z0, "date({year: 2017, month: 10, day: 30})", "PT25H"),
        ],
    )
    def test_in_seconds(self, iris_cursor, lhs, rhs, expected):
        t = tr(f"RETURN duration.inSeconds({lhs}, {rhs}) AS duration")
        iris_cursor.execute(t.sql, t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters)
        assert iris_cursor.fetchall()[0][0] == expected


class TestZonedTimeOrdering:
    """Temporal7 [3] @1.1: ordering of zoned times compares instants, not strings."""

    def test_offset_applied(self, iris_cursor):
        t = tr(
            "WITH time({hour: 10, minute: 0, timezone: '+01:00'}) AS x, "
            "time({hour: 9, minute: 35, second: 14, nanosecond: 645876123, timezone: '+00:00'}) AS d "
            "RETURN x > d, x < d, x >= d, x <= d"
        )
        iris_cursor.execute(t.sql, t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters)
        assert tuple(int(v) for v in iris_cursor.fetchall()[0]) == (0, 1, 0, 1)


def _rows(cur, t):
    cur.execute(t.sql, t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters)
    return [r[0] for r in cur.fetchall()]


class TestOrderBySortKey:
    """WithOrderBy1/ReturnOrderBy1: ORDER BY follows Cypher orderability via SQLUser.CY_SORT_KEY."""

    def test_with_order_by_emits_sort_key(self):
        assert "CY_SORT_KEY" in tr("UNWIND [2, 1] AS x WITH x ORDER BY x RETURN x").sql

    def test_return_order_by_emits_sort_key(self):
        assert "CY_SORT_KEY" in tr("UNWIND [2, 1] AS x RETURN x ORDER BY x").sql

    def test_key_orders_numbers(self, iris_cursor):
        vals = ["-100", "-2", "-1.5", "-1", "-.001", "0", ".001", "1", "1.5", "2", "10", "123456789012"]
        keys = []
        for v in vals:
            iris_cursor.execute("SELECT SQLUser.CY_SORT_KEY(?, 'x')", [v])
            keys.append(iris_cursor.fetchone()[0])
        assert keys == sorted(keys)

    def test_lists_sort_elementwise(self, iris_cursor):
        t = tr(
            "UNWIND [[], ['a'], ['a', 1], [1], [1, 'a'], [1, null], [null, 1], [null, 2]] AS lists "
            "WITH lists ORDER BY lists LIMIT 4 RETURN lists"
        )
        assert _rows(iris_cursor, t) == ["[]", '["a"]', '["a",1]', "[1]"]

    def test_zoned_times_sort_by_instant(self, iris_cursor):
        t = tr(
            "UNWIND [time({hour: 10, minute: 35, timezone: '-08:00'}), "
            "time({hour: 12, minute: 35, second: 15, timezone: '+05:00'})] AS t "
            "RETURN t ORDER BY t"
        )
        assert _rows(iris_cursor, t)[0].startswith("12:35:15")

    def test_nulls_last_ascending_first_descending(self, iris_cursor):
        asc = _rows(iris_cursor, tr("UNWIND [2, null, 1] AS x RETURN x ORDER BY x"))
        desc = _rows(iris_cursor, tr("UNWIND [2, null, 1] AS x RETURN x ORDER BY x DESC"))
        assert asc[-1] is None and desc[0] is None


class TestScalarVarComparison:
    """WithOrderBy1 [45]: `x < value` on untyped scalar variables agrees with ORDER BY (SQLUser.CY_CMP)."""

    def test_var_var_ordering_emits_cy_cmp(self):
        t = tr("WITH [1, 2] AS vs UNWIND vs AS v RETURN [x IN vs WHERE x < v] AS lt")
        assert "CY_CMP" in t.sql

    def test_property_comparison_does_not_use_cy_cmp(self):
        assert "CY_CMP" not in tr("MATCH (n) WHERE n.x < 3 RETURN n").sql

    def test_lists_compare_elementwise(self, iris_cursor):
        t = tr(
            "WITH [[2, 2], [1], [1, -20], []] AS values UNWIND values AS value "
            "RETURN size([x IN values WHERE x < value]) AS c ORDER BY value"
        )
        assert _rows(iris_cursor, t) == [0, 1, 2, 3]

    def test_zoned_times_compare_by_instant(self, iris_cursor):
        t = tr(
            "WITH [time({hour: 10, minute: 35, timezone: '-08:00'}), "
            "time({hour: 12, minute: 35, second: 15, timezone: '+05:00'})] AS values "
            "UNWIND values AS value RETURN size([x IN values WHERE x < value]) AS c ORDER BY value"
        )
        assert _rows(iris_cursor, t) == [0, 1]

    def test_cross_type_is_null(self, iris_cursor):
        iris_cursor.execute("SELECT SQLUser.CY_CMP('a', '1')")
        assert iris_cursor.fetchone()[0] is None


def _param_count_ok(t) -> bool:
    return t.sql.count("?") == len(t.parameters[0])


class TestOptionalMatchIsAllOrNothing:
    """Match7 [9], [11]; MatchWhere6 [7].

    A multi-hop OPTIONAL MATCH used to be a flat chain of LEFT OUTER JOINs, so a
    partial match (first hop found, second not) survived as an extra NULL row
    next to the full match. The hops of one OPTIONAL MATCH now form one nested
    join group, so they null out together, and the optional WHERE and the
    per-pattern edge-uniqueness guards join the group's ON instead of the
    statement's WHERE.
    """

    def test_two_hop_optional_is_one_nested_join_group(self):
        t = tr("MATCH (a:Single), (c:C) OPTIONAL MATCH (a)-->(b)-->(c) RETURN b")
        assert "LEFT OUTER JOIN (" in t.sql
        # inner hops are plain joins inside the group
        grp = t.sql[t.sql.index("LEFT OUTER JOIN (") :]
        assert grp.count("LEFT OUTER JOIN") == 1
        # the uniqueness guard moved into the group's ON, not the WHERE
        where = t.sql[t.sql.rindex("WHERE ") :] if "\nWHERE " in t.sql else ""
        assert "e6.s IS NULL" not in where
        assert _param_count_ok(t)

    def test_optional_where_goes_into_the_group(self):
        t = tr(
            "MATCH (a)-[r {name: 'r1'}]-(b) OPTIONAL MATCH (b)-[r2]-(c) "
            "WHERE r <> r2 RETURN a, b, c"
        )
        assert "LEFT OUTER JOIN (" in t.sql
        assert ".node_id IS NULL OR" not in t.sql
        assert _param_count_ok(t)

    def test_optional_where_on_property_of_far_node(self):
        t = tr(
            "MATCH (x:X) OPTIONAL MATCH (x)-[:E1]->(y:Y)-[:E2]->(z:Z) "
            "WHERE x.val < z.val RETURN x, y, z"
        )
        assert "LEFT OUTER JOIN (" in t.sql
        assert "n4.node_id IS NULL OR" not in t.sql
        assert _param_count_ok(t)

    def test_single_hop_optional_without_where_is_unchanged(self):
        t = tr("MATCH (a) OPTIONAL MATCH (a)-[:R]->(b) RETURN a, b")
        assert "LEFT OUTER JOIN (" not in t.sql


class TestUndirectedRelationshipInlineProperties:
    """Match7 [11]: `(a)-[r {name: 'r1'}]-(b)` ignored the property map, so both
    edges matched. The undirected CTE now filters on its qualifiers too."""

    def test_undirected_inline_property_filters_the_edge(self):
        t = tr("MATCH (a)-[r {name: 'r1'}]-(b) RETURN a")
        assert "JSON_VALUE(e" in t.sql and "qualifiers, '$.name')" in t.sql


def _where_exists(t) -> str:
    return t.sql[t.sql.index("EXISTS (") :]


class TestPatternPredicateShape:
    """Pattern1 [10], [13], [18]; MatchWhere4 [2].

    A pattern predicate (`WHERE (n)-[..]-(m)`) kept only the first relationship
    type, ignored `*N..M` bounds, dropped labels on bound nodes and left
    anonymous interior nodes unconnected.
    """

    def test_every_alternative_type_is_accepted(self):
        t = tr("MATCH (n), (m) WHERE (n)-[:REL1|REL2|REL3]-(m) RETURN n, m")
        assert "IN ('REL1', 'REL2', 'REL3')" in _where_exists(t)

    def test_fixed_length_var_length_is_unrolled_with_distinct_edges(self):
        t = tr("MATCH (n) WHERE (n)-[:REL1*2]-() RETURN n")
        ex = _where_exists(t)
        assert ex.count("rdf_edges ex") == 2
        assert ".edge_id <> " in ex
        assert _param_count_ok(t)

    def test_bounded_range_is_a_disjunction_of_lengths(self):
        t = tr("MATCH (n), (m) WHERE (n)-[:R*1..2]->(m) RETURN n")
        assert t.sql.count("EXISTS (SELECT 1 FROM") == 2

    def test_label_on_bound_node_is_checked(self):
        t = tr("MATCH (a), (b) WHERE (a)-[:T]->(b:MissingLabel) RETURN b")
        assert "rdf_labels" in _where_exists(t)
        assert "MissingLabel" in t.parameters[0]
        assert _param_count_ok(t)

    def test_anonymous_interior_node_links_both_hops(self):
        t = tr("MATCH (a), (b) WHERE (a)-[:X]->()-[:Y]->(b) RETURN a")
        ex = _where_exists(t)
        assert "ex2.o_id = n" in ex or "ex3.o_id = n" in ex
        assert ex.count(".s = n") >= 2


class TestOptionalUndirectedHopToBoundTarget:
    """Match8 [2]: `OPTIONAL MATCH (a)--(b)` with both ends bound put the
    target equality in the WHERE, dropping every unmatched (null) row."""

def _edge_insert(t):
    """The (sql, params) of the rdf_edges INSERT a translation emits."""
    for sql, params in zip(t.sql, t.parameters):
        if "INSERT INTO" in sql and "rdf_edges" in sql:
            return sql, params
    raise AssertionError("no rdf_edges INSERT")


class TestEdgeInsertAfterWithBindsStageParamsFirst:
    """Merge5 [16], [17]: MERGE/CREATE of a relationship between WITH-aliased nodes.

    The edge INSERT is `WITH Stage1 AS (…) INSERT … SELECT _ge.c1, …, ? FROM (…) AS _ge`.
    IRIS binds the CTE's markers first, then the outer select list, then the derived
    table. The parameters put the outer graph_id first, so the CTE's label filter
    got '' and the INSERT matched no rows.
    """

    @pytest.mark.parametrize("verb", ["MERGE", "CREATE"])
    def test_stage_params_precede_graph_id(self, verb):
        t = tr(f"MATCH (n:L) MATCH (m:L) WITH n AS a, m AS b {verb} (a)-[r:T]->(b) RETURN a")
        sql, params = _edge_insert(t)
        assert sql.startswith("WITH Stage1")
        assert params[:3] == ["L", "L", ""]
        assert params[3] == "T"

    def test_no_stage_keeps_outer_first(self):
        t = tr("MATCH (a:L), (b:L) MERGE (a)-[r:T]->(b) RETURN a")
        _sql, params = _edge_insert(t)
        assert params[:2] == ["", "T"]


class TestIdOfAStageNodeIsAProperty:
    """Merge5 [16], [17], [19]: `WITH n AS a … RETURN a.id` returned the node_id.

    Outside a stage `n.id` reads the `id` property; through a WITH it compiled to
    the stage column itself, so an integer `id` property came back as a UUID.
    """

    def test_stage_id_reads_the_property(self):
        sql = tr("MATCH (n:L) WITH n AS a RETURN a.id AS a").sql
        assert "SELECT a AS a" not in sql
        assert '"key" = ?' in sql


class TestMergeBindsANamedPath:
    """Merge1 [13], Merge5 [10]: `MERGE p = (…) RETURN p` was a parse error."""

    def test_parser_keeps_the_path_variable(self):
        q = parse_query("MERGE p = (a {num: 1}) RETURN p")
        merge = q.query_parts[0].clauses[0]
        assert merge.path_variable == "p"

    def test_single_node_path_projects(self):
        sql = tr("MERGE p = (a:L {num: 1}) RETURN p").sql
        assert "Undefined" not in str(sql)

    def test_relationship_path_joins_the_edge(self):
        t = tr("MERGE (a:L {num: 1}) MERGE (b:L {num: 2}) MERGE p = (a)-[:R]->(b) RETURN p")
        final = t.sql[-1] if isinstance(t.sql, list) else t.sql
        assert "rdf_edges" in final


class TestMergeActionReadsBoundRows:
    """Merge2 [5], Merge3 [4], Merge4 [2]: `ON CREATE/ON MATCH SET city.name =
    person.bornIn` bound the PropertyReference AST as a parameter, which the
    driver rejects ("Unsupported argument type")."""

    @pytest.mark.parametrize(
        "actions",
        [
            "ON CREATE SET city.name = person.bornIn",
            "ON MATCH SET city.name = person.bornIn",
            "ON MATCH SET city.name = person.bornIn ON CREATE SET city.name = person.bornIn",
        ],
    )
    def test_no_ast_is_bound(self, actions):
        t = tr(f"MATCH (person:Person) MERGE (city:City) {actions} RETURN person.bornIn")
        for params in t.parameters:
            assert all(p is None or isinstance(p, (str, int, float)) for p in params), params
        assert "SELECT TOP 1" in "\n".join(t.sql)
        for sql, params in zip(t.sql, t.parameters):
            assert sql.count("?") == len(params), sql


class TestMergeActionCopiesNodeIntoRelationship:
    """Merge6 [6], Merge7 [4]: `ON CREATE/ON MATCH SET r = a` (copy the node's
    properties onto the merged relationship) was silently dropped."""

    @pytest.mark.parametrize("action", ["ON CREATE", "ON MATCH"])
    def test_qualifiers_built_from_node_props(self, action):
        t = tr(
            "MATCH (a {name: 'A'}), (b {name: 'B'}) "
            f"MERGE (a)-[r:TYPE]->(b) {action} SET r = a"
        )
        sqls = t.sql if isinstance(t.sql, list) else [t.sql]
        upd = [s for s in sqls if "rdf_edges SET qualifiers" in s and "LIST(" in s]
        assert len(upd) == 1, sqls
        for sql, params in zip(t.sql, t.parameters):
            assert sql.count("?") == len(params), sql


class TestNodeInsertAfterWithBindsStageParamsFirst:
    """Create3 [6]-[8]: a node CREATEd after `WITH` bound its own id to the CTE's
    label marker, so the gate matched nothing and the node was never written; the
    (correctly ordered) edge insert then failed its foreign key."""

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (a), (b) WITH * OPTIONAL MATCH (a)--(b) RETURN count(*)",
            "MATCH (a), (b) OPTIONAL MATCH (a)--(b) RETURN count(*)",
        ],
    )
    def test_target_equality_is_in_the_on_clause(self, q):
        t = tr(q)
        assert "\nWHERE" not in t.sql.split("LEFT OUTER JOIN", 1)[1]
        assert "._dst" in t.sql.split("LEFT OUTER JOIN", 1)[1]


class TestPatternComprehensionKeepsNulls:
    """Pattern2 [4], [5]: `[(n)-->(b) | b.name]` must keep a null element for
    every match whose projection is null; JSON_ARRAYAGG drops NULLs."""

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n) RETURN [(n)-[:T]->(b) | b.name] AS list",
            "MATCH (n) RETURN [(n)-[r:T]->() | r.name] AS list",
        ],
    )
    def test_null_projection_is_kept(self, q):
        t = tr(q)
        assert "JSON_ARRAYAGG(COALESCE(" in t.sql
        assert "'null')" in t.sql


class TestUndirectedRelationshipEqualityIsPhysical:
    """Match7 [11]: `r <> r2` between two undirected hops compared the
    traversal-oriented _src/_dst, so one edge walked both ways looked like two
    different relationships."""

    def test_undirected_rels_compare_physical_endpoints(self):
        t = tr(
            "MATCH (a)-[r {name: 'r1'}]-(b) OPTIONAL MATCH (b)-[r2]-(c) "
            "WHERE r <> r2 RETURN a, b, c"
        )
        assert "e2._os <> e4._os" in t.sql and "e2._oo <> e4._oo" in t.sql


class TestPatternComprehensionDirectionAndScalarSource:
    """Pattern2 [7], [11]: pattern comprehensions ignored the arrow (every
    pattern was walked outgoing) and, inside a list comprehension, joined the
    loop variable as `lc.node_id` although the JSON_TABLE column is `lc.x`."""

    def test_incoming_walks_the_edge_backwards(self):
        t = tr("MATCH (n) RETURN [(n)<-[:T]-(m) | m.name] AS l")
        assert "epc1.o_id = n0.node_id" in t.sql
        assert "pct2.node_id = epc1.s" in t.sql

    def test_undirected_concatenates_both_directions(self):
        t = tr("MATCH (liker) RETURN [p = (liker)--() | p] AS isNew")
        assert "SQLUser.LIST_CONCAT(" in t.sql
        assert "epc1.s <> epc1.o_id" in t.sql

    def test_list_comprehension_variable_is_the_json_table_column(self):
        t = tr(
            "MATCH p = (n:X)-->() "
            "RETURN n, [x IN nodes(p) | size([(x)-->(:Y) | 1])] AS list"
        )
        assert ".node_id AND pcl" not in t.sql
        assert "= lc4.x" in t.sql


class TestOptionalGroupKeepsSubqueriesOutOfInnerOn:
    """Match7 [7]: a label EXISTS inside the nested group's inner ON made IRIS
    fail with <UNDEFINED>flattenExists^%qaqpre; it now sits in the outer ON."""

    def test_label_exists_goes_to_the_outer_on(self):
        t = tr(
            "MATCH (a:L {name: 'A'}) "
            "OPTIONAL MATCH (a)-[:KNOWS]->()-[:KNOWS]->(foo:L) RETURN foo"
        )
        grp = t.sql[t.sql.index("LEFT OUTER JOIN (") :]
        inner = grp[: grp.index(") ON ")]
        assert "EXISTS" not in inner and "SELECT" not in inner
        assert "EXISTS" in grp[grp.index(") ON ") :]
        assert _param_count_ok(t)

    def test_plain_unwind_vars_do_not_use_cy_cmp(self):
        # Precedence1: CY_CMP inside a `(a < b) IN c` membership subquery fails with -400
        t = tr("UNWIND [true, false] AS a UNWIND [true, false] AS b RETURN (a < b) IN [true] AS r")
        assert "CY_CMP" not in t.sql

class TestConstantListOperatorFolding:
    """Precedence3 [2] [4] [5] [6], Precedence4 [4].

    Operators over literal values fold at translate time with Cypher
    semantics. Evaluated in SQL they went wrong: JSON_TABLE '$[i]' returned the
    first scalar of a nested list, `IN` against a folded JSON string compared
    the strings, and a folded `IN` was then compared with a list.
    """

    @staticmethod
    def col(cypher):
        sql = tr(cypher).sql
        assert sql.startswith("SELECT ") and sql.endswith(" AS a"), sql
        return sql[len("SELECT ") : -len(" AS a")]

    @pytest.mark.parametrize(
        "expr, expected",
        [
            ("[[1], [2, 3], [4, 5]] + [5, [6, 7], [8, 9], 10][2]", "[[1], [2, 3], [4, 5], 8, 9]"),
            ("([[1], [2, 3], [4, 5]] + [5, [6, 7], [8, 9], 10])[2]", "[4, 5]"),
            ("[1]+(2 IN [3])+4", "[1, false, 4]"),
            ("(([1]+[2]) IN [3])+[4]", "[false, 4]"),
            ("[1, 2, 3][-1]", "3"),
        ],
    )
    def test_list_values(self, expr, expected):
        c = self.col(f"RETURN {expr} AS a")
        if expected.startswith("["):
            assert f"'{expected}'" in c
        else:
            assert c == expected

    @pytest.mark.parametrize(
        "expr, expected",
        [
            ("[1]+2 IN [3]+4", "0"),
            ("[1]+[2] IN [3]+[4]", "0"),
            ("[1, 2] = [3, 4] IN [[3, 4], false]", "0"),
            ("[1, 2] <> [3, 4] IN [[3, 4], false]", "1"),
            ("[1, 2] < [3, 4] IN [[3, 4], false]", "NULL"),
            ("[1, 2] >= ([3, 4] IN [[3, 4], false])", "NULL"),
            ("([1, 2] < [3, 4]) IN [[3, 4], false]", "0"),
            ("([1, 2] > [3, 4]) IN [[3, 4], false]", "1"),
            ("('abc' STARTS WITH null OR true) = (('abc' STARTS WITH null) OR true)", "1"),
            ("('abc' STARTS WITH null OR true) <> ('abc' STARTS WITH (null OR true))", "NULL"),
            ("(true OR null STARTS WITH 'abc') <> ((true OR null) STARTS WITH 'abc')", "NULL"),
            ("null IN [1, 2]", "NULL"),
            ("2 IN [1, null, 2]", "1"),
        ],
    )
    def test_boolean_values(self, expr, expected):
        assert self.col(f"RETURN {expr} AS a") == expected

    def test_non_boolean_logical_operand_still_errors(self):
        with pytest.raises(Exception):
            tr("RETURN 1 AND true AS a")


class TestQuantifierOverCollect:
    """List11 [3]: `all(ok IN collect(expr) WHERE ok)`.

    The quantifier put JSON_ARRAYAGG inside a correlated JSON_TABLE source,
    which IRIS rejects with SQLCODE -19. It is now a grouped SUM/COUNT.
    """

    def test_no_aggregate_inside_json_table(self):
        sql = tr(
            "UNWIND [1, 2] AS x RETURN all(ok IN collect(x > 0) WHERE ok) AS okay"
        ).sql
        assert "JSON_ARRAYAGG" not in sql
        assert "SUM(CASE WHEN" in sql and "COUNT(" in sql

    def test_grouping_key_beside_quantifier_is_grouped(self):
        sql = tr(
            "UNWIND [1, 2, 3] AS x RETURN x % 2 AS k, any(v IN collect(x) WHERE v > 1) AS a"
        ).sql
        assert "GROUP BY" in sql

    def test_distinct_collect_keeps_the_list_path(self):
        sql = tr("UNWIND [1, 1] AS x RETURN any(v IN collect(DISTINCT x) WHERE v > 0) AS a").sql
        assert "JSON_TABLE" in sql

class TestSmallestIntegerLiteral:
    """Literals2 [8], Literals3 [8], Literals4 [8].

    IRIS parses the SQL literal -9223372036854775808 as a rounded decimal
    (-9223372036854775810); it must be built from representable parts.
    """

    @pytest.mark.parametrize(
        "lit", ["-9223372036854775808", "-0x8000000000000000", "-0o1000000000000000000000"]
    )
    def test_min_int64_is_not_emitted_as_one_literal(self, lit):
        sql = tr(f"RETURN {lit} AS literal").sql
        assert "-9223372036854775808" not in sql
        assert "(-9223372036854775807 - 1)" in sql


class TestEmptyMapLiteralIsTyped:
    """Literals8 [1]: a bare '{}' is literal-substituted by the IRIS statement
    cache and inherits the type of an earlier `SELECT 0 AS literal`."""

    def test_empty_map_is_cast_to_varchar(self):
        sql = tr("RETURN {} AS literal").sql
        assert "CAST('{}' AS VARCHAR" in sql


class TestSimpleCaseCrossType:
    """Conditional2 [1] @1.10/@1.11: CASE '0' WHEN 0 / CASE true WHEN 1 must not
    match, since Cypher equality never holds across types."""

    def test_string_test_value_skips_numeric_whens(self):
        sql = tr(
            "RETURN CASE '0' WHEN 0 THEN 'zero' WHEN 1 THEN 'one' "
            "ELSE 'something else' END AS result"
        ).sql
        assert "'zero'" not in sql and "'one'" not in sql
        assert "'something else'" in sql

    def test_boolean_test_value_skips_numeric_whens(self):
        sql = tr(
            "RETURN CASE true WHEN 0 THEN 'zero' WHEN 1 THEN 'one' "
            "ELSE 'something else' END AS result"
        ).sql
        assert "'zero'" not in sql and "'one'" not in sql

    def test_same_type_whens_are_kept(self):
        sql = tr("RETURN CASE 1 WHEN 0 THEN 'zero' WHEN 1 THEN 'one' END AS result").sql
        assert "'zero'" in sql and "'one'" in sql

    def test_all_whens_dropped_without_else_is_null(self):
        sql = tr("RETURN CASE 'x' WHEN 1 THEN 'one' END AS result").sql
        assert "'one'" not in sql
        assert "NULL" in sql


class TestNumericExpressionVersusStringOrdering:
    """Comparison2 [5] @1.4: 0.0 / 0.0 > 'a' is null — ordering a number
    against a string is undefined even when the number is computed."""

    def test_arith_vs_string_is_null(self):
        sql = tr("RETURN 0.0 / 0.0 > 'a' AS gt, 0.0 / 0.0 <= 'a' AS ltE").sql
        assert sql.replace(" ", "").startswith("SELECTNULLASgt,NULLASltE")


class TestGherkinCellUnescape:
    """Literals6 [5]: Gherkin unescapes `\\\\` in table cells before the value
    is read as a Cypher literal; behave only unescapes `\\|`."""

    def test_double_backslash_in_cell(self):
        from tests.tck.steps.comparison import TCKValue

        cell = "'a\\\\\\\\bcn5t\\'\"\\\\\\\\//\\\\\\\\\"\\''"
        assert TCKValue.parse(cell).python == 'a\\bcn5t\'"\\//\\"\''

    def test_escaped_newline_cell(self):
        from tests.tck.steps.comparison import TCKValue

        assert TCKValue.parse("'\\nFoo\\n'").python == "\nFoo\n"


class TestDisconnectedOptionalLabeledNode:
    """Aggregation5 [2]: a second, disconnected OPTIONAL MATCH (n:Label) must
    yield one null row when no node carries the label. A CROSS JOIN over all
    nodes plus a WHERE label check drops every row instead."""

    def test_left_joins_a_labeled_subquery(self):
        sql = tr(
            "OPTIONAL MATCH (f:DoesExist) OPTIONAL MATCH (n:DoesNotExist) "
            "RETURN collect(DISTINCT n.num) AS a, collect(DISTINCT f.num) AS b"
        ).sql
        assert "CROSS JOIN" not in sql
        assert "LEFT OUTER JOIN (SELECT" in sql
        assert "ON 1=1" in sql


class TestNestedQuantifierSqlSize:
    """Quantifier6 [2]: single(x IN list WHERE single(y IN list WHERE ...))
    blew the IRIS <STRINGSTACK> at Prepare because each quantifier repeated its
    predicate (and so the nested subquery) five or six times."""

    @pytest.mark.parametrize("outer", ["single", "any", "all", "none"])
    @pytest.mark.parametrize("inner", ["single", "any", "all", "none"])
    def test_nested_predicate_is_not_repeated(self, outer, inner):
        sql = tr(
            "WITH [1, 2, 3, 4, 5, 6, 7, 8, 9] AS list "
            f"RETURN {outer}(x IN list WHERE {inner}(y IN list WHERE x % y = 1)) AS result"
        ).sql
        assert sql.count("JSON_TABLE") <= 3

    def test_flat_quantifier_keeps_sum_counts(self):
        # The SUM-count shape is what IRIS prepares reliably when an enclosing
        # 3VL CASE repeats the quantifier (Quantifier7 [3], Precedence1 [23]).
        sql = tr("WITH [1, 2] AS list RETURN single(x IN list WHERE x > 1) AS r").sql
        assert "SUM(CASE WHEN qc" in sql and "COUNT(*)" in sql


class TestStringPredicateOnUnwoundNonStrings:
    """String8 [8], String9 [8], String10 [8]: operands unwound from a literal
    list with no string element can never satisfy STARTS WITH / ENDS WITH /
    CONTAINS; the result is null, not a coerced LIKE."""

    @pytest.mark.parametrize("op", ["STARTS WITH", "ENDS WITH", "CONTAINS"])
    def test_non_string_operands_give_null(self, op):
        sql = tr(
            "WITH [1, 3.14, true, [], {}, null] AS operands "
            "UNWIND operands AS op1 UNWIND operands AS op2 "
            f"WITH op1 {op} op2 AS v RETURN v, count(*)"
        ).sql
        assert "LIKE" not in sql

    def test_string_elements_keep_like(self):
        sql = tr(
            "WITH ['a', 1] AS operands UNWIND operands AS op1 "
            "WITH op1 STARTS WITH 'a' AS v RETURN v"
        ).sql
        assert "LIKE" in sql

    def test_rebinding_clears_kinds(self):
        sql = tr(
            "UNWIND [1, 2] AS x WITH toString(x) AS x "
            "RETURN x STARTS WITH '1' AS v"
        ).sql
        assert "LIKE" in sql


class TestMinMaxOverMixedKinds:
    """Aggregation2 [9], [11], [12]: min()/max() over values unwound from a
    literal list holding lists or mixed kinds use Cypher orderability
    (list < string < boolean < number, lists element-wise), not VARCHAR order."""

    @pytest.mark.parametrize(
        "src",
        ["[[1], [2], [2, 1]]", "[1, 'a', null, [1, 2], 0.2, 'b']"],
    )
    def test_ordering_key_is_used(self, src):
        sql = tr(f"UNWIND {src} AS x RETURN max(x)").sql
        assert "CY_EXP_ORDKEY" in sql and "CY_EXP_ORDVAL" in sql

    def test_single_kind_scalars_keep_native_aggregate(self):
        sql = tr("UNWIND [1, 2, 3] AS x RETURN max(x)").sql
        assert "CY_EXP_ORDKEY" not in sql

    @pytest.mark.parametrize(
        "src, fn, expected",
        [
            ("[[1], [2], [2, 1]]", "max", "[2,1]"),
            ("[[1], [2], [2, 1]]", "min", "[1]"),
            ("[1, 'a', null, [1, 2], 0.2, 'b']", "max", "1"),
            ("[1, 'a', null, [1, 2], 0.2, 'b']", "min", "[1,2]"),
            ("[-1.5, 'x', -1.55, [3]]", "max", "-1.5"),
        ],
    )
    def test_cypher_orderability(self, iris_cursor, src, fn, expected):
        from iris_vector_graph.schema import GraphSchema

        for ddl in GraphSchema.get_procedures_sql_list():
            if "CY_EXP_ORD" in ddl:
                iris_cursor.execute(ddl.strip())
        t = tr(f"UNWIND {src} AS x RETURN {fn}(x) AS m")
        params = t.parameters[0] if t.parameters and isinstance(t.parameters[0], list) else t.parameters
        iris_cursor.execute(t.sql, params)
        assert str(iris_cursor.fetchall()[0][0]).replace(" ", "") == expected

class TestWithForwardsPathVariable:
    """With1 [4]: a named path projected through WITH stays a path value."""

    def test_single_node_path_through_with(self):
        sql = tr("MATCH p = (a:X) WITH p RETURN p").sql
        assert "Stage1" in sql
        assert '"nodes"' in sql
        assert "Stage1.p" in sql

    def test_path_with_relationship_through_with(self):
        sql = tr("MATCH p = (a:X)-[:T]->(b) WITH p AS q RETURN q").sql
        assert '"rels"' in sql
        assert "Stage1.q" in sql


class TestUnlabelledOptionalMatchNullRow:
    """Graph6 [7]: a lone unlabelled OPTIONAL MATCH yields one null row when empty."""

    def test_null_row_when_match_is_empty(self):
        sql = tr("OPTIONAL MATCH ()-[r]->() RETURN r.missing").sql
        assert "UNION ALL" in sql
        assert "NOT EXISTS (SELECT 1 FROM (" in sql

    def test_labelled_anchor_keeps_label_check(self):
        sql = tr("OPTIONAL MATCH (a:L)-[r]->() RETURN r.missing").sql
        assert "NOT EXISTS (SELECT 1 FROM (" not in sql

    def test_not_after_match(self):
        sql = tr("MATCH (a:L) OPTIONAL MATCH (a)-[r]->() RETURN r.missing").sql
        assert "__om" not in sql


class TestNamedPathAsValue:
    """Return4 [6]: a named path can be an aggregation argument."""

    def test_count_distinct_path(self):
        sql = tr("MATCH p = (n:X)-->(b) RETURN coUnt( dIstInct p )").sql
        assert "COUNT(DISTINCT" in sql.upper()
        assert '"nodes"' in sql


class TestCreateBoundNodeWithEmptyMap:
    """Create1 [19]: `(n {})` on a bound node is VariableAlreadyBound."""

    def test_empty_map_on_bound_node(self):
        with pytest.raises(SyntaxError, match="VariableAlreadyBound"):
            tr("CREATE (n:Foo)\nCREATE (n {})-[:OWNS]->(:Dog)")

    def test_bare_bound_node_allowed(self):
        tr("CREATE (n:Foo)\nCREATE (n)-[:OWNS]->(:Dog)")


class TestWithOrderByUnprojectedAggregate:
    """WithOrderBy4 [14]: sorting by an aggregate the WITH does not project reads
    variables that are out of scope."""

    def test_unprojected_aggregate_raises(self):
        with pytest.raises(SyntaxError, match="UndefinedVariable"):
            tr(
                "MATCH (a:A) WITH a.num2 % 3 AS mod, min(a.num + a.num2) AS min "
                "ORDER BY sum(a.num + a.num2) LIMIT 2 RETURN mod, min"
            )

    def test_projected_aggregate_allowed(self):
        tr(
            "MATCH (a:A) WITH a.num2 % 3 AS mod, sum(a.num + a.num2) AS sum "
            "ORDER BY sum(a.num + a.num2) LIMIT 2 RETURN mod, sum"
        )

    def test_projected_aggregate_with_param_allowed(self):
        tr(
            "MATCH (person) WITH avg(person.age) AS avgAge "
            "ORDER BY $age + avg(person.age) - 1000 RETURN avgAge",
            {"age": 38},
        )



class TestWithOrderByParameterBinding:
    """WithOrderBy4 [16]: a $param inside a WITH ORDER BY expression must be bound."""

    def test_param_in_sort_expression_bound(self):
        t = tr(
            "MATCH (person:X) WITH avg(person.age) AS avgAge "
            "ORDER BY $age + avg(person.age) - 1000 RETURN avgAge",
            {"age": 38},
        )
        params = t.parameters[0]
        assert 38 in params
        assert t.sql.count("?") == len(params)



class TestWithOrderByPriorStageVariable:
    """WithOrderBy4 [8]: ORDER BY a variable the earlier WITH projected but this
    one does not; the column is only visible inside the sort wrapper."""

    def test_prior_stage_column_projected_for_sort(self):
        t = tr(
            "MATCH (a:A) WITH a, a.num + a.num2 AS sum "
            "WITH a, a.num2 % 3 AS mod ORDER BY sum LIMIT 3 RETURN a, mod"
        )
        tail = t.sql.split("__ob ORDER BY", 1)[1].split("\n", 1)[0]
        assert "Stage1." not in tail
        assert t.sql.count("?") == len(t.parameters[0])



class TestPatternPredicateBoundNodeLabel:
    """WithWhere4 [2]: a label on an already-bound node inside a pattern
    predicate, `(a)-[:T]->(b:TheLabel)`, must constrain b."""

    def test_bound_node_label_checked(self):
        t = tr(
            "MATCH (a), (b) WITH a, b WHERE (a)-[:T]->(b:MissingLabel) RETURN b"
        )
        assert "MissingLabel" in t.parameters[0]
        assert t.sql.count("?") == len(t.parameters[0])

    def test_where_clause_bound_node_label_checked(self):
        t = tr("MATCH (a), (b) WHERE (a)-[:T]->(b:Foo) RETURN b")
        assert "Foo" in t.parameters[0]
        assert t.sql.count("?") == len(t.parameters[0])



class TestPropertyOfNodeFromListElement:
    """Graph6 [4]: `(list[1]).prop` where the element is a node id, not a map,
    reads the node property instead of parsing the id as JSON."""

    def test_list_element_property_reads_node_props(self):
        t = tr("MATCH (n) WITH [123, n] AS list RETURN (list[1]).existing")
        assert "rdf_props" in t.sql.split("FROM Stage1")[0].split("Stage1 AS")[-1] or (
            "rdf_props" in t.sql.rsplit("SELECT", 1)[-1]
        )
        assert t.sql.count("?") == len(t.parameters[0])

    def test_map_element_property_still_json(self):
        t = tr("WITH [{a: 1}] AS list RETURN (list[0]).a AS a")
        assert "JSON_VALUE" in t.sql



class TestDeletedEntityAccess:
    """Return2 [15]-[17]: reading properties or labels of an entity deleted in the
    same query part raises EntityNotFound (DeletedEntityAccess)."""

    def test_property_of_deleted_node(self):
        with pytest.raises(KeyError, match="DeletedEntityAccess"):
            tr("MATCH (n) DELETE n RETURN n.num")

    def test_labels_of_deleted_node(self):
        with pytest.raises(KeyError, match="DeletedEntityAccess"):
            tr("MATCH (n) DELETE n RETURN labels(n)")

    def test_property_of_deleted_relationship(self):
        with pytest.raises(KeyError, match="DeletedEntityAccess"):
            tr("MATCH ()-[r]->() DELETE r RETURN r.num")

    def test_type_of_deleted_relationship_allowed(self):
        tr("MATCH ()-[r]->() DELETE r RETURN type(r)")

    def test_deleted_node_itself_allowed(self):
        tr("MATCH (n) DELETE n RETURN n")

    def test_property_of_other_variable_allowed(self):
        tr("MATCH (n)-[r]->(m) DELETE r RETURN n.num")



class TestLabelsOfPathRejected:
    """Graph3 [8]: labels(p) on a named path is InvalidArgumentType (a path
    value is now translatable, so the check must be explicit)."""

    def test_labels_of_path_raises(self):
        with pytest.raises(SyntaxError, match="InvalidArgumentType"):
            tr("MATCH p = (a) RETURN labels(p) AS l")

    def test_labels_of_node_allowed(self):
        tr("MATCH p = (a) RETURN labels(a) AS l")

class TestNodeInsertAfterWithBindsStageParamsFirst:
    """Create3 [6]-[8]: a node CREATEd after `WITH` bound its own id to the CTE's
    label marker, so the gate matched nothing and the node was never written; the
    (correctly ordered) edge insert then failed its foreign key."""

    @pytest.mark.parametrize(
        "q",
        [
            "MATCH (n:L) WITH n AS a CREATE (a)-[:T]->(:M) RETURN a",
            "MATCH (n:L) WITH n AS a CREATE (a)-[:T]->({num: 1}) RETURN a",
            "MATCH (n:L) WITH n.num AS v CREATE (:M {num: v})",
        ],
    )
    def test_cte_marker_binds_first(self, q):
        t = tr(q)
        dml = [(s, p) for s, p in zip(t.sql, t.parameters) if s.startswith("WITH ")]
        dml = [(s, p) for s, p in dml if "INSERT" in s]
        assert dml
        for sql, params in dml:
            assert params[0] == "L", (sql, params)
            assert sql.count("?") == len(params), sql


class TestRowDrivenEdgeInsertSkipsAnExistingEdge:
    """Create3 [7]: rdf_edges holds one edge per (s, p, o_id, graph_id), so a
    second row-driven CREATE of the same edge failed the whole transaction (-119).
    The row-driven insert now skips an edge that already exists."""

    def test_guard_present_and_params_line_up(self):
        t = tr(
            "MATCH (n:L) MATCH (m:L) WITH n AS a, m AS b CREATE (a)-[:T]->(b) "
            "WITH a AS x, b AS y CREATE (x)-[:T]->(y) RETURN x, y"
        )
        ins = [(s, p) for s, p in zip(t.sql, t.parameters) if "INSERT INTO" in s and "rdf_edges" in s]
        assert len(ins) == 2
        for sql, params in ins:
            assert "NOT EXISTS" in sql and "_gx" in sql, sql
            assert sql.count("?") == len(params), sql
            assert params[0] == "L"

    @pytest.mark.parametrize("arrow", ["->", "-"])
    def test_merge_guard_conjoins_to_it(self, arrow):
        t = tr(f"MATCH (a:A), (b:B) MERGE (a)-[r:T]{arrow}(b) RETURN count(r)")
        ins = [(s, p) for s, p in zip(t.sql, t.parameters) if "INSERT INTO" in s and "rdf_edges" in s]
        assert len(ins) == 1
        sql, params = ins[0]
        assert sql.count(" AS _ge WHERE ") == 1, sql
        assert "AND NOT EXISTS (SELECT 1 FROM rdf_edges WHERE" in sql.replace("Graph_KG.", ""), sql
        assert sql.count("?") == len(params), sql

def _sql_text(t):
    return t.sql if isinstance(t.sql, str) else "\n".join(t.sql)


class TestVarLengthExpandsInSql:
    """Match5 [3], [8], [19]-[29]; Match6 [15]-[20]; Path2 [1], [2]; ReturnOrderBy2 [12].

    A var-length relationship was handed to the engine's Python BFS, which ignored
    `*0` bounds, dropped every other hop in the chain, and deduplicated targets
    instead of returning one row per path. It now expands in SQL through
    `SQLUser.CY_VLP_PATHS`, so the rest of the pattern joins as usual.
    """

    def test_zero_length_bound_is_expanded_in_sql(self):
        t = tr("MATCH (a:A) MATCH (a)-[:LIKES*0]->(c) RETURN c.name")
        sql = _sql_text(t)
        assert "SQLUser.CY_VLP_PATHS(" in sql
        assert "'out', 0, 0" in sql
        assert not t.var_length_paths

    def test_chain_keeps_the_fixed_hop(self):
        t = tr("MATCH (a:A) MATCH (a)-[:LIKES*1]->()-[:LIKES]->(c) RETURN c.name")
        sql = _sql_text(t)
        assert "SQLUser.CY_VLP_PATHS(" in sql
        assert "rdf_edges" in sql.split("CY_VLP_PATHS", 1)[1]
        # the fixed hop may not reuse an edge the var-length segment walked
        assert "SQLUser.CY_VLP_HAS(" in sql
        assert not t.var_length_paths

    def test_two_var_length_segments_are_edge_disjoint(self):
        t = tr("MATCH p = (a {name: 'A'})-[:KNOWS*0..1]->(b)-[:FRIEND*0..1]->(c) RETURN p")
        sql = _sql_text(t)
        assert sql.count("SQLUser.CY_VLP_PATHS(") == 2
        assert "SQLUser.CY_VLP_DISJOINT(" in sql

    def test_named_path_splices_the_segment_nodes(self):
        t = tr("MATCH p = (n {name: 'A'})-[:KNOWS*1..2]->(x) RETURN p")
        sql = _sql_text(t)
        assert "LIST_CONCAT(" in sql
        assert not t.var_length_paths

    def test_length_of_a_var_length_path_reads_the_segment_length(self):
        t = tr("MATCH p = (a)-[*]->(b) RETURN collect(nodes(p)) AS paths, length(p) AS l ORDER BY l")
        sql = _sql_text(t)
        assert "SQLUser.CY_VLP_PATHS(" in sql
        assert ".l" in sql
        assert not t.var_length_paths

    def test_relationships_of_a_var_length_path_are_objects(self):
        t = tr("MATCH p = (a:Start)-[:REL*2..2]->(b) RETURN relationships(p)")
        sql = _sql_text(t)
        assert "SQLUser.CY_VLP_PATHS(" in sql
        assert ".r" in sql

    def test_shortest_path_still_goes_to_the_engine(self):
        t = tr("MATCH p = shortestPath((a {name: 'A'})-[*]->(b {name: 'B'})) RETURN p")
        assert t.var_length_paths

    def test_optional_var_length_still_goes_to_the_engine(self):
        t = tr("MATCH (a:Single) OPTIONAL MATCH (a)-[*]->(b) RETURN b")
        assert t.var_length_paths

    def test_var_length_with_relationship_properties_still_goes_to_the_engine(self):
        t = tr("MATCH (a:Artist)-[:WORKED_WITH* {year: 1988}]->(b:Artist) RETURN *")
        assert t.var_length_paths

    def test_node_id_bound_endpoint_keeps_the_engine_bfs_fast_path(self):
        # `{node_id: ...}` pins the endpoint as surely as `{id: ...}`; the engine's
        # id-bound BFS (and approx_count_distinct, spec 230) read var_length_paths.
        for q in (
            "MATCH (a {node_id: 'star:c'})-[:SPOKE*1..2]-(b) RETURN b",
            "MATCH (a {node_id: $src})-[:SPOKE*1..2]-(b) RETURN b",
        ):
            t = translate_to_sql(parse_query(q), {"src": "star:c"})
            assert t.var_length_paths, q

    def test_where_bound_endpoint_id_keeps_the_engine_route(self):
        # `WHERE a.node_id = $src` pins the source just like `{node_id: $src}`.
        for q in (
            "USE GRAPH 'g1' MATCH (a)-[:SMOKE*1..1]->(b) WHERE a.node_id = $src RETURN b.node_id",
            "MATCH (a)-[:SMOKE*1..2]->(b) WHERE b.id = $src RETURN a",
        ):
            t = translate_to_sql(parse_query(q), {"src": "x"})
            assert t.var_length_paths, q

    def test_approx_count_distinct_keeps_the_engine_route(self):
        # The engine answers approx_count_distinct from var_length_paths (spec 230).
        t = translate_to_sql(
            parse_query("MATCH (a)-[:SPOKE*1..2]-(b) RETURN approx_count_distinct(b) AS c"), {}
        )
        assert t.var_length_paths

    def test_where_on_other_property_still_expands_in_sql(self):
        t = translate_to_sql(
            parse_query("MATCH (a)-[:SMOKE*1..2]->(b) WHERE a.name = 'x' RETURN b"), {}
        )
        assert not t.var_length_paths

    def test_path_udf_keeps_its_adjacency_cache_private(self):
        # A LANGUAGE OBJECTSCRIPT function is not a procedure block, so an
        # un-NEWed local outlives the call: a second segment in the same process
        # reused the first segment's adjacency cache and ignored its own types.
        from iris_vector_graph.schema import GraphSchema

        ddl = next(
            s for s in GraphSchema.get_procedures_sql_list()
            if "FUNCTION SQLUser.CY_VLP_PATHS(" in s
        )
        body = ddl.split("LANGUAGE OBJECTSCRIPT {", 1)[1].lstrip()
        assert body.startswith("New ")
        newed = {v.strip() for v in body[4:].split(" Set ", 1)[0].split(",")}
        assert {"adj", "st", "sd", "sl", "se", "args", "sql", "targ"} <= newed


class TestMergedNodeFeedsLaterMerge:
    """Merge5 [19]: a node found or made by `MERGE (c)` is bound by pattern.

    A later `MERGE (a)-[:T]->(c)` has to use the matched row, not the id the
    first MERGE would have given c had it created it.
    """

    def test_edge_merge_targets_the_matched_node(self):
        t = tr(
            "MATCH (n) WITH n AS a MERGE (c) MERGE (a)-[:T]->(c) RETURN a.id AS x"
        )
        edge_sql = next(s for s in t.sql if "INSERT INTO" in s and "rdf_edges" in s)
        node_params = next(
            p for s, p in zip(t.sql, t.parameters) if re.search(r"INSERT INTO (\w+\.)?nodes\b", s)
        )
        new_id = node_params[0]
        edge_params = t.parameters[t.sql.index(edge_sql)]
        assert new_id not in edge_params
        assert "? c3" not in edge_sql


class TestIsolationLabelFollowsWithScope:
    """Merge5 [18]/[19]: the TCK harness labels a MERGE node with the scenario label
    so it only sees this scenario's nodes. A variable projected away by `WITH` and
    then reused names a new node, so it needs the label again; without it `MERGE (c)`
    matched every node left in the database by earlier scenarios.
    """

    @staticmethod
    def inject(q):
        from tests.tck.steps.graph_setup import _inject_label

        return _inject_label(q, "TCK_X", inject_anonymous=False).split("\n")

    def test_merge_var_reused_after_with_is_labelled_again(self):
        lines = self.inject(
            "MATCH (n)\nWITH n AS a\nMERGE (c)\nMERGE (a)-[:T]->(c)\n"
            "WITH a AS x\nMERGE (c)\nMERGE (x)-[:T]->(c)\nRETURN x.id AS x"
        )
        assert lines[2] == "MERGE (c:TCK_X)"
        assert lines[3] == "MERGE (a)-[:T]->(c)"
        assert lines[5] == "MERGE (c:TCK_X)"
        assert lines[6] == "MERGE (x)-[:T]->(c)"

    def test_with_alias_source_is_out_of_scope_afterwards(self):
        lines = self.inject(
            "MATCH (n)\nMATCH (m)\nWITH n AS a, m AS b\nMERGE (a)-[:T]->(b)\n"
            "WITH a AS x, b AS y\nMERGE (a)\nMERGE (b)\nMERGE (a)-[:T]->(b)\n"
            "RETURN x.id AS x, y.id AS y"
        )
        assert lines[3] == "MERGE (a)-[:T]->(b)"
        assert lines[5] == "MERGE (a:TCK_X)"
        assert lines[6] == "MERGE (b:TCK_X)"
        assert lines[7] == "MERGE (a)-[:T]->(b)"

    def test_passthrough_and_star_keep_variables_bound(self):
        lines = self.inject("MATCH (a)\nWITH a\nMERGE (a)-[:T]->(b)\nWITH *\nMERGE (b)")
        assert lines[2] == "MERGE (a)-[:T]->(b:TCK_X)"
        assert lines[4] == "MERGE (b)"


class TestSetAfterStageBindsCteParamsFirst:
    """List12 [1]/[2]: a SET after `WITH` prefixes its DML with the stage CTE, whose
    markers come first in the SQL. The SET value was bound ahead of them, so the
    stage's label filter got 'newName' and the SET touched nothing."""

    Q = (
        "MATCH (a:Label1) WITH collect(a) AS nodes "
        "WITH nodes, [x IN nodes | x.name] AS oldNames "
        "UNWIND nodes AS n SET n.name = 'newName' RETURN n.name, oldNames"
    )

    def test_update_and_insert_bind_the_stage_label_first(self):
        t = tr(self.Q)
        dml = [(s, p) for s, p in zip(t.sql, t.parameters) if "UPDATE" in s or "INSERT" in s]
        assert len(dml) == 2
        for s, p in dml:
            assert p[0] == "Label1", (s, p)
            assert s.count("?") == len(p)
        upd = next(p for s, p in dml if "UPDATE" in s)
        assert upd == ["Label1", "name", "newName", "name"]
        ins = next(p for s, p in dml if "INSERT" in s)
        assert ins == ["Label1", "name", "name", "newName", "name"]

    def test_set_label_after_stage_binds_stage_first(self):
        t = tr("MATCH (a:Label1) WITH collect(a) AS ns UNWIND ns AS n SET n:Foo RETURN n")
        s, p = next((s, p) for s, p in zip(t.sql, t.parameters) if "INSERT" in s)
        assert p[0] == "Label1"
        assert p[1:] == ["Foo", "Foo"]
