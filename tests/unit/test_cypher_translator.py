import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def test_translate_return_node_includes_labels_and_properties():
    query = "MATCH (n) RETURN n"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "JSON_ARRAYAGG(label)" in sql
    assert "JSON_ARRAYAGG" in sql


def test_translate_labels_function():
    query = "MATCH (n) RETURN labels(n)"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "JSON_ARRAYAGG(label)" in sql


def test_translate_properties_function():
    query = "MATCH (n) RETURN properties(n)"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    # properties() returns a Cypher map (spec 203, Graph9 [1]), built by the
    # CY_PROPS_MAP UDF rather than a JSON_ARRAYAGG key/value list.
    assert "SQLUser.CY_PROPS_MAP(" in sql


def test_translate_order_by_limit():
    """Test ORDER BY and LIMIT translation. NULLS LAST removed for IRIS compatibility."""
    query = "MATCH (n) RETURN n.id ORDER BY n.created_at DESC LIMIT 10"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "ORDER BY" in sql
    assert "DESC" in sql
    # IRIS doesn't support NULLS LAST, so we don't emit it
    assert "FETCH FIRST 10 ROWS ONLY" in sql


def test_translate_numeric_comparison_cast():
    query = "MATCH (n) WHERE n.score >= 0.5 RETURN n.id"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "CAST" in sql
    assert "rdf_props" in sql
    assert 'p1."key" = ?' in sql


def test_translate_type_function():
    """Test that type(r) returns the relationship type from rdf_edges.p"""
    query = "MATCH (a)-[r]->(b) RETURN type(r)"
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    # type(r) should reference the edge alias's p column
    assert ".p AS type_r_" in sql
    assert "rdf_edges" in sql or "MatchEdges" in sql


def test_translate_type_function_in_where():
    """Test that type(r) can be used in WHERE clause"""
    query = 'MATCH (a)-[r]->(b) WHERE type(r) = "KNOWS" RETURN a'
    parsed = parse_query(query)
    sql_query = translate_to_sql(parsed)
    sql = "\n".join(sql_query.sql) if isinstance(sql_query.sql, list) else sql_query.sql

    assert "rdf_edges" in sql or "MatchEdges" in sql
    assert ".p = ?" in sql or ".p =" in sql


def test_translate_pattern_operators():
    """Test CONTAINS, STARTS WITH, ENDS WITH operators"""
    # CONTAINS
    query = "MATCH (n) WHERE n.name CONTAINS 'test' RETURN n"
    parsed = parse_query(query)
    sql = translate_to_sql(parsed)
    sql_str = "\n".join(sql.sql) if isinstance(sql.sql, list) else sql.sql
    assert "LIKE" in sql_str

    # STARTS WITH
    query2 = "MATCH (n) WHERE n.name STARTS WITH 'foo' RETURN n"
    parsed2 = parse_query(query2)
    sql2 = translate_to_sql(parsed2)
    sql_str2 = "\n".join(sql2.sql) if isinstance(sql2.sql, list) else sql2.sql
    assert "LIKE" in sql_str2

    # ENDS WITH
    query3 = "MATCH (n) WHERE n.name ENDS WITH 'bar' RETURN n"
    parsed3 = parse_query(query3)
    sql3 = translate_to_sql(parsed3)
    sql_str3 = "\n".join(sql3.sql) if isinstance(sql3.sql, list) else sql3.sql
    assert "LIKE" in sql_str3


