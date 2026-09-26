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
