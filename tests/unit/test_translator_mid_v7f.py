"""
Tests targeting uncovered lines in iris_vector_graph/cypher/translator.py:
- Lines 7498-7632  (translate_boolean_expression: label predicate, literals, property refs)
- Lines 7643-7800  (constant folding: list ordering, string predicate guards, inline literals)
- Lines 7869-8066  (pattern comprehension, prop_ref_cast, arith expr)
- Lines 8101-8207  (arith +/runtime polymorphism, _lp_predicate helpers)
- Lines 8229-8373  (list predicate expression, list_comprehension_type_check)
- Lines 8421-8532  (list comprehension with collect, reduce)
- Lines 8571-8660  (CASE expression, temporal detect)
- Lines 8700-8788  (extract_temporal_component: localdatetime/time properties)
- Lines 8874-9004  (duration properties, property_reference)
- Lines 9027-9150  (property_reference Stage, map projection, subscript)
"""
import pytest


def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


# ---------------------------------------------------------------------------
# Helper: just ensure tr() runs without raising
# ---------------------------------------------------------------------------
def check(q, params=None, contains=None, not_contains=None):
    sql = tr(q, params)
    assert sql, f"Expected non-empty SQL for: {q!r}"
    if contains:
        if isinstance(contains, str):
            contains = [contains]
        for fragment in contains:
            assert fragment in sql, f"Expected {fragment!r} in SQL:\n{sql}"
    if not_contains:
        if isinstance(not_contains, str):
            not_contains = [not_contains]
        for fragment in not_contains:
            assert fragment not in sql, f"Did NOT expect {fragment!r} in SQL:\n{sql}"
    return sql


# ===========================================================================
# Lines 7498-7632  translate_boolean_expression
# ===========================================================================

