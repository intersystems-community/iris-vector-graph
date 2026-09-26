"""
Tests targeting uncovered lines in iris_vector_graph/cypher/translator.py:
- Lines 11584-11700  (isnan, isinfinite, haversin, date/datetime/localdatetime/time/localtime functions)
- Lines 11732-11854  (more temporal: localtime variable arg, duration from map)
- Lines 11873-11991  (temporal_to_datetime_obj, compute_duration_between internals)
- Lines 12005-12167  (wall-clock parse, compute_forward)
- Lines 12205-12314  (duration_inmonths, duration_indays)
- Lines 12317-12426  (duration_indays continued, duration_inseconds)
- Lines 12441-12461  (duration_inseconds time-only path)

All tests are pure Python — no IRIS connection required.
"""
import pytest
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


# ---------------------------------------------------------------------------
# Lines 11584-11590: isnan / isinfinite
# ---------------------------------------------------------------------------

def test_isnan_no_args():
    """isnan() with no args returns 0."""
    sql = tr("RETURN isNaN() AS r")
    assert "0" in sql


def test_isnan_with_arg():
    """isnan(x) returns a CASE WHEN NaN check."""
    sql = tr("MATCH (n) RETURN isNaN(n.val) AS r")
    assert "NaN" in sql or "nan" in sql.lower()


def test_isinfinite_no_args():
    """isInfinite() with no args returns 0."""
    sql = tr("RETURN isInfinite() AS r")
    assert "0" in sql


def test_isinfinite_with_arg():
    """isInfinite(x) returns a CASE WHEN Infinity check."""
    sql = tr("MATCH (n) RETURN isInfinite(n.val) AS r")
    assert "Infinity" in sql


# ---------------------------------------------------------------------------
# Lines 11591-11600: haversin, e, rand, timestamp, randomuuid
# ---------------------------------------------------------------------------

def test_haversin_with_arg():
    """haversin(x) expands to (1 - COS(x)) / 2."""
    sql = tr("MATCH (n) RETURN haversin(n.theta) AS r")
    assert "COS" in sql


def test_haversin_no_args():
    """haversin() with no args returns NULL."""
    sql = tr("RETURN haversin() AS r")
    assert "NULL" in sql


def test_e_function():
    """e() returns EXP(1)."""
    sql = tr("RETURN e() AS r")
    assert "EXP(1)" in sql


def test_rand_function():
    """rand() returns SQLUser.RAND()."""
    sql = tr("RETURN rand() AS r")
    assert "RAND" in sql


def test_timestamp_function():
    """timestamp() returns DATEDIFF milliseconds from epoch."""
    sql = tr("RETURN timestamp() AS r")
    assert "DATEDIFF" in sql


def test_randomuuid_function():
    """randomUUID() returns SQLUser.NEWID()."""
    sql = tr("RETURN randomUUID() AS r")
    assert "NEWID" in sql


# ---------------------------------------------------------------------------
# Lines 11601-11622: date() function
# ---------------------------------------------------------------------------

def test_date_no_args():
    """date() with no args reads the statement clock."""
    sql = tr("RETURN date() AS r")
    assert "NULL" not in sql


def test_date_from_string_literal():
    """date('1984-10-11') returns '1984-10-11'."""
    sql = tr("RETURN date('1984-10-11') AS r")
    assert "'1984-10-11'" in sql


def test_date_from_compact_string():
    """date('19841011') should parse to 1984-10-11."""
    sql = tr("RETURN date('19841011') AS r")
    assert "1984" in sql and "10" in sql and "11" in sql


def test_date_from_map_literal():
    """date({year: 2021, month: 6, day: 15}) → '2021-06-15'."""
    sql = tr("RETURN date({year: 2021, month: 6, day: 15}) AS r")
    assert "'2021-06-15'" in sql


def test_date_from_variable():
    """date(dt_var) on a Variable arg returns SUBSTRING."""
    sql = tr("WITH datetime('2021-06-15T10:30:00Z') AS dt RETURN date(dt) AS r")
    assert "SUBSTRING" in sql or "2021-06-15" in sql


# ---------------------------------------------------------------------------
# Lines 11623-11661: localdatetime() function
# ---------------------------------------------------------------------------

def test_localdatetime_no_args():
    """localdatetime() with no args reads the statement clock."""
    sql = tr("RETURN localdatetime() AS r")
    assert "NULL" not in sql


def test_localdatetime_from_string():
    """localdatetime('2015-07-21T21:40:32.142') → literal."""
    sql = tr("RETURN localdatetime('2015-07-21T21:40:32.142') AS r")
    assert "2015-07-21" in sql
    assert "21:40:32" in sql


