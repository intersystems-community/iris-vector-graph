"""Side trees, namespace guard and namespace script of scripts/bench/alt/setup_sides.py (spec 250).

The tree export uses real `git archive` on this repository (commit 6f17be0f is the baseline);
nothing here touches IRIS.
"""

import json
import os
import subprocess
import sys
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
# CI-parity runs from a scratch copy of the tracked files, which has no git history at all.
needs_head = pytest.mark.skipif(not _has_ref("HEAD"), reason="no git history here (a scratch copy), so no HEAD to export")


def test_default_sides_are_the_documented_ones():
    a, b = sides.SIDES["A"], sides.SIDES["B"]
    assert (a.ref, a.namespace, a.tree) == ("6f17be0f", "IVGBENCHA", str(sides.BENCH_HOME / "ivg-src-A"))
    assert (b.ref, b.namespace, b.tree) == ("alt-integration", "IVGBENCHB", str(sides.BENCH_HOME / "ivg-src-B"))


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


@needs_head
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


@needs_baseline
@needs_head
def test_baseline_tree_gets_the_bfs_stream_methods_the_benchmark_fallback_reads(tmp_path):
    dest = tmp_path / "A"
    info = ss.export_side_tree("6f17be0f", dest)
    bfs = (dest / "iris_src" / "src" / "Graph" / "KG" / "TraversalBFS.cls").read_text()
    assert "ClassMethod BFSFastStream(" in bfs and "ClassMethod ReadBFSResultsStream(" in bfs
    assert info["patches"] == ["baseline-bfs-stream.patch", "baseline-ic1-vlp-ends.patch"]


@needs_head
def test_a_non_baseline_tree_is_left_alone(tmp_path):
    info = ss.export_side_tree("HEAD", tmp_path / "B")
    assert info["patches"] == []


@needs_baseline
@needs_head
def test_baseline_tree_translates_ic1_with_the_ends_only_function(tmp_path):
    dest = tmp_path / "A"
    info = ss.export_side_tree("6f17be0f", dest)
    assert "baseline-ic1-vlp-ends.patch" in info["patches"]
    code = (
        "from iris_vector_graph.cypher.parser import parse_query\n"
        "from iris_vector_graph.cypher.translator import translate_to_sql\n"
        "q = 'MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $a AND f.firstName = $n AND f.sid <> $a "
        "RETURN DISTINCT f.sid, f.lastName ORDER BY f.sid LIMIT 20'\n"
        "print(translate_to_sql(parse_query(q), {'a': '1', 'n': 'x'}).sql)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=dest, env={**os.environ, "PYTHONPATH": str(dest)}, capture_output=True, text=True)
    assert "CY_VLP_ENDS" in out.stdout, out.stderr[-400:]


def test_nothing_the_harness_keeps_lives_in_tmp(monkeypatch, tmp_path):
    """The host reboots and wipes /tmp: side trees, datasets and the comparison venv default to IVG_BENCH_HOME (~/.cache/ivg-bench)."""
    import importlib
    from pathlib import Path

    from scripts.bench.alt import sides

    monkeypatch.delenv("IVG_BENCH_HOME", raising=False)
    s = importlib.reload(sides)
    home = Path.home() / ".cache" / "ivg-bench"
    assert s.BENCH_HOME == home
    assert s.SIDES["A"].tree == str(home / "ivg-src-A") and s.SIDES["B"].tree == str(home / "ivg-src-B")
    assert s.data_root("snb1") == str(home / "snb1" / "out") and s.COMP_PYTHON == str(home / "comp-venv" / "bin" / "python")
    monkeypatch.setenv("IVG_BENCH_HOME", str(tmp_path))
    s = importlib.reload(sides)
    assert s.SIDES["A"].tree == str(tmp_path / "ivg-src-A")
    monkeypatch.delenv("IVG_BENCH_HOME")
    importlib.reload(sides)


def test_the_adjacency_index_is_declared_after_the_deploy(monkeypatch):
    # spec 252: the schema step runs before the deploy compiles Graph.KG.AdjIndex, so its ensure_indexes skips the
    # adjacency index; without a step after the deploy the run measures the unindexed write path
    import inspect

    from scripts.bench.alt import kgbench

    for src in (inspect.getsource(kgbench.setup), inspect.getsource(ss.main)):
        assert src.index("deploy_classes") < src.index("declare_adjacency_index")
    seen = {}

    class _P:
        returncode, stdout, stderr = 0, "ADJ {'idx_edges_adj': 'created', 'idx_labels_adj': 'created'}\n", ""

    def fake(side, code, extra_env=None):
        seen["code"] = code
        return _P()

    monkeypatch.setattr(ss, "_side_python", fake)
    side = sides.SIDES["B"]
    assert "created" in ss.declare_adjacency_index(side)
    assert "ensure_adjacency_index" in seen["code"] and side.namespace in seen["code"]


def test_a_tree_without_the_adjacency_index_reports_it(monkeypatch):
    class _P:
        returncode, stdout, stderr = 0, "ADJ n/a\n", ""

    monkeypatch.setattr(ss, "_side_python", lambda side, code, extra_env=None: _P())
    assert ss.declare_adjacency_index(sides.SIDES["A"]) == "n/a"


@needs_head
def test_a_clone_without_the_baseline_patches_nothing(tmp_path, monkeypatch):
    # CI checks out one commit: the baseline is not in the clone, so no tree it exports can be the baseline
    monkeypatch.setattr(ss.sd, "BASELINE_REF", "0" * 40)
    assert ss.apply_baseline_patches(tmp_path, "HEAD") == []