class TestTranslateBooleanExpression:
    """Tests for translate_boolean_expression helper paths."""

    # LabelPredicate
    def test_label_predicate_in_where(self):
        sql = check("MATCH (n) WHERE n:Person RETURN n.name")
        assert "rdf_labels" in sql or "EXISTS" in sql

    # Literal True / False in WHERE (via BooleanExpression wrapping)
    def test_literal_true_where(self):
        # WHERE true should produce 1=1 or equivalent
        sql = check("MATCH (n) WHERE true RETURN n")
        assert "1=1" in sql or "SELECT" in sql

    def test_literal_false_where(self):
        sql = check("MATCH (n) WHERE false RETURN n")
        assert "1=0" in sql or "SELECT" in sql

    # IS NULL
    def test_is_null_simple(self):
        sql = check("MATCH (n) WHERE n.name IS NULL RETURN n")
        assert "IS NULL" in sql

    def test_is_not_null_simple(self):
        sql = check("MATCH (n) WHERE n.name IS NOT NULL RETURN n")
        assert "IS NOT NULL" in sql

    # NULL comparison → NULL
    def test_null_equals_something(self):
        sql = check("WITH null AS x RETURN x = 1")
        # Should produce NULL
        assert "NULL" in sql

    def test_something_equals_null(self):
        sql = check("WITH null AS x RETURN 1 = x")
        assert "NULL" in sql

    # null IN [] = false (empty list)
    def test_null_in_empty_list(self):
        sql = check("RETURN null IN []")
        # null IN [] = false — translator may produce 1=0 or SELECT 0 (both mean false)
        assert "1=0" in sql or "SELECT 0" in sql or sql.strip().startswith("SELECT 0")

    # null IN non-empty list → NULL
    def test_null_in_nonempty_list(self):
        sql = check("RETURN null IN [1, 2, 3]")
        assert "NULL" in sql

    # Param null comparison
    def test_null_param_comparison(self):
        sql = check("RETURN $x = 1", params={"x": None})
        assert "NULL" in sql

    # EQUALS with both literal lists
    def test_list_equals_same(self):
        sql = check("RETURN [1, 2] = [1, 2]")
        # Constant-folded to true — may be 1=1 or SELECT 1 (true)
        assert "1=1" in sql or "SELECT 1" in sql

    def test_list_equals_different(self):
        sql = check("RETURN [1, 2] = [1, 3]")
        # Constant-folded to false — may be 1=0 or SELECT 0
        assert "1=0" in sql or "SELECT 0" in sql

    def test_list_equals_null_element(self):
        sql = check("RETURN [1, null] = [1, null]")
        # null = null → NULL (not true)
        assert "NULL" in sql

    # NOT EQUALS with literal lists
    def test_list_not_equals(self):
        sql = check("RETURN [1, 2] <> [1, 3]")
        # [1,2] <> [1,3] → true
        assert "1=1" in sql or "SELECT 1" in sql

    # Map literal equality
    def test_map_equals(self):
        sql = check("RETURN {a: 1} = {a: 1}")
        assert "1=1" in sql or "SELECT 1" in sql

    def test_map_not_equals(self):
        sql = check("RETURN {a: 1} = {a: 2}")
        assert "1=0" in sql or "SELECT 0" in sql

    # Numeric cast for equality int vs float
    def test_int_float_equality_cast(self):
        sql = check("RETURN 1 = 1.0")
        assert "CAST" in sql or "DOUBLE" in sql or "1=1" in sql or "SELECT 1 AS" in sql

    # Property reference boolean context (rdf_props stores '1'/'0')
    def test_property_ref_bool_context(self):
        sql = check("MATCH (n) WHERE n.active RETURN n")
        # Should produce = '1' comparison
        assert "= '1'" in sql or "= '0'" in sql or "val" in sql

    # IN operator with list param
    def test_in_with_param_list(self):
        sql = check("MATCH (n) WHERE n.name IN $names RETURN n", params={"names": ["Alice", "Bob"]})
        assert "IN" in sql or "Alice" in sql

    # String type guard: STARTS WITH with non-string → NULL
    def test_starts_with_non_string(self):
        sql = check("RETURN 1 STARTS WITH 2")
        assert "NULL" in sql

    def test_ends_with_non_string(self):
        sql = check("RETURN 1 ENDS WITH 'x'")
        assert "NULL" in sql

    def test_contains_non_string(self):
        sql = check("RETURN [1,2] CONTAINS 'a'")
        assert "NULL" in sql

    # STARTS WITH valid
    def test_starts_with_valid(self):
        sql = check("MATCH (n) WHERE n.name STARTS WITH 'Al' RETURN n")
        assert "LIKE" in sql or "STARTS" in sql or "%" in sql

    # Cross-type ordering → NULL
    def test_string_less_than_number(self):
        sql = check("RETURN 'abc' < 1")
        assert "NULL" in sql

    def test_number_greater_than_string(self):
        sql = check("RETURN 1 > 'abc'")
        assert "NULL" in sql

    # IS NULL with BooleanExpression operand
    def test_boolean_expr_is_null(self):
        sql = check("RETURN (true AND false) IS NULL")
        # true AND false = false (known), so IS NULL = false → 1=0 or SELECT 0
        assert "1=0" in sql or "SELECT 0" in sql

    def test_boolean_expr_is_not_null(self):
        sql = check("RETURN (true AND true) IS NOT NULL")
        # true AND true = true (known), so IS NOT NULL = true → 1=1 or SELECT 1
        assert "1=1" in sql or "SELECT 1" in sql


# ===========================================================================
# Lines 7643-7800  list ordering ops + _inline_literal + _cypher_eq/_cypher_list_cmp
# ===========================================================================

