"""Every shipped `.cls` must be parseable by the IRIS class compiler.

Deploy is all-or-nothing per file, and a file that cannot be parsed is not a
warning: `%SYSTEM.OBJ.LoadDir` reports `ERROR #5559` and the class simply is not
there, so callers get `<CLASS DOES NOT EXIST>` at the point of use rather than at
deploy time. `iris_src/src/PageRankEmbedded.cls` shipped in that state long enough
to be written down in `docs/KNOWN_ISSUES.md`, because
`scripts/enterprise-container.sh compile-all` greps `PageRankEmbed` out of its own
error display.

The cause was its method name. An ObjectScript identifier may start with a letter
or with `%`; `_` is the concatenation operator, so `ClassMethod _computeFoo()` is
a syntax error in the class body and takes the whole file down with it. The name
came from Python, where a leading underscore is the convention for "internal" —
in ObjectScript that job belongs to the `[ Internal ]` keyword, which the same
method already carried.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"

#: `Method Foo(`, `ClassMethod Foo(`, and the quoted/delimited forms.
_METHOD = re.compile(r"^\s*(?:Class)?Method\s+(\S+?)\s*\(", re.MULTILINE)


def _class_files() -> list:
    return sorted(SRC.rglob("*.cls"))


def test_the_class_tree_is_readable():
    """Guard the guard: an empty file list would pass the scan below."""
    files = _class_files()
    assert len(files) > 30, f"found only {len(files)} .cls files under {SRC}"


@pytest.mark.parametrize("path", _class_files(), ids=lambda p: p.name)
def test_no_method_name_starts_with_an_underscore(path):
    """`_name` is not an identifier in ObjectScript — it is `_` then `name`."""
    offenders = [
        name for name in _METHOD.findall(path.read_text()) if name.startswith("_")
    ]
    assert offenders == [], (
        f"{path.name} declares {offenders}, which the class compiler cannot parse "
        "(ERROR #5559) — the whole file fails to load. Drop the underscore and mark "
        "the method [ Internal ] instead."
    )
