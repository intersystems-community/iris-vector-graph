"""4.6.0 R307 (a): a list predicate in RETURN is read back as a boolean.

`RETURN n.id, any(x IN [1, 2] WHERE x = 1)` answered '1' (text) and the item alone 1: the item had no SQL alias
(`_expr_to_cypher_text` gives '' for a list predicate), so it was not in `bool_expr_columns`, which the engine reads
back as True / False. openCypher answers true. Found by fixes50; the live check is
tests/e2e/test_460_r307_value_matrix_e2e.py::test_any_answers_a_boolean_alone_and_beside_a_column.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


@pytest.mark.parametrize(
    "query,flags",
    [
        ("MATCH (n:L) RETURN any(x IN [1, 2] WHERE x = 1)", 1),
        ("MATCH (n:L) RETURN n.id, any(x IN [1, 2] WHERE x = 1)", 1),
        ("MATCH (n {node_id: 'a'}) RETURN n.id, all(x IN [1, 2] WHERE x > 0), none(x IN [1] WHERE x > 5), "
         "single(x IN [1, 2] WHERE x = 2)", 3),
        ("MATCH (n:L) RETURN n.id, any(e IN [n.p] WHERE e IS NOT NULL)", 1),
    ],
)
def test_a_list_predicate_column_is_boolean(query, flags):
    sq = translate_to_sql(parse_query(query))
    assert len(sq.bool_expr_columns) == flags, (sq.bool_expr_columns, sq.sql)


def test_its_column_is_named_as_written():
    sq = translate_to_sql(parse_query("MATCH (n:L) RETURN n.id, any(x IN [1, 2] WHERE x = 1)"))
    (alias,) = sq.bool_expr_columns
    assert sq.column_name_map.get(alias.strip('"')) == "any(x IN [1, 2] WHERE x = 1)", sq.column_name_map
