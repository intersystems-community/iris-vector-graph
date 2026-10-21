"""The AWS SNB runner sends its results off the box after every step.

The SF10 run of 2026-10-17 was lost: the instance terminated at its backstop with every result still on its
disk. run_snb.sh now pushes the results directory to S3 after each step, and pull.sh copies it to the
laptop, which does not depend on the instance's (expiring) credentials.
"""

import re
from pathlib import Path

AWS = Path(__file__).resolve().parents[2] / "scripts" / "bench" / "aws"


def test_every_step_is_followed_by_a_push():
    lines = (AWS / "run_snb.sh").read_text().splitlines()
    steps = [i for i, l in enumerate(lines) if re.search(r'\blog "(setup|bulk load|engine load|weights|align stats|ivg queries|ivg updates|\$eng )', l)]
    assert len(steps) >= 6
    for i in steps:
        window = "\n".join(lines[i : i + 4])
        assert "push" in window, lines[i]


def test_push_targets_the_bucket_and_never_fails_the_run():
    text = (AWS / "run_snb.sh").read_text()
    m = re.search(r"^push\(\) \{(.*)$", text, re.M)
    assert m and 'aws s3 sync "$OUT" "s3://$BUCKET/results/aws-$TAG"' in m.group(1)
    assert "||" in m.group(1)  # a failed push is logged, the run goes on


def test_pull_copies_results_to_the_laptop():
    text = (AWS / "pull.sh").read_text()
    assert "rsync" in text and "docs/performance/alt-bench/results/" in text


def test_both_sides_build_their_indexes_with_the_same_parallel_jobs():
    # SF1 2026-10-19: --index-jobs defaulted to 1, so side B's adjacency (a functional index, built with the edge
    # indexes) ran in one process while side A's ran in a job overlapping the row loads
    line = next(row for row in (AWS / "run_snb.sh").read_text().splitlines() if "snb_bulk.py load" in row)
    assert '--index-jobs "${INDEX_JOBS:-8}"' in line
    assert "--side $s" in line  # one line, both sides


def test_run_native_deploys_with_the_repo_venv_and_stops_on_a_stale_executor():
    """The instance's python3 has no IRIS driver: tcp-deploy failed (ModuleNotFoundError) and the run went on against
    classes older than the planner, where the version check keeps native off and the "native" leg times the SQL path.
    The deploy runs with .venv/bin first on PATH, and the run stops unless it succeeded and the executor is current."""
    src = (AWS / "run_native.sh").read_text()
    deploy = next(line for line in src.splitlines() if "tcp-deploy" in line)
    assert '.venv/bin:$PATH' in deploy
    assert "|| { log" in deploy and "exit 1" in deploy
    assert "_native_executor_current()" in src
