"""Static typing of RETURN/WITH columns that read a map field (spec 229 typed
TCK, "int-got-str"): Map1 [1]/[3], Return4 [11], With4 [6]/[7].

Static map field access (`m.existing`, not a node/relationship property) is
translated as ``SQLUser.JSON_VALUE(base, '$.field')`` (``_expr_property_access``).
That UDF is declared ``RETURNS VARCHAR`` and ends `Quit val _ ""`, which forces
any JSON number or boolean it reads into SQL text — unlike a plain stored
property (``rdf_props.val``), whose canonical-numeric text is retyped to a
Python int/float by the driver itself. So `.field` access on a map always
comes back as ``str`` (`"42"` for the number `42`), whatever the underlying
JSON held.

The fix mirrors ``bool_expr_columns`` / ``int_expr_columns``: the translator
tags the SQL aliases of RETURN items (and WITH-carried variables) that are a
static map field access (``SQLQuery.map_text_columns``), and the engine
re-decodes those columns the same way ``CY_PROPS_MAP`` types values going in
(``prop_values`` spellings for bool, numeric text for int/float, else text
unchanged) via ``_decode_map_text_columns``.
"""

from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import _decode_map_text_columns
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


def map_text_tagged(cypher: str, params: dict | None = None) -> set:
    return {c.strip('"').lower() for c in tr(cypher, params).map_text_columns}


class TestMapFieldAccessTagged:
    def test_static_field_access_on_a_map_literal(self):
        # Map1 [1]
        cols = map_text_tagged(
            "WITH {existing: 42, notMissing: null} AS m "
            "RETURN m.missing, m.notMissing, m.existing"
        )
        assert cols == {"m_missing", "m_notmissing", "m_existing"}

    def test_static_field_access_via_an_explicit_alias(self):
        # Return4 [11] shape: a direct RETURN item `latestLike.likeTime AS likeTime`
        cols = map_text_tagged(
            "WITH {likeTime: 20160614} AS latestLike "
            "RETURN latestLike.likeTime AS likeTime"
        )
        assert cols == {"liketime"}

    def test_field_access_on_a_subscripted_list_element(self):
        # Map1 [3]: (list[1]).existing
        cols = map_text_tagged(
            "WITH [123, {existing: 42, notMissing: null}] AS list "
            "RETURN (list[1]).missing, (list[1]).notMissing, (list[1]).existing"
        )
        assert cols == {"_list_1___missing", "_list_1___notmissing", "_list_1___existing"}

    def test_plain_property_reference_is_not_tagged(self):
        # `n.level` reads rdf_props.val directly: no JSON_VALUE, no tagging needed.
        assert map_text_tagged("MATCH (n) RETURN n.level") == set()

    def test_a_boolean_or_numeric_returnitem_is_not_tagged(self):
        assert map_text_tagged("RETURN 1 AS a") == set()
        assert map_text_tagged("RETURN true AS a") == set()


class TestMapFieldAccessTaggingFlowsThroughWith:
    def test_with_carried_map_field_access_is_tagged_at_final_return(self):
        # With4 [6]: the field access happens in a WITH item; the final RETURN
        # is a bare variable reference to that WITH-computed column.
        cols = map_text_tagged(
            "WITH {likeTime: 20160614} AS latestLike "
            "WITH latestLike.likeTime AS likeTime "
            "RETURN likeTime"
        )
        assert cols == {"liketime"}

    def test_reassigned_map_variable_field_access_is_tagged(self):
        # With4 [7]: `m` is reused as a map name shadowing an earlier binding.
        cols = map_text_tagged(
            "WITH {first: 0} AS m "
            "WITH {second: m.first} AS m "
            "RETURN m.second"
        )
        assert cols == {"m_second"}

    def test_a_non_map_with_alias_is_not_tagged_downstream(self):
        cols = map_text_tagged("WITH 1 AS x RETURN x")
        assert cols == set()


class TestEngineDecodesMapTextColumns:
    def _decode(self, rows, cols=("a", "k"), map_text_columns=("a",)):
        sq = SimpleNamespace(map_text_columns=list(map_text_columns))
        res = SimpleNamespace(columns=list(cols), rows=rows)
        _decode_map_text_columns(res, sq)
        return res.rows

    def test_integer_text_becomes_int(self):
        rows = self._decode([("42", "x")])
        assert rows == [[42, "x"]]
        assert isinstance(rows[0][0], int)

    def test_negative_integer_text_becomes_int(self):
        assert self._decode([("-7", "x")]) == [[-7, "x"]]

    def test_float_text_becomes_float(self):
        rows = self._decode([("0.55", "x")])
        assert rows == [[0.55, "x"]]
        assert isinstance(rows[0][0], float)

    def test_true_false_text_becomes_bool(self):
        rows = self._decode([("true", "x"), ("false", "x")])
        assert rows[0][0] is True
        assert rows[1][0] is False

    def test_null_stays_null(self):
        assert self._decode([(None, None)]) == [[None, None]]

    def test_plain_string_is_unchanged(self):
        assert self._decode([("Mats", "x")]) == [["Mats", "x"]]

    def test_untagged_column_is_untouched(self):
        rows = self._decode([("42", "42")], map_text_columns=())
        assert [list(r) for r in rows] == [["42", "42"]]

    def test_only_tagged_column_is_decoded(self):
        rows = self._decode([("42", "42")])
        assert rows == [[42, "42"]]

    def test_decimal_driver_value_passes_through_prefix_value_rules(self):
        from decimal import Decimal

        rows = self._decode([(Decimal("9001"), "x")])
        assert rows == [[9001, "x"]]
        assert isinstance(rows[0][0], int)