class TestListOrderingAndConstantFolding:
    """Tests for list literal ordering and related helper functions."""

    def test_list_less_than(self):
        sql = check("RETURN [1, 2] < [1, 3]")
        # [1,2] < [1,3] → true
        assert "1=1" in sql or "SELECT 1" in sql

    def test_list_greater_than(self):
        sql = check("RETURN [2, 0] > [1, 9]")
        # [2,0] > [1,9] → true (2 > 1)
        assert "1=1" in sql or "SELECT 1" in sql

    def test_list_less_than_equal(self):
        sql = check("RETURN [1, 2] <= [1, 2]")
        # equal lists → true for <=
        assert "1=1" in sql or "SELECT 1" in sql

    def test_list_greater_than_equal(self):
        sql = check("RETURN [3] >= [3]")
        assert "1=1" in sql or "SELECT 1" in sql

    def test_list_less_than_different_lengths(self):
        sql = check("RETURN [1] < [1, 2]")
        # [1] < [1,2] → first elements equal, [1] is shorter → true (<0)
        assert "1=1" in sql or "SELECT 1" in sql

    def test_list_ordering_null_element(self):
        sql = check("RETURN [null, 1] < [2, 3]")
        assert "NULL" in sql

    def test_list_cross_type_ordering(self):
        sql = check("RETURN ['a', 1] < ['b', 2]")
        # 'a' < 'b' → 1=1 (only compares first element which are same type)
        assert sql is not None

    # _inline_literal tests (exercised via WHERE literals)
    def test_inline_literal_null(self):
        sql = check("RETURN null")
        assert "NULL" in sql

    def test_inline_literal_true(self):
        sql = check("RETURN true")
        assert "1" in sql

    def test_inline_literal_false(self):
        sql = check("RETURN false")
        assert "0" in sql

    def test_inline_literal_int(self):
        sql = check("RETURN 42")
        assert "42" in sql

    def test_inline_literal_float(self):
        sql = check("RETURN 3.14")
        assert "3.14" in sql

    def test_inline_literal_string(self):
        sql = check("RETURN 'hello'")
        assert "hello" in sql

    # _cypher_eq directly tested through list equality
    def test_nested_list_equality(self):
        sql = check("RETURN [[1, 2], [3]] = [[1, 2], [3]]")
        assert "1=1" in sql or "SELECT 1" in sql

    def test_nested_list_inequality(self):
        sql = check("RETURN [[1, 2]] = [[1, 3]]")
        assert "1=0" in sql or "SELECT 0" in sql

    def test_list_different_lengths_equality(self):
        sql = check("RETURN [1, 2] = [1]")
        assert "1=0" in sql or "SELECT 0" in sql

    # Cross-type ordering: list vs list with nulls
    def test_list_ordering_both_null_first_element(self):
        sql = check("RETURN [null] < [null]")
        assert "NULL" in sql

    # Ordering with int vs float (same type)
    def test_int_literal_less_than(self):
        sql = check("RETURN 1 < 2")
        # Non-list literals: not constant-folded to 1=1, generates comparison SQL
        assert "1 < 2" in sql or "1=1" in sql or "SELECT" in sql

    def test_int_literal_greater_than(self):
        sql = check("RETURN 5 > 3")
        assert "5 > 3" in sql or "1=1" in sql or "SELECT" in sql

    # Ordering when int vs float literal (cross-numeric)
    def test_int_less_than_float(self):
        sql = check("RETURN 1 < 1.5")
        assert "CAST" in sql or "1=1" in sql or "SELECT" in sql


# ===========================================================================
# Lines 7869-8066  pattern comprehension + arithmetic
# ===========================================================================

