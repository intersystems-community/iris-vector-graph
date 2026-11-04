"""R234: scripts/install-embedded-deps.sh puts IVG's own import-time dependencies in IRIS's embedded Python.

The E2E tests that run IVG inside IRIS (test_embedded_irispython_e2e, test_host_conformance) copy the checkout into
the container and import it with `irispython`; `import iris_vector_graph` imports numpy (fusion.py), pydantic and
requests. The script installed igraph and leidenalg only, so on a freshly made container those 13 tests failed with
`No module named 'numpy'`; the long-lived container passed because numpy had been put there by hand.

Every core dependency in pyproject.toml is installed by the script except the two that provide the `iris` module
(the driver and the wrapper): inside IRIS, `iris` is IRIS's own, and a copy in /usr/irissys/mgr/python shadows it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install-embedded-deps.sh"

#: Packages that write a top-level `iris` module; never installed into IRIS's embedded Python.
IRIS_MODULE = {"intersystems-iris", "intersystems-irispython", "iris-embedded-python-wrapper"}


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0].strip().lower()


def core_dependencies() -> set[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return {_name(d) for d in deps}


def _pip_install_lines(text: str) -> list[str]:
    """Each `pip install` command in the script, its continuation lines joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [ln for ln in joined.splitlines() if "pip install" in ln and not ln.lstrip().startswith("#")]


def test_every_core_dependency_but_the_iris_module_is_installed():
    lines = " ".join(_pip_install_lines(SCRIPT.read_text()))
    installed = {_name(tok.strip("\"'")) for tok in lines.split() if not tok.startswith("-")}
    missing = sorted(core_dependencies() - IRIS_MODULE - installed)
    assert missing == [], f"install-embedded-deps.sh does not install {missing} into IRIS's embedded Python"


def test_nothing_that_writes_an_iris_module_is_installed():
    lines = " ".join(_pip_install_lines(SCRIPT.read_text()))
    installed = {_name(tok.strip("\"'")) for tok in lines.split() if not tok.startswith("-")}
    assert installed & IRIS_MODULE == set()


def test_the_script_checks_the_imports_it_installs():
    """The idempotence check and the post-condition import what was installed, so a half install is redone."""
    text = SCRIPT.read_text()
    for module in ("numpy", "pydantic", "requests", "igraph", "leidenalg"):
        assert re.search(rf"import [^\"']*\b{module}\b", text), f"the script never imports {module} to check it"