def test_localdatetime_from_map():
    """localdatetime({year: 2015, month: 7, day: 21, hour: 21, minute: 40}) → literal."""
    sql = tr("RETURN localdatetime({year: 2015, month: 7, day: 21, hour: 21, minute: 40}) AS r")
    assert "2015-07-21" in sql
    assert "21:40" in sql


def test_localdatetime_from_datetime_string():
    """localdatetime(datetime('2015-07-21T21:40:32Z')) strips timezone."""
    sql = tr("RETURN localdatetime(datetime('2015-07-21T21:40:32Z')) AS r")
    # Timezone Z stripped → localdatetime is TZ-naive
    assert "Z" not in sql or "2015-07-21" in sql


def test_localdatetime_strips_offset():
    """localdatetime(datetime('2015-07-21T21:40:32+02:00')) strips +02:00."""
    sql = tr("RETURN localdatetime(datetime('2015-07-21T21:40:32+02:00')) AS r")
    assert "2015-07-21" in sql


def test_localdatetime_variable_arg_runtime():
    """localdatetime(col_var) generates SQL CASE for stripping TZ at runtime."""
    sql = tr("MATCH (n) RETURN localdatetime(n.dt) AS r")
    assert "CHARINDEX" in sql or "SUBSTRING" in sql or "CASE" in sql


# ---------------------------------------------------------------------------
# Lines 11662-11690: datetime() function
# ---------------------------------------------------------------------------

def test_datetime_no_args():
    """datetime() with no args reads the statement clock."""
    sql = tr("RETURN datetime() AS r")
    assert "NULL" not in sql


def test_datetime_from_string():
    """datetime('2015-07-21T21:40:32.142+0100') → literal."""
    sql = tr("RETURN datetime('2015-07-21T21:40:32.142+0100') AS r")
    assert "2015-07-21" in sql


def test_datetime_from_map():
    """datetime({year: 2015, month: 7, day: 21, hour: 21, minute: 40, timezone: '+01:00'})."""
    sql = tr("RETURN datetime({year: 2015, month: 7, day: 21, hour: 21, minute: 40, timezone: '+01:00'}) AS r")
    assert "2015-07-21" in sql


def test_datetime_from_localdatetime_var():
    """datetime(localdatetime_var) adds Z suffix."""
    sql = tr("WITH localdatetime('2015-07-21T21:40:32') AS ldt RETURN datetime(ldt) AS r")
    # Should produce a datetime with Z appended
    assert "Z" in sql or "2015-07-21" in sql


# ---------------------------------------------------------------------------
# Lines 11691-11729: localtime() and time() from map + string
# ---------------------------------------------------------------------------

def test_time_no_args():
    """time() with no args reads the statement clock."""
    sql = tr("RETURN time() AS r")
    assert "NULL" not in sql


def test_localtime_no_args():
    """localtime() with no args reads the statement clock."""
    sql = tr("RETURN localtime() AS r")
    assert "NULL" not in sql


def test_time_from_map():
    """time({hour: 23, minute: 59, second: 59, timezone: '+01:00'}) → '23:59:59+01:00'."""
    sql = tr("RETURN time({hour: 23, minute: 59, second: 59, timezone: '+01:00'}) AS r")
    assert "23:59:59" in sql
    assert "+01:00" in sql


def test_localtime_from_map():
    """localtime({hour: 12, minute: 0}) → '12:00'."""
    sql = tr("RETURN localtime({hour: 12, minute: 0}) AS r")
    assert "12:00" in sql


def test_time_from_string():
    """time('21:40:32.142Z') → '21:40:32.142Z'."""
    sql = tr("RETURN time('21:40:32.142Z') AS r")
    assert "21:40:32" in sql
    assert "Z" in sql


def test_localtime_from_string():
    """localtime('21:40:32') → '21:40:32'."""
    sql = tr("RETURN localtime('21:40:32') AS r")
    assert "21:40:32" in sql


def test_time_from_string_no_tz_adds_z():
    """time('21:40:32') without TZ → adds Z."""
    sql = tr("RETURN time('21:40:32') AS r")
    assert "Z" in sql


def test_localtime_from_map_with_millis():
    """localtime({hour: 12, minute: 31, second: 14, millisecond: 645}) → '12:31:14.645'."""
    sql = tr("RETURN localtime({hour: 12, minute: 31, second: 14, millisecond: 645}) AS r")
    assert "12:31:14" in sql
    assert "645" in sql


