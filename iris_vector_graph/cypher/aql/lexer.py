import enum
from dataclasses import dataclass


class AQLTokenType(enum.Enum):
    FOR = "FOR"; IN = "IN"; OUTBOUND = "OUTBOUND"; INBOUND = "INBOUND"; ANY = "ANY"
    RETURN = "RETURN"; FILTER = "FILTER"; LET = "LET"; SORT = "SORT"; LIMIT = "LIMIT"
    COLLECT = "COLLECT"; WITH = "WITH"; COUNT = "COUNT"; INTO = "INTO"; GRAPH = "GRAPH"
    SHORTEST_PATH = "SHORTEST_PATH"; K_SHORTEST_PATHS = "K_SHORTEST_PATHS"; K_PATHS = "K_PATHS"
    TO = "TO"; DISTINCT = "DISTINCT"; ASC = "ASC"; DESC = "DESC"
    AND = "AND"; OR = "OR"; NOT = "NOT"; NULL = "NULL"; TRUE = "TRUE"; FALSE = "FALSE"
    ALL = "ALL"; AGGREGATE = "AGGREGATE"; SEARCH = "SEARCH"
    INSERT = "INSERT"; UPDATE = "UPDATE"; REMOVE = "REMOVE"; UPSERT = "UPSERT"
    REPLACE = "REPLACE"; WINDOW = "WINDOW"
    IDENT = "IDENT"; INT = "INT"; FLOAT = "FLOAT"; STRING = "STRING"
    BIND_VAR = "BIND_VAR"; DYN_COLLECTION = "DYN_COLLECTION"
    RANGE = ".."; EQ = "=="; NEQ = "!="; LTE = "<="; GTE = ">="; LT = "<"; GT = ">"
    REGEX_MATCH = "=~"; REGEX_NOTMATCH = "!~"; ASSIGN = "="
    PLUS = "+"; MINUS = "-"; MUL = "*"; DIV = "/"; MOD = "%"
    DOT = "."; COMMA = ","; COLON = ":"; LPAREN = "("; RPAREN = ")"
    LBRACKET = "["; RBRACKET = "]"; LBRACE = "{"; RBRACE = "}"; QUESTION = "?"
    EOF = "EOF"


_KW = {t.value: t for t in AQLTokenType
       if t.value.replace('_', '').isalpha() and t not in (
           AQLTokenType.IDENT, AQLTokenType.EOF)}


@dataclass(slots=True)
class AQLToken:
    kind: AQLTokenType
    value: str
    line: int
    column: int


