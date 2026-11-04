"""4.6.0 R135: `engine.sync()` raises when a rebuild fails, instead of answering `False`.

`sync()` rewrites `^KG` and `^NKG`, a data-changing path, and `_sync_kg` / `_sync_nkg` caught every exception and
returned `False` (TESTING_POLICY, "No `except Exception: return False` on a path that changes data"). The rebuilds
also report their own failures as a `%Status` (`Graph.KG.TraversalBuild.BuildKG` catches everything and returns
`ex.AsStatus()`), which `classMethodVoid` threw away, so a failed rebuild answered `True`.

Each failure is forced by patching what the code calls: `engine._iris_obj()` and the Native API object it returns.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.engine import IRISGraphEngine

ERROR_STATUS = "0 $lb(...)"  # anything but the OK status "1"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.return_value = []
    return IRISGraphEngine(conn, embedding_dimension=4)


def _native(statuses: dict, raises: dict = None):
    """A Native API stand-in: `statuses[method]` is what that class method returns, `raises[method]` what it raises.
    `%SYSTEM.Status.GetErrorText` answers with the text the server would."""
    raises = raises or {}

    def value(cls, method, *args):
        if method in raises:
            raise raises[method]
        if (cls, method) == ("%SYSTEM.Status", "GetErrorText"):
            return f"ERROR #5002: ObjectScript error: <UNDEFINED> in {args[0]!r}"
        return statuses[method]

    obj = MagicMock()
    obj.classMethodValue.side_effect = value
    obj.classMethodVoid.side_effect = AssertionError("a rebuild's %Status must be read, not discarded")
    return obj


def test_a_failed_kg_rebuild_status_raises_naming_the_error():
    eng = _engine()
    native = _native({"BuildKG": ERROR_STATUS})
    with patch.object(eng, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError, match=r"Graph\.KG\.Traversal\.BuildKG.*<UNDEFINED>"):
            eng.sync()
    assert eng.capabilities.kg_built is not True


def test_an_exception_from_the_kg_rebuild_propagates():
    eng = _engine()
    native = _native({}, raises={"BuildKG": RuntimeError("<COMMUNICATION LINK ERROR>")})
    with patch.object(eng, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError, match="COMMUNICATION LINK ERROR"):
            eng.sync()


def test_a_failed_nkg_rebuild_raises():
    eng = _engine()
    native = _native({"BuildKG": "1", "BuildNKG": ERROR_STATUS, "Build2HopStats": 0})
    with patch.object(eng, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError, match=r"Graph\.KG\.Traversal\.BuildNKG"):
            eng.sync()
    assert eng._nkg_dirty is True, "^NKG was not rebuilt, so it is still out of date"


def test_an_exception_from_the_nkg_rebuild_propagates():
    eng = _engine()
    native = _native({"BuildKG": "1"}, raises={"BuildNKG": RuntimeError("BuildNKG: another ^NKG build held it")})
    with patch.object(eng, "_iris_obj", return_value=native):
        with pytest.raises(RuntimeError, match="another"):
            eng.sync()


def test_a_successful_sync_answers_true_and_clears_the_pending_flag():
    eng = _engine()
    eng._nkg_dirty = True
    native = _native({"BuildKG": "1", "BuildNKG": "1", "Build2HopStats": 12})
    with patch.object(eng, "_iris_obj", return_value=native):
        assert eng.sync() is True
    assert eng._nkg_dirty is False
    assert eng.capabilities.kg_built is True
    called = [c.args[:2] for c in native.classMethodValue.call_args_list]
    assert called == [("Graph.KG.Traversal", "BuildKG"), ("Graph.KG.Traversal", "BuildNKG"),
                      ("Graph.KG.Traversal", "Build2HopStats")]


def test_the_status_is_ok_as_the_driver_returns_it_an_int_or_a_string():
    eng = _engine()
    native = _native({"BuildKG": 1, "BuildNKG": "1", "Build2HopStats": "0"})
    with patch.object(eng, "_iris_obj", return_value=native):
        assert eng.sync() is True

