"""Spec 235: the budget check in scripts/fhir/bench_235.py (SC-005, SC-006, SC-007). No IRIS."""

from __future__ import annotations

import json

import pytest

from scripts.fhir import bench_235 as bench


def _run(set_="scale", **over):
    run = {
        "label": "x",
        "set": set_,
        "commit": "abc",
        "sync_ms": 1000.0,
        "ppr_ms": 100.0,
        "ppr_group_ms": 110.0,
        "coverage_ms": 500.0,
        "gaps_ms": 500.0,
    }
    run.update(over)
    return run


def _baseline(set_="scale"):
    return _run(set_, ppr_group_ms=None, coverage_ms=None, gaps_ms=None)


class TestCompare:
    def test_sync_at_15_percent_passes(self):
        assert bench.compare(_baseline(), _run(sync_ms=1150.0)) == []

    def test_sync_over_15_percent_fails(self):
        breaches = bench.compare(_baseline(), _run(sync_ms=1151.0))
        assert len(breaches) == 1 and "sync" in breaches[0]

    def test_group_by_against_baseline_default_ppr(self):
        assert bench.compare(_baseline(), _run(ppr_ms=500.0, ppr_group_ms=120.0)) == []
        breaches = bench.compare(_baseline(), _run(ppr_group_ms=120.1))
        assert len(breaches) == 1 and "group" in breaches[0]

    @pytest.mark.parametrize("key", ["coverage_ms", "gaps_ms"])
    def test_report_at_two_seconds_fails(self, key):
        assert bench.compare(_baseline(), _run(**{key: 1999.9})) == []
        breaches = bench.compare(_baseline(), _run(**{key: 2000.0}))
        assert len(breaches) == 1 and key.split("_")[0] in breaches[0]

    @pytest.mark.parametrize("key", ["sync_ms", "ppr_group_ms", "coverage_ms", "gaps_ms"])
    def test_missing_current_measurement_fails(self, key):
        cur = _run()
        del cur[key]
        breaches = bench.compare(_baseline(), cur)
        assert any("missing" in b and key in b for b in breaches)

    @pytest.mark.parametrize("key", ["sync_ms", "ppr_ms"])
    def test_missing_baseline_measurement_fails(self, key):
        base = _baseline()
        base[key] = None
        assert any("missing" in b and key in b for b in bench.compare(base, _run()))

    def test_set_mismatch_fails(self):
        assert any("set" in b for b in bench.compare(_baseline("fixture"), _run("scale")))

    def test_fixture_set_checks_sync_only(self):
        # Group-by and report budgets are defined at scale; the fixture set is too small to time them fairly.
        cur = _run("fixture", ppr_group_ms=10_000.0, coverage_ms=10_000.0, gaps_ms=10_000.0)
        assert bench.compare(_baseline("fixture"), cur) == []
        assert bench.compare(_baseline("fixture"), _run("fixture", sync_ms=2000.0))


class TestCompareCli:
    def _write(self, tmp_path, name, data):
        p = tmp_path / name
        p.write_text(json.dumps(data))
        return str(p)

    def test_exit_zero_within_budget(self, tmp_path, capsys):
        b = self._write(tmp_path, "b.json", {"fixture": _baseline("fixture"), "scale": _baseline()})
        c = self._write(tmp_path, "c.json", {"fixture": _run("fixture"), "scale": _run()})
        assert bench.main(["compare", b, c]) == 0

    def test_exit_one_and_breaches_printed(self, tmp_path, capsys):
        b = self._write(tmp_path, "b.json", {"scale": _baseline()})
        c = self._write(tmp_path, "c.json", {"scale": _run(sync_ms=5000.0, gaps_ms=2500.0)})
        assert bench.main(["compare", b, c]) == 1
        out = capsys.readouterr().out
        assert "sync" in out and "gaps" in out

    def test_single_run_files(self, tmp_path):
        b = self._write(tmp_path, "b.json", _baseline())
        c = self._write(tmp_path, "c.json", _run())
        assert bench.main(["compare", b, c]) == 0

    def test_set_missing_from_current_fails(self, tmp_path):
        b = self._write(tmp_path, "b.json", {"fixture": _baseline("fixture"), "scale": _baseline()})
        c = self._write(tmp_path, "c.json", {"scale": _run()})
        assert bench.main(["compare", b, c]) == 1
