"""KGBENCH-scale temporal ingest and query runner: the side table, the TSV reader and the ingest loop (no IRIS)."""

import json

import pytest

from scripts.bench.alt import kgbench as kg
from scripts.bench.alt import sides as sd


def test_kg_sides_use_their_own_namespaces_and_trees():
    sides = kg.kg_sides()
    assert sides["A"].namespace == "KGBENCHA" and sides["B"].namespace == "KGBENCHB"
    assert sides["A"].tree == sd.SIDES["A"].tree and sides["B"].ref == sd.SIDES["B"].ref
    for side in sides.values():
        assert sd.check_namespace(side.namespace) == side.namespace  # inside the harness's own namespaces


def test_read_edges_parses_five_columns_and_skips_bad_lines(tmp_path):
    f = tmp_path / "t.tsv"
    f.write_text("100\ta\tCALLS_AT\tb\t1.5\nbroken\n200\tc\tCALLS_AT\td\tx\n300\te\tCALLS_AT\tf\t2\n")
    edges = list(kg.read_edges(f))
    assert edges == [{"ts": 100, "s": "a", "p": "CALLS_AT", "o": "b", "w": 1.5}, {"ts": 300, "s": "e", "p": "CALLS_AT", "o": "f", "w": 2.0}]


def test_read_edges_honours_limit_and_skip(tmp_path):
    f = tmp_path / "t.tsv"
    f.write_text("".join(f"{i}\ts{i}\tP\to{i}\t1\n" for i in range(10)))
    assert [e["ts"] for e in kg.read_edges(f, skip=3, limit=4)] == [3, 4, 5, 6]


class FakeEngine:
    def __init__(self):
        self.batches = []

    def bulk_create_edges_temporal(self, batch):
        self.batches.append(list(batch))


def test_ingest_batches_edges_and_records_progress(tmp_path):
    f = tmp_path / "t.tsv"
    f.write_text("".join(f"{i}\ts{i % 3}\tP\to{i}\t1\n" for i in range(25)))
    eng = FakeEngine()
    ticks = iter(range(1000))
    progress = kg.ingest(eng, [f], batch=10, report_every=10, clock=lambda: float(next(ticks)))
    assert [len(b) for b in eng.batches] == [10, 10, 5]
    assert progress[-1]["edges"] == 25
    assert all(p["edges_per_s"] > 0 for p in progress)


def test_parse_bench_full_output_collects_res_lines():
    text = "noise\nRES window.5min 0.0850\nRES agg.count.1hr 0.0120\nRES bad notanumber\n  not a RES line 1.0\n"
    assert kg.parse_bench_full(text) == {"window.5min": pytest.approx(0.085), "agg.count.1hr": pytest.approx(0.012)}


def test_journal_script_touches_only_the_namespaces_own_database_and_refuses_others():
    script = kg.journal_off_script("KGBENCHA")
    assert "/usr/irissys/mgr/kgbencha/" in script and "GlobalJournalState=2" in script
    with pytest.raises(ValueError):
        kg.journal_off_script("USER")


def test_chunk_plan_covers_every_row_once_in_file_order():
    from scripts.bench.alt import kg_worker as w

    plan = w.chunk_plan(100_000_000)
    assert sum(n for _, _, n in plan) == 535_116_500 == sum(w.FILE_ROWS)
    assert [i for i, _, _ in plan] == sorted(i for i, _, _ in plan)
    for fi, rows in enumerate(w.FILE_ROWS):  # contiguous, no gaps, within the file
        parts = [(s, n) for i, s, n in plan if i == fi]
        assert parts[0][0] == 0 and sum(n for _, n in parts) == rows
        assert all(parts[k][0] + parts[k][1] == parts[k + 1][0] for k in range(len(parts) - 1))


def test_chunk_plan_total_limit():
    from scripts.bench.alt import kg_worker as w

    plan = w.chunk_plan(10_000_000, total_limit=25_000_000)
    assert [n for _, _, n in plan] == [10_000_000, 10_000_000, 5_000_000]
