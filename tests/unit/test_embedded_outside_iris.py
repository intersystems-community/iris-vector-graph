"""4.6.0 (R056): `EmbeddedConnection` outside IRIS fails at once, and no MagicMock reaches IVG.

Outside IRIS the embedded-Python wrapper (a core dependency) answers `iris.sql` and `iris.cls(...)` with MagicMocks.
`IRISGraphEngine(EmbeddedConnection())` then built an engine whose every call "succeeded", and `initialize_schema()`
returned a status dict having done nothing (the doctest run of USER_GUIDE's "Embedded Python" block). Embedded mode
needs a process inside IRIS (irispython, or a [ Language = python ] method); anywhere else it is an error that says so.
"""

from __future__ import annotations

import os
from unittest.mock import NonCallableMock

import pytest

from iris_vector_graph.embedded import EmbeddedConnection, EmbeddedIRIS, EmbeddedUnavailableError
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture(autouse=True)
def outside_iris():
    import logging

    import iris

    wrapper = logging.getLogger("_iris_ep")
    level, wrapper.level = wrapper.level, logging.ERROR  # the probe below makes the wrapper warn
    try:
        sql = getattr(iris, "sql", None)
    finally:
        wrapper.setLevel(level)
    if os.environ.get("IRISINSTALLDIR") or (sql is not None and not isinstance(sql, NonCallableMock)):
        pytest.skip("this process has embedded IRIS")


def test_building_the_engine_says_embedded_mode_needs_iris():
    with pytest.raises(EmbeddedUnavailableError, match=r"inside IRIS \(irispython\)"):
        IRISGraphEngine(EmbeddedConnection(), embedding_dimension=4)


def test_a_cursor_says_so_too():
    with pytest.raises(EmbeddedUnavailableError, match=r"inside IRIS \(irispython\)"):
        EmbeddedConnection().cursor().execute("SELECT 1")


def test_a_class_method_says_so_too():
    with pytest.raises(EmbeddedUnavailableError, match=r"inside IRIS \(irispython\)"):
        EmbeddedIRIS().classMethodValue("%SYSTEM.SYS", "NameSpace")


def test_it_is_an_import_error_as_before():
    # callers that caught ImportError from _require_iris_sql keep working
    assert issubclass(EmbeddedUnavailableError, ImportError)
