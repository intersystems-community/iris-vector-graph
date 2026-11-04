"""4.6.0 R017: the docs tree has two audiences, and contributor material is out of their path.

`docs/` top level holds what an application developer or an IRIS operator reads. Testing policy, TCK, coverage,
benchmark harness notes, research and design notes live under `docs/dev/` (`docs/dev/notes/` for the notes).
Scratch files are gone, and so is `docs/python/PYTHON_SDK.md` (outdated since v1.47.0; the generated API
reference replaces it). `CONTRIBUTING.md` is where a contributor starts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

DELETED = (
    "docs/dc_contest_raw.md",
    "docs/bulk-loader-pr.md",
    "docs/rdf2sql-brief.txt",
    "docs/flowchart LR.mmd",
    "docs/python/PYTHON_SDK.md",
)

#: old path -> new path (R018)
MOVED = {
    "docs/TESTING_POLICY.md": "docs/dev/TESTING_POLICY.md",
    "docs/TCK.md": "docs/dev/TCK.md",
    "docs/coverage.md": "docs/dev/coverage.md",
    "docs/objectscript-coverage-floor.json": "docs/dev/objectscript-coverage-floor.json",
    "docs/arno-reader-proof.json": "docs/dev/arno-reader-proof.json",
    "docs/setup/UV_SETUP.md": "docs/dev/UV_SETUP.md",
    "docs/performance/ALT_VS_BASELINE.md": "docs/dev/notes/ALT_VS_BASELINE.md",
    "docs/performance/alt-bench": "docs/dev/notes/alt-bench",
    "docs/enhancements": "docs/dev/notes/enhancements",
    "docs/research": "docs/dev/notes/research",
    "docs/rdf-landscape-spike-2026.md": "docs/dev/notes/rdf-landscape-spike-2026.md",
    "docs/cypher-gap-recommendations.md": "docs/dev/notes/cypher-gap-recommendations.md",
    "docs/graph-analytics-detection-roadmap.md": "docs/dev/notes/graph-analytics-detection-roadmap.md",
    "docs/index-maintenance-architecture-review.md": "docs/dev/notes/index-maintenance-architecture-review.md",
    "docs/ldbc_snb_query_analysis.md": "docs/dev/notes/ldbc_snb_query_analysis.md",
    "docs/for-opsreview": "docs/dev/notes/for-opsreview",
}

#: What may sit at docs/ top level: reader docs and the folders that hold them. `dev/` is the one contributor
#: folder. API_REFERENCE.md is R025's; OPERATIONS.md goes in R032 (folded into ADMIN_GUIDE).
READER_TOP_LEVEL = {
    "ADMIN_API.md", "ADMIN_GUIDE.md", "API_REFERENCE.md", "BENCHMARKS.md", "COMMUNITIES.md", "FHIR_GRAPH.md",
    "KNOWN_ISSUES.md", "OPERATIONS.md", "SEMANTIC_LAYER.md", "USER_GUIDE.md", "iris-sql-quirks.md",
    "ledger-prometheus-hook.md",
    "adr", "architecture", "demos", "migration", "notebooks", "performance", "releases", "setup",
    "dev",
}


@pytest.mark.parametrize("path", DELETED)
def test_scratch_files_and_the_outdated_sdk_doc_are_gone(path):
    assert not (ROOT / path).exists(), path


@pytest.mark.parametrize("old,new", sorted(MOVED.items()))
def test_contributor_material_lives_under_docs_dev(old, new):
    assert (ROOT / new).exists(), f"{new} missing"
    assert not (ROOT / old).exists(), f"{old} still at its old path"


def test_docs_top_level_holds_only_reader_facing_docs():
    extra = sorted(p.name for p in DOCS.iterdir() if p.name not in READER_TOP_LEVEL and p.name != ".DS_Store")
    assert not extra, f"not reader-facing, move under docs/dev/: {extra}"


def test_contributing_exists_and_points_into_docs_dev():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    for target in ("docs/dev/TESTING_POLICY.md", "docs/dev/TCK.md", "docs/dev/coverage.md",
                   "scripts/ci-parity.sh", "scripts/enterprise-container.sh", "PRE_RELEASE_CHECKLIST.md"):
        assert target in text, target


def test_the_arno_reader_proof_is_read_from_its_new_path():
    from iris_vector_graph import arno_readers

    assert arno_readers._DEFAULT_PROOF == ROOT / "docs" / "dev" / "arno-reader-proof.json"
    assert arno_readers._DEFAULT_PROOF.is_file()


def test_the_coverage_floor_is_read_from_its_new_path():
    import importlib.util

    spec = importlib.util.spec_from_file_location("linecov", ROOT / "scripts" / "line_coverage" / "linecov.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FLOOR_PATH == ROOT / "docs" / "dev" / "objectscript-coverage-floor.json"
    assert mod.FLOOR_PATH.is_file()


def test_no_tracked_file_names_an_old_path():
    """Every reference to a moved file follows it (R019). History (CHANGELOG, sign-offs, specs) keeps old paths.

    In a checkout this is `git grep`; in ci-parity's scratch copy (no `.git`) the same files are walked."""
    hits = _old_path_hits(ROOT)
    assert hits == "", "old paths still named:\n" + hits


