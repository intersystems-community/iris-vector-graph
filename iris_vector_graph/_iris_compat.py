"""Repair an `iris` package whose `__init__.py` lost the driver (DEBT entry 10).

Three distributions install `iris/__init__.py`: intersystems-irispython,
iris-embedded-python-wrapper and sqlalchemy-iris (which iris-devtester pulls in
through testcontainers-iris). The last one installed wins. sqlalchemy-iris
0.18.1's copy loads the driver from `iris/_init_elsdk.py`; intersystems-irispython
5.4.0 ships `iris/_elsdk_.py` instead. When that copy wins, nothing loads the
driver, and `iris.IRISConnection`, `iris.createIRIS` and `iris.irissdk` fall
through to the wrapper's module `__getattr__`, which returns a MagicMock. A
connection then "succeeds" and every query returns a mock, and `iris.dbapi`
(which subclasses `iris.irissdk.dbapiDataRow` at import) fails to import on
Python 3.12+.

This runs before anything in the package imports `iris.dbapi` and does what the
wrapper's own `__init__.py` does: load the driver's public names into `iris`,
then let the wrapper rebind its facade over them. It does nothing when the
driver is already loaded or not installed.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os

logger = logging.getLogger(__name__)

_DRIVER_MODULES = ("iris._elsdk_", "iris._init_elsdk")
REINSTALL_HINT = (
    "pip install --force-reinstall --no-deps iris-embedded-python-wrapper"
)


def _driver_module():
    for name in _DRIVER_MODULES:
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ValueError):
            continue
    return None


def _driver_loaded(namespace: dict) -> bool:
    # A real attribute, not one the wrapper's __getattr__ invents on lookup.
    return isinstance(namespace.get("IRISConnection"), type)


#: The embedded-Python wrapper's logger. Outside IRIS its first import warns that IRISINSTALLDIR is unset and
#: "Embedded Python not configured": true, and nothing to act on for a client connecting over TCP (NR18).
_WRAPPER_LOGGER = "_iris_ep"


def _import_iris_quietly():
    """`import iris`, without the wrapper's start-up warnings when no IRIS install is named in the environment."""
    if os.environ.get("IRISINSTALLDIR") or os.environ.get("ISC_PACKAGE_INSTALLDIR"):
        import iris

        return iris
    wrapper = logging.getLogger(_WRAPPER_LOGGER)
    level = wrapper.level
    wrapper.setLevel(logging.ERROR)
    try:
        import iris
    finally:
        wrapper.setLevel(level)
    return iris


def repair_iris_namespace() -> bool:
    """Load the driver into `iris` if its `__init__.py` did not. True if repaired."""
    try:
        iris = _import_iris_quietly()
    except Exception:
        return False
    namespace = vars(iris)
    if _driver_loaded(namespace):
        return False
    name = _driver_module()
    if name is None:
        return False  # no driver on disk: nothing to repair, callers report it

    try:
        from iris_utils._driver_loader import load_driver_symbols, rebind_wrapper_symbols
    except ImportError:
        module = importlib.import_module(name)
        for attr, value in vars(module).items():
            if not attr.startswith("_"):
                namespace[attr] = value
    else:
        load_driver_symbols(namespace)
        rebind_wrapper_symbols(namespace)

    if not _driver_loaded(namespace):
        logger.warning(
            "The installed iris/__init__.py does not load the IRIS driver (%s), and "
            "loading it here did not work either. Reinstall so the right file wins: %s",
            name,
            REINSTALL_HINT,
        )
        return False
    logger.warning(
        "The installed iris/__init__.py (overwritten by another package, usually "
        "sqlalchemy-iris) did not load the IRIS driver; iris_vector_graph loaded %s "
        "itself. To fix the install: %s",
        name,
        REINSTALL_HINT,
    )
    return True


repair_iris_namespace()
