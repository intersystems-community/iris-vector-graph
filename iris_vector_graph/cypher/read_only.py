"""Whether a Cypher text writes: the one rule ``read_only`` and ``IVG_READ_ONLY`` enforce.

``execute_cypher(read_only=True)`` and every server route (cypher_api's REST and Neo4j
HTTP routes, api.main's ``/api/cypher`` and GraphQL ``executeCypher``, the ``cypher``
field in :mod:`iris_vector_graph.gql`, Bolt's RUN) ask :func:`writes_cypher`. Before
4.6.0 the servers asked a keyword match over the raw text, which refused a read naming
``CREATE`` in a string or a comment and passed a query that does not parse (R113).
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import List

from .lexer import Lexer, TokenType
from .parser import Parser

# Tokens that open a write clause. Used only for the statements execute_cypher answers
# without the parser (below); everything else is decided on the AST.
_WRITE_TOKENS = frozenset({
    TokenType.CREATE, TokenType.MERGE, TokenType.DELETE, TokenType.DETACH,
    TokenType.SET, TokenType.REMOVE, TokenType.FOREACH,
})

_EXPLAIN = re.compile(r"\s*EXPLAIN\s+", re.IGNORECASE)


def split_statements(text: str) -> List[str]:
    """`text` cut at each `;` that is outside a string, a backquoted name and a comment.

    A plain `split(";")` cut `WHERE x = 'a;b'` in two, and each half failed to parse."""
    parts, start, i, n = [], 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            i += 1
            while i < n and text[i] != ch:
                i += 2 if ch != "`" and text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 1
        elif ch == ";":
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def answered_without_parsing(upper: str) -> bool:
    """Whether execute_cypher answers this statement (upper-cased, stripped) itself.

    ``SHOW ...`` and Neo4j Browser's metadata query (``CALL db.labels() YIELD ... UNION
    ALL ...``, which the parser does not accept) get fixed answers that run no Cypher."""
    return upper.startswith("SHOW ") or ("CALL DB.LABELS() YIELD" in upper and "UNION" in upper)


def _names_a_write_clause(statement: str) -> bool:
    try:
        tokens = Lexer(statement).tokens
    except Exception:  # fail closed: a statement that does not lex is refused
        return True
    return any(t.kind in _WRITE_TOKENS for t in tokens)


def _statement_writes(statement: str) -> bool:
    # EXPLAIN answers a placeholder; judging what follows it refuses `EXPLAIN CREATE ...`,
    # which costs nothing and keeps the rule one rule.
    explained = _EXPLAIN.match(statement)
    body = statement[explained.end():] if explained else statement
    if answered_without_parsing(body.strip().upper()):
        return _names_a_write_clause(body)
    try:
        # A parse of its own, not parse_query's cached tree: execution reads and changes that tree, and a verdict
        # read from it was a verdict on whatever the last run left there (R113, R115).
        return Parser(Lexer(body)).parse().writes
    except Exception:  # fail closed: the parser cannot vouch for it, so it is refused
        return True


@lru_cache(maxsize=1024)
def _text_writes(text: str) -> bool:
    """The verdict on one text, kept as a bool: nothing that runs the query can reach it, and a repeated
    query skips the parse (22 us for R113's cases, 5 ms for an 8 KB query)."""
    statements = split_statements(text) or [text]
    return any(_statement_writes(s) for s in statements)


def writes_cypher(text: str) -> bool:
    """Whether ``text`` may write, so that read-only mode refuses it.

    True when any ``;``-separated statement holds a write clause (CREATE, MERGE, SET,
    REMOVE, DELETE, DETACH DELETE, FOREACH) anywhere the parser puts one: a UNION branch,
    a ``CALL { }`` body, after ``WITH``. A keyword inside a string literal, a backquoted
    name or a comment is not a write. A statement that does not parse is counted as a
    write: nothing can say it does not write. ``SHOW`` and Neo4j Browser's metadata
    query, which ``execute_cypher`` answers without the parser, are reads unless they
    name a write clause. The verdict comes from a parse of its own, so nothing a run of
    the query does to the parse cache can change it.
    """
    return _text_writes(text)
