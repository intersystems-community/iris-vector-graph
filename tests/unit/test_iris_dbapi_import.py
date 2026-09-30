"""The `iris` driver is real once `iris_vector_graph` is imported (DEBT entry 10).

Three packages install `iris/__init__.py`: intersystems-irispython,
iris-embedded-python-wrapper and sqlalchemy-iris (pulled in by iris-devtester via
testcontainers-iris). The last one installed wins. sqlalchemy-iris 0.18.1's copy
loads the driver from `iris/_init_elsdk.py`, which intersystems-irispython 5.4.0
no longer ships (it is `_elsdk_.py`). When that copy wins, `iris.connect`,
`iris.createIRIS` and `iris.IRISConnection` fall through to the wrapper's module
`__getattr__`, which returns a MagicMock: a connection "succeeds" and every query
returns a mock. `iris.dbapi` subclasses `iris.irissdk.dbapiDataRow` read the same
way, so on Python 3.12+ it fails to import at all (`Union[DataRow, None]` raises
SyntaxError); on 3.11 it imports and subclasses a mock.

The project `.venv` had the driver's own `__init__.py`, so the suite was green
locally and red in CI, where uv/pip install order differs.

Each check runs in a fresh interpreter: anything this process has already
imported would hide the order that fails.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("iris")


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )


def test_iris_dbapi_imports_after_iris_vector_graph():
    out = _run(
        "import iris_vector_graph, iris.dbapi, types\n"
        "assert isinstance(iris.dbapi, types.ModuleType) or callable(iris.dbapi.connect)\n"
        "import sys; assert isinstance(sys.modules['iris.dbapi'], types.ModuleType)\n"
    )
    assert out.returncode == 0, out.stderr[-2000:]


def test_the_dbapi_datarow_is_the_real_class():
    """On 3.11 a mock base imports fine and breaks later: check the class itself."""
    out = _run(
        "import iris_vector_graph, sys\n"
        "import iris.dbapi\n"
        "from unittest.mock import Mock\n"
        "bases = sys.modules['iris.dbapi'].DataRow.__mro__\n"
        "assert not any(issubclass(b, Mock) for b in bases if isinstance(b, type)), bases\n"
    )
    assert out.returncode == 0, out.stderr[-2000:]


@pytest.mark.parametrize("name", ["IRISConnection", "createIRIS", "createConnection"])
def test_the_native_api_is_not_a_mock(name):
    out = _run(
        "import iris_vector_graph, iris\n"
        "from unittest.mock import Mock\n"
        f"obj = iris.{name}\n"
        "assert not isinstance(obj, Mock), obj\n"
    )
    assert out.returncode == 0, out.stderr[-2000:]


# --- the repair itself, against a fake `iris`, so a healthy venv still covers it ---


class _Conn:
    pass


def _fake_iris(monkeypatch, namespace: dict, driver: dict | None):
    import importlib.util
    import types

    from iris_vector_graph import _iris_compat

    fake = types.ModuleType("iris")
    vars(fake).update(namespace)
    monkeypatch.setitem(sys.modules, "iris", fake)
    if driver is not None:
        mod = types.ModuleType("iris._elsdk_")
        vars(mod).update(driver)
        monkeypatch.setitem(sys.modules, "iris._elsdk_", mod)
    real_find = importlib.util.find_spec
    monkeypatch.setattr(
        _iris_compat.importlib.util,
        "find_spec",
        lambda n, *a: object() if (n == "iris._elsdk_" and driver is not None)
        else (None if n.startswith("iris.") else real_find(n, *a)),
    )
    # Exercise the manual path: the wrapper's loader is the real package's business.
    monkeypatch.setitem(sys.modules, "iris_utils._driver_loader", None)
    return fake, _iris_compat


def test_a_namespace_without_the_driver_is_repaired(monkeypatch, caplog):
    fake, compat = _fake_iris(
        monkeypatch, {}, {"IRISConnection": _Conn, "createIRIS": len, "_private": 1}
    )
    assert compat.repair_iris_namespace() is True
    assert fake.IRISConnection is _Conn and fake.createIRIS is len
    assert not hasattr(fake, "_private")
    assert compat.REINSTALL_HINT in caplog.text


def test_a_loaded_driver_is_left_alone(monkeypatch, caplog):
    fake, compat = _fake_iris(monkeypatch, {"IRISConnection": _Conn}, {"IRISConnection": int})
    assert compat.repair_iris_namespace() is False
    assert fake.IRISConnection is _Conn
    assert caplog.text == ""


def test_no_driver_on_disk_is_not_an_error(monkeypatch, caplog):
    _, compat = _fake_iris(monkeypatch, {}, None)
    assert compat.repair_iris_namespace() is False
    assert caplog.text == ""
