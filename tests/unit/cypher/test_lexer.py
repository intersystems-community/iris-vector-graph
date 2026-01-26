import pytest
from iris_vector_graph.cypher.lexer import Lexer, TokenType

def test_lexer_basic_keywords():
    source = "MATCH WITH WHERE RETURN"
    lexer = Lexer(source)
    
    assert lexer.eat().kind == TokenType.MATCH
    assert lexer.eat().kind == TokenType.WITH
    assert lexer.eat().kind == TokenType.WHERE
    assert lexer.eat().kind == TokenType.RETURN
    assert lexer.eat().kind == TokenType.EOF

def test_lexer_literals():
    source = "123 45.6 'hello' \"world\" $param"
    lexer = Lexer(source)
    
    t1 = lexer.eat()
    assert t1.kind == TokenType.INTEGER_LITERAL
    assert t1.value == "123"
    
    t2 = lexer.eat()
    assert t2.kind == TokenType.FLOAT_LITERAL
    assert t2.value == "45.6"
    
    t3 = lexer.eat()
    assert t3.kind == TokenType.STRING_LITERAL
    assert t3.value == "hello"
    
    t4 = lexer.eat()
    assert t4.kind == TokenType.STRING_LITERAL
    assert t4.value == "world"
    
    t5 = lexer.eat()
    assert t5.kind == TokenType.PARAMETER
    assert t5.value == "param"

def test_lexer_operators():
    source = "() [] {} , . : | = <> < <= > >= + - * / <- ->"
    lexer = Lexer(source)
    
    expected = [
        TokenType.LPAREN, TokenType.RPAREN,
        TokenType.LBRACKET, TokenType.RBRACKET,
        TokenType.LBRACE, TokenType.RBRACE,
        TokenType.COMMA, TokenType.DOT, TokenType.COLON, TokenType.PIPE,
        TokenType.EQUALS, TokenType.NOT_EQUALS,
        TokenType.LESS_THAN, TokenType.LESS_THAN_OR_EQUAL,
        TokenType.GREATER_THAN, TokenType.GREATER_THAN_OR_EQUAL,
        TokenType.PLUS, TokenType.MINUS, TokenType.STAR, TokenType.SLASH,
        TokenType.ARROW_LEFT, TokenType.ARROW_RIGHT
    ]
    
    for kind in expected:
        tok = lexer.eat()
        assert tok.kind == kind, f"Expected {kind}, got {tok.kind}"

def test_lexer_starts_with():
    source = "STARTS WITH"
    lexer = Lexer(source)
    
    assert lexer.eat().kind == TokenType.STARTS
    assert lexer.eat().kind == TokenType.WITH_KW
    assert lexer.eat().kind == TokenType.EOF

def test_lexer_identifiers():
    source = "node_123 _var name"
    lexer = Lexer(source)
    
    assert lexer.eat().value == "node_123"
    assert lexer.eat().value == "_var"
    assert lexer.eat().value == "name"

def test_lexer_position_tracking():
    source = "MATCH\n  (n)"
    lexer = Lexer(source)
    
    t1 = lexer.eat() # MATCH
    assert t1.line == 1
    assert t1.column == 1
    
    t2 = lexer.eat() # (
    assert t2.line == 2
    assert t2.column == 3
    
    t3 = lexer.eat() # n
    assert t3.line == 2
    assert t3.column == 4
