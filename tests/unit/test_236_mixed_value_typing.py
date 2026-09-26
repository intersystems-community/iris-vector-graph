"""Typed decoding of RETURN columns holding a Cypher-heterogeneous value
(spec 236, TCK "typed" scoring).

`UNWIND [n, r, p, 1.5, ['list'], 'text', null, false, 0.0 / 0.0, {a: 'map'}]
AS types` (and `types[i]` subscript access, and `max()`/`min()` over such a
variable) keeps each element's type in its own text -- 'true'/'false' for a
boolean, 'NaN' for a float division-by-zero, a bare numeric literal's text
for a number (see `_mixed_graph_list_sql` / `CY_EXP_ORDKEY` in the
translator) -- because the column itself can hold any Cypher type across
rows and IRIS has no type tag to carry alongside a VARCHAR value. Node /
relationship / path / list / map JSON text, a real string and null already
decode correctly downstream (`tests/tck/capture.py`'s `tag_maybe_json`, the
live TCK matcher); only the bare bool/float/int spellings were still read
back as plain text (ReturnOrderBy1 [11]/[12], WithOrderBy1 [22],
Comparison2 [3], Aggregation2 [11] `max()`/`min()` over mixed values).

The translator records the aliases of such RETURN items
(`SQLQuery.mixed_expr_columns`, mirroring `bool_expr_columns` from spec 203)
and the engine decodes each row's raw text to its real Python type.

A separate case: `MATCH (a) WITH a, a.bool AS bool WITH a, bool RETURN a,
bool` (WithOrderBy1 [23]/[24]) -- `bool` is a plain stored property, not a
mixed value, but by the time it reaches RETURN it is a bare `Variable`, not
the `PropertyReference` `_bool_text_columns` (index-based) matches. The
translator tracks such WITH aliases by name (`SQLQuery.prop_text_expr_columns`,
mirroring `bool_text_columns`) so `parse_prop_text`'s safe 'true'/'false'-only
decode still reaches them.
"""

from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import _decode_bool_text_columns, _decode_mixed_expr_columns, _mixed_scalar_value
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


def mixed_tagged(cypher: str, params: dict | None = None):
    return {c.strip('"').lower() for c in tr(cypher, params).mixed_expr_columns}


def prop_text_tagged(cypher: str, params: dict | None = None):
    return {c.strip('"').lower() for c in tr(cypher, params).prop_text_expr_columns}


class TestMixedUnwindColumnTagged:
    def test_return_of_mixed_unwind_alias_tagged(self):
        # ReturnOrderBy1 [11]/[12]
        assert mixed_tagged(
            "MATCH p = (n:N)-[r:REL]->() "
            "UNWIND [n, r, p, 1.5, ['list'], 'text', null, false, 0.0 / 0.0, {a: 'map'}] AS types "
            "RETURN types ORDER BY types"
        ) == {"types"}

    def test_with_passthrough_of_mixed_alias_tagged(self):
        # WithOrderBy1 [21]/[22]
        assert mixed_tagged(
            "MATCH p = (n:N)-[r:REL]->() "
            "UNWIND [n, r, p, 1.5, ['list'], 'text', null, false, 0.0 / 0.0, {a: 'map'}] AS types "
            "WITH types ORDER BY types DESC LIMIT 5 RETURN types"
        ) == {"types"}

    def test_pure_scalar_list_not_tagged(self):
        # A homogeneous / non-graph-mixed UNWIND (`_use_union` path) never
        # reaches CY_SORT_KEY/CY_EXP_ORDKEY text: no mixed-decode needed.
        assert mixed_tagged("UNWIND [1, 2, 3] AS x RETURN x") == set()


class TestMixedSubscriptColumnTagged:
    def test_subscript_of_mixed_list_var_tagged(self):
        # Comparison2 [3]
        assert mixed_tagged(
            "MATCH p = (n)-[r]->() "
            "WITH [n, r, p, '', 1, 3.14, true, null, [], {}] AS types "
            "WITH types[0] AS lhs, types[1] AS rhs RETURN lhs, rhs"
        ) == {"lhs", "rhs"}


