"""4.7.0 row 15: a named zone's offset comes from the ``tzdata`` wheel first, not the host's tz database.

``datetime('1818-07-21T21:40:32.142[Europe/Stockholm]')`` is ``+00:53:28`` in the openCypher TCK (Temporal2 [6]) and in
the ``tzdata`` wheel, whose mainline build links Stockholm to Berlin. Python's ``zoneinfo.ZoneInfo`` reads the host's
database first (/usr/share/zoneinfo), and Debian and Ubuntu build theirs with ``backzone``, which keeps Stockholm's own
1818 offset: ``+01:12:12`` in ``python:3.12-slim`` (measured 2026-11-10 with ~/.cache/ivg-bench/tzprobe/probe.py). So
the same IVG answered by host. Here the host is a stand-in answering ``+01:12:12`` for every zone, as a backzone build
does for this one; the wheel's answer is the known one, ``+00:53:28``.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest

from iris_vector_graph import _tz
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

BACKZONE = dt.timezone(dt.timedelta(hours=1, minutes=12, seconds=12))
WHEN = dt.datetime(1818, 7, 21, 21, 40, 32)
MAINLINE = dt.timedelta(minutes=53, seconds=28)


@pytest.fixture
def backzone_host(monkeypatch):
    """zoneinfo.ZoneInfo as a backzone host answers: +01:12:12 for any zone it is asked for."""
    asked = []

    def host(name):
        asked.append(name)
        return BACKZONE

    monkeypatch.setattr(zoneinfo, "ZoneInfo", host)
    _tz.zone.cache_clear()
    yield asked
    _tz.zone.cache_clear()


def test_the_wheel_answers_before_the_host(backzone_host):
    assert WHEN.replace(tzinfo=_tz.zone("Europe/Stockholm")).utcoffset() == MAINLINE
    assert backzone_host == [], "the host's database was read"


def test_without_the_wheel_the_host_answers(backzone_host, monkeypatch):
    def no_wheel(name):
        raise ModuleNotFoundError("No module named 'tzdata'")

    monkeypatch.setattr(_tz, "_from_wheel", no_wheel)
    assert _tz.zone("Europe/Stockholm") is BACKZONE
    assert backzone_host == ["Europe/Stockholm"]


def test_a_zone_the_wheel_lacks_is_asked_of_the_host(backzone_host):
    assert _tz.zone("Not/AZone") is BACKZONE


@pytest.mark.parametrize("name", ["", "../etc/passwd", "/Europe/Stockholm", "Europe/../Berlin", "Europe/Stockholm/"])
def test_a_name_that_is_no_zone_key_is_not_read_as_a_path(name):
    with pytest.raises(ValueError):
        _tz.zone(name)


def test_every_translation_site_reads_the_wheel(backzone_host):
    """The string form, the map form and an offset at a reference date: each +00:53 (and :28 where printed)."""
    def sql(q):
        s = translate_to_sql(parse_query(q), {}).sql
        return s if isinstance(s, str) else "\n".join(s)

    assert "1818-07-21T21:40:32.142+00:53:28[Europe/Stockholm]" in sql(
        "RETURN datetime('1818-07-21T21:40:32.142[Europe/Stockholm]') AS d")
    assert "+00:53[Europe/Stockholm]" in sql(
        "RETURN datetime({year: 1818, month: 7, day: 21, hour: 21, minute: 40, timezone: 'Europe/Stockholm'}) AS d")
    from iris_vector_graph.cypher.translator import _iana_tz_offset

    assert _iana_tz_offset("Europe/Stockholm", 1818, 7, 21) == "+00:53:28"
    assert backzone_host == [], "a site read the host's database"


def test_a_modern_offset_is_unchanged():
    assert dt.datetime(2015, 7, 21, 12).replace(tzinfo=_tz.zone("Europe/Stockholm")).utcoffset() == dt.timedelta(hours=2)
    assert dt.datetime(2015, 1, 21, 12).replace(tzinfo=_tz.zone("America/New_York")).utcoffset() == dt.timedelta(hours=-5)