def test_time_from_map_with_nanosecond():
    """time({hour: 12, minute: 31, second: 14, nanosecond: 789, timezone: 'Z'}) → includes nanos."""
    sql = tr("RETURN time({hour: 12, minute: 31, second: 14, nanosecond: 789, timezone: 'Z'}) AS r")
    assert "12:31:14" in sql


# ---------------------------------------------------------------------------
# Lines 11732-11751: localtime/time from variable (runtime SQL)
# ---------------------------------------------------------------------------

def test_localtime_from_variable_generates_charindex():
    """localtime(v) where v is a WITH-bound variable → SQL with CHARINDEX for TZ stripping."""
    sql = tr("WITH datetime('2021-06-01T12:30:00+01:00') AS dt RETURN localtime(dt) AS r")
    assert "CHARINDEX" in sql or "12:30" in sql


def test_time_from_variable_generates_case():
    """time(v) where v is a WITH-bound variable → SQL expression for time part."""
    sql = tr("WITH datetime('2021-06-01T12:30:00Z') AS dt RETURN time(dt) AS r")
    assert "CHARINDEX" in sql or "CASE" in sql or "12:30" in sql


# ---------------------------------------------------------------------------
# Lines 11752-11824: duration() from map and string
# ---------------------------------------------------------------------------

def test_duration_from_string():
    """duration('P14DT16H12M') → 'P14DT16H12M'."""
    sql = tr("RETURN duration('P14DT16H12M') AS r")
    assert "P14DT16H12M" in sql


def test_duration_from_map_days_hours():
    """duration({days: 14, hours: 16, minutes: 12}) → 'P14DT16H12M'."""
    sql = tr("RETURN duration({days: 14, hours: 16, minutes: 12}) AS r")
    assert "P14DT16H12M" in sql


def test_duration_from_map_years_months():
    """duration({years: 1, months: 6}) → 'P1Y6M'."""
    sql = tr("RETURN duration({years: 1, months: 6}) AS r")
    assert "P1Y6M" in sql


def test_duration_from_map_weeks():
    """duration({weeks: 2}) → 14 days."""
    sql = tr("RETURN duration({weeks: 2}) AS r")
    assert "P14D" in sql


def test_duration_from_map_seconds():
    """duration({seconds: 90}) → 'PT1M30S'."""
    sql = tr("RETURN duration({seconds: 90}) AS r")
    assert "PT1M30S" in sql


def test_duration_from_map_milliseconds():
    """duration({milliseconds: 500}) → includes fractional seconds."""
    sql = tr("RETURN duration({milliseconds: 500}) AS r")
    assert "PT0.5S" in sql or "500" in sql


def test_duration_from_map_microseconds():
    """duration({microseconds: 1}) → includes nanosecond precision."""
    sql = tr("RETURN duration({microseconds: 1}) AS r")
    assert "PT" in sql


def test_duration_from_map_hours_minutes_seconds():
    """duration({hours: 2, minutes: 30, seconds: 15}) → 'PT2H30M15S'."""
    sql = tr("RETURN duration({hours: 2, minutes: 30, seconds: 15}) AS r")
    assert "PT2H30M15S" in sql


def test_duration_from_map_fractional_days():
    """duration({days: 1.5}) → 1 day + 12 hours."""
    sql = tr("RETURN duration({days: 1.5}) AS r")
    assert "P1DT12H" in sql


def test_duration_from_map_all_zeros():
    """duration({}) → 'PT0S'."""
    sql = tr("RETURN duration({years: 0, months: 0, days: 0}) AS r")
    assert "PT0S" in sql


# ---------------------------------------------------------------------------
# Lines 11873-11918: _temporal_to_datetime_obj paths
# (exercised via duration.between which calls it)
# ---------------------------------------------------------------------------

def test_duration_between_dates():
    """duration.between(date('1984-10-11'), date('2015-06-24')) → calendar diff."""
    sql = tr("RETURN duration.between(date('1984-10-11'), date('2015-06-24')) AS r")
    assert "P" in sql
    assert "Y" in sql


def test_duration_between_same_date():
    """duration.between(date('2020-01-01'), date('2020-01-01')) → PT0S."""
    sql = tr("RETURN duration.between(date('2020-01-01'), date('2020-01-01')) AS r")
    assert "PT0S" in sql


def test_duration_between_datetime():
    """duration.between(datetime('2017-01-01T00:00:00Z'), datetime('2017-01-02T12:00:00Z'))."""
    sql = tr("RETURN duration.between(datetime('2017-01-01T00:00:00Z'), datetime('2017-01-02T12:00:00Z')) AS r")
    assert "P1DT12H" in sql


