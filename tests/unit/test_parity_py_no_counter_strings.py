"""No Python file under iris_vector_graph reads or writes `^KG("deg")` / `^KG("degp")` (spec 249).

The counters are removed (DEG-AUDIT.md). This is a source scan, so it runs without a container and keeps them out: it
fails on a string constant `"deg"` / `"degp"` (the subscript, passed to `host.get`, `native.set`, a `nextSubscript`, ...)
and on any string holding the ObjectScript spelling `^KG("deg"` / `^KG("degp"` (SQL `$Data(...)`, embedded code).

Docstrings and comments are not scanned: they may name the counters while explaining what was removed. Strings are
found with `ast`, not a regular expression over text, so a docstring is recognised as one.

The allow-list holds each exception with its reason. A new entry is a decision, not a convenience.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Tuple

PACKAGE = Path(__file__).resolve().parents[2] / "iris_vector_graph"

#: The subscripts. `deg2p` / `deg2p_exact` are different stores (two-hop statistics) and stay.
SUBSCRIPT_NAMES = frozenset({"deg", "degp"})
OBJECTSCRIPT_SPELLING = re.compile(r'\^KG\(\s*(?:\\)?"degp?"')

#: (path relative to the package, the exact string) -> reason.
ALLOW = {
    (
        "_engine/snapshot.py",
        "deg",
    ): "restore ignores `deg` lines of an old archive (3.2.0 and earlier wrote them); listed in LEGACY_KG_COUNTERS",
    (
        "_engine/snapshot.py",
        "degp",
    ): "restore ignores `degp` lines of an old archive; listed in LEGACY_KG_COUNTERS",
}


def _docstring_nodes(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    found.add(id(body[0].value))
    return found


def offending_strings(source: str) -> List[Tuple[int, str]]:
    """Every (line, string) in ``source`` that names a counter, excluding docstrings."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if text in SUBSCRIPT_NAMES or OBJECTSCRIPT_SPELLING.search(text):
            hits.append((node.lineno, text))
    return sorted(hits)


# --- the scanner itself ----------------------------------------------------------------------------------------


def test_the_scanner_catches_a_subscript_passed_to_the_host():
    assert offending_strings('host.get("^KG", "deg", g, s)\n') == [(1, "deg")]
    assert offending_strings('native.set(1, "^KG", "degp", g, s, p)\n') == [(1, "degp")]


def test_the_scanner_catches_the_objectscript_spelling_in_sql_or_code():
    assert offending_strings("""cursor.execute('SELECT $Data(^KG("deg"))')\n""")
    assert offending_strings('code = "Set x = $Get(^KG(\\"degp\\", g, s, p))"\n')


def test_the_scanner_leaves_docstrings_other_stores_and_comments_alone():
    source = (
        '"""Module doc: the old ^KG("deg") counter."""\n'
        "def f():\n"
        '    """Doc: ^KG("degp", g, s, p)."""\n'
        '    # host.get("^KG", "deg", g, s)\n'
        '    return host.get("^KG", "deg2p", g, s), "degree", "deg_total"\n'
    )
    assert offending_strings(source) == []


# --- the package -----------------------------------------------------------------------------------------------


def _package_sources():
    return sorted(p for p in PACKAGE.rglob("*.py") if "browser_static" not in p.parts)


def test_the_package_is_found():
    assert len(_package_sources()) > 20, PACKAGE


def test_no_python_file_names_a_stored_degree_counter_outside_the_allow_list():
    offenders = []
    for path in _package_sources():
        rel = path.relative_to(PACKAGE).as_posix()
        for lineno, text in offending_strings(path.read_text(encoding="utf-8")):
            if (rel, text) not in ALLOW:
                offenders.append(f"{rel}:{lineno}: {text!r}")
    assert not offenders, "stored degree counter named in code:\n" + "\n".join(offenders)


def test_every_allow_list_entry_is_still_needed():
    """An exception nobody uses is a hole that stays open."""
    used = set()
    for path in _package_sources():
        rel = path.relative_to(PACKAGE).as_posix()
        for _, text in offending_strings(path.read_text(encoding="utf-8")):
            used.add((rel, text))
    stale = sorted(set(ALLOW) - used)
    assert not stale, f"allow-listed but not present: {stale}"
    assert all(reason.strip() for reason in ALLOW.values())


def test_the_snapshot_back_compat_ignore_is_one_named_constant():
    """The only place a counter name may appear is the tuple the restore ignores."""
    from iris_vector_graph._engine import snapshot

    assert tuple(snapshot.LEGACY_KG_COUNTERS) == ("deg", "degp")
    assert tuple(snapshot.DERIVED_KG_CACHE) == ("gen", "degc", "degw")
