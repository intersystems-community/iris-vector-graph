"""Spec 252: BuildKG builds the adjacency functional indexes with parallel jobs.

%BuildIndices runs each row's callback in one process by default: at SNB SF1 the edge index took 701 s against the
unindexed side's 126 s for its B-tree indexes (the unindexed loader writes ^KG in a job that overlaps the row loads).
With 8 jobs the SF0.1 build drops from 20.9 s to 5.8 s, and serial and parallel builds leave byte-identical
^KG("out"/"in") (checksummed over all 2,164,444 entries each).
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "TraversalBuild.cls"


def test_both_index_builds_pass_a_job_count():
    text = SRC.read_text(encoding="utf-8")
    calls = re.findall(r'"%BuildIndices",\s*\$ListBuild\("(idx\w+adj)"\)([^\n]*)', text)
    assert {name for name, _ in calls} == {"idxedgesadj", "idxlabelsadj"}
    for name, rest in calls:
        assert rest.count(",") >= 8, f"{name}: %BuildIndices is called without its job-count argument"
        assert "tJobs" in rest


def test_the_job_count_comes_from_the_cpu_count_and_is_bounded():
    text = SRC.read_text(encoding="utf-8")
    assert "NumberOfCPUs()" in text
    assert re.search(r"Set tJobs = \$Select\(", text)
