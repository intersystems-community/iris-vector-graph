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
    """Every reference to a moved file follows it (R019). History (CHANGELOG, sign-offs, specs) keeps old paths."""
    import subprocess

    olds = [o for o in MOVED if o != "docs/research"] + ["docs/research/"]
    out = subprocess.run(
        ["git", "grep", "-nF", *sum((["-e", o] for o in olds), []), "--", ".",
         ":!specs", ":!CHANGELOG.md", ":!PRE_RELEASE_CHECKLIST.md", ":!*.pyc",
         ":!tests/unit/test_docs_layout.py"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert out.returncode in (0, 1), out.stderr
    assert out.stdout == "", "old paths still named:\n" + out.stdout
