"""Spec 213 performance envelope (SC-002 soak, SC-004..SC-008, SC-011) — opt-in.

Run with:  .venv/bin/pytest tests/integration/test_ledger_perf.py -m perf -q -p no:warnings
Results are written to tests/benchmarks/results/ledger_<timestamp>.json.
"""

import json
import multiprocessing as mp
import os
import statistics
import time
from pathlib import Path

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import canonical_tables, make_engine
from tests.integration.test_ledger_us03_concurrency import _conn_params, _load_worker, _revisions

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.perf,
    pytest.mark.slow,
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
]

RESULTS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "results"
RESULTS: dict = {}


def _p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def _timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000.0


@pytest.fixture(scope="module", autouse=True)
def _write_results():
    yield
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"ledger_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(RESULTS, indent=2, sort_keys=True))
    print(f"\nledger perf results → {path}")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _node_ops(cs, prefix, n):
    for i in range(n):
        cs.create_node(f"{prefix}-{i}", labels=["P"], properties={"i": str(i)})


class TestCommitLatency:
    def test_sc004_commit_latency_and_overhead(self, engine):
        # 100-op changesets × 20
        d100 = []
        for r in range(20):
            cs = Changeset(actor="perf", actor_type="system")
            _node_ops(
                cs, f"c{r}", 100
            )  # 100 ops: 100 create_node (labels/props are attributes of the op)
            _, ms = _timed(lambda: engine.ledger.commit(cs))
            d100.append(ms)
        # 5,000-op changesets × 5
        d5k = []
        for r in range(5):
            cs = Changeset(actor="perf", actor_type="system")
            _node_ops(cs, f"k{r}", 5000)
            _, ms = _timed(lambda: engine.ledger.commit(cs))
            d5k.append(ms)
        # equivalent non-ledger transactional write of 100 nodes (+label +prop each) through the store
        dtx = []
        for r in range(20):
            stmts, params = [], []
            for i in range(100):
                nid = f"tx{r}-{i}"
                stmts.append("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)")
                params.append([nid])
                stmts.append("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)")
                params.append([nid, "P"])
                stmts.append('INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)')
                params.append([nid, "i", str(i)])
            _, ms = _timed(lambda: engine._store.execute_transaction(stmts, params))
            dtx.append(ms)
        RESULTS["sc004"] = {
            "commit100_p95_ms": _p95(d100),
            "commit100_median_ms": statistics.median(d100),
            "commit5000_p95_ms": _p95(d5k),
            "tx100_median_ms": statistics.median(dtx),
            "overhead_ratio_median": statistics.median(d100) / max(statistics.median(dtx), 1e-9),
        }
        assert _p95(d100) <= 500, RESULTS["sc004"]
        assert _p95(d5k) <= 10_000, RESULTS["sc004"]
        assert RESULTS["sc004"]["overhead_ratio_median"] <= 2.0, RESULTS["sc004"]

    def test_sc005_head_read(self, engine):
        ds = [_timed(engine.ledger.head)[1] for _ in range(100)]
        RESULTS["sc005"] = {"head_p95_ms": _p95(ds)}
        assert _p95(ds) <= 10, RESULTS["sc005"]


class TestReferenceGraph:
    """10k nodes / 50k relationships in 1,000 revisions; reconstruct, diff, verify."""

    @pytest.fixture
    def big(self, engine):
        nodes_per_rev, rels_per_rev = 10, 50
        ids = []
        for r in range(1000):
            cs = Changeset(actor="perf", actor_type="ingest")
            base = r * nodes_per_rev
            for i in range(nodes_per_rev):
                cs.create_node(f"n{base + i}", labels=["N"], properties={"r": str(r)})
            # 50 relationships among nodes created so far (ring within the batch + back-links)
            for j in range(rels_per_rev):
                s = f"n{base + (j % nodes_per_rev)}"
                o = f"n{max(0, base + (j % nodes_per_rev) - (j // nodes_per_rev) - 1)}"
                if s == o:
                    o = f"n{base + ((j + 1) % nodes_per_rev)}"
                cs.upsert_relationship(s, f"R{j % 5}", o, qualifiers={"w": str(j % 7)})
            ids.append(engine.ledger.commit(cs).revision)
        return engine, ids

    def test_sc006_sc007_sc008(self, big):
        engine, revs = big
        assert engine.ledger.head().seq == 1001
        t = {}
        for name, seq_idx in (("genesis", 0), ("mid", 499), ("head", 999)):
            rid = (
                revs[seq_idx].revision_id
                if name != "genesis"
                else engine.ledger.history(limit=1).revisions[0].revision_id
            )
            state, ms = _timed(lambda: engine.ledger.reconstruct(rid))
            t[f"reconstruct_{name}_ms"] = ms
            assert ms <= 30_000, t
        head_state = engine.ledger.reconstruct(revs[-1].revision_id)
        tables = canonical_tables(engine.conn)
        assert len(head_state.nodes) == len(tables["nodes"]) == 10_000
        assert {s.tuple for s in head_state.statements.values()} == set(tables["rels"])
        adj = [
            _timed(lambda: engine.ledger.diff(revs[i].revision_id, revs[i + 1].revision_id))[1]
            for i in range(500, 520)
        ]
        t["diff_adjacent_p95_ms"] = _p95(adj)
        assert _p95(adj) <= 100, t
        _, full = _timed(lambda: engine.ledger.diff(revs[0].revision_id, revs[-1].revision_id))
        t["diff_full_ms"] = full
        assert full <= 10_000, t
        report, vms = _timed(engine.ledger.verify)
        t["verify_ms"] = vms
        assert vms <= 60_000 and report.result == "equal", (t, report.classification)
        RESULTS["sc006_008"] = t

    def test_sc011_genesis_on_prepopulated_graph(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        nodes = [
            {"id": f"g{i}", "labels": ["G"], "properties": {"i": str(i)}} for i in range(10_000)
        ]
        eng.bulk_create_nodes(nodes)
        edges = [{"s": f"g{i}", "o": f"g{(i * 7 + 1) % 10_000}"} for i in range(50_000)]
        eng.bulk_ingest_edges(edges, predicate="R", auto_sync=False)
        genesis, ms = _timed(eng.ledger.enable)
        RESULTS["sc011"] = {"genesis_ms": ms, "op_count": genesis.op_count}
        assert ms <= 60_000, RESULTS["sc011"]
        assert eng.ledger.verify().result == "equal"


class TestSoak:
    def test_sc002_soak_ten_repetitions(self, iris_connection, ledger_reset):
        eng = make_engine(iris_connection)
        eng.ledger.enable()
        params = _conn_params(eng.conn)
        ctx = mp.get_context("spawn")
        total_ok = 0
        for rep in range(10):
            out = ctx.Queue()
            procs = [
                ctx.Process(target=_load_worker, args=(params, rep * 8 + i, 50, out))
                for i in range(8)
            ]
            for p in procs:
                p.start()
            results = [out.get(timeout=900) for _ in procs]
            for p in procs:
                p.join(timeout=60)
            total_ok += sum(r[1] for r in results)
            revs = _revisions(eng.conn)
            assert [r[0] for r in revs] == list(range(1, len(revs) + 1))
            for prev, cur in zip(revs, revs[1:]):
                assert cur[2] == prev[1]
        assert total_ok == 4000 and eng.ledger.head().seq == 4001
        RESULTS["sc002_soak"] = {"commits": total_ok, "head_seq": eng.ledger.head().seq}
