"""Spec 186 Phase A — complexity guard.

Self-contained AST-based cyclomatic-complexity counter (no external dep, per
NFR-001). Enforces SC-002: no function in cypher/translator.py exceeds
cyclomatic complexity 25 after Phase B.

Starts as xfail; Phase B (translator decomposition) flips it to pass by
removing the strict=True / xfail marker.
"""
from __future__ import annotations

import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRANSLATOR = os.path.join(REPO_ROOT, "iris_vector_graph", "cypher", "translator.py")

# Threshold from spec 186 SC-002.
MAX_CC = 25

ALLOWLIST = {}


class _CCVisitor(ast.NodeVisitor):
    """Counts McCabe cyclomatic complexity for a single function body.

    CC = 1 + number of decision points. Decision points: if/elif, for, while,
    each boolean operator clause beyond the first, except handlers, with-items,
    comprehension 'if' clauses, ternary expressions, and 'assert'.
    """

    def __init__(self) -> None:
        self.score = 1

    def visit_If(self, node: ast.If) -> None:
        self.score += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.score += 1
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.score += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.score += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # n operands => n-1 additional branches
        self.score += len(node.values) - 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.score += 1
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.score += len(node.ifs)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.score += 1
        self.generic_visit(node)

    def visit_Match(self, node) -> None:  # py310+ match statement
        # each case after the first is a branch
        self.score += max(0, len(node.cases) - 1)
        self.generic_visit(node)


def _complexity_of(func: ast.AST) -> int:
    visitor = _CCVisitor()
    # Visit only the function body, not nested function defs (they get their
    # own measurement when iterated separately).
    for stmt in getattr(func, "body", []):
        visitor.visit(stmt)
    return visitor.score


def _all_functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _offenders(path: str, threshold: int = MAX_CC):
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    bad = []
    for fn in _all_functions(tree):
        cc = _complexity_of(fn)
        if cc > threshold:
            bad.append((fn.name, fn.lineno, cc))
    bad.sort(key=lambda t: t[2], reverse=True)
    return bad


def test_translator_no_function_exceeds_cc25():
    offenders = _offenders(TRANSLATOR)
    msg = "\n".join(f"  {name} (line {ln}): cc={cc}" for name, ln, cc in offenders)
    assert not offenders, (
        f"{len(offenders)} function(s) in cypher/translator.py exceed "
        f"cyclomatic complexity {MAX_CC}:\n{msg}"
    )


def test_translator_complexity_within_budget():
    offenders = _offenders(TRANSLATOR)
    regressions = []
    for name, ln, cc in offenders:
        budget = ALLOWLIST.get(name)
        if budget is None:
            regressions.append(f"  NEW offender {name} (line {ln}): cc={cc} > {MAX_CC}")
        elif cc > budget:
            regressions.append(f"  {name} (line {ln}): cc={cc} regressed past allowlisted {budget}")
    assert not regressions, "Translator complexity regression:\n" + "\n".join(regressions)
