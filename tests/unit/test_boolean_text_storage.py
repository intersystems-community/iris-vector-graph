"""Booleans are stored as 'true' / 'false' text (TypeConversion4 [4]).

`rdf_props.val` is a text column with no type tag. Booleans used to land there
as '1' / '0' (the DB-API binds a Python bool as an integer), which made
`toString(n.flag)` return '1'. New writes spell them 'true' / 'false'; equality
against a boolean still matches the legacy '1' / '0' (and the 'True' / 'False'
that `str(bool)` wrote from the Python API), so old rows keep filtering.
"""

import json
import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.prop_values import (
    FALSE_SPELLINGS,
    TRUE_SPELLINGS,
    parse_prop_text,
    prop_text,
)

TRUE_IN = "IN ('true', '1', 'True')"
FALSE_IN = "IN ('false', '0', 'False')"


def tr(cypher: str, params: dict | None = None):
    return translate_to_sql(parse_query(cypher), params or {})


def _stmts(t):
    return t.sql if isinstance(t.sql, list) else [t.sql]


def _prop_write_params(t):
    """Every parameter bound into a statement that writes rdf_props."""
    out = []
    for s, p in zip(_stmts(t), t.parameters):
        if re.search(r"(INSERT INTO|UPDATE)\s+\S*rdf_props\b", s):
            out.extend(p)
    return out


class TestPropText:
    def test_bool_spelled_as_json_text(self):
        assert prop_text(True) == "true"
        assert prop_text(False) == "false"

    def test_other_values_unchanged(self):
        assert prop_text(5) == "5"
        assert prop_text(1.5) == "1.5"
        assert prop_text("x") == "x"
        assert prop_text({"a": True}) == json.dumps({"a": True})
        assert prop_text([1, False]) == json.dumps([1, False])

    def test_parse_reads_new_spelling_only(self):
        assert parse_prop_text("true") is True
        assert parse_prop_text("false") is False
        # Legacy '1' is indistinguishable from the integer 1; it stays text.
        assert parse_prop_text("1") == "1"
        assert parse_prop_text("True") == "True"

    def test_spellings_include_legacy(self):
        assert TRUE_SPELLINGS == ("true", "1", "True")
        assert FALSE_SPELLINGS == ("false", "0", "False")


class TestCypherWritersEmitText:
    @pytest.mark.parametrize(
        "q,params",
        [
            ("CREATE (:M {b: true, c: false})", None),
            ("MATCH (n:M) SET n.b = true, n.c = false", None),
            ("MATCH (n:M) SET n += {b: true, c: false}", None),
            ("MATCH (n:M) SET n = {b: true, c: false}", None),
            ("MATCH (n:M) SET n.b = $t, n.c = $f", {"t": True, "f": False}),
            ("MERGE (n:M {k: 1}) ON CREATE SET n.b = true ON MATCH SET n.c = false", None),
            ("CREATE (:M {b: $t})", {"t": True}),
        ],
    )
    def test_no_python_bool_bound_into_rdf_props(self, q, params):
        t = tr(q, params)
        vals = _prop_write_params(t)
        assert not any(isinstance(v, bool) for v in vals), vals
        assert "true" in vals or "false" in vals, vals

    def test_merge_node_property_writes_text(self):
        t = tr("MERGE (n:M {b: true})")
        vals = _prop_write_params(t)
        assert "true" in vals and not any(isinstance(v, bool) for v in vals)

    def test_computed_boolean_set_stored_as_text(self):
        t = tr("MATCH (n:M) SET n.c = (n.a = 1)")
        upd = [s for s in _stmts(t) if "rdf_props" in s and ("UPDATE" in s or "INSERT" in s)]
        assert upd and all("'true'" in s and "'false'" in s for s in upd), upd

    def test_edge_qualifier_bool_is_text(self):
        t = tr("CREATE ()-[:R {b: true, c: false}]->()")
        quals = [v for p in t.parameters for v in p if isinstance(v, str) and v.startswith("{")]
        assert quals and json.loads(quals[0]) == {"b": "true", "c": "false"}, quals

    def test_unwind_map_bool_stored_as_text(self):
        t = tr("UNWIND [{a: true}, {a: false}] AS r CREATE (:X {a: r.a})")
        vals = _prop_write_params(t)
        assert not any(isinstance(v, bool) for v in vals), vals