class AQLLexer:
    def __init__(self, source: str):
        self._src = self._strip_comments(source)
        self._pos = 0
        self._line = 1
        self._col = 1

    @staticmethod
    def _strip_comments(src: str) -> str:
        result = []
        i = 0
        while i < len(src):
            if src[i:i+2] == "/*":
                end = src.find("*/", i + 2)
                if end == -1:
                    break
                result.append('\n' * src[i:end+2].count('\n'))
                i = end + 2
            elif src[i:i+2] == "//":
                end = src.find('\n', i)
                result.append('\n')
                i = (end + 1) if end != -1 else len(src)
            else:
                result.append(src[i])
                i += 1
        return ''.join(result)

    def _peek(self, offset: int = 0) -> str:
        p = self._pos + offset
        return self._src[p] if p < len(self._src) else ''

    def _advance(self) -> str:
        ch = self._src[self._pos]; self._pos += 1
        if ch == '\n': self._line += 1; self._col = 1
        else: self._col += 1
        return ch

    def _tok(self, kind: AQLTokenType, value: str, line: int, col: int) -> AQLToken:
        return AQLToken(kind=kind, value=value, line=line, column=col)

    def tokenize(self) -> list:
        tokens = []
        while self._pos < len(self._src):
            while self._pos < len(self._src) and self._src[self._pos] in ' \t\r\n':
                self._advance()
            if self._pos >= len(self._src):
                break
            line, col = self._line, self._col
            ch = self._peek()

            if ch == '@':
                self._advance()
                if self._peek() == '@':
                    self._advance()
                    tokens.append(self._tok(AQLTokenType.DYN_COLLECTION, '@@' + self._read_ident(), line, col))
                else:
                    tokens.append(self._tok(AQLTokenType.BIND_VAR, '@' + self._read_ident(), line, col))

            elif ch.isdigit():
                tokens.append(self._read_number(line, col))

            elif ch in ('"', "'", '`'):
                tokens.append(self._read_string(line, col))

            elif ch.isalpha() or ch == '_':
                word = self._read_ident()
                upper = word.upper()
                kind = _KW.get(upper, AQLTokenType.IDENT)
                tokens.append(self._tok(kind, word, line, col))

            elif ch == '.' and self._peek(1) == '.':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.RANGE, '..', line, col))

            elif ch == '.':
                self._advance()
                tokens.append(self._tok(AQLTokenType.DOT, '.', line, col))

            elif ch == '=' and self._peek(1) == '~':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.REGEX_MATCH, '=~', line, col))

            elif ch == '!' and self._peek(1) == '~':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.REGEX_NOTMATCH, '!~', line, col))

            elif ch == '=' and self._peek(1) == '=':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.EQ, '==', line, col))

            elif ch == '!' and self._peek(1) == '=':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.NEQ, '!=', line, col))

            elif ch == '<' and self._peek(1) == '=':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.LTE, '<=', line, col))

            elif ch == '>' and self._peek(1) == '=':
                self._advance(); self._advance()
                tokens.append(self._tok(AQLTokenType.GTE, '>=', line, col))

            elif ch in '<>=!+-*/%,:()[]{}?':
                m = {'<': AQLTokenType.LT, '>': AQLTokenType.GT, '=': AQLTokenType.ASSIGN,
                     '+': AQLTokenType.PLUS, '-': AQLTokenType.MINUS, '*': AQLTokenType.MUL,
                     '/': AQLTokenType.DIV, '%': AQLTokenType.MOD, ',': AQLTokenType.COMMA,
                     ':': AQLTokenType.COLON, '(': AQLTokenType.LPAREN, ')': AQLTokenType.RPAREN,
                     '[': AQLTokenType.LBRACKET, ']': AQLTokenType.RBRACKET,
                     '{': AQLTokenType.LBRACE, '}': AQLTokenType.RBRACE, '?': AQLTokenType.QUESTION}
                if ch in m:
                    self._advance()
                    tokens.append(self._tok(m[ch], ch, line, col))
                else:
                    self._advance()
            else:
                self._advance()

        tokens.append(AQLToken(AQLTokenType.EOF, '', self._line, self._col))
        return tokens

    def _read_ident(self) -> str:
        start = self._pos
        while self._pos < len(self._src) and (self._src[self._pos].isalnum() or self._src[self._pos] == '_'):
            self._advance()
        return self._src[start:self._pos]

    def _read_number(self, line: int, col: int) -> AQLToken:
        start = self._pos
        while self._pos < len(self._src) and self._src[self._pos].isdigit():
            self._advance()
        if (self._pos < len(self._src) and self._src[self._pos] == '.' and
                (self._pos + 1 >= len(self._src) or self._src[self._pos + 1] != '.')):
            self._advance()
            while self._pos < len(self._src) and self._src[self._pos].isdigit():
                self._advance()
            return AQLToken(AQLTokenType.FLOAT, self._src[start:self._pos], line, col)
        return AQLToken(AQLTokenType.INT, self._src[start:self._pos], line, col)

    def _read_string(self, line: int, col: int) -> AQLToken:
        quote = self._advance()
        chars = []
        while self._pos < len(self._src):
            ch = self._src[self._pos]
            if ch == '\\' and quote != '`':
                self._advance()
                esc = self._advance()
                chars.append({'n': '\n', 't': '\t', 'r': '\r', '\\': '\\',
                               '"': '"', "'": "'"}.get(esc, esc))
            elif ch == quote:
                self._advance(); break
            else:
                chars.append(self._advance())
        return AQLToken(AQLTokenType.STRING, ''.join(chars), line, col)
