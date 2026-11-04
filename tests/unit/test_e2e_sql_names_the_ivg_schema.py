"""R234: an E2E test's SQL names IVG's tables in the IVG Schema, not unqualified or in SQLUser.

Spec 262 removed the 4.5.x views `SQLUser.nodes`, `rdf_edges`, `rdf_labels` and `rdf_props`. An unqualified `FROM
nodes` resolves to SQLUser, so a test that wrote one passed only on a container whose namespaces an older release had
set up (the views were still there) and failed with `Table 'SQLUSER.NODES' not found` on a fresh one (20 errors in
the first full E2E run on a new container). The test files that build a 4.5.x state on purpose are exempt.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

E2E = Path(__file__).resolve().parents[1] / "e2e"

#: Files that create or inspect a legacy (pre-4.6.0) namespace, where SQLUser names are the subject.
LEGACY = re.compile(r"^(test_262_|ns262|test_227_migration|test_460_legacy_manifest|test_upgrade_)")

TABLES = "nodes|rdf_edges|rdf_labels|rdf_props|rdf_reifications|kg_NodeEmbeddings|docs"
UNQUALIFIED = re.compile(rf"\b(?:FROM|INTO|UPDATE|JOIN)\s+(?:SQLUser\.)?(?:{TABLES})\b", re.IGNORECASE)


def _sql_literals(tree: ast.AST):
    """(line, text) of each string literal passed first to an `execute`/`executemany` call."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("execute", "executemany") or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            yield arg.lineno, arg.value
        elif isinstance(arg, ast.JoinedStr):
            text = "".join(v.value for v in arg.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            yield arg.lineno, text


def offenders() -> list[str]:
    found = []
    for path in sorted(E2E.rglob("*.py")):
        if LEGACY.match(path.name):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for line, text in _sql_literals(tree):
            if UNQUALIFIED.search(text):
                found.append(f"{path.relative_to(E2E.parent)}:{line}: {text.strip()[:120]}")
    return found


def test_the_pattern_catches_what_failed_on_the_fresh_container():
    assert UNQUALIFIED.search("SELECT node_id FROM nodes WHERE node_id LIKE ?")
    assert UNQUALIFIED.search("SELECT COUNT(*) FROM SQLUser.rdf_edges")
    assert not UNQUALIFIED.search("SELECT node_id FROM Graph_KG.nodes WHERE node_id LIKE ?")
    assert not UNQUALIFIED.search("SELECT COUNT(*) FROM Graph_KG.rdf_edges")


def test_no_e2e_sql_reads_an_ivg_table_through_sqluser():
    assert offenders() == []


def bare_table_calls() -> list[str]:
    """`_table(name)` calls with no prefix in tests/e2e: outside a translation they name the translator's module
    prefix, module state any test may set (tests/unit/test_schema_prefix_isolation.py), not the namespace's IVG
    Schema. Until 4.6.0 R265 an unset one named the table unqualified, which resolves in SQLUser:
    test_cypher_vector_search's fixture wrote its vectors that way, swallowed the -30, and searched an empty route."""
    found = []
    for path in sorted(E2E.rglob("*.py")):
        if LEGACY.match(path.name):
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_table"
                    and len(node.args) + len(node.keywords) == 1):
                found.append(f"{path.relative_to(E2E.parent)}:{node.lineno}")
    return found


def test_no_e2e_test_names_a_table_through_a_bare__table_call():
    assert bare_table_calls() == []
