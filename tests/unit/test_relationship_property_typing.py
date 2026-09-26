"""Relationship (qualifiers-JSON) property values come back typed like node
properties do (spec: TCK strict/typed cluster — Create2 [14],[16],[17];
Create6 [10],[11],[12]; Delete6 [12]; Graph6 [5],[6],[8]; Remove3
[17],[18],[19]; Set6 [17],[18],[19]; Return2 [4]).

A node's bare property (`n.num`) is already typed once it reaches the engine:
`rdf_props.val` is a real VARCHAR column and IRIS's driver reads back
whatever type the bound INSERT parameter was written with, so a genuine int
property never arrives as text. A relationship's bare property (`r.num`)
never gets that for free: `rdf_edges.qualifiers` packs every property into
one JSON *string* column, and `SQLUser.JSON_VALUE(qualifiers, '$.num')`
always returns SQL VARCHAR text no matter what the JSON leaf held. This
module's `parse_rel_prop_text` promotes that text back to int/float/bool,
and the translator/engine wire it up for exactly the RETURN items that read
a relationship qualifier as text.
"""

from types import SimpleNamespace

import pytest

from iris_vector_graph._engine.query import _decode_rel_prop_text_columns
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.prop_values import parse_rel_prop_text


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


class TestParseRelPropText:
    def test_canonical_int(self):
        assert parse_rel_prop_text("42") == 42
        assert isinstance(parse_rel_prop_text("42"), int)

    def test_negative_int(self):
        assert parse_rel_prop_text("-7") == -7

    def test_zero(self):
        assert parse_rel_prop_text("0") == 0

    def test_canonical_float(self):
        assert parse_rel_prop_text("1.5") == 1.5
        assert isinstance(parse_rel_prop_text("1.5"), float)

    def test_negative_float(self):
        assert parse_rel_prop_text("-1.5") == -1.5

    def test_true_false_spellings(self):
        assert parse_rel_prop_text("true") is True
        assert parse_rel_prop_text("false") is False

    def test_non_canonical_number_stays_text(self):
        # Leading zero: not a canonical Cypher/IRIS number, so it is a string.
        assert parse_rel_prop_text("007") == "007"

    def test_plain_string_unchanged(self):
        assert parse_rel_prop_text("foo") == "foo"

    def test_none_passthrough(self):
        assert parse_rel_prop_text(None) is None

    def test_already_int_passthrough(self):
        assert parse_rel_prop_text(5) == 5

    def test_already_bool_passthrough(self):
        assert parse_rel_prop_text(True) is True


class TestTranslatorTracksRelPropTextColumns:
    def test_bare_rel_property_tracked(self):
        t = tr("MATCH ()-[r]->() RETURN r.num")
        assert t.rel_prop_text_columns == [0]

    def test_bare_node_property_not_tracked(self):
        t = tr("MATCH (a) RETURN a.num")
        assert t.rel_prop_text_columns == []

    def test_multiple_rel_properties_tracked_by_index(self):
        t = tr("MATCH ()-[r]->() RETURN r.missing, r.missingToo, r.existing")
        assert t.rel_prop_text_columns == [0, 1, 2]

    def test_mixed_node_and_rel_properties(self):
        t = tr("MATCH (a)-[r]->(b) RETURN a.num, r.num, b.num")
        assert t.rel_prop_text_columns == [1]

    def test_aliased_rel_property_tracked(self):
        t = tr("MATCH ()-[r]->() RETURN r.num AS num")
        assert t.rel_prop_text_columns == [0]

    def test_rel_property_after_with_tracked(self):
        t = tr(
            "MATCH ()-[r]->() WITH r WHERE r.num % 2 = 0 RETURN r.num AS num"
        )
        assert t.rel_prop_text_columns == [0]

    def test_rel_property_carried_through_with_alias_tracked(self):
        # Delete6 [12]: the property is read once (`WITH r.num AS num`) and the
        # bare alias re-projected later — still the same JSON_VALUE text.
        t = tr(
            "MATCH ()-[r]->() WITH r, r.num AS num WITH num "
            "WHERE num % 2 = 0 RETURN num"
        )
        assert t.rel_prop_text_columns == [0]

    def test_rel_property_created_in_same_query_tracked(self):
        # A relationship created and returned in the same statement (Create2 [14]):
        # the alias never went through a MATCH, so it's registered by CREATE itself.
        t = tr("CREATE ()-[r:R {num: 42}]->() RETURN r.num AS num")
        assert t.rel_prop_text_columns == [0]

    def test_rebinding_alias_to_non_rel_prop_clears(self):
        t = tr(
            "MATCH ()-[r]->() WITH r, r.num AS num WITH 'foo' AS num RETURN num"
        )
        assert t.rel_prop_text_columns == []


class TestEngineDecodesRelPropColumns:
    def _decode(self, rows, rel_cols=(0,), cols=("num",)):
        sq = SimpleNamespace(rel_prop_text_columns=list(rel_cols), return_arity=len(cols))
        res = SimpleNamespace(columns=list(cols), rows=rows)
        _decode_rel_prop_text_columns(res, sq)
        return res.rows

    def test_numeric_text_becomes_int(self):
        rows = self._decode([("42",)])
        assert rows == [[42]]
        assert isinstance(rows[0][0], int)

    def test_bool_text_becomes_bool(self):
        rows = self._decode([("true",)])
        assert rows == [[True]]

    def test_null_stays_null(self):
        assert self._decode([(None,)]) == [[None]]

    def test_non_numeric_text_unchanged(self):
        assert self._decode([("foo",)]) == [["foo"]]

    def test_no_columns_configured_no_op(self):
        sq = SimpleNamespace(rel_prop_text_columns=[], return_arity=1)
        res = SimpleNamespace(columns=["num"], rows=[("42",)])
        _decode_rel_prop_text_columns(res, sq)
        assert res.rows == [("42",)]

    def test_column_count_mismatch_no_op(self):
        # e.g. a UNION/DISTINCT branch reshapes columns — arity guard skips decode.
        sq = SimpleNamespace(rel_prop_text_columns=[0], return_arity=2)
        res = SimpleNamespace(columns=["num"], rows=[("42",)])
        _decode_rel_prop_text_columns(res, sq)
        assert res.rows == [("42",)]
