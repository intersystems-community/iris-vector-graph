"""4.7.0 row 20b: a named zone's offset is read at the value's own wall time, with its seconds.

- `_iana_tz_offset` read the offset at midnight of the day: on the day Europe/Berlin moves to summer time (2015-03-29,
  02:00 CET -> 03:00 CEST) `datetime('2015-03-29T12:00[Europe/Berlin]')` answered +01:00, where 12:00 that day is
  +02:00; on the day it moves back (2015-10-25, 03:00 CEST -> 02:00 CET) 12:00 answered +02:00, where it is +01:00.
- `datetime({..., timezone: 'Europe/Stockholm'})` printed the offset to the minute: Stockholm in 1818 is +00:53:28
  (the tzdata wheel's, 4.7.0 row 15), and the map form answered +00:53, where the string form answers +00:53:28.

The offsets are the tz database's, worked from its rules: CET +01:00, CEST +02:00, Stockholm 1818 Berlin's LMT
+00:53:28.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _iana_tz_offset, translate_to_sql


def _value(cypher):
    sql = translate_to_sql(parse_query(cypher), {}).sql
    sql = sql if isinstance(sql, str) else sql[-1]
    return sql.split("'")[1]


@pytest.mark.parametrize("text, want", [
    ("2015-03-29T01:30[Europe/Berlin]", "2015-03-29T01:30+01:00[Europe/Berlin]"),
    ("2015-03-29T12:00[Europe/Berlin]", "2015-03-29T12:00+02:00[Europe/Berlin]"),
    ("2015-10-25T01:00[Europe/Berlin]", "2015-10-25T01:00+02:00[Europe/Berlin]"),
    ("2015-10-25T12:00[Europe/Berlin]", "2015-10-25T12:00+01:00[Europe/Berlin]"),
    ("2015-03-08T12:00[America/New_York]", "2015-03-08T12:00-04:00[America/New_York]"),
])
def test_a_datetime_string_reads_the_offset_at_its_wall_time(text, want):
    assert _value(f"RETURN datetime('{text}') AS d") == want


def test_the_offset_helper_reads_the_wall_time():
    assert _iana_tz_offset("Europe/Berlin", 2015, 3, 29) == "+01:00"  # midnight, still CET
    assert _iana_tz_offset("Europe/Berlin", 2015, 3, 29, 12, 0, 0) == "+02:00"
    assert _iana_tz_offset("Europe/Berlin", 2015, 10, 25, 12, 0, 0) == "+01:00"


@pytest.mark.parametrize("query, want", [
    ("RETURN datetime({year: 1818, month: 7, day: 21, hour: 21, minute: 40, second: 32, "
     "timezone: 'Europe/Stockholm'}) AS d", "1818-07-21T21:40:32+00:53:28[Europe/Stockholm]"),
    ("RETURN datetime({year: 1818, month: 7, day: 21, hour: 21, minute: 40, timezone: 'Europe/Stockholm'}) AS d",
     "1818-07-21T21:40+00:53:28[Europe/Stockholm]"),
    ("RETURN datetime({year: 2015, month: 3, day: 29, hour: 12, timezone: 'Europe/Berlin'}) AS d",
     "2015-03-29T12:00+02:00[Europe/Berlin]"),
])
def test_a_datetime_map_prints_the_offsets_seconds(query, want):
    assert _value(query) == want


def test_the_string_and_map_forms_agree():
    s = _value("RETURN datetime('1818-07-21T21:40:32[Europe/Stockholm]') AS d")
    m = _value("RETURN datetime({year: 1818, month: 7, day: 21, hour: 21, minute: 40, second: 32, "
               "timezone: 'Europe/Stockholm'}) AS d")
    assert s == m == "1818-07-21T21:40:32+00:53:28[Europe/Stockholm]"


@pytest.mark.parametrize("unit, want", [
    ("hour", "2015-03-29T12:00+02:00[Europe/Berlin]"),
    ("day", "2015-03-29T00:00+01:00[Europe/Berlin]"),
])
def test_a_truncation_reads_the_offset_at_the_truncated_time(unit, want):
    q = (f"RETURN datetime.truncate('{unit}', datetime({{year: 2015, month: 3, day: 29, hour: 12, minute: 30, "
         f"timezone: 'Europe/Berlin'}})) AS d")
    assert _value(q) == want


@pytest.mark.parametrize("query, want", [
    # the string form kept no zone: '2015-03-29T12:00Z'
    ("RETURN datetime.truncate('hour', datetime('2015-03-29T12:30[Europe/Berlin]')) AS d",
     "2015-03-29T12:00+02:00[Europe/Berlin]"),
    ("RETURN datetime.truncate('day', datetime('2015-03-29T12:30[Europe/Berlin]')) AS d",
     "2015-03-29T00:00+01:00[Europe/Berlin]"),
    ("RETURN datetime.truncate('hour', datetime('1818-07-21T21:40:32[Europe/Stockholm]')) AS d",
     "1818-07-21T21:00+00:53:28[Europe/Stockholm]"),
    # what the map form and a numeric offset answered already
    ("RETURN datetime.truncate('hour', datetime('2015-03-29T12:30+02:00[Europe/Berlin]')) AS d",
     "2015-03-29T12:00+02:00[Europe/Berlin]"),
    ("RETURN datetime.truncate('hour', datetime('2015-03-29T12:30+02:00')) AS d", "2015-03-29T12:00+02:00"),
    # a time keeps its zone too, as time('12:30[Europe/Berlin]') answers +02:00 (its reference date, 2015-07-21)
    ("RETURN time.truncate('hour', time('12:30[Europe/Berlin]')) AS d", "12:00+02:00[Europe/Berlin]"),
    # a local value has none to keep
    ("RETURN localdatetime.truncate('hour', datetime('2015-03-29T12:30[Europe/Berlin]')) AS d", "2015-03-29T12:00"),
])
def test_a_truncation_of_a_zoned_string_keeps_its_zone(query, want):
    assert _value(query) == want
