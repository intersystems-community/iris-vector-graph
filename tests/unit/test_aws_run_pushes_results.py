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