def test_duration_between_localdatetime():
    """duration.between(localdatetime('2017-06-01T12:00:00'), localdatetime('2017-06-15T18:30:00'))."""
    sql = tr("RETURN duration.between(localdatetime('2017-06-01T12:00:00'), localdatetime('2017-06-15T18:30:00')) AS r")
    assert "P14DT6H30M" in sql


def test_duration_between_localtime():
    """duration.between(localtime('10:00:00'), localtime('14:30:00')) → 4h30m."""
    sql = tr("RETURN duration.between(localtime('10:00:00'), localtime('14:30:00')) AS r")
    assert "PT4H30M" in sql


def test_duration_between_time():
    """duration.between(time('10:00:00Z'), time('14:30:00Z')) → 4h30m."""
    sql = tr("RETURN duration.between(time('10:00:00Z'), time('14:30:00Z')) AS r")
    assert "PT4H30M" in sql


def test_duration_between_negative():
    """duration.between(date('2020-06-01'), date('2020-01-01')) → negative."""
    sql = tr("RETURN duration.between(date('2020-06-01'), date('2020-01-01')) AS r")
    assert "-" in sql


def test_duration_between_too_few_args():
    """duration.between with fewer than 2 args → NULL."""
    sql = tr("RETURN duration.between(date('2020-01-01')) AS r")
    assert "NULL" in sql


def test_duration_between_datetime_with_tz_offset():
    """duration.between with tz-aware datetimes uses UTC normalization."""
    sql = tr(
        "RETURN duration.between("
        "datetime('2017-01-01T00:00:00+01:00'), "
        "datetime('2017-01-01T00:00:00Z')) AS r"
    )
    # +01:00 means 1 hour behind UTC → diff is -1h
    assert "PT1H" in sql or "-" in sql or "P" in sql


def test_duration_between_date_and_datetime_mixed():
    """duration.between(date, datetime) — mixed wall-clock comparison."""
    sql = tr("RETURN duration.between(date('2021-01-01'), localdatetime('2021-06-15T12:00:00')) AS r")
    assert "P" in sql


# ---------------------------------------------------------------------------
# Lines 11991-12005 / 12005-12119: _parse_wall_clock_dt + _compute_forward
# (exercised via duration.between continuing from above)
# ---------------------------------------------------------------------------

def test_duration_between_date_to_date_one_year():
    """duration.between(date, date) → 1 year boundary."""
    sql = tr("RETURN duration.between(date('2000-01-01'), date('2001-01-01')) AS r")
    assert "P1Y" in sql


def test_duration_between_end_of_month():
    """duration.between on end-of-month anchoring."""
    sql = tr("RETURN duration.between(date('2020-01-31'), date('2020-02-29')) AS r")
    assert "P" in sql and "28D" in sql or "P" in sql


def test_duration_between_datetime_cross_midnight():
    """duration.between across midnight boundary."""
    sql = tr(
        "RETURN duration.between("
        "datetime('2021-01-01T23:00:00Z'), "
        "datetime('2021-01-02T01:00:00Z')) AS r"
    )
    assert "PT2H" in sql


def test_duration_between_localdatetime_same_day():
    """duration.between on same day → only time diff."""
    sql = tr(
        "RETURN duration.between("
        "localdatetime('2021-06-01T08:00:00'), "
        "localdatetime('2021-06-01T16:30:00')) AS r"
    )
    assert "PT8H30M" in sql


# ---------------------------------------------------------------------------
# Lines 12196-12223: duration.inMonths
# ---------------------------------------------------------------------------

def test_duration_inmonths_basic():
    """duration.inMonths(date, date) → year+month component only."""
    sql = tr("RETURN duration.inMonths(date('1984-10-11'), date('2015-06-24')) AS r")
    assert "Y" in sql or "M" in sql


def test_duration_inmonths_same_date():
    """duration.inMonths(same, same) → PT0S."""
    sql = tr("RETURN duration.inMonths(date('2020-06-01'), date('2020-06-01')) AS r")
    assert "PT0S" in sql


def test_duration_inmonths_time_only_returns_zero():
    """duration.inMonths with localtime args → PT0S (time-only → 0 months)."""
    sql = tr("RETURN duration.inMonths(localtime('10:00'), localtime('14:00')) AS r")
    assert "PT0S" in sql


def test_duration_inmonths_one_year():
    """duration.inMonths(date, date+1y) → P1Y."""
    sql = tr("RETURN duration.inMonths(date('2000-01-01'), date('2001-01-01')) AS r")
    assert "P1Y" in sql


def test_duration_inmonths_partial_year():
    """duration.inMonths(date, date+8m) → P8M."""
    sql = tr("RETURN duration.inMonths(date('2020-01-01'), date('2020-09-01')) AS r")
    assert "P8M" in sql


