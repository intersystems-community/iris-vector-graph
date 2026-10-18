"""sum() over values of unknown type: a whole-number result is a Cypher INTEGER, as the 5.2.1 driver returned it.

`intersystems-irispython` 5.4.0 returns a SUM over the VARCHAR value column as a float (15.0 for 15); 5.2.1 returned an int.
IRIS numbers are canonical decimals, so the server cannot tell 15 from 15.0 either; the rule the TCK passed under (5.2.1) is:
whole -> int, otherwise float. The translator marks such columns (`sum_expr_columns`), the engine applies the rule there only.
"""

from types import SimpleNamespace

from iris_vector_graph._engine.query import _decode_numeric_expr_columns
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _q(text):
    return translate_to_sql(parse_query(text), {})


def test_a_sum_over_a_property_is_marked():
    sq = _q("MATCH (n) RETURN sum(n.x) AS total")
    assert "total" in [c.lower() for c in sq.sum_expr_columns]


def test_a_sum_carried_through_with_is_marked_where_it_is_returned():
    sq = _q("MATCH (n) WITH sum(n.x) AS s RETURN s")
    assert "s" in [c.lower() for c in sq.sum_expr_columns]


def test_a_sum_of_a_known_float_is_not_marked():
    sq = _q("MATCH (n) RETURN sum(toFloat(n.x)) AS total")
    assert "total" not in [c.lower() for c in sq.sum_expr_columns]


def test_the_engine_turns_whole_floats_into_ints_only_in_marked_columns():
    sq = SimpleNamespace(int_expr_columns=[], float_expr_columns=[], float_list_expr_columns=[], sum_expr_columns=["total"])
    result = SimpleNamespace(columns=["total", "other"], rows=[[15.0, 15.0], [1.5, 2.0], [None, None]])
    _decode_numeric_expr_columns(result, sq)
    assert result.rows == [[15, 15.0], [1.5, 2.0], [None, None]]
    assert isinstance(result.rows[0][0], int) and isinstance(result.rows[0][1], float)


def test_a_sum_over_a_property_is_not_an_int_column():
    # int_expr_columns rounds (2.25 -> 2); a property sum must not land there
    sq = _q("MATCH (n) RETURN sum(n.x) AS total")
    assert "total" not in [c.lower() for c in sq.int_expr_columns]
