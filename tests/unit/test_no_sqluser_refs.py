"""Spec 262 T012: IVG's code names no `SQLUser` object and reads no table unqualified (FR-002, FR-007, FR-008).

An unqualified `FROM rdf_edges` resolved through the 4.5.x `SQLUser` views, which 4.6.0 no longer creates; a
`SQLUser.<fn>` call resolves to a function an application may own. The legacy manifest and the code that copies grants
from, or removes, the 4.5.x objects are the only places allowed to name them; a line there that must is marked
`# 4.5.x legacy`.
"""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "iris_vector_graph"
EXEMPT_FILES = {"legacy_manifest.py"}
MARK = "# 4.5.x legacy"
SQLUSER = re.compile(r"SQLUser\.")
UNQUALIFIED = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(rdf_edges|rdf_props|rdf_labels|nodes)\b(?!\.)")


def _offenders(pattern):
    found = []
    for path in sorted(PKG.rglob("*.py")):
        if path.name in EXEMPT_FILES:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and MARK not in line:
                found.append(f"{path.relative_to(PKG)}:{n}: {line.strip()[:120]}")
    return found


def test_no_sqluser_reference():
    assert _offenders(SQLUSER) == []


def _sql_literals(path):
    """`(line, text)` of every string literal in `path` that is not a docstring: the SQL a module can send."""
    import io
    import tokenize

    toks = [t for t in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
            if t.type not in (tokenize.NL, tokenize.COMMENT)]
    for i, t in enumerate(toks):
        if t.type != tokenize.STRING:
            continue
        before = toks[i - 1].type if i else tokenize.NEWLINE
        after = toks[i + 1].type if i + 1 < len(toks) else tokenize.ENDMARKER
        if before in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT) and after in (
                tokenize.NEWLINE, tokenize.ENDMARKER):
            continue
        yield t.start[0], t.string


def test_no_unqualified_table_read():
    found = []
    for path in sorted(PKG.rglob("*.py")):
        if path.name in EXEMPT_FILES:
            continue
        for line, text in _sql_literals(path):
            if UNQUALIFIED.search(text):
                found.append(f"{path.relative_to(PKG)}:{line}: {text.strip()[:120]}")
    assert found == []


#: Test files that call a `SQLUser` routine of an IVG routine's name on purpose: a 4.5.x copy in a namespace they build
#: or upgrade, or an application's own function (R151).
LEGACY_CALLERS = {
    "e2e/test_262_fresh_namespace.py": "an application's own SQLUser.RAND must keep answering beside Graph_KG.RAND",
    "e2e/test_262_not_upgraded.py": "a namespace 4.6.0 has not set up keeps answering through its 4.5.x routines",
    "e2e/test_262_upgrade_451.py": "the 4.5.1 install's own routines, before and after the upgrade",
    "e2e/test_262_vendored.py": "an application that vendored 4.5.x routines keeps them",
}
#: `CREATE ... FUNCTION <schema>.<name>(`, the schema written out or as a format field (`{schema}`, `{s}`).
_ROUTINE = re.compile(r"(?:FUNCTION|PROCEDURE)\s+(?:\{\w*\}|\w+)\.(\w+)\s*\(", re.IGNORECASE)


def test_no_test_calls_an_ivg_routine_through_sqluser():
    """Since spec 262 IVG's routines live in the IVG Schema; a test calling the `SQLUser` one calls a 4.5.x copy, or
    nothing (R151: tests/e2e/test_cy_sort_key_e2e.py called CY_SORT_KEY there)."""
    tests = PKG.parent / "tests"
    routines = {name for path in PKG.rglob("*.py") if path.name not in EXEMPT_FILES
                for name in _ROUTINE.findall(path.read_text(encoding="utf-8"))}
    assert {"CY_SORT_KEY", "CY_CMP", "kg_KNN_VEC"} <= routines, sorted(routines)
    call = re.compile(r"SQLUser\.(" + "|".join(sorted(routines)) + r")\s*\(")
    found = []
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(tests).as_posix()
        if rel in LEGACY_CALLERS:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if call.search(line):
                found.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert found == []
