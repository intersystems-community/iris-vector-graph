"""4.6.3, live: a duration's field reads straight off a constructor, a stored property and a duration function, and
answers what the WITH form does.

Expected answers: the Neo4j Cypher manual's "Accessing components of durations" example,
`duration({years: 1, months: 4, days: 111, hours: 1, minutes: 1, seconds: 1, nanoseconds: 111111111})`, written here
as its ISO text 'P1Y4M111DT1H1M1.111111111S': years 1, quarters 5, months 16, weeks 15, days 111, hours 1, minutes 61,
seconds 3661, milliseconds 3661111, nanoseconds 3661111111111, monthsOfYear 4, daysOfWeek 6, minutesOfHour 1,
secondsOfMinute 1, nanosecondsOfSecond 111111111. And `duration.inSeconds` of one hour: PT1H, whose seconds are 3600.

Before 4.6.3: `RETURN duration(...).f` failed with SQLCODE -25, `a.d.f` and `duration.inSeconds(...).seconds` with
-149; only `WITH ... AS d RETURN d.f` read. Each case runs on the default route and the SQL route.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

ISO = "P1Y4M111DT1H1M1.111111111S"
FIELDS = {
    "years": 1, "quarters": 5, "months": 16, "weeks": 15, "days": 111, "hours": 1, "minutes": 61, "seconds": 3661,
    "milliseconds": 3661111, "nanoseconds": 3661111111111, "monthsOfYear": 4, "daysOfWeek": 6, "minutesOfHour": 1,
    "secondsOfMinute": 1, "nanosecondsOfSecond": 111111111,
}
WANT = [[(v, "int") for v in FIELDS.values()]]


@pytest.fixture(scope="module")
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    eng = engs["sql"]
    eng.execute_cypher("MATCH (n:D463) DETACH DELETE n", raise_on_error=True)
    eng.execute_cypher(f"CREATE (:D463 {{d: duration('{ISO}')}})", raise_on_error=True)
    yield engs
    eng.execute_cypher("MATCH (n:D463) DETACH DELETE n", raise_on_error=True)
    conn.close()


def _items(base):
    return ", ".join(f"{base}.{f} AS {f}" for f in FIELDS)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize(
    "query",
    [
        f"WITH duration('{ISO}') AS d RETURN {_items('d')}",  # the form that read before 4.6.3: the control
        f"RETURN {_items(f'duration({chr(39)}{ISO}{chr(39)})')}",
        f"RETURN {_items('duration($iso)')}",
        f"MATCH (a:D463) RETURN {_items('a.d')}",
        f"MATCH (a:D463) WITH a.d AS d RETURN {_items('d')}",
    ],
    ids=["with", "constructor", "constructor-param", "stored", "stored-with"],
)
def test_a_duration_field_reads_on_every_form(engines, route, query):
    assert ns463.typed(ns463.rows(engines[route], query, {"iso": ISO})) == WANT


IN_SECONDS = "duration.inSeconds(datetime('2020-01-01T00:00:00Z'), datetime('2020-01-01T01:00:00Z'))"


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_duration_function_reads_its_field(engines, route):
    q = f"RETURN {IN_SECONDS}.seconds AS s, {IN_SECONDS}.minutes AS m"
    assert ns463.typed(ns463.rows(engines[route], q)) == [[(3600, "int"), (60, "int")]]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_missing_duration_property_reads_null(engines, route):
    assert ns463.rows(engines[route], "MATCH (a:D463) RETURN a.nothing.seconds AS s") == [[None]]