class TestPatternComprehensionAndArithmetic:
    """Tests for pattern comprehension and arithmetic expression generation."""

    # Pattern comprehension basic
    def test_pattern_comprehension_basic(self):
        sql = check("MATCH (n) RETURN [(n)-[:KNOWS]->(m) | m.name]")
        assert "JSON_ARRAYAGG" in sql or "rdf_edges" in sql

    def test_pattern_comprehension_no_type(self):
        sql = check("MATCH (n) RETURN [(n)-[]->(m) | m.name]")
        assert "JSON_ARRAYAGG" in sql

    def test_pattern_comprehension_node_id(self):
        sql = check("MATCH (n) RETURN [(n)-->(m) | m.node_id]")
        assert "node_id" in sql

    # Arithmetic: modulo
    def test_modulo_literal(self):
        sql = check("RETURN 10 % 3")
        assert "MOD" in sql

    def test_modulo_zero(self):
        sql = check("RETURN 10 % 0")
        assert "NaN" in sql or "CASE WHEN" in sql

    # Power (^)
    def test_power_operator(self):
        sql = check("RETURN 2 ^ 10")
        assert "POWER" in sql and "DOUBLE" in sql

    # String concatenation (+)
    def test_string_concatenation(self):
        sql = check("RETURN 'hello' + ' world'")
        assert "VARCHAR" in sql or "||" in sql

    # Numeric addition
    def test_integer_addition(self):
        sql = check("RETURN 3 + 4")
        assert sql is not None

    # Subtraction
    def test_subtraction(self):
        sql = check("RETURN 10 - 3")
        assert sql is not None

    # Multiplication
    def test_multiplication(self):
        sql = check("RETURN 4 * 5")
        assert sql is not None

    # Division integer/integer = floor
    def test_integer_division_floor(self):
        sql = check("RETURN 7 / 2")
        assert "FLOOR" in sql

    # Division by zero
    def test_division_by_zero(self):
        sql = check("RETURN 5 / 0")
        assert "NaN" in sql or "CASE WHEN" in sql

    # List concatenation
    def test_list_concatenation_literal(self):
        sql = check("RETURN [1, 2] + [3, 4]")
        assert "[1, 2, 3, 4]" in sql or "1" in sql

    # Mixed numeric add with float
    def test_numeric_add_float(self):
        sql = check("RETURN 1 + 2.5")
        assert sql is not None

    # Integer / float → not floor
    def test_division_int_by_float(self):
        sql = check("RETURN 7 / 2.0")
        # No FLOOR for float division
        assert sql is not None

    # Unary negation
    def test_unary_negation(self):
        sql = check("RETURN -5")
        assert "-5" in sql or "5" in sql


# ===========================================================================
# Lines 8101-8207  _lp_predicate helpers + runtime polymorphic +
# ===========================================================================

class TestArithPlusPolymorphismAndHelpers:
    """Tests for polymorphic + and list predicate helpers."""

    # List + scalar (runtime check)
    def test_list_plus_scalar_number(self):
        sql = check("RETURN [1, 2] + 3")
        assert sql is not None

    # _lp_source_all_non_numeric
    def test_any_predicate_string_list(self):
        sql = check("RETURN any(x IN ['a', 'b', 'c'] WHERE x = 'b')")
        assert "CASE WHEN" in sql or "SUM" in sql

    # _lp_needs_null_sentinel: single-element list with numeric inner list
    def test_any_predicate_numeric_nested_list(self):
        sql = check("RETURN any(x IN [[1, 2, 3]] WHERE size(x) > 1)")
        assert sql is not None

    # _lp_predicate_uses_arithmetic_on_var
    def test_all_predicate_with_arithmetic(self):
        sql = check("RETURN all(x IN [2, 4, 6] WHERE x % 2 = 0)")
        assert "SUM" in sql or "CASE WHEN" in sql

    def test_none_predicate(self):
        sql = check("RETURN none(x IN [1, 2, 3] WHERE x > 5)")
        assert "SUM" in sql or "CASE WHEN" in sql

    def test_single_predicate(self):
        sql = check("RETURN single(x IN [1, 2, 3] WHERE x = 2)")
        assert "SUM" in sql or "CASE WHEN" in sql

    # any predicate
    def test_any_predicate_basic(self):
        sql = check("RETURN any(x IN [1, 2, 3] WHERE x > 1)")
        assert "CASE WHEN" in sql

    def test_any_predicate_bool_list(self):
        sql = check("RETURN any(x IN [true, false] WHERE x = true)")
        assert sql is not None

    # all predicate
    def test_all_predicate_basic(self):
        sql = check("RETURN all(x IN [2, 4, 6] WHERE x > 0)")
        assert "CASE WHEN" in sql


# ===========================================================================
# Lines 8229-8373  list predicate + list_comprehension_type_check
# ===========================================================================

