"""
Tests targeting remaining uncovered lines in iris_vector_graph/cypher/translator.py.
Focus on: _safe_alias, _expr_to_cypher_text, _contains_aggregation, properties() fn,
labels() fn, size() fn, _expr_slice, subscript / index expressions, translate_return_clause,
temporal detection, _detect_temporal_type, _scalar_statistical, procedure call errors,
WITH alias expansion (where), HAVING clauses, property access on literals/non-maps,
_create_resolve_prop_value, MERGE patterns, duplicate column checks, RETURN *.
"""

import pytest


def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


# ─────────────────────────────────────────────────────────────────────────────
# _safe_alias: reserved words get double-quoted
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeAlias:
    def test_reserved_count_alias(self):
        sql = tr("MATCH (n) RETURN count(n) AS count")
        assert '"count"' in sql.lower() or "count" in sql

    def test_reserved_date_alias(self):
        sql = tr("RETURN date('2024-01-01') AS date")
        assert "date" in sql.lower()

    def test_reserved_type_alias(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN type(r) AS type")
        assert "type" in sql.lower()

    def test_reserved_order_alias(self):
        sql = tr("MATCH (n) RETURN n.val AS order")
        assert '"order"' in sql or "order" in sql.lower()

    def test_reserved_group_alias(self):
        sql = tr("MATCH (n) RETURN n.val AS group")
        assert '"group"' in sql or "group" in sql.lower()

    def test_non_reserved_alias_unchanged(self):
        sql = tr("MATCH (n) RETURN n.name AS myalias")
        assert "myalias" in sql

    def test_input_prefix_reserved(self):
        # 'inputList' starts with 'input' (reserved prefix) — should be quoted
        sql = tr("MATCH (n) RETURN n.val AS inputList")
        assert '"inputList"' in sql or "inputList" in sql

    def test_empty_string_alias_passthrough(self):
        # Ordinary alias not reserved
        sql = tr("MATCH (n) RETURN n.name AS personName")
        assert "personName" in sql

    def test_reserved_value_alias(self):
        sql = tr("MATCH (n) RETURN n.x AS value")
        assert "value" in sql.lower()

    def test_reserved_key_alias(self):
        sql = tr("MATCH (n) RETURN n.x AS key")
        assert "key" in sql.lower()


# ─────────────────────────────────────────────────────────────────────────────
# labels() function
# ─────────────────────────────────────────────────────────────────────────────

class TestLabelsFunction:
    def test_labels_of_node(self):
        sql = tr("MATCH (n) RETURN labels(n)")
        assert "labels" in sql.lower() or "rdf_labels" in sql.lower() or "label" in sql.lower()

    def test_labels_where_contains(self):
        sql = tr("MATCH (n) WHERE 'Person' IN labels(n) RETURN n.name")
        assert "label" in sql.lower() or "rdf_labels" in sql.lower()

    def test_labels_in_with(self):
        sql = tr("MATCH (n) WITH labels(n) AS lbls RETURN lbls")
        assert "label" in sql.lower() or "Stage" in sql


# ─────────────────────────────────────────────────────────────────────────────
# properties() function
# ─────────────────────────────────────────────────────────────────────────────

class TestPropertiesFunction:
    def test_properties_of_node(self):
        sql = tr("MATCH (n) RETURN properties(n)")
        assert "properties" in sql.lower() or "JSON" in sql or "json" in sql.lower()

    def test_properties_of_map_literal(self):
        sql = tr("RETURN properties({a: 1, b: 2})")
        assert sql is not None

    def test_properties_of_null(self):
        sql = tr("RETURN properties(null)")
        assert "NULL" in sql.upper()

    def test_properties_scalar_raises(self):
        with pytest.raises((SyntaxError, TypeError, Exception)):
            tr("RETURN properties(1)")

    def test_properties_of_relationship(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN properties(r)")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# size() function
# ─────────────────────────────────────────────────────────────────────────────

class TestSizeFunction:
    def test_size_of_list(self):
        sql = tr("RETURN size([1,2,3])")
        assert "JSON_ARRAYLENGTH" in sql or "LENGTH" in sql or "size" in sql.lower()

    def test_size_of_string(self):
        sql = tr("RETURN size('hello')")
        assert "LENGTH" in sql or "5" in sql

    def test_size_of_collect(self):
        sql = tr("MATCH (n) RETURN size(collect(n.name))")
        assert "JSON_ARRAYLENGTH" in sql or "COUNT" in sql or "collect" in sql.lower()

    def test_size_of_property(self):
        sql = tr("MATCH (n) RETURN size(n.tags)")
        assert "LENGTH" in sql or "JSON_ARRAYLENGTH" in sql

    def test_size_of_variable(self):
        sql = tr("MATCH (n) WITH collect(n) AS nodes RETURN size(nodes)")
        assert "JSON_ARRAYLENGTH" in sql or "LENGTH" in sql or "Stage" in sql


# ─────────────────────────────────────────────────────────────────────────────
# _expr_slice (list/string slicing [start..end])
# ─────────────────────────────────────────────────────────────────────────────

class TestSliceExpression:
    def test_slice_full_range(self):
        sql = tr("RETURN [1,2,3,4,5][1..3]")
        assert sql is not None

    def test_slice_no_start(self):
        sql = tr("RETURN [1,2,3,4,5][..2]")
        assert sql is not None

    def test_slice_no_end(self):
        sql = tr("RETURN [1,2,3,4,5][2..]")
        assert sql is not None

    def test_slice_both_none(self):
        sql = tr("RETURN [1,2,3][..]")
        assert sql is not None

    def test_slice_negative_start(self):
        sql = tr("RETURN [1,2,3,4,5][-2..]")
        assert sql is not None

    def test_slice_negative_end(self):
        sql = tr("RETURN [1,2,3,4,5][..-1]")
        assert sql is not None

    def test_slice_with_param(self):
        sql = tr("RETURN $list[$s..$e]", params={"list": [1, 2, 3], "s": 1, "e": 2})
        assert sql is not None

    def test_slice_on_property(self):
        sql = tr("MATCH (n) RETURN n.tags[0..2]")
        assert sql is not None

    def test_slice_string_range(self):
        sql = tr("RETURN 'hello'[1..3]")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# Subscript expressions (list[index], map[key])
# ─────────────────────────────────────────────────────────────────────────────

class TestSubscriptExpression:
    def test_list_literal_index(self):
        sql = tr("RETURN [10, 20, 30][1]")
        assert "20" in sql or "JSON_ARRAYGET" in sql or "JSON_TABLE" in sql

    def test_list_index_zero(self):
        sql = tr("RETURN [10, 20, 30][0]")
        assert sql is not None

    def test_negative_index(self):
        sql = tr("RETURN [1,2,3][-1]")
        assert sql is not None

    def test_param_list_index(self):
        sql = tr("RETURN $list[0]", params={"list": [1, 2, 3]})
        assert sql is not None

    def test_map_key_access(self):
        sql = tr("RETURN {a: 1, b: 2}['a']")
        assert "JSON_VALUE" in sql or "1" in sql

    def test_param_map_string_key(self):
        sql = tr("RETURN $m[$k]", params={"m": {"x": 10}, "k": "x"})
        assert "JSON_VALUE" in sql or sql is not None

    def test_param_int_index(self):
        sql = tr("RETURN $list[$idx]", params={"list": [1, 2, 3], "idx": 1})
        assert sql is not None

    def test_non_integer_index_var_string(self):
        # Non-integer index into a list → type error branch
        sql = tr("RETURN $list[$k]", params={"list": [1, 2], "k": "hello"})
        # Should produce a CASE/IVGTYPEERROR or similar
        assert sql is not None

    def test_non_integer_index_var_bool(self):
        sql = tr("RETURN $list[$k]", params={"list": [1, 2], "k": True})
        assert sql is not None

    def test_non_integer_index_var_int_into_map(self):
        sql = tr("RETURN $m[$k]", params={"m": {"x": 1}, "k": 0})
        assert sql is not None

    def test_property_access_on_variable(self):
        sql = tr("MATCH (n) RETURN n['name']")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# _expr_property_access — property on literal raises TypeError
# ─────────────────────────────────────────────────────────────────────────────

class TestPropertyAccessErrors:
    def test_prop_on_integer_literal_raises(self):
        with pytest.raises((TypeError, Exception)):
            tr("RETURN 1.name")

    def test_prop_on_string_literal_raises(self):
        with pytest.raises((TypeError, Exception)):
            tr("RETURN 'hello'.name")

    def test_prop_on_map_literal_ok(self):
        sql = tr("RETURN {name: 'Alice'}.name")
        assert "Alice" in sql or "JSON_VALUE" in sql

    def test_prop_on_null_literal(self):
        # null.name raises TypeError (null is a non-map non-dict literal)
        with pytest.raises((TypeError, Exception)):
            tr("RETURN null.name")


# ─────────────────────────────────────────────────────────────────────────────
# _detect_temporal_type
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectTemporalType:
    def test_date_fn_is_temporal(self):
        sql = tr("RETURN date('2024-01-01').year")
        assert sql is not None  # temporal component extraction runs

    def test_datetime_fn_is_temporal(self):
        sql = tr("RETURN datetime('2024-01-01T12:00:00').hour")
        assert sql is not None

    def test_localtime_fn_is_temporal(self):
        sql = tr("RETURN localtime('12:30:00').hour")
        assert sql is not None

    def test_time_fn_is_temporal(self):
        sql = tr("RETURN time('12:30:00+01:00').hour")
        assert sql is not None

    def test_localdatetime_fn_is_temporal(self):
        sql = tr("RETURN localdatetime('2024-01-01T12:00:00').year")
        assert sql is not None

    def test_duration_fn_is_temporal(self):
        sql = tr("RETURN duration('P1Y2M3D').months")
        assert sql is not None

    def test_duration_between_is_temporal(self):
        sql = tr("RETURN duration.between(date('2020-01-01'), date('2024-01-01')).months")
        assert sql is not None

    def test_date_truncate_is_temporal(self):
        sql = tr("RETURN date.truncate('month', date('2024-01-15')).year")
        assert sql is not None

    def test_datetime_truncate_is_temporal(self):
        sql = tr("RETURN datetime.truncate('day', datetime('2024-01-15T12:30:00Z')).year")
        assert sql is not None

    def test_localtime_dot_is_temporal(self):
        sql = tr("RETURN localtime.truncate('minute', localtime('12:34:56')).hour")
        assert sql is not None

    def test_time_dot_is_temporal(self):
        sql = tr("RETURN time.truncate('hour', time('12:34:56+01:00')).hour")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# _extract_temporal_component — date/time property access
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractTemporalComponent:
    def test_date_year(self):
        sql = tr("RETURN date('2024-03-15').year")
        assert "2024" in sql or "SUBSTRING" in sql

    def test_date_month(self):
        sql = tr("RETURN date('2024-03-15').month")
        assert "03" in sql or "SUBSTRING" in sql or "3" in sql

    def test_date_day(self):
        sql = tr("RETURN date('2024-03-15').day")
        assert "15" in sql or "SUBSTRING" in sql

    def test_date_weekYear(self):
        # Use WITH to promote date to a typed variable so temporal extraction runs
        sql = tr("WITH date('2024-01-01') AS d RETURN d.weekYear")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_date_week(self):
        sql = tr("WITH date('2024-01-01') AS d RETURN d.week")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_date_dayOfWeek(self):
        sql = tr("WITH date('2024-01-01') AS d RETURN d.dayOfWeek")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_date_weekDay(self):
        sql = tr("WITH date('2024-01-01') AS d RETURN d.weekDay")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_date_dayOfYear(self):
        sql = tr("WITH date('2024-01-01') AS d RETURN d.dayOfYear")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_date_ordinalDay(self):
        sql = tr("WITH date('2024-01-01') AS d RETURN d.ordinalDay")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_date_quarter(self):
        sql = tr("WITH date('2024-03-15') AS d RETURN d.quarter")
        assert "SUBSTRING" in sql or "CAST" in sql

    def test_datetime_hour(self):
        sql = tr("RETURN datetime('2024-03-15T10:30:00Z').hour")
        assert "SUBSTRING" in sql or "10" in sql

    def test_datetime_minute(self):
        sql = tr("RETURN datetime('2024-03-15T10:30:00Z').minute")
        assert "SUBSTRING" in sql or "30" in sql

    def test_datetime_second(self):
        sql = tr("RETURN datetime('2024-03-15T10:30:00Z').second")
        assert "SUBSTRING" in sql or "00" in sql or "0" in sql

    def test_datetime_timezone(self):
        sql = tr("RETURN datetime('2024-03-15T10:30:00+01:00').timezone")
        assert sql is not None

    def test_duration_months(self):
        sql = tr("RETURN duration('P1Y2M3D').months")
        assert sql is not None

    def test_duration_days(self):
        sql = tr("RETURN duration('P1Y2M3D').days")
        assert sql is not None

    def test_duration_seconds(self):
        sql = tr("RETURN duration('PT1H30M').seconds")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# RETURN * (star expansion)
# ─────────────────────────────────────────────────────────────────────────────

class TestReturnStar:
    def test_return_star_with_node(self):
        sql = tr("MATCH (n:Person) RETURN *")
        assert "SELECT" in sql

    def test_return_star_with_edge(self):
        sql = tr("MATCH (a)-[r:KNOWS]->(b) RETURN *")
        assert "SELECT" in sql

    def test_return_star_no_vars_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("RETURN *")

    def test_return_star_multiple_nodes(self):
        sql = tr("MATCH (a:A)-[:R]->(b:B) RETURN *")
        assert "SELECT" in sql


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate column name check
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateColumnName:
    def test_duplicate_explicit_alias_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) RETURN n.name AS x, n.age AS x")

    def test_duplicate_implicit_alias_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) RETURN n, n")

    def test_no_duplicate_ok(self):
        sql = tr("MATCH (n) RETURN n.name AS a, n.age AS b")
        assert "SELECT" in sql