class TestBooleanEqualityMatchesLegacy:
    @pytest.mark.parametrize(
        "where,frag",
        [
            ("n.b = true", TRUE_IN),
            ("true = n.b", TRUE_IN),
            ("n.b = false", FALSE_IN),
            ("n.b <> false", f"NOT {FALSE_IN}"),
            ("n.b <> true", f"NOT {TRUE_IN}"),
            ("n.b", TRUE_IN),
        ],
    )
    def test_where_on_stored_property(self, where, frag):
        sql = tr(f"MATCH (n:M) WHERE {where} RETURN n.k").sql
        assert frag in sql, sql

    def test_where_bool_parameter(self):
        t = tr("MATCH (n:M) WHERE n.b = $p RETURN n.k", {"p": True})
        assert TRUE_IN in t.sql, t.sql
        assert True not in [v for v in t.parameters[0] if isinstance(v, bool)]

    def test_in_list_with_booleans(self):
        sql = tr("MATCH (n:M) WHERE n.b IN [true] RETURN n.k").sql
        assert "'true'" in sql and "'1'" in sql, sql

    def test_node_pattern_property(self):
        sql = tr("MATCH (n:M {b: true}) RETURN n.k").sql
        assert TRUE_IN in sql, sql

    def test_relationship_property(self):
        sql = tr("MATCH ()-[r:R {b: true}]->() RETURN r.b").sql
        assert TRUE_IN in sql, sql

    def test_merge_guard_matches_legacy(self):
        t = tr("MERGE (n:M {b: true})")
        guard = _stmts(t)[0]
        assert TRUE_IN in guard, guard
        assert "True" not in [v for v in t.parameters[0] if isinstance(v, str)]


class TestReadersTreatTextAsBoolean:
    def test_bolt_node_props_decode_booleans(self):
        from iris_vector_graph.bolt_server import BoltSession

        raw = json.dumps(
            [
                json.dumps({"key": "b", "value": "true"}),
                json.dumps({"key": "c", "value": "false"}),
                json.dumps({"key": "s", "value": "x"}),
            ]
        )
        props = BoltSession._parse_props_field(None, raw)
        assert props == {"b": True, "c": False, "s": "x"}

    def test_return_of_stored_property_is_tagged(self):
        t = tr("MATCH (n:M) RETURN n.b AS b, 'true' AS s, n.k + 1 AS k")
        assert t.bool_text_columns == [0]

    def test_tostring_of_stored_property_not_tagged(self):
        t = tr("MATCH (n:M) RETURN toString(n.b) AS s")
        assert t.bool_text_columns == []

    def test_return_arity_recorded(self):
        t = tr("MATCH (n:M) RETURN n.b AS b, n.k AS k")
        assert t.return_arity == 2

    def test_engine_decodes_tagged_columns(self):
        from types import SimpleNamespace

        from iris_vector_graph._engine.query import _decode_bool_text_columns

        sq = SimpleNamespace(bool_text_columns=[0], return_arity=2)
        res = SimpleNamespace(columns=["b", "s"], rows=[("true", "true"), ("false", "x"), (1, None)])
        _decode_bool_text_columns(res, sq)
        assert res.rows == [[True, "true"], [False, "x"], [1, None]]

    def test_engine_skips_when_column_count_differs(self):
        from types import SimpleNamespace

        from iris_vector_graph._engine.query import _decode_bool_text_columns

        sq = SimpleNamespace(bool_text_columns=[0], return_arity=2)
        res = SimpleNamespace(columns=["b"], rows=[("true",)])
        _decode_bool_text_columns(res, sq)
        assert res.rows == [("true",)]


class TestBooleanExpressionColumns:
    """A comparison or label test that IRIS types as VARCHAR comes back as '1'/'0';
    WithOrderBy1 [45] (list = after ORDER BY + collect) and Graph5 [1]-[3] (a:B)."""

    def test_comparison_and_label_items_are_tagged_by_alias(self):
        t = tr("MATCH (a) RETURN a, a:B AS l, a.k = 1 AS e, a.k AS k, NOT a.f AS n")
        assert t.bool_expr_columns == ["l", "e", "n"]

    def test_unaliased_items_use_generated_alias(self):
        t = tr("MATCH (a) RETURN a, a:B")
        assert t.bool_expr_columns == list(t.column_name_map)

    def test_non_boolean_items_not_tagged(self):
        t = tr("MATCH (a) RETURN a.k + 1 AS k, toString(a.k = 1) AS s")
        assert t.bool_expr_columns == []

    def test_engine_decodes_by_name_when_node_expands_columns(self):
        from types import SimpleNamespace

        from iris_vector_graph._engine.query import _decode_bool_text_columns

        sq = SimpleNamespace(bool_text_columns=[], bool_expr_columns=['"result"'], return_arity=2)
        res = SimpleNamespace(
            columns=["a_id", "a_labels", "a_props", "result"],
            rows=[("1", "[]", "[]", "1"), ("0", "[]", "[]", "0"), ("x", "[]", "[]", 1), ("y", "[]", "[]", None)],
        )
        _decode_bool_text_columns(res, sq)
        assert [r[0] for r in res.rows] == ["1", "0", "x", "y"]
        assert [r[3] for r in res.rows] == [True, False, 1, None]


class TestHarnessIsStrict:
    def test_legacy_text_no_longer_equals_boolean(self):
        from tests.tck.steps.comparison import normalise_iris_value

        assert normalise_iris_value("1", True) != True  # noqa: E712
        assert normalise_iris_value("0", False) != False  # noqa: E712
        assert normalise_iris_value("true", True) is True
        assert normalise_iris_value("false", False) is False
