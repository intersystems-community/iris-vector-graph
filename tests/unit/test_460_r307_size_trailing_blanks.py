"""4.6.0 R307: size() of a string counts its trailing blanks.

`size(n.p)` of the string ' ' answered 0 and of 'ab ' 2: IRIS SQL's LENGTH() leaves trailing blanks out, CHAR_LENGTH()
counts them. Found by the value matrix (tests/e2e/test_460_r307_value_matrix_e2e.py, reader size_str).
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("q", [
    "MATCH (n:L) RETURN size(n.p)",
    "MATCH (n:L) WITH n.p AS s RETURN size(s)",
    "MATCH (n:L) WHERE size(n.p) = 1 RETURN n.id",
])
def test_size_of_a_string_counts_code_points(q):
    """CY_STR_SIZE: every character, trailing blanks too (LENGTH() left them out), a surrogate pair once (CHAR_LENGTH
    counts UTF-16 code units, so size('\U0001d11e') answered 2)."""
    sql = translate_to_sql(parse_query(q), {}).sql
    assert "CY_STR_SIZE(" in sql, sql
    assert not re.search(r"(?<![A-Z_])(CHAR_)?LENGTH\(", sql), sql


def test_size_of_a_computed_string_counts_code_points():
    sql = translate_to_sql(parse_query("MATCH (n:L) RETURN size(toString(n.p)) AS s"), {}).sql
    assert "CY_STR_SIZE(" in sql and "CHAR_LENGTH(" not in sql, sql


def test_cy_str_size_counts_a_surrogate_pair_once():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "schema.py").read_text()
    line = next(x for x in src.splitlines() if "FUNCTION Graph_KG.CY_STR_SIZE(" in x)
    body = line.split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert "56319" in body and "57344" in body and "$Char(0)" in body
    assert "'" not in body and "?" not in body and not re.search(r":\d", body)


def test_properties_map_reads_no_text_as_a_number():
    """4.6.0 R307: properties(n) (CY_PROPS_MAP) read '1.0' as 1 and '1e3' as the integer 1000 (+v). 4.7.0 row 10: it
    reads no text as a number at all, as `n.p` does not (a string '30' was 30 in the map); a stored number is
    CY_JSON_NUM's. The answers are in tests/e2e/test_470_properties_map_types_e2e.py."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "schema.py").read_text()
    body = next(x for x in src.splitlines() if "FUNCTION Graph_KG.CY_PROPS_MAP(" in x).split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert "$Match(v" not in body and "$IsValidNum" not in body and "CYJSONNUM(v)" in body
    assert "+v" not in body and "?" not in body and "'" not in body


def test_relationship_maps_read_only_canonical_number_text():
    """4.6.0 R307: properties(r) and a relationship list (CY_RETYPE_MAP) read the strings '1e3' and a Cypher CREATE's
    '1e-07' as numbers where RETURN r.p reads them as strings; the documented rule for a relationship is canonical text
    (KNOWN_ISSUES: the string ".95" stays a string), as parse_rel_prop_text has it."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "schema.py").read_text()
    body = next(x for x in src.splitlines() if "FUNCTION Graph_KG.CY_RETYPE_MAP(" in x).split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert 'ElseIf $Match(v, "-{0,1}(0|[1-9][0-9]*)([.][0-9]+){0,1}") {' in body
    assert "[eE]" not in body and "?" not in body and "'" not in body
