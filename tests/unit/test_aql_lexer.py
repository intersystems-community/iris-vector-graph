import pytest
from iris_vector_graph.cypher.aql.lexer import AQLLexer, AQLTokenType


def tok(src):
    return AQLLexer(src).tokenize()


def kinds(src):
    return [t.kind for t in tok(src) if t.kind != AQLTokenType.EOF]


class TestKeywords:
    def test_all_27_keywords(self):
        kws = ["FOR", "IN", "OUTBOUND", "INBOUND", "ANY", "RETURN", "FILTER",
               "LET", "SORT", "LIMIT", "COLLECT", "WITH", "COUNT", "INTO",
               "GRAPH", "SHORTEST_PATH", "TO", "DISTINCT", "ASC", "DESC",
               "AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "AGGREGATE"]
        for kw in kws:
            tokens = tok(kw)
            assert tokens[0].kind != AQLTokenType.IDENT, f"{kw} tokenized as IDENT"
            assert tokens[0].kind == AQLTokenType[kw], f"{kw} wrong kind"

    def test_case_insensitive(self):
        assert kinds("for")[0] == AQLTokenType.FOR
        assert kinds("outbound")[0] == AQLTokenType.OUTBOUND
        assert kinds("Return")[0] == AQLTokenType.RETURN


class TestLiterals:
    def test_integer(self):
        t = tok("42")[0]
        assert t.kind == AQLTokenType.INT
        assert t.value == "42"

    def test_float(self):
        t = tok("3.14")[0]
        assert t.kind == AQLTokenType.FLOAT
        assert t.value == "3.14"

    def test_single_quoted_string(self):
        t = tok("'hello world'")[0]
        assert t.kind == AQLTokenType.STRING
        assert t.value == "hello world"

    def test_double_quoted_string(self):
        t = tok('"hello"')[0]
        assert t.kind == AQLTokenType.STRING
        assert t.value == "hello"

    def test_backtick_string(self):
        t = tok("`my-collection`")[0]
        assert t.kind == AQLTokenType.STRING
        assert t.value == "my-collection"

    def test_string_escape(self):
        t = tok(r'"hello\nworld"')[0]
        assert '\n' in t.value


class TestBindVars:
    def test_bind_var(self):
        t = tok("@start")[0]
        assert t.kind == AQLTokenType.BIND_VAR
        assert t.value == "@start"

    def test_dyn_collection(self):
        t = tok("@@coll")[0]
        assert t.kind == AQLTokenType.DYN_COLLECTION
        assert t.value == "@@coll"


class TestOperators:
    def test_range_not_two_dots(self):
        ks = kinds("1..3")
        assert AQLTokenType.RANGE in ks
        assert ks.count(AQLTokenType.DOT) == 0

    def test_eq(self):
        assert kinds("==")[0] == AQLTokenType.EQ

    def test_neq(self):
        assert kinds("!=")[0] == AQLTokenType.NEQ

    def test_lte(self):
        assert kinds("<=")[0] == AQLTokenType.LTE

    def test_gte(self):
        assert kinds(">=")[0] == AQLTokenType.GTE

    def test_regex_match(self):
        assert kinds("=~")[0] == AQLTokenType.REGEX_MATCH

    def test_regex_notmatch(self):
        assert kinds("!~")[0] == AQLTokenType.REGEX_NOTMATCH


class TestComments:
    def test_block_comment_stripped(self):
        ks = kinds("FOR /* skip this */ v")
        assert AQLTokenType.FOR in ks
        assert AQLTokenType.IDENT in ks
        assert len([k for k in ks if k == AQLTokenType.IDENT]) == 1

    def test_line_comment_stripped(self):
        ks = kinds("FOR v // ignore\nIN")
        assert AQLTokenType.FOR in ks
        assert AQLTokenType.IN in ks


class TestFullQuery:
    def test_simple_traversal(self):
        ks = kinds("FOR v IN 1..3 OUTBOUND @start g RETURN v")
        assert AQLTokenType.FOR in ks
        assert AQLTokenType.RANGE in ks
        assert AQLTokenType.OUTBOUND in ks
        assert AQLTokenType.BIND_VAR in ks
        assert AQLTokenType.RETURN in ks
