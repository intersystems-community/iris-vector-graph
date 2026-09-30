"""Every shipped module compiles with warnings as errors.

An invalid escape sequence (``"\\d"``, ``"\\ "``) is a silent DeprecationWarning on
3.11, a SyntaxWarning on 3.12+, and a planned SyntaxError. The warning fires only
when the module is compiled, not when a cached ``.pyc`` is loaded, and pytest's
``--disable-warnings`` hides it anyway, so an import-based test never sees it.
Compiling the source text directly does, on every interpreter we support.
"""

import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("iris_vector_graph", "tests", "scripts")
MODULES = sorted(
    p
    for tree in TREES
    for p in (ROOT / tree).rglob("*.py")
    if "__pycache__" not in p.parts
)


def test_the_package_has_modules():
    assert sum(1 for p in MODULES if p.parts[len(ROOT.parts)] == "iris_vector_graph") > 50


def test_every_module_compiles_without_warnings():
    bad = []
    for path in MODULES:
        source = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                compile(source, str(path), "exec")
            except (SyntaxWarning, DeprecationWarning, SyntaxError) as exc:
                bad.append(f"{path.relative_to(ROOT)}:{getattr(exc, 'lineno', '?')}: {exc}")
    assert not bad, "\n".join(bad)