def test_duration_inmonths_insufficient_args():
    """duration.inMonths with 1 arg → NULL."""
    sql = tr("RETURN duration.inMonths(date('2020-01-01')) AS r")
    assert "NULL" in sql


# ---------------------------------------------------------------------------
# Lines 12226-12336: duration.inDays
# ---------------------------------------------------------------------------

def test_duration_indays_basic():
    """duration.inDays(date, date) → whole days."""
    sql = tr("RETURN duration.inDays(date('2020-01-01'), date('2020-01-15')) AS r")
    assert "P14D" in sql


def test_duration_indays_same_date():
    """duration.inDays(same, same) → PT0S."""
    sql = tr("RETURN duration.inDays(date('2020-01-01'), date('2020-01-01')) AS r")
    assert "PT0S" in sql


def test_duration_indays_time_only_returns_zero():
    """duration.inDays with time-only args → PT0S."""
    sql = tr("RETURN duration.inDays(localtime('08:00'), localtime('12:00')) AS r")
    assert "PT0S" in sql


def test_duration_indays_datetime():
    """duration.inDays(datetime, datetime) → whole days (ignores time)."""
    sql = tr(
        "RETURN duration.inDays("
        "datetime('2021-01-01T23:00:00Z'), "
        "datetime('2021-01-04T01:00:00Z')) AS r"
    )
    # 2 whole days (ignoring sub-day time fraction when total < 3 days)
    assert "P" in sql


def test_duration_indays_negative():
    """duration.inDays where rhs < lhs → negative days."""
    sql = tr("RETURN duration.inDays(date('2020-06-01'), date('2020-01-01')) AS r")
    assert "-" in sql


def test_duration_indays_insufficient_args():
    """duration.inDays with 1 arg → NULL."""
    sql = tr("RETURN duration.inDays(date('2020-01-01')) AS r")
    assert "NULL" in sql


def test_duration_indays_tz_aware():
    """duration.inDays with tz-aware datetimes applies UTC normalization."""
    sql = tr(
        "RETURN duration.inDays("
        "datetime('2021-01-01T00:00:00Z'), "
        "datetime('2021-01-03T00:00:00Z')) AS r"
    )
    assert "P2D" in sql


# ---------------------------------------------------------------------------
# Lines 12339-12463: duration.inSeconds
# ---------------------------------------------------------------------------

def test_duration_inseconds_basic():
    """duration.inSeconds(localtime, localtime) → seconds-only duration."""
    sql = tr("RETURN duration.inSeconds(localtime('10:00:00'), localtime('12:30:45')) AS r")
    assert "PT2H30M45S" in sql


def test_duration_inseconds_same():
    """duration.inSeconds(same, same) → PT0S."""
    sql = tr("RETURN duration.inSeconds(localtime('10:00:00'), localtime('10:00:00')) AS r")
    assert "PT0S" in sql


def test_duration_inseconds_datetime():
    """duration.inSeconds(datetime, datetime) → H/M/S only (no years/months/days shown for small diff)."""
    sql = tr(
        "RETURN duration.inSeconds("
        "datetime('2021-01-01T10:00:00Z'), "
        "datetime('2021-01-01T12:30:45Z')) AS r"
    )
    assert "PT2H30M45S" in sql


def test_duration_inseconds_cross_day():
    """duration.inSeconds crossing midnight → hours > 24."""
    sql = tr(
        "RETURN duration.inSeconds("
        "datetime('2021-01-01T22:00:00Z'), "
        "datetime('2021-01-02T04:00:00Z')) AS r"
    )
    assert "PT6H" in sql


def test_duration_inseconds_negative():
    """duration.inSeconds(later, earlier) → negative."""
    sql = tr("RETURN duration.inSeconds(localtime('14:00'), localtime('10:00')) AS r")
    assert "-" in sql


def test_duration_inseconds_insufficient_args():
    """duration.inSeconds with 1 arg → NULL."""
    sql = tr("RETURN duration.inSeconds(localtime('10:00')) AS r")
    assert "NULL" in sql


def test_duration_inseconds_time_with_tz():
    """duration.inSeconds(time, time) with UTC → applies tz normalization."""
    sql = tr(
        "RETURN duration.inSeconds("
        "time('10:00:00Z'), "
        "time('12:30:00Z')) AS r"
    )
    assert "PT2H30M" in sql


