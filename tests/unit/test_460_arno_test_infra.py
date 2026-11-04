"""4.6.0 R171: the Arno integration tests connect where the rest of the suite does, and say why when they skip.

- The Arno helpers fell back to localhost:31971 when OrbStack DNS was down. ivg-iris-enterprise maps 31971 to its
  port 19721, not the SuperServer, so every Arno fixture failed with a COMMUNICATION LINK ERROR. They read
  IVG_ARNO_PORT, then IVG_PORT, like tests/conftest.py's other connections (default 31972).
- The five Arno integration modules redirected `createIRIS` to a native connection in USER, while their data went
  in through `arno_iris_connection` in the run's namespace, so they were marked USER-only and skipped in every
  scratch run. They open the native connection in TEST_NAMESPACE.
- `test_execution_contexts_new` named a container that does not exist (`iris-vector-graph-enterprise`), so its
  ObjectScript-engine class never ran. It uses the test container and the run's namespace.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]
ARNO_MODULES = [
    TESTS / "integration" / name
    for name in (
        "test_arno_adjacency_helpers.py",
        "test_arno_deploy.py",
        "test_arno_deploy_flow.py",
        "test_arno_dispatch_bugs.py",
        "test_arno_rust_always_on.py",
    )
]


@pytest.mark.parametrize(
    "env, want",
    [
        ({}, 31972),
        ({"IVG_PORT": "41972"}, 41972),
        ({"IVG_ARNO_PORT": "31972", "IVG_PORT": "41972"}, 31972),
        ({"IVG_ARNO_PORT": "", "IVG_PORT": "41972"}, 41972),
    ],
)
def test_arno_port_follows_the_run(env, want):
    from tests.conftest import arno_port

    assert arno_port(env) == want


def _calls(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == name) or (isinstance(f, ast.Name) and f.id == name):
                yield node


def _kw(call, key):
    for k in call.keywords:
        if k.arg == key:
            return k.value
    return None


@pytest.mark.parametrize("path", sorted(TESTS.rglob("*.py")), ids=lambda p: str(p.relative_to(TESTS)))
def test_no_connection_targets_port_31971(path):
    """A literal 31971 as a connect() port, or as the default of an *_PORT variable, is the wrong listener."""
    text = path.read_text()
    if "31971" not in text or path.name == "test_230_test_port_defaults.py" or path == Path(__file__):
        return
    tree = ast.parse(text)
    for call in list(_calls(tree, "connect")):
        port = _kw(call, "port")
        assert not (isinstance(port, ast.Constant) and str(port.value) == "31971"), f"{path}:{call.lineno}"
    assert not re.search(r"environ\.get\([^)]*PORT[^)]*,\s*['\"]31971['\"]\)", text), path


@pytest.mark.parametrize("path", ARNO_MODULES, ids=lambda p: p.name)
def test_arno_modules_open_the_run_s_namespace(path):
    text = path.read_text()
    tree = ast.parse(text)
    for call in _calls(tree, "connect"):
        ns = _kw(call, "namespace")
        assert not (isinstance(ns, ast.Constant) and ns.value == "USER"), f"{path.name}:{call.lineno}"


@pytest.mark.parametrize("path", ARNO_MODULES, ids=lambda p: p.name)
def test_arno_modules_are_not_skipped_whole_outside_user(path):
    """A test that needs USER says so itself; the module does not skip as a whole."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pytestmark" for t in node.targets):
            assert "requires_user_namespace" not in ast.unparse(node.value), path.name


TEST_NAMESPACE_ARG = '"-U", TEST_NAMESPACE'


def test_execution_contexts_uses_the_test_container():
    text = (TESTS / "e2e" / "test_execution_contexts_new.py").read_text()
    assert "iris-vector-graph-enterprise" not in text
    assert TEST_NAMESPACE_ARG in text  # the session runs in the run's namespace
