"""The connection shim that points benchmark scripts at the side's namespace (spec 250).

Many benchmark scripts hard-code host, port or namespace (USER, 1972, 21972). The shim is a
sitecustomize module on the side's PYTHONPATH that rewrites `iris.connect` / `iris.dbapi.connect`
arguments from IVG_BENCH_* variables. It is loaded here by path, with a fake `iris` module.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parents[2] / "scripts" / "bench" / "alt" / "shim" / "sitecustomize.py"


@pytest.fixture()
def shim():
    spec = importlib.util.spec_from_file_location("ivg_bench_sitecustomize", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENV = {"IVG_BENCH_NS": "IVGBENCHA", "IVG_BENCH_PORT": "31972", "IVG_BENCH_HOST": "localhost"}


def test_unset_environment_leaves_arguments_alone(shim):
    args, kwargs = shim.rewrite_connect_args(("h", 1, "USER", "u", "p"), {}, env={})
    assert args == ("h", 1, "USER", "u", "p") and kwargs == {}


def test_keyword_arguments_are_rewritten(shim):
    args, kwargs = shim.rewrite_connect_args(
        (), {"hostname": "x", "port": 1972, "namespace": "USER", "username": "u", "password": "p"}, env=ENV
    )
    assert kwargs == {
        "hostname": "localhost",
        "port": 31972,
        "namespace": "IVGBENCHA",
        "username": "u",
        "password": "p",
    }


def test_positional_arguments_are_rewritten(shim):
    args, kwargs = shim.rewrite_connect_args(("host", 21972, "USER", "_SYSTEM", "SYS"), {}, env=ENV)
    assert args[:3] == ("localhost", 31972, "IVGBENCHA")
    assert args[3:] == ("_SYSTEM", "SYS")


def test_connection_string_form_is_rewritten(shim):
    args, kwargs = shim.rewrite_connect_args(("1.2.3.4:1981/USER", "_SYSTEM", "SYS"), {}, env=ENV)
    assert args == ("localhost:31972/IVGBENCHA", "_SYSTEM", "SYS")


def test_host_alias_keyword_is_rewritten(shim):
    _, kwargs = shim.rewrite_connect_args((), {"host": "x", "port": 1, "namespace": "USER"}, env=ENV)
    assert kwargs["host"] == "localhost" and kwargs["port"] == 31972 and kwargs["namespace"] == "IVGBENCHA"


def test_foreign_namespace_target_is_refused(shim):
    bad = dict(ENV, IVG_BENCH_NS="USER")
    with pytest.raises(RuntimeError):
        shim.rewrite_connect_args(("h", 1, "USER"), {}, env=bad)


def test_install_wraps_connect_once(shim, monkeypatch):
    fake = types.ModuleType("iris")
    seen = []
    fake.connect = lambda *a, **k: seen.append((a, k))
    fake.dbapi = types.ModuleType("iris.dbapi")
    fake.dbapi.connect = lambda *a, **k: seen.append((a, k))
    # monkeypatch restores the real `iris` module afterwards; popping the names would delete it for every later test
    monkeypatch.setitem(sys.modules, "iris", fake)
    monkeypatch.setitem(sys.modules, "iris.dbapi", fake.dbapi)
    shim.install(env=ENV)
    shim.install(env=ENV)  # idempotent
    fake.connect("h", 21972, "USER", "u", "p")
    fake.dbapi.connect(hostname="h", port=1, namespace="USER")
    assert seen[0][0][:3] == ("localhost", 31972, "IVGBENCHA")
    assert seen[1][1]["namespace"] == "IVGBENCHA"
    assert len(seen) == 2


def test_container_attach_is_redirected(shim):
    assert shim.attach_name("hard-coded-container", env=dict(ENV, IVG_BENCH_CONTAINER="ivg-iris-enterprise")) == "ivg-iris-enterprise"
    assert shim.attach_name("anything", env={}) == "anything"
