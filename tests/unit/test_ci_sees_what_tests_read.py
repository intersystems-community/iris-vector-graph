"""Every file a test reads under `specs/` is one CI checks out (DEBT entry 10).

`.gitignore` ignores `specs/`; the spec files the suite needs are force-added one by one.
A test that reads an ignored file passes on every machine that has the spec checked
out locally and fails in CI, and the red run says "file not found", not why. Main was
red for eight pushes that way (`test_230_graphql_contract`), and a ledger test skipped
its schema check in CI for the same reason.

Two checks:

  * locally (a git worktree): nothing under a referenced spec directory is present but
    ignored. That is the file CI will not have. Fix: `git add -f <file>`.
  * anywhere: every referenced spec directory exists, so in CI the failure names the
    missing directory rather than surfacing as a parse error three calls deep.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
#: What CI runs. Integration and benchmark suites run only where a container is.
SCANNED = REPO / "tests" / "unit"

# "specs/213-x/contracts/y.json" as a string the code uses.
_SLASHED = re.compile(r"specs/((?:[\w.-]+/?)+)")
# "specs" / "a" / "b"  or  "specs", "a", "b"  (os.path.join), across lines.
_JOINED = re.compile(r"""["']specs["']((?:\s*[/,]\s*["'][\w.-]+["'])+)""")
_PART = re.compile(r"""["']([\w.-]+)["']""")


def _code_strings(tree) -> list:
    """String constants other than docstrings: prose naming a spec is not a read."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def _referenced_spec_dirs() -> dict:
    """{spec directory: {test files naming it}}. A named file maps to its directory."""
    found: dict = {}
    for path in SCANNED.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve():
            continue
        src = path.read_text(errors="replace")
        refs = [
            m.group(1).rstrip("/").split("/")
            for text in _code_strings(ast.parse(src))
            for m in _SLASHED.finditer(text)
        ]
        refs += [_PART.findall(m.group(1)) for m in _JOINED.finditer(src)]
        for parts in refs:
            if not parts:
                continue
            target = REPO.joinpath("specs", *parts)
            # A file reference counts against the directory holding it.
            while target.suffix and target.parent != REPO:
                target = target.parent
            found.setdefault(target, set()).add(path.relative_to(REPO).as_posix())
    return found


REFERENCED = _referenced_spec_dirs()


def test_the_scan_finds_the_known_readers():
    """Without this the file passes vacuously if the patterns stop matching."""
    names = {p.relative_to(REPO).as_posix() for p in REFERENCED}
    assert "specs/archive/003-add-graphql-endpoint/contracts" in names
    assert "specs/213-graph-revision-ledger/contracts" in names


def _in_git_worktree() -> bool:
    if shutil.which("git") is None:
        return False
    out = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return out.returncode == 0 and out.stdout.strip() == "true"


def test_no_referenced_spec_file_is_present_but_ignored():
    if not _in_git_worktree():
        pytest.skip("not a git worktree (CI parity copy); the existence check still runs")
    offenders = []
    for spec_dir, readers in sorted(REFERENCED.items()):
        if not spec_dir.exists():
            continue
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--others", "--ignored",
             "--exclude-standard", "--", str(spec_dir)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        offenders += [f"{f}  (read by {', '.join(sorted(readers))})" for f in out]
    assert offenders == [], (
        "ignored by .gitignore, so CI never sees them; `git add -f` each:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "spec_dir",
    sorted(REFERENCED),
    ids=[p.relative_to(REPO).as_posix() for p in sorted(REFERENCED)],
)
def test_every_referenced_spec_directory_exists(spec_dir):
    assert spec_dir.is_dir(), (
        f"{spec_dir.relative_to(REPO)} is read by {sorted(REFERENCED[spec_dir])} but is not "
        "in this checkout. specs/ is gitignored: force-add the files the test reads."
    )
