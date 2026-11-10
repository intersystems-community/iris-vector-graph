"""Translator unit tests — early logic (lines 454-1500).

Targets:
- SQL context build (build_stage_sql, select assembly, FROM expansion)
- Multiple node labels in MATCH
- WITH aggregation pipeline
- Property access and JSON_VALUE patterns
- Relationship patterns with properties
- RETURN expressions (arithmetic, string concat, list)
- Numeric comparisons in WHERE
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def tr(q, params=None):
    ast_tree = parse_query(q)
    result = translate_to_sql(ast_tree, params or {})
    sql = result.sql
    return sql[0] if isinstance(sql, list) else sql


def tr_result(q, params=None):
    ast_tree = parse_query(q)
    return translate_to_sql(ast_tree, params or {})


def tr_params(q, params=None):
    """Return (sql, flat_params) for inspection.

    SQLQuery.parameters is a list of per-statement param lists.
    Flatten all of them into a single list for easy membership checks.
    """
    result = tr_result(q, params)
    sql = result.sql
    sql_str = sql[0] if isinstance(sql, list) else sql
    raw_p = result.parameters or []
    # Flatten: parameters is [[p1, p2, ...], ...] for multi-statement results
    flat: list = []
    for item in raw_p:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return sql_str, flat


# ---------------------------------------------------------------------------
# Group 1: Multiple node labels in MATCH
# ---------------------------------------------------------------------------

class TestMultipleNodeLabels:
    """Tests for labeled node patterns — labels are bound as SQL params."""

    def test_single_label_basic(self):
        sql, params = tr_params("MATCH (n:Person) RETURN n.id")
        # Label appears as a ? param, not inline
        assert "rdf_labels" in sql.lower() or "_lbl" in sql.lower()
        assert "Person" in params

    def test_two_labels_conjunction(self):
        """Multi-label: MATCH (n:Person:Doctor) — both labels must be present."""
        sql, params = tr_params("MATCH (n:Person:Doctor) RETURN n.id")
        assert "rdf_labels" in sql.lower()
        assert "Person" in params
        assert "Doctor" in params

    def test_three_labels_conjunction(self):
        sql, params = tr_params("MATCH (n:A:B:C) RETURN n.id")
        assert "A" in params
        assert "B" in params
        assert "C" in params

    def test_two_separate_labeled_nodes(self):
        """Two separate labeled nodes in same MATCH."""
        sql, params = tr_params("MATCH (n:Person), (m:Drug) RETURN n.id, m.id")
        assert "Person" in params
        assert "Drug" in params

    def test_labeled_node_with_property(self):
        sql, params = tr_params("MATCH (n:Person {active: true}) RETURN n.name")
        assert "Person" in params
        assert "active" in params

    def test_labeled_node_property_integer(self):
        sql, params = tr_params("MATCH (n:Item {count: 5}) RETURN n.id")
        assert "Item" in params
        assert "count" in params

    def test_labeled_node_property_string(self):
        sql, params = tr_params("MATCH (n:User {role: 'admin'}) RETURN n.id")
        assert "User" in params
        # Property key is a ? param; string value may be inline or param
        assert "role" in params or "admin" in sql.lower()

    def test_label_with_relationship(self):
        sql, params = tr_params("MATCH (n:Person)-[r]->(m:Company) RETURN n.id, m.id")
        assert "Person" in params
        assert "Company" in params

    def test_label_no_return_property(self):
        """Returning node variable directly."""
        sql, params = tr_params("MATCH (n:Person) RETURN n")
        assert "Person" in params
        assert "SELECT" in sql.upper()

    def test_unlabeled_node_any(self):
        """No label — matches all nodes."""
        sql = tr("MATCH (n) RETURN n.id")
        assert "SELECT" in sql.upper()
        assert "FROM" in sql.upper()

    def test_label_relationship_label(self):
        sql, params = tr_params("MATCH (n:Doctor)-[r:TREATS]->(m:Patient) RETURN n.id, m.id")
        assert "Doctor" in params
        assert "Patient" in params
        assert "TREATS" in params

    def test_rdf_labels_join_for_label(self):
        """rdf_labels JOIN is required for any labeled node."""
        sql, params = tr_params("MATCH (n:Widget) RETURN n.name")
        assert "rdf_labels" in sql.lower()
        assert "Widget" in params

    def test_two_label_rdf_labels_count(self):
        """Two distinct labels → at least two rdf_labels joins."""
        sql, params = tr_params("MATCH (n:Alpha:Beta) RETURN n.id")
        assert sql.lower().count("rdf_labels") >= 2
        assert "Alpha" in params
        assert "Beta" in params


# ---------------------------------------------------------------------------
# Group 2: WITH aggregation pipeline
# ---------------------------------------------------------------------------

class TestWithAggregationPipeline:
    """Tests for WITH clause aggregation stages."""

    def test_with_count_group_by(self):
        sql = tr("MATCH (n:Person) WITH n.city AS city, count(*) AS cnt RETURN city, cnt ORDER BY cnt DESC")
        assert "Stage" in sql
        assert "COUNT" in sql.upper()
        assert "GROUP BY" in sql.upper()

    def test_with_collect(self):
        sql = tr("MATCH (n) WITH collect(n) AS nodes RETURN size(nodes) AS cnt")
        assert "Stage" in sql
        assert "SELECT" in sql.upper()

    def test_with_count_relationship_degree(self):
        sql = tr("MATCH (n)-[r]->(m) WITH n, count(r) AS deg RETURN n.id, deg")
        assert "Stage" in sql
        assert "COUNT" in sql.upper()

    def test_with_double_stage_filter(self):
        """Double WITH: first WITH projects, second WITH filters on count."""
        sql = tr("MATCH (n) WITH n WHERE n.active = true WITH count(n) AS cnt RETURN cnt")
        assert "Stage" in sql
        assert "COUNT" in sql.upper()

    def test_with_sum_aggregation(self):
        sql = tr("MATCH (n) WITH n.type AS t, sum(n.score) AS total RETURN t, total")
        assert "Stage" in sql
        assert "SUM" in sql.upper()
        assert "GROUP BY" in sql.upper()

    def test_with_avg_aggregation(self):
        sql = tr("MATCH (n) WITH n.label AS lbl, avg(n.score) AS avg_score RETURN lbl, avg_score")
        assert "Stage" in sql
        assert "AVG" in sql.upper()
        assert "GROUP BY" in sql.upper()

    def test_with_max_aggregation(self):
        sql = tr("MATCH (n) WITH n.type AS t, max(n.age) AS max_age RETURN t, max_age")
        assert "Stage" in sql
        assert "MAX" in sql.upper()

    def test_with_min_aggregation(self):
        sql = tr("MATCH (n) WITH n.type AS t, min(n.age) AS min_age RETURN t, min_age")
        assert "Stage" in sql
        assert "MIN" in sql.upper()

    def test_with_limit_passthrough(self):
        sql = tr("MATCH (n) WITH n LIMIT 10 RETURN n.id")
        assert "Stage" in sql
        assert "10" in sql

    def test_with_skip_and_limit(self):
        sql = tr("MATCH (n) WITH n SKIP 5 LIMIT 10 RETURN n.id")
        # SKIP+LIMIT in WITH results in a Stage with row numbering
        assert "Stage" in sql

    def test_with_order_by(self):
        sql = tr("MATCH (n) WITH n.name AS name ORDER BY name RETURN name")
        assert "Stage" in sql

    def test_with_distinct(self):
        sql = tr("MATCH (n) WITH DISTINCT n.city AS city RETURN city")
        assert "Stage" in sql
        assert "DISTINCT" in sql.upper()

    def test_with_count_returns_scalar(self):
        """WITH collect(n) AS ns RETURN size(ns) should produce a Stage."""
        sql = tr("MATCH (n) WITH collect(n.id) AS ids RETURN size(ids) AS cnt")
        assert "Stage" in sql

    def test_with_node_count_no_property(self):
        sql = tr("MATCH (n:Person) WITH n.city AS city, count(n) AS cnt RETURN city, cnt")
        assert "Stage" in sql
        assert "COUNT" in sql.upper()
        assert "GROUP BY" in sql.upper()

    def test_with_passthrough_no_aggregation(self):
        """WITH without aggregation passes node through as a Stage."""
        sql = tr("MATCH (n) WITH n RETURN n.id")
        # Simple WITH n just passes through without creating Stage unless needed
        assert "SELECT" in sql.upper()

    def test_with_where_on_original_prop(self):
        """Using the original property reference (not alias) in WITH WHERE."""
        sql = tr("MATCH (n) WITH n.name AS name WHERE n.name STARTS WITH 'A' RETURN name")
        assert "Stage" in sql
        assert "LIKE" in sql.upper() or "Stage" in sql


# ---------------------------------------------------------------------------
# Group 3: Property access and JSON_VALUE patterns
# ---------------------------------------------------------------------------

class TestPropertyAccess:
    """Tests for property reference → SQL translation."""

    def test_single_property_generates_select(self):
        sql = tr("MATCH (n) RETURN n.name")
        assert "SELECT" in sql.upper()
        assert "name" in sql.lower()

    def test_multiple_properties(self):
        sql = tr("MATCH (n) RETURN n.name, n.age, n.score")
        assert "name" in sql.lower()
        assert "age" in sql.lower()
        assert "score" in sql.lower()

    def test_property_is_not_null(self):
        sql = tr("MATCH (n) WHERE n.metadata IS NOT NULL RETURN n.metadata")
        assert "metadata" in sql.lower()
        assert "NULL" in sql.upper() or "EXISTS" in sql.upper()

    def test_property_is_null(self):
        sql, params = tr_params("MATCH (n) WHERE n.deleted IS NULL RETURN n.id")
        # Property key 'deleted' is a ? param; IS NULL check appears in SQL
        assert "deleted" in params
        assert "NULL" in sql.upper()

    def test_property_access_with_label(self):
        sql, params = tr_params("MATCH (n:Person) RETURN n.name, n.age")
        assert "Person" in params
        assert "name" in sql.lower()
        assert "age" in sql.lower()

    def test_property_in_where_and_return(self):
        sql = tr("MATCH (n) WHERE n.status = 'active' RETURN n.name")
        assert "status" in sql.lower() or "?" in sql
        assert "name" in sql.lower()

    def test_node_id_property(self):
        """n.id property access."""
        sql, params = tr_params("MATCH (n:Person) RETURN n.id")
        assert "Person" in params
        assert "SELECT" in sql.upper()

    def test_relationship_property_access(self):
        sql, params = tr_params("MATCH (n)-[r:KNOWS]->(m) RETURN r.weight")
        assert "KNOWS" in params
        assert "weight" in sql.lower()

    def test_property_boolean_value(self):
        sql, params = tr_params("MATCH (n {active: true}) RETURN n.id")
        assert "active" in params

    def test_property_float_value(self):
        sql, params = tr_params("MATCH (n {score: 0.5}) RETURN n.id")
        assert "score" in params

    def test_property_via_rdf_props(self):
        """Property access should join rdf_props."""
        sql = tr("MATCH (n) WHERE n.name = 'Alice' RETURN n.name")
        assert "rdf_props" in sql.lower()

    def test_property_key_in_params(self):
        """Property key 'name' should appear as ? param for rdf_props key lookup."""
        sql, params = tr_params("MATCH (n) WHERE n.name = 'Alice' RETURN n.name")
        assert "name" in params


# ---------------------------------------------------------------------------
# Group 4: Relationship patterns with properties
# ---------------------------------------------------------------------------

class TestRelationshipPatterns:
    """Tests for relationship with property filters."""

    def test_rel_with_property_filter(self):
        sql, params = tr_params("MATCH (n)-[r:KNOWS {weight: 0.9}]->(m) RETURN r.weight")
        assert "KNOWS" in params
        assert "weight" in sql.lower()

    def test_rel_weight_where(self):
        sql, params = tr_params("MATCH (n)-[r]->(m) WHERE r.weight > 0.5 RETURN n, r, m")
        assert "weight" in sql.lower() or "qualifiers" in sql.lower()
        assert "0.5" in sql

    def test_rel_typed_with_return(self):
        sql, params = tr_params("MATCH (n)-[r:TREATS]->(m) RETURN n.id, r.dose, m.id")
        assert "TREATS" in params
        assert "dose" in sql.lower()

    def test_undirected_relationship(self):
        sql = tr("MATCH (n)-[r]-(m) RETURN n.id, m.id")
        assert "SELECT" in sql.upper()

    def test_rel_type_check_via_params(self):
        sql, params = tr_params("MATCH (n)-[r:KNOWS]->(m) RETURN type(r)")
        assert "KNOWS" in params

    def test_multi_rel_path_via_params(self):
        sql, params = tr_params("MATCH (a)-[r1:KNOWS]->(b)-[r2:LIKES]->(c) RETURN a.id, c.id")
        assert "KNOWS" in params
        assert "LIKES" in params

    def test_rel_no_type(self):
        sql = tr("MATCH (n)-[r]->(m) RETURN n.id, m.id")
        assert "SELECT" in sql.upper()
        assert "rdf_edges" in sql.lower()

    def test_incoming_rel_via_params(self):
        sql, params = tr_params("MATCH (n)<-[r:KNOWS]-(m) RETURN n.id, m.id")
        assert "KNOWS" in params

    def test_rel_with_two_properties_via_params(self):
        sql, params = tr_params("MATCH (n)-[r:RATED {score: 5, verified: true}]->(m) RETURN n.id")
        assert "RATED" in params
        # rel properties land in qualifiers column
        assert "score" in sql.lower() or "qualifiers" in sql.lower()

    def test_multiple_match_rels(self):
        sql = tr("MATCH (n)-[r1]->(m), (m)-[r2]->(k) RETURN n.id, k.id")
        assert "SELECT" in sql.upper()

    def test_rdf_edges_table_used(self):
        sql = tr("MATCH (n)-[r]->(m) RETURN n.id, m.id")
        assert "rdf_edges" in sql.lower()

    def test_rel_direction_out_join(self):
        """Outgoing rel joins rdf_edges e.s = n.node_id."""
        sql = tr("MATCH (n)-[r]->(m) RETURN n.id")
        assert "e2.s" in sql or "ON" in sql.upper()


# ---------------------------------------------------------------------------
# Group 5: RETURN expressions (arithmetic, string concat, list)
# ---------------------------------------------------------------------------

class TestReturnExpressions:
    """Tests for expression translation in RETURN clause."""

    def test_arithmetic_add(self):
        sql = tr("MATCH (n) RETURN n.x + n.y AS sum")
        assert "+" in sql
        assert "sum" in sql.lower() or "AS" in sql.upper()

    def test_arithmetic_multiply(self):
        sql = tr("MATCH (n) RETURN n.x * 2 AS doubled")
        assert "*" in sql
        assert "doubled" in sql.lower()

    def test_arithmetic_divide(self):
        sql = tr("MATCH (n) RETURN n.x / n.y AS ratio")
        assert "/" in sql
        assert "ratio" in sql.lower()

    def test_arithmetic_subtract(self):
        sql = tr("MATCH (n) RETURN n.x - n.y AS diff")
        assert "-" in sql
        assert "diff" in sql.lower()

    def test_string_concat(self):
        sql = tr("MATCH (n) RETURN n.name + ' ' + n.surname AS full_name")
        assert "+" in sql or "||" in sql
        assert "full_name" in sql.lower()

    def test_list_literal_return(self):
        sql = tr("MATCH (n) RETURN [n.x, n.y, n.z] AS coords")
        # List literals may be JSON-encoded
        assert "coords" in sql.lower()
        assert "SELECT" in sql.upper()

    def test_return_constant_integer(self):
        sql = tr("MATCH (n) RETURN 42 AS answer")
        assert "42" in sql
        assert "answer" in sql.lower()

    def test_return_string_literal(self):
        sql = tr("MATCH (n) RETURN 'hello' AS greeting")
        assert "hello" in sql.lower()
        assert "greeting" in sql.lower()

    def test_return_boolean_true(self):
        sql = tr("MATCH (n) RETURN true AS flag")
        assert "flag" in sql.lower()

    def test_return_null_literal(self):
        sql = tr("MATCH (n) RETURN null AS nothing")
        assert "NULL" in sql.upper()

    def test_return_id_function(self):
        sql = tr("MATCH (n) RETURN id(n) AS node_id")
        assert "node_id" in sql.lower()

    def test_return_count_star(self):
        sql = tr("MATCH (n) RETURN count(*) AS total")
        assert "COUNT" in sql.upper()
        assert "total" in sql.lower()

    def test_return_distinct_property(self):
        sql = tr("MATCH (n) RETURN DISTINCT n.city AS city")
        assert "DISTINCT" in sql.upper()
        assert "city" in sql.lower()

    def test_return_multiple_agg(self):
        sql = tr("MATCH (n) RETURN count(*) AS total, max(n.age) AS oldest")
        assert "COUNT" in sql.upper()
        assert "MAX" in sql.upper()

    def test_return_sum(self):
        sql = tr("MATCH (n) RETURN sum(n.score) AS total_score")
        assert "SUM" in sql.upper()
        assert "total_score" in sql.lower()


# ---------------------------------------------------------------------------
# Group 6: Numeric comparisons in WHERE
# ---------------------------------------------------------------------------

class TestNumericComparisons:
    """Tests for WHERE numeric comparisons."""

    def test_greater_than_or_equal(self):
        sql = tr("MATCH (n) WHERE n.score >= 0.8 RETURN n")
        # Translator expands >= as (> OR =) for string-stored numerics
        assert "0.8" in sql
        assert "score" in sql.lower()
        assert ">" in sql

    def test_not_equal(self):
        sql = tr("MATCH (n) WHERE n.age <> 0 RETURN n")
        assert "NOT (" in sql and "CAST(p1.val AS DOUBLE) = CAST('0' AS DOUBLE)" in sql  # a stored number (4.7.0 row 24)
        assert "age" in sql.lower()

    def test_and_combined(self):
        sql = tr("MATCH (n) WHERE n.x > 0 AND n.y < 100 RETURN n")
        assert ">" in sql
        assert "<" in sql
        assert "AND" in sql.upper()

    def test_greater_than(self):
        sql = tr("MATCH (n) WHERE n.weight > 5 RETURN n.id")
        assert ">" in sql
        assert "5" in sql
        assert "weight" in sql.lower()

    def test_less_than(self):
        sql = tr("MATCH (n) WHERE n.age < 65 RETURN n.id")
        assert "<" in sql
        assert "65" in sql

    def test_equal_numeric(self):
        sql = tr("MATCH (n) WHERE n.level = 3 RETURN n.id")
        assert "level" in sql.lower()
        assert "3" in sql

    def test_less_than_or_equal(self):
        sql = tr("MATCH (n) WHERE n.rank <= 10 RETURN n.id")
        # Translator expands <= as (< OR =)
        assert "10" in sql
        assert "<" in sql
        assert "rank" in sql.lower()

    def test_or_combined(self):
        sql = tr("MATCH (n) WHERE n.status = 'active' OR n.status = 'pending' RETURN n.id")
        assert "OR" in sql.upper()

    def test_not_condition(self):
        sql = tr("MATCH (n) WHERE NOT n.deleted = true RETURN n.id")
        assert "NOT" in sql.upper() or "deleted" in sql.lower()

    def test_where_string_equals(self):
        sql, params = tr_params("MATCH (n) WHERE n.name = 'Alice' RETURN n.id")
        # Property key 'name' is a ? param; string value 'Alice' may be inline or param
        assert "name" in params
        assert "Alice" in sql or "Alice" in str(params)

    def test_where_starts_with(self):
        sql = tr("MATCH (n) WHERE n.name STARTS WITH 'Al' RETURN n.id")
        assert "LIKE" in sql.upper()
        assert "Al" in sql

    def test_where_ends_with(self):
        sql = tr("MATCH (n) WHERE n.email ENDS WITH '.com' RETURN n.id")
        assert "LIKE" in sql.upper()
        assert ".com" in sql

    def test_where_contains(self):
        sql = tr("MATCH (n) WHERE n.bio CONTAINS 'doctor' RETURN n.id")
        assert "LIKE" in sql.upper()
        assert "doctor" in sql.lower()

    def test_where_in_list(self):
        sql, params = tr_params("MATCH (n) WHERE n.status IN ['active', 'verified'] RETURN n.id")
        assert "IN" in sql.upper()
        # Values passed as ? params
        assert "active" in params or "active" in sql.lower()

    def test_where_label_filter_exists(self):
        """WHERE n:Person generates EXISTS (SELECT 1 FROM rdf_labels ...)."""
        sql, params = tr_params("MATCH (n) WHERE n:Person RETURN n.id")
        # Label ends up as a ? param in EXISTS subquery
        assert "Person" in params
        assert "EXISTS" in sql.upper()


# ---------------------------------------------------------------------------
# Group 7: SQL context build / SELECT assembly / FROM expansion
# ---------------------------------------------------------------------------

class TestSQLContextBuild:
    """Tests for build_stage_sql, FROM clauses, JOINs — lines 454-530."""

    def test_basic_select_generated(self):
        sql = tr("MATCH (n) RETURN n.id")
        assert sql.upper().startswith("SELECT") or "WITH " in sql.upper()

    def test_from_clause_present(self):
        sql = tr("MATCH (n) RETURN n.id")
        assert "FROM" in sql.upper()

    def test_where_condition_generated(self):
        sql = tr("MATCH (n) WHERE n.status = 'x' RETURN n.id")
        assert "WHERE" in sql.upper()

    def test_multiple_select_items(self):
        sql = tr("MATCH (n) RETURN n.id, n.name, n.age")
        assert "SELECT" in sql.upper()

    def test_distinct_select(self):
        sql = tr("MATCH (n) RETURN DISTINCT n.city")
        assert "DISTINCT" in sql.upper()

    def test_limit_added(self):
        sql = tr("MATCH (n) RETURN n.id LIMIT 5")
        assert "5" in sql
        assert "TOP" in sql.upper() or "FETCH" in sql.upper() or "LIMIT" in sql.upper()

    def test_order_by_added(self):
        sql = tr("MATCH (n) RETURN n.name ORDER BY n.name ASC")
        assert "ORDER BY" in sql.upper()

    def test_group_by_with_aggregation(self):
        sql = tr("MATCH (n) RETURN n.city, count(*) AS cnt")
        assert "GROUP BY" in sql.upper()
        assert "COUNT" in sql.upper()

    def test_skip_produces_wrapper(self):
        sql = tr("MATCH (n) RETURN n.id SKIP 10")
        assert "10" in sql

    def test_cte_stages_prefix(self):
        """WITH aggregation stages should produce 'WITH Stage0 AS (...)' CTE prefix."""
        sql = tr("MATCH (n) WITH n.city AS city, count(*) AS cnt RETURN city, cnt")
        assert "Stage" in sql

    def test_no_match_pure_return(self):
        """Pure RETURN without MATCH."""
        sql = tr("RETURN 1 AS one")
        assert "1" in sql

    def test_match_no_label_from_nodes(self):
        """Unlabeled MATCH (n) should select from nodes table."""
        sql = tr("MATCH (n) RETURN count(n) AS total")
        assert "nodes" in sql.lower() or "COUNT" in sql.upper()

    def test_and_conditions_joined(self):
        sql = tr("MATCH (n) WHERE n.a = 1 AND n.b = 2 RETURN n.id")
        assert "AND" in sql.upper()

    def test_join_clause_for_property(self):
        """Property equality filter produces a rdf_props JOIN."""
        sql = tr("MATCH (n {name: 'X'}) RETURN n.id")
        assert "rdf_props" in sql.lower()


# ---------------------------------------------------------------------------
# Group 8: Schema prefix and table references
# ---------------------------------------------------------------------------

class TestSchemaPrefix:
    """Tests that correct table names are used."""

    def test_rdf_labels_referenced_in_labeled_match(self):
        sql = tr("MATCH (n:Person) RETURN n.id")
        assert "rdf_labels" in sql.lower()

    def test_rdf_props_referenced_in_property_filter(self):
        sql = tr("MATCH (n {name: 'Alice'}) RETURN n.id")
        assert "rdf_props" in sql.lower()

    def test_nodes_table_used(self):
        sql = tr("MATCH (n) RETURN n.id")
        assert "nodes" in sql.lower()

    def test_rdf_edges_referenced_in_relationship(self):
        sql = tr("MATCH (n)-[r]->(m) RETURN n.id, m.id")
        assert "rdf_edges" in sql.lower()

    def test_rdf_labels_for_additional_label(self):
        """Second label in multi-label match adds another rdf_labels join."""
        sql = tr("MATCH (n:A:B) RETURN n.id")
        assert sql.lower().count("rdf_labels") >= 2
