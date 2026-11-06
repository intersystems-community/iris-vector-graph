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
