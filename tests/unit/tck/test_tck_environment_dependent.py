"""TCK scenarios whose expected answer depends on the host, not on IVG: listed with why, and reported apart.

Temporal2 [6] parses `1818-07-21T21:40:32.142[Europe/Stockholm]` and expects the offset `+00:53:28`. IVG resolves a
zone with Python's `zoneinfo`, which reads the host's tz database (/usr/share/zoneinfo) before the `tzdata` package.
A mainline tz build (macOS, the `tzdata` wheel) answers +00:53:28; Debian and Ubuntu build theirs with `backzone`,
which keeps Stockholm's own 1818 offset, and answer +01:12:12 (both tz 2026e, 2026-11-07). Both are right for their
database, and IVG does not pin one to satisfy a test. tests/tck/environment_dependent.txt lists such scenarios, each
with its reason; summarize.py still counts a listed failure as eligible and failed, and names it on a line of its own
so a gate can tell it from a regression. docs/dev/TCK.md says the same.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.tck import summarize as S

ROOT = Path(__file__).resolve().parents[3]
TEMPORAL2_6 = ("Temporal2.Temporal2 - Create Temporal Values from a String::[6] Should parse date time with named time "
               "zone from string -- @1.5")


def test_the_list_names_temporal2_6_with_its_reason():
    listed = S.environment_dependent()
    assert TEMPORAL2_6 in listed
    assert "backzone" in listed[TEMPORAL2_6] and "+01:12:12" in listed[TEMPORAL2_6]


def test_every_listed_scenario_is_in_the_vendored_features():
    features = ROOT / "tests" / "tck" / "features"
    if not any(features.glob("**/*.feature")):
        pytest.skip("needs the vendored TCK features (git submodule vendor/opencypher)")
    for key in S.environment_dependent():
        stem, title = key.split(".", 1)[0], key.split("::", 1)[1].split(" -- @")[0]
        text = "".join(p.read_text() for p in features.glob(f"**/{stem}.feature"))
        assert title.split("] ", 1)[1] in text, key


def test_an_entry_without_a_reason_is_refused(tmp_path):
    bad = tmp_path / "env.txt"
    bad.write_text("# comment\nTemporal2.X::[6] a scenario\n")
    with pytest.raises(ValueError, match="reason"):
        S.environment_dependent(bad)


def _run(tmp_path, status: str) -> Path:
    run = tmp_path / "run" / "temporal"
    run.mkdir(parents=True)
    classname, name = TEMPORAL2_6.split("::")
    body = "<failure message='offset'/>" if status == "failed" else ""
    (run / "TESTS-Temporal2.xml").write_text(
        f"<testsuite><testcase classname='features.expressions.temporal.{classname}' name='{name}' "
        f"status='{status}'>{body}</testcase></testsuite>")
    return tmp_path / "run"


def test_a_listed_failure_is_counted_and_named_apart(tmp_path, capsys):
    assert S.main([str(_run(tmp_path, "failed")), "--features", ""]) == 0
    out = capsys.readouterr().out
    assert "0 / 1 eligible" in out and "failed 1, skipped by harness 0" in out
    assert f"environment-dependent failures: 1 (tests/tck/environment_dependent.txt)\n  ENV {TEMPORAL2_6}" in out


def test_a_listed_scenario_that_passes_is_not_named(tmp_path, capsys):
    assert S.main([str(_run(tmp_path, "passed")), "--features", ""]) == 0
    out = capsys.readouterr().out
    assert "1 / 1 eligible" in out and "environment-dependent failures: 0" in out and "ENV " not in out
