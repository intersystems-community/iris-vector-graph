"""
Tests for Cypher parser/translator bugs reported by the posos project.

Covers:
  1. Boolean literal support (TRUE, FALSE, NULL)
  2. Parameterized SKIP/LIMIT ($offset, $limit)
  3. String function translation (toLower → LOWER, toUpper → UPPER)
"""

import pytest
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher.translator import translate_to_sql


# ---------------------------------------------------------------------------
# Bug 1: Boolean literals
# ---------------------------------------------------------------------------

class TestBooleanLiterals:
    def test_true_literal_in_where(self):
        """WHERE n.flag = TRUE should parse and translate to = 1"""
        q = parse_query("MATCH (n:Drug) WHERE n.is_primary = TRUE RETURN n")
        sql = translate_to_sql(q)
        assert "= 1" in sql.sql

    def test_false_literal_in_where(self):
        """WHERE n.flag = FALSE should parse and translate to = 0"""
        q = parse_query("MATCH (n:Drug) WHERE n.active = FALSE RETURN n")
        sql = translate_to_sql(q)
        assert "= 0" in sql.sql

    def test_null_literal_in_is_null(self):
        """WHERE n.prop IS NULL should work (NULL used in IS NULL context)"""
        q = parse_query("MATCH (n:Drug) WHERE n.deleted IS NULL RETURN n")
        sql = translate_to_sql(q)
        assert "IS NULL" in sql.sql

    def test_true_lowercase(self):
        """true (lowercase) should also parse correctly"""
        q = parse_query("MATCH (n:Drug) WHERE n.enabled = true RETURN n")
        sql = translate_to_sql(q)
        assert "= 1" in sql.sql

    def test_false_lowercase(self):
        """false (lowercase) should also parse correctly"""
        q = parse_query("MATCH (n:Drug) WHERE n.enabled = false RETURN n")
        sql = translate_to_sql(q)
        assert "= 0" in sql.sql

    def test_not_false(self):
        """NOT FALSE should parse and translate"""
        q = parse_query("MATCH (n:Drug) WHERE NOT n.deleted = FALSE RETURN n")
        sql = translate_to_sql(q)
        assert sql.sql  # just verify it doesn't raise

    def test_boolean_with_and(self):
        """Boolean combined with AND should work"""
        q = parse_query("MATCH (n:Drug) WHERE n.active = TRUE AND n.approved = TRUE RETURN n")
        sql = translate_to_sql(q)
        assert sql.sql.count("= 1") == 2


# ---------------------------------------------------------------------------
# Bug 2: Parameterized SKIP/LIMIT
# ---------------------------------------------------------------------------

class TestParameterizedPagination:
    def test_skip_with_parameter(self):
        """SKIP $offset should resolve from params dict"""
        q = parse_query("MATCH (n:Drug) RETURN n SKIP $offset")
        sql = translate_to_sql(q, params={"offset": 10})
        assert "OFFSET 10" in sql.sql

    def test_limit_with_parameter(self):
        """LIMIT $limit should resolve from params dict"""
        q = parse_query("MATCH (n:Drug) RETURN n LIMIT $limit")
        sql = translate_to_sql(q, params={"limit": 25})
        assert "LIMIT 25" in sql.sql

    def test_skip_and_limit_both_parameterized(self):
        """SKIP $offset LIMIT $limit — both params should resolve"""
        q = parse_query("MATCH (n:Drug) RETURN n SKIP $offset LIMIT $limit")
        sql = translate_to_sql(q, params={"offset": 20, "limit": 50})
        assert "LIMIT 50" in sql.sql
        assert "OFFSET 20" in sql.sql

    def test_integer_literal_skip_still_works(self):
        """Integer literal SKIP/LIMIT should still work unchanged"""
        q = parse_query("MATCH (n:Drug) RETURN n SKIP 5 LIMIT 10")
        sql = translate_to_sql(q)
        assert "LIMIT 10" in sql.sql
        assert "OFFSET 5" in sql.sql

    def test_missing_param_raises(self):
        """Referencing an undefined parameter in SKIP should raise ValueError"""
        q = parse_query("MATCH (n:Drug) RETURN n SKIP $undefined")
        with pytest.raises(ValueError, match="Parameter.*undefined.*not provided"):
            translate_to_sql(q, params={})

    def test_param_is_zero(self):
        """SKIP $offset with offset=0 should produce OFFSET 0"""
        q = parse_query("MATCH (n:Drug) RETURN n SKIP $offset LIMIT $limit")
        sql = translate_to_sql(q, params={"offset": 0, "limit": 100})
        assert "OFFSET 0" in sql.sql
        assert "LIMIT 100" in sql.sql

    def test_no_sql_injection_via_param(self):
        """Pagination params must be cast to int — non-numeric values should raise"""
        q = parse_query("MATCH (n:Drug) RETURN n LIMIT $limit")
        with pytest.raises((ValueError, TypeError)):
            translate_to_sql(q, params={"limit": "0; DROP TABLE nodes;--"})


# ---------------------------------------------------------------------------
# Bug 3: String function translation
# ---------------------------------------------------------------------------

class TestStringFunctionTranslation:
    def test_tolower_translates_to_lower(self):
        """toLower() should translate to SQL LOWER()"""
        q = parse_query("MATCH (n:Drug) WHERE toLower(n.name) CONTAINS $term RETURN n")
        sql = translate_to_sql(q, params={"term": "aspirin"})
        assert "LOWER(" in sql.sql
        assert "TOLOWER" not in sql.sql

    def test_toupper_translates_to_upper(self):
        """toUpper() should translate to SQL UPPER()"""
        q = parse_query("MATCH (n:Drug) WHERE toUpper(n.code) = $code RETURN n")
        sql = translate_to_sql(q, params={"code": "ABC"})
        assert "UPPER(" in sql.sql
        assert "TOUPPER" not in sql.sql

    def test_tolower_case_insensitive_function_name(self):
        """TOLOWER (uppercase) should also map to LOWER"""
        q = parse_query("MATCH (n:Drug) WHERE TOLOWER(n.name) = $name RETURN n")
        sql = translate_to_sql(q, params={"name": "aspirin"})
        assert "LOWER(" in sql.sql

    def test_tolower_in_return(self):
        """toLower() should work in RETURN clause too"""
        q = parse_query("MATCH (n:Drug) RETURN toLower(n.name)")
        sql = translate_to_sql(q)
        assert "LOWER(" in sql.sql

    def test_trim_translates(self):
        """trim() should translate to TRIM()"""
        q = parse_query("MATCH (n:Drug) WHERE trim(n.name) = $name RETURN n")
        sql = translate_to_sql(q, params={"name": "aspirin"})
        assert "TRIM(" in sql.sql

    def test_size_translates_to_length(self):
        """size() should translate to LENGTH()"""
        q = parse_query("MATCH (n:Drug) WHERE size(n.name) > 3 RETURN n")
        sql = translate_to_sql(q)
        assert "LENGTH(" in sql.sql

    def test_unknown_function_uppercased(self):
        """Unknown functions should still be uppercased as a best-effort fallback"""
        q = parse_query("MATCH (n:Drug) RETURN someCustomFn(n.name)")
        sql = translate_to_sql(q)
        assert "SOMECUSTOMFN(" in sql.sql
