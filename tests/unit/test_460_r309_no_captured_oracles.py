"""4.6.0 R309: no test takes its expected answer from IVG's own earlier output.

A test whose expected value is what IVG answered once (a "baseline", a "capture", a "golden" file) passes as long
as IVG keeps answering the same, right or wrong: `test_235_patient_ranking_e2e.py` held 4.1.0's PPR scores, which
stopped early, and so pinned a wrong answer until R270 changed the stop rule and the capture had to be edited around.
The expected answer comes from somewhere else: worked by hand, a reference computation (networkx, a walk over the
rows), or the input the test wrote.

The lint, over every Python file under tests/: a string naming a data file (json, jsonl, csv, tsv, txt, sql, parquet,
pkl, npy) whose name says baseline, capture, golden, expected or recorded, and an import of a module of this repo whose
name says capture, baseline or golden, are flagged. Each flag must be in ALLOWED, with the reason the file holds
something other than IVG's answers (a shape, an input, a perf number, a recording for a tool), or be replaced by a
reference computation. An ALLOWED entry nothing flags any more is stale and fails too.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAME = re.compile(r"baseline|captur|golden|expected|recorded", re.I)
MODULE_NAME = re.compile(r"baseline|captur|golden", re.I)
DATA = re.compile(r"\.(json|jsonl|csv|tsv|txt|sql|parquet|pkl|npy)$", re.I)

#: (file under tests/, what it names) -> why its content is not IVG's answer
ALLOWED = {
    ("tests/unit/test_234_multigraph_cypher_sql.py", "tests.unit.golden.gen_234_mode_off_sql"):
        "translator golden SQL: the SQL text the translator emitted before multigraph, a shape check (FR-009); "
        "answers are checked by the E2E and TCK suites",
    ("tests/regression/test_engine_api.py", "engine_api_baseline.json"):
        "the public method names and signatures (an API-surface guard), not query answers",
    ("tests/e2e/fixtures/old_releases.py", "ivg-*.expected.json"):
        "the manifest scripts/fixtures/generate_old_snapshot.py writes of what its fixed seed loaded into an old "
        "release: the upgrade tests' input, which must survive the upgrade",
    ("tests/e2e/fixtures/old_releases.py", ".expected.json"):
        "the same manifests' file suffix",
    ("tests/unit/test_235_docs.py", "baseline.json"):
        "benchmark medians (timings) the 235 docs quote, compared with a fresh bench run; no answers",
    ("tests/tck/environment.py", "tests.tck.capture"):
        "IVG_TCK_CAPTURE records results for offline rescoring; the TCK's expected tables are the openCypher feature "
        "files'",
    ("tests/tck/steps/query.py", "tests.tck.capture"): "as tests/tck/environment.py",
    ("tests/tck/steps/results.py", "tests.tck.capture"): "as tests/tck/environment.py",
    ("tests/unit/tck/test_tck_capture.py", "tests.tck.capture"): "the unit test of the recorder itself",
    ("tests/unit/tck/test_tck_rescore.py", "tests.tck.capture"): "the rescore tool's unit test, over hand-made records",
}


def _repo_module(name: str) -> bool:
    path = REPO.joinpath(*name.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def flags(path: Path) -> set:
    """(what) for each captured-output data file or capture module `path` names."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" not in node.value:
            base = node.value.rsplit("/", 1)[-1]
            if DATA.search(base) and NAME.search(base) and not node.value.startswith("/tmp/"):
                out.add(base)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] + [f"{node.module}.{a.name}" for a in node.names] if node.module else [])
            for m in mods:
                if MODULE_NAME.search(m) and _repo_module(m):
                    out.add(m)
    return out


@functools.lru_cache(maxsize=1)
def _all_flags() -> dict:
    found = {}
    for path in sorted((REPO / "tests").rglob("*.py")):
        if "__pycache__" in path.parts or path.parts[-2:-1] == ("benchmarks",) or path == Path(__file__).resolve():
            continue
        for what in flags(path):
            found[(str(path.relative_to(REPO)), what)] = path
    return found


def test_the_lint_sees_what_it_is_about():
    found = _all_flags()
    assert ("tests/regression/test_engine_api.py", "engine_api_baseline.json") in found
    assert ("tests/unit/test_234_multigraph_cypher_sql.py", "tests.unit.golden.gen_234_mode_off_sql") in found


def test_no_test_takes_its_expected_answer_from_a_capture():
    unlisted = sorted(k for k in _all_flags() if k not in ALLOWED)
    assert unlisted == [], ("these tests read captured output; replace it with a reference computation or list it in "
                            f"ALLOWED with the reason it is not IVG's answer: {unlisted}")


def test_every_allowed_entry_is_still_flagged():
    found = _all_flags()
    stale = sorted(k for k in ALLOWED if k not in found)
    assert stale == [], f"ALLOWED entries nothing flags any more (remove them): {stale}"


def test_every_allowed_entry_says_why():
    for key, why in ALLOWED.items():
        assert len(why) >= 15, key


def test_the_235_ranking_test_no_longer_reads_the_410_capture():
    """The capture this task removed: 4.1.0's early-stopped PPR scores (specs/235-.../baseline_410.json)."""
    assert flags(REPO / "tests" / "e2e" / "test_235_patient_ranking_e2e.py") == set()
