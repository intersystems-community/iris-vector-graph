"""scripts/tck/run_all.sh and run_sharded.sh must exit non-zero when the run is not a pass.

4.7.1's gate reported 0 / 3896 with exit 0 (the container was down, every scenario failed).
The script ran each behave process with `|| true` and ended on a summary that always
returned 0 when it found results. These tests run the real scripts against a fixture repo
with a stand-in `python -m behave` that writes JUnit XML, so no IRIS and no TCK checkout
is needed.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "tck"

FEATURE = """Feature: Fixture
  Scenario: alpha
    Given any graph
  Scenario: beta
    Given any graph
"""

# Stand-in for the interpreter: `-m behave ... --junit-directory D FEATURE_DIR` writes the XML
# FAKE_TCK_MODE asks for; anything else (the summary script) runs under the real interpreter.
FAKE_PY = """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "behave" ]; then
  while [ $# -gt 0 ]; do [ "$1" = "--junit-directory" ] && dir=$2; shift; done
  area=$(basename "$dir")
  case "${FAKE_TCK_MODE:-pass}" in
    pass)  a=passed; b=passed ;;
    fail)  a=passed; b=failed ;;
    allfail) a=failed; b=failed ;;
    skip)  a=passed; b=skipped ;;
    zero)  a=; b= ;;
    crash) echo "ModuleNotFoundError: no behave" >&2; exit 1 ;;
  esac
  mkdir -p "$dir"
  {
    echo '<testsuite name="x" tests="2">'
    [ -n "$a" ] && echo "<testcase classname=\\"features.cat.$area.Fixture$area\\" name=\\"alpha\\" status=\\"$a\\"/>"
    [ -n "$b" ] && echo "<testcase classname=\\"features.cat.$area.Fixture$area\\" name=\\"beta\\" status=\\"$b\\"/>"
    echo '</testsuite>'
  } > "$dir/TESTS-fixture.xml"
  [ "$a$b" = "passedpassed" ] && exit 0
  exit 1
fi
exec "__REAL_PY__" "$@"
"""


@pytest.fixture
def harness(tmp_path):
    repo = tmp_path / "repo"
    for area in ("Alpha1", "Beta2"):
        d = repo / "tests" / "tck" / "features" / "cat" / area
        d.mkdir(parents=True)
        (d / f"{area}.feature").write_text(FEATURE)
    fake = tmp_path / "fakepy"
    fake.write_text(FAKE_PY.replace("__REAL_PY__", sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def run(script, mode, *args):
        env = dict(os.environ, IVG_REPO=str(repo), PYTHON=str(fake), TCK_LOCK="0", FAKE_TCK_MODE=mode)
        return subprocess.run(
            ["bash", str(SCRIPTS / script), str(tmp_path / "out"), *args],
            env=env, capture_output=True, text=True, timeout=120,
        )

    return run


def test_all_pass_exits_zero(harness):
    r = harness("run_all.sh", "pass")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "4 / 4 eligible" in r.stdout


def test_one_failed_scenario_exits_non_zero(harness):
    r = harness("run_all.sh", "fail")
    assert "2 / 4 eligible" in r.stdout  # the summary still prints
    assert r.returncode != 0, r.stdout + r.stderr


def test_every_scenario_failed_exits_non_zero(harness):
    r = harness("run_all.sh", "allfail")
    assert "0 / 4 eligible" in r.stdout
    assert r.returncode != 0


def test_zero_scenarios_ran_exits_non_zero(harness):
    r = harness("run_all.sh", "zero")
    assert r.returncode != 0, r.stdout + r.stderr


def test_harness_crash_before_any_scenario_exits_non_zero(harness):
    r = harness("run_all.sh", "crash")
    assert r.returncode != 0, r.stdout + r.stderr


def test_one_area_crashing_among_passing_areas_exits_non_zero(tmp_path, harness):
    # Alpha1 passes; Beta2 produces no XML at all. The summary alone would say 2 / 2 and exit 0.
    marker = tmp_path / "repo" / "tests" / "tck" / "features" / "cat" / "Beta2"
    fake = tmp_path / "fakepy"
    fake.write_text(
        fake.read_text().replace(
            'area=$(basename "$dir")',
            'area=$(basename "$dir"); [ "$area" = Beta2 ] && exit 1',
        )
    )
    assert marker.is_dir()
    r = harness("run_all.sh", "pass")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "Beta2" in r.stdout + r.stderr  # says which area


def test_sharded_run_with_a_failure_exits_non_zero(harness):
    r = harness("run_sharded.sh", "fail", "NSA", "NSB")
    assert r.returncode != 0, r.stdout + r.stderr


def test_sharded_run_all_pass_exits_zero(harness):
    r = harness("run_sharded.sh", "pass", "NSA", "NSB")
    assert r.returncode == 0, r.stdout + r.stderr
