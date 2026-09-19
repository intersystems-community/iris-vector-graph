"""A bare `pip install iris-vector-graph` must be able to `import iris_vector_graph`.

Found 2026-09-19 by the clean-venv install check in `PRE_RELEASE_CHECKLIST.md` §9,
against the 3.1.0 wheel on PyPI:

    $ pip install iris-vector-graph==3.1.0
    $ python -c "import iris_vector_graph"
    ModuleNotFoundError: No module named 'requests'

`__init__.py` imports `fhir_bridge` eagerly, `fhir_bridge.py` imports `requests`
unconditionally, and `requests` was declared only in the `[full]` extra. So the
advertised install produced a package that could not be imported at all — not a
degraded feature, the whole entry point. 3.0.1 failed identically; the defect dates
to `c46dc1b` (spec 027, FHIR-KG Clinical Bridge), which added the `fhir_bridge`
import to `__init__.py` without moving its dependency across.

It stayed invisible for as long as it did because every development and CI path in
this repo installs an extra, so `requests` was always already there. A unit test is
the right guard precisely because it runs in that same polluted environment: it reads
the *declarations* rather than trying to import anything, so it fails on the
packaging mistake even when the module happens to be installed.

`numpy` and `pydantic` were moved into core dependencies earlier for exactly this
reason — `pyproject.toml` says so in a comment. This test generalises that comment
into something enforced.
"""

import ast
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "iris_vector_graph"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Import name -> distribution name, for the cases where they differ. Only the
# third-party modules the eager chain can reach need an entry.
DISTRIBUTION_OF = {
    "iris": "iris-embedded-python-wrapper",
}


def _load_pyproject():
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        pytest.skip("tomllib requires Python 3.11+")
    return tomllib.loads(PYPROJECT.read_text())


def _core_dependency_names():
    """The distributions a plain `pip install iris-vector-graph` brings in."""
    import re

    deps = _load_pyproject()["project"]["dependencies"]
    return {re.split(r"[<>=!~\[ ]", spec, maxsplit=1)[0].strip().lower() for spec in deps}


def _module_file(dotted: str):
    """Resolve a dotted name inside the package to a file, or None."""
    as_module = PACKAGE_DIR / (dotted.replace(".", "/") + ".py")
    if as_module.is_file():
        return as_module
    as_package = PACKAGE_DIR / dotted.replace(".", "/") / "__init__.py"
    return as_package if as_package.is_file() else None


def _eager_third_party_imports():
    """Third-party top-level modules reachable by importing the package.

    Walks only unconditional module-level imports: an import nested in a function,
    a class, an `if`, or a `try`/`except ImportError` is a deliberate soft
    dependency and is allowed to live in an extra. Returns
    `{module_name: {importing file, ...}}`.
    """
    found: dict[str, set[str]] = {}
    visited: set[str] = set()

    def visit(dotted: str):
        if dotted in visited:
            return
        visited.add(dotted)
        path = _module_file(dotted) if dotted else PACKAGE_DIR / "__init__.py"
        if path is None:
            return
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.setdefault(alias.name.split(".")[0], set()).add(
                        str(path.relative_to(REPO_ROOT))
                    )
            elif isinstance(node, ast.ImportFrom):
                if not node.level:
                    found.setdefault(node.module.split(".")[0], set()).add(
                        str(path.relative_to(REPO_ROOT))
                    )
                    continue
                # Relative import: stay inside the package and keep walking.
                prefix = f"{dotted}." if (dotted and path.name == "__init__.py") else ""
                if dotted and path.name != "__init__.py" and "." in dotted:
                    prefix = dotted.rsplit(".", 1)[0] + "."
                target = f"{prefix}{node.module}" if node.module else prefix.rstrip(".")
                visit(target)
                for alias in node.names:
                    candidate = f"{target}.{alias.name}" if target else alias.name
                    if _module_file(candidate):
                        visit(candidate)

    visit("")
    return {
        name: files
        for name, files in found.items()
        if name not in sys.stdlib_module_names and name != "iris_vector_graph"
    }


def test_requests_is_a_core_dependency():
    """The specific regression. `fhir_bridge` is imported eagerly, so `requests` is
    not optional no matter which extra also happens to list it."""
    assert "requests" in _core_dependency_names()


def test_every_eager_third_party_import_is_a_core_dependency():
    """The class of defect, so the next `__init__` addition cannot repeat it."""
    core = _core_dependency_names()

    offenders = []
    for module, files in sorted(_eager_third_party_imports().items()):
        distribution = DISTRIBUTION_OF.get(module, module).lower()
        if distribution not in core:
            offenders.append(f"{module} (as {distribution}) imported by {sorted(files)[0]}")

    assert not offenders, (
        "imported unconditionally at package import time but not in core "
        "`dependencies`, so `pip install iris-vector-graph` yields a package that "
        "cannot be imported:\n" + "\n".join(offenders)
    )


def test_the_walk_actually_finds_the_known_eager_imports():
    """Guards the guard. If the walk silently stopped following relative imports it
    would find nothing and the test above would pass vacuously."""
    eager = _eager_third_party_imports()

    assert {"numpy", "pydantic", "requests"} <= set(eager)
    assert "iris_vector_graph/fhir_bridge.py" in eager["requests"]


def test_soft_dependencies_are_not_dragged_into_core():
    """`rdflib`, `fastapi`, and friends are imported inside functions or guarded by
    `try`/`except ImportError`, and must stay in their extras. If the walk started
    reporting them, it would be over-reporting and the class test would force a
    dependency the library does not need."""
    eager = set(_eager_third_party_imports())

    for optional in ("rdflib", "pyshacl", "fastapi", "uvicorn", "igraph", "leidenalg"):
        assert optional not in eager, (
            f"{optional} now looks eagerly imported. Either an import moved to module "
            "level (fix the import), or this walk over-reports (fix the walk)."
        )
