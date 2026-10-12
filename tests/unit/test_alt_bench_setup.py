"""Side trees, namespace guard and namespace script of scripts/bench/alt/setup_sides.py (spec 250).

The tree export uses real `git archive` on this repository (commit 6f17be0f is the baseline);
nothing here touches IRIS.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.bench.alt import setup_sides as ss
from scripts.bench.alt import sides

REPO = Path(__file__).resolve().parents[2]


def _has_ref(ref):
    return (
        subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
            capture_output=True,
        ).returncode
        == 0
    )


needs_baseline = pytest.mark.skipif(not _has_ref("6f17be0f"), reason="baseline commit 6f17be0f not in this clone")


def test_default_sides_are_the_documented_ones():
    a, b = sides.SIDES["A"], sides.SIDES["B"]
    assert (a.ref, a.namespace, a.tree) == ("6f17be0f", "IVGBENCHA", "/tmp/ivg-src-A")
    assert (b.ref, b.namespace, b.tree) == ("alt-integration", "IVGBENCHB", "/tmp/ivg-src-B")


@pytest.mark.parametrize("ns", ["USER", "IVGTEST", "IVGSEC", "IVGPERFA", "IVGFIX", "ivgbencha ", "%SYS", ""])
def test_namespace_guard_refuses_foreign_namespaces(ns):
    with pytest.raises(ValueError):
        sides.check_namespace(ns)


@pytest.mark.parametrize("ns", ["IVGBENCHA", "IVGBENCHB", "IVGBENCHSMOKE"])
def test_namespace_guard_allows_ours(ns):
    assert sides.check_namespace(ns) == ns


def test_namespace_script_creates_database_and_never_deletes():
    text = ss.namespace_script("IVGBENCHA")
    assert "Config.Namespaces" in text and "Config.Databases" in text
    assert "/usr/irissys/mgr/ivgbencha/" in text
    low = text.lower()
    for forbidden in ("delete", "kill", "remove", "dismount", "rmdir"):
        assert forbidden not in low
    # Idempotent: an existing namespace is left alone.
    assert "Exists" in text


def test_namespace_script_refuses_foreign_namespace():
    with pytest.raises(ValueError):
        ss.namespace_script("USER")


@needs_baseline
def test_export_side_tree_layout_and_overlay(tmp_path):
    dest = tmp_path / "ivg-src-A"
    info = ss.export_side_tree("6f17be0f", dest, repo_root=REPO, overlay_ref="HEAD", data_dir=None)
    assert (dest / "iris_vector_graph" / "__init__.py").is_file()
    assert (dest / "iris_src" / "src" / "Graph" / "KG").is_dir()
    # Baseline predates the host module; the overlay must not bring the working tree's library in.
    assert not (dest / "iris_vector_graph" / "host.py").exists()
    # Harness code comes from the overlay ref.
    assert (dest / "scripts" / "bench" / "bench_full.py").is_file()
    assert (dest / "pyproject.toml").is_file()
    marker = json.loads((dest / sides.MARKER).read_text())
    assert marker["ref"] == "6f17be0f"
    assert len(marker["commit"]) == 40 and marker["commit"].startswith("6f17be0f")
    assert info["commit"] == marker["commit"]


@needs_baseline
def test_export_is_repeatable_and_drops_stale_files(tmp_path):
    dest = tmp_path / "ivg-src-A"
    ss.export_side_tree("6f17be0f", dest, repo_root=REPO, overlay_ref="HEAD", data_dir=None)
    (dest / "iris_vector_graph" / "stale_leftover.py").write_text("x = 1\n")
    ss.export_side_tree("6f17be0f", dest, repo_root=REPO, overlay_ref="HEAD", data_dir=None)
    assert not (dest / "iris_vector_graph" / "stale_leftover.py").exists()


def test_export_refuses_to_wipe_an_unmarked_directory(tmp_path):
    dest = tmp_path / "precious"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine")
    with pytest.raises(RuntimeError):
        ss.export_side_tree("HEAD", dest, repo_root=REPO, overlay_ref="HEAD", data_dir=None)
    assert (dest / "keep.txt").read_text() == "mine"


def test_export_links_data_dir(tmp_path):
    data = tmp_path / "somedata"
    data.mkdir()
    dest = tmp_path / "side"
    ss.export_side_tree("HEAD", dest, repo_root=REPO, overlay_ref="HEAD", data_dir=data)
    assert os.path.realpath(dest / "data") == os.path.realpath(data)


@needs_baseline
def test_imported_package_resolves_inside_side_tree(tmp_path):
    dest = tmp_path / "ivg-src-A"
    ss.export_side_tree("6f17be0f", dest, repo_root=REPO, overlay_ref="HEAD", data_dir=None)
    path = ss.imported_package_path(dest)
    assert Path(path).resolve().is_relative_to(dest.resolve())


def test_source_hash_matches_the_conftest_digest(tmp_path):
    import hashlib

    f = tmp_path / "A.cls"
    f.write_bytes(b"Class A {}\n")
    assert ss.source_hash(f) == hashlib.sha256(b"Class A {}\n").hexdigest()[:20]
    # Same function the conftest uses, so a recorded hash is recognised as "already deployed".
    from tests.conftest import _source_hash

    assert ss.source_hash(f) == _source_hash(str(f))


def test_class_names_come_from_the_class_line_not_the_path(tmp_path):
    src = tmp_path / "iris_src" / "src"
    (src / "Graph" / "KG").mkdir(parents=True)
    (src / "Graph" / "KG" / "X.cls").write_text("Class Graph.KG.X Extends %RegisteredObject\n{\n}\n")
    (src / "Odd.cls").write_text("/// doc\nClass PageRankEmbedded Extends %RegisteredObject\n{\n}\n")
    assert ss.class_names(tmp_path) == ["Graph.KG.X", "PageRankEmbedded"]
