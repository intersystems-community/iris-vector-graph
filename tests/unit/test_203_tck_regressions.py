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
