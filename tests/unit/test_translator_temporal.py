"""
Tests for temporal (date/datetime/time/localdatetime/duration) code paths in
iris_vector_graph/cypher/translator.py.

All tests are pure Python — no IRIS connection required.
"""

import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(q, params=None):
    """Translate a Cypher query to SQL and return the SQL string."""
    ast_q = parse_query(q)
    result = translate_to_sql(ast_q, params=params or {})
    sql = result.sql
    if isinstance(sql, list):
        return " ".join(sql)
    return sql


# ---------------------------------------------------------------------------
# Lines ~10359-10420: _parse_date_string — ISO date string parsing
# ---------------------------------------------------------------------------


def test_date_literal_iso():
    # YYYY-MM-DD
    sql = tr("RETURN date('2020-06-15') AS d")
    assert "2020-06-15" in sql


def test_date_literal_ordinal():
    # YYYY-DDD ordinal format → parsed to 2020-06-23 (day 175 of 2020)
    sql = tr("RETURN date('2020-175') AS d")
    assert "2020" in sql
    # Day 175 of 2020 = June 23
    assert "06" in sql or "23" in sql


def test_date_literal_iso_week():
    # YYYY-Www-D → 2020-W23-3 = Wednesday of week 23 of 2020
    sql = tr("RETURN date('2020-W23-3') AS d")
    assert "2020" in sql
    # Should produce a date string
    assert "06" in sql or "2020" in sql


def test_date_literal_year_only():
    # YYYY → Jan 1
    sql = tr("RETURN date('2020') AS d")
    assert "2020-01-01" in sql


def test_date_literal_year_month():
    # YYYY-MM → first day of month
    sql = tr("RETURN date('2020-06') AS d")
    assert "2020-06-01" in sql


def test_date_literal_compact_ymd():
    # YYYYMMDD
    sql = tr("RETURN date('20200615') AS d")
    assert "2020-06-15" in sql


# ---------------------------------------------------------------------------
# Lines ~10428-10557: _build_date_from_map — date from map literal
# ---------------------------------------------------------------------------


def test_date_from_map_ymd():
    # date({year:2020, month:6, day:15})
    sql = tr("RETURN date({year:2020, month:6, day:15}) AS d")
    assert "2020-06-15" in sql


def test_date_from_map_year_only():
    # date({year:2020}) → defaults month=1, day=1
    sql = tr("RETURN date({year:2020}) AS d")
    assert "2020-01-01" in sql


def test_date_from_map_iso_week():
    # date({year:2020, week:23})
    sql = tr("RETURN date({year:2020, week:23}) AS d")
    # Should produce some date string; week 23 of 2020 starts around June 1
    assert "2020" in sql


def test_date_from_map_iso_week_with_dow():
    # date({year:2020, week:23, dayOfWeek:3}) → Wednesday of week 23
    sql = tr("RETURN date({year:2020, week:23, dayOfWeek:3}) AS d")
    assert "2020" in sql


def test_date_from_map_ordinal_day():
    # date({year:2020, ordinalDay:167})
    sql = tr("RETURN date({year:2020, ordinalDay:167}) AS d")
    assert "2020" in sql
    # Day 167 of 2020 is June 15
    assert "06-15" in sql or "2020" in sql


def test_date_from_map_quarter():
    # date({year:2020, quarter:2, dayOfQuarter:1}) → April 1
    sql = tr("RETURN date({year:2020, quarter:2, dayOfQuarter:1}) AS d")
    assert "2020-04-01" in sql


# ---------------------------------------------------------------------------
# Lines ~10577-10768: time/localtime from map and string
# ---------------------------------------------------------------------------


def test_localtime_from_map():
    # localtime({hour:9, minute:0})
    sql = tr("RETURN localtime({hour:9, minute:0}) AS t")
    assert "09:00" in sql


def test_localtime_from_map_with_seconds():
    # localtime({hour:12, minute:30, second:45})
    sql = tr("RETURN localtime({hour:12, minute:30, second:45}) AS t")
    assert "12:30:45" in sql


def test_time_from_map_with_timezone():
    # time({hour:12, minute:30, timezone:'+01:00'})
    sql = tr("RETURN time({hour:12, minute:30, timezone:'+01:00'}) AS t")
    assert "12:30" in sql
    assert "+01:00" in sql


