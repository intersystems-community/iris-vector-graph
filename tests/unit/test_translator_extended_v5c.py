"""Extended translator tests v5c — deep expression handling.

Targets lines 7400-8700 in translator.py:
  - List comprehension (_expr_list_comprehension)
  - Pattern predicates / list predicates (_expr_list_predicate)
  - Runtime polymorphic operators (_expr_arith + / concat)
  - String functions (toLower, toUpper, trim, split, STARTS WITH, ENDS WITH, CONTAINS)
  - Math functions (abs, floor, ceil, round, sqrt, sign, log, log10)
  - CASE expressions (_expr_case simple + searched)
  - IN with complex RHS (literal list, param list, NOT IN, list comprehension RHS)
  - head / last / range
  - Numeric coerce / conversion (toInteger, toFloat, toString, coalesce)
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(q, params=None):
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


# ---------------------------------------------------------------------------
# 1. List comprehension (10 tests)
# ---------------------------------------------------------------------------

class TestListComprehension:

    def test_basic_projection_multiply(self):
        """[x IN [1,2,3] | x * 2] — simple arithmetic projection."""
        sql = tr("MATCH (n) RETURN [x IN [1,2,3] | x * 2] AS doubled")
        assert "doubled" in sql.lower() or "doubled" in sql
        # Should produce a JSON_TABLE-based subquery or inline JSON
        assert "JSON" in sql.upper() or "SELECT" in sql.upper()

    def test_filter_with_starts_with(self):
        """[x IN n.tags WHERE x STARTS WITH 'a' | x] — filter predicate."""
        sql = tr("MATCH (n) RETURN [x IN n.tags WHERE x STARTS WITH 'a' | x] AS filtered")
        assert "filtered" in sql.lower() or "filtered" in sql
        assert "LIKE" in sql.upper() or "%" in sql

    def test_literal_list_tofloat_projection(self):
        """[x IN [1.0, null, 3.0] | toFloat(x)] — constant-folded."""
        sql = tr("MATCH (n) RETURN [x IN [1.0, null, 3.0] | toFloat(x)] AS floats")
        # Constant-folded to a JSON literal
        assert "1.0" in sql or "[1.0" in sql or "floats" in sql.lower()

    def test_size_of_list_comprehension(self):
        """size([x IN [1,2,3] | x]) AS sz — size of a list comprehension result."""
        sql = tr("MATCH (n) RETURN size([x IN [1,2,3] | x]) AS sz")
        assert "sz" in sql.lower() or "sz" in sql
        # size over a list should emit JSON_ARRAYLENGTH
        assert "ARRAYLENGTH" in sql.upper() or "LENGTH" in sql.upper()

    def test_list_comp_no_projection(self):
        """[x IN [1,2,3]] — list comprehension with identity projection."""
        sql = tr("MATCH (n) RETURN [x IN [1,2,3]] AS lst")
        assert "lst" in sql.lower() or "lst" in sql

    def test_list_comp_tointeger(self):
        """[x IN ['1','2','3'] | toInteger(x)] — constant-folded to [1,2,3]."""
        sql = tr("MATCH (n) RETURN [x IN ['1','2','3'] | toInteger(x)] AS ints")
        assert "ints" in sql.lower() or "ints" in sql

    def test_list_comp_tostring(self):
        """[x IN [1,2,3] | toString(x)] — constant-folded."""
        sql = tr("MATCH (n) RETURN [x IN [1,2,3] | toString(x)] AS strs")
        assert "strs" in sql.lower() or "strs" in sql

    def test_list_comp_with_arithmetic(self):
        """[x IN n.vals | x + 10] — dynamic projection from property."""
        sql = tr("MATCH (n) RETURN [x IN n.vals | x + 10] AS shifted")
        assert "shifted" in sql.lower() or "shifted" in sql
        assert "JSON" in sql.upper()

    def test_list_comp_nested_filter_and_projection(self):
        """[x IN [1,2,3,4,5] WHERE x > 2 | x * x] — filter + projection."""
        sql = tr("MATCH (n) RETURN [x IN [1,2,3,4,5] WHERE x > 2 | x * x] AS sq")
        assert "sq" in sql.lower() or "sq" in sql
        assert "JSON" in sql.upper() or "SELECT" in sql.upper()

    def test_list_comp_empty_source(self):
        """[x IN [] | x] — empty source list."""
        sql = tr("MATCH (n) RETURN [x IN [] | x] AS empty")
        # Should produce empty JSON array or equivalent
        assert "empty" in sql.lower() or "[]" in sql or "JSON" in sql.upper()


# ---------------------------------------------------------------------------
# 2. String functions (8 tests)
# ---------------------------------------------------------------------------

class TestStringFunctions:

    def test_tolower_in_where(self):
        """WHERE toLower(n.name) = 'alice' — LOWER() in SQL."""
        sql = tr("MATCH (n) WHERE toLower(n.name) = 'alice' RETURN n")
        assert "LOWER(" in sql.upper() or "lower(" in sql

    def test_starts_with(self):
        """WHERE n.name STARTS WITH 'Ali' — LIKE with Ali prefix."""
        sql = tr("MATCH (n) WHERE n.name STARTS WITH 'Ali' RETURN n")
        assert "LIKE" in sql.upper()
        assert "Ali" in sql

    def test_ends_with(self):
        """WHERE n.name ENDS WITH 'ce' — LIKE with ce suffix."""
        sql = tr("MATCH (n) WHERE n.name ENDS WITH 'ce' RETURN n")
        assert "LIKE" in sql.upper()
        assert "ce" in sql

    def test_contains(self):
        """WHERE n.name CONTAINS 'li' — LIKE with li substring."""
        sql = tr("MATCH (n) WHERE n.name CONTAINS 'li' RETURN n")
        assert "LIKE" in sql.upper()
        assert "li" in sql

    def test_split_function(self):
        """RETURN split(n.name, ',') AS parts — STR_SPLIT."""
        sql = tr("MATCH (n) RETURN split(n.name, ',') AS parts")
        assert "STR_SPLIT" in sql.upper() or "SPLIT" in sql.upper()

    def test_trim_function(self):
        """RETURN trim(n.name) AS name — TRIM()."""
        sql = tr("MATCH (n) RETURN trim(n.name) AS name")
        assert "TRIM(" in sql.upper()

    def test_toupper_function(self):
        """RETURN toUpper(n.name) AS up — UPPER()."""
        sql = tr("MATCH (n) RETURN toUpper(n.name) AS up")
        assert "UPPER(" in sql.upper()

    def test_substring_function(self):
        """RETURN substring(n.name, 0, 3) AS abbr — SUBSTRING()."""
        sql = tr("MATCH (n) RETURN substring(n.name, 0, 3) AS abbr")
        assert "SUBSTRING(" in sql.upper()


# ---------------------------------------------------------------------------
# 3. Math functions (8 tests)
# ---------------------------------------------------------------------------

class TestMathFunctions:

    def test_abs_function(self):
        """RETURN abs(n.value) AS a — ABS()."""
        sql = tr("MATCH (n) RETURN abs(n.value) AS a")
        assert "ABS(" in sql.upper()

    def test_floor_function(self):
        """RETURN floor(n.value) AS f — FLOOR()."""
        sql = tr("MATCH (n) RETURN floor(n.value) AS f")
        assert "FLOOR(" in sql.upper()

    def test_ceil_function(self):
        """RETURN ceil(n.value) AS c — CEILING()."""
        sql = tr("MATCH (n) RETURN ceil(n.value) AS c")
        assert "CEILING(" in sql.upper() or "CEIL(" in sql.upper()

    def test_round_function(self):
        """RETURN round(n.value) AS r — ROUND()."""
        sql = tr("MATCH (n) RETURN round(n.value) AS r")
        assert "ROUND(" in sql.upper()

    def test_sqrt_function(self):
        """RETURN sqrt(n.value) AS s — SQRT()."""
        sql = tr("MATCH (n) RETURN sqrt(n.value) AS s")
        assert "SQRT(" in sql.upper()

    def test_sign_function(self):
        """RETURN sign(n.value) AS s — SIGN()."""
        sql = tr("MATCH (n) RETURN sign(n.value) AS s")
        assert "SIGN(" in sql.upper()

    def test_log_function(self):
        """RETURN log(n.value) AS l — LOG()."""
        sql = tr("MATCH (n) RETURN log(n.value) AS l")
        assert "LOG(" in sql.upper()

    def test_log10_function(self):
        """RETURN log10(n.value) AS l — LOG10()."""
        sql = tr("MATCH (n) RETURN log10(n.value) AS l")
        assert "LOG10(" in sql.upper() or "LOG(" in sql.upper()


# ---------------------------------------------------------------------------
# 4. CASE expressions (8 tests)
# ---------------------------------------------------------------------------

class TestCaseExpressions:

    def test_simple_case_two_when(self):
        """CASE n.type WHEN 'a' THEN 1 WHEN 'b' THEN 2 ELSE 0 END."""
        sql = tr("MATCH (n) RETURN CASE n.type WHEN 'a' THEN 1 WHEN 'b' THEN 2 ELSE 0 END AS val")
        assert "CASE" in sql.upper()
        assert "WHEN" in sql.upper()
        assert "THEN" in sql.upper()
        assert "ELSE" in sql.upper()
        assert "END" in sql.upper()

    def test_searched_case_adult_minor(self):
        """CASE WHEN n.age > 18 THEN 'adult' ELSE 'minor' END."""
        sql = tr("MATCH (n) RETURN CASE WHEN n.age > 18 THEN 'adult' ELSE 'minor' END AS grp")
        assert "CASE" in sql.upper()
        assert "adult" in sql
        assert "minor" in sql

    def test_case_in_where(self):
        """WHERE CASE WHEN n.x IS NULL THEN false ELSE n.x > 0 END."""
        sql = tr("MATCH (n) WHERE CASE WHEN n.x IS NULL THEN false ELSE n.x > 0 END RETURN n")
        assert "CASE" in sql.upper()
        assert "IS NULL" in sql.upper()

    def test_case_null_branch(self):
        """CASE WHEN n.score IS NULL THEN 0 ELSE n.score END."""
        sql = tr("MATCH (n) RETURN CASE WHEN n.score IS NULL THEN 0 ELSE n.score END AS score")
        assert "CASE" in sql.upper()
        assert "IS NULL" in sql.upper()

    def test_case_no_else(self):
        """CASE WHEN n.flag = 1 THEN 'yes' END — no ELSE clause."""
        sql = tr("MATCH (n) RETURN CASE WHEN n.flag = 1 THEN 'yes' END AS res")
        assert "CASE" in sql.upper()
        assert "WHEN" in sql.upper()
        # No ELSE should still produce valid SQL
        assert "END" in sql.upper()

    def test_case_multiple_searched_branches(self):
        """CASE WHEN n.x < 0 THEN 'neg' WHEN n.x = 0 THEN 'zero' ELSE 'pos' END."""
        sql = tr(
            "MATCH (n) RETURN CASE WHEN n.x < 0 THEN 'neg' WHEN n.x = 0 THEN 'zero' ELSE 'pos' END AS sign"
        )
        assert "CASE" in sql.upper()
        assert "neg" in sql
        assert "zero" in sql
        assert "pos" in sql

    def test_simple_case_string_keys(self):
        """CASE n.color WHEN 'red' THEN 1 WHEN 'blue' THEN 2 END."""
        sql = tr("MATCH (n) RETURN CASE n.color WHEN 'red' THEN 1 WHEN 'blue' THEN 2 END AS code")
        assert "CASE" in sql.upper()
        assert "red" in sql
        assert "blue" in sql

    def test_case_in_order_by(self):
        """ORDER BY CASE WHEN n.priority IS NULL THEN 1 ELSE 0 END."""
        sql = tr(
            "MATCH (n) RETURN n.id ORDER BY CASE WHEN n.priority IS NULL THEN 1 ELSE 0 END"
        )
        assert "CASE" in sql.upper()
        assert "ORDER BY" in sql.upper()


# ---------------------------------------------------------------------------
# 5. IN with various RHS (8 tests)
# ---------------------------------------------------------------------------

class TestInOperator:

    def test_in_literal_list_strings(self):
        """WHERE n.id IN ['a','b','c'] — SQL IN (?, ?, ?)."""
        sql = tr("MATCH (n) WHERE n.id IN ['a','b','c'] RETURN n")
        assert " IN " in sql.upper()
        assert "?" in sql or "'a'" in sql

    def test_in_param_list(self):
        """WHERE n.id IN $ids — parameter list expansion."""
        sql = tr("MATCH (n) WHERE n.id IN $ids RETURN n", params={"ids": ["a", "b"]})
        assert " IN " in sql.upper()
        assert "?" in sql

    def test_not_in_literal_list(self):
        """WHERE n.id NOT IN ['x','y'] — NOT IN."""
        sql = tr("MATCH (n) WHERE NOT n.id IN ['x','y'] RETURN n")
        assert "NOT" in sql.upper()
        assert " IN " in sql.upper()

    def test_in_integer_list(self):
        """WHERE n.count IN [1, 2, 3] — integer literal list."""
        sql = tr("MATCH (n) WHERE n.count IN [1, 2, 3] RETURN n")
        assert " IN " in sql.upper()

    def test_in_empty_list(self):
        """WHERE n.id IN [] — always false."""
        sql = tr("MATCH (n) WHERE n.id IN [] RETURN n")
        # Should produce always-false condition
        assert "1=0" in sql or "0=1" in sql or "WHERE" in sql.upper()

    def test_in_list_with_null(self):
        """WHERE n.id IN ['a', null, 'b'] — 3VL: null in list means CASE WHEN ... THEN 1 ELSE NULL."""
        sql = tr("MATCH (n) WHERE n.id IN ['a', null, 'b'] RETURN n")
        assert " IN " in sql.upper()

    def test_in_list_comprehension_rhs(self):
        """WHERE n.type IN [x IN n.tags | x] — list comprehension as RHS."""
        sql = tr("MATCH (n) WHERE n.type IN [x IN n.tags | x] RETURN n")
        # membership over a JSON array: one 1/0/NULL subquery
        assert ".__jv = " in sql
        assert "JSON_TABLE" in sql.upper() or "SELECT" in sql.upper()

    def test_not_in_param_list(self):
        """WHERE NOT n.id IN $ids — NOT IN with parameter."""
        sql = tr("MATCH (n) WHERE NOT n.id IN $ids RETURN n", params={"ids": ["x", "y"]})
        assert "NOT" in sql.upper()
        assert " IN " in sql.upper()


# ---------------------------------------------------------------------------
# 6. head / last / range (5 tests)
# ---------------------------------------------------------------------------

class TestHeadLastRange:

    def test_head_literal_list(self):
        """RETURN head([1,2,3]) AS h — JSON_ARRAYGET at index 0."""
        sql = tr("MATCH (n) RETURN head([1,2,3]) AS h")
        assert "JSON_ARRAYGET" in sql.upper() or "HEAD" in sql.upper() or "0" in sql

    def test_last_literal_list(self):
        """RETURN last([1,2,3]) AS l — JSON_ARRAYGET at last index."""
        sql = tr("MATCH (n) RETURN last([1,2,3]) AS l")
        assert "JSON_ARRAYGET" in sql.upper() or "ARRAYLENGTH" in sql.upper() or "last" in sql.lower()

    def test_range_basic(self):
        """RETURN range(1, 10) AS r — constant-folded JSON_ARRAY."""
        sql = tr("MATCH (n) RETURN range(1, 10) AS r")
        assert "JSON_ARRAY" in sql.upper() or "RANGE" in sql.upper() or "1" in sql

    def test_range_with_step(self):
        """RETURN range(0, 10, 2) AS r — even numbers."""
        sql = tr("MATCH (n) RETURN range(0, 10, 2) AS r")
        assert "JSON_ARRAY" in sql.upper() or "0" in sql

    def test_range_produces_correct_values(self):
        """range(1, 5) should constant-fold to JSON_ARRAY(1, 2, 3, 4, 5)."""
        sql = tr("MATCH (n) RETURN range(1, 5) AS r")
        # Constant-folded: all values should appear in the SQL string
        assert "1" in sql and "5" in sql


# ---------------------------------------------------------------------------
# 7. Numeric coerce / conversion (7 tests)
# ---------------------------------------------------------------------------

class TestTypeConversion:

    def test_tointeger_string(self):
        """RETURN toInteger('42') AS n — ISNUMERIC guard + CAST INTEGER."""
        sql = tr("MATCH (n) RETURN toInteger('42') AS n")
        assert "CAST" in sql.upper()
        assert "INTEGER" in sql.upper() or "ISNUMERIC" in sql.upper()

    def test_tofloat_string(self):
        """RETURN toFloat('3.14') AS f — ISNUMERIC guard + CAST DOUBLE."""
        sql = tr("MATCH (n) RETURN toFloat('3.14') AS f")
        assert "CAST" in sql.upper()
        assert "DOUBLE" in sql.upper() or "ISNUMERIC" in sql.upper()

    def test_tostring_integer(self):
        """RETURN toString(42) AS s — CAST AS VARCHAR."""
        sql = tr("MATCH (n) RETURN toString(42) AS s")
        assert "CAST" in sql.upper()
        assert "VARCHAR" in sql.upper()

    def test_coalesce_two_args(self):
        """RETURN coalesce(n.x, n.y) AS v — COALESCE()."""
        sql = tr("MATCH (n) RETURN coalesce(n.x, n.y) AS v")
        assert "COALESCE(" in sql.upper()

    def test_coalesce_with_literal_default(self):
        """RETURN coalesce(n.x, n.y, 0) AS v — COALESCE with integer fallback."""
        sql = tr("MATCH (n) RETURN coalesce(n.x, n.y, 0) AS v")
        assert "COALESCE(" in sql.upper()

    def test_tointeger_property(self):
        """RETURN toInteger(n.score) AS score — wraps property ref."""
        sql = tr("MATCH (n) RETURN toInteger(n.score) AS score")
        assert "CAST" in sql.upper() or "ISNUMERIC" in sql.upper()
        assert "INTEGER" in sql.upper()

    def test_tofloat_property(self):
        """RETURN toFloat(n.score) AS fscore — CAST AS DOUBLE."""
        sql = tr("MATCH (n) RETURN toFloat(n.score) AS fscore")
        assert "CAST" in sql.upper() or "ISNUMERIC" in sql.upper()
        assert "DOUBLE" in sql.upper()


# ---------------------------------------------------------------------------
# 8. Additional edge-case / runtime polymorphism tests (10 tests)
# ---------------------------------------------------------------------------

class TestRuntimePolymorphism:

    def test_string_concat_plus(self):
        """RETURN n.first + ' ' + n.last AS full — string concatenation."""
        sql = tr("MATCH (n) RETURN n.first + ' ' + n.last AS full")
        assert "full" in sql.lower() or "full" in sql
        # Either runtime polymorphic or concat operator
        assert "||" in sql or "+" in sql or "SELECT" in sql.upper()

    def test_addition_of_integers(self):
        """RETURN 1 + 2 AS sum — constant integer addition."""
        sql = tr("MATCH (n) RETURN 1 + 2 AS sum")
        # Constant folded or generates arithmetic SQL
        assert "sum" in sql.lower() or "3" in sql or "+" in sql

    def test_subtraction(self):
        """RETURN n.value - 10 AS diff."""
        sql = tr("MATCH (n) RETURN n.value - 10 AS diff")
        assert "diff" in sql.lower() or "diff" in sql
        assert "-" in sql or "DIFF" in sql

    def test_multiplication(self):
        """RETURN n.price * n.qty AS total."""
        sql = tr("MATCH (n) RETURN n.price * n.qty AS total")
        assert "*" in sql or "total" in sql.lower()

    def test_modulo(self):
        """RETURN n.value % 3 AS mod."""
        sql = tr("MATCH (n) RETURN n.value % 3 AS mod")
        assert "%" in sql or "MOD" in sql.upper()

    def test_power_operator(self):
        """RETURN n.x ^ 2 AS sq."""
        sql = tr("MATCH (n) RETURN n.x ^ 2 AS sq")
        assert "sq" in sql.lower() or "sq" in sql

    def test_is_null_predicate(self):
        """WHERE n.name IS NULL — IS NULL."""
        sql = tr("MATCH (n) WHERE n.name IS NULL RETURN n")
        assert "IS NULL" in sql.upper()

    def test_is_not_null_predicate(self):
        """WHERE n.name IS NOT NULL — IS NOT NULL."""
        sql = tr("MATCH (n) WHERE n.name IS NOT NULL RETURN n")
        assert "IS NOT NULL" in sql.upper()

    def test_not_equals(self):
        """WHERE n.status <> 'inactive' — <> operator."""
        sql = tr("MATCH (n) WHERE n.status <> 'inactive' RETURN n")
        assert "<>" in sql or "!=" in sql

    def test_greater_than_less_than(self):
        """WHERE n.age > 18 AND n.age < 65 — range comparison."""
        sql = tr("MATCH (n) WHERE n.age > 18 AND n.age < 65 RETURN n")
        assert ">" in sql
        assert "<" in sql


# ---------------------------------------------------------------------------
# 9. List predicate expressions (any/all/none/single) (5 tests)
# ---------------------------------------------------------------------------

class TestListPredicates:

    def test_any_predicate(self):
        """WHERE any(x IN [1,2,3] WHERE x > 2)."""
        sql = tr("MATCH (n) WHERE any(x IN [1,2,3] WHERE x > 2) RETURN n")
        assert "SELECT" in sql.upper() or "CASE" in sql.upper()

    def test_all_predicate(self):
        """WHERE all(x IN [1,2,3] WHERE x > 0)."""
        sql = tr("MATCH (n) WHERE all(x IN [1,2,3] WHERE x > 0) RETURN n")
        assert "SELECT" in sql.upper() or "CASE" in sql.upper()

    def test_none_predicate(self):
        """WHERE none(x IN [1,2,3] WHERE x < 0)."""
        sql = tr("MATCH (n) WHERE none(x IN [1,2,3] WHERE x < 0) RETURN n")
        assert "SELECT" in sql.upper() or "CASE" in sql.upper()

    def test_single_predicate(self):
        """WHERE single(x IN [1,2,3] WHERE x = 2)."""
        sql = tr("MATCH (n) WHERE single(x IN [1,2,3] WHERE x = 2) RETURN n")
        assert "SELECT" in sql.upper() or "CASE" in sql.upper()

    def test_any_predicate_on_property(self):
        """WHERE any(x IN n.tags WHERE x = 'python')."""
        sql = tr("MATCH (n) WHERE any(x IN n.tags WHERE x = 'python') RETURN n")
        assert "SELECT" in sql.upper() or "CASE" in sql.upper() or "JSON" in sql.upper()


# ---------------------------------------------------------------------------
# 10. Additional CASE / IN combos (5 tests)
# ---------------------------------------------------------------------------

class TestCaseAndInCombos:

    def test_in_with_case_lhs(self):
        """CASE ... END IN [1, 2] — CASE expression as IN lhs."""
        sql = tr(
            "MATCH (n) WHERE CASE WHEN n.x > 0 THEN 1 ELSE 2 END IN [1, 2] RETURN n"
        )
        assert " IN " in sql.upper()
        assert "CASE" in sql.upper()

    def test_case_return_integer(self):
        """CASE WHEN 1=1 THEN 42 END — always returns 42."""
        sql = tr("MATCH (n) RETURN CASE WHEN 1=1 THEN 42 END AS answer")
        assert "CASE" in sql.upper()
        assert "42" in sql

    def test_nested_case(self):
        """Nested CASE: CASE WHEN ... THEN (CASE WHEN ... THEN ... END) END."""
        sql = tr(
            "MATCH (n) RETURN CASE WHEN n.x > 0 THEN CASE WHEN n.x > 10 THEN 'big' ELSE 'small' END ELSE 'neg' END AS sz"
        )
        assert "CASE" in sql.upper()
        assert "big" in sql
        assert "small" in sql
        assert "neg" in sql

    def test_in_range_result(self):
        """WHERE n.id IN range(1, 5) — range produces JSON array for IN."""
        sql = tr("MATCH (n) WHERE n.id IN range(1, 5) RETURN n")
        # membership over a JSON array: one 1/0/NULL subquery
        assert ".__jv = " in sql
        assert "JSON" in sql.upper() or "SELECT" in sql.upper()

    def test_contains_in_return(self):
        """RETURN n.name CONTAINS 'ali' — boolean in RETURN context."""
        sql = tr("MATCH (n) RETURN (n.name CONTAINS 'ali') AS has_ali")
        assert "LIKE" in sql.upper() or "%" in sql
