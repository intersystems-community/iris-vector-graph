"""pytest plugin: runs behave against TCK features, collects JUnit XML results."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Generator

import pytest

TCK_DIR = Path(__file__).parent
FEATURES_DIR = TCK_DIR / "features"
WIP_FILE = TCK_DIR / "wip.txt"
REPO_ROOT = TCK_DIR.parent.parent


# ---------------------------------------------------------------------------
# Public helpers (also used by unit tests)
# ---------------------------------------------------------------------------

def parse_behave_junit(xml_text: str) -> list[dict]:
    """Parse JUnit XML produced by behave --junit into a list of scenario dicts."""
    root = ET.fromstring(xml_text)
    scenarios = []

    # behave --junit emits one <testsuite> per feature file
    for suite in root.iter("testsuite"):
        for tc in suite.findall("testcase"):
            name = tc.get("name", "")
            classname = tc.get("classname", "")
            status = tc.get("status", "")

            # behave may not set status attr; infer from child elements
            if not status:
                if tc.find("failure") is not None or tc.find("error") is not None:
                    status = "failed"
                elif tc.find("skipped") is not None:
                    status = "skipped"
                else:
                    status = "passed"

            message = ""
            for child_tag in ("failure", "error"):
                child = tc.find(child_tag)
                if child is not None:
                    message = child.text or child.get("message", "")
                    break

            scenarios.append({
                "name": name,
                "classname": classname,
                "status": status,
                "message": message,
            })

    return scenarios


_REASON_PREFIX = "# reason:"


def load_wip_registry(wip_path: str | None = None) -> set[str]:
    """Load wip.txt into a set of 'feature_file::Scenario: title' keys.

    Every entry must sit directly under its own non-empty ``# reason: ...`` line, in
    the same comment block (spec 229 FR-011, SC-006). A reasonless entry raises
    ``ValueError``, so the runner refuses to start rather than defer silently.
    """
    path = wip_path or str(WIP_FILE)
    registry: set[str] = set()
    missing: list[str] = []
    try:
        with open(path) as f:
            reason = False
            for n, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    reason = False
                elif line.startswith("#"):
                    if line.lower().startswith(_REASON_PREFIX):
                        reason = bool(line[len(_REASON_PREFIX):].strip())
                else:
                    if not reason:
                        missing.append(f"line {n}: {line}")
                    registry.add(line)
                    reason = False
    except FileNotFoundError:
        pass
    if missing:
        raise ValueError(
            f"{path}: every deferral entry needs its own '# reason: ...' line above it; "
            "missing for:\n  " + "\n  ".join(missing)
        )
    return registry


# ---------------------------------------------------------------------------
# pytest plugin: TCK test item
# ---------------------------------------------------------------------------

class TCKScenarioItem(pytest.Item):
    def __init__(self, name, parent, scenario_data: dict):
        super().__init__(name, parent)
        self._scenario = scenario_data
        self.add_marker(pytest.mark.tck)

    def runtest(self):
        status = self._scenario["status"]
        if status == "skipped":
            pytest.skip(reason="@wip")
        elif status == "failed":
            raise TCKScenarioFailure(self._scenario["message"])
        # passed — do nothing

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, TCKScenarioFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.fspath, None, self._scenario["name"]


class TCKScenarioFailure(Exception):
    pass


class TCKCollector(pytest.Collector):
    """Runs behave for all TCK features and exposes results as pytest items."""

    def __init__(self, name, parent, results: list[dict], wip_registry: set[str]):
        super().__init__(name, parent)
        self._results = results
        self._wip = wip_registry

    def collect(self) -> Generator:
        for scenario in self._results:
            key = f"{scenario['classname']}::Scenario: {scenario['name']}"
            if key in self._wip:
                # Override status to skipped
                scenario = dict(scenario, status="skipped")
            yield TCKScenarioItem.from_parent(
                self,
                name=scenario["name"],
                scenario_data=scenario,
            )


def _run_behave(junit_dir: str) -> list[dict]:
    """Run behave subprocess, collect JUnit XML, return parsed scenario list."""
    if not FEATURES_DIR.exists():
        raise RuntimeError(
            "TCK features not found. Run: git submodule update --init vendor/opencypher"
        )

    cmd = [
        sys.executable, "-m", "behave",
        "--junit",
        f"--junit-directory={junit_dir}",
        "--no-capture",
        "--format", "null",
        str(FEATURES_DIR),
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )

    scenarios = []
    junit_path = Path(junit_dir)
    for xml_file in junit_path.glob("*.xml"):
        try:
            scenarios.extend(parse_behave_junit(xml_file.read_text()))
        except ET.ParseError:
            pass

    return scenarios


# ---------------------------------------------------------------------------
# pytest hooks
# ---------------------------------------------------------------------------

_tck_results: list[dict] = []
_tck_wip_count: int = 0


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "tck: openCypher TCK conformance tests (requires ivg-iris)"
    )


def pytest_sessionstart(session):
    global _tck_results, _tck_wip_count
    # Only run behave if TCK tests are requested (avoid slowing down normal test runs)
    # Check if -m tck or tests/tck/ is in the args
    args_str = " ".join(str(a) for a in sys.argv)
    if "tck" not in args_str and "tests/tck" not in args_str:
        return

    wip = load_wip_registry()
    _tck_wip_count = 0  # will be counted after collection

    try:
        with tempfile.TemporaryDirectory() as junit_dir:
            _tck_results = _run_behave(junit_dir)
    except Exception as e:
        _tck_results = []
        print(f"\n[TCK] behave run failed: {e}", file=sys.stderr)


def pytest_sessionfinish(session, exitstatus):
    wip = load_wip_registry()
    wip_count = sum(
        1 for s in _tck_results
        if f"{s['classname']}::Scenario: {s['name']}" in wip
        or s["status"] == "skipped"
    )
    total = len(_tck_results)
    passed = sum(1 for s in _tck_results if s["status"] == "passed")
    if total > 0:
        print(f"\n# TCK wip: {wip_count} scenarios skipped | {passed}/{total} passed")


def pytest_collect_file(parent, file_path):
    # We don't collect .feature files directly — results come from behave subprocess
    return None