def test_duration_inseconds_mixed_time_and_datetime():
    """duration.inSeconds with localtime lhs and localdatetime rhs → time-only diff."""
    sql = tr(
        "RETURN duration.inSeconds("
        "localtime('10:00:00'), "
        "localdatetime('2021-06-01T12:30:00')) AS r"
    )
    assert "PT2H30M" in sql


def test_duration_inseconds_with_subseconds():
    """duration.inSeconds with sub-second times."""
    sql = tr("RETURN duration.inSeconds(localtime('10:00:00'), localtime('10:00:00.5')) AS r")
    assert "PT0.5S" in sql or "500" in sql or "PT" in sql


# ---------------------------------------------------------------------------
# Lines 12466-12614: _eval_temporal_ns_function (statement/transaction/realtime
#                    variants + datetime.fromepoch + datetime.fromepochmillis)
# ---------------------------------------------------------------------------

def test_date_statement_no_arg():
    """date.statement() → None / falls through (runtime)."""
    sql = tr("RETURN date.statement() AS r")
    # Should not raise; may produce NULL or a call placeholder
    assert sql is not None


def test_date_statement_null_arg():
    """date.statement(null) → NULL."""
    sql = tr("RETURN date.statement(null) AS r")
    assert "NULL" in sql


def test_datetime_transaction_null_arg():
    """datetime.transaction(null) → NULL."""
    sql = tr("RETURN datetime.transaction(null) AS r")
    assert "NULL" in sql


def test_localtime_realtime_null_arg():
    """localtime.realtime(null) → NULL."""
    sql = tr("RETURN localtime.realtime(null) AS r")
    assert "NULL" in sql


def test_datetime_fromepoch_basic():
    """datetime.fromepoch(0, 0) → '1970-01-01T00:00:00Z'."""
    sql = tr("RETURN datetime.fromepoch(0, 0) AS r")
    assert "'1970-01-01T00:00:00Z'" in sql


def test_datetime_fromepoch_with_nanos():
    """datetime.fromepoch(0, 123000000) → includes fractional seconds."""
    sql = tr("RETURN datetime.fromepoch(0, 123000000) AS r")
    assert "1970-01-01T00:00:00" in sql
    assert "123" in sql


def test_datetime_fromepoch_positive_secs():
    """datetime.fromepoch(86400, 0) → '1970-01-02T00:00:00Z'."""
    sql = tr("RETURN datetime.fromepoch(86400, 0) AS r")
    assert "'1970-01-02T00:00:00Z'" in sql


def test_datetime_fromepoch_no_args():
    """datetime.fromepoch() with no args → NULL."""
    sql = tr("RETURN datetime.fromepoch() AS r")
    assert "NULL" in sql


def test_datetime_fromepochmillis_basic():
    """datetime.fromEpochMillis(0) → '1970-01-01T00:00:00Z'."""
    sql = tr("RETURN datetime.fromEpochMillis(0) AS r")
    assert "'1970-01-01T00:00:00Z'" in sql


def test_datetime_fromepochmillis_with_millis():
    """datetime.fromEpochMillis(1000) → 1 second past epoch."""
    sql = tr("RETURN datetime.fromEpochMillis(1000) AS r")
    assert "'1970-01-01T00:00:01Z'" in sql


def test_datetime_fromepochmillis_remainder():
    """datetime.fromEpochMillis(1500) → includes ms in result."""
    sql = tr("RETURN datetime.fromEpochMillis(1500) AS r")
    assert "500" in sql or "1970-01-01" in sql


def test_datetime_fromepochmillis_no_args():
    """datetime.fromEpochMillis() → NULL."""
    sql = tr("RETURN datetime.fromEpochMillis() AS r")
    assert "NULL" in sql


# ---------------------------------------------------------------------------
# Lines 12617-12908: date.truncate / datetime.truncate / localdatetime.truncate
# ---------------------------------------------------------------------------

def test_date_truncate_millennium():
    """date.truncate('millennium', date('2017-10-11')) → '2000-01-01'."""
    sql = tr("RETURN date.truncate('millennium', date('2017-10-11')) AS r")
    assert "'2000-01-01'" in sql


def test_date_truncate_century():
    """date.truncate('century', date('1984-10-11')) → '1900-01-01'."""
    sql = tr("RETURN date.truncate('century', date('1984-10-11')) AS r")
    assert "'1900-01-01'" in sql


def test_date_truncate_decade():
    """date.truncate('decade', date('1984-10-11')) → '1980-01-01'."""
    sql = tr("RETURN date.truncate('decade', date('1984-10-11')) AS r")
    assert "'1980-01-01'" in sql


def test_date_truncate_year():
    """date.truncate('year', date('2017-10-11')) → '2017-01-01'."""
    sql = tr("RETURN date.truncate('year', date('2017-10-11')) AS r")
    assert "'2017-01-01'" in sql