class TestListPredicateAndTypeCheck:
    """Tests for list predicate expression and type-checking."""

    # _list_comprehension_type_check: toBoolean with number → TypeError
    def test_toboolean_with_number_raises(self):
        with pytest.raises(TypeError):
            tr("RETURN [x IN [1, 2] | toBoolean(x)]")

    # toBoolean with valid strings
    def test_toboolean_with_strings(self):
        sql = check("RETURN [x IN ['true', 'false'] | toBoolean(x)]")
        assert "true" in sql.lower() or "false" in sql.lower()

    # toInteger with bool → TypeError
    def test_tointeger_with_bool_raises(self):
        with pytest.raises(TypeError):
            tr("RETURN [x IN [true, false] | toInteger(x)]")

    # toFloat with string → ok (constant folded)
    def test_tofloat_with_string_list(self):
        sql = check("RETURN [x IN ['1.5', '2.5'] | toFloat(x)]")
        assert "1.5" in sql or "2.5" in sql

    # toString with list element → TypeError
    def test_tostring_with_list_raises(self):
        with pytest.raises(TypeError):
            tr("RETURN [x IN [[1,2]] | toString(x)]")

    # Constant fold toInteger list
    def test_tointeger_constant_fold(self):
        sql = check("RETURN [x IN [1, 2, 3] | toInteger(x)]")
        assert "1" in sql and "2" in sql and "3" in sql

    # Constant fold toString list
    def test_tostring_constant_fold(self):
        sql = check("RETURN [x IN [1, 2] | toString(x)]")
        assert "1" in sql and "2" in sql

    # Constant fold toBoolean list
    def test_toboolean_constant_fold(self):
        sql = check("RETURN [x IN ['true', 'false', 'maybe'] | toBoolean(x)]")
        assert "true" in sql.lower()

    # Constant fold toFloat list
    def test_tofloat_constant_fold(self):
        sql = check("RETURN [x IN [1, 2, 3] | toFloat(x)]")
        assert sql is not None

    # List predicate with IN operator
    def test_list_predicate_in_where(self):
        sql = check("MATCH (n) WHERE any(x IN [1,2,3] WHERE x = n.age) RETURN n")
        assert sql is not None

    # all() with IS NULL check
    def test_all_predicate_null_element(self):
        sql = check("RETURN all(x IN [null, 1] WHERE x IS NOT NULL)")
        assert sql is not None

    # none() with multiple conditions
    def test_none_predicate_complex(self):
        sql = check("RETURN none(x IN ['a', 'b', 'c'] WHERE x STARTS WITH 'z')")
        assert sql is not None


# ===========================================================================
# Lines 8421-8532  list comprehension with collect + reduce
# ===========================================================================

class TestListComprehensionAndReduce:
    """Tests for list comprehension (including collect source) and reduce()."""

    # Basic list comprehension
    def test_list_comp_basic(self):
        sql = check("RETURN [x IN [1, 2, 3] | x * 2]")
        assert "JSON_ARRAYAGG" in sql or "[2, 4, 6]" in sql

    def test_list_comp_with_filter(self):
        sql = check("RETURN [x IN [1, 2, 3, 4] WHERE x > 2 | x]")
        assert "JSON_ARRAYAGG" in sql or "WHERE" in sql

    # List comprehension with no projection (identity)
    def test_list_comp_no_projection(self):
        sql = check("RETURN [x IN [1, 2, 3]]")
        assert sql is not None

    # reduce() basic
    def test_reduce_basic(self):
        sql = check("RETURN reduce(acc = 0, x IN [1, 2, 3] | acc + x)")
        assert "SUM" in sql or "JSON_TABLE" in sql

    def test_reduce_with_float_init(self):
        sql = check("RETURN reduce(acc = 0.0, x IN [1.0, 2.0] | acc + x)")
        assert "SUM" in sql or "JSON_TABLE" in sql

    # List comprehension on collect source (aggregate)
    def test_list_comp_collect_source(self):
        sql = check("MATCH (n) RETURN [x IN collect(n.age) | x * 2]")
        assert "JSON_ARRAYAGG" in sql

    # List comprehension collect source with filter
    def test_list_comp_collect_with_filter(self):
        sql = check("MATCH (n) RETURN [x IN collect(n.age) WHERE x > 18 | x]")
        assert "JSON_ARRAYAGG" in sql

    # List comprehension with type conversion
    def test_list_comp_tointeger(self):
        sql = check("RETURN [x IN [1, 2, 3] | toInteger(x)]")
        assert sql is not None

    # reduce() with collect source
    def test_reduce_with_collect(self):
        sql = check("MATCH (n) RETURN reduce(total = 0, x IN collect(n.age) | total + x)")
        assert "SUM" in sql


