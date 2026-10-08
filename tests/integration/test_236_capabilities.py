"""Spec 236 (arno callout adoption): the scratch namespace, and the capability read.

Every live test in this spec runs in the scratch namespace ``IVG236`` and asserts a
sentinel global before any write or erase, because ``BuildKG`` / ``sync()`` can leave
the Native API process in ``USER`` (research R3, memory note build2hop_namespace_flip).
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------- T004: the fixture


def test_the_scratch_fixture_names_its_namespace_and_holds_the_sentinel(ivg_236_scratch):
    native = ivg_236_scratch.native()
    assert ivg_236_scratch.namespace == "IVG236"
    assert str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) == "IVG236"
    assert ivg_236_scratch.sentinel_ok(native)


def test_the_sentinel_check_refuses_a_connection_that_is_not_in_the_scratch_namespace(
    ivg_236_scratch,
):
    # A connection to USER has no sentinel, so a write through it must be refused.
    wrong = ivg_236_scratch.native(namespace="USER")
    assert not ivg_236_scratch.sentinel_ok(wrong)
    with pytest.raises(AssertionError):
        ivg_236_scratch.require_scratch(wrong)


def test_after_buildkg_the_fixture_hands_back_a_connection_in_the_scratch_namespace(
    ivg_236_scratch,
):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    native.classMethodValue("Graph.KG.Traversal", "BuildKG")
    # The handle that ran BuildKG may now be in USER (the flip). The fixture's
    # reconnect helper must give one that is not, whatever the handle did.
    fresh = ivg_236_scratch.reconnect_after_sync(native)
    ivg_236_scratch.require_scratch(fresh)
    assert str(fresh.classMethodValue("%SYSTEM.SYS", "NameSpace")) == "IVG236"


# ------------------------------------------------- T011: ReaderRevisions on this build


def test_reader_revisions_never_throws_and_returns_a_json_object(ivg_236_scratch):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    first = str(native.classMethodValue("Graph.KG.ArnoAccel", "ReaderRevisions"))
    report = json.loads(first)
    assert isinstance(report, dict)
    # The 1.0.x build has no capabilities entry: "{}" and nothing else. A build that
    # has one reports integer revisions per reader name. Either way, a second call
    # still works, because the first call's failure must leave no bad state.
    for name, rev in report.items():
        assert isinstance(name, str) and isinstance(rev, int) and rev >= 1
    second = str(native.classMethodValue("Graph.KG.ArnoAccel", "ReaderRevisions"))
    assert json.loads(second) == report


def test_reader_revisions_is_empty_when_no_library_is_loaded(ivg_236_scratch):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    # Hide the recorded handle for this call only, as a fresh process without Load
    # would see it. The method must answer "{}" and not touch the handle it did not set.
    saved = native.get("ArnoAccel", "dllid")
    native.kill("ArnoAccel", "dllid")
    try:
        assert str(native.classMethodValue("Graph.KG.ArnoAccel", "ReaderRevisions")) == "{}"
    finally:
        if saved is not None:
            native.set(saved, "ArnoAccel", "dllid")


def test_load_leaves_the_process_in_the_namespace_it_was_called_from(ivg_236_scratch):
    # The library's init entry moves the process to USER (research R3).
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    lib = native.get("ArnoAccel", "lib_path")
    if not lib:
        pytest.skip("no library recorded for the scratch namespace")
    assert native.classMethodValue("Graph.KG.ArnoAccel", "Load", lib)
    assert str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) == "IVG236"
    ivg_236_scratch.require_scratch(native)


def test_a_native_reader_refuses_to_answer_outside_user(ivg_236_scratch):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    lib = native.get("ArnoAccel", "lib_path")
    if not lib:
        pytest.skip("no library recorded for the scratch namespace")
    native.classMethodValue("Graph.KG.ArnoAccel", "Load", lib)
    with pytest.raises(RuntimeError, match="USER only"):
        native.classMethodValue("Graph.KG.ArnoAccel", "WCCNativeJson")
    ivg_236_scratch.require_scratch(native)


def test_buildkg_leaves_the_handle_in_the_namespace_it_was_called_from(ivg_236_scratch):
    # T048: Build2HopExactStats used to leave the process in USER.
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    lib = native.get("ArnoAccel", "lib_path")
    if lib:
        native.classMethodValue("Graph.KG.NKGAccelLoader", "Load", lib)
    native.set(1, "^KG", "out", 0, "a", "P", "b")
    native.set(1, "^KG", "in", 0, "b", "P", "a")
    try:
        native.classMethodValue("Graph.KG.Traversal", "BuildKG")
        assert str(native.classMethodValue("%SYSTEM.SYS", "NameSpace")) == "IVG236"
        ivg_236_scratch.require_scratch(native)
    finally:
        if ivg_236_scratch.sentinel_ok(native):
            native.classMethodValue("Graph.KG.Eraser", "EraseAll")


def test_with_the_library_loaded_the_version_and_reader_report_are_readable(ivg_236_scratch):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    lib = native.get("ArnoAccel", "lib_path")
    if not lib:
        pytest.skip("no library recorded for the scratch namespace")
    assert native.classMethodValue("Graph.KG.ArnoAccel", "Load", lib)
    version = str(native.classMethodValue("Graph.KG.ArnoAccel", "LibraryVersion"))
    assert version.startswith("arno-callout "), version
    readers = json.loads(str(native.classMethodValue("Graph.KG.ArnoAccel", "ReaderRevisions")))
    if readers:  # a build without the capabilities entry reports {}
        assert all(isinstance(v, int) and v >= 1 for v in readers.values())
    ivg_236_scratch.require_scratch(native)


def test_library_version_is_empty_when_nothing_is_loaded(ivg_236_scratch):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    saved = native.get("ArnoAccel", "dllid")
    native.kill("ArnoAccel", "dllid")
    try:
        assert native.classMethodValue("Graph.KG.ArnoAccel", "LibraryVersion") in (None, "")
    finally:
        if saved is not None:
            native.set(saved, "ArnoAccel", "dllid")


@pytest.mark.parametrize(
    "method,args",
    [
        ("PageRankNativeJson", ("0.85", "20")),
        ("PPRNativeJson", ('["a"]', "0.85", "20")),
        ("CDLPNativeJson", ("10",)),
        ("SubgraphNativeJson", ('["a"]', "2", "100")),
    ],
)
def test_every_native_reader_entry_point_refuses_outside_user(ivg_236_scratch, method, args):
    native = ivg_236_scratch.native()
    ivg_236_scratch.require_scratch(native)
    lib = native.get("ArnoAccel", "lib_path")
    if not lib:
        pytest.skip("no library recorded for the scratch namespace")
    native.classMethodValue("Graph.KG.ArnoAccel", "Load", lib)
    with pytest.raises(RuntimeError, match="USER only"):
        native.classMethodValue("Graph.KG.ArnoAccel", method, *args)
    ivg_236_scratch.require_scratch(native)