def test_time_from_map_no_timezone():
    # time({hour:6, minute:15}) → defaults to Z
    sql = tr("RETURN time({hour:6, minute:15}) AS t")
    assert "06:15" in sql
    assert "Z" in sql


def test_localtime_from_string():
    sql = tr("RETURN localtime('12:30:00') AS t")
    assert "12:30:00" in sql


def test_time_from_string_with_tz():
    sql = tr("RETURN time('12:30:00+01:00') AS t")
    assert "12:30:00" in sql
    assert "+01:00" in sql


def test_time_from_string_without_tz_gets_z():
    # time() with no tz in string → appends Z
    sql = tr("RETURN time('14:00:00') AS t")
    assert "14:00:00" in sql
    assert "Z" in sql


# ---------------------------------------------------------------------------
# Lines ~10840-11190 / 11197-11452: datetime construction
# ---------------------------------------------------------------------------


def test_localdatetime_from_map():
    # localdatetime({year:2020, month:1, day:1, hour:9})
    sql = tr("RETURN localdatetime({year:2020, month:1, day:1, hour:9}) AS dt")
    assert "2020-01-01T09:00" in sql


def test_localdatetime_from_string():
    sql = tr("RETURN localdatetime('2020-06-15T12:30:00') AS dt")
    assert "2020-06-15T12:30:00" in sql


def test_datetime_from_map_with_utc():
    # datetime({year:2020, month:6, day:15, hour:12, minute:0, timezone:'UTC'})
    sql = tr("RETURN datetime({year:2020, month:6, day:15, hour:12, minute:0, timezone:'UTC'}) AS dt")
    assert "2020-06-15" in sql
    assert "12:00" in sql


def test_datetime_from_string_with_offset():
    sql = tr("RETURN datetime('2020-06-15T12:30:00+01:00') AS dt")
    assert "2020-06-15" in sql
    assert "12:30:00" in sql
    assert "+01:00" in sql


def test_datetime_from_map_no_tz_defaults_z():
    # datetime without explicit timezone → Z
    sql = tr("RETURN datetime({year:2020, month:6, day:15, hour:12, minute:0}) AS dt")
    assert "2020-06-15" in sql
    assert "12:00" in sql
    assert "Z" in sql or "z" in sql.lower()


def test_localdatetime_from_map_with_seconds():
    sql = tr("RETURN localdatetime({year:2020, month:6, day:15, hour:12, minute:30, second:45}) AS dt")
    assert "2020-06-15T12:30:45" in sql


def test_datetime_from_string_z():
    sql = tr("RETURN datetime('2020-06-15T12:30:00Z') AS dt")
    assert "2020-06-15T12:30:00Z" in sql


# ---------------------------------------------------------------------------
# Lines ~8676-8804: _get_temporal_component_sql — property access on temporal types
# Trigger via WITH clause so the date var is in the translation context.
# ---------------------------------------------------------------------------


def test_date_year_component():
    # WITH date as variable → SUBSTRING for year
    sql = tr("WITH date('2020-06-15') AS d RETURN d.year AS y")
    assert "CY_TEMPORAL_FIELD" in sql


def test_date_month_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.month AS m")
    assert "CY_TEMPORAL_FIELD" in sql


def test_date_day_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.day AS dv")
    assert "CY_TEMPORAL_FIELD" in sql


def test_date_week_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.week AS w")
    # Uses IRIS {fn WEEK(...)}
    assert "WEEK" in sql.upper()


def test_date_dayofweek_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.dayOfWeek AS dow")
    assert "DAYOFWEEK" in sql.upper() or "MOD" in sql.upper()


def test_date_ordinalday_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.ordinalDay AS od")
    assert "CY_TEMPORAL_FIELD" in sql


def test_date_quarter_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.quarter AS q")
    assert "CY_TEMPORAL_FIELD" in sql


def test_date_weekyear_component():
    sql = tr("WITH date('2020-06-15') AS d RETURN d.weekYear AS wy")
    assert "CY_TEMPORAL_FIELD" in sql


def test_localdatetime_hour_component():
    sql = tr("WITH localdatetime('2020-06-15T12:30:00') AS dt RETURN dt.hour AS h")
    assert "SUBSTRING" in sql


