"""R046: scripts/ci-parity.sh runs the 4.6.0 gates, and a failing gate fails it.

A gate dropped from ci-parity is a gate nobody runs before a push. Each case names a gate's test file; ci-parity
must run it, on a line that sets `status=1` when it fails. `tests/unit/...` gates are run by ci-parity's
`pytest tests/unit` step.

A gate whose test is not written yet is SKIPPED with its pending task ID, never passed: the case turns strict
the moment the file exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CI_PARITY = REPO / "scripts" / "ci-parity.sh"

#: (gate, test file, task that writes the test file or None when it exists, needs the container)
GATES = [
    ("API reference is fresh (4.6.0 R024)", "tests/unit/test_api_reference_fresh.py", "R024", False),
    ("installed inventory matches docs/inventory.json (spec 262 T061)", "tests/e2e/test_262_inventory.py",
     "T061", True),
    ("docs agree with the code (R043)", "tests/docs/test_docs_consistent.py", None, False),
    ("docs run and their answers match (R042)", "tests/docs/test_docs_run.py", None, True),
]


def _command_lines() -> list:
    """ci-parity.sh's lines with comments and heredoc bodies removed."""
    out, heredoc = [], None
    for raw in CI_PARITY.read_text().splitlines():
        line = raw.strip()
        if heredoc:
            if line == heredoc:
                heredoc = None
            continue
        m = re.search(r"<<-?'?(\w+)'?", line)
        if m:
            heredoc = m.group(1)
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _runs(path: str) -> list:
    """Indices of the command lines that run `path`."""
    lines = _command_lines()
    hits = [i for i, ln in enumerate(lines) if path in ln and "pytest" in ln]
    if not hits and path.startswith("tests/unit/"):
        hits = [i for i, ln in enumerate(lines) if re.search(r"pytest\b.*\btests/unit\b(?!/)", ln)]
    return hits


@pytest.mark.parametrize("gate, path, pending, _container", GATES, ids=[g[2] or g[1] for g in GATES])
def test_ci_parity_runs_the_gate(gate, path, pending, _container):
    if pending and not (REPO / path).exists():
        pytest.skip(f"pending {pending}: {path} is not written yet, so ci-parity cannot run the {gate} gate; "
                    "this case is strict once the file exists")
    assert (REPO / path).exists(), f"{path} is missing: the {gate} gate is gone"
    hits = _runs(path)
    assert hits, f"scripts/ci-parity.sh does not run {path} ({gate})"
    lines = _command_lines()
    for i in hits:
        window = " ".join(lines[max(0, i - 1): i + 3])
        assert "status=1" in window, f"ci-parity runs {path} but does not fail when it fails: {lines[i]}"


def test_container_gates_run_only_when_the_container_is_running():
    text = "\n".join(_command_lines())
    assert re.search(r"docker inspect -f '\{\{\.State\.Status\}\}'", text), (
        "ci-parity must decide on the container's state, not on `docker inspect` succeeding")
    for _gate, path, _pending, container in GATES:
        if container:
            assert path in text, f"ci-parity has no container step for {path}"


def test_doc_consistency_runs_without_docker():
    """R043 is the always-on gate: it runs with the Docker daemon hidden, as CI has none."""
    lines = _command_lines()
    (i,) = [i for i, ln in enumerate(lines) if "tests/docs/test_docs_consistent.py" in ln]
    assert "DOCKER_HOST=unix:///nonexistent" in " ".join(lines[max(0, i - 2): i + 1])
