"""TCK scenarios whose expected answer depends on the host, not on IVG: listed with why, and reported apart.

None is listed. Temporal2 [6] was, until 4.7.0: it parses `1818-07-21T21:40:32.142[Europe/Stockholm]` and expects
`+00:53:28`, and IVG read the zone with Python's `zoneinfo`, which reads the host's tz database first, so Debian and
Ubuntu (tzdata built with `backzone`) answered +01:12:12. IVG now reads the `tzdata` wheel, a dependency, first
(iris_vector_graph/_tz.py, tests/unit/test_470_tz.py), and every host answers +00:53:28. The list and the summary's
line for it stay, for a scenario that is right both ways on its host; the tests below use a stand-in entry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.tck import summarize as S

ROOT = Path(__file__).resolve().parents[3]
TEMPORAL2_6 = ("Temporal2.Temporal2 - Create Temporal Values from a String::[6] Should parse date time with named time "
               "zone from string -- @1.5")


def test_temporal2_6_is_not_listed():
    """The tzdata wheel answers it on every host (4.7.0 row 15), so it is not environment-dependent."""
    assert TEMPORAL2_6 not in S.environment_dependent()
    assert S.environment_dependent() == {}


@pytest.fixture
def listed(monkeypatch):
    """The list holding one stand-in entry, the scenario the runs below fail or pass."""
    monkeypatch.setattr(S, "environment_dependent", lambda *a: {TEMPORAL2_6: "a stand-in reason"})


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


def test_a_listed_failure_is_counted_and_named_apart(tmp_path, capsys, listed):
    assert S.main([str(_run(tmp_path, "failed")), "--features", ""]) == 0
    out = capsys.readouterr().out
    assert "0 / 1 eligible" in out and "failed 1, skipped by harness 0" in out
    assert f"environment-dependent failures: 1 (tests/tck/environment_dependent.txt)\n  ENV {TEMPORAL2_6}" in out


def test_a_listed_scenario_that_passes_is_not_named(tmp_path, capsys, listed):
    assert S.main([str(_run(tmp_path, "passed")), "--features", ""]) == 0
    out = capsys.readouterr().out
    assert "1 / 1 eligible" in out and "environment-dependent failures: 0" in out and "ENV " not in out