def test_inline_property_filter_on_relationship_target():
    """Inline props on relationship target must generate WHERE conditions, not be silently dropped.

    Bug: MATCH (t)-[:R]->(c:Label {id: 'x'}) returned all nodes instead of filtering,
    because translate_relationship_pattern did not apply target_node.properties after joining.
    """
    query = "MATCH (t:IServiceTicket)-[:TICKET_FOR]->(c:Customer {id: 'Customer:Northwell'}) RETURN t.id as id"
    parsed = parse_query(query)
    sql_result = translate_to_sql(parsed)
    sql_str = "\n".join(sql_result.sql) if isinstance(sql_result.sql, list) else sql_result.sql
    # SQLQuery.parameters is a list of param-lists (one per statement); flatten to check
    params = [p for plist in sql_result.parameters for p in plist]

    # The literal appears either as a bind param or inlined in WHERE — check both
    filter_applied = (
        "Customer:Northwell" in params
        or "Customer:Northwell" in sql_str
    )
    assert filter_applied, \
        f"Inline property filter on relationship target was dropped — not in params or SQL: {params}"
    # Must use node_id equality for id/node_id keys (not rdf_props join)
    assert "node_id" in sql_str


def test_inline_property_filter_on_relationship_source():
    """Inline props on relationship source must also generate WHERE conditions."""
    query = "MATCH (t:IServiceTicket {status: 'Open'})-[:TICKET_FOR]->(c:Customer) RETURN c.id as cid"
    parsed = parse_query(query)
    sql_result = translate_to_sql(parsed)
    params = [p for plist in sql_result.parameters for p in plist]

    sql_str = "\n".join(sql_result.sql) if isinstance(sql_result.sql, list) else sql_result.sql
    filter_applied = "Open" in params or "Open" in sql_str
    assert filter_applied, \
        f"Inline property filter on relationship source was dropped — not in params or SQL: {params}"


def test_anonymous_source_node_pattern():
    """MATCH ()-[r]->() must not raise KeyError for anonymous source node."""
    sql = translate_to_sql(parse_query("MATCH ()-[r]->() RETURN count(r) AS c"))
    assert "rdf_edges" in sql.sql or "MatchEdges" in sql.sql
    assert "COUNT" in sql.sql.upper()


def test_anonymous_target_node_pattern():
    """MATCH (a:Gene)-[]->() must not raise KeyError for anonymous target."""
    sql = translate_to_sql(parse_query("MATCH (a:Gene)-[]->() RETURN a.id LIMIT 3"))
    assert "rdf_edges" in sql.sql or "MatchEdges" in sql.sql


def test_anonymous_both_nodes_pattern():
    """MATCH ()-[r]->(b) works without crashing."""
    sql = translate_to_sql(parse_query("MATCH ()-[r]->(b) RETURN b.id LIMIT 3"))
    assert "rdf_edges" in sql.sql or "MatchEdges" in sql.sql


def test_in_list_param_expands_placeholders():
    """WHERE a.id IN $ids with a list param must produce IN (?,?,?) not IN ?."""
    q = "MATCH (a)-[r]-(b) WHERE a.id IN $node_ids RETURN a.id AS src, type(r) AS rel, b.id AS dst LIMIT 100"
    result = translate_to_sql(parse_query(q), {"node_ids": ["node1", "node2", "node3"]})
    assert "IN (?, ?, ?)" in result.sql or "IN (?,?,?)" in result.sql.replace(" ", ""), \
        f"Expected IN (?,?,?) expansion, got: {result.sql}"
    params = result.parameters[0] if result.parameters else []
    assert "node1" in params and "node2" in params and "node3" in params, \
        f"Expected list values as individual params, got: {params}"
    assert not any(isinstance(p, list) for p in params), \
        f"No nested list should appear in params after expansion, got: {params}"


def test_in_literal_list_expands_placeholders():
    """WHERE a.id IN ['x','y','z'] must produce IN (?,?,?) not IN [literal]."""
    q = "MATCH (a) WHERE a.id IN ['python','rust','go'] RETURN a.id"
    result = translate_to_sql(parse_query(q), {})
    assert "IN (?, ?, ?)" in result.sql or "IN (?,?,?)" in result.sql.replace(" ", ""), \
        f"Expected IN (?,?,?) expansion for literal list, got: {result.sql}"
    params = result.parameters[0] if result.parameters else []
    assert "python" in params and "rust" in params and "go" in params


