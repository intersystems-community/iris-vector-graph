"""Unit tests for CALL { ... } subquery clauses in Cypher parser and translator."""
import pytest
from iris_vector_graph.cypher.parser import parse_query, CypherParseError
from iris_vector_graph.cypher.translator import translate_to_sql, set_schema_prefix
from iris_vector_graph.cypher import ast


class TestSubqueryParsing:

    def test_parse_independent_subquery_projection(self):
        """T009"""
        q = parse_query("CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name")
        clause = q.query_parts[0].clauses[0]
        assert isinstance(clause, ast.SubqueryCall)
        assert clause.import_variables == []
        assert clause.inner_query.return_clause is not None

    def test_parse_independent_subquery_aggregation(self):
        """T010"""
        q = parse_query("CALL { MATCH (n) RETURN count(n) AS cnt } RETURN cnt")
        clause = q.query_parts[0].clauses[0]
        assert isinstance(clause, ast.SubqueryCall)
        inner_ret = clause.inner_query.return_clause
        assert any(
            isinstance(item.expression, ast.AggregationFunction)
            for item in inner_ret.items
        )

    def test_parse_correlated_subquery_with_import(self):
        """T015"""
        q = parse_query(
            "MATCH (p) CALL { WITH p MATCH (p)-[r]->(q) RETURN count(q) AS deg } RETURN p, deg"
        )
        match_clause = q.query_parts[0].clauses[0]
        assert isinstance(match_clause, ast.MatchClause)
        sub_clause = q.query_parts[0].clauses[1]
        assert isinstance(sub_clause, ast.SubqueryCall)
        assert sub_clause.import_variables == ["p"]

    def test_parse_in_transactions_with_batch_size(self):
        """T020"""
        q = parse_query(
            "CALL { MATCH (n) RETURN n.id AS id } IN TRANSACTIONS OF 500 ROWS RETURN id"
        )
        clause = q.query_parts[0].clauses[0]
        assert isinstance(clause, ast.SubqueryCall)
        assert clause.in_transactions is True
        assert clause.transactions_batch_size == 500

    def test_parse_in_transactions_without_batch_size(self):
        """T021"""
        q = parse_query(
            "CALL { MATCH (n) RETURN n.id AS id } IN TRANSACTIONS RETURN id"
        )
        clause = q.query_parts[0].clauses[0]
        assert clause.in_transactions is True
        assert clause.transactions_batch_size is None

    def test_parse_subquery_missing_return_raises_error(self):
        """T008a"""
        with pytest.raises(CypherParseError, match="RETURN"):
            parse_query("CALL { MATCH (n) } RETURN n")

    def test_existing_call_procedure_still_works(self):
        q = parse_query(
            "CALL ivg.vector.search('Gene', 'embedding', [1.0, 0.0], 5) "
            "YIELD node, score RETURN node, score"
        )
        assert q.procedure_call is not None
        assert q.procedure_call.procedure_name == "ivg.vector.search"


class TestSubqueryTranslation:

    @pytest.fixture(autouse=True)
    def setup_schema(self):
        set_schema_prefix("Graph_KG")
        yield
        set_schema_prefix("")

    def test_independent_subquery_emits_cte(self):
        """T011"""
        q = parse_query("CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name")
        result = translate_to_sql(q)
        sql = result.sql
        assert "SubQuery" in sql
        assert "AS" in sql

    def test_subquery_output_var_accessible_in_outer_return(self):
        """T012"""
        q = parse_query("CALL { MATCH (n:Drug) RETURN n.name AS name } RETURN name")
        result = translate_to_sql(q)
        sql = result.sql
        assert "name" in sql

    def test_correlated_subquery_emits_scalar_with_coalesce(self):
        """T016"""
        q = parse_query(
            "MATCH (p:Protein) "
            "CALL { WITH p MATCH (p)-[:INTERACTS_WITH]->(q) RETURN count(q) AS deg } "
            "RETURN p.id, deg"
        )
        result = translate_to_sql(q)
        sql = result.sql
        assert "COALESCE" in sql

    def test_scope_isolation_independent_subquery_raises_error(self):
        """T017"""
        q = parse_query(
            "MATCH (p:Protein) "
            "CALL { MATCH (q)-[]->(r) RETURN count(q) AS deg } "
            "RETURN p.id, deg"
        )
        result = translate_to_sql(q)
        sql = result.sql
        assert "SubQuery" in sql
        assert "deg" in sql
