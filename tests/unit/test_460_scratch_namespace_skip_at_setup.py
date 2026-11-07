"""An E2E file whose scratch-namespace variable is unset collects, and its tests skip when they set up.

The E2E files that read a per-run namespace (tests/e2e/gate.py GROUPS) skipped at import when it was unset:
`pytest.skip("set X to a scratch namespace of this run", allow_module_level=True)`. In gate mode (`IVG_GATE=1`,
scripts/ci-parity.sh) a module skipped at import fails the run, so the documented bare `pytest --collect-only`
failed there with 38 modules (tests/unit/test_230_collection_integrity.py). They now carry
`tests/e2e/scratch_vars.requires(...)`: the module imports, and each test skips at setup with the same message,
which the gate turns into a failure of that test (a scratch namespace not provided is never an allowed skip).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.e2e import gate, scratch_vars

REPO = Path(__file__).resolve().parents[2]
E2E = REPO / "tests" / "e2e"
MESSAGE = "to a scratch namespace of this run"
#: Every per-run variable but IVG_NAMESPACE, which the session itself reads (tests/conftest.py)
VARS = sorted({g.var for g in gate.GROUPS.values()} - {"IVG_NAMESPACE"})


def _requiring_modules() -> dict:
    """{file name: variables} for every tests/e2e module that calls scratch_vars.requires(...)."""
    out = {}
    for path in sorted(E2E.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "requires"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "scratch_vars"):
                out.setdefault(path.name, []).extend(a.value for a in node.args if isinstance(a, ast.Constant))
    return out


def test_requires_skips_naming_the_first_unset_variable(monkeypatch):
    monkeypatch.delenv("IVG_R174_NAMESPACE", raising=False)
    monkeypatch.setenv("IVG_R175_NAMESPACE", "IVG460FXZZ")
    mark = scratch_vars.requires("IVG_R175_NAMESPACE", "IVG_R174_NAMESPACE")
    assert mark.name == "skipif"
    assert mark.args == (True,)
    assert mark.kwargs["reason"] == "set IVG_R174_NAMESPACE to a scratch namespace of this run"


def test_requires_does_not_skip_when_every_variable_is_set(monkeypatch):
    monkeypatch.setenv("IVG_R174_NAMESPACE", "IVG460FXZZ")
    mark = scratch_vars.requires("IVG_R174_NAMESPACE")
    assert mark.args == (False,)


def test_no_e2e_file_skips_at_import_for_an_unset_scratch_namespace():
    offenders = []
    for path in sorted(E2E.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "skip"
                    and any(k.arg == "allow_module_level" for k in node.keywords)
                    and any(isinstance(a, ast.Constant) and MESSAGE in str(a.value) for a in node.args)):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"skip at setup with scratch_vars.requires(...) instead: {offenders}"


def test_the_scan_sees_the_modules_this_is_about():
    found = _requiring_modules()
    for name, var in [("test_460_r174_gds_shims_e2e.py", "IVG_R174_NAMESPACE"),
                      ("test_262_two_engines.py", "IVG_NS_IVG262UPG"),
                      ("test_460_r308_abs_cached_e2e.py", "IVG_NS_IVG460FXOC"),
                      ("test_migration_4_0_0_statement_count.py", "IVG_LEGACY_NAMESPACE")]:
        assert var in found.get(name, []), name


# Prints, per collected test, whether a skipif that fires carries the scratch-namespace message (other marks, such as
# the container check, fire here too: the Docker daemon is hidden).
_PLUGIN = textwrap.dedent("""
    def pytest_collection_finish(session):
        for item in session.items:
            fires = [m.kwargs.get("reason", "") for m in item.iter_markers("skipif") if m.args and m.args[0] is True]
            scratch = any(r.startswith("set ") and r.endswith(" to a scratch namespace of this run") for r in fires)
            print(f"SKIPAT {item.path.name} {scratch}")
""")


def _collect(tmp_path, files, env_vars: dict):
    (tmp_path / "skipat_plugin.py").write_text(_PLUGIN)
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(env_vars)
    env.update(IVG_GATE="1", IVG_GATE_NAMESPACES="0", SKIP_IRIS_TESTS="true",
               DOCKER_HOST="unix:///nonexistent/ivg-test.sock",
               PYTHONPATH=os.pathsep.join([str(tmp_path), str(REPO)]))
    return subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                           "-p", "skipat_plugin", *files], cwd=REPO, env=env, capture_output=True, text=True,
                          timeout=600)


def test_with_the_variables_unset_every_such_module_collects_and_its_tests_skip_at_setup(tmp_path):
    modules = _requiring_modules()
    result = _collect(tmp_path, [f"tests/e2e/{m}" for m in modules], {})
    out = result.stdout + result.stderr
    assert result.returncode == 0, out[-3000:]
    assert "skipped at import" not in out, out[-3000:]
    seen: dict = {}
    for line in result.stdout.splitlines():
        if line.startswith("SKIPAT "):
            _, name, scratch_skip = line.split(" ")
            seen.setdefault(name, set()).add(scratch_skip)
    # every module collected tests, and each of those tests skips for the unset namespace
    assert {m: seen.get(m) for m in modules} == {m: {"True"} for m in modules}


def test_with_the_variables_set_the_tests_do_not_skip_for_them(tmp_path):
    modules = _requiring_modules()
    names = {v: "IVG460FXZZ" for vs in modules.values() for v in vs}
    result = _collect(tmp_path, [f"tests/e2e/{m}" for m in modules], names)
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
    lines = [line for line in result.stdout.splitlines() if line.startswith("SKIPAT ")]
    assert {line.split(" ")[1] for line in lines} == set(modules)
    assert [line for line in lines if line.endswith(" True")] == []