def test_in_param_empty_list_handled():
    """WHERE a.id IN $ids with empty list should not crash (returns no results)."""
    q = "MATCH (a) WHERE a.id IN $ids RETURN a.id"
    result = translate_to_sql(parse_query(q), {"ids": []})
    assert result.sql is not None


def test_undirected_1hop_uses_union_all_not_or_join():
    q = "MATCH (a)-[r]-(b) WHERE a.id = $x RETURN b.id, type(r)"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" in r.sql, "undirected pattern must use UNION ALL not OR-join"
    assert " OR " not in r.sql or "o_id" not in r.sql.split("OR")[0], \
        "OR-join pattern must be eliminated for undirected 1-hop"


def test_undirected_with_node_props_uses_union_all():
    q = "MATCH (a)-[r]-(b) WHERE a.id = $x RETURN a.id, b.id, b.name, type(r)"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" in r.sql
    assert "e2._p AS type_r_" in r.sql, "type(r) must use _p column alias in UNION subquery"


def test_undirected_typed_rel_uses_union_all():
    q = "MATCH (a)-[r:KNOWS]-(b) WHERE a.id = $x RETURN b.id"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" in r.sql
    assert "'KNOWS'" in r.sql, "predicate inlined as literal inside UNION arms (not as bind param)"
    qmarks = r.sql.count("?")
    pcount = len(r.parameters[0]) if r.parameters else 0
    assert qmarks == pcount, f"param count mismatch: {qmarks} ?s vs {pcount} params"


def test_directed_outgoing_still_uses_single_join():
    from iris_vector_graph.cypher.translator import set_schema_prefix
    set_schema_prefix("SQLUser")
    q = "MATCH (a)-[r]->(b) WHERE a.id = $x RETURN b.id, type(r)"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" not in r.sql
    assert "rdf_edges" in r.sql
    assert "e2.p AS type_r_" in r.sql


def test_directed_incoming_still_uses_single_join():
    from iris_vector_graph.cypher.translator import set_schema_prefix
    set_schema_prefix("SQLUser")
    q = "MATCH (a)<-[r]-(b) WHERE a.id = $x RETURN b.id"
    r = translate_to_sql(parse_query(q), {"x": "hla-b27"})
    assert "UNION ALL" not in r.sql
    assert "rdf_edges" in r.sql


def test_multi_query_parts_parses():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a) RETURN a.id MATCH (b) WHERE b.id = 1 RETURN b.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) == 1

def test_multi_query_three_parts_parses():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a) RETURN a.id MATCH (b) RETURN b.id MATCH (c) RETURN c.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) >= 1

def test_single_query_unchanged():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a) WHERE a.id = $x RETURN a.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) == 0

def test_gqs_oracle_pattern_parses():
    from iris_vector_graph.cypher.parser import parse_query
    q = "MATCH (a)-[r]-(b) WHERE a.id = 1 RETURN count(*) MATCH (a)-[r2]-(b) WHERE a.id = 1 RETURN DISTINCT b.id"
    parsed = parse_query(q)
    assert len(parsed.subsequent_queries) >= 1

def test_merge_relationship_with_labeled_nodes():
    """Test MERGE with relationship creates idempotent INSERT and includes edge in SELECT."""
    query = "MATCH (a:A), (b:B) MERGE (a)-[r:TYPE]->(b) RETURN count(r)"
    parsed = parse_query(query)
    result = translate_to_sql(parsed)

    # Should generate INSERT and SELECT
    assert len(result.sql) >= 2, f"Expected at least 2 SQL statements, got {len(result.sql)}"

    insert_sql = result.sql[0]
    select_sql = result.sql[1]

    # INSERT should have idempotency guard
    assert "INSERT INTO" in insert_sql, "INSERT statement missing"
    assert "rdf_edges" in insert_sql, "INSERT should be to rdf_edges"
    assert "NOT EXISTS" in insert_sql, f"INSERT missing NOT EXISTS guard: {insert_sql}"

    # SELECT should join with rdf_edges to find the created/existing edge
    assert "SELECT" in select_sql, "SELECT statement missing"
    assert "rdf_edges" in select_sql, f"SELECT missing rdf_edges join: {select_sql}"
    assert "e4 ON" in select_sql, "Edge alias should be referenced in JOIN ON clause"
    assert "COUNT(e" in select_sql, "COUNT should reference edge alias"