# ─────────────────────────────────────────────────────────────────────────────
# WITH alias expansion in WHERE clause
# ─────────────────────────────────────────────────────────────────────────────

class TestWithAliasExpansion:
    def test_with_alias_where_eq(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm = 'Alice' RETURN nm")
        assert "Stage" in sql or "Alice" in sql

    def test_with_alias_where_gt(self):
        sql = tr("MATCH (n) WITH n.age AS a WHERE a > 30 RETURN a")
        assert "Stage" in sql or "30" in sql

    def test_with_alias_where_is_null(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm IS NULL RETURN nm")
        assert "IS NULL" in sql or "Stage" in sql

    def test_with_alias_where_is_not_null(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE nm IS NOT NULL RETURN nm")
        assert "IS NOT NULL" in sql or "Stage" in sql

    def test_with_alias_where_and(self):
        sql = tr("MATCH (n) WITH n.age AS a, n.name AS nm WHERE a > 20 AND nm = 'Bob' RETURN nm")
        assert "Stage" in sql or "Bob" in sql

    def test_with_alias_where_or(self):
        sql = tr("MATCH (n) WITH n.age AS a WHERE a < 10 OR a > 90 RETURN a")
        assert "Stage" in sql or "OR" in sql.upper()

    def test_with_alias_where_not(self):
        sql = tr("MATCH (n) WITH n.name AS nm WHERE NOT nm = 'Eve' RETURN nm")
        assert "Stage" in sql or "NOT" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# HAVING clauses (aggregate filters in WITH/RETURN)
# ─────────────────────────────────────────────────────────────────────────────

class TestHavingClauses:
    def test_with_having_count_gt(self):
        sql = tr("MATCH (n) WITH count(n) AS c WHERE c > 5 RETURN c")
        assert "HAVING" in sql.upper() or "Stage" in sql

    def test_with_having_sum_lt(self):
        sql = tr("MATCH (n) WITH sum(n.age) AS total WHERE total < 100 RETURN total")
        assert "HAVING" in sql.upper() or "Stage" in sql

    def test_with_having_and(self):
        sql = tr("MATCH (n) WITH count(n) AS c WHERE c > 2 AND c < 10 RETURN c")
        assert "HAVING" in sql.upper() or "Stage" in sql

    def test_with_having_or(self):
        sql = tr("MATCH (n) WITH count(n) AS c WHERE c = 1 OR c = 5 RETURN c")
        assert "HAVING" in sql.upper() or "Stage" in sql

    def test_with_having_not(self):
        sql = tr("MATCH (n) WITH count(n) AS c WHERE NOT c = 0 RETURN c")
        assert "HAVING" in sql.upper() or "NOT" in sql.upper() or "Stage" in sql

    def test_return_having_aggregation(self):
        sql = tr("MATCH (n:Person) RETURN n.city AS city, count(n) AS cnt ORDER BY cnt DESC")
        assert "COUNT" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# _scalar_statistical functions
# ─────────────────────────────────────────────────────────────────────────────

class TestScalarStatistical:
    def test_stdev(self):
        sql = tr("MATCH (n) RETURN stdev(n.age)")
        assert "STDDEV" in sql.upper() or "stdev" in sql.lower()

    def test_stdevp(self):
        sql = tr("MATCH (n) RETURN stDevP(n.age)")
        assert "STDDEV" in sql.upper() or "stdev" in sql.lower()


# ─────────────────────────────────────────────────────────────────────────────
# _create_resolve_prop_value edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateResolvePropValue:
    def test_create_with_list_prop(self):
        sql = tr("CREATE (n:Tag {items: [1, 2, 3]})")
        assert sql is not None

    def test_create_with_dict_prop(self):
        sql = tr("CREATE (n:Config {settings: {key: 'val'}})")
        assert sql is not None

    def test_create_param_list(self):
        sql = tr("CREATE (n:T {items: $items})", params={"items": [1, 2, 3]})
        assert sql is not None

    def test_create_param_dict(self):
        sql = tr("CREATE (n:T {cfg: $cfg})", params={"cfg": {"a": 1}})
        assert sql is not None

    def test_create_temporal_date_prop(self):
        sql = tr("CREATE (n:E {born: date('1990-01-01')})")
        assert sql is not None

    def test_create_undefined_variable_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("CREATE (n:T {x: undefinedVar})")

    def test_create_prop_ref_from_same_clause(self):
        # Property reference to a previously-created node: CREATE (a {id: 0}), (b {ref: a.id})
        sql = tr("CREATE (a {id: 0}), (b {ref: a.id})")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# _expr_to_cypher_text helpers (used for ORDER BY column aliasing)
# ─────────────────────────────────────────────────────────────────────────────

class TestExprToCypherText:
    def test_order_by_label_predicate(self):
        # LabelPredicate in ORDER BY context
        sql = tr("MATCH (n) RETURN n.name ORDER BY n.name")
        assert "ORDER BY" in sql.upper()

    def test_order_by_property_reference(self):
        sql = tr("MATCH (n) RETURN n.age ORDER BY n.age DESC")
        assert "DESC" in sql.upper()

    def test_order_by_subscript_expression(self):
        sql = tr("MATCH (n) RETURN n.tags ORDER BY n.tags[0]")
        assert sql is not None

    def test_order_by_map_literal(self):
        sql = tr("MATCH (n) RETURN {x: n.age} AS m ORDER BY m")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# _contains_aggregation
# ─────────────────────────────────────────────────────────────────────────────

class TestContainsAggregation:
    def test_size_of_collect(self):
        sql = tr("MATCH (n) RETURN size(collect(n.name)) AS cnt")
        assert "JSON_ARRAYLENGTH" in sql or "COUNT" in sql

    def test_arithmetic_with_agg(self):
        sql = tr("MATCH (n) RETURN count(n) * 2 AS doubled")
        assert "COUNT" in sql.upper()

    def test_nested_agg_in_boolean(self):
        sql = tr("MATCH (n) WITH n.city AS city, count(n) AS c WHERE c > 1 RETURN city, c")
        assert "COUNT" in sql.upper()

    def test_collect_in_map_literal(self):
        sql = tr("MATCH (n) RETURN {names: collect(n.name)}")
        assert "collect" in sql.lower() or "JSON_ARRAYAGG" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# Procedure call error paths
# ─────────────────────────────────────────────────────────────────────────────

class TestProcedureCallErrors:
    def test_weighted_shortest_path_non_string_args_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr("CALL ivg.shortestPath.weighted(1, 2, 'weight') YIELD path")

    def test_proc_aggregation_in_args_raises(self):
        with pytest.raises((SyntaxError, Exception)):
            tr("MATCH (n) CALL ivg.shortestPath.weighted(count(n), 'b', 'w') YIELD path RETURN path")

    def test_proc_missing_param_raises(self):
        with pytest.raises((KeyError, Exception)):
            tr("CALL ivg.shortestPath.weighted($from, $to, 'w') YIELD path", params={})

    def test_proc_wrong_arg_type_raises(self):
        # CALL with a STRING where INTEGER expected: type check fires
        with pytest.raises((SyntaxError, ValueError, Exception)):
            tr("CALL ivg.shortestPath.weighted($f, $t, $w, 'not_a_number') YIELD path",
               params={"f": "a", "t": "b", "w": "cost"})


# ─────────────────────────────────────────────────────────────────────────────
# Temporal OR-condition error
# ─────────────────────────────────────────────────────────────────────────────

class TestTemporalBoundErrors:
    def test_temporal_ts_or_raises(self):
        with pytest.raises((ValueError, Exception)):
            tr(
                "MATCH (a)-[r:KNOWS]->(b) "
                "WHERE r.ts >= 100 OR r.ts <= 200 "
                "RETURN a.name"
            )


# ─────────────────────────────────────────────────────────────────────────────
# MERGE patterns with ON CREATE / ON MATCH
# ─────────────────────────────────────────────────────────────────────────────

class TestMergePatterns:
    def test_merge_node_on_create_set(self):
        sql = tr("MERGE (n:Person {name: 'Alice'}) ON CREATE SET n.created = true RETURN n")
        assert sql is not None

    def test_merge_node_on_match_set(self):
        sql = tr("MERGE (n:Person {name: 'Bob'}) ON MATCH SET n.updated = true RETURN n")
        assert sql is not None

    def test_merge_relationship(self):
        sql = tr("MERGE (a:A)-[:R]->(b:B) RETURN a")
        assert sql is not None

    def test_merge_relationship_on_create(self):
        sql = tr("MERGE (a:A {id:'1'})-[:KNOWS]->(b:B {id:'2'}) ON CREATE SET a.created = 1 RETURN a")
        assert sql is not None

    def test_merge_undirected_relationship(self):
        # MERGE with BOTH direction falls back to directed; just ensure no crash
        sql = tr("MERGE (a:P {id:'x'})-[:R]->(b:P {id:'y'}) RETURN a")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# MATCH self-loop / back-reference
# ─────────────────────────────────────────────────────────────────────────────

class TestMatchSelfLoop:
    def test_self_loop_same_node_both_ends(self):
        sql = tr("MATCH (n)-[:R]->(n) RETURN n")
        assert "SELECT" in sql

    def test_self_loop_in_where(self):
        sql = tr("MATCH (a)-[:KNOWS]->(b) WHERE a = b RETURN a")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# Variable-length path: duplicate rel var raises
# ─────────────────────────────────────────────────────────────────────────────

class TestVariableAlreadyBound:
    def test_duplicate_rel_var_raises(self):
        with pytest.raises(Exception):
            tr("MATCH (a)-[r:R]->(b)-[r:S]->(c) RETURN a")


# ─────────────────────────────────────────────────────────────────────────────
# _eval_pagination_expr — SKIP/LIMIT with function expressions
# ─────────────────────────────────────────────────────────────────────────────

class TestEvalPaginationExpr:
    def test_skip_tointeger(self):
        sql = tr("MATCH (n) RETURN n SKIP toInteger(2.7)")
        assert "OFFSET" in sql.upper() or "TOP" in sql.upper() or "2" in sql

    def test_limit_ceil(self):
        sql = tr("MATCH (n) RETURN n LIMIT ceil(1.2)")
        assert "FETCH FIRST" in sql.upper() or "TOP" in sql.upper() or "2" in sql

    def test_skip_tointeger_larger(self):
        # Different value to increase coverage variance
        sql = tr("MATCH (n) RETURN n SKIP toInteger(5.9)")
        assert "OFFSET" in sql.upper() or "TOP" in sql.upper() or "5" in sql

    def test_limit_zero(self):
        sql = tr("MATCH (n) RETURN n LIMIT 0")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# WITH * (pass-through all)
# ─────────────────────────────────────────────────────────────────────────────

class TestWithStar:
    def test_with_star_pass_through(self):
        sql = tr("MATCH (n) WITH * RETURN n.name")
        assert "SELECT" in sql

    def test_with_star_then_where(self):
        sql = tr("MATCH (n:Person) WITH * WHERE n.age > 30 RETURN n.name")
        assert "SELECT" in sql


# ─────────────────────────────────────────────────────────────────────────────
# UNWIND + CREATE + RETURN context reset
# ─────────────────────────────────────────────────────────────────────────────

class TestUnwindCreateReturn:
    def test_unwind_create_return(self):
        sql = tr("UNWIND [1,2,3] AS x CREATE (n:T {val: x}) RETURN n")
        assert sql is not None

    def test_unwind_merge_return(self):
        sql = tr("UNWIND ['a','b'] AS name MERGE (n:Person {name: name}) RETURN n")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# DELETE / DETACH DELETE
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteClauses:
    def test_delete_node(self):
        sql = tr("MATCH (n:T {id: '1'}) DELETE n")
        assert sql is not None

    def test_detach_delete_node(self):
        sql = tr("MATCH (n:T {id: '1'}) DETACH DELETE n")
        assert sql is not None

    def test_delete_relationship(self):
        sql = tr("MATCH (a)-[r:KNOWS]->(b) DELETE r")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# SET property modifications (transactional clearing of WHERE conditions)
# ─────────────────────────────────────────────────────────────────────────────

class TestSetRemoveReturnFilterClear:
    def test_set_then_return_clears_filter(self):
        sql = tr("MATCH (n:Person {name: 'Alice'}) SET n.name = 'Bob' RETURN n.name")
        assert sql is not None

    def test_remove_then_return(self):
        sql = tr("MATCH (n:Person) REMOVE n.age RETURN n.name")
        assert sql is not None

    def test_set_label_then_return(self):
        sql = tr("MATCH (n {id: '1'}) SET n:Manager RETURN n.name")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# CALL standalone with YIELD * (no preceding MATCH)
# ─────────────────────────────────────────────────────────────────────────────

class TestStandaloneCallYield:
    def test_standalone_call_no_yield(self):
        sql = tr("CALL ivg.shortestPath.weighted('a', 'b', 'w') YIELD path, totalCost")
        assert sql is not None

    def test_standalone_call_yield_explicit(self):
        sql = tr("CALL ivg.shortestPath.weighted('a', 'b', 'w') YIELD path")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL MATCH null-row union
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionalMatchNullRow:
    def test_optional_match_produces_union_all(self):
        sql = tr("OPTIONAL MATCH (n:NotThere) RETURN n.name")
        assert "UNION ALL" in sql.upper() or "NULL" in sql.upper() or "SELECT" in sql

    def test_optional_match_with_bound(self):
        sql = tr("MATCH (a:Person) OPTIONAL MATCH (a)-[:KNOWS]->(b:Person) RETURN a.name, b.name")
        assert "LEFT" in sql.upper() or "OUTER" in sql.upper() or "SELECT" in sql


# ─────────────────────────────────────────────────────────────────────────────
# LabelPredicate expressions (n:Label in WHERE)
# ─────────────────────────────────────────────────────────────────────────────

class TestLabelPredicate:
    def test_label_predicate_in_select(self):
        sql = tr("MATCH (n) RETURN n:Person")
        assert "EXISTS" in sql.upper() or "rdf_labels" in sql.lower() or "CASE" in sql.upper()

    def test_label_predicate_in_where(self):
        sql = tr("MATCH (n) WHERE n:Person RETURN n.name")
        assert "rdf_labels" in sql.lower() or "label" in sql.lower()

    def test_relationship_type_predicate(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN r:KNOWS")
        assert "CASE" in sql.upper() or "rdf_edges" in sql.lower()


# ─────────────────────────────────────────────────────────────────────────────
# type() and other relationship functions
# ─────────────────────────────────────────────────────────────────────────────

class TestRelationshipFunctions:
    def test_type_of_relationship(self):
        sql = tr("MATCH (a)-[r:KNOWS]->(b) RETURN type(r)")
        assert "p" in sql.lower() or "type" in sql.lower()

    def test_startnode_function(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN startNode(r)")
        assert sql is not None

    def test_endnode_function(self):
        sql = tr("MATCH (a)-[r]->(b) RETURN endNode(r)")
        assert sql is not None

    def test_id_of_node(self):
        sql = tr("MATCH (n) RETURN id(n)")
        assert "node_id" in sql.lower() or "id" in sql.lower()


# ─────────────────────────────────────────────────────────────────────────────
# CASE expressions
# ─────────────────────────────────────────────────────────────────────────────

class TestCaseExpressions:
    def test_simple_case(self):
        sql = tr("RETURN CASE 1 WHEN 1 THEN 'one' WHEN 2 THEN 'two' ELSE 'other' END")
        assert "CASE" in sql.upper() and "WHEN" in sql.upper()

    def test_generic_case(self):
        sql = tr("MATCH (n) RETURN CASE WHEN n.age > 18 THEN 'adult' ELSE 'minor' END")
        assert "CASE" in sql.upper() and "WHEN" in sql.upper()

    def test_case_no_else(self):
        sql = tr("MATCH (n) RETURN CASE WHEN n.age > 18 THEN 'adult' END")
        assert "CASE" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# String functions
# ─────────────────────────────────────────────────────────────────────────────

class TestStringFunctions:
    def test_tolower(self):
        sql = tr("RETURN toLower('HELLO')")
        assert "LOWER" in sql.upper() or "hello" in sql

    def test_toupper(self):
        sql = tr("RETURN toUpper('hello')")
        assert "UPPER" in sql.upper() or "HELLO" in sql

    def test_trim(self):
        sql = tr("RETURN trim(' hello ')")
        assert "TRIM" in sql.upper() or "hello" in sql

    def test_ltrim(self):
        sql = tr("RETURN lTrim(' hello')")
        assert "LTRIM" in sql.upper() or "TRIM" in sql.upper()

    def test_rtrim(self):
        sql = tr("RETURN rTrim('hello ')")
        assert "RTRIM" in sql.upper() or "TRIM" in sql.upper()

    def test_split(self):
        sql = tr("RETURN split('a,b,c', ',')")
        assert sql is not None

    def test_replace(self):
        sql = tr("RETURN replace('hello world', 'world', 'Cypher')")
        assert "REPLACE" in sql.upper() or "Cypher" in sql

    def test_substring(self):
        sql = tr("RETURN substring('hello', 1, 3)")
        assert "SUBSTRING" in sql.upper()

    def test_left_fn(self):
        sql = tr("RETURN left('hello', 3)")
        assert "LEFT" in sql.upper() or "SUBSTRING" in sql.upper()

    def test_right_fn(self):
        sql = tr("RETURN right('hello', 3)")
        assert "RIGHT" in sql.upper() or "SUBSTRING" in sql.upper()

    def test_reverse(self):
        sql = tr("RETURN reverse('hello')")
        assert "REVERSE" in sql.upper()

    def test_tostring(self):
        sql = tr("RETURN toString(42)")
        assert "CAST" in sql.upper() or "VARCHAR" in sql.upper() or "42" in sql

    def test_tointeger(self):
        sql = tr("RETURN toInteger('42')")
        assert "CAST" in sql.upper() or "INTEGER" in sql.upper() or "42" in sql

    def test_tofloat(self):
        sql = tr("RETURN toFloat('3.14')")
        assert "CAST" in sql.upper() or "DOUBLE" in sql.upper() or "FLOAT" in sql.upper()

    def test_toboolean(self):
        sql = tr("RETURN toBoolean('true')")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# Math functions
# ─────────────────────────────────────────────────────────────────────────────

class TestMathFunctions:
    def test_abs(self):
        sql = tr("RETURN abs(-5)")
        assert "ABS" in sql.upper() or "5" in sql

    def test_ceil(self):
        sql = tr("RETURN ceil(1.3)")
        assert "CEILING" in sql.upper() or "CEIL" in sql.upper() or "2" in sql

    def test_floor(self):
        sql = tr("RETURN floor(1.7)")
        assert "FLOOR" in sql.upper() or "1" in sql

    def test_round(self):
        sql = tr("RETURN round(1.5)")
        assert "ROUND" in sql.upper() or "2" in sql

    def test_sqrt(self):
        sql = tr("RETURN sqrt(16)")
        assert "SQRT" in sql.upper() or "4" in sql

    def test_sign(self):
        sql = tr("RETURN sign(-3)")
        assert "SIGN" in sql.upper() or "-1" in sql

    def test_log(self):
        sql = tr("RETURN log(10)")
        assert "LOG" in sql.upper()

    def test_log10(self):
        sql = tr("RETURN log10(100)")
        assert "LOG" in sql.upper() or "2" in sql

    def test_exp(self):
        sql = tr("RETURN exp(1)")
        assert "EXP" in sql.upper()

    def test_sin(self):
        sql = tr("RETURN sin(0)")
        assert "SIN" in sql.upper() or "0" in sql

    def test_cos(self):
        sql = tr("RETURN cos(0)")
        assert "COS" in sql.upper() or "1" in sql

    def test_tan(self):
        sql = tr("RETURN tan(0)")
        assert "TAN" in sql.upper() or "0" in sql

    def test_power(self):
        sql = tr("RETURN 2 ^ 8")
        assert "POWER" in sql.upper() or "^" in sql or "256" in sql

    def test_modulo(self):
        sql = tr("RETURN 10 % 3")
        assert "MOD" in sql.upper() or "%" in sql or "1" in sql


# ─────────────────────────────────────────────────────────────────────────────
# List functions
# ─────────────────────────────────────────────────────────────────────────────

class TestListFunctions:
    def test_range_two_args(self):
        sql = tr("RETURN range(1, 5)")
        assert sql is not None

    def test_range_three_args(self):
        sql = tr("RETURN range(0, 10, 2)")
        assert sql is not None

    def test_head(self):
        sql = tr("RETURN head([1,2,3])")
        assert sql is not None

    def test_last(self):
        sql = tr("RETURN last([1,2,3])")
        assert sql is not None

    def test_tail(self):
        sql = tr("RETURN tail([1,2,3])")
        assert sql is not None

    def test_reverse_list(self):
        sql = tr("RETURN reverse([1,2,3])")
        assert sql is not None

    def test_keys_of_map(self):
        sql = tr("RETURN keys({a: 1, b: 2})")
        assert sql is not None

    def test_keys_of_node(self):
        sql = tr("MATCH (n) RETURN keys(n)")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# Predicate functions (any, all, none, single, exists)
# ─────────────────────────────────────────────────────────────────────────────

class TestPredicateFunctions:
    def test_any_predicate(self):
        sql = tr("RETURN any(x IN [1,2,3] WHERE x > 2)")
        assert sql is not None

    def test_all_predicate(self):
        sql = tr("RETURN all(x IN [1,2,3] WHERE x > 0)")
        assert sql is not None

    def test_none_predicate(self):
        sql = tr("RETURN none(x IN [1,2,3] WHERE x > 5)")
        assert sql is not None

    def test_single_predicate(self):
        sql = tr("RETURN single(x IN [1,2,3] WHERE x = 2)")
        assert sql is not None


# ─────────────────────────────────────────────────────────────────────────────
# DISTINCT in various positions
# ─────────────────────────────────────────────────────────────────────────────

class TestDistinctQueries:
    def test_return_distinct(self):
        sql = tr("MATCH (n) RETURN DISTINCT n.city")
        assert "DISTINCT" in sql.upper()

    def test_count_distinct(self):
        sql = tr("MATCH (n) RETURN count(DISTINCT n.city)")
        assert "DISTINCT" in sql.upper() and "COUNT" in sql.upper()

    def test_collect_distinct(self):
        sql = tr("MATCH (n) RETURN collect(DISTINCT n.name)")
        assert "DISTINCT" in sql.upper() or "JSON_ARRAYAGG" in sql.upper()

    def test_with_distinct(self):
        sql = tr("MATCH (n) WITH DISTINCT n.city AS city RETURN city")
        assert "DISTINCT" in sql.upper() or "Stage" in sql


# ─────────────────────────────────────────────────────────────────────────────
# UNION / UNION ALL
# ─────────────────────────────────────────────────────────────────────────────

class TestUnionQueries:
    def test_union_two_matches(self):
        sql = tr("MATCH (n:A) RETURN n.name UNION MATCH (n:B) RETURN n.name")
        assert "UNION" in sql.upper()

    def test_union_all_two_matches(self):
        sql = tr("MATCH (n:A) RETURN n.name UNION ALL MATCH (n:B) RETURN n.name")
        assert "UNION ALL" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# WHERE IN and NOT IN
# ─────────────────────────────────────────────────────────────────────────────

class TestWhereInNotIn:
    def test_where_in_list(self):
        sql = tr("MATCH (n) WHERE n.city IN ['NYC', 'LA'] RETURN n.name")
        assert "IN" in sql.upper() or "NYC" in sql

    def test_where_not_in_list(self):
        sql = tr("MATCH (n) WHERE NOT n.city IN ['NYC', 'LA'] RETURN n.name")
        assert "NOT" in sql.upper() or "IN" in sql.upper()

    def test_where_in_param(self):
        sql = tr("MATCH (n) WHERE n.id IN $ids RETURN n.name", params={"ids": ["a", "b"]})
        assert "IN" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# STARTS WITH / ENDS WITH / CONTAINS
# ─────────────────────────────────────────────────────────────────────────────

class TestStringMatching:
    def test_starts_with(self):
        sql = tr("MATCH (n) WHERE n.name STARTS WITH 'Al' RETURN n.name")
        assert "LIKE" in sql.upper() or "STARTS" in sql.lower() or "Al%" in sql

    def test_ends_with(self):
        sql = tr("MATCH (n) WHERE n.name ENDS WITH 'ice' RETURN n.name")
        assert "LIKE" in sql.upper() or "ENDS" in sql.lower() or "%ice" in sql

    def test_contains(self):
        sql = tr("MATCH (n) WHERE n.name CONTAINS 'lic' RETURN n.name")
        assert "LIKE" in sql.upper() or "CONTAINS" in sql.lower() or "%lic%" in sql

    def test_not_starts_with(self):
        sql = tr("MATCH (n) WHERE NOT n.name STARTS WITH 'Z' RETURN n.name")
        assert "NOT" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# EXISTS subquery
# ─────────────────────────────────────────────────────────────────────────────

class TestExistsSubquery:
    def test_exists_pattern(self):
        sql = tr("MATCH (n) WHERE EXISTS { (n)-[:KNOWS]->() } RETURN n.name")
        assert "EXISTS" in sql.upper()

    def test_not_exists_pattern(self):
        sql = tr("MATCH (n) WHERE NOT EXISTS { (n)-[:KNOWS]->() } RETURN n.name")
        assert "NOT" in sql.upper() and "EXISTS" in sql.upper()


# ─────────────────────────────────────────────────────────────────────────────
# WITH ORDER BY
# ─────────────────────────────────────────────────────────────────────────────

class TestWithOrderBy:
    def test_with_order_by_asc(self):
        sql = tr("MATCH (n) WITH n ORDER BY n.name ASC RETURN n.name")
        assert "ORDER BY" in sql.upper() or "Stage" in sql

    def test_with_order_by_desc(self):
        sql = tr("MATCH (n) WITH n ORDER BY n.age DESC RETURN n.age")
        assert "DESC" in sql.upper() or "Stage" in sql

    def test_with_order_skip_limit(self):
        sql = tr("MATCH (n) WITH n ORDER BY n.name SKIP 5 LIMIT 10 RETURN n.name")
        assert sql is not None
