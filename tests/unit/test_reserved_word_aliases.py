"""A RETURN alias that is an IRIS reserved word is quoted.

`RETURN n.at AS at` emitted `AS at`; AT is reserved, the statement failed at Prepare and
execute_cypher answered zero rows. _IRIS_RESERVED had 110 words; IRIS 2026.3 reports 208
(%SYSTEM.SQL.IsReservedWord) and 118 of them were missing.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("word", ["at", "current", "level", "match", "names", "of", "open", "to", "trim", "work"])
def test_reserved_alias_is_quoted(word):
    sql = translate_to_sql(parse_query(f"MATCH (n) RETURN n.x AS {word}"), {}).sql
    assert f'AS "{word}"' in sql, sql


def test_an_ordinary_alias_is_not_quoted():
    sql = translate_to_sql(parse_query("MATCH (n) RETURN n.x AS ts"), {}).sql
    assert "AS ts" in sql and 'AS "ts"' not in sql
