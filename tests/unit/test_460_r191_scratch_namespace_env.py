"""4.6.0 R191: an E2E or integration file that drops or rebuilds a scratch namespace takes it from an env var.

A regression run by one agent wiped a namespace another was using: `ns262.recreate("IVG262CONA")` and its kind drop
and rebuild a namespace named in the file, whoever else is in it. Each such file now reads the namespace from an
environment variable whose default is the old name (`NS = os.environ.get("IVG_NS_IVG262ALT", "IVG262ALT")`), and
names that variable in its module docstring, so two runs can be pointed at two namespaces.

The guard, over the test files of tests/e2e, tests/integration and tests/docs: a file that drops or rebuilds a
namespace (a `recreate(...)` or `drop(...)` of ns262 / make_scratch_namespace, or `Config.Namespaces).Delete(`) holds
no scratch namespace name (IVG262*, IVG460*, IVG252*, IVG463*) as a string of its own anywhere but as an `os.environ.get`
default (a prefix passed to `startswith`, a message or a pattern is not a name), and its docstring names every such
variable. PER_RUN lists the files whose namespace is made per run, with a name no other run can hold.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = re.compile(r"IVG(?:262|460|252|463)[A-Z0-9]+")
DROPPERS = {"ns262", "make_scratch_namespace", "msn"}
#: files that drop a namespace made per run, which no other run can be using
PER_RUN = {"test_embedded_irispython_e2e.py": "IVGEMB plus a uuid, made and deleted by the test"}
#: the variables the E2E gate names per run (tests/e2e/gate.py GROUPS); their default is '' since 4.6.0
GATE_VARS = {g.var for g in __import__("tests.e2e.gate", fromlist=["GROUPS"]).GROUPS.values()}


def _drops_a_namespace(tree: ast.AST, text: str) -> bool:
    if "Config.Namespaces).Delete(" in text:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ("recreate", "drop") and isinstance(f.value, ast.Name):
                if f.value.id in DROPPERS:
                    return True
    return False


def _env_defaults(tree: ast.AST) -> dict:
    """{id(default constant): env var name} for every os.environ.get("IVG_...", "<default>")."""
    out = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[1], ast.Constant)
        ):
            out[id(node.args[1])] = node.args[0].value
    return out


def _startswith_args(tree: ast.AST) -> set:
    """Ids of `startswith` arguments, and of the prefixes in a tuple passed to it (`startswith(("IVG262", "IVG460FX"))`)."""
    return {
        id(e)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith"
        for a in node.args
        for e in ([a] + (list(a.elts) if isinstance(a, ast.Tuple) else []))
    }


def _docstrings(tree: ast.AST) -> set:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _files():
    for sub in ("tests/e2e", "tests/integration", "tests/docs"):
        yield from sorted((ROOT / sub).glob("test_*.py"))


DROPPING = []
for _path in _files():
    _text = _path.read_text()
    _tree = ast.parse(_text)
    if _drops_a_namespace(_tree, _text):
        DROPPING.append(_path)


def test_the_guard_sees_the_files_it_is_about():
    names = {p.name for p in DROPPING}
    assert {"test_460_r174_gds_shims_e2e.py", "test_262_alt_schema_paths.py", "test_252_fresh_namespace_e2e.py"} <= names


@pytest.mark.parametrize("path", DROPPING, ids=lambda p: p.name)
def test_a_dropped_namespace_comes_from_an_env_var_named_in_the_header(path):
    text = path.read_text()
    tree = ast.parse(text)
    defaults, skip = _env_defaults(tree), _docstrings(tree) | _startswith_args(tree)
    bare = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
        and id(node) not in defaults and SCRATCH.fullmatch(node.value)
    ]
    assert bare == [], f"{path.name}: scratch namespace names outside os.environ.get defaults: {bare}"
    if path.name in PER_RUN:
        return
    header = ast.get_docstring(tree) or ""
    # a variable whose default is an old scratch name, or one the E2E gate names per run (default '', 4.6.0)
    used = sorted({v for k, v in defaults.items() if SCRATCH.fullmatch(_default_value(tree, k) or "") or v in GATE_VARS})
    assert used, f"{path.name}: drops a namespace it does not take from an env var"
    missing = [v for v in used if v not in header]
    assert missing == [], f"{path.name}: the module docstring does not name {missing}"


def _default_value(tree: ast.AST, const_id: int):
    for node in ast.walk(tree):
        if id(node) == const_id:
            return node.value
    return None


# --------------------------------------------------------------------------- fixtures (4.6.0 R287)
#
# A fixture that puts IVG into a namespace (deploys the classes, runs setup, creates or rebuilds it) does to that
# namespace what a dropping file does. The `ivg_236_scratch` fixture deployed this checkout's classes into IVG236, a
# name written in tests/conftest.py, so one agent's run overwrote IVG236 for everyone, and a helper agent ran it there
# again on 2026-11-04. The same rule as above, for the modules that define fixtures: each namespace comes from an
# environment variable, named in the module docstring. The IVG236 scratch has no default: its fixture refuses to run
# until IVG_NS_IVG236 names a namespace the caller owns. A test file using that fixture names the namespace through the
# same variable or the fixture's `.namespace`, never by the literal, or it writes where the fixture did not set up.

#: A namespace name: upper-case IVG and more (IVG236, IVGFHIR, IVG460DOC).
NAMESPACE = re.compile(r"IVG[A-Z0-9]+")
#: What a fixture module calls to put IVG into a namespace.
SETS_UP = re.compile(r"deploy_packaged_classes|setup_namespace\.sh|initialize_schema\(|tcp-deploy|make_scratch_namespace"
                     r"|\.recreate\(")


def _fixture_modules():
    for path in sorted((ROOT / "tests").rglob("*conftest*.py")):
        text = path.read_text()
        if "pytest.fixture" in text and SETS_UP.search(text):
            yield path


FIXTURE_MODULES = list(_fixture_modules())


def test_the_fixture_guard_sees_the_modules_it_is_about():
    names = {str(p.relative_to(ROOT)) for p in FIXTURE_MODULES}
    assert {"tests/conftest.py", "tests/e2e/fhir_conftest.py"} <= names


@pytest.mark.parametrize("path", FIXTURE_MODULES, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_fixture_module_takes_its_namespaces_from_env_vars_named_in_the_header(path):
    text = path.read_text()
    tree = ast.parse(text)
    defaults, skip = _env_defaults(tree), _docstrings(tree) | _startswith_args(tree)
    bare = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
        and id(node) not in defaults and NAMESPACE.fullmatch(node.value)
    ]
    assert bare == [], f"{path.name}: namespace names outside os.environ.get defaults: {bare}"
    header = ast.get_docstring(tree) or ""
    named = sorted({v for k, v in defaults.items() if v.startswith("IVG_NS_")})
    missing = [v for v in named if v not in header]
    assert missing == [], f"{path.name}: the module docstring does not name {missing}"


def test_the_236_scratch_has_no_default_and_the_fixture_refuses_without_one():
    text = (ROOT / "tests" / "conftest.py").read_text()
    tree = ast.parse(text)
    defaults = {v: _default_value(tree, k) for k, v in _env_defaults(tree).items()}
    assert defaults.get("IVG_NS_IVG236") == ""
    fixture = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ivg_236_scratch")
    body = ast.get_source_segment(text, fixture)
    assert "IVG_NS_IVG236" in body and "pytest.skip(" in body
    assert body.index("pytest.skip(") < body.index("deploy_packaged_classes")
    assert "initialize_schema(" in body  # the setup functions (CY_TYPED and the rest) exist after it


def _uses_the_236_scratch(text: str) -> bool:
    return "ivg_236_scratch" in text or "Scratch236" in text


USING_236 = [p for p in sorted((ROOT / "tests").rglob("*.py"))
             if p.name != "conftest.py" and p != Path(__file__).resolve() and _uses_the_236_scratch(p.read_text())]


@pytest.mark.parametrize("path", USING_236, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_file_using_the_236_scratch_never_names_it_by_the_literal(path):
    tree = ast.parse(path.read_text())
    skip = _docstrings(tree) | _startswith_args(tree)
    bare = [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == "IVG236" and id(node) not in skip]
    assert bare == [], f"{path.name}: names IVG236; use os.environ['IVG_NS_IVG236'] or the fixture's .namespace: {bare}"
