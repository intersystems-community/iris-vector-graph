from dataclasses import dataclass
from typing import Optional, List, Any
import enum

class TokenType(enum.Enum):
    # Keywords
    MATCH = "MATCH"
    WITH = "WITH"
    WHERE = "WHERE"
    RETURN = "RETURN"
    ORDER = "ORDER"
    BY = "BY"
    LIMIT = "LIMIT"
    SKIP = "SKIP"
    ASC = "ASC"
    DESC = "DESC"
    DISTINCT = "DISTINCT"
    AS = "AS"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    TRUE = "TRUE"
    FALSE = "FALSE"
    NULL = "NULL"
    IN = "IN"
    IS = "IS"
    STARTS = "STARTS"
    WITH_KW = "WITH_KW" # For STARTS WITH
    CONTAINS = "CONTAINS"
    ENDS = "ENDS"
    UNWIND = "UNWIND"
    CREATE = "CREATE"
    MERGE = "MERGE"
    DELETE = "DELETE"
    SET = "SET"
    REMOVE = "REMOVE"
    ON = "ON"
    DETACH = "DETACH"

    # Literals and Identifiers
    IDENTIFIER = "IDENTIFIER"
    STRING_LITERAL = "STRING_LITERAL"
    INTEGER_LITERAL = "INTEGER_LITERAL"
    FLOAT_LITERAL = "FLOAT_LITERAL"
    PARAMETER = "PARAMETER"

    # Operators and Punctuation
    LPAREN = "("
    RPAREN = ")"
    LBRACKET = "["
    RBRACKET = "]"
    LBRACE = "{"
    RBRACE = "}"
    COMMA = ","
    DOT = "."
    COLON = ":"
    PIPE = "|"
    EQUALS = "="
    NOT_EQUALS = "<>"
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    PLUS = "+"
    MINUS = "-"
    STAR = "*"
    SLASH = "/"
    ARROW_LEFT = "<-"
    ARROW_RIGHT = "->"
    
    EOF = "EOF"

@dataclass(slots=True, frozen=True)
class Token:
    kind: TokenType
    value: Optional[str] = None
    pos: int = 0
    line: int = 1
    column: int = 1