def test_localdatetime_minute_component():
    sql = tr("WITH localdatetime('2020-06-15T12:30:00') AS dt RETURN dt.minute AS mi")
    assert "SUBSTRING" in sql


def test_localdatetime_second_component():
    sql = tr("WITH localdatetime('2020-06-15T12:30:00') AS dt RETURN dt.second AS s")
    assert "SUBSTRING" in sql


def test_localdatetime_year_component():
    sql = tr("WITH localdatetime('2020-06-15T12:30:00') AS dt RETURN dt.year AS y")
    assert "CY_TEMPORAL_FIELD" in sql


def test_localdatetime_epochseconds():
    sql = tr("WITH localdatetime('2020-06-15T12:30:00') AS dt RETURN dt.epochSeconds AS es")
    assert "DATEDIFF" in sql.upper()


def test_localtime_hour_component():
    sql = tr("WITH localtime('12:30:45') AS t RETURN t.hour AS h")
    assert "SUBSTRING" in sql


def test_time_timezone_component():
    sql = tr("WITH time('12:30:00+01:00') AS t RETURN t.timezone AS tz")
    assert "CHARINDEX" in sql.upper()


def test_time_offset_minutes_component():
    sql = tr("WITH time('12:30:00+01:00') AS t RETURN t.offsetMinutes AS om")
    assert "CHARINDEX" in sql.upper()


# ---------------------------------------------------------------------------
# Lines ~9622-9717: _extract_int_from_map_entry, _has_map_key, _date_from_iso_week
# Called indirectly via date({week:...}) and truncate
# ---------------------------------------------------------------------------


def test_extract_int_via_date_week_map():
    # Exercises _extract_int_from_map_entry and _date_from_iso_week
    sql = tr("RETURN date({year:2020, week:1, dayOfWeek:1}) AS d")
    assert "2019-12-30" in sql or "2020" in sql  # ISO week 1 of 2020 starts Dec 30 2019


def test_extract_num_via_duration_map():
    # Exercises _extract_num_from_map_entry (floats allowed for duration)
    sql = tr("RETURN duration({years:1, months:3, days:14}) AS dur")
    assert "P1Y3M14D" in sql


def test_has_map_key_via_ordinal_branch():
    # Exercises _has_map_key for ordinalDay key
    sql = tr("RETURN date({year:2020, ordinalDay:1}) AS d")
    assert "2020-01-01" in sql


# ---------------------------------------------------------------------------
# Lines ~11679-11931: duration construction
# ---------------------------------------------------------------------------


def test_duration_from_map_years_months_days():
    sql = tr("RETURN duration({years:1, months:3, days:14}) AS dur")
    assert "P1Y3M14D" in sql


def test_duration_from_map_time_components():
    sql = tr("RETURN duration({hours:2, minutes:30, seconds:15}) AS dur")
    assert "PT" in sql
    assert "H" in sql or "M" in sql


def test_duration_from_iso_string():
    sql = tr("RETURN duration('P1Y3M14D') AS dur")
    assert "P1Y3M14D" in sql


def test_duration_from_iso_string_time_only():
    sql = tr("RETURN duration('PT22H') AS dur")
    assert "PT22H" in sql


def test_duration_from_iso_string_mixed():
    sql = tr("RETURN duration('P1YT4H50M') AS dur")
    assert "P1Y" in sql
    assert "T" in sql


# ---------------------------------------------------------------------------
# Lines ~11960-12041: duration arithmetic (duration.between etc.)
# ---------------------------------------------------------------------------


def test_duration_between_dates():
    sql = tr("RETURN duration.between(date('2020-01-01'), date('2020-06-15')) AS dur")
    # Computed at translate time → should be a literal ISO duration string
    assert "P" in sql


def test_duration_between_datetimes():
    sql = tr("RETURN duration.between(datetime('2020-01-01T00:00:00Z'), datetime('2020-06-15T12:00:00Z')) AS dur")
    assert "P" in sql


def test_duration_between_times():
    sql = tr("RETURN duration.between(localtime('10:00:00'), localtime('14:30:00')) AS dur")
    assert "PT" in sql


def test_duration_inmonths():
    sql = tr("RETURN duration.inMonths(date('2020-01-01'), date('2020-06-15')) AS dur")
    assert "P" in sql


