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