class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.cursor = 0
        self.line = 1
        self.column = 1
        self.tokens: List[Token] = []
        self._tokenize()
        self.token_index = 0

    def _tokenize(self):
        while self.cursor < len(self.source):
            char = self.source[self.cursor]
            
            if char.isspace():
                self._skip_whitespace()
                continue
            
            start_pos = self.cursor
            start_col = self.column
            
            match char:
                case '(': self._add_token(TokenType.LPAREN, char)
                case ')': self._add_token(TokenType.RPAREN, char)
                case '[': self._add_token(TokenType.LBRACKET, char)
                case ']': self._add_token(TokenType.RBRACKET, char)
                case '{': self._add_token(TokenType.LBRACE, char)
                case '}': self._add_token(TokenType.RBRACE, char)
                case ',': self._add_token(TokenType.COMMA, char)
                case '.': self._add_token(TokenType.DOT, char)
                case ':': self._add_token(TokenType.COLON, char)
                case '|': self._add_token(TokenType.PIPE, char)
                case '+': self._add_token(TokenType.PLUS, char)
                case '*': self._add_token(TokenType.STAR, char)
                case '/': self._add_token(TokenType.SLASH, char)
                case '=': self._add_token(TokenType.EQUALS, char)
                case '<':
                    if self._peek() == '>':
                        self.cursor += 1
                        self.column += 1
                        self._add_token(TokenType.NOT_EQUALS, "<>")
                    elif self._peek() == '=':
                        self.cursor += 1
                        self.column += 1
                        self._add_token(TokenType.LESS_THAN_OR_EQUAL, "<=")
                    elif self._peek() == '-':
                        self.cursor += 1
                        self.column += 1
                        self._add_token(TokenType.ARROW_LEFT, "<-")
                    else:
                        self._add_token(TokenType.LESS_THAN, char)
                case '>':
                    if self._peek() == '=':
                        self.cursor += 1
                        self.column += 1
                        self._add_token(TokenType.GREATER_THAN_OR_EQUAL, ">=")
                    else:
                        self._add_token(TokenType.GREATER_THAN, char)
                case '-':
                    if self._peek() == '[':
                        self._add_token(TokenType.MINUS, char)
                    elif self._peek() == '>':
                        self.cursor += 1
                        self.column += 1
                        self._add_token(TokenType.ARROW_RIGHT, "->")
                    else:
                        self._add_token(TokenType.MINUS, char)
                case '"' | "'":
                    self._tokenize_string(char)
                case '$':
                    self._tokenize_parameter()
                case c if c.isdigit():
                    self._tokenize_number()
                case c if c.isalpha() or c == '_':
                    self._tokenize_identifier_or_keyword()
                case _:
                    raise SyntaxError(f"Unexpected character '{char}' at line {self.line}, col {self.column}")
        
        self.tokens.append(Token(TokenType.EOF, pos=self.cursor, line=self.line, column=self.column))

    def _add_token(self, kind: TokenType, value: str):
        self.tokens.append(Token(kind, value, self.cursor, self.line, self.column))
        self.cursor += 1
        self.column += 1

    def _peek(self) -> Optional[str]:
        if self.cursor + 1 < len(self.source):
            return self.source[self.cursor + 1]
        return None

    def _skip_whitespace(self):
        while self.cursor < len(self.source) and self.source[self.cursor].isspace():
            if self.source[self.cursor] == '\n':
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.cursor += 1

    def _tokenize_string(self, quote: str):
        start_pos = self.cursor
        start_col = self.column
        self.cursor += 1
        self.column += 1
        value = ""
        while self.cursor < len(self.source) and self.source[self.cursor] != quote:
            if self.source[self.cursor] == '\\':
                self.cursor += 1
                self.column += 1
                if self.cursor < len(self.source):
                    value += self.source[self.cursor]
            else:
                value += self.source[self.cursor]
            self.cursor += 1
            self.column += 1
        
        if self.cursor >= len(self.source):
            raise SyntaxError(f"Unterminated string starting at line {self.line}, col {start_col}")
        
        self.cursor += 1
        self.column += 1
        self.tokens.append(Token(TokenType.STRING_LITERAL, value, start_pos, self.line, start_col))

    def _tokenize_parameter(self):
        start_pos = self.cursor
        start_col = self.column
        self.cursor += 1
        self.column += 1
        value = ""
        while self.cursor < len(self.source) and (self.source[self.cursor].isalnum() or self.source[self.cursor] == '_'):
            value += self.source[self.cursor]
            self.cursor += 1
            self.column += 1
        self.tokens.append(Token(TokenType.PARAMETER, value, start_pos, self.line, start_col))

    def _tokenize_number(self):
        start_pos = self.cursor
        start_col = self.column
        value = ""
        is_float = False
        while self.cursor < len(self.source) and (self.source[self.cursor].isdigit() or self.source[self.cursor] == '.'):
            if self.source[self.cursor] == '.':
                if is_float: break
                is_float = True
            value += self.source[self.cursor]
            self.cursor += 1
            self.column += 1
        
        kind = TokenType.FLOAT_LITERAL if is_float else TokenType.INTEGER_LITERAL
        self.tokens.append(Token(kind, value, start_pos, self.line, start_col))

    def _tokenize_identifier_or_keyword(self):
        start_pos = self.cursor
        start_col = self.column
        value = ""
        while self.cursor < len(self.source) and (self.source[self.cursor].isalnum() or self.source[self.cursor] == '_'):
            value += self.source[self.cursor]
            self.cursor += 1
            self.column += 1
        
        upper_value = value.upper()
        try:
            # Check for STARTS WITH
            if upper_value == "STARTS" and self._peek_keyword("WITH"):
                self.tokens.append(Token(TokenType.STARTS, value, start_pos, self.line, start_col))
                self._consume_keyword("WITH", TokenType.WITH_KW)
                return

            kind = TokenType[upper_value]
            self.tokens.append(Token(kind, value, start_pos, self.line, start_col))
        except KeyError:
            self.tokens.append(Token(TokenType.IDENTIFIER, value, start_pos, self.line, start_col))

    def _peek_keyword(self, keyword: str) -> bool:
        # Simple peek for multi-word keywords
        current_cursor = self.cursor
        
        # Skip whitespace
        while current_cursor < len(self.source) and self.source[current_cursor].isspace():
            current_cursor += 1
        
        k_val = ""
        while current_cursor < len(self.source) and self.source[current_cursor].isalpha():
            k_val += self.source[current_cursor]
            current_cursor += 1
        
        return k_val.upper() == keyword.upper()

    def _consume_keyword(self, keyword: str, kind: TokenType):
        self._skip_whitespace()
        start_pos = self.cursor
        start_col = self.column
        value = ""
        while self.cursor < len(self.source) and self.source[self.cursor].isalpha():
            value += self.source[self.cursor]
            self.cursor += 1
            self.column += 1
        self.tokens.append(Token(kind, value, start_pos, self.line, start_col))

    def peek(self) -> Token:
        if self.token_index < len(self.tokens):
            return self.tokens[self.token_index]
        return self.tokens[-1]

    def eat(self) -> Token:
        tok = self.peek()
        if tok.kind != TokenType.EOF:
            self.token_index += 1
        return tok