def _plain_copy(tmp_path):
    """A tree laid out like ci-parity's scratch copy: no .git, plus what a test run leaves behind."""
    files = {
        "docs/guide.md": "intro\nsee docs/TCK.md for the suite\n",
        "README.md": "nothing old here\n",
        "CHANGELOG.md": "moved docs/TCK.md\n",
        "PRE_RELEASE_CHECKLIST.md": "docs/coverage.md\n",
        f"{_SCAN_EXCLUDED[0]}/262-x/plan.md": "docs/research/foo.md\n",  # the spec tree, without naming it
        "tests/unit/test_docs_layout.py": "docs/TCK.md\n",
        "pkg/__pycache__/m.cpython-312.pyc": "docs/TCK.md\n",
        "pkg/m.pyc": "docs/TCK.md\n",
        "iris_vector_graph.egg-info/PKG-INFO": "docs/TCK.md\n",
        "sub/module/file.md": "docs/TCK.md\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    (tmp_path / ".gitmodules").write_text('[submodule "m"]\n\tpath = sub/module\n')
    (tmp_path / "blob.bin").write_bytes(b"\x00docs/TCK.md")
    (tmp_path / "linked").symlink_to(tmp_path / "docs")
    return tmp_path


def test_old_path_scan_walks_a_copy_that_has_no_git(tmp_path):
    root = _plain_copy(tmp_path)
    assert not _is_git_checkout(root)
    assert _old_path_hits(root) == "docs/guide.md:2:see docs/TCK.md for the suite\n"


def test_old_path_scan_without_git_passes_a_clean_copy(tmp_path):
    root = _plain_copy(tmp_path)
    (root / "docs/guide.md").write_text("see docs/dev/TCK.md for the suite\n")
    assert _old_path_hits(root) == ""


# ── the old-path scan ──────────────────────────────────────────────────────────

#: History keeps old paths; this test names them. The same set as the `git grep` pathspecs below.
_SCAN_EXCLUDED = ("specs", "CHANGELOG.md", "PRE_RELEASE_CHECKLIST.md", "tests/unit/test_docs_layout.py")
#: In a plain copy, what a test run or an editable install leaves behind (all gitignored, so a checkout's
#: `git grep` never sees them either).
_GENERATED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv",
                   "build", "dist", ".eggs", "node_modules"}


def _old_paths():
    return [o for o in MOVED if o != "docs/research"] + ["docs/research/"]


def _is_git_checkout(root: Path) -> bool:
    """True when `root` is the top of a git work tree (not merely inside some other repository)."""
    import subprocess

    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True)
    return out.returncode == 0 and Path(out.stdout.strip()).resolve() == root.resolve()


def _copy_files(root: Path):
    """The files ci-parity's copy step carries, walked in a tree with no .git: symlinks are copied as links (and
    `git grep` does not follow them), submodules are not fetched, generated files are not tracked."""
    import os
    import re

    gm = root / ".gitmodules"
    submodules = re.findall(r"(?m)^\s*path\s*=\s*(\S+)", gm.read_text()) if gm.exists() else []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in _GENERATED_DIRS and not d.endswith(".egg-info") and not (here / d).is_symlink()
            and (d if rel_dir == "." else f"{rel_dir}/{d}") not in submodules
        )
        for name in sorted(filenames):
            p = here / name
            if p.is_symlink() or name.endswith(".pyc"):
                continue
            yield p.relative_to(root).as_posix()


def _old_path_hits(root: Path) -> str:
    """`path:line:text` for each line naming an old path, outside history; "" when there is none."""
    import subprocess

    olds = _old_paths()
    if _is_git_checkout(root):
        out = subprocess.run(
            ["git", "grep", "-nF", *sum((["-e", o] for o in olds), []), "--", ".",
             *(f":!{x}" for x in _SCAN_EXCLUDED), ":!*.pyc"],
            cwd=root, capture_output=True, text=True,
        )
        assert out.returncode in (0, 1), out.stderr
        return out.stdout
    hits = []
    for rel in _copy_files(root):
        if any(rel == x or rel.startswith(x + "/") for x in _SCAN_EXCLUDED):
            continue
        data = (root / rel).read_bytes()
        if b"\0" in data:
            continue  # binary: git grep says "Binary file matches", never a line
        for n, line in enumerate(data.decode("utf-8", errors="replace").split("\n"), 1):
            if any(o in line for o in olds):
                hits.append(f"{rel}:{n}:{line}\n")
    return "".join(hits)
