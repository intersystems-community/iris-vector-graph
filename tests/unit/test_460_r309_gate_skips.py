"""4.6.0 R309: in gate mode (`IVG_GATE=1`) a skipped E2E test fails the run unless tests/e2e/gate_skips.txt allows it.

A full E2E of 4.6.0 reported 1577 skips beside 3580 passes and read as green. Each skip is a test nobody ran; in the
gate a skip needs a reviewed reason: a line of the allow-list naming the test (an fnmatch pattern over its node id),
the text its skip message must contain, and why that skip is acceptable. Anything else fails, the skip message in the
failure. These tests run the plugin (`tests/e2e/gate.py`) in a pytest of its own over a small project.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.e2e import gate

REPO = Path(__file__).resolve().parents[2]
ALLOW = REPO / "tests" / "e2e" / "gate_skips.txt"
PY = sys.executable


# --------------------------------------------------------------------------- the allow-list


def test_parse_reads_pattern_message_and_why():
    entries = gate.parse_allow_list("# comment\n\ntests/e2e/test_x.py::* | needs Community | the enterprise image "
                                    "cannot be Community\n")
    assert [(e.pattern, e.message, e.why, e.line) for e in entries] == [
        ("tests/e2e/test_x.py::*", "needs Community", "the enterprise image cannot be Community", 3)]


@pytest.mark.parametrize("line", [
    "tests/e2e/test_x.py::*",                       # no message, no why
    "tests/e2e/test_x.py::* | needs Community",     # no why
    "tests/e2e/test_x.py::* |  | why",              # empty message
    " | needs Community | why",                      # empty pattern
    "* | anything | a pattern matching every test is no review",
    "tests/e2e/* | e | a whole directory is no review either",
])
def test_an_entry_without_all_three_parts_or_too_broad_is_refused(line):
    with pytest.raises(ValueError, match="line 1"):
        gate.parse_allow_list(line + "\n")


def test_match_needs_both_the_test_and_the_message():
    [entry] = gate.parse_allow_list("tests/e2e/test_x.py::* | needs Community | why\n")
    assert gate.match([entry], "tests/e2e/test_x.py::test_a", "Skipped: needs Community (enterprise)") is entry
    assert gate.match([entry], "tests/e2e/test_x.py::test_a", "Skipped: ivg-iris-enterprise is not running") is None
    assert gate.match([entry], "tests/e2e/test_y.py::test_a", "needs Community") is None


def test_the_repo_allow_list_parses_and_each_entry_says_why():
    entries = gate.parse_allow_list(ALLOW.read_text())
    assert entries, "the allow-list is empty: then every skip fails, which is allowed, but say so by deleting this"
    for e in entries:
        assert len(e.why) >= 20, f"line {e.line}: '{e.why}' is not a reason"


@pytest.mark.parametrize("text", ["is not running", "SKIP_IRIS_TESTS", "IVG_NAMESPACE must name"])
def test_the_repo_allow_list_never_allows_a_skip_the_gate_exists_to_catch(text):
    """No container, a run told to skip IRIS, or a scratch namespace not provided: each is the gate not running."""
    entries = gate.parse_allow_list(ALLOW.read_text())
    assert [e.line for e in entries if text in e.message] == []


# --------------------------------------------------------------------------- the plugin in a run


def _project(tmp_path: Path, allow: str) -> None:
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text("from tests.e2e import gate\n\ndef pytest_configure(config):\n"
                                          "    gate.register_skips(config)\n")
    (tmp_path / "tests" / "e2e" / "gate_skips.txt").write_text(allow)
    (tmp_path / "tests" / "e2e" / "test_e.py").write_text(textwrap.dedent("""
        import pytest

        def test_passes():
            pass

        @pytest.mark.skipif(True, reason="needs Community")
        def test_allowed():
            pass

        @pytest.mark.skipif(True, reason="ivg-iris-enterprise is not running")
        def test_not_allowed():
            pass

        @pytest.fixture
        def fx():
            pytest.skip("no FHIR here")

        def test_fixture_skip(fx):
            pass

        @pytest.mark.xfail(reason="known", strict=True)
        def test_xfail():
            assert False
    """))
    (tmp_path / "tests" / "e2e" / "test_module_skip.py").write_text(
        'import pytest\npytest.skip("whole module: no strawberry", allow_module_level=True)\n')
    (tmp_path / "tests" / "unit" / "test_u.py").write_text(
        'import pytest\n\n@pytest.mark.skip(reason="a unit skip is not the gate\'s business")\ndef test_u():\n'
        '    pass\n')


def _run(tmp_path: Path, gate_on: bool, *args: str):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IVG_", "SKIP_IRIS"))}
    env["PYTHONPATH"] = str(REPO)
    if gate_on:
        env["IVG_GATE"] = "1"
    return subprocess.run([PY, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA", *args, "tests"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


ALLOW_ONE = "tests/e2e/test_e.py::test_allowed | needs Community | the enterprise image is not Community\n"


def test_without_the_gate_skips_are_skips(tmp_path):
    _project(tmp_path, ALLOW_ONE)
    out = _run(tmp_path, False)
    assert out.returncode == 0, out.stdout
    assert "1 passed" in out.stdout and "5 skipped" in out.stdout, out.stdout


def test_in_the_gate_an_unlisted_skip_fails_and_says_what_it_was(tmp_path):
    _project(tmp_path, ALLOW_ONE)
    out = _run(tmp_path, True)
    assert out.returncode == 1, out.stdout
    text = out.stdout
    # allowed, unit and xfail stay what they were
    assert "SKIPPED [1] tests/e2e/test_e.py" in text or "SKIPPED" in text
    assert "1 xfailed" in text
    for nodeid in ("tests/e2e/test_e.py::test_not_allowed", "tests/e2e/test_e.py::test_fixture_skip"):
        assert f"ERROR {nodeid}" in text, text
    assert "ivg-iris-enterprise is not running" in text and "no FHIR here" in text
    # a module skipped at import is a collection report: listed, and the run fails
    assert "tests/e2e/test_module_skip.py" in text and "whole module: no strawberry" in text
    assert "IVG_GATE=1" in text
    assert "a unit skip is not the gate's business" in text  # still a skip, not an error
    assert "ERROR tests/unit" not in text


def test_in_the_gate_with_every_skip_allowed_the_run_passes(tmp_path):
    allow = ALLOW_ONE + textwrap.dedent("""\
        tests/e2e/test_e.py::test_not_allowed | is not running | (only in this test) the container is not up
        tests/e2e/test_e.py::test_fixture_skip | no FHIR here | (only in this test) no FHIR namespace in it
        tests/e2e/test_module_skip.py | no strawberry | (only in this test) the module needs strawberry
    """)
    _project(tmp_path, allow)
    out = _run(tmp_path, True)
    assert out.returncode == 0, out.stdout
    assert "5 skipped" in out.stdout


def test_in_the_gate_an_allow_list_that_does_not_parse_stops_the_run(tmp_path):
    _project(tmp_path, "tests/e2e/test_e.py::test_allowed | needs Community\n")
    out = _run(tmp_path, True)
    assert out.returncode != 0
    assert "gate_skips.txt" in out.stdout + out.stderr and "line 1" in out.stdout + out.stderr


def test_the_root_conftest_registers_the_gate():
    text = (REPO / "tests" / "conftest.py").read_text()
    assert "gate.register(config)" in text
    assert "register_skips" in (REPO / "tests" / "e2e" / "gate.py").read_text()