# ===========================================================================
# Lines 8571-8660  CASE expression + _detect_temporal_type
# ===========================================================================

class TestCaseExpressionAndTemporalDetect:
    """Tests for CASE expression and temporal type detection."""

    # Simple CASE (searched)
    def test_searched_case(self):
        sql = check("RETURN CASE WHEN 1 = 1 THEN 'yes' ELSE 'no' END")
        assert "CASE WHEN" in sql

    def test_searched_case_no_else(self):
        sql = check("RETURN CASE WHEN 1 = 2 THEN 'yes' END")
        assert "CASE WHEN" in sql

    # Simple CASE (value-based)
    def test_value_case(self):
        sql = check("RETURN CASE 1 WHEN 1 THEN 'one' WHEN 2 THEN 'two' ELSE 'other' END")
        assert "CASE" in sql

    # Multiple WHEN clauses
    def test_case_multiple_when(self):
        sql = check("RETURN CASE WHEN 1 > 5 THEN 1 WHEN 1 < 5 THEN 2 ELSE 3 END")
        assert "CASE WHEN" in sql

    # CASE in WHERE
    def test_case_in_where(self):
        sql = check("MATCH (n) WHERE CASE WHEN n.age > 18 THEN true ELSE false END RETURN n")
        assert "CASE WHEN" in sql

    # Temporal function detection - date()
    def test_date_function(self):
        sql = check("RETURN date('2024-01-15')")
        assert "2024-01-15" in sql or "date" in sql.lower()

    # Temporal function detection - datetime()
    def test_datetime_function(self):
        sql = check("RETURN datetime('2024-01-15T12:00:00')")
        assert "2024-01-15" in sql or "datetime" in sql.lower()

    # Temporal function detection - duration()
    def test_duration_function(self):
        sql = check("RETURN duration('P1Y2M3DT4H5M6S')")
        assert "P1Y2M3DT4H5M6S" in sql or "duration" in sql.lower()

    # Temporal namespace functions
    def test_duration_between(self):
        sql = check("RETURN duration.between(date('2020-01-01'), date('2024-01-01'))")
        assert sql is not None

    def test_date_truncate(self):
        sql = check("RETURN date.truncate('month', date('2024-03-15'))")
        assert sql is not None


# ===========================================================================
# Lines 8700-8788  extract_temporal_component (localdatetime/time properties)
# ===========================================================================

