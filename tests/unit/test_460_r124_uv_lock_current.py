"""4.6.0 R124: `uv.lock` records what `pyproject.toml` asks for, and every dependency in it resolves.

The lock was made before the `dev` extra gained `obonet` and `rdflib`, and `uv lock` could not remake it: the `ai`
extra required `iris-agentic-dev`, which is on no package index (it is an MCP server binary, installed on its own,
and nothing in IVG imports it or names the extra). `uv lock` resolves every extra, so one unresolvable requirement
kept the whole lock stale, and `uv sync` installed a `dev` without them.

CI installs with pip from `pyproject.toml` (`.github/workflows/ci.yml`) and reads no lock; this keeps the lock true
for the `uv sync` that CONTRIBUTING and docs/dev/UV_SETUP.md describe. Offline: the comparison is of the two files,
not a resolution (`uv lock --check` needs the index).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    req = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")
    wanted = set()
    for spec in project.get("dependencies", []):
        name, _, rest = req.match(spec).groups()
        wanted.add((_norm(name), None, rest.split(";")[0].strip()))
    for extra, specs in project.get("optional-dependencies", {}).items():
        for spec in specs:
            name, _, rest = req.match(spec).groups()
            wanted.add((_norm(name), extra, rest.split(";")[0].strip()))
    return project, wanted


def _locked():
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    entry = next(p for p in lock["package"] if p["name"] == "iris-vector-graph")
    got = set()
    for d in entry["metadata"]["requires-dist"]:
        m = re.search(r"extra == '([^']+)'", d.get("marker", ""))
        got.add((_norm(d["name"]), m.group(1) if m else None, d.get("specifier", "")))
    return entry, got


def test_the_lock_records_every_requirement_of_the_pyproject():
    _, wanted = _pyproject()
    _, got = _locked()
    norm = lambda s: {(n, e, re.sub(r"\s+", "", spec)) for n, e, spec in s}  # noqa: E731
    assert norm(wanted) - norm(got) == set(), "in pyproject.toml, not in uv.lock: run `uv lock`"
    assert norm(got) - norm(wanted) == set(), "in uv.lock, not in pyproject.toml: run `uv lock`"


def test_the_lock_provides_the_pyprojects_extras():
    project, _ = _pyproject()
    entry, _ = _locked()
    assert sorted(entry["metadata"]["provides-extras"]) == sorted(project["optional-dependencies"])


def test_no_extra_needs_a_package_no_index_has():
    """iris-agentic-dev is installed as a binary (its own installer), never from an index."""
    _, wanted = _pyproject()
    assert [w for w in wanted if w[0] == "iris-agentic-dev"] == []


def test_ci_installs_from_the_pyproject_not_the_lock():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert 'pip install -e ".[dev,full]"' in ci and "uv.lock" not in ci and "uv sync" not in ci
