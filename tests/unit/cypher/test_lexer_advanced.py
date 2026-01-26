from typing import List, Optional, Any, Dict, Union
from iris_vector_graph.cypher.lexer import Lexer, TokenType
from iris_vector_graph.cypher import ast

def test_lexer_advanced_keywords():
    source = "UNWIND CREATE MERGE DELETE SET REMOVE ON OPTIONAL"
    lexer = Lexer(source)
    
    assert lexer.eat().kind == TokenType.UNWIND
    assert lexer.eat().kind == TokenType.CREATE
    assert lexer.eat().kind == TokenType.MERGE
    assert lexer.eat().kind == TokenType.DELETE
    assert lexer.eat().kind == TokenType.SET
    assert lexer.eat().kind == TokenType.REMOVE
    assert lexer.eat().kind == TokenType.ON
    assert lexer.eat().kind == TokenType.IDENTIFIER # OPTIONAL is an identifier in lexer
    assert lexer.eat().kind == TokenType.EOF