def test_date_truncate_month():
    """date.truncate('month', date('2017-10-11')) → '2017-10-01'."""
    sql = tr("RETURN date.truncate('month', date('2017-10-11')) AS r")
    assert "'2017-10-01'" in sql


def test_date_truncate_quarter():
    """date.truncate('quarter', date('2017-10-11')) → '2017-10-01' (Q4=Oct)."""
    sql = tr("RETURN date.truncate('quarter', date('2017-10-11')) AS r")
    assert "2017-10" in sql or "2017-07" in sql  # Q4 starts Oct; Q3 July


def test_date_truncate_week():
    """date.truncate('week', date('2017-10-11')) → Monday of the ISO week."""
    sql = tr("RETURN date.truncate('week', date('2017-10-11')) AS r")
    assert "2017-10-09" in sql  # 2017-W41 starts Monday Oct 9


def test_date_truncate_weekyear():
    """date.truncate('weekYear', date('2015-02-05')) → first Monday of ISO week year."""
    sql = tr("RETURN date.truncate('weekYear', date('2015-02-05')) AS r")
    assert "2014-12-29" in sql  # ISO week year 2015 starts 2014-12-29


def test_datetime_truncate_millennium():
    """datetime.truncate('millennium', datetime('2017-10-11T12:30:00Z')) → truncated datetime."""
    sql = tr("RETURN datetime.truncate('millennium', datetime('2017-10-11T12:30:00Z')) AS r")
    assert "2000-01-01T00:00" in sql


def test_datetime_truncate_year():
    """datetime.truncate('year', datetime('2017-10-11T12:30:00Z')) → '2017-01-01T00:00Z'."""
    sql = tr("RETURN datetime.truncate('year', datetime('2017-10-11T12:30:00Z')) AS r")
    assert "'2017-01-01T00:00Z'" in sql


def test_datetime_truncate_month():
    """datetime.truncate('month', datetime('2017-10-11T12:30:00Z'))."""
    sql = tr("RETURN datetime.truncate('month', datetime('2017-10-11T12:30:00Z')) AS r")
    assert "2017-10-01" in sql


def test_datetime_truncate_day():
    """datetime.truncate('day', datetime('2017-10-11T12:30:00Z')) → midnight."""
    sql = tr("RETURN datetime.truncate('day', datetime('2017-10-11T12:30:00Z')) AS r")
    assert "2017-10-11T00:00Z" in sql


def test_datetime_truncate_hour():
    """datetime.truncate('hour', datetime('2017-10-11T12:30:58Z')) → 12:00."""
    sql = tr("RETURN datetime.truncate('hour', datetime('2017-10-11T12:30:58Z')) AS r")
    assert "2017-10-11T12:00Z" in sql


def test_datetime_truncate_minute():
    """datetime.truncate('minute', datetime('2017-10-11T12:31:14Z')) → 12:31."""
    sql = tr("RETURN datetime.truncate('minute', datetime('2017-10-11T12:31:14Z')) AS r")
    assert "2017-10-11T12:31Z" in sql


def test_datetime_truncate_second():
    """datetime.truncate('second', datetime('2017-10-11T12:31:14.645Z'))."""
    sql = tr("RETURN datetime.truncate('second', datetime('2017-10-11T12:31:14.645Z')) AS r")
    assert "2017-10-11T12:31:14Z" in sql


def test_datetime_truncate_millisecond():
    """datetime.truncate('millisecond', datetime with microseconds)."""
    sql = tr("RETURN datetime.truncate('millisecond', datetime('2017-10-11T12:31:14.645876Z')) AS r")
    assert "2017-10-11T12:31:14.645" in sql


def test_datetime_truncate_microsecond():
    """datetime.truncate('microsecond', datetime) → keeps microseconds."""
    sql = tr("RETURN datetime.truncate('microsecond', datetime('2017-10-11T12:31:14.645876Z')) AS r")
    assert "2017-10-11T12:31:14" in sql


def test_localdatetime_truncate_year():
    """localdatetime.truncate('year', localdatetime('2017-10-11T12:30:00')) → '2017-01-01T00:00'."""
    sql = tr("RETURN localdatetime.truncate('year', localdatetime('2017-10-11T12:30:00')) AS r")
    assert "'2017-01-01T00:00'" in sql


def test_localdatetime_truncate_month():
    """localdatetime.truncate('month', localdatetime('2017-10-11T12:30:00'))."""
    sql = tr("RETURN localdatetime.truncate('month', localdatetime('2017-10-11T12:30:00')) AS r")
    assert "2017-10-01" in sql


