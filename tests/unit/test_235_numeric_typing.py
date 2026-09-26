"""Static INTEGER / FLOAT typing of RETURN columns (spec 235, TCK exact-compare
"int-got-float", "float-got-int", "int-value").

IRIS has no distinct SQL type that survives arithmetic the way Cypher's
INTEGER / FLOAT do: FLOOR()/MOD() come back as DOUBLE or Decimal even for
Cypher expressions that are statically INTEGER (`count(n) / 60`, `a.num %
a.num2`), and some FLOAT-valued functions (avg(), percentileCont/Disc()) can
come back as an IRIS integer when the underlying values happen to be whole
numbers. The translator records the aliases of RETURN items whose value is
statically INTEGER / FLOAT (SQLQuery.int_expr_columns / float_expr_columns,
mirroring bool_expr_columns from spec 203) and the engine casts the driver
value in those columns to the matching Python type. A property of unknown
type is left untouched.

Aggregation6, Mathematical2, Precedence2, Return2, Return6, WithOrderBy4.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import _decode_numeric_expr_columns
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


def int_tagged(cypher: str, params: dict | None = None):
    return {c.strip('"').lower() for c in tr(cypher, params).int_expr_columns}


def float_tagged(cypher: str, params: dict | None = None):
    return {c.strip('"').lower() for c in tr(cypher, params).float_expr_columns}


class TestIntegerValuedExpressionsTagged:
    @pytest.mark.parametrize(
        "expr",
        [
            "1",
            "1 + 2",
            "4 / 2",
            "4 / 2 + 3 % 2",
            "4 / (2 + 3) % 2",
            "3 * 4 - 2",
        ],
    )
    def test_return_item_tagged(self, expr):
        assert "a" in int_tagged(f"RETURN {expr} AS a"), expr

    def test_count_tagged(self):
        assert int_tagged("MATCH (n) RETURN count(n) AS c") == {"c"}

    def test_count_division_chain_tagged(self):
        assert int_tagged("MATCH (n) RETURN count(n) / 60 / 60 AS c") == {"c"}

    def test_sum_over_property_tagged(self):
        assert int_tagged("MATCH (a) RETURN sum(a.num) AS s") == {"s"}

    def test_property_plus_int_literal_tagged(self):
        assert int_tagged("MATCH (a) RETURN a.version + 5 AS foo") == {"foo"}

    def test_property_mod_property_tagged(self):
        assert int_tagged("MATCH (a) RETURN a.num % a.num2 AS mod") == {"mod"}

    def test_property_plus_property_tagged(self):
        assert int_tagged("MATCH (a) RETURN a.num + a.num2 AS sum") == {"sum"}

    def test_tointeger_tagged(self):
        assert int_tagged("RETURN toInteger(3.9) AS a") == {"a"}


class TestFloatValuedExpressionsTagged:
    @pytest.mark.parametrize(
        "expr",
        [
            "1.0",
            "1 ^ 2",
            "toFloat(1)",
        ],
    )
    def test_return_item_tagged(self, expr):
        assert "a" in float_tagged(f"RETURN {expr} AS a"), expr

    def test_avg_tagged(self):
        assert float_tagged("MATCH (n) RETURN avg(n.k) AS a") == {"a"}

    def test_avg_of_int_function_tagged(self):
        # avg() is always Float-valued even when its argument is Integer-valued.
        assert float_tagged(
            "MATCH p=(a)-[*]->(b) RETURN avg(length(p)) AS a"
        ) == {"a"}

    def test_percentile_cont_tagged(self):
        assert float_tagged(
            "MATCH (n) RETURN percentileCont(n.price, 0.5) AS p"
        ) == {"p"}

    def test_percentile_disc_tagged(self):
        assert float_tagged(
            "MATCH (n) RETURN percentileDisc(n.price, 0.5) AS p"
        ) == {"p"}

    def test_mixed_int_and_float_operand_is_float(self):
        assert float_tagged("RETURN 1 + 2.0 AS a") == {"a"}
        assert float_tagged("MATCH (a) RETURN a.num + 2.0 AS s") == {"s"}


class TestUnknownPropertyLeftUntagged:
    def test_bare_property_not_tagged(self):
        # Bare `RETURN n.prop` goes through bool_text_columns / parse_prop_text,
        # which reads the stored text as-is — untouched by this static pass.
        assert int_tagged("MATCH (n) RETURN n.price AS p") == set()
        assert float_tagged("MATCH (n) RETURN n.price AS p") == set()

    def test_sum_of_unknown_variable_not_tagged(self):
        assert int_tagged("MATCH (n) RETURN sum(n) AS s") == set()
        assert float_tagged("MATCH (n) RETURN sum(n) AS s") == set()


class TestNumericTypeFlowsThroughWith:
    def test_with_alias_of_int_arithmetic(self):
        assert int_tagged("MATCH (a) WITH a.num + a.num2 AS sum RETURN sum") == {"sum"}

    def test_with_alias_of_count(self):
        assert int_tagged("MATCH (n) WITH count(n) AS c RETURN c") == {"c"}

    def test_rebinding_alias_updates_type(self):
        # WithOrderBy4 [7]: same alias name rebound to a different expression
        # in a later WITH keeps its (new) statically-known type.
        assert int_tagged(
            "MATCH (a) WITH a, a.num2 % 3 AS x WITH a, a.num + a.num2 AS x RETURN x"
        ) == {"x"}

    def test_multi_stage_with_preserves_earlier_alias_type(self):
        # WithOrderBy4 [8]: a variable projected in an earlier WITH keeps its
        # type after a later WITH introduces unrelated aliases.
        assert int_tagged(
            "MATCH (a) WITH a, a.num + a.num2 AS sum "
            "WITH a, a.num2 % 3 AS mod RETURN a, mod"
        ) == {"mod"}

    def test_rebinding_to_unknown_clears(self):
        assert int_tagged("WITH 1 + 2 AS x WITH 'foo' AS x RETURN x") == set()


class TestEngineDecodesNumericColumns:
    def _decode(self, rows, int_cols=("a",), float_cols=(), cols=("a", "k")):
        sq = SimpleNamespace(int_expr_columns=list(int_cols), float_expr_columns=list(float_cols))
        res = SimpleNamespace(columns=list(cols), rows=rows)
        _decode_numeric_expr_columns(res, sq)
        return res.rows

    def test_float_becomes_int(self):
        rows = self._decode([(104.0, "x")])
        assert rows == [[104, "x"]]
        assert isinstance(rows[0][0], int)

    def test_decimal_becomes_int(self):
        rows = self._decode([(Decimal("3.000000000000000000"), "x")])
        assert rows == [[3, "x"]]
        assert isinstance(rows[0][0], int)

    def test_float_noise_rounds_to_int(self):
        # A statically INTEGER expression whose IRIS DOUBLE arithmetic left a
        # tiny floating-point residue rounds to the true integer value.
        rows = self._decode([(-1.1102230246251565e-15, "x")])
        assert rows == [[0, "x"]]
        rows = self._decode([(-7.0000000000000004, "x")])
        assert rows == [[-7, "x"]]

    def test_null_stays_null(self):
        assert self._decode([(None, "x")]) == [[None, "x"]]

    def test_int_column_int_value_unchanged(self):
        assert self._decode([(2, "x")]) == [[2, "x"]]

    def test_int_becomes_float(self):
        rows = self._decode([(10, "x")], int_cols=(), float_cols=("a",))
        assert rows == [[10.0, "x"]]
        assert isinstance(rows[0][0], float)

    def test_decimal_becomes_float(self):
        rows = self._decode([(Decimal("30"), "x")], int_cols=(), float_cols=("a",))
        assert rows == [[30.0, "x"]]
        assert isinstance(rows[0][0], float)

    def test_bool_passes_through_int_column(self):
        assert self._decode([(True, "x")]) == [[True, "x"]]

    def test_no_columns_configured_no_op(self):
        sq = SimpleNamespace(int_expr_columns=[], float_expr_columns=[])
        res = SimpleNamespace(columns=["a"], rows=[(104.0,)])
        _decode_numeric_expr_columns(res, sq)
        assert res.rows == [(104.0,)]


class TestCteQualifierDroppedForOrderBy:
    """A CTE-qualified column in the outer SELECT breaks once an ORDER BY is
    added — even `ORDER BY 1` — with SQLCODE -23 "Label 'X' is not listed
    among the applicable tables" (Aggregation6 [5]: `WITH n, size([(n)-->() |
    1]) AS deg ... RETURN deg ORDER BY deg`-shaped queries). The unqualified
    column, or the same query without ORDER BY, both work."""

    def test_stage_qualifier_dropped_when_order_by_present(self):
        from iris_vector_graph.cypher.translator import TranslationContext
        from iris_vector_graph.cypher.translator import _drop_cte_select_qualifier_for_order_by

        ctx = TranslationContext()
        ctx.from_clauses = ["Stage1"]
        all_ctes = ["Stage1 AS (\nSELECT n0.node_id AS n, 5 AS deg\nFROM nodes n0\n)"]
        sql = "SELECT Stage1.deg AS deg\nFROM Stage1\nORDER BY deg ASC"
        out = _drop_cte_select_qualifier_for_order_by(sql, all_ctes, ctx)
        assert out == "SELECT deg AS deg\nFROM Stage1\nORDER BY deg ASC"

    def test_qualifier_kept_without_order_by(self):
        from iris_vector_graph.cypher.translator import TranslationContext
        from iris_vector_graph.cypher.translator import _drop_cte_select_qualifier_for_order_by

        ctx = TranslationContext()
        ctx.from_clauses = ["Stage1"]
        all_ctes = ["Stage1 AS (\nSELECT n0.node_id AS n, 5 AS deg\nFROM nodes n0\n)"]
        sql = "SELECT Stage1.deg AS deg\nFROM Stage1"
        out = _drop_cte_select_qualifier_for_order_by(sql, all_ctes, ctx)
        assert out == sql

    def test_qualifier_kept_when_join_present(self):
        # A JOIN makes an unqualified column potentially ambiguous.
        from iris_vector_graph.cypher.translator import TranslationContext
        from iris_vector_graph.cypher.translator import _drop_cte_select_qualifier_for_order_by

        ctx = TranslationContext()
        ctx.from_clauses = ["Stage1"]
        ctx.join_clauses = ["JOIN Stage2 ON Stage2.n = Stage1.n"]
        all_ctes = ["Stage1 AS (\nSELECT n0.node_id AS n, 5 AS deg\nFROM nodes n0\n)"]
        sql = "SELECT Stage1.deg AS deg\nFROM Stage1\nORDER BY deg ASC"
        out = _drop_cte_select_qualifier_for_order_by(sql, all_ctes, ctx)
        assert out == sql

    def test_qualifier_kept_when_multiple_from_sources(self):
        from iris_vector_graph.cypher.translator import TranslationContext
        from iris_vector_graph.cypher.translator import _drop_cte_select_qualifier_for_order_by

        ctx = TranslationContext()
        ctx.from_clauses = ["Stage1", "Stage2"]
        all_ctes = ["Stage1 AS (SELECT 5 AS deg)", "Stage2 AS (SELECT 6 AS deg)"]
        sql = "SELECT Stage1.deg AS deg\nFROM Stage1, Stage2\nORDER BY deg ASC"
        out = _drop_cte_select_qualifier_for_order_by(sql, all_ctes, ctx)
        assert out == sql

    def test_qualifier_kept_when_from_is_not_a_cte(self):
        # FROM a plain table alias, not one of the CTE names, is untouched
        # (the bug is specific to CTE column binding, not any alias).
        from iris_vector_graph.cypher.translator import TranslationContext
        from iris_vector_graph.cypher.translator import _drop_cte_select_qualifier_for_order_by

        ctx = TranslationContext()
        ctx.from_clauses = ["nodes n0"]
        all_ctes = ["Stage1 AS (SELECT 5 AS deg)"]
        sql = "SELECT n0.deg AS deg\nFROM nodes n0\nORDER BY deg ASC"
        out = _drop_cte_select_qualifier_for_order_by(sql, all_ctes, ctx)
        assert out == sql

    def test_end_to_end_translation_drops_qualifier(self):
        # WITH n, size([(n)-->() | 1]) AS deg ... RETURN deg ORDER BY deg:
        # the single-stage CTE column must not be qualified once ORDER BY
        # is added, or IRIS raises SQLCODE -23 at Prepare.
        sq = tr(
            "MATCH (n) WITH n, size([(n)-->() | 1]) AS deg RETURN deg ORDER BY deg"
        )
        assert "Stage1.deg" not in sq.sql
        assert "ORDER BY" in sq.sql.upper()


class TestPercentileRewritePreservesOtherColumns:
    """The single-percentile-query SQL rewrite used to discard every other
    RETURN column and the enclosing WITH-CTE preamble (Aggregation6 [5]:
    `RETURN percentileDisc(0.90, deg), deg` broke with "Label 'STAGE2' is not
    listed among the applicable tables"). It now substitutes the percentile
    call in place when there are other columns."""

    def test_other_column_and_cte_preamble_survive(self):
        sq = tr(
            "MATCH (n:S) WITH n, size([(n)-->() | 1]) AS deg WHERE deg > 2 "
            "WITH deg LIMIT 100 RETURN percentileDisc(0.90, deg), deg"
        )
        assert "IVG.Percentile_PDISC" in sq.sql
        assert "Stage2" in sq.sql  # CTE preamble preserved
        assert sq.sql.count(" AS deg") >= 1  # the plain `deg` column survives

    def test_sole_percentile_column_still_translates(self):
        # No other RETURN columns: JSON_ARRAYAGG is a genuine SQL aggregate
        # here, so it collapses the FROM to one row on its own.
        sq = tr("MATCH (n) RETURN percentileDisc(n.price, 0.5) AS p")
        assert "IVG.Percentile_PDISC" in sq.sql
        assert "GROUP BY" not in sq.sql.upper()
