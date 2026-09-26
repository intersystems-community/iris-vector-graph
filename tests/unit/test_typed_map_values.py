"""properties() and map literals return a Cypher map with typed values.

Graph9 [1]: `properties(n)` returned a key/value list (`[{"key":...,"value":...}]`)
instead of a map (`{key: value}`), per the Cypher spec.

Graph9 [2]/[3], Return4 [9]: values inside a map (`properties(r)`, or a general
value like `count(b)` or a property reference inside a `{...}` literal) came
back as text — `{"level":"9001"}` instead of `{"level":9001}` — even though a
bare property fetch (`RETURN n.level`) is already typed by the DB-API driver
(int text -> a Python int). The fix reuses that same typing rule at the point
where a dynamic value is embedded in a hand-built JSON string: `null` for NULL,
a bare `true`/`false` for the two spellings a stored boolean property uses, a
bare number for text that reads as one, and a quoted string otherwise.
"""
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import (
    _typed_json_value_sql,
    properties_map_subquery,
    properties_subquery,
    translate_to_sql,
)


def tr(q, params=None):
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


class TestPropertiesFunctionIsAMap:
    def test_calls_the_typed_map_udf(self):
        sql = properties_map_subquery("n0.node_id")
        assert "SQLUser.CY_PROPS_MAP(n0.node_id" in sql

    def test_no_longer_builds_a_key_value_list(self):
        sql = properties_map_subquery("n0.node_id")
        assert '"key"' not in sql
        assert '"value"' not in sql

    def test_properties_of_node_translates_through_the_udf(self):
        sql = tr("MATCH (p:Person) RETURN properties(p) AS m")
        assert "SQLUser.CY_PROPS_MAP(" in sql

    def test_node_value_hydration_is_unchanged(self):
        """`RETURN n`'s internal `_props` field is a different call site
        (properties_subquery, not properties_map_subquery) and keeps the
        key/value list TCK comparison's `_blob_props` already parses — only
        `properties(n)` as its own RETURN expression becomes a map."""
        sql = tr("MATCH (n) RETURN n")
        assert "JSON_ARRAYAGG" in sql
        assert "CY_PROPS_MAP" not in sql


class TestRelationshipPropertiesAreRetyped:
    def test_properties_of_relationship_wraps_qualifiers_in_retype(self):
        sql = tr("MATCH ()-[r:R]->() RETURN properties(r) AS m")
        assert "SQLUser.CY_RETYPE_MAP(" in sql
        assert ".qualifiers" in sql


class TestTypedJsonValueSql:
    class _FakeContext:
        def __init__(self):
            self.select_params = []

    def test_null_becomes_the_json_null_literal(self):
        ctx = self._FakeContext()
        expr = _typed_json_value_sql("NULL", ctx)
        assert "COALESCE" in expr
        assert "'null'" in expr

    def test_looks_numeric_and_stored_bool_stay_unquoted(self):
        ctx = self._FakeContext()
        expr = _typed_json_value_sql("p2.val", ctx)
        assert "ISNUMERIC(p2.val)" in expr
        assert "IN ('true', 'false')" in expr

    def test_duplicates_params_for_a_placeholder_value(self):
        ctx = self._FakeContext()
        ctx.select_params = ["marker"]
        expr = _typed_json_value_sql("?", ctx)
        # val_sql ("?") appears 4 times in the emitted SQL — the one param added
        # by the caller before this call must become 4 total.
        assert ctx.select_params == ["marker"] * 4
        assert expr.count("?") == 4


class TestMapLiteralDynamicValuesAreTyped:
    def test_aggregation_value_in_a_map_literal_is_not_force_quoted(self):
        sql = tr(
            "MATCH (a:A), (b:B) RETURN {name: count(b)} AS baz"
        )
        assert "ISNUMERIC(" in sql
        # the old fallback always did `:\"'||CAST(...)||'\"` — a hard-coded quote
        # pair around the value with no runtime type check.
        assert ':\\"\'||CAST(' not in sql

    def test_property_reference_value_in_a_map_literal_is_typed(self):
        sql = tr("MATCH (n) RETURN {lvl: n.level} AS m")
        assert "ISNUMERIC(" in sql
