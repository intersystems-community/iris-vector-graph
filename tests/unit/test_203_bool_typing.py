"""Static boolean typing of RETURN columns (spec 203, TCK "bool-got-int").

IRIS has no boolean SQL type: a Cypher boolean in RETURN comes back as 1 / 0
(or '1' / '0'). The translator records the aliases of RETURN items whose value
is statically boolean (SQLQuery.bool_expr_columns) and the engine turns 1 / 0
in those columns back into True / False. null stays null.

Quantifier1-11, Precedence1, List5, Comparison1, WithOrderBy3, Null1-3, ...
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import _decode_bool_text_columns
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


def tagged(cypher: str, params: dict | None = None):
    return {c.strip('"').lower() for c in tr(cypher, params).bool_expr_columns}


class TestBooleanValuedExpressionsTagged:
    @pytest.mark.parametrize(
        "expr",
        [
            "none(x IN [] WHERE true)",
            "any(x IN [1, 2] WHERE x = 2)",
            "all(x IN [1, 2] WHERE x > 0)",
            "single(x IN [1, 2] WHERE x = 2)",
            "true",
            "false",
            "true AND false",
            "true OR false",
            "true XOR false",
            "NOT true",
            "NOT true AND false",
            "1 IN [1, 2]",
            "null IS NULL",
            "1 IS NOT NULL",
            "'ab' STARTS WITH 'a'",
            "'ab' ENDS WITH 'b'",
            "'ab' CONTAINS 'b'",
            "'ab' =~ 'a.*'",
            "1 < 2 < 3",
            "1 = 1",
            "[1, 2] = 'foo'",
            "(1 < 2) = (2 < 3)",
            "none(x IN [1] WHERE x = 1) = all(x IN [1] WHERE x = 1)",
            "CASE WHEN 1 = 1 THEN true ELSE false END",
            "CASE WHEN 1 = 1 THEN 1 < 2 END",
            "CASE 1 WHEN 1 THEN true ELSE null END",
            "toBoolean('true')",
        ],
    )
    def test_return_item_tagged(self, expr):
        assert "a" in tagged(f"RETURN {expr} AS a"), expr

    def test_exists_pattern_tagged(self):
        assert "e" in tagged("MATCH (n) RETURN exists { (n)-->() } AS e")

    def test_label_and_property_comparison_tagged(self):
        assert tagged("MATCH (n) RETURN n:A AS l, n.k = 1 AS e") == {"l", "e"}

    @pytest.mark.parametrize(
        "expr",
        [
            "1",
            "'x'",
            "null",
            "toString(true)",
            "CASE WHEN 1 = 1 THEN true ELSE 1 END",
            "CASE WHEN 1 = 1 THEN 'a' END",
            "[true, false]",
            "size([true])",
        ],
    )
    def test_non_boolean_not_tagged(self, expr):
        assert "a" not in tagged(f"RETURN {expr} AS a"), expr

    def test_stored_property_not_tagged(self):
        # Stored properties go through bool_text_columns, not the static typing.
        assert tagged("MATCH (n) RETURN n.b AS b") == set()

    def test_mixed_row_only_booleans_tagged(self):
        assert tagged("RETURN 1 AS n, true AS b, 1 < 2 AS c, 'x' AS s") == {"b", "c"}


class TestBooleanTypeFlowsThroughProjections:
    def test_with_alias_of_boolean(self):
        assert "b" in tagged("WITH 1 < 2 AS b RETURN b")

    def test_with_alias_of_boolean_renamed(self):
        assert "c" in tagged("WITH 1 < 2 AS b WITH b AS c RETURN c")

    def test_with_alias_of_boolean_literal(self):
        assert "b" in tagged("WITH true AS b RETURN b")

    def test_with_alias_used_in_expression(self):
        assert "r" in tagged("WITH true AS b RETURN b AND false AS r")

    def test_rebinding_to_non_boolean_clears(self):
        assert "b" not in tagged("WITH true AS b WITH 1 AS b RETURN b")

    def test_with_non_boolean_not_tagged(self):
        assert "b" not in tagged("WITH 1 AS b RETURN b")

    def test_unwind_boolean_list(self):
        assert "x" in tagged("UNWIND [true, false, null] AS x RETURN x")

    def test_unwind_mixed_list_not_tagged(self):
        assert "x" not in tagged("UNWIND [true, 1] AS x RETURN x")

    def test_unwind_from_with_boolean_list(self):
        assert "x" in tagged("WITH [true, false] AS l UNWIND l AS x RETURN x")

    def test_unaliased_boolean_uses_generated_alias(self):
        t = tr("RETURN 1 < 2, true")
        assert len(t.bool_expr_columns) == 2


class TestEngineDecodesBooleanColumns:
    def _decode(self, rows, cols=("a", "k")):
        sq = SimpleNamespace(bool_text_columns=[], bool_expr_columns=["a"], return_arity=2)
        res = SimpleNamespace(columns=list(cols), rows=rows)
        _decode_bool_text_columns(res, sq)
        return res.rows

    def test_integer_one_zero(self):
        rows = self._decode([(1, 1), (0, 0)])
        assert rows == [[True, 1], [False, 0]]
        assert rows[0][0] is True and rows[1][0] is False

    def test_text_one_zero(self):
        rows = self._decode([("1", "1"), ("0", "0")])
        assert rows == [[True, "1"], [False, "0"]]
        assert rows[0][0] is True and rows[1][0] is False

    def test_text_true_false(self):
        assert self._decode([("true", "x"), ("false", "x")]) == [[True, "x"], [False, "x"]]

    def test_decimal(self):
        rows = self._decode([(Decimal(1), 5), (Decimal(0), 5)])
        assert rows[0][0] is True and rows[1][0] is False

    def test_null_stays_null(self):
        assert self._decode([(None, None)]) == [[None, None]]

    def test_bool_unchanged(self):
        assert self._decode([(True, 1), (False, 0)]) == [[True, 1], [False, 0]]

    def test_other_values_unchanged(self):
        assert self._decode([(2, 2), ("x", "x")]) == [[2, 2], ["x", "x"]]


class TestUnion:
    def test_boolean_in_every_branch_tagged(self):
        assert tagged("RETURN true AS a UNION RETURN 1 < 2 AS a") == {"a"}

    def test_boolean_in_one_branch_only_not_tagged(self):
        assert tagged("RETURN true AS a UNION ALL RETURN 1 AS a") == set()