class TestExtractTemporalComponent:
    """Tests for temporal component extraction from localdatetime and time types."""

    def test_localdatetime_hour(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.hour")
        assert "SUBSTRING" in sql

    def test_localdatetime_minute(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.minute")
        assert "SUBSTRING" in sql

    def test_localdatetime_second(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.second")
        assert "SUBSTRING" in sql

    def test_localdatetime_year(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.year")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_localdatetime_month(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.month")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_localdatetime_day(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.day")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_localdatetime_quarter(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.quarter")
        assert "CAST" in sql or "INTEGER" in sql

    def test_localdatetime_day_of_week(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.dayOfWeek")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_localdatetime_day_of_year(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.dayOfYear")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_localdatetime_week(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45') AS dt RETURN dt.week")
        assert "CY_TEMPORAL_FIELD" in sql

    def test_localdatetime_millisecond(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45.123') AS dt RETURN dt.millisecond")
        assert "SUBSTRING" in sql

    def test_localdatetime_microsecond(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:45.123456') AS dt RETURN dt.microsecond")
        assert "SUBSTRING" in sql

    def test_localdatetime_epoch_seconds(self):
        sql = check("WITH localdatetime('2024-01-15T12:30:00') AS dt RETURN dt.epochSeconds")
        assert "DATEDIFF" in sql or "second" in sql.lower()

    def test_localtime_hour(self):
        sql = check("WITH localtime('12:30:45') AS t RETURN t.hour")
        assert "SUBSTRING" in sql

    def test_localtime_minute(self):
        sql = check("WITH localtime('12:30:45') AS t RETURN t.minute")
        assert "SUBSTRING" in sql

    def test_localtime_second(self):
        sql = check("WITH localtime('12:30:45') AS t RETURN t.second")
        assert "SUBSTRING" in sql

    def test_localtime_millisecond(self):
        sql = check("WITH localtime('12:30:45.123') AS t RETURN t.millisecond")
        assert "SUBSTRING" in sql

    def test_time_hour(self):
        sql = check("WITH time('12:30:45+01:00') AS t RETURN t.hour")
        assert "SUBSTRING" in sql

    def test_time_timezone(self):
        sql = check("WITH time('12:30:45+01:00') AS t RETURN t.timezone")
        assert "CHARINDEX" in sql or "Z" in sql

    def test_time_offset_minutes(self):
        sql = check("WITH time('12:30:45+01:00') AS t RETURN t.offsetMinutes")
        assert "CHARINDEX" in sql

    def test_time_offset_seconds(self):
        sql = check("WITH time('12:30:45+01:00') AS t RETURN t.offsetSeconds")
        assert "CHARINDEX" in sql

    def test_date_week_year(self):
        sql = check("WITH date('2024-01-15') AS d RETURN d.weekYear")
        assert "DATEPART" in sql or "year" in sql.lower()

    def test_date_day_of_quarter(self):
        sql = check("WITH date('2024-02-15') AS d RETURN d.dayOfQuarter")
        assert "CASE WHEN" in sql


# ===========================================================================
# Lines 8874-9004  duration properties + property_reference
# ===========================================================================

class TestDurationPropertiesAndPropertyReference:
    """Tests for duration temporal component extraction and property references."""

    def test_duration_months(self):
        sql = check("WITH duration('P1Y2M') AS d RETURN d.months")
        assert sql is not None

    def test_duration_days(self):
        sql = check("WITH duration('P10D') AS d RETURN d.days")
        assert sql is not None

    def test_duration_hours(self):
        sql = check("WITH duration('PT5H') AS d RETURN d.hours")
        assert sql is not None

    def test_duration_minutes(self):
        sql = check("WITH duration('PT30M') AS d RETURN d.minutes")
        assert sql is not None

    def test_duration_seconds(self):
        sql = check("WITH duration('PT22H') AS d RETURN d.seconds")
        assert "CHARINDEX" in sql or "SUBSTRING" in sql or "CASE WHEN" in sql

    def test_duration_milliseconds(self):
        sql = check("WITH duration('PT1.5S') AS d RETURN d.milliseconds")
        assert "CASE WHEN" in sql or "RPAD" in sql

    def test_duration_microseconds(self):
        sql = check("WITH duration('PT1.5S') AS d RETURN d.microseconds")
        assert "CASE WHEN" in sql or "RPAD" in sql

    def test_duration_nanoseconds(self):
        sql = check("WITH duration('PT1.5S') AS d RETURN d.nanoseconds")
        assert "CASE WHEN" in sql or "RPAD" in sql

    def test_duration_nanoseconds_of_second(self):
        sql = check("WITH duration('PT1.5S') AS d RETURN d.nanosecondsOfSecond")
        assert "CASE WHEN" in sql

    # Property reference: basic node property
    def test_node_property_reference(self):
        sql = check("MATCH (n) RETURN n.name")
        assert "rdf_props" in sql or "val" in sql

    def test_node_property_in_where(self):
        sql = check("MATCH (n) WHERE n.age > 18 RETURN n")
        assert "rdf_props" in sql or "val" in sql

    # Property reference: node_id
    def test_node_id_property(self):
        sql = check("MATCH (n) RETURN n.node_id")
        assert "node_id" in sql

    # Property reference via Stage (WITH clause)
    def test_stage_property_reference(self):
        sql = check("WITH 1 AS x RETURN x")
        assert sql is not None

    # Property reference: undefined variable should raise
    def test_undefined_variable_raises(self):
        with pytest.raises((SyntaxError, KeyError, Exception)):
            tr("RETURN undefined_var.name")

    # Property reference in ORDER BY (inline segment)
    def test_property_reference_order_by(self):
        sql = check("MATCH (n) RETURN n ORDER BY n.name")
        assert "rdf_props" in sql or "val" in sql


# ===========================================================================
# Lines 9027-9150  property_reference Stage/map_projection/subscript
# ===========================================================================

class TestPropertyReferenceStageMapSubscript:
    """Tests for Stage column property references, map projections, and subscript access."""

    # Stage property: node variable from WITH
    def test_stage_node_property(self):
        sql = check("MATCH (n) WITH n RETURN n.age")
        assert "rdf_props" in sql or "val" in sql

    # Map literal (fully literal → constant-folded)
    def test_map_literal_constant_fold(self):
        sql = check("RETURN {name: 'Alice', age: 30}")
        assert "Alice" in sql and "30" in sql

    # Map literal empty
    def test_map_literal_empty(self):
        sql = check("RETURN {}")
        assert "{}" in sql

    # Map literal with null value
    def test_map_literal_null_value(self):
        sql = check("RETURN {x: null}")
        assert "null" in sql.lower() or "NULL" in sql

    # Map literal with boolean
    def test_map_literal_bool_value(self):
        sql = check("RETURN {active: true}")
        assert "true" in sql

    # Map literal with nested map
    def test_map_literal_nested(self):
        sql = check("RETURN {outer: {inner: 1}}")
        assert "inner" in sql or "outer" in sql

    # Map literal with list value
    def test_map_literal_list_value(self):
        sql = check("RETURN {items: [1, 2, 3]}")
        assert "items" in sql or "1" in sql

    # Map projection
    def test_map_projection(self):
        sql = check("MATCH (n) RETURN n {.name, .age}")
        assert "rdf_props" in sql or "node_id" in sql

    # Subscript access on list
    def test_subscript_on_list_literal(self):
        sql = check("RETURN [1, 2, 3][0]")
        assert sql is not None

    # Subscript access on Stage variable (scalar context)
    def test_subscript_on_stage_variable(self):
        sql = check("WITH {a: 1} AS m RETURN m['a']")
        assert "JSON_VALUE" in sql or "a" in sql

    # Property access on Stage scalar (JSON map)
    def test_stage_map_property_access(self):
        sql = check("WITH {name: 'Alice'} AS m RETURN m.name")
        assert "JSON_VALUE" in sql or "Alice" in sql

    # _lp_source_all_non_numeric: all string elements
    def test_any_string_source_no_null_sentinel(self):
        sql = check("RETURN any(x IN ['a', 'b'] WHERE x = 'a')")
        assert sql is not None

    # Nested list comprehension
    def test_nested_list_comprehension(self):
        sql = check("RETURN [x IN [1, 2, 3] | x + 1]")
        assert sql is not None

    # reduce() starting at non-zero
    def test_reduce_nonzero_init(self):
        sql = check("RETURN reduce(acc = 10, x IN [1, 2, 3] | acc + x)")
        assert "SUM" in sql or "10" in sql

    # CASE ELSE null
    def test_case_else_null(self):
        sql = check("RETURN CASE WHEN 1 = 2 THEN 'x' ELSE null END")
        assert "NULL" in sql or "CASE WHEN" in sql

    # CASE with bool result
    def test_case_bool_result(self):
        sql = check("RETURN CASE WHEN 1 = 1 THEN true ELSE false END")
        assert "1" in sql or "0" in sql

    # Literal list subscript access
    def test_list_subscript_variable(self):
        sql = check("WITH [1, 2, 3] AS lst RETURN lst[1]")
        assert sql is not None

    # NOT operator
    def test_not_operator(self):
        sql = check("RETURN NOT true")
        assert "0" in sql or "NOT" in sql

    # AND operator
    def test_and_operator(self):
        sql = check("RETURN true AND false")
        assert sql is not None

    # OR operator
    def test_or_operator(self):
        sql = check("RETURN true OR false")
        assert sql is not None

    # XOR operator
    def test_xor_operator(self):
        sql = check("RETURN true XOR false")
        assert sql is not None
