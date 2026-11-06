"""4.6.0 R309: the pre-push gate runs the unit suite and the property-reading E2E files under both drivers.

The repo's .venv has intersystems-irispython 5.4.0 and a fresh install gets 5.4.1; the two decode a `$DOUBLE` in a
VARCHAR column differently (docs/dev/driver-drift.md). R230 wrote scripts/dev/driver_matrix.sh, but nothing ran it:
ci-parity built one venv per Python version from whatever pip resolved, so every gate saw one driver. Now:

- scripts/ci-parity.sh names both drivers (DRIVERS), runs the unit suite with each pinned, and, when the test container
  is running, scripts/dev/driver_matrix.sh with those two interpreters, a failure of either failing the run;
- scripts/dev/driver_matrix.sh builds one venv per driver with that driver pinned, checks each interpreter really has
  it, and runs the unit suite and scripts/dev/driver_matrix_e2e.txt under each.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CI_PARITY = REPO / "scripts" / "ci-parity.sh"
MATRIX = REPO / "scripts" / "dev" / "driver_matrix.sh"
BOTH = ("5.4.0", "5.4.1")


def _code(path: Path) -> str:
    """The script without comment lines, continuation lines joined."""
    text = path.read_text().replace("\\\n", " ")
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _drivers(code: str) -> tuple:
    m = re.search(r"^\s*DRIVERS=\(([^)]*)\)", code, re.M)
    assert m, "no DRIVERS=(...) array"
    return tuple(m.group(1).split())


def test_ci_parity_names_both_drivers():
    assert _drivers(_code(CI_PARITY)) == BOTH


def test_ci_parity_pins_each_driver_and_runs_the_unit_suite_with_it():
    code = _code(CI_PARITY)
    assert re.search(r'intersystems-irispython==\$\{?drv\}?', code), "a venv per driver, with that driver pinned"
    assert 'pins=("${DRIVERS[@]}")' in code, "the first Python version runs once per driver"
    loop = code[code.index('for drv in "${pins[@]}"'):]
    unit = re.search(r"pytest tests/unit\b", loop)
    assert unit, "the unit suite runs inside the per-driver loop"
    assert "status=1" in loop[unit.start(): unit.start() + 400]


def test_ci_parity_checks_each_venv_has_the_driver_it_pinned():
    code = _code(CI_PARITY)
    assert 'm.version("intersystems-irispython")' in code or "importlib.metadata" in code


def test_ci_parity_runs_the_driver_matrix_with_the_two_interpreters_when_the_container_runs():
    code = _code(CI_PARITY)
    hit = re.search(r"IVG_PY_REPO=\S+ IVG_PY_FRESH=\S+ .*driver_matrix\.sh", code)
    assert hit, "ci-parity runs scripts/dev/driver_matrix.sh with both interpreters"
    window = code[hit.start(): hit.start() + 400]
    assert "status=1" in window
    before = code[: hit.start()]
    assert before.rfind("State.Status") > before.rfind("=== wheel"), "inside the container-running branch"


def test_ci_parity_runs_in_gate_mode():
    assert re.search(r"^export IVG_GATE=1$", _code(CI_PARITY), re.M)


def test_the_matrix_names_both_drivers_and_pins_them():
    code = _code(MATRIX)
    assert _drivers(code) == BOTH
    assert re.search(r'intersystems-irispython==\$\{?drv\}?', code)


def test_the_matrix_refuses_an_interpreter_with_the_wrong_driver():
    code = _code(MATRIX)
    assert re.search(r'"\$\(driver "\$py"\)" != "\$drv"', code), code


def test_the_matrix_runs_the_unit_suite_under_each_driver():
    code = _code(MATRIX)
    assert re.search(r"pytest\b.*tests/unit\b", code)
    assert "--no-unit" in code  # ci-parity has run it already
