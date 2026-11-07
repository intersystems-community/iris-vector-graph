"""4.6.0 fixes80: no SQL the translator writes casts to VARCHAR without a length.

IRIS gives `CAST(x AS VARCHAR)` a length of 30 (measured 2026-11-07 on 2026.3.0AI and Community 2026.1: a 100,001-character
value came back as 30, as 64,000 under VARCHAR(64000) and whole under VARCHAR(3641144)). The map-literal builder cast
each value so: `WITH n.title + '-abcdefghijklmnopqrstuvwxyz0123456789' AS s RETURN {a: s}` answered
{'a': 'Alien-abcdefghijklmnopqrstuvwx'}, and a list in a map was cut mid-element, so its JSON did not parse. String
`+`, toString() and coalesce() cast each operand to VARCHAR(4096): `n.title + <a 5,000-character literal>` answered
4,101 characters, where a property holds up to 64,000 (rdf_props.val).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

PACKAGE = Path(__file__).resolve().parents[2] / "iris_vector_graph"
UNSIZED = re.compile(r"AS\s+VARCHAR\s*\)", re.IGNORECASE)
#: the translator's value casts were VARCHAR(4096) too; a property holds up to 64,000 characters
TRANSLATOR_4096 = re.compile(r"AS\s+VARCHAR\(4096\)", re.IGNORECASE)


def _code_strings(path: Path):
    """Each string the module's code holds (f-string parts included), with its line: not a docstring or any other
    string that stands as a statement by itself, which no SQL is built from."""
    tree = ast.parse(path.read_text())
    standing = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in standing:
            yield n.lineno, n.value


def test_no_sql_in_the_package_casts_to_varchar_without_a_length():
    found = [(str(p.relative_to(PACKAGE)), line) for p in sorted(PACKAGE.rglob("*.py"))
             for line, text in _code_strings(p) if UNSIZED.search(text)]
    assert found == [], found


def test_the_translator_casts_no_value_to_varchar_4096():
    found = [line for line, text in _code_strings(PACKAGE / "cypher" / "translator.py") if TRANSLATOR_4096.search(text)]
    assert found == [], found


def test_a_map_of_query_values_casts_each_to_the_longest_string():
    for q in ("MATCH (n) WITH n.title + 'x' AS s RETURN {a: s} AS v",
              "WITH ['a', 'b'] AS l RETURN {a: l} AS v",
              "MATCH (n) WITH collect(n) AS ns RETURN {f: ns} AS v",
              "RETURN {a: 1, b: {c: 2}, d: [3]} AS v"):
        sql = translate_to_sql(parse_query(q), {}).sql
        assert not UNSIZED.search(sql if isinstance(sql, str) else " ".join(sql)), (q, sql)