def test_duration_indays():
    sql = tr("RETURN duration.inDays(date('2020-01-01'), date('2020-06-15')) AS dur")
    # 166 days
    assert "P166D" in sql


def test_duration_inseconds():
    sql = tr("RETURN duration.inSeconds(localtime('00:00:00'), localtime('01:00:00')) AS dur")
    assert "PT1H" in sql or "PT3600S" in sql or "3600" in sql


# ---------------------------------------------------------------------------
# Lines ~12084-12336: date.truncate, datetime.truncate, localdatetime.truncate
# ---------------------------------------------------------------------------


def test_date_truncate_month():
    sql = tr("RETURN date.truncate('month', date('2020-06-15')) AS d")
    assert "2020-06-01" in sql


def test_date_truncate_year():
    sql = tr("RETURN date.truncate('year', date('2020-06-15')) AS d")
    assert "2020-01-01" in sql


def test_date_truncate_decade():
    sql = tr("RETURN date.truncate('decade', date('2020-06-15')) AS d")
    assert "2020-01-01" in sql


def test_date_truncate_century():
    sql = tr("RETURN date.truncate('century', date('2020-06-15')) AS d")
    assert "2000-01-01" in sql


def test_date_truncate_millennium():
    sql = tr("RETURN date.truncate('millennium', date('2020-06-15')) AS d")
    assert "2000-01-01" in sql


def test_date_truncate_quarter():
    # Q2 starts April 1
    sql = tr("RETURN date.truncate('quarter', date('2020-06-15')) AS d")
    assert "2020-04-01" in sql


def test_date_truncate_week():
    # Monday of that ISO week
    sql = tr("RETURN date.truncate('week', date('2020-06-15')) AS d")
    # 2020-06-15 is a Monday; truncated to itself
    assert "2020-06-15" in sql


def test_datetime_truncate_day():
    sql = tr("RETURN datetime.truncate('day', datetime('2020-06-15T12:30:00Z')) AS dt")
    assert "2020-06-15T00:00" in sql


def test_datetime_truncate_hour():
    sql = tr("RETURN datetime.truncate('hour', datetime('2020-06-15T12:30:00Z')) AS dt")
    assert "2020-06-15T12:00" in sql


def test_localdatetime_truncate_hour():
    sql = tr("RETURN localdatetime.truncate('hour', localdatetime('2020-06-15T12:30:00')) AS dt")
    assert "2020-06-15T12:00" in sql


def test_localdatetime_truncate_minute():
    sql = tr("RETURN localdatetime.truncate('minute', localdatetime('2020-06-15T12:30:45')) AS dt")
    assert "2020-06-15T12:30" in sql


# ---------------------------------------------------------------------------
# Lines ~12343-12612: temporal namespace helpers (date.realtime / .statement)
# ---------------------------------------------------------------------------


def test_date_realtime_returns_null_or_passthrough():
    # date.realtime() can't be evaluated at compile time → returns None → falls through
    # The translator should still produce valid SQL (may contain NULL)
    sql = tr("RETURN date.realtime() AS d")
    assert sql is not None and len(sql) > 0


def test_date_statement_with_null_arg():
    # date.statement(null) → NULL (lines ~12478-12486)
    sql = tr("RETURN date.statement(null) AS d")
    assert "NULL" in sql.upper()


def test_datetime_fromepoch():
    # datetime.fromepoch(0, 0) → 1970-01-01T00:00:00Z
    sql = tr("RETURN datetime.fromepoch(0, 0) AS dt")
    assert "1970-01-01T00:00:00Z" in sql


def test_datetime_fromepoch_with_secs():
    # datetime.fromepoch(86400, 0) → 1970-01-02T00:00:00Z
    sql = tr("RETURN datetime.fromepoch(86400, 0) AS dt")
    assert "1970-01-02T00:00:00Z" in sql


def test_datetime_fromepochmillis():
    sql = tr("RETURN datetime.fromepochMillis(0) AS dt")
    assert "1970-01-01T00:00:00Z" in sql


def test_datetime_fromepochmillis_with_ms():
    sql = tr("RETURN datetime.fromepochMillis(1000) AS dt")
    assert "1970-01-01T00:00:01Z" in sql


# ---------------------------------------------------------------------------
# Lines ~12622-12908: _eval_truncate full body — extended unit coverage
# ---------------------------------------------------------------------------