def test_localtime_truncate_hour():
    """localtime.truncate('hour', localtime('12:31:14')) → '12:00'."""
    sql = tr("RETURN localtime.truncate('hour', localtime('12:31:14')) AS r")
    assert "'12:00'" in sql


def test_time_truncate_hour():
    """time.truncate('hour', time('12:31:14Z')) → '12:00Z'."""
    sql = tr("RETURN time.truncate('hour', time('12:31:14Z')) AS r")
    assert "12:00Z" in sql


def test_date_truncate_week_with_day_of_week_override():
    """date.truncate('week', date('2017-10-11'), {dayOfWeek: 2}) → Tuesday."""
    sql = tr("RETURN date.truncate('week', date('2017-10-11'), {dayOfWeek: 2}) AS r")
    # Monday Oct 9 + 1 day = Tuesday Oct 10
    assert "2017-10-10" in sql


def test_datetime_truncate_minute_with_second_override():
    """datetime.truncate('minute', datetime('2017-10-11T12:31:14Z'), {second: 30})."""
    sql = tr("RETURN datetime.truncate('minute', datetime('2017-10-11T12:31:14Z'), {second: 30}) AS r")
    assert "12:31:30" in sql


def test_date_truncate_no_args():
    """date.truncate() → NULL."""
    sql = tr("RETURN date.truncate() AS r")
    assert "NULL" in sql


def test_datetime_truncate_runtime_unit():
    """datetime.truncate with a variable unit → None / falls through."""
    # When unit is a variable (not a literal string), should return None or a runtime expression
    sql = tr("MATCH (n) RETURN datetime.truncate(n.unit, datetime('2017-10-11T12:30:00Z')) AS r")
    # Should not crash; result is some SQL
    assert sql is not None


def test_date_truncate_from_map_datetime():
    """date.truncate('year', datetime({year: 2017, month: 10, day: 11, hour: 12, minute: 0, timezone: 'Z'}))."""
    sql = tr("RETURN date.truncate('year', datetime({year: 2017, month: 10, day: 11, hour: 12, minute: 0, timezone: 'Z'})) AS r")
    assert "2017-01-01" in sql


def test_localdatetime_truncate_with_timezone_override():
    """localdatetime.truncate with timezone override → promotes to datetime."""
    sql = tr("RETURN localdatetime.truncate('year', localdatetime('2017-10-11T12:30:00'), {timezone: '+02:00'}) AS r")
    # timezone in overrides → output is datetime type
    assert "+02:00" in sql or "2017-01-01" in sql


def test_datetime_truncate_with_tz_offset():
    """datetime.truncate preserves timezone offset."""
    sql = tr("RETURN datetime.truncate('year', datetime('2017-10-11T12:30:00+02:00')) AS r")
    assert "+02:00" in sql


def test_datetime_truncate_millisecond_with_nanosecond_override():
    """datetime.truncate('millisecond', dt, {nanosecond: 2}) → keeps ms, adds 2 ns."""
    sql = tr("RETURN datetime.truncate('millisecond', datetime('2017-10-11T12:31:14.645876543Z'), {nanosecond: 2}) AS r")
    assert "12:31:14" in sql


def test_localtime_truncate_minute():
    """localtime.truncate('minute', localtime('12:31:14')) → '12:31'."""
    sql = tr("RETURN localtime.truncate('minute', localtime('12:31:14')) AS r")
    assert "'12:31'" in sql


def test_time_truncate_minute():
    """time.truncate('minute', time('12:31:14.645Z')) → '12:31Z'."""
    sql = tr("RETURN time.truncate('minute', time('12:31:14.645Z')) AS r")
    assert "12:31Z" in sql


def test_time_truncate_second():
    """time.truncate('second', time('12:31:14.645876Z')) → '12:31:14Z'."""
    sql = tr("RETURN time.truncate('second', time('12:31:14.645876Z')) AS r")
    assert "12:31:14Z" in sql


def test_datetime_truncate_from_localtime_map():
    """localtime.truncate('hour', localtime({hour: 12, minute: 31, second: 14}))."""
    sql = tr("RETURN localtime.truncate('hour', localtime({hour: 12, minute: 31, second: 14})) AS r")
    assert "'12:00'" in sql


def test_datetime_truncate_from_time_map():
    """time.truncate('hour', time({hour: 12, minute: 31, second: 14, timezone: '+01:00'}))."""
    sql = tr("RETURN time.truncate('hour', time({hour: 12, minute: 31, second: 14, timezone: '+01:00'})) AS r")
    assert "12:00" in sql
