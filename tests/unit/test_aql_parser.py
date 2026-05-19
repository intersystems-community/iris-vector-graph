import pytest
from iris_vector_graph.cypher.aql import AQLParseError, AQLTranslationError
from iris_vector_graph.cypher.aql.lexer import AQLLexer
from iris_vector_graph.cypher.aql.parser import AQLParser
from iris_vector_graph.cypher.aql.ast import AQLDirection, AQLBindVar, ForClause


def parse(src):
    return AQLParser(AQLLexer(src).tokenize()).parse()


class TestForClause:
    def test_basic_for(self):
        q = parse("FOR v IN 1..3 OUTBOUND @start g RETURN v._key")
        assert q.for_clause.vertex_var == "v"
        assert q.for_clause.edge_var is None
        assert q.for_clause.path_var is None
        assert q.for_clause.min_depth == 1
        assert q.for_clause.max_depth == 3
        assert q.for_clause.direction == AQLDirection.OUTBOUND

    def test_for_with_edge(self):
        q = parse("FOR v, e IN 2 INBOUND @start g RETURN v")
        assert q.for_clause.edge_var == "e"
        assert q.for_clause.min_depth == 2
        assert q.for_clause.max_depth == 2
        assert q.for_clause.direction == AQLDirection.INBOUND

    def test_for_with_path(self):
        q = parse("FOR v, e, p IN 1..2 ANY @s g RETURN p")
        assert q.for_clause.path_var == "p"
        assert q.for_clause.direction == AQLDirection.ANY

    def test_graph_syntax(self):
        q = parse("FOR v IN 1..2 OUTBOUND @s GRAPH 'proteins' RETURN v")
        assert q.for_clause.is_graph is True
        assert q.for_clause.graph_or_collections == ["proteins"]

    def test_collection_syntax(self):
        q = parse("FOR v IN 1..2 OUTBOUND @s interactions RETURN v")
        assert q.for_clause.is_graph is False
        assert "interactions" in q.for_clause.graph_or_collections

    def test_multiple_collections(self):
        q = parse("FOR v IN 1..2 OUTBOUND @s e1, e2 RETURN v")
        assert len(q.for_clause.graph_or_collections) == 2

    def test_bind_var_start(self):
        q = parse("FOR v IN 1..1 OUTBOUND @start g RETURN v")
        assert isinstance(q.for_clause.start_expr, AQLBindVar)
        assert q.for_clause.start_expr.name == "start"


class TestFilterClause:
    def test_basic_filter(self):
        q = parse("FOR v IN 1..2 OUTBOUND @s g FILTER v.x == 'y' RETURN v")
        assert len(q.filter_clauses) == 1

    def test_multiple_filters(self):
        q = parse("FOR v IN 1..2 OUTBOUND @s g FILTER v.x == 1 FILTER v.y > 0 RETURN v")
        assert len(q.filter_clauses) == 2


class TestReturnClause:
    def test_return_key_access(self):
        q = parse("FOR v IN 1..1 OUTBOUND @s g RETURN v._key")
        assert q.return_clause is not None

    def test_return_property(self):
        q = parse("FOR v IN 1..1 OUTBOUND @s g RETURN v.name")
        assert q.return_clause is not None

    def test_return_object(self):
        q = parse("FOR v IN 1..1 OUTBOUND @s g RETURN { id: v._key, name: v.name }")
        assert q.return_clause is not None


class TestErrorCases:
    def test_nested_for_raises(self):
        with pytest.raises((AQLParseError, AQLTranslationError)):
            parse("FOR v IN 1..2 OUTBOUND @s g FOR w IN 1..1 OUTBOUND v d RETURN w")

    def test_syntax_error_has_line_col(self):
        with pytest.raises(AQLParseError) as exc:
            parse("FOR v IN OUTBOUND @s RETURN v")
        assert exc.value.line >= 1
