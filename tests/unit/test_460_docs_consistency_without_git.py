"""4.6.0: the docs-consistency check answers the same without git history (a `git archive`, ci-parity's copy, CI's
shallow clone) as in a checkout with the release tags.

A history doc may name a path a past release shipped (`docs/performance/`). tests/docs/consistency.py asked git
whether a release tag held it, and without tags fell back to the legacy manifest's class files, which do not list
such paths: four docs failed in a tree with no `.git`. The paths the history docs name are now in
tests/docs/past_release_paths.txt, generated from the tags and committed, and the fallback reads it.

Each test says what it needs. The copy of what git tracks needs a git work tree, and the list's check against the tags
needs the tags; each skips, naming it, where it is missing (a `git archive` tree, CI's shallow clone). The disk-copy
test needs neither, so the fallback is tested in every kind of tree.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.docs import consistency

REPO = Path(__file__).resolve().parents[2]
#: Left out of the disk copy: git itself and caches and environments no doc check reads
NOT_COPIED = shutil.ignore_patterns(".git", ".venv", "venv*", "__pycache__", ".pytest_cache", ".mypy_cache",
                                    ".ruff_cache", "node_modules", ".tox", "*.egg-info")


def _git_work_tree() -> bool:
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=REPO, capture_output=True, text=True)
    except FileNotFoundError:  # no git installed
        return False
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO.resolve()


@pytest.fixture(scope="module")
def copy_without_git(tmp_path_factory) -> Path:
    """Every file git tracks, as the working tree holds it, in a directory with no `.git`."""
    if not _git_work_tree():
        pytest.skip(f"needs a git work tree at {REPO} (git ls-files lists the copy); this tree has none")
    out = tmp_path_factory.mktemp("nogit")
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    for rel in filter(None, files.split("\0")):
        src, dst = REPO / rel, out / rel
        if src.is_symlink() or src.is_file():  # a symlink as a symlink, as `git archive` writes it
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
    return out


def _findings(repo: Path) -> dict:
    ctx = consistency.build_context(repo)
    return {
        doc: sorted(f.key() for f in consistency.check_doc(doc, (repo / doc).read_text(), ctx, reader=doc in ctx.reader_docs))
        for doc in consistency.docs_to_check(repo, ctx)
    }


def test_the_copy_has_no_git_and_so_no_release_tags(copy_without_git):
    assert not (copy_without_git / ".git").exists()
    assert consistency._release_tags(copy_without_git) == ()


def test_the_check_agrees_with_and_without_git(copy_without_git):
    with_git, without = _findings(REPO), _findings(copy_without_git)
    assert set(with_git) == set(without)
    differ = {doc: (with_git[doc], without[doc]) for doc in with_git if with_git[doc] != without[doc]}
    assert differ == {}


def test_the_past_release_paths_are_what_the_tags_hold():
    # each listed path is one a release tag shipped, where the tags are here to ask
    ctx = consistency.build_context(REPO)
    if not ctx.release_tags:
        pytest.skip("needs the release tags (git tag -l 'v*', v3.2.0 to v4.5.1) to check the list against; "
                    "this tree has none (fetch the tags)")
    listed = consistency.past_release_paths()
    assert listed, "the list is empty"
    off = [p for p in listed if not any(
        subprocess.run(["git", "cat-file", "-e", f"{t}:{p}"], cwd=REPO, capture_output=True).returncode == 0
        for t in ctx.release_tags)]
    assert off == []


@pytest.fixture(scope="module")
def copy_from_disk(tmp_path_factory) -> Path:
    """The tree as the disk holds it (no `git ls-files`), with no `.git`: works in a `git archive` tree too."""
    out = tmp_path_factory.mktemp("fromdisk") / "tree"
    shutil.copytree(REPO, out, symlinks=True, ignore=NOT_COPIED)
    return out


def _all_findings(repo: Path, ctx) -> list:
    return [f for doc in consistency.docs_to_check(repo, ctx)
            for f in consistency.check_doc(doc, (repo / doc).read_text(), ctx, reader=doc in ctx.reader_docs)]


def _paths(findings) -> set:
    return {f.item.rstrip("/") for f in findings if f.kind == "path"}


def test_without_git_the_list_answers_for_the_tags(copy_from_disk):
    listed = consistency.past_release_paths()
    assert listed, "tests/docs/past_release_paths.txt lists no path"
    assert not (copy_from_disk / ".git").exists()
    ctx = consistency.build_context(copy_from_disk)
    assert ctx.release_tags == () and listed <= ctx.manifest_paths
    # with the list, no doc's path finding names a listed path, and every finding is one the baseline knows
    found = _all_findings(copy_from_disk, ctx)
    assert _paths(found) & listed == set()
    known = consistency.load_baseline()
    assert sorted(f.key() for f in found if f.key() not in known) == []
    # the list is what answers: without it, and with no tags to ask, each listed path is a finding again
    ctx.manifest_paths -= listed
    assert listed <= _paths(_all_findings(copy_from_disk, ctx))
