"""4.6.3: a duration's field reads straight off a constructor, a stored property or a duration function.

Before 4.6.3 only a WITH column's field read (`WITH duration('PT1H') AS d RETURN d.seconds`: 3600):
`RETURN duration('PT1H').seconds` translated to `SELECT ?.seconds` (SQLCODE -25), and `a.d.seconds` and
`duration.inSeconds(...).seconds` to `JSON_VALUE(<the duration>, '$.seconds')`, which fails on text that is not JSON
(-149). The translation now reads the field as the WITH form does (CY_DURATION_FIELD for a duration, the runtime
accessor for a stored property). Live answers: tests/e2e/test_463_duration_field_direct_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sq(cypher, params=None):
    return translate_to_sql(parse_query(cypher), params or {})


@pytest.mark.parametrize(
    "query",
    [
        "RETURN duration('PT1H').seconds AS s",
        "RETURN duration.inSeconds(datetime('2020-01-01T00:00:00Z'), datetime('2020-01-01T01:00:00Z')).seconds AS s",
        "RETURN duration.between(date('2020-01-01'), date('2020-03-04')).days AS s",
        "RETURN duration($d).minutes AS s",
    ],
)
def test_a_constructed_duration_reads_its_field(query):
    sq = _sq(query, {"d": "PT1H30M"})
    assert "CY_DURATION_FIELD" in sq.sql, sq.sql
    assert ".seconds" not in sq.sql and "'$.seconds'" not in sq.sql and "'$.days'" not in sq.sql, sq.sql


def test_each_bind_appears_once():
    sq = _sq("RETURN duration($d).minutes AS s", {"d": "PT1H30M"})
    assert sq.sql.count("?") == len(sq.parameters[0]), (sq.sql, sq.parameters)


def test_a_stored_duration_reads_its_field_by_its_text():
    sq = _sq("MATCH (a:D) RETURN a.d.seconds AS s")
    assert "'$.seconds'" in sq.sql and "LIKE 'P%'" in sq.sql, sq.sql  # the runtime accessor: a map's key still reads