class TestMixedMinMaxTagged:
    def test_max_over_mixed_values_tagged(self):
        # Aggregation2 [11]
        assert mixed_tagged("UNWIND [1, 'a', null, [1, 2], 0.2, 'b'] AS x RETURN max(x)") == {
            "max_x_"
        }

    def test_max_over_homogeneous_values_not_tagged(self):
        assert mixed_tagged("UNWIND [1, 2, 3] AS x RETURN max(x)") == set()


class TestPropertyThroughWithChainTagged:
    def test_bool_property_through_double_with_tagged(self):
        # WithOrderBy1 [23]/[24]
        assert prop_text_tagged(
            "MATCH (a) WITH a, a.bool AS bool WITH a, bool ORDER BY bool RETURN a, bool"
        ) == {"bool"}

    def test_direct_property_reference_also_tagged(self):
        # Belt-and-suspenders: the direct case is already covered by the
        # index-based bool_text_columns, but tracking it by name too is safe
        # (parse_prop_text is idempotent on an already-decoded bool).
        assert prop_text_tagged("MATCH (a) RETURN a.bool AS bool") == {"bool"}

    def test_unrelated_with_alias_not_tagged(self):
        assert prop_text_tagged("MATCH (a) WITH a, 1 AS x RETURN a, x") == set()


class TestMixedScalarValueDecode:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True),
            ("false", False),
            ("1.5", 1.5),
            ("5", 5),
            ("-11", -11),
            ("text", "text"),  # plain string passes through unchanged
        ],
    )
    def test_scalar_spellings(self, raw, expected):
        assert _mixed_scalar_value(raw) == expected
        if isinstance(expected, bool):
            assert isinstance(_mixed_scalar_value(raw), bool)
        elif isinstance(expected, int) and not isinstance(expected, bool):
            assert isinstance(_mixed_scalar_value(raw), (int, float))

    def test_nan_becomes_float_nan(self):
        import math

        assert math.isnan(_mixed_scalar_value("NaN"))

    def test_null_passes_through(self):
        assert _mixed_scalar_value(None) is None

    def test_json_shaped_text_untouched(self):
        assert _mixed_scalar_value("['list']") == "['list']"
        assert _mixed_scalar_value('{"a": "map"}') == '{"a": "map"}'

    def test_non_string_untouched(self):
        assert _mixed_scalar_value(1) == 1
        assert _mixed_scalar_value(1.5) == 1.5
        assert _mixed_scalar_value(True) is True


class TestEngineDecodesMixedColumns:
    def _decode(self, rows, mixed_cols=("a",), cols=("a", "k")):
        sq = SimpleNamespace(mixed_expr_columns=list(mixed_cols))
        res = SimpleNamespace(columns=list(cols), rows=rows)
        _decode_mixed_expr_columns(res, sq)
        return res.rows

    def test_bool_text_decoded(self):
        rows = self._decode([("true", "x")])
        assert rows == [[True, "x"]]
        assert rows[0][0] is True

    def test_numeric_text_decoded(self):
        rows = self._decode([("1.5", "x")])
        assert rows[0][0] == 1.5
        assert isinstance(rows[0][0], float)

    def test_string_untouched(self):
        assert self._decode([("hello", "x")]) == [["hello", "x"]]

    def test_null_untouched(self):
        assert self._decode([(None, "x")]) == [[None, "x"]]

    def test_no_columns_configured_no_op(self):
        sq = SimpleNamespace(mixed_expr_columns=[])
        res = SimpleNamespace(columns=["a"], rows=[("true",)])
        _decode_mixed_expr_columns(res, sq)
        assert res.rows == [("true",)]


class TestEngineDecodesPropTextColumns:
    def _decode(self, rows, prop_cols=("bool",), cols=("bool", "k")):
        sq = SimpleNamespace(bool_text_columns=[], bool_expr_columns=[], prop_text_expr_columns=list(prop_cols), return_arity=len(cols))
        res = SimpleNamespace(columns=list(cols), rows=rows)
        _decode_bool_text_columns(res, sq)
        return res.rows

    def test_true_false_decoded(self):
        assert self._decode([("true", "x")]) == [[True, "x"]]
        assert self._decode([("false", "x")]) == [[False, "x"]]

    def test_other_text_untouched(self):
        # Safety property: an int property that happens to read "1" is not
        # misread as a boolean -- only the exact 'true'/'false' spellings are.
        assert self._decode([("1", "x")]) == [["1", "x"]]
        assert self._decode([("0", "x")]) == [["0", "x"]]