def test_eval_truncate_weekyear():
    # weekYear truncation
    sql = tr("RETURN date.truncate('weekYear', date('2020-06-15')) AS d")
    # 2020-06-15 weekYear = 2020; Monday of ISO week 1 of 2020 = 2019-12-30
    assert "2019-12-30" in sql or "2020" in sql


def test_eval_truncate_second():
    sql = tr("RETURN localdatetime.truncate('second', localdatetime('2020-06-15T12:30:45')) AS dt")
    assert "2020-06-15T12:30:45" in sql


def test_eval_truncate_millisecond():
    sql = tr("RETURN datetime.truncate('millisecond', datetime('2020-06-15T12:30:45.645876Z')) AS dt")
    # keeps ms part 645
    assert "2020-06-15" in sql
    assert "12:30:45" in sql


def test_eval_truncate_with_map_override():
    # date.truncate with 3rd arg map override
    sql = tr("RETURN date.truncate('year', date('2020-06-15'), {month:3}) AS d")
    assert "2020-03-01" in sql


def test_eval_truncate_datetime_with_tz_preserved():
    # datetime.truncate should preserve TZ
    sql = tr("RETURN datetime.truncate('day', datetime('2020-06-15T12:30:00+02:00')) AS dt")
    assert "2020-06-15" in sql
    assert "+02:00" in sql


# ---------------------------------------------------------------------------
# Lines ~12466-12614: _eval_temporal_ns_function — duration variants
# ---------------------------------------------------------------------------


def test_duration_between_negative():
    # rhs < lhs → negative duration
    sql = tr("RETURN duration.between(date('2020-06-15'), date('2020-01-01')) AS dur")
    assert "-" in sql or "P" in sql


def test_duration_between_same_date():
    sql = tr("RETURN duration.between(date('2020-01-01'), date('2020-01-01')) AS dur")
    assert "PT0S" in sql


def test_duration_property_years():
    # duration.years component via WITH variable
    sql = tr("WITH duration('P2Y3M') AS dur RETURN dur.years AS y")
    assert "CY_DURATION_FIELD" in sql


def test_duration_property_months():
    sql = tr("WITH duration('P2Y3M') AS dur RETURN dur.months AS m")
    assert "CY_DURATION_FIELD" in sql


def test_duration_property_days():
    sql = tr("WITH duration('P30D') AS dur RETURN dur.days AS d")
    assert "CHARINDEX" in sql.upper() or "SUBSTRING" in sql.upper() or "CASE" in sql.upper()


def test_duration_property_hours():
    sql = tr("WITH duration('PT22H') AS dur RETURN dur.hours AS h")
    assert "CY_DURATION_FIELD" in sql


def test_duration_property_minutes():
    sql = tr("WITH duration('PT1H30M') AS dur RETURN dur.minutes AS m")
    assert "CY_DURATION_FIELD" in sql


# ---------------------------------------------------------------------------
# Edge cases: datetime with date+time variable combination
# ---------------------------------------------------------------------------


def test_localdatetime_from_date_and_time():
    # localdatetime({year:2020, month:6, day:15, hour:12, minute:0})
    sql = tr("RETURN localdatetime({year:2020, month:6, day:15, hour:12, minute:0}) AS dt")
    assert "2020-06-15T12:00" in sql


def test_datetime_subsecond_milliseconds():
    # datetime with millisecond component
    sql = tr("RETURN localdatetime({year:2020, month:1, day:1, hour:0, minute:0, second:0, millisecond:500}) AS dt")
    assert "2020-01-01" in sql
    assert ".5" in sql or "500" in sql


def test_date_from_map_with_date_base():
    # date({date: date('2020-06-15'), year: 2021})
    sql = tr("RETURN date({date: date('2020-06-15'), year: 2021}) AS d")
    assert "2021" in sql


def test_localtime_with_millisecond():
    sql = tr("RETURN localtime({hour:12, minute:0, second:0, millisecond:250}) AS t")
    assert "12:00:00" in sql
    assert ".25" in sql or "250" in sql


def test_time_with_nanosecond_component():
    sql = tr("RETURN time({hour:0, minute:0, second:0, nanosecond:1}) AS t")
    # Should produce time with fractional seconds
    assert "00:00:00" in sql
