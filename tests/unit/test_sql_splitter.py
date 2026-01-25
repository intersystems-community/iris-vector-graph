import pytest
from tests.conftest import _split_sql_statements
from scripts.setup import _decompose_multi_row_insert

def test_split_simple_statements():
    sql = "CREATE TABLE t1(a INT); CREATE TABLE t2(b INT);"
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 2
    assert stmts[0] == "CREATE TABLE t1(a INT)"
    assert stmts[1] == "CREATE TABLE t2(b INT)"

def test_split_with_comments():
    sql = """
    -- This is a comment
    CREATE TABLE t1(a INT);
    /* Multi-line
       comment */
    CREATE TABLE t2(b INT); -- another comment
    """
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 2
    assert "CREATE TABLE t1(a INT)" in stmts[0]
    assert "CREATE TABLE t2(b INT)" in stmts[1]

def test_split_with_quotes():
    sql = "INSERT INTO t1 VALUES ('semi;colon'); SELECT * FROM t1;"
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 2
    assert "semi;colon" in stmts[0]

def test_split_objectscript_procedure():
    sql = """
    CREATE PROCEDURE test()
    LANGUAGE OBJECTSCRIPT
    {
        Write "hello; world", !
        If 1 {
            Set x = 1
        }
    }
    CREATE TABLE t1(a INT);
    """
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 2
    assert "LANGUAGE OBJECTSCRIPT" in stmts[0]
    assert stmts[0].strip().endswith("}")
    assert stmts[1] == "CREATE TABLE t1(a INT)"

def test_split_sql_procedure():
    sql = """
    CREATE PROCEDURE test()
    LANGUAGE SQL
    BEGIN
        SELECT 1;
        SELECT 2;
    END;
    CREATE TABLE t1(a INT);
    """
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 2
    assert "LANGUAGE SQL" in stmts[0]
    assert stmts[0].strip().endswith("END;")
    assert stmts[1] == "CREATE TABLE t1(a INT)"

class TestDecomposeMultiRowInsert:
    """Test decomposition of multi-row INSERTs for IRIS compatibility."""
    
    def test_single_row_unchanged(self):
        stmt = "INSERT INTO nodes(node_id) VALUES ('A')"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 1
        assert result[0] == stmt
    
    def test_multi_row_basic(self):
        stmt = "INSERT INTO nodes(node_id) VALUES ('A'), ('B'), ('C')"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 3
        assert "VALUES ('A')" in result[0]
        assert "VALUES ('B')" in result[1]
        assert "VALUES ('C')" in result[2]
    
    def test_multi_row_with_semicolon(self):
        stmt = "INSERT INTO t(a,b) VALUES (1,'x'), (2,'y');"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 2
        assert "VALUES (1,'x')" in result[0]
        assert "VALUES (2,'y')" in result[1]
    
    def test_multi_row_with_commas_in_strings(self):
        stmt = "INSERT INTO t(a) VALUES ('hello, world'), ('foo, bar')"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 2
        assert "'hello, world'" in result[0]
        assert "'foo, bar'" in result[1]
    
    def test_multi_row_with_function_calls(self):
        """Test that function calls with commas inside are handled."""
        stmt = "INSERT INTO t(a, b) VALUES (TO_VECTOR('[1,2,3]'), 'x'), (TO_VECTOR('[4,5,6]'), 'y')"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 2
        assert "TO_VECTOR('[1,2,3]')" in result[0]
        assert "TO_VECTOR('[4,5,6]')" in result[1]
    
    def test_multi_row_with_null(self):
        stmt = "INSERT INTO t(a,b) VALUES ('x', NULL), ('y', NULL)"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 2
        assert "NULL" in result[0]
        assert "NULL" in result[1]
    
    def test_non_insert_unchanged(self):
        stmt = "SELECT * FROM t WHERE a = 1"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 1
        assert result[0] == stmt
    
    def test_or_ignore_preserved(self):
        stmt = "INSERT %OR %IGNORE INTO t(a) VALUES ('x'), ('y')"
        result = _decompose_multi_row_insert(stmt)
        assert len(result) == 2
        assert "%OR %IGNORE" in result[0]
        assert "%OR %IGNORE" in result[1]
    
    def test_empty_string(self):
        assert _decompose_multi_row_insert("") == []
        assert _decompose_multi_row_insert("   ") == []


if __name__ == "__main__":
    pytest.main([__file__])
