"""4.6.0 R050 W2: ORDER BY over labels(n)[0] and head(labels(n)) sorts on the first label's value.

The translation was right (the rows came back unsorted because the sort key read "Person" as a duration, which
tests/e2e/test_460_r050_sort_key_p_strings_e2e.py answers live); this holds that both forms reach the sort key
as the first element of the node's label list, in the direction asked.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

LABELS = "(SELECT JSON_ARRAYAGG(label) FROM Graph_KG.rdf_labels WHERE s = n0.node_id)"
#: the first element of that list: labels(n)[0] reads it through JSON_TABLE, head() through JSON_ARRAYGET
FIRST = {"labels(n)[0]": "'$[0]' COLUMNS (elem VARCHAR(1000) PATH '$')) __jt), 'x'))",
         "head(labels(n))": "CAST('[]' AS VARCHAR(256))), 0), 'x'))"}


def _order_by(query: str) -> str:
    sql = translate_to_sql(parse_query(query)).sql
    return sql[sql.rindex("ORDER BY"):]


@pytest.mark.parametrize("expr", ["labels(n)[0]", "head(labels(n))"])
@pytest.mark.parametrize("direction", ["", " DESC"])
def test_order_by_the_first_label_sorts_on_it(expr, direction):
    order_by = _order_by(f"MATCH (n) RETURN n.node_id AS id ORDER BY {expr}{direction}")
    assert order_by.startswith("ORDER BY %EXACT(Graph_KG.CY_SORT_KEY(") and LABELS in order_by
    assert order_by.endswith(FIRST[expr] + (" DESC" if direction else " ASC"))


@pytest.mark.parametrize("expr", ["labels(n)[0]", "head(labels(n))"])
def test_order_by_an_alias_of_the_first_label_sorts_on_it(expr):
    order_by = _order_by(f"MATCH (n) RETURN {expr} AS l, count(*) AS c ORDER BY l")
    assert LABELS in order_by and order_by.endswith(FIRST[expr] + " ASC")