def test_with_rebind_node_to_scalar():
    """Test that WITH allows rebinding a node variable to a scalar (new scope)."""
    query = "MATCH (n) WITH n.name AS n RETURN n"
    parsed = parse_query(query)
    result = translate_to_sql(parsed)
    sql = "\n".join(result.sql) if isinstance(result.sql, list) else result.sql
    # Should translate without VariableTypeConflict error
    assert "Stage" in sql or "SELECT" in sql


def test_with_rebind_same_name():
    """Test WITH can rebind with the same variable name from an expression."""
    query = "MATCH (a) WITH a.name AS a RETURN a"
    parsed = parse_query(query)
    result = translate_to_sql(parsed)
    sql = "\n".join(result.sql) if isinstance(result.sql, list) else result.sql
    # Should translate without VariableTypeConflict error
    assert "Stage" in sql or "SELECT" in sql


def test_with_rebind_relationship_to_scalar():
    """Test that WITH allows rebinding a relationship variable to a scalar."""
    query = "MATCH (a)-[r]->(b) WITH r.weight AS r RETURN r"
    parsed = parse_query(query)
    result = translate_to_sql(parsed)
    sql = "\n".join(result.sql) if isinstance(result.sql, list) else result.sql
    # Should translate without VariableTypeConflict error
    assert "Stage" in sql or "SELECT" in sql


def test_with_rebind_preserves_scope():
    """Test that WITH rebinding preserves scope: old binding is not available after WITH."""
    query = "MATCH (a:Begin {num: 42}) WITH a.num AS property MATCH (b:End) WHERE property = b.num RETURN b"
    parsed = parse_query(query)
    result = translate_to_sql(parsed)
    sql = "\n".join(result.sql) if isinstance(result.sql, list) else result.sql
    # Should translate without VariableTypeConflict error
    assert "Stage" in sql or "SELECT" in sql
    assert "property" in sql or "Stage" in sql  # property alias should be in result


# ============================================================================
# String Function Tests
# ============================================================================

class TestStringFunctions:
    """Test Cypher string functions map correctly to IRIS SQL."""

    def test_toUpper(self):
        """Test toUpper() maps to UPPER()."""
        query = "RETURN toUpper('hello') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "UPPER('hello')" in sql

    def test_toLower(self):
        """Test toLower() maps to LOWER()."""
        query = "RETURN toLower('HELLO') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "LOWER('HELLO')" in sql

    def test_trim(self):
        """Test trim() maps to TRIM()."""
        query = "RETURN trim('  hello  ') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "TRIM('  hello  ')" in sql

    def test_lTrim(self):
        """Test lTrim() maps to LTRIM()."""
        query = "RETURN lTrim('  hello') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "LTRIM('  hello')" in sql

    def test_rTrim(self):
        """Test rTrim() maps to RTRIM()."""
        query = "RETURN rTrim('hello  ') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "RTRIM('hello  ')" in sql

    def test_substring_with_start(self):
        """Test substring(s, start) with 0-based to 1-based conversion."""
        query = "RETURN substring('hello', 0) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Cypher 0-based index should convert to 1-based IRIS SQL
        assert "SUBSTRING('hello', (0) + 1)" in sql

    def test_substring_with_length(self):
        """Test substring(s, start, length) with index conversion."""
        query = "RETURN substring('hello', 1, 3) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Start index 1 should become 2 (1+1), length stays the same
        assert "SUBSTRING('hello', (1) + 1, 3)" in sql

    def test_left(self):
        """Test left(s, n) maps to LEFT()."""
        query = "RETURN left('hello', 2) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "LEFT('hello', 2)" in sql

    def test_right(self):
        """Test right(s, n) maps to RIGHT()."""
        query = "RETURN right('hello', 2) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "RIGHT('hello', 2)" in sql

    def test_replace(self):
        """Test replace(s, search, replacement) maps to REPLACE()."""
        query = "RETURN replace('hello', 'l', 'r') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "REPLACE('hello', 'l', 'r')" in sql

    def test_reverse(self):
        """Test reverse(s) maps to REVERSE()."""
        query = "RETURN reverse('hello') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "REVERSE('hello')" in sql

    def test_split(self):
        """Test split(s, delim) maps to STR_SPLIT()."""
        query = "RETURN split('a,b,c', ',') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "STR_SPLIT('a,b,c', ',')" in sql

    def test_size_on_string(self):
        """Test size(s) on string maps to LENGTH()."""
        query = "RETURN size('hello') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "LENGTH('hello')" in sql


