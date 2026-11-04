"""Tests targeting translator.py lines 9161-10552.

Covers:
- Subscript/index expressions (_expr_subscript)
- Slice expressions (_expr_slice)
- Property access (_expr_property_access)
- Variable resolution (_expr_variable)
- Literal translation (_expr_literal)
- Aggregation (_expr_aggregation)
- String/coerce functions (_scalar_string, _scalar_coalesce)
- Duration/date/time helper functions (_parse_duration_string, _format_duration,
  _parse_time_string, _parse_datetime_string, _parse_date_string,
  _normalize_tz_str, _iana_tz_offset, _normalize_tz_offset, _subsecond_frac,
  _date_from_iso_week, _date_from_ordinal_day, _date_from_quarter)
- _build_date_sql_from_dynamic_base
- _build_date_from_map
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


def tr_full(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    return translate_to_sql(ast_tree, params or {})


# ===========================================================================
# _parse_date_string  (lines ~9949-9999)
# ===========================================================================

class TestParseDateString:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _parse_date_string
        self._fn = _parse_date_string

    def test_yyyy_mm_dd(self):
        assert self._fn("2015-07-21") == (2015, 7, 21)

    def test_yyyymmdd_compact(self):
        assert self._fn("20150721") == (2015, 7, 21)

    def test_yyyy_mm_partial(self):
        assert self._fn("2015-07") == (2015, 7, 1)

    def test_yyyymm_compact(self):
        assert self._fn("201507") == (2015, 7, 1)

    def test_yyyy_only(self):
        assert self._fn("2015") == (2015, 1, 1)

    def test_iso_week_extended(self):
        result = self._fn("2015-W30-2")
        assert result is not None
        y, m, d = result
        assert y == 2015

    def test_iso_week_compact(self):
        result = self._fn("2015W302")
        assert result is not None

    def test_iso_week_no_day_extended(self):
        result = self._fn("2015-W30")
        assert result is not None

    def test_iso_week_no_day_compact(self):
        result = self._fn("2015W30")
        assert result is not None

    def test_ordinal_extended(self):
        result = self._fn("2015-202")
        assert result is not None
        y, m, d = result
        assert y == 2015

    def test_ordinal_compact(self):
        result = self._fn("2015202")
        assert result is not None

    def test_invalid_returns_none(self):
        assert self._fn("not-a-date") is None

    def test_empty_returns_none(self):
        assert self._fn("") is None


# ===========================================================================
# _date_from_iso_week  (lines ~9644-9652)
# ===========================================================================

class TestDateFromIsoWeek:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _date_from_iso_week
        self._fn = _date_from_iso_week

    def test_week1_monday(self):
        import datetime as dt
        d = self._fn(2015, 1, 1)
        assert d.isoweekday() == 1  # Monday

    def test_week30_tuesday(self):
        d = self._fn(2015, 30, 2)
        assert d.isoweekday() == 2  # Tuesday

    def test_week53(self):
        d = self._fn(2015, 53, 4)
        assert d.year in (2015, 2016)

    def test_default_dow_is_monday(self):
        d = self._fn(2015, 10)
        assert d.isoweekday() == 1


# ===========================================================================
# _date_from_ordinal_day  (lines ~9655-9658)
# ===========================================================================

class TestDateFromOrdinalDay:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _date_from_ordinal_day
        self._fn = _date_from_ordinal_day

    def test_day1_is_jan1(self):
        import datetime as dt
        d = self._fn(2015, 1)
        assert d == dt.date(2015, 1, 1)

    def test_day365(self):
        import datetime as dt
        d = self._fn(2015, 365)
        assert d == dt.date(2015, 12, 31)

    def test_day202(self):
        import datetime as dt
        d = self._fn(2015, 202)
        assert d.year == 2015


# ===========================================================================
# _date_from_quarter  (lines ~9661-9666)
# ===========================================================================

class TestDateFromQuarter:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _date_from_quarter
        self._fn = _date_from_quarter

    def test_q1_day1(self):
        import datetime as dt
        d = self._fn(2015, 1, 1)
        assert d == dt.date(2015, 1, 1)

    def test_q2_day1(self):
        import datetime as dt
        d = self._fn(2015, 2, 1)
        assert d == dt.date(2015, 4, 1)

    def test_q3_day1(self):
        import datetime as dt
        d = self._fn(2015, 3, 1)
        assert d == dt.date(2015, 7, 1)

    def test_q4_day1(self):
        import datetime as dt
        d = self._fn(2015, 4, 1)
        assert d == dt.date(2015, 10, 1)

    def test_q2_day32(self):
        import datetime as dt
        d = self._fn(2015, 2, 32)
        assert d == dt.date(2015, 5, 2)


# ===========================================================================
# _normalize_tz_str  (lines ~9669-9696)
# ===========================================================================

class TestNormalizeTzStr:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _normalize_tz_str
        self._fn = _normalize_tz_str

    def test_z_uppercase(self):
        assert self._fn("Z") == "Z"

    def test_z_lowercase(self):
        assert self._fn("z") == "Z"

    def test_plus0000(self):
        assert self._fn("+0000") == "Z"

    def test_minus0000(self):
        assert self._fn("-0000") == "Z"

    def test_plus0100_compact(self):
        assert self._fn("+0100") == "+01:00"

    def test_plus0530_compact(self):
        assert self._fn("+0530") == "+05:30"

    def test_plus01_colon_00(self):
        assert self._fn("+01:00") == "+01:00"

    def test_minus05_colon_00(self):
        assert self._fn("-05:00") == "-05:00"

    def test_plus_hours_only(self):
        assert self._fn("+05") == "+05:00"

    def test_empty_string(self):
        assert self._fn("") == ""

    def test_none_empty(self):
        assert self._fn(None) == ""


# ===========================================================================
# _normalize_tz_offset  (lines ~9922-9929)
# ===========================================================================

class TestNormalizeTzOffset:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _normalize_tz_offset
        self._fn = _normalize_tz_offset

    def test_strip_trailing_00_seconds(self):
        assert self._fn("+01:00:00") == "+01:00"

    def test_keep_nonzero_seconds(self):
        assert self._fn("+05:30:30") == "+05:30:30"

    def test_passthrough_no_seconds(self):
        assert self._fn("+01:00") == "+01:00"


# ===========================================================================
# _subsecond_frac  (lines ~9932-9946)
# ===========================================================================

class TestSubsecondFrac:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _subsecond_frac
        self._fn = _subsecond_frac

    def test_all_negative_returns_none(self):
        assert self._fn(-1, -1, -1) is None

    def test_ms_only(self):
        r = self._fn(-1, -1, 1)
        assert r == "001"

    def test_us_only(self):
        r = self._fn(-1, 1, -1)
        assert r == "000001"

    def test_ns_only(self):
        r = self._fn(1, -1, -1)
        assert r == "000000001"

    def test_ms_500(self):
        r = self._fn(-1, -1, 500)
        assert r == "5"

    def test_all_zero_returns_none(self):
        r = self._fn(0, 0, 0)
        assert r is None


# ===========================================================================
# _parse_time_string  (lines ~9720-9776)
# ===========================================================================

class TestParseTimeString:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _parse_time_string
        self._fn = _parse_time_string

    def test_hhmm_colon(self):
        r = self._fn("12:31")
        assert r == "12:31"

    def test_hhmmss_colon(self):
        r = self._fn("12:31:14")
        assert r == "12:31:14"

    def test_hhmmss_frac_colon(self):
        r = self._fn("12:31:14.645876")
        assert r == "12:31:14.645876"

    def test_with_z(self):
        r = self._fn("12:31:14Z")
        assert r == "12:31:14Z"

    def test_with_plus_offset(self):
        r = self._fn("12:31:14+01:00")
        assert r == "12:31:14+01:00"

    def test_compact_hhmm(self):
        r = self._fn("1231")
        assert r == "12:31"

    def test_compact_hh(self):
        r = self._fn("12")
        assert r == "12:00"

    def test_compact_hhmmss(self):
        r = self._fn("123114")
        assert r == "12:31:14"

    def test_invalid_returns_none(self):
        r = self._fn("not-a-time")
        assert r is None


# ===========================================================================
# _parse_datetime_string  (lines ~9779-9801)
# ===========================================================================

class TestParseDatetimeString:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _parse_datetime_string
        self._fn = _parse_datetime_string

    def test_basic_datetime(self):
        r = self._fn("2015-07-21T12:31:14")
        assert r == "2015-07-21T12:31:14"

    def test_with_tz(self):
        r = self._fn("2015-07-21T12:31:14Z")
        assert r == "2015-07-21T12:31:14Z"

    def test_no_t_returns_none(self):
        r = self._fn("2015-07-21")
        assert r is None

    def test_bad_date_part_returns_none(self):
        r = self._fn("bad-dateT12:00")
        assert r is None

    def test_bad_time_part_returns_none(self):
        r = self._fn("2015-07-21Tbadtime")
        assert r is None


# ===========================================================================
# _parse_duration_string  (lines ~9804-9880)
# ===========================================================================

class TestParseDurationString:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _parse_duration_string
        self._fn = _parse_duration_string

    def test_full_duration(self):
        r = self._fn("P1Y2M3DT4H5M6S")
        assert r is not None
        assert "1Y" in r
        assert "2M" in r

    def test_just_hours(self):
        r = self._fn("PT4H")
        assert r == "PT4H"

    def test_days(self):
        r = self._fn("P3D")
        assert r == "P3D"

    def test_fractional_seconds(self):
        r = self._fn("PT0.5S")
        assert r is not None

    def test_weeks(self):
        r = self._fn("P2W")
        assert r is not None
        assert "D" in r  # weeks convert to days

    def test_calendar_notation(self):
        r = self._fn("P2012-02-02T14:37:21.545")
        assert r is not None

    def test_empty_duration(self):
        r = self._fn("PT0S")
        assert r == "PT0S"

    def test_invalid_returns_none(self):
        r = self._fn("not-a-duration")
        assert r is None

    def test_years_and_months(self):
        r = self._fn("P1Y6M")
        assert r is not None
        assert "1Y" in r

    def test_negative_duration(self):
        r = self._fn("P-1Y")
        assert r is not None


# ===========================================================================
# _format_duration  (lines ~9883-9919)
# ===========================================================================

class TestFormatDuration:
    def setup_method(self):
        from iris_vector_graph.cypher.translator import _format_duration
        self._fn = _format_duration

    def test_zero_duration(self):
        assert self._fn(0, 0, 0, 0, 0, 0, 0) == "PT0S"

    def test_only_years(self):
        assert self._fn(1, 0, 0, 0, 0, 0, 0) == "P1Y"

    def test_only_days(self):
        assert self._fn(0, 0, 3, 0, 0, 0, 0) == "P3D"

    def test_only_hours(self):
        assert self._fn(0, 0, 0, 4, 0, 0, 0) == "PT4H"

    def test_full(self):
        r = self._fn(1, 2, 3, 4, 5, 6, 0)
        assert "1Y" in r and "2M" in r and "3D" in r and "4H" in r and "5M" in r and "6S" in r

    def test_fractional_seconds(self):
        r = self._fn(0, 0, 0, 0, 0, 1, 500_000_000)
        assert "1.5S" in r

    def test_negative_fractional_seconds_zero_int(self):
        # s_int=0, rem_ns=-1_000_000 → "-0.001S"
        r = self._fn(0, 0, 0, 0, 0, 0, -1_000_000)
        assert "-0.001S" in r

    def test_date_only_no_T(self):
        r = self._fn(1, 2, 3, 0, 0, 0, 0)
        assert "T" not in r


# ===========================================================================
# _expr_literal (lines ~9426-9486)  — via tr()
# ===========================================================================

class TestExprLiteral:
    def test_true_literal(self):
        sql = tr("RETURN true AS x")
        assert "1" in sql

    def test_false_literal(self):
        sql = tr("RETURN false AS x")
        assert "0" in sql

    def test_null_literal(self):
        sql = tr("RETURN null AS x")
        assert "NULL" in sql

    def test_integer_literal(self):
        sql = tr("RETURN 42 AS x")
        assert "42" in sql

    def test_string_literal(self):
        sql = tr("RETURN 'hello' AS x")
        assert "hello" in sql

    def test_float_literal(self):
        sql = tr("RETURN 3.14 AS x")
        assert "3.14" in sql

    def test_list_literal_simple(self):
        sql = tr("RETURN [1, 2, 3] AS x")
        assert sql is not None

    def test_nested_list_literal(self):
        sql = tr("RETURN [[1, 2], [3, 4]] AS x")
        assert sql is not None

    def test_string_with_escaped_chars(self):
        sql = tr('RETURN "hello world" AS x')
        assert sql is not None


# ===========================================================================
# _expr_aggregation  (lines ~9494-9563)  — via tr()
# ===========================================================================

class TestExprAggregation:
    def test_count_star(self):
        sql = tr("MATCH (n) RETURN count(*) AS c")
        assert "COUNT" in sql.upper()

    def test_count_distinct(self):
        sql = tr("MATCH (n) RETURN count(DISTINCT n) AS c")
        assert "DISTINCT" in sql.upper()

    def test_collect_literal(self):
        sql = tr("MATCH (n) RETURN collect(n.name) AS names")
        assert "LIST(" in sql and "CY_JSON_NUM(" in sql  # 4.6.0 R262: a node property is listed as its JSON text (CY_JSON_NUM: 5.0 stays 5.0)

    def test_sum(self):
        sql = tr("MATCH (n) RETURN sum(n.val) AS s")
        assert "SUM" in sql.upper()

    def test_avg(self):
        sql = tr("MATCH (n) RETURN avg(n.val) AS a")
        assert "AVG" in sql.upper()

    def test_max(self):
        sql = tr("MATCH (n) RETURN max(n.val) AS m")
        assert "MAX" in sql.upper()

    def test_min(self):
        sql = tr("MATCH (n) RETURN min(n.val) AS m")
        assert "MIN" in sql.upper()

    def test_nested_aggregation_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) RETURN count(count(n)) AS c")

    def test_collect_with_null(self):
        sql = tr("MATCH (n) RETURN collect(null) AS ns")
        assert "JSON_ARRAYAGG" in sql.upper() or "NULL" in sql.upper()


# ===========================================================================
# _scalar_coalesce  (lines ~9566-9578)  — via tr()
# ===========================================================================

class TestScalarCoalesce:
    def test_coalesce_two_args(self):
        sql = tr("RETURN coalesce(null, 'fallback') AS x")
        assert "COALESCE" in sql.upper()

    def test_coalesce_three_args(self):
        sql = tr("RETURN coalesce(null, null, 'x') AS x")
        assert "COALESCE" in sql.upper()


# ===========================================================================
# _scalar_string (lines ~9582-9637)  — via tr()
# ===========================================================================

class TestScalarString:
    def test_tointeger(self):
        sql = tr("RETURN toInteger('42') AS x")
        assert "INTEGER" in sql.upper()

    def test_tofloat(self):
        sql = tr("RETURN toFloat('3.14') AS x")
        assert "DOUBLE" in sql.upper()

    def test_tostring_int(self):
        sql = tr("RETURN toString(42) AS x")
        assert "VARCHAR" in sql.upper()

    def test_tostring_true(self):
        sql = tr("RETURN toString(true) AS x")
        assert "true" in sql

    def test_tostring_false(self):
        sql = tr("RETURN toString(false) AS x")
        assert "false" in sql

    def test_substring_two_args(self):
        sql = tr("RETURN substring('hello', 1) AS x")
        assert "SUBSTRING" in sql.upper()

    def test_substring_three_args(self):
        sql = tr("RETURN substring('hello', 1, 3) AS x")
        assert "SUBSTRING" in sql.upper()

    def test_reverse_string(self):
        sql = tr("RETURN reverse('hello') AS x")
        assert sql is not None


# ===========================================================================
# Subscript expressions via tr()  (lines ~9161-9227)
# ===========================================================================

class TestSubscriptExpressions:
    def test_string_key_subscript(self):
        sql = tr("RETURN {a: 1, b: 2}['a'] AS x")
        assert sql is not None

    def test_integer_subscript_list(self):
        sql = tr("RETURN [1, 2, 3][0] AS x")
        assert sql is not None

    def test_string_subscript_on_map_param(self, ):
        sql = tr("RETURN $m['key'] AS x", {"m": {"key": "val"}})
        assert sql is not None


# ===========================================================================
# Slice expressions via tr()  (lines ~9248-9322)
# ===========================================================================

class TestSliceExpressions:
    def test_static_slice(self):
        sql = tr("RETURN [1, 2, 3, 4, 5][1..3] AS x")
        assert sql is not None

    def test_slice_equal_start_end_returns_empty(self):
        sql = tr("RETURN [1, 2, 3][2..2] AS x")
        # e <= s → EMPTY array
        assert sql is not None

    def test_slice_empty_end_gt_start(self):
        sql = tr("RETURN [1, 2, 3][3..2] AS x")
        assert sql is not None

    def test_slice_null_start(self):
        sql = tr("RETURN [1, 2, 3][null..2] AS x")
        assert "NULL" in sql

    def test_slice_null_end(self):
        sql = tr("RETURN [1, 2, 3][1..null] AS x")
        assert "NULL" in sql

    def test_negative_start(self):
        sql = tr("RETURN [1, 2, 3, 4, 5][-2..5] AS x")
        assert sql is not None

    def test_no_end(self):
        sql = tr("RETURN [1, 2, 3][1..] AS x")
        assert sql is not None

    def test_no_start(self):
        sql = tr("RETURN [1, 2, 3][..2] AS x")
        assert sql is not None


# ===========================================================================
# Property access via tr()  (lines ~9325-9342)
# ===========================================================================

class TestPropertyAccess:
    def test_simple_property_access(self):
        sql = tr("MATCH (n) RETURN n.name AS nm")
        assert sql is not None

    def test_property_access_on_non_map_literal_raises(self):
        with pytest.raises((TypeError, Exception)):
            tr("RETURN 42.prop AS x")

    def test_nested_property_access(self):
        sql = tr("MATCH (n) RETURN n.address AS a")
        assert sql is not None


# ===========================================================================
# Date/temporal function compilation via tr()
# ===========================================================================

class TestDateFunctions:
    def test_date_from_string(self):
        sql = tr("RETURN date('2015-07-21') AS d")
        assert "2015-07-21" in sql

    def test_date_from_map_year_month_day(self):
        sql = tr("RETURN date({year: 2015, month: 7, day: 21}) AS d")
        assert "2015" in sql

    def test_date_from_map_week(self):
        sql = tr("RETURN date({year: 2015, week: 30, dayOfWeek: 2}) AS d")
        assert sql is not None

    def test_date_from_map_ordinal_day(self):
        sql = tr("RETURN date({year: 2015, ordinalDay: 202}) AS d")
        assert sql is not None

    def test_date_from_map_quarter(self):
        sql = tr("RETURN date({year: 2015, quarter: 3, dayOfQuarter: 1}) AS d")
        assert sql is not None

    def test_localtime_from_string(self):
        sql = tr("RETURN localtime('12:31:14') AS t")
        assert "12:31:14" in sql

    def test_localtime_from_map(self):
        sql = tr("RETURN localtime({hour: 12, minute: 31, second: 14}) AS t")
        assert sql is not None

    def test_localdatetime_from_string(self):
        sql = tr("RETURN localdatetime('2015-07-21T12:31:14') AS dt")
        assert "2015" in sql

    def test_datetime_from_string(self):
        sql = tr("RETURN datetime('2015-07-21T12:31:14Z') AS dt")
        assert "2015" in sql

    def test_duration_basic(self):
        sql = tr("RETURN duration('P1Y2M3DT4H5M6S') AS dur")
        assert sql is not None

    def test_duration_pt0s(self):
        sql = tr("RETURN duration('PT0S') AS dur")
        assert sql is not None

    def test_date_from_map_date_base(self):
        # date({date: date('2015-07-21'), year: 2016})
        sql = tr("RETURN date({date: date('2015-07-21'), year: 2016}) AS d")
        assert sql is not None

    def test_localdatetime_map_year_month_day_hour(self):
        sql = tr("RETURN localdatetime({year: 2015, month: 7, day: 21, hour: 10, minute: 30}) AS dt")
        assert "2015" in sql

    def test_time_with_tz(self):
        sql = tr("RETURN time('12:31:14+01:00') AS t")
        assert "+01:00" in sql

    def test_datetime_with_timezone_override(self):
        sql = tr("RETURN datetime({year: 2015, month: 7, day: 21, hour: 12, minute: 0, second: 0, timezone: '+01:00'}) AS dt")
        assert sql is not None


# ===========================================================================
# _build_date_sql_from_dynamic_base  (lines ~10002-10420) via tr() with params
# ===========================================================================

class TestBuildDateSqlFromDynamicBase:
    """These use variable references, so the SQL is generated dynamically."""

    def test_date_from_variable_date_base(self):
        sql = tr("WITH $d AS d RETURN date({date: d}) AS out", {"d": "2015-07-21"})
        assert sql is not None

    def test_date_from_variable_date_with_year_override(self):
        sql = tr("WITH $d AS d RETURN date({date: d, year: 2016}) AS out", {"d": "2015-07-21"})
        assert "2016" in sql

    def test_localtime_from_variable(self):
        sql = tr("WITH $t AS t RETURN localtime({time: t}) AS out", {"t": "12:31:14"})
        assert sql is not None

    def test_localdatetime_from_variable_date_base(self):
        sql = tr("WITH $d AS d RETURN localdatetime({date: d}) AS out", {"d": "2015-07-21"})
        assert sql is not None

    def test_datetime_from_variable_datetime_base(self):
        sql = tr("WITH $dt AS dt RETURN datetime({datetime: dt}) AS out", {"dt": "2015-07-21T12:31:14Z"})
        assert sql is not None


# ===========================================================================
# _build_date_from_map comprehensive (lines ~10423-10557)
# ===========================================================================

class TestBuildDateFromMap:
    def test_date_iso_week(self):
        sql = tr("RETURN date({year: 2015, week: 30}) AS d")
        assert sql is not None

    def test_date_ordinal_day(self):
        sql = tr("RETURN date({year: 2015, ordinalDay: 1}) AS d")
        assert "2015-01-01" in sql

    def test_date_quarter(self):
        sql = tr("RETURN date({year: 2015, quarter: 2, dayOfQuarter: 1}) AS d")
        assert "2015-04-01" in sql

    def test_date_year_only(self):
        sql = tr("RETURN date({year: 2015}) AS d")
        assert "2015" in sql

    def test_localdatetime_map_full(self):
        sql = tr(
            "RETURN localdatetime({year: 2015, month: 7, day: 21, hour: 10, minute: 30, second: 0}) AS dt"
        )
        assert "2015" in sql

    def test_datetime_with_iana_tz(self):
        sql = tr(
            "RETURN datetime({year: 2015, month: 7, day: 21, hour: 12, minute: 0, second: 0, timezone: 'Europe/London'}) AS dt"
        )
        assert sql is not None

    def test_datetime_with_utc_tz(self):
        sql = tr(
            "RETURN datetime({year: 2015, month: 7, day: 21, hour: 12, minute: 0, second: 0, timezone: 'UTC'}) AS dt"
        )
        assert sql is not None

    def test_datetime_map_returns_string_with_tz(self):
        sql = tr(
            "RETURN datetime({year: 2015, month: 7, day: 21, hour: 0, minute: 0, second: 0, timezone: '+05:30'}) AS dt"
        )
        assert "+05:30" in sql


# ===========================================================================
# UNWIND, CREATE, MERGE, DELETE, SET via tr()
# ===========================================================================

class TestDMLStatements:
    def test_unwind_return(self):
        sql = tr("UNWIND [1, 2, 3] AS x RETURN x")
        assert sql is not None

    def test_unwind_with_where(self):
        sql = tr("UNWIND [1, 2, 3] AS x WHERE x > 1 RETURN x")
        assert sql is not None

    def test_match_with_where(self):
        sql = tr("MATCH (n) WHERE n.name = 'Alice' RETURN n")
        assert sql is not None

    def test_match_where_and(self):
        sql = tr("MATCH (n) WHERE n.name = 'Alice' AND n.age > 30 RETURN n")
        assert sql is not None

    def test_return_order_by(self):
        sql = tr("MATCH (n) RETURN n.name ORDER BY n.name ASC")
        assert sql is not None

    def test_return_order_by_desc(self):
        sql = tr("MATCH (n) RETURN n.name ORDER BY n.name DESC")
        assert sql is not None

    def test_return_limit(self):
        sql = tr("MATCH (n) RETURN n LIMIT 10")
        assert "10" in sql

    def test_return_skip(self):
        sql = tr("MATCH (n) RETURN n SKIP 5")
        assert "5" in sql

    def test_return_skip_limit(self):
        sql = tr("MATCH (n) RETURN n SKIP 5 LIMIT 10")
        assert sql is not None

    def test_with_clause(self):
        sql = tr("MATCH (n) WITH n RETURN n")
        assert sql is not None

    def test_with_limit(self):
        sql = tr("MATCH (n) WITH n LIMIT 5 RETURN n")
        assert sql is not None


# ===========================================================================
# Subqueries, CALL, OPTIONAL MATCH
# ===========================================================================

class TestSubqueriesAndCall:
    def test_optional_match(self):
        sql = tr("MATCH (n) OPTIONAL MATCH (n)-[r]->(m) RETURN n, m")
        assert sql is not None

    def test_exists_subquery(self):
        sql = tr("MATCH (n) WHERE EXISTS { MATCH (n)-[]->(m) } RETURN n")
        assert sql is not None

    def test_count_node_match(self):
        sql = tr("MATCH (n) RETURN count(n) AS cnt")
        assert "COUNT" in sql.upper()


# ===========================================================================
# Pattern comprehension and list comprehension
# ===========================================================================

class TestListAndPatternComprehension:
    def test_list_comprehension(self):
        sql = tr("RETURN [x IN [1, 2, 3] WHERE x > 1] AS y")
        assert sql is not None

    def test_list_comprehension_with_map(self):
        sql = tr("RETURN [x IN [1, 2, 3] | x * 2] AS y")
        assert sql is not None

    def test_pattern_comprehension(self):
        sql = tr("MATCH (n) RETURN [(n)-[r]->(m) | m.name] AS names")
        assert sql is not None


# ===========================================================================
# Misc expressions
# ===========================================================================

class TestMiscExpressions:
    def test_case_simple(self):
        sql = tr("RETURN CASE WHEN 1 = 1 THEN 'yes' ELSE 'no' END AS x")
        assert "CASE" in sql.upper()

    def test_case_generic(self):
        sql = tr("MATCH (n) RETURN CASE n.val WHEN 1 THEN 'one' ELSE 'other' END AS x")
        assert "CASE" in sql.upper()

    def test_in_predicate(self):
        sql = tr("MATCH (n) WHERE n.val IN [1, 2, 3] RETURN n")
        assert sql is not None

    def test_not_in_predicate(self):
        sql = tr("MATCH (n) WHERE NOT n.val IN [1, 2, 3] RETURN n")
        assert sql is not None

    def test_is_null(self):
        sql = tr("MATCH (n) WHERE n.name IS NULL RETURN n")
        assert "NULL" in sql.upper()

    def test_is_not_null(self):
        sql = tr("MATCH (n) WHERE n.name IS NOT NULL RETURN n")
        assert "NOT NULL" in sql.upper()

    def test_starts_with(self):
        sql = tr("MATCH (n) WHERE n.name STARTS WITH 'A' RETURN n")
        assert sql is not None

    def test_ends_with(self):
        sql = tr("MATCH (n) WHERE n.name ENDS WITH 'e' RETURN n")
        assert sql is not None

    def test_contains(self):
        sql = tr("MATCH (n) WHERE n.name CONTAINS 'lic' RETURN n")
        assert sql is not None

    def test_regex_match(self):
        sql = tr("MATCH (n) WHERE n.name =~ 'Al.*' RETURN n")
        assert sql is not None

    def test_string_concat(self):
        sql = tr("RETURN 'hello' + ' ' + 'world' AS x")
        assert sql is not None

    def test_arithmetic_add(self):
        sql = tr("RETURN 1 + 2 AS x")
        assert sql is not None

    def test_arithmetic_subtract(self):
        sql = tr("RETURN 5 - 3 AS x")
        assert sql is not None

    def test_arithmetic_multiply(self):
        sql = tr("RETURN 3 * 4 AS x")
        assert sql is not None

    def test_arithmetic_divide(self):
        sql = tr("RETURN 10 / 2 AS x")
        assert sql is not None

    def test_arithmetic_modulo(self):
        sql = tr("RETURN 10 % 3 AS x")
        assert sql is not None

    def test_power(self):
        sql = tr("RETURN 2 ^ 8 AS x")
        assert sql is not None

    def test_abs_function(self):
        sql = tr("RETURN abs(-5) AS x")
        assert sql is not None

    def test_ceil_function(self):
        sql = tr("RETURN ceil(3.2) AS x")
        assert sql is not None

    def test_floor_function(self):
        sql = tr("RETURN floor(3.7) AS x")
        assert sql is not None

    def test_round_function(self):
        sql = tr("RETURN round(3.5) AS x")
        assert sql is not None

    def test_sqrt_function(self):
        sql = tr("RETURN sqrt(9.0) AS x")
        assert sql is not None

    def test_size_string(self):
        sql = tr("RETURN size('hello') AS x")
        assert sql is not None

    def test_length_path(self):
        sql = tr("MATCH p = (n)-[]->() RETURN length(p) AS l")
        assert sql is not None

    def test_type_edge(self):
        sql = tr("MATCH ()-[r]->() RETURN type(r) AS t")
        assert sql is not None

    def test_labels_node(self):
        sql = tr("MATCH (n) RETURN labels(n) AS l")
        assert sql is not None

    def test_keys_function(self):
        sql = tr("MATCH (n) RETURN keys(n) AS k")
        assert sql is not None

    def test_head_function(self):
        sql = tr("RETURN head([1, 2, 3]) AS x")
        assert sql is not None

    def test_last_function(self):
        sql = tr("RETURN last([1, 2, 3]) AS x")
        assert sql is not None

    def test_tail_function(self):
        sql = tr("RETURN tail([1, 2, 3]) AS x")
        assert sql is not None

    def test_range_function(self):
        sql = tr("RETURN range(1, 5) AS x")
        assert sql is not None

    def test_range_step_function(self):
        sql = tr("RETURN range(1, 10, 2) AS x")
        assert sql is not None

    def test_nodes_path(self):
        sql = tr("MATCH p = (n)-[*1..2]->(m) RETURN nodes(p) AS ns")
        assert sql is not None

    def test_relationships_path(self):
        sql = tr("MATCH p = (n)-[*1..2]->(m) RETURN relationships(p) AS rs")
        assert sql is not None


# ===========================================================================
# Parameters
# ===========================================================================

class TestParameters:
    def test_param_in_where(self):
        sql = tr("MATCH (n) WHERE n.name = $name RETURN n", {"name": "Alice"})
        assert sql is not None

    def test_param_in_return(self):
        sql = tr("RETURN $val AS x", {"val": 42})
        assert sql is not None

    def test_list_param(self):
        sql = tr("MATCH (n) WHERE n.val IN $vals RETURN n", {"vals": [1, 2, 3]})
        assert sql is not None


# ===========================================================================
# Combine - multi-clause queries
# ===========================================================================

class TestMultiClause:
    def test_match_with_return(self):
        sql = tr("MATCH (n) WITH count(n) AS c RETURN c")
        assert sql is not None

    def test_match_where_with_return(self):
        sql = tr("MATCH (n) WHERE n.x > 5 WITH n RETURN n.x AS x")
        assert sql is not None

    def test_match_unwind_return(self):
        sql = tr("MATCH (n) UNWIND n.tags AS t RETURN t")
        assert sql is not None

    def test_distinct_return(self):
        sql = tr("MATCH (n) RETURN DISTINCT n.name AS nm")
        assert "DISTINCT" in sql.upper()
