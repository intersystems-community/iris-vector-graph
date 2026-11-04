"""R141: the release child's result is found in its stdout whatever the old release printed before it.

v4.0.0's setup echoes `$system.OBJ.LoadDir` output to the Python process's stdout, and its last write (deleting the
stale `Graph.KG.TestEdge`) has no newline: `ERROR #5351: Class 'Graph.KG.TestEdge' does not exist.{"tag": ...`. The
parent read the last line as JSON and failed with `Expecting value: line 1 column 1`. Measured on IVG262LEGA.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fixtures" / "build_release_namespace.py"


@pytest.fixture(scope="module")
def brn():
    spec = importlib.util.spec_from_file_location("build_release_namespace_result", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _emitted(brn, noise: str, obj) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print(noise, end="")
        brn.emit_result(obj)
    return buf.getvalue()


def test_a_result_after_output_with_no_trailing_newline(brn):
    out = _emitted(brn, "Load finished successfully.\nERROR #5351: Class 'Graph.KG.TestEdge' does not exist.",
                   {"tag": "v4.0.0", "views": ["nodes"]})
    assert brn.parse_result(out) == {"tag": "v4.0.0", "views": ["nodes"]}


def test_the_last_result_wins_and_noise_after_it_is_ignored(brn):
    out = _emitted(brn, "", {"n": 1}) + _emitted(brn, "x", {"n": 2}) + "trailing chatter\n"
    assert brn.parse_result(out) == {"n": 2}


def test_noise_that_looks_like_json_is_not_a_result(brn):
    out = json.dumps({"n": "not this"}) + "\n" + _emitted(brn, "", {"n": "this"})
    assert brn.parse_result(out) == {"n": "this"}


def test_no_result_is_an_error_that_shows_the_output(brn):
    with pytest.raises(RuntimeError, match="no result line.*ERROR #5351"):
        brn.parse_result("ERROR #5351: Class 'Graph.KG.TestEdge' does not exist.")


def test_the_release_s_server_side_class_directory_is_its_own(brn):
    """Every release up to 4.4 loads classes from the server's /tmp/src/ first, which on the shared test container
    holds whatever a test run staged there (another checkout's classes, and files no release has together). The
    child points that path at the tag's own stage."""
    import types

    seen = []
    module = types.SimpleNamespace(_call_classmethod=lambda native, cls, method, *args: seen.append((cls, method, args)))
    brn.redirect_server_src(module, "/tmp/ivg262_src_v4_0_0")
    module._call_classmethod("n", "%SYSTEM.OBJ", "LoadDir", "/tmp/src/", "ck", "", 1)
    module._call_classmethod("n", "%Library.File", "Delete", "/tmp/src//Graph/KG/Edge.cls")
    module._call_classmethod("n", "%SYSTEM.OBJ", "LoadDir", "/irisdev/app/iris_src/src/", "ck", "", 1)
    module._call_classmethod("n", "Graph.KG.Meta", "Set", "/tmp/srcx", 1)
    assert seen == [
        ("%SYSTEM.OBJ", "LoadDir", ("/tmp/ivg262_src_v4_0_0/", "ck", "", 1)),
        ("%Library.File", "Delete", ("/tmp/ivg262_src_v4_0_0//Graph/KG/Edge.cls",)),
        ("%SYSTEM.OBJ", "LoadDir", ("/irisdev/app/iris_src/src/", "ck", "", 1)),
        ("Graph.KG.Meta", "Set", ("/tmp/srcx", 1)),
    ]
