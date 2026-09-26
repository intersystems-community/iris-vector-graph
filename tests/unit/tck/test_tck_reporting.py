"""Unit tests for the TCK reporting kit: scripts/tck/summarize.py and the
harness's handling of upstream ``@ignore`` scenarios.

No IRIS needed: feature files and JUnit XML are built in a temp dir.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.tck import summarize as S

FEATURE = textwrap.dedent(
    """\
    Feature: Demo1 - A demo feature

      Scenario: [1] Plain
        Given an empty graph

      @ignore @skipStyleCheck
      Scenario: [2] Ignored upstream
        Given an empty graph

      @skipGrammarCheck
      Scenario: [3] Grammar tooling tag only
        Given an empty graph

      @ignore
      Scenario Outline: [4] Ignored outline
        Given an empty graph

        Examples:
          | x |
          | 1 |
          | 2 |

      Scenario Outline: [5] Outline with one ignored examples block
        Given an empty graph

        Examples:
          | x |
          | 1 |

        @ignore
        Examples:
          | x |
          | 2 |
    """
)

FEATURE_ALL_IGNORED = textwrap.dedent(
    """\
    @ignore
    Feature: Demo2 - Whole feature ignored

      Scenario: [1] Inherits the feature tag
        Given an empty graph
    """
)


def _testcase(classname: str, name: str, status: str) -> str:
    child = {"passed": "", "failed": "<failure message='x'/>", "skipped": "<skipped/>"}[status]
    return f'<testcase classname="{classname}" name="{name}" status="{status}">{child}</testcase>'


def _junit(cases: list[tuple[str, str, str]]) -> str:
    body = "".join(_testcase(*c) for c in cases)
    return f'<testsuite name="x" tests="{len(cases)}">{body}</testsuite>'


@pytest.fixture
def features_dir(tmp_path: Path) -> Path:
    d = tmp_path / "features" / "clauses" / "demo"
    d.mkdir(parents=True)
    (d / "Demo1.feature").write_text(FEATURE)
    (d / "Demo2.feature").write_text(FEATURE_ALL_IGNORED)
    return tmp_path / "features"


D1 = "Demo1.Demo1 - A demo feature"
D2 = "Demo2.Demo2 - Whole feature ignored"


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    out = tmp_path / "run"
    (out / "demo").mkdir(parents=True)
    cases = [
        (D1, "[1] Plain", "passed"),
        (D1, "[2] Ignored upstream", "passed"),  # old harness ran it and "passed"
        (D1, "[3] Grammar tooling tag only", "failed"),
        (D1, "[4] Ignored outline -- @1.1 ", "skipped"),
        (D1, "[4] Ignored outline -- @1.2 ", "skipped"),
        (D1, "[5] Outline with one ignored examples block -- @1.1 ", "passed"),
        (D1, "[5] Outline with one ignored examples block -- @2.1 ", "skipped"),
    ]
    (out / "demo" / "TESTS-Demo1.xml").write_text(_junit(cases))
    (out / "demo" / "TESTS-Demo2.xml").write_text(
        _junit([(D2, "[1] Inherits the feature tag", "skipped")])
    )
    return out


class TestUpstreamIgnored:
    def test_scenario_feature_outline_and_examples_tags(self, features_dir):
        ignored = S.upstream_ignored(features_dir)
        assert ignored == {
            f"{D1}::[2] Ignored upstream",
            f"{D1}::[4] Ignored outline -- @1.1",
            f"{D1}::[4] Ignored outline -- @1.2",
            f"{D1}::[5] Outline with one ignored examples block -- @2.1",
            f"{D2}::[1] Inherits the feature tag",
        }

    def test_tooling_tags_are_not_ignore(self, features_dir):
        ignored = S.upstream_ignored(features_dir)
        assert not any("[3]" in k for k in ignored)
        assert "skipGrammarCheck" not in S.IGNORE_TAGS
        assert "skipStyleCheck" not in S.IGNORE_TAGS


class TestNormaliseKey:
    def test_strips_path_prefix_and_trailing_space(self):
        k = S.normalise_key("features.clauses.match.Match1.Match1 - x", "[1] y -- @1.1 ")
        assert k == "Match1.Match1 - x::[1] y -- @1.1"

    def test_plain_classname_unchanged(self):
        assert S.normalise_key("Graph5.Graph5 - z", "[2] a") == "Graph5.Graph5 - z::[2] a"


class TestSummarise:
    def test_verdicts(self, run_dir, features_dir):
        v = S.collect(run_dir, features_dir)
        assert v[f"{D1}::[1] Plain"] == S.PASS
        assert v[f"{D1}::[2] Ignored upstream"] == S.IGNORED  # a pass does not count
        assert v[f"{D1}::[3] Grammar tooling tag only"] == S.FAIL
        assert v[f"{D1}::[4] Ignored outline -- @1.1"] == S.IGNORED
        assert v[f"{D2}::[1] Inherits the feature tag"] == S.IGNORED

    def test_skipped_without_ignore_tag_is_not_a_pass_and_stays_eligible(self, tmp_path, features_dir):
        out = tmp_path / "r"
        (out / "a").mkdir(parents=True)
        (out / "a" / "TESTS-Demo1.xml").write_text(_junit([(D1, "[1] Plain", "skipped")]))
        v = S.collect(out, features_dir)
        assert v[f"{D1}::[1] Plain"] == S.SKIPPED
        c = S.counts(v)
        assert (c.passed, c.eligible, c.ignored) == (0, 1, 0)

    def test_counts_and_line(self, run_dir, features_dir):
        c = S.counts(S.collect(run_dir, features_dir))
        # eligible: [1], [3], [5]@1.1 ; ignored: [2], [4]x2, [5]@2.1, Demo2[1]
        assert (c.passed, c.eligible, c.ignored, c.total) == (2, 3, 5, 8)
        assert S.summary_line(c) == "2 / 3 eligible (5 ignored upstream; 8 scenarios in total)"

    def test_without_features_nothing_is_ignored_but_skips_are_reported(self, run_dir):
        c = S.counts(S.collect(run_dir, None))
        assert c.ignored == 0
        assert c.skipped == 4


class TestTsv:
    def test_round_trip(self, tmp_path, run_dir, features_dir):
        v = S.collect(run_dir, features_dir)
        p = tmp_path / "r.tsv"
        S.write_tsv(v, p)
        lines = p.read_text().splitlines()
        keys = [line.split("\t", 1)[1] for line in lines]
        assert keys == sorted(keys)
        assert all(line.split("\t")[0] in {"1", "0", "S", "I"} for line in lines)
        assert S.read_tsv(p) == v

    def test_reads_legacy_two_column_file(self, tmp_path):
        p = tmp_path / "old.tsv"
        p.write_text("1\tA.A - a::[1] x -- @1.1 \n0\tA.A - a::[2] y\n")
        assert S.read_tsv(p) == {"A.A - a::[1] x -- @1.1": S.PASS, "A.A - a::[2] y": S.FAIL}


class TestDiff:
    def test_only_in_and_ignored_excluded(self):
        a = {"k1": S.PASS, "k2": S.PASS, "k3": S.IGNORED, "k4": S.FAIL}
        b = {"k1": S.PASS, "k2": S.FAIL, "k3": S.FAIL, "k4": S.PASS}
        d = S.diff(a, b)
        assert d.only_a == ["k2"]
        assert d.only_b == ["k4"]

    def test_ignore_set_applies_to_legacy_base(self):
        # A legacy base file marks an upstream-@ignore scenario as a pass;
        # the diff must not call it a regression when the new run skips it.
        a = {"k1": S.PASS, "k3": S.PASS}
        b = {"k1": S.PASS, "k3": S.IGNORED}
        assert S.diff(a, b, ignored={"k3"}).only_a == []


class TestHarnessSkipsIgnored:
    def test_ignore_tag_skips(self, monkeypatch):
        from tests.tck import environment as env

        monkeypatch.delenv("IVG_TCK_RUN_IGNORED", raising=False)
        assert env._upstream_ignored({"ignore", "skipStyleCheck"})
        assert not env._upstream_ignored({"skipStyleCheck", "skipGrammarCheck"})
        assert not env._upstream_ignored(set())

    def test_run_ignored_env_overrides(self, monkeypatch):
        from tests.tck import environment as env

        monkeypatch.setenv("IVG_TCK_RUN_IGNORED", "1")
        assert not env._upstream_ignored({"ignore"})

    def test_before_scenario_calls_skip(self, monkeypatch):
        from tests.tck import environment as env

        monkeypatch.delenv("IVG_TCK_RUN_IGNORED", raising=False)

        class Sc:
            effective_tags = {"ignore"}
            reason = None

            def skip(self, reason=None):
                self.reason = reason

        class Ctx:
            pass

        sc = Sc()
        env.before_scenario(Ctx(), sc)
        assert sc.reason and "@ignore" in sc.reason
