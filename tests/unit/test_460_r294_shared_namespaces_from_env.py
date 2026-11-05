"""4.6.0 R294: no live test names the shared scratch namespaces IVG236 and IVGFHIR in its code.

R191 and R287 made the files and fixtures that set a namespace up take it from the environment. Two kinds were left:
`tests/e2e/test_host_conformance.py` connected to `NAMESPACE = "IVG236"`, whoever was using it, and the FHIR files
wrote `"IVGFHIR"` into graph ids (`fhir:IVGFHIR:erase-other`), the task's namespace check, and the `iris session -U`
of a process they start, so pointed at another namespace (`IVG_NS_IVGFHIR`) they wrote or checked IVGFHIR anyway.

The guard: a test file under tests/e2e, tests/integration or tests/docs holds neither name in a string of its code (an
f-string's literal parts included), but as an `os.environ.get` default or a section title (DOC_TITLES). Docstrings
and comments may name them. The 236 scratch has no default (R287): `IVG_NS_IVG236`; the FHIR namespace is
`tests/e2e/fhir_conftest.NAMESPACE` (`IVG_NS_IVGFHIR`, default IVGFHIR).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: The names as words: IVG_NS_IVG236 (the variable), IVG236BFS (a predicate) and IVG236Tmp (a package) are not them.
SHARED = re.compile(r"(?<![A-Za-z0-9_])(IVG236|IVGFHIR)(?![A-Za-z0-9_])")
#: Text that names the namespace as the title of the section that makes it, not as where to connect.
DOC_TITLES = {"CONTRIBUTING.md, 'The FHIR namespace (IVGFHIR)'"}


def _skipped(tree: ast.AST) -> set:
    """Ids of the docstrings and of the `os.environ.get(name, default)` defaults."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "environ"
                and len(node.args) == 2):
            ids.add(id(node.args[1]))
    return ids


def _named(path: Path) -> list:
    tree = ast.parse(path.read_text())
    skip = _skipped(tree)
    return sorted({(node.lineno, m.group(1)) for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
                   and node.value not in DOC_TITLES
                   for m in SHARED.finditer(node.value)})


FILES = [p for sub in ("tests/e2e", "tests/integration", "tests/docs") for p in sorted((ROOT / sub).glob("*.py"))]


def test_the_guard_sees_the_files_it_is_about():
    names = {p.name for p in FILES}
    assert {"test_host_conformance.py", "test_231_fhir_graph_sync_e2e.py", "test_fhirgraph_failures_e2e.py",
            "fhir_conftest.py"} <= names


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_shared_scratch_namespace_is_named_in_code(path):
    assert _named(path) == [], f"{path.name} names a shared namespace outside an os.environ.get default"
