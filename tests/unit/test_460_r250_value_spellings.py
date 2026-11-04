"""4.6.0 R250: a sum of floats and a relationship's properties keep openCypher's spelling.

- ``RETURN sum(r.weight)`` over the floats 2.0 and 3.0 answered 5, not 5.0: the SUM came back as a number with no
  type, and an untyped sum is decoded as an integer when whole (a sum of integers must read 5). The SUM column now
  carries the summed values' type: ``CY_TYPED(CASE WHEN MAX(CY_IS_FLOAT(x)) = 1 THEN CAST(SUM(x) AS DOUBLE) ELSE
  SUM(x) END)``, where ``CY_IS_FLOAT`` is 1 for a ``$DOUBLE``, a decimal with a fraction, or text spelled as a float
  (``"2.0"``, what a Cypher write stores in ``qualifiers``). One float makes the sum a float, as openCypher says.
- ``properties(r)`` spelled a stored ``"2.0"`` as 2 (``CY_RETYPE_MAP`` re-read it as a number), and answered None for an
  edge with no properties, where openCypher answers ``{}``. A float's text keeps its fraction, a JSON number keeps the
  text it was written with, and an edge with no properties answers ``{}``; a missing relationship (an OPTIONAL MATCH
  that matched none) is still null (Graph9 [3]).

No TCK scenario covers either (fixes30: Match8's ``sum(r1.times)`` sums integers, Graph9 [2] has no float).
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema


def _q(q):
    return translate_to_sql(parse_query(q), {})


def _sql(q):
    r = _q(q)
    return r.sql if isinstance(r.sql, str) else r.sql[-1]


def _routine(name):
    for s in GraphSchema.get_procedures_sql_list():
        if re.search(rf"FUNCTION\s+\w+\.{name}\(", s):
            return s
    raise AssertionError(f"no routine {name}")


def _body(name):
    return _routine(name).split("LANGUAGE OBJECTSCRIPT", 1)[1]


class TestSum:
    @pytest.mark.parametrize(
        "q",
        ["MATCH ()-[r:R]->() RETURN sum(r.w) AS s", "MATCH (n:L) RETURN sum(n.f) AS s"],
    )
    def test_a_returned_sum_of_a_property_carries_its_type(self, q):
        r = _q(q)
        assert re.search(
            r"CY_TYPED\(CASE WHEN MAX\(\w+\.CY_IS_FLOAT\((.+)\)\) = 1 THEN CAST\(SUM\(\1\) AS DOUBLE\) ELSE SUM\(\1\) END\) AS s",
            r.sql,
        ), r.sql
        assert "s" in r.typed_value_columns
        # decoded from the mark, not by the whole-means-integer rule
        assert "s" not in r.sum_expr_columns

    def test_a_grouped_sum_keeps_its_group_key(self):
        sql = _sql("MATCH (a)-[r:R]->() RETURN a.k AS k, sum(r.w) AS s")
        assert "GROUP BY" in sql and "CY_IS_FLOAT" in sql

    def test_a_sum_of_an_integer_typed_expression_is_unchanged(self):
        r = _q("MATCH (n:L) RETURN sum(1) AS s")
        assert "CY_IS_FLOAT" not in r.sql

    def test_the_sort_key_reads_the_plain_sum(self):
        sql = _sql("MATCH (a)-[r:R]->() RETURN a.k AS k, sum(r.w) AS s ORDER BY s")
        order = sql.rsplit("ORDER BY", 1)[1]
        assert "CY_TYPED" not in order

    def test_cy_is_float_is_set_up_without_apostrophe_or_question_mark(self):
        body = _body("CY_IS_FLOAT")
        assert "'" not in body and "?" not in body and not re.search(r":\d", body)
        assert "$LISTBUILD(v)" in body


class TestRelationshipProperties:
    def test_an_edge_with_no_properties_answers_an_empty_map(self):
        sql = _sql("MATCH ()-[r:R]->() RETURN properties(r) AS p")
        assert re.search(
            r"CASE WHEN (e\d+)\.p IS NULL THEN NULL ELSE COALESCE\(\w+\.CY_RETYPE_MAP\(\1\.qualifiers\), '\{\}'\) END",
            sql,
        ), sql

    def test_through_with_too(self):
        sql = _sql("MATCH ()-[r:R]->() WITH r RETURN properties(r) AS p")
        assert re.search(
            r"CASE WHEN Stage1\.__edge_r_p IS NULL THEN NULL ELSE COALESCE\(\w+\.CY_RETYPE_MAP\(Stage1\.r\), '\{\}'\) END",
            sql,
        ), sql

    def test_retype_map_spells_a_float_text_and_a_json_number_as_written(self):
        body = _body("CY_RETYPE_MAP")
        assert '"json"' in body  # a JSON number's own text (2.0 stays 2.0)
        assert "CYFLOATTEXT" in body  # number-shaped text with a fraction is spelled as a float
        assert "'" not in body and "?" not in body and not re.search(r":\d", body)