# ============================================================================
# Type Conversion Function Tests
# ============================================================================

class TestTypeConversionFunctions:
    """Test Cypher type conversion functions map correctly to IRIS SQL."""

    def test_toString_number(self):
        """Test toString(n) on number maps to CAST AS VARCHAR."""
        query = "RETURN toString(42) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "CAST(42 AS VARCHAR" in sql

    def test_toString_boolean_literal(self):
        """Test toString(bool) on boolean literal converts to string at compile time."""
        query = "RETURN toString(true) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "'true'" in sql or "true" in sql.lower()

    def test_toInteger_numeric_string(self):
        """Test toInteger(s) with ISNUMERIC guard."""
        query = "RETURN toInteger('42') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "ISNUMERIC" in sql
        assert "CAST('42' AS INTEGER)" in sql
        assert "ELSE NULL END" in sql

    def test_toFloat_numeric_string(self):
        """Test toFloat(s) with ISNUMERIC guard."""
        query = "RETURN toFloat('3.14') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "ISNUMERIC" in sql
        assert "CAST('3.14' AS DOUBLE)" in sql
        assert "ELSE NULL END" in sql

    def test_toBoolean_string_true(self):
        """Test toBoolean('true') with case-insensitive matching."""
        query = "RETURN toBoolean('true') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "CASE WHEN" in sql
        assert "'true'" in sql or "true" in sql.lower()
        assert "THEN 1" in sql or "THEN 1" in sql
        assert "THEN 0" in sql or "THEN 0" in sql
        assert "NULL END" in sql

    def test_toBoolean_numeric(self):
        """Test toBoolean(expr) returns 1 or 0 for non-string literals."""
        query = "RETURN toBoolean(1) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should return "1" for truthy, not a CASE expression
        assert "1" in sql


# ============================================================================
# Utility Function Tests
# ============================================================================

class TestUtilityFunctions:
    """Test Cypher utility functions map correctly to IRIS SQL."""

    def test_coalesce_multiple_args(self):
        """Test coalesce(a, b, c) maps to COALESCE()."""
        query = "RETURN coalesce(null, null, 'default') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "COALESCE" in sql
        assert "'default'" in sql

    def test_nullIf_equal(self):
        """Test nullIf(a, b) maps to NULLIF()."""
        query = "RETURN nullIf('a', 'a') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "NULLIF('a', 'a')" in sql

    def test_nullIf_not_equal(self):
        """Test nullIf(a, b) returns a when a != b."""
        query = "RETURN nullIf('a', 'b') AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "NULLIF('a', 'b')" in sql

    def test_size_on_list(self):
        """Test size(list) on list literal maps to JSON_ARRAYLENGTH()."""
        query = "RETURN size([1, 2, 3]) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "JSON_ARRAYLENGTH" in sql

    def test_keys_on_map_literal(self):
        """Test keys(map) on map literal returns JSON_ARRAY of keys."""
        query = "RETURN keys({a: 1, b: 2}) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "JSON_ARRAY" in sql


# ============================================================================
# Nested and Complex Expression Tests
# ============================================================================

class TestComplexStringExpressions:
    """Test string functions in nested and complex expressions."""

    def test_nested_string_functions(self):
        """Test toUpper(substring(...)) nesting."""
        query = "RETURN toUpper(substring('hello', 1, 3)) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "UPPER" in sql
        assert "SUBSTRING" in sql

    def test_string_function_in_where(self):
        """Test string function in WHERE clause."""
        query = "MATCH (n) WHERE toUpper(n.name) = 'JOHN' RETURN n"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "UPPER" in sql
        assert "WHERE" in sql

    def test_type_conversion_in_arithmetic(self):
        """Test type conversion in arithmetic expression."""
        query = "RETURN toInteger('10') + 5 AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "ISNUMERIC" in sql or "CAST" in sql
        assert "+" in sql

    def test_coalesce_with_functions(self):
        """Test coalesce with function results."""
        query = "RETURN coalesce(null, toUpper('hello')) AS r"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "COALESCE" in sql
        assert "UPPER" in sql


# ============================================================================
# Quantifier Expression Tests
# ============================================================================

class TestQuantifierExpressions:
    """Test Cypher quantifier expressions: any(), none(), all(), single()."""

    def test_any_quantifier_literal_list(self):
        """Test any(x IN list WHERE condition) with literal list."""
        query = "RETURN any(x IN [1, 2, 3] WHERE x > 1) AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should use JSON_TABLE to iterate over list
        assert "JSON_TABLE" in sql
        # Should have CASE WHEN for 3VL semantics
        assert "CASE WHEN" in sql
        # Should check if any element satisfies the condition
        assert ">" in sql

    def test_none_quantifier_empty_list(self):
        """Test none() on empty list returns true."""
        query = "RETURN none(x IN [] WHERE true) AS a"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should use JSON_TABLE
        assert "JSON_TABLE" in sql
        # Should have CASE WHEN for 3VL semantics
        assert "CASE WHEN" in sql

    def test_single_quantifier_literal_list(self):
        """Test single(x IN list WHERE condition) with literal list."""
        query = "RETURN single(x IN [1, 2, 3] WHERE x = 2) AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should use JSON_TABLE to iterate
        assert "JSON_TABLE" in sql
        # Should have CASE WHEN for 3VL semantics
        assert "CASE WHEN" in sql
        # Should check if exactly one element satisfies
        assert "=" in sql

    def test_single_quantifier_multiple_subqueries(self):
        """Test single() uses the aggregation approach with a single JSON_TABLE scan.

        The aggregation approach computes sat/dfail/tot in one pass to avoid
        alias duplication across sibling subqueries (IRIS LoadTableFunction crash).
        """
        query = "RETURN single(x IN [1, 2, 3] WHERE x = 2) AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should use exactly one JSON_TABLE scan (single-pass aggregation approach)
        assert "JSON_TABLE" in sql
        assert sql.count("JSON_TABLE") == 1
        # Single-pass approach uses inline SUM(CASE WHEN ...) without sat/dfail/tot CTEs
        assert "SUM(CASE WHEN" in sql

    def test_all_quantifier_literal_list(self):
        """Test all(x IN list WHERE condition) with literal list."""
        query = "RETURN all(x IN [1, 2, 3] WHERE x > 0) AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should use JSON_TABLE to iterate
        assert "JSON_TABLE" in sql
        # Should have CASE WHEN for 3VL semantics
        assert "CASE WHEN" in sql
        # Should check non-null count vs satisfying count
        assert ">" in sql or "<" in sql

    def test_any_with_string_predicate(self):
        """Test any() with string list and string predicate."""
        query = "RETURN any(x IN ['a', 'b', 'c'] WHERE size(x) = 1) AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        assert "JSON_TABLE" in sql
        assert "CASE WHEN" in sql

    def test_quantifier_parses_without_error(self):
        """Test that quantifier expressions parse and translate without error."""
        queries = [
            "RETURN any(x IN [1, 2, 3] WHERE x > 1)",
            "RETURN none(x IN [] WHERE true)",
            "RETURN single(x IN [1, 2, 3] WHERE x = 2)",
            "RETURN all(x IN [1, 2, 3] WHERE x > 0)",
        ]
        for query in queries:
            result = translate_to_sql(parse_query(query))
            # Just verify it produces SQL output without error
            sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
            assert len(sql) > 0

class TestBooleanLogic:
    """Tests for boolean operators: AND, OR, XOR, NOT with 3VL (three-valued logic)."""

    def test_xor_true_true(self):
        """Test XOR with two true literals produces small SQL."""
        query = "RETURN true XOR true AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should fold to 0 (false) at Python level, resulting in simple CASE WHEN
        # We check that it doesn't have exponentially nested AND/OR expressions
        paren_count = sql.count('(')
        # A simple "CASE WHEN (1=0) THEN 1 ELSE 0 END" has only a few parens
        assert paren_count < 10, f"Expected simple SQL but got: {sql}"

    def test_xor_true_false(self):
        """Test XOR with true and false literals."""
        query = "RETURN true XOR false AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should fold to 1 (true) - may be "SELECT 1" or "CASE WHEN (1=1) THEN 1"
        assert "1" in sql
        # Should not have exponential nesting
        paren_count = sql.count('(')
        assert paren_count < 10

    def test_xor_false_false(self):
        """Test XOR with two false literals."""
        query = "RETURN false XOR false AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should fold to 0 (false) - may be "SELECT 0" or "CASE WHEN (1=0) THEN 1"
        assert "0" in sql
        paren_count = sql.count('(')
        assert paren_count < 10

    def test_xor_with_null(self):
        """Test XOR with null returns null."""
        query = "RETURN true XOR null AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should return NULL
        assert "NULL" in sql

    def test_xor_chained_literals(self):
        """Test that chained XOR with literals produces simple SQL."""
        query = "RETURN true XOR true XOR true XOR true XOR true AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # Should fold to simple result, not exponentially large expression
        # With 5 trues: true XOR true = false, false XOR true = true, true XOR true = false, false XOR true = true
        # The SQL should be relatively small, not deeply nested
        paren_count = sql.count('(')
        # Without constant folding, this would have hundreds of parens
        assert paren_count < 20, f"Expected simple SQL but got {paren_count} parens: {sql}"

    def test_xor_mixed_true_false_chained(self):
        """Test chained XOR with mix of true and false."""
        query = "RETURN true XOR true XOR false AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # true XOR true = false, false XOR false = false
        assert "0" in sql
        # Should not have exponential nesting
        paren_count = sql.count('(')
        assert paren_count < 15

    def test_not_true(self):
        """Test NOT true."""
        query = "RETURN NOT true AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # NOT true = false
        assert "0" in sql

    def test_not_null(self):
        """Test NOT null returns null."""
        query = "RETURN NOT null AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # NOT null = null
        assert "NULL" in sql

    def test_null_equals_null(self):
        """Test that null = null returns null (not true)."""
        query = "RETURN null = null AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # null = null should return NULL
        assert "NULL" in sql

    def test_null_not_equals_null(self):
        """Test that null <> null returns null."""
        query = "RETURN null <> null AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # null <> null should return NULL
        assert "NULL" in sql

    def test_in_list_with_null(self):
        """Test IN with null in the list uses 3VL semantics."""
        query = "RETURN 1 IN [1, null, 3] AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # 1 IN [1, null, 3] is true (1 is in the list); constant-folded
        assert "SELECT 1 AS" in sql

    def test_and_with_true_and_false(self):
        """Test AND with true and false."""
        query = "RETURN true AND false AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # true AND false = false
        assert "0" in sql

    def test_or_with_true_and_false(self):
        """Test OR with true and false."""
        query = "RETURN true OR false AS result"
        result = translate_to_sql(parse_query(query))
        sql = result.sql if isinstance(result.sql, str) else "\n".join(result.sql)
        # true OR false = true
        assert "1" in sql
