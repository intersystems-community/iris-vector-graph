"""4.6.0 R308, offline: a relationship of a variable-length list compared with a number reads every stored spelling.

`MATCH (a)-[r*1..2]-(b) WHERE all(x IN r WHERE x.f = 2.0)` compared `JSON_VALUE(...'$.f')`, the text ObjectScript
writes for the JSON number (`2`), with the number 2.0 and matched no relationship, where `[r*1..2 {f: 2.0}]` and a
fixed hop's `r.f = 2.0` match (`IN ('2', '2.0')`, R139/R168). Found by tests/e2e/test_460_r308_cypher_differential_e2e.py
(seed 1 #250, the varlen_props form).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize("q, spelled", [
    ("MATCH (a:L)-[r:R*1..2]->(b) WHERE all(x IN r WHERE x.f = 2.0) RETURN a.name", "IN ('2', '2.0')"),
    ("MATCH (a:L)-[r:R*1..2]->(b) WHERE any(x IN r WHERE x.w = 2) RETURN a.name", "IN ('2', '2.0')"),
    ("MATCH (a:L)-[r:R*1..2]->(b) WHERE none(x IN r WHERE x.f <> 1.5) RETURN a.name", "NOT IN ('1.5')"),
])
def test_a_list_element_s_property_compares_with_every_spelling(q, spelled):
    sql = translate_to_sql(parse_query(q), {}).sql
    assert spelled in sql, sql
