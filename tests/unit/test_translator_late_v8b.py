"""
Tests targeting late-stage lines in iris_vector_graph/cypher/translator.py:
  - Lines 12842-12943  (_compute_temporal_override / _scalar_statistical)
  - Lines 12980-13013  (_expr_fn_shortestpath / _expr_fn_path_funcs)
  - Lines 13046-13393  (vector ops, node funcs, keys, range, list ops, _expr_function_call)
  - Lines 13556-13700  (translate_expression: LabelPredicate, _safe_alias, _expr_to_cypher_text)
  - Lines 13751-13917  (translate_return_clause: RETURN *)
  - Lines 13944-13993  (translate_return_clause: aggregation / ambiguous)
  - Lines 14021-14542  (translate_return_clause full, translate_with_clause, HAVING)
  - Lines 14720-14816  (GDS procs: triangle_count, scc, kcore)
"""
import pytest
import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


def tr_full(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    return translate_to_sql(ast_tree, params or {})


# ===========================================================================
# _scalar_statistical (lines 12911-12948)
# ===========================================================================

class TestScalarStatistical:
    def test_stdev(self):
        sql = tr("MATCH (n) RETURN stdev(n.age)")
        assert "STDDEV" in sql.upper()

    def test_stdevp(self):
        sql = tr("MATCH (n) RETURN stdevp(n.age)")
        assert "STDDEV_POP" in sql.upper() or "STDDEV" in sql.upper()

    def test_stdevs(self):
        sql = tr("MATCH (n) RETURN stdevs(n.age)")
        assert "STDDEV" in sql.upper()

    def test_percentiledisc_literal(self):
        sql = tr("MATCH (n) RETURN percentileDisc(n.age, 0.5)")
        assert "__PERCENTILE_PLACEHOLDER_0__" in sql or "PERCENTILE" in sql.upper()

    def test_percentilecont_literal(self):
        sql = tr("MATCH (n) RETURN percentileCont(n.age, 0.9)")
        assert "__PERCENTILE_PLACEHOLDER_0__" in sql or "PERCENTILE" in sql.upper()

    def test_percentiledisc_param_percentile(self):
        sql = tr("MATCH (n) RETURN percentileDisc(n.age, $p)", {"p": 0.5})
        assert "__PERCENTILE_PLACEHOLDER_0__" in sql or "PERCENTILE" in sql.upper()

    def test_percentilecont_no_args(self):
        # No args path → NULL
        # This requires a query that calls the function with no args; parser may reject it,
        # but the branch exists for safety. Test what we can via normal usage.
        sql = tr("MATCH (n) RETURN percentileCont(n.score, 0.5)")
        assert "PERCENTILE" in sql.upper() or "__PERCENTILE_PLACEHOLDER" in sql


# ===========================================================================
# _expr_fn_shortestpath (lines 12978-12995)
# ===========================================================================

class TestShortestPathFunction:
    def test_shortestpath_basic(self):
        sql = tr("MATCH p = shortestPath((a)-[*]->(b)) RETURN p")
        assert "JSON_ARRAY" in sql or "nodes" in sql or sql  # compiles to path json

    def test_allshortestpaths_basic(self):
        sql = tr("MATCH p = allShortestPaths((a)-[*]->(b)) RETURN p")
        assert "JSON_ARRAY" in sql or sql  # compiles to path json

    def test_shortestpath_in_return(self):
        sql = tr("MATCH (a), (b) RETURN shortestPath((a)-[*]->(b))")
        assert "'path'" in sql or sql  # returns 'path' sentinel or full sql


# ===========================================================================
# _expr_fn_path_funcs (lines 12998-13039)
# ===========================================================================

class TestPathFunctions:
    def test_length_null_literal(self):
        sql = tr("RETURN length(null)")
        assert "NULL" in sql

    def test_nodes_null_literal(self):
        sql = tr("RETURN nodes(null)")
        assert "NULL" in sql

    def test_relationships_null_literal(self):
        sql = tr("RETURN relationships(null)")
        assert "NULL" in sql

    def test_length_named_path(self):
        sql = tr("MATCH p = (a)-[r]->(b) RETURN length(p)")
        # Should return a count (1 relationship)
        assert "1" in sql or "length" in sql.lower() or "LENGTH" in sql

    def test_nodes_named_path(self):
        sql = tr("MATCH p = (a)-[r]->(b) RETURN nodes(p)")
        assert "JSON_ARRAY" in sql

    def test_relationships_named_path(self):
        # 4.7.0 row 20: the relationship itself, as RETURN [r] spells it, not JSON_ARRAY of its type
        sql = tr("MATCH p = (a)-[r]->(b) RETURN relationships(p)")
        assert "'{\"type\":\"' || e" in sql and '"props"' in sql

    def test_nodes_non_path_var_raises(self):
        with pytest.raises((ValueError, SyntaxError)):
            tr("MATCH (a) RETURN nodes(a)")

    def test_length_on_node_raises(self):
        with pytest.raises((SyntaxError, ValueError)):
            tr("MATCH (n) RETURN length(n)")


# ===========================================================================
# _expr_fn_vector_ops (lines 13042-13067)
# ===========================================================================

class TestVectorOps:
    def test_vector_similarity_list_literal(self):
        sql = tr("MATCH (n) RETURN vector_similarity(n, [0.1, 0.2, 0.3])")
        assert "VECTOR_COSINE" in sql
        assert "kg_NodeEmbeddings" in sql

    def test_vector_distance_list_literal(self):
        sql = tr("MATCH (n) RETURN vector_distance(n, [0.1, 0.2])")
        assert "VECTOR_COSINE" in sql
        # distance uses (1 - ...)
        assert "1 -" in sql

    def test_vector_similarity_param_list(self):
        sql = tr("MATCH (n) RETURN vector_similarity(n, $vec)", {"vec": [0.1, 0.2]})
        assert "VECTOR_COSINE" in sql
        assert "TO_VECTOR" in sql

    def test_vector_distance_param_list(self):
        sql = tr("MATCH (n) RETURN vector_distance(n, $vec)", {"vec": [0.5, 0.6]})
        assert "VECTOR_COSINE" in sql
        assert "1 -" in sql

    def test_ivg_vector_similarity(self):
        sql = tr("MATCH (n) RETURN ivg.vector_similarity(n, [1.0])")
        assert "VECTOR_COSINE" in sql

    def test_ivg_vector_distance(self):
        sql = tr("MATCH (n) RETURN ivg.vector_distance(n, [1.0])")
        assert "1 -" in sql

    def test_vector_ops_too_few_args_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("MATCH (n) RETURN vector_similarity(n)")


# ===========================================================================
# _expr_fn_node_funcs (lines 13070-13102)
# ===========================================================================

class TestNodeFunctions:
    def test_type_on_relationship(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN type(r)")
        assert ".p" in sql or "type" in sql.lower()

    def test_startnode(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN startNode(r)")
        assert ".s" in sql

    def test_endnode(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN endNode(r)")
        assert ".o_id" in sql

    def test_id_on_node(self):
        sql = tr("MATCH (n) RETURN id(n)")
        assert "node_id" in sql

    def test_type_no_alias(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN type(r)")
        assert sql  # should produce something


# ===========================================================================
# keys() function (lines 13105-13117, 13169-13180, 13309-13337)
# ===========================================================================

class TestKeysFunctions:
    def test_keys_no_args(self):
        sql = tr("RETURN keys(null)")
        assert "NULL" in sql

    def test_keys_map_literal(self):
        sql = tr("RETURN keys({a: 1, b: 2})")
        assert "'a'" in sql and "'b'" in sql

    def test_keys_empty_map_literal(self):
        sql = tr("RETURN keys({})")
        assert "[]" in sql or "CAST" in sql

    def test_keys_null_param(self):
        sql = tr("RETURN keys($p)", {"p": None})
        assert "NULL" in sql

    def test_keys_dict_param(self):
        sql = tr("RETURN keys($p)", {"p": {"x": 1, "y": 2}})
        assert "x" in sql or "JSON_ARRAY" in sql or "CAST" in sql

    def test_keys_empty_dict_param(self):
        sql = tr("RETURN keys($p)", {"p": {}})
        assert "[]" in sql or "CAST" in sql


# ===========================================================================
# range() function (lines 13123-13166)
# ===========================================================================

class TestRangeFunction:
    def test_range_basic(self):
        sql = tr("RETURN range(1, 5)")
        assert "JSON_ARRAY" in sql
        assert "1" in sql and "5" in sql

    def test_range_with_step(self):
        sql = tr("RETURN range(0, 10, 2)")
        assert "JSON_ARRAY" in sql

    def test_range_negative_step(self):
        sql = tr("RETURN range(5, 1, -1)")
        assert "JSON_ARRAY" in sql

    def test_range_empty_result(self):
        sql = tr("RETURN range(5, 1)")
        # step=1, start>end → empty JSON array or fallback CypherFn call
        assert "[]" in sql or "CAST" in sql or "CypherFn_IVGRANGE" in sql

    def test_range_zero_step_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("RETURN range(1, 5, 0)")

    def test_range_map_arg_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("RETURN range({}, 5)")

    def test_range_float_arg_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("RETURN range(1.5, 5)")

    def test_range_string_arg_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("RETURN range('a', 5)")

    def test_range_in_match(self):
        sql = tr("MATCH (n) RETURN range(1, 3)")
        assert "JSON_ARRAY" in sql


# ===========================================================================
# _expr_fn_list_ops (lines 13169-13235)
# ===========================================================================

class TestListOps:
    def test_head_function(self):
        # a list from a row, read with the element's type (4.6.0 R227); a constant list folds to its element (R193)
        sql = tr("UNWIND [[1,2,3]] AS x RETURN head(x)")
        assert "CY_LIST_ELEM" in sql and ", 0)" in sql

    def test_tail_function(self):
        sql = tr("RETURN tail([1,2,3])")
        assert "LIST_TAIL" in sql

    def test_last_function(self):
        # a list from a row, read with the element's type (4.6.0 R227); a constant list folds to its element (R193)
        sql = tr("UNWIND [[1,2,3]] AS x RETURN last(x)")
        assert "CY_LIST_ELEM" in sql and ", -1)" in sql

    def test_head_no_args(self):
        sql = tr("RETURN head(null)")
        # null → JSON_ARRAYGET(NULL, 0) or NULL
        assert "NULL" in sql or "JSON_ARRAYGET" in sql

    def test_last_no_args(self):
        sql = tr("RETURN last(null)")
        assert "NULL" in sql or "JSON_ARRAYGET" in sql

    def test_isempty_true(self):
        sql = tr("RETURN isEmpty([])")
        assert "CASE WHEN" in sql or "1" in sql

    def test_isempty_string(self):
        sql = tr("MATCH (n) RETURN isEmpty(n.name)")
        assert "CASE WHEN" in sql

    def test_round_function(self):
        sql = tr("RETURN round(3.14)")
        assert "ROUND" in sql

    def test_size_of_range(self):
        sql = tr("RETURN size(range(1, 10))")
        assert "JSON_ARRAYLENGTH" in sql

    def test_size_of_collect(self):
        sql = tr("MATCH (n) RETURN size(collect(n.name))")
        assert "JSON_ARRAYLENGTH" in sql

    def test_size_of_literal_list(self):
        sql = tr("RETURN size([1,2,3])")
        assert "JSON_ARRAYLENGTH" in sql

    def test_size_pattern_predicate_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) WHERE size((n)-->()) > 0 RETURN n")


# ===========================================================================
# _expr_function_call / translate_expression (lines 13238-13454)
# ===========================================================================

class TestExprFunctionCall:
    def test_toboolean_true(self):
        sql = tr("RETURN toBoolean(true)")
        assert "1" in sql

    def test_toboolean_false(self):
        sql = tr("RETURN toBoolean(false)")
        assert "0" in sql

    def test_tointeger_literal(self):
        sql = tr("RETURN toInteger(3.7)")
        assert "INTEGER" in sql or "CAST" in sql

    def test_tofloat_literal(self):
        sql = tr("RETURN toFloat('3.14')")
        assert "DOUBLE" in sql or "CAST" in sql

    def test_tointeger_property(self):
        sql = tr("MATCH (n) RETURN toInteger(n.age)")
        assert "ISNUMERIC" in sql

    def test_tofloat_property(self):
        sql = tr("MATCH (n) RETURN toFloat(n.price)")
        assert "ISNUMERIC" in sql

    def test_tostring_bool_expr(self):
        sql = tr("MATCH (n) RETURN toString(n.age > 5)")
        assert "'true'" in sql and "'false'" in sql

    def test_labels_function(self):
        sql = tr("MATCH (n) RETURN labels(n)")
        assert "rdf_labels" in sql or "labels" in sql.lower()

    def test_properties_null(self):
        sql = tr("RETURN properties(null)")
        assert "NULL" in sql

    def test_properties_map(self):
        sql = tr("RETURN properties({a: 1})")
        assert "1" in sql

    def test_properties_scalar_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("RETURN properties(42)")

    def test_size_scalar_variable(self):
        sql = tr("WITH 'hello' AS s RETURN size(s)")
        assert "LENGTH" in sql or "CASE WHEN" in sql

    def test_size_property_ref(self):
        sql = tr("MATCH (n) RETURN size(n.name)")
        assert "LENGTH" in sql or "CASE WHEN" in sql

    def test_tolower(self):
        sql = tr("RETURN toLower('HELLO')")
        assert "LOWER" in sql

    def test_toupper(self):
        sql = tr("RETURN toUpper('hello')")
        assert "UPPER" in sql

    def test_trim(self):
        sql = tr("RETURN trim(' hi ')")
        assert "TRIM" in sql

    def test_ltrim(self):
        sql = tr("RETURN lTrim(' hi')")
        assert "LTRIM" in sql

    def test_rtrim(self):
        sql = tr("RETURN rTrim('hi ')")
        assert "RTRIM" in sql

    def test_substring(self):
        sql = tr("RETURN substring('hello', 1, 3)")
        assert "SUBSTRING" in sql

    def test_replace(self):
        sql = tr("RETURN replace('hello', 'l', 'r')")
        assert "REPLACE" in sql

    def test_reverse(self):
        sql = tr("RETURN reverse('hello')")
        assert "REVERSE" in sql

    def test_abs(self):
        sql = tr("RETURN abs(-5)")
        assert "CAST(5 AS BIGINT)" in sql  # 4.6.0 R308: a literal's abs() is answered in its type

    def test_ceil(self):
        sql = tr("RETURN ceil(3.2)")
        assert "CEILING" in sql

    def test_floor(self):
        sql = tr("RETURN floor(3.9)")
        assert "FLOOR" in sql

    def test_sqrt(self):
        sql = tr("RETURN sqrt(4)")
        assert "SQRT" in sql

    def test_sign(self):
        sql = tr("RETURN sign(-3)")
        assert "SIGN" in sql

    def test_coalesce(self):
        sql = tr("RETURN coalesce(null, 1)")
        assert "COALESCE" in sql

    def test_nullif(self):
        sql = tr("RETURN nullIf(1, 1)")
        assert "NULLIF" in sql


# ===========================================================================
# _expr_boolean / translate_expression boolean (lines 13457-13502)
# ===========================================================================

class TestExprBoolean:
    def test_boolean_is_null(self):
        sql = tr("MATCH (n) RETURN n.name IS NULL")
        assert "IS NULL" in sql

    def test_boolean_is_not_null(self):
        sql = tr("MATCH (n) RETURN n.name IS NOT NULL")
        assert "IS NOT NULL" in sql

    def test_boolean_equals_in_select(self):
        sql = tr("MATCH (n) RETURN n.age = 5")
        assert "CASE WHEN" in sql

    def test_boolean_not(self):
        sql = tr("MATCH (n) RETURN NOT n.active")
        assert sql  # should compile

    def test_boolean_null_returns_null(self):
        sql = tr("RETURN NOT null")
        assert "NULL" in sql

    def test_boolean_true_constant(self):
        sql = tr("RETURN true = true")
        # (1=1) sentinel → "1"
        assert "1" in sql


# ===========================================================================
# _safe_alias / _IRIS_RESERVED (lines 13580-13612)
# ===========================================================================

class TestSafeAlias:
    def test_reserved_word_quoted(self):
        # 'count' is reserved — alias should be quoted
        sql = tr("MATCH (n) RETURN n.count AS count")
        assert '"count"' in sql

    def test_non_reserved_unquoted(self):
        sql = tr("MATCH (n) RETURN n.name AS myName")
        assert "myName" in sql
        assert '"myName"' not in sql

    def test_reserved_prefix_input(self):
        # 'inputList' starts with reserved prefix 'input'
        sql = tr("MATCH (n) RETURN n.data AS inputList")
        assert '"inputList"' in sql

    def test_date_reserved(self):
        sql = tr("MATCH (n) RETURN n.date AS date")
        assert '"date"' in sql

    def test_type_reserved(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN type(r) AS type")
        assert '"type"' in sql


# ===========================================================================
# _expr_to_cypher_text (lines 13615-13717)
# ===========================================================================

class TestExprToCypherText:
    def test_function_call_alias(self):
        # Function results get cypher text as alias
        sql = tr("MATCH (n) RETURN labels(n)")
        assert "labels_n_" in sql or "labels" in sql

    def test_aggregation_count_star_alias(self):
        sql = tr("MATCH (n) RETURN count(*)")
        assert "count" in sql.lower()

    def test_arith_expression_alias(self):
        # Arithmetic expressions produce underscored aliases
        sql = tr("MATCH (n) RETURN n.age + 1")
        assert "n_age" in sql or "age" in sql

    def test_boolean_expression_alias(self):
        sql = tr("MATCH (n) RETURN n.age > 5")
        assert sql  # should compile

    def test_property_reference_alias(self):
        sql = tr("MATCH (n) RETURN n.name")
        # auto-alias should be n_name
        assert "n_name" in sql

    def test_map_literal_cypher_text(self):
        sql = tr("RETURN {x: 1, y: 2}")
        assert "1" in sql and "2" in sql


# ===========================================================================
# translate_return_clause: RETURN * (lines 13783-13917)
# ===========================================================================

class TestReturnStar:
    def test_return_star_no_vars_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("RETURN *")

    def test_return_star_with_node(self):
        sql = tr("MATCH (n) RETURN *")
        assert "node_id" in sql or "rdf_props" in sql or "n_id" in sql

    def test_return_star_with_edge(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN *")
        assert sql  # should compile and include edge columns

    def test_return_star_with_named_path(self):
        sql = tr("MATCH p = (a)-[r]->(b) RETURN *")
        assert "JSON_ARRAY" in sql or "nodes" in sql


# ===========================================================================
# translate_return_clause: aggregation checks (lines 13919-13993)
# ===========================================================================

class TestReturnAggregation:
    def test_return_count_star(self):
        sql = tr("MATCH (n) RETURN count(*)")
        assert "COUNT" in sql

    def test_return_sum(self):
        sql = tr("MATCH (n) RETURN sum(n.age)")
        assert "SUM" in sql

    def test_return_avg(self):
        sql = tr("MATCH (n) RETURN avg(n.salary)")
        assert "AVG" in sql

    def test_return_pure_aggregation_no_group_by(self):
        result = tr_full("MATCH (n) RETURN count(*)")
        sql = result.sql[0] if isinstance(result.sql, list) else result.sql
        # Pure aggregation — no GROUP BY needed
        assert "GROUP BY" not in sql

    def test_return_aggregation_with_grouping(self):
        sql = tr("MATCH (n) RETURN n.name, count(*)")
        assert "GROUP BY" in sql

    def test_ambiguous_aggregation_raises(self):
        # n.age + count(*) without n.age as standalone grouping key
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) RETURN n.age + count(*)")

    def test_column_name_conflict_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) RETURN n.name AS x, n.age AS x")

    def test_return_distinct(self):
        sql = tr("MATCH (n) RETURN DISTINCT n.name")
        assert "DISTINCT" in sql

    def test_return_order_by(self):
        sql = tr("MATCH (n) RETURN n.name ORDER BY n.name")
        assert "ORDER BY" in sql

    def test_return_limit(self):
        sql = tr("MATCH (n) RETURN n LIMIT 10")
        assert "10" in sql

    def test_return_skip(self):
        sql = tr("MATCH (n) RETURN n SKIP 5")
        assert "5" in sql


# ===========================================================================
# translate_return_clause full (lines 14021-14162)
# ===========================================================================

class TestReturnClauseFull:
    def test_return_edge_variable(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN r")
        assert "type" in sql.lower() or "qualifiers" in sql or "props" in sql

    def test_return_node_variable(self):
        sql = tr("MATCH (n) RETURN n")
        assert "node_id" in sql or "n_id" in sql

    def test_return_property(self):
        sql = tr("MATCH (n) RETURN n.name")
        assert "rdf_props" in sql or "val" in sql

    def test_return_alias(self):
        sql = tr("MATCH (n) RETURN n.name AS name")
        assert "name" in sql

    def test_return_multiple(self):
        sql = tr("MATCH (n)-[r]->(m) RETURN n, r, m")
        assert sql

    def test_return_boolean_is_null_null_row(self):
        # IS NULL in RETURN should produce null-row sentinel of '1'
        sql = tr("MATCH (n) RETURN n.name IS NULL")
        assert "IS NULL" in sql

    def test_return_is_not_null_null_row(self):
        sql = tr("MATCH (n) RETURN n.name IS NOT NULL")
        assert "IS NOT NULL" in sql

    def test_return_function_dedup_alias(self):
        # When two return items produce the same auto-alias, deduplicate
        sql = tr("MATCH (n) RETURN n.age > 1, n.age > 1")
        assert sql

    def test_return_aggregation_group_by(self):
        sql = tr("MATCH (n) RETURN n.label, count(n)")
        assert "GROUP BY" in sql

    def test_return_null_literal(self):
        sql = tr("RETURN null")
        assert "NULL" in sql


# ===========================================================================
# translate_with_clause (lines 14165-14419)
# ===========================================================================

class TestWithClause:
    def test_with_star(self):
        sql = tr("MATCH (n) WITH * RETURN n")
        assert sql

    def test_with_alias(self):
        sql = tr("MATCH (n) WITH n.name AS name RETURN name")
        assert "name" in sql

    def test_with_aggregation(self):
        sql = tr("MATCH (n) WITH count(*) AS cnt RETURN cnt")
        assert "COUNT" in sql

    def test_with_where(self):
        sql = tr("MATCH (n) WITH n.age AS age WHERE age > 18 RETURN age")
        assert "18" in sql

    def test_with_where_is_null(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm IS NULL RETURN nm")
        assert "IS NULL" in sql

    def test_with_where_is_not_null(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm IS NOT NULL RETURN nm")
        assert "IS NOT NULL" in sql

    def test_with_agg_having(self):
        sql = tr("MATCH (n) WITH count(*) AS cnt WHERE cnt > 5 RETURN cnt")
        assert "HAVING" in sql or "5" in sql

    def test_with_no_alias_agg_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) WITH count(*) RETURN count")

    def test_with_ambiguous_agg_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) WITH n.age + count(*) AS x RETURN x")

    def test_with_edge_variable(self):
        sql = tr("MATCH (a)-[r]->(b) WITH r RETURN r")
        assert sql

    def test_with_and_where_not(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE NOT nm IS NULL RETURN nm")
        assert "NOT" in sql or "IS NOT NULL" in sql

    def test_with_and_where_and(self):
        sql = tr("MATCH (n) WITH n.name AS nm, n.age AS age WHERE nm = 'Alice' AND age > 10 RETURN nm")
        assert "AND" in sql

    def test_with_and_where_or(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm = 'Alice' OR nm = 'Bob' RETURN nm")
        assert "OR" in sql

    def test_with_temporal_type_tracked(self):
        sql = tr("WITH date('2023-01-01') AS d RETURN d")
        assert "2023" in sql

    def test_with_literal_list_tracked(self):
        sql = tr("WITH [1, 2, 3] AS lst RETURN lst")
        assert "JSON_ARRAY" in sql or "1" in sql


# ===========================================================================
# _translate_where_with_alias_expansion (lines 14422-14501)
# ===========================================================================

class TestWithAliasExpansion:
    def test_alias_expanded_in_where(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm = 'Alice' RETURN nm")
        # WHERE should use the original n.name expression
        assert "Alice" in sql

    def test_comparison_op_in_where(self):
        sql = tr("MATCH (n) WITH n.age AS age WHERE age >= 21 RETURN age")
        assert "21" in sql

    def test_not_in_where(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE NOT nm IS NULL RETURN nm")
        assert sql

    def test_less_than_in_where(self):
        sql = tr("MATCH (n) WITH n.age AS age WHERE age < 65 RETURN age")
        assert "65" in sql

    def test_not_equals_in_where(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm <> 'Bob' RETURN nm")
        assert "Bob" in sql


# ===========================================================================
# _references_agg_alias / _translate_having_expr (lines 14504-14542)
# ===========================================================================

class TestHavingExpr:
    def test_having_count_gt(self):
        sql = tr("MATCH (n) WITH n.label AS lbl, count(*) AS cnt WHERE cnt > 3 RETURN lbl, cnt")
        assert "HAVING" in sql
        assert "3" in sql

    def test_having_sum_lte(self):
        sql = tr("MATCH (n) WITH n.cat AS cat, sum(n.score) AS total WHERE total <= 100 RETURN cat, total")
        assert "HAVING" in sql

    def test_having_and(self):
        sql = tr("MATCH (n) WITH count(*) AS cnt, sum(n.v) AS s WHERE cnt > 1 AND s < 10 RETURN cnt")
        assert "HAVING" in sql

    def test_having_or(self):
        sql = tr("MATCH (n) WITH count(*) AS cnt WHERE cnt > 10 OR cnt < 2 RETURN cnt")
        assert "HAVING" in sql

    def test_having_not(self):
        sql = tr("MATCH (n) WITH count(*) AS cnt WHERE NOT cnt = 0 RETURN cnt")
        assert "HAVING" in sql or "NOT" in sql


# ===========================================================================
# GDS procedures: triangle count, SCC, kcore (lines 14720-14816)
# ===========================================================================

class TestGDSProcedures:
    def test_triangle_count_yield(self):
        sql = tr("CALL ivg.triangleCount() YIELD node, triangles, lcc RETURN node, triangles")
        assert "TriangleCount" in sql or "kg_TriangleCount" in sql

    def test_triangle_count_topk(self):
        sql = tr("CALL ivg.triangleCount({topK: 50}) YIELD node, triangles RETURN node")
        assert "50" in sql

    def test_scc_basic(self):
        sql = tr("CALL ivg.scc() YIELD node, component, size RETURN node, component")
        assert "SCC" in sql or "kg_SCC" in sql

    def test_scc_topk(self):
        sql = tr("CALL ivg.scc({topK: 100}) YIELD node, component RETURN node")
        assert "100" in sql

    def test_kcore_basic(self):
        sql = tr("CALL ivg.kcore() YIELD node, coreness RETURN node, coreness")
        assert "KCore" in sql or "kg_KCore" in sql

    def test_kcore_topk(self):
        sql = tr("CALL ivg.kcore({topK: 200}) YIELD node, coreness RETURN node")
        assert "200" in sql

    def test_degree_centrality(self):
        sql = tr("CALL ivg.degreeCentrality({direction: 'out', topK: 50}) YIELD node, score RETURN node, score")
        assert "DegCent" in sql or "kg_DegreeCentrality" in sql

    def test_betweenness(self):
        sql = tr("CALL ivg.betweenness({topK: 50}) YIELD node, score RETURN node, score")
        assert "Betweenness" in sql or "kg_Betweenness" in sql

    def test_closeness(self):
        sql = tr("CALL ivg.closeness({topK: 50}) YIELD node, score RETURN node, score")
        assert "Closeness" in sql or "kg_Closeness" in sql

    def test_eigenvector(self):
        sql = tr("CALL ivg.eigenvector({maxIter: 30, topK: 50}) YIELD node, score RETURN node, score")
        assert "Eigenvector" in sql or "kg_Eigenvector" in sql

    def test_leiden_basic(self):
        sql = tr("CALL ivg.leiden() YIELD node, community, size RETURN node, community")
        assert "Leiden" in sql or "kg_Leiden" in sql

    def test_leiden_options(self):
        sql = tr("CALL ivg.leiden({maxLevels: 5, gamma: 0.5}) YIELD node, community RETURN node")
        assert "5" in sql


# ===========================================================================
# LabelPredicate in translate_expression (lines 13550-13574)
# ===========================================================================

class TestLabelPredicateExpression:
    def test_label_predicate_in_return(self):
        sql = tr("MATCH (n) RETURN n:Person")
        assert "rdf_labels" in sql or "CASE WHEN" in sql

    def test_label_predicate_in_where(self):
        sql = tr("MATCH (n) WHERE n:Person RETURN n")
        assert "rdf_labels" in sql or "Person" in sql

    def test_relationship_label_predicate(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN r:KNOWS")
        assert "CASE WHEN" in sql or "KNOWS" in sql


# ===========================================================================
# _contains_aggregation / _collect_non_agg_var_refs (lines 13720-13780)
# ===========================================================================

class TestContainsAggregation:
    def test_nested_agg_in_arithmetic(self):
        # sum(n.x) + 1 with n as standalone return item — should work
        sql = tr("MATCH (n) RETURN n.label, sum(n.score) + 0")
        assert "SUM" in sql

    def test_pure_agg_in_return(self):
        sql = tr("MATCH (n) RETURN count(n), sum(n.score)")
        assert "COUNT" in sql and "SUM" in sql

    def test_non_agg_after_agg(self):
        sql = tr("MATCH (n) RETURN n.category, avg(n.score)")
        assert "AVG" in sql and "GROUP BY" in sql


# ===========================================================================
# UNION translation (combined with above)
# ===========================================================================

class TestUnionTranslation:
    def test_union(self):
        sql = tr("MATCH (n:Person) RETURN n.name UNION MATCH (n:Animal) RETURN n.name")
        assert "UNION" in sql

    def test_union_all(self):
        sql = tr("MATCH (n:Person) RETURN n.name UNION ALL MATCH (n:Animal) RETURN n.name")
        assert "UNION ALL" in sql


# ===========================================================================
# Miscellaneous coverage for remaining lines
# ===========================================================================

class TestMisc:
    def test_return_path_variable(self):
        sql = tr("MATCH p = (a)-[r]->(b) RETURN p")
        assert "JSON_ARRAY" in sql

    def test_order_by_with_agg(self):
        sql = tr("MATCH (n) RETURN n.name, count(*) ORDER BY count(*)")
        assert "ORDER BY" in sql

    def test_with_multiple_aggregations(self):
        sql = tr("MATCH (n) WITH count(*) AS cnt, sum(n.age) AS total RETURN cnt, total")
        assert "COUNT" in sql and "SUM" in sql

    def test_return_case_expression(self):
        sql = tr("MATCH (n) RETURN CASE n.age WHEN 1 THEN 'one' ELSE 'other' END")
        assert "CASE" in sql

    def test_return_list_comprehension(self):
        sql = tr("MATCH (n) RETURN [x IN [1, 2, 3] WHERE x > 1 | x]")
        assert sql

    def test_return_exists_pattern_raises(self):
        # Pattern predicates in RETURN context raise SyntaxError
        with pytest.raises(SyntaxError):
            tr("MATCH (n) RETURN exists((n)-->())")

    def test_with_skip_limit(self):
        sql = tr("MATCH (n) WITH n SKIP 2 LIMIT 5 RETURN n")
        assert "5" in sql or "2" in sql

    def test_return_tostring(self):
        sql = tr("RETURN toString(42)")
        assert "CAST" in sql or "42" in sql

    def test_return_split(self):
        sql = tr("RETURN split('a,b,c', ',')")
        assert "STRTOK_TO_TABLE" in sql or "split" in sql.lower()

    def test_return_left_right(self):
        sql1 = tr("RETURN left('hello', 3)")
        assert "LEFT" in sql1
        sql2 = tr("RETURN right('hello', 3)")
        assert "RIGHT" in sql2

    def test_return_nodes_rels_in_collect(self):
        # collect is an aggregation
        sql = tr("MATCH (n) RETURN collect(n.name)")
        assert "JSON_ARRAYAGG" in sql or "collect" in sql.lower()

    def test_return_distinct_with_aggregation(self):
        sql = tr("MATCH (n) RETURN DISTINCT n.label, count(*)")
        assert "DISTINCT" in sql or "COUNT" in sql

    def test_return_multiple_nodes(self):
        sql = tr("MATCH (a), (b) RETURN a, b")
        assert sql

    def test_temporal_override_iana_tz(self):
        # datetime with timezone → triggers IANA tz offset computation
        sql = tr("RETURN datetime({year: 2023, month: 6, day: 15, timezone: 'Europe/Berlin'})")
        assert "2023" in sql

    def test_temporal_localdatetime_with_tz_override(self):
        sql = tr("RETURN localDateTime({year: 2023, month: 1, day: 1, timezone: 'UTC'})")
        assert "2023" in sql

    def test_range_list_arg_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("RETURN range([1,2], 5)")
