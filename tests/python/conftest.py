"""Fixtures for `tests/python/`, which had none of its own.

Two things were missing and both made tests pass or error for the wrong reason:

`engine` is defined in `tests/integration/conftest.py`, so it is invisible here —
`tests/python/test_python_operators.py` requested it and every one of its tests
ended in `fixture 'engine' not found` rather than running.

`iris.cls(...)` needs a runtime. The installed `iris-embedded-python-wrapper`
returns a **`MagicMock`** from `iris.cls(name)` when neither embedded Python nor a
bound native handle is available (`_iris_ep/_runtime_facade.py:582-583`), after a
warning nobody reads. A test that then calls a class method gets a mock back and
asserts against it: `json.loads(<MagicMock>)`, `isinstance(<MagicMock>, int)`,
`DID NOT RAISE`. `hasattr(iris, "cls")` cannot detect this — the attribute always
exists. `native_iris_runtime` binds the test container as a native handle so
`iris.cls` hands back a real `NativeClassProxy`, and `iris_class_access_is_real`
is the honest guard for when it could not.
"""

import os
import socket

import pytest

from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def engine(iris_connection):
    """An initialized engine, mirroring `tests/integration/conftest.py`'s fixture."""
    eng = IRISGraphEngine(iris_connection, embedding_dimension=768)
    eng.initialize_schema(auto_deploy_objectscript=True)
    return eng


def _native_handle(iris_connection):
    """A Native API handle on the same server the DB-API fixture reached.

    The fixture's own connection cannot be reused: `iris.runtime.configure` wants a
    native handle, and a DB-API connection is not one.
    """
    import iris as _iris

    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    candidates = [
        (getattr(iris_connection, "hostname", None), getattr(iris_connection, "port", None)),
    ]
    try:
        candidates.append((socket.gethostbyname(f"{container}.orb.local"), 1972))
    except Exception:
        pass
    candidates.append(("localhost", int(os.environ.get("IVG_PORT", "31972"))))

    namespace = getattr(iris_connection, "namespace", None) or "USER"
    for hostname, port in candidates:
        if not hostname or not port:
            continue
        try:
            return _iris.connect(
                hostname=hostname,
                port=int(port),
                namespace=namespace,
                username="_SYSTEM",
                password="SYS",
            )
        except Exception:
            continue
    return None


@pytest.fixture(scope="session")
def native_iris_runtime(iris_connection):
    """Bind the test container so `iris.cls(...)` is a real proxy, not a `MagicMock`.

    Yields the handle, or None when no native connection could be made — callers
    must skip in that case rather than run against a mock.
    """
    import iris as _iris

    handle = _native_handle(iris_connection)
    if handle is None:
        yield None
        return
    try:
        _iris.runtime.configure(native_connection=handle)
    except Exception:
        handle.close()
        yield None
        return
    try:
        yield handle
    finally:
        try:
            _iris.runtime.reset()
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


def iris_class_access_is_real() -> bool:
    """True when `iris.cls` returns a live proxy rather than the wrapper's mock."""
    from unittest.mock import MagicMock

    import iris as _iris

    try:
        probe = _iris.cls("%SYSTEM.Version")
    except Exception:
        return False
    return not isinstance(probe, MagicMock)
