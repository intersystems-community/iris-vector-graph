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
def test_size_of_a_string_is_char_length(q):
    sql = translate_to_sql(parse_query(q), {}).sql
    assert not re.search(r"(?<![A-Z_])LENGTH\(", sql), sql


def test_properties_map_reads_only_canonical_number_text():
    """4.6.0 R307: properties(n) (CY_PROPS_MAP) read '1.0' as 1 and '1e3' as the integer 1000 (+v): text a number only
    as JSON spells one (R121) is put in the map as written, so JSON reads 1.0 and 1e3 as floats."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "iris_vector_graph" / "schema.py").read_text()
    body = next(x for x in src.splitlines() if "FUNCTION Graph_KG.CY_PROPS_MAP(" in x).split("LANGUAGE OBJECTSCRIPT", 1)[1]
    assert '$Match(v, "-{0,1}(0|[1-9][0-9]*)([.][0-9]+){0,1}([eE][-+]{0,1}[0-9]+){0,1}") { Set j = v }' in body
    assert "+v" not in body and "?" not in body and "'" not in body
