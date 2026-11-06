"""4.6.0 R050 (Python-newcomer walkthrough W3): every engine method a message names exists.

The `gds.pageRank.stream` refusal told the reader to call `engine.pagerank()`; the engine has no such method (it is
`kg_PAGERANK`). This reads every string in the package (error and warning texts, docstrings) for `engine.<name>(`, and
every string inside a `raise` or a call (a message being built) for `engine.<name>`, and asserts the engine has each.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from iris_vector_graph.engine import IRISGraphEngine

PACKAGE = Path(__file__).resolve().parents[2] / "iris_vector_graph"
CALLED = re.compile(r"\bengine\.([A-Za-z_]\w*)\s*\(")
NAMED = re.compile(r"\bengine\.([A-Za-z_]\w*)")


def _strings(tree):
    """(string, is_in_a_message) for every string constant; a message is one inside a raise or a call."""
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Raise, ast.Call)):
            inside.update(id(c) for c in ast.walk(node) if isinstance(c, ast.Constant))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node, id(node) in inside


def _named_methods():
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        for node, in_message in _strings(ast.parse(path.read_text(encoding="utf-8"))):
            pattern = NAMED if in_message else CALLED
            for m in pattern.finditer(node.value):
                found.setdefault(m.group(1), []).append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
    return found


def test_the_scan_finds_the_names_messages_use():
    names = _named_methods()
    # names the package's messages are known to give: the scan is not blind
    assert {"sync", "kg_PAGERANK", "betweenness_centrality"} <= set(names)


def test_every_engine_method_a_message_names_exists():
    missing = {n: where for n, where in _named_methods().items() if not hasattr(IRISGraphEngine, n)}
    assert not missing, missing


def test_the_gds_pagerank_refusal_names_methods_that_exist():
    from iris_vector_graph.cypher.parser import parse_query

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    parsed = parse_query("CALL gds.pageRank.stream() YIELD nodeId, score RETURN nodeId, score LIMIT 3")
    with pytest.raises(ValueError) as e:
        engine._execute_parsed(parsed, {})
    named = CALLED.findall(str(e.value))
    assert "kg_PAGERANK" in named, str(e.value)
    assert all(hasattr(IRISGraphEngine, n) for n in named), named
