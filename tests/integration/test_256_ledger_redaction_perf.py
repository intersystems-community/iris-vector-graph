"""Spec 256 SC-002 and SC-003, opt in with ``-m perf``.

SC-002: 10,000 revisions over 10 tenants, 5% of them spanning two tenants. After erasing and
redacting one tenant, verify() is equal and every other tenant's records and revision rows are
byte-identical (SC-004).

SC-003: a tenant of 100,000 records is redacted in under 60 s, and no batch holds the head lock
for a second or more. ``IVG_256_SC003_RECORDS`` scales it down for a quick look.
"""

from __future__ import annotations

import os
import random
import time

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import make_engine
from tests.integration.test_256_ledger_redaction_e2e import all_records, records_of, revision_row

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true"),
    pytest.mark.perf,
    pytest.mark.requires_database,
    pytest.mark.integration,
]

TENANTS = [f"tnt{i:02d}" for i in range(10)]


def _graph(t):
    return f"g_{t}"


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def test_sc002_verify_is_equal_after_redacting_one_of_ten_tenants(engine, capsys):
    rnd = random.Random(256)
    led = engine.ledger
    n_revisions = 10_000
    t0 = time.perf_counter()
    # seed every tenant with two nodes so later revisions can relate them
    for t in TENANTS:
        cs = Changeset(actor="ingest:etl", actor_type="ingest", message=f"{t} seed", correlation_id=f"{t}|seed")
        cs.create_node(f"{t}|n0", properties={"v": f"{t}-v0"})
        cs.create_node(f"{t}|n1", properties={"v": f"{t}-v1"})
        cs.create_relationship(f"{t}|n0", "R", f"{t}|n1", graph=_graph(t))
        led.commit(cs)
    for i in range(n_revisions - len(TENANTS)):
        span = rnd.random() < 0.05
        ts = rnd.sample(TENANTS, 2) if span else [rnd.choice(TENANTS)]
        cs = Changeset(actor="ingest:etl", actor_type="ingest", message=f"rev {i} " + " ".join(ts), idempotency_key=f"{'-'.join(ts)}-{i}")
        for t in ts:
            cs.upsert_node(f"{t}|n{i + 2}", properties={"v": f"{t}-val-{i}"})
            cs.create_relationship(f"{t}|n{i + 2}", "R", f"{t}|n0", qualifiers={"w": str(i)}, graph=_graph(t))
        led.commit(cs)
    build_s = time.perf_counter() - t0

    victim = TENANTS[3]
    before = all_records(engine)
    others = {k: v for k, v in before.items() if victim not in v}
    other_seqs = sorted({k[0] for k in records_of(before, [t for t in TENANTS if t != victim])} - {k[0] for k in records_of(before, [victim])})
    rows_before = {s: revision_row(engine, s) for s in other_seqs}

    engine.erase_graph(_graph(victim))
    engine.delete_nodes_by_prefix(f"{victim}|")
    t1 = time.perf_counter()
    g = led.redact(_graph(victim))
    p = led.redact(None, prefix=victim)
    redact_s = time.perf_counter() - t1
    t2 = time.perf_counter()
    report = led.verify()
    verify_s = time.perf_counter() - t2
    assert report.result == "equal", report.differences[:5]

    after = all_records(engine)
    assert {k: after[k] for k in others} == others
    assert {s: revision_row(engine, s) for s in other_seqs} == rows_before
    with capsys.disabled():
        print(
            f"\nSC-002: {n_revisions} revisions built in {build_s:.1f}s; redacted {g.records_tombstoned + p.records_tombstoned} "
            f"records in {len(set(g.revision_seqs) | set(p.revision_seqs))} revisions in {redact_s:.2f}s "
            f"(max batch {max(g.max_batch_ms, p.max_batch_ms)} ms); verify {verify_s:.1f}s equal"
        )


def test_sc003_a_tenant_of_100k_records_redacts_in_under_a_minute_with_short_batches(engine, capsys):
    records = int(os.environ.get("IVG_256_SC003_RECORDS", "100000"))
    per_node = 4  # create_node, add_label, two set_prop
    nodes = records // per_node
    per_commit = 2_500
    led = engine.ledger
    t0 = time.perf_counter()
    for start in range(0, nodes, per_commit):
        cs = Changeset(actor="ingest:etl", actor_type="ingest", message="big tenant load", correlation_id=f"big|{start}")
        for i in range(start, min(nodes, start + per_commit)):
            cs.create_node(f"big|n{i}", labels=["BigLabel"], properties={"a": f"big-a-{i}", "b": f"big-b-{i}"})
        led.commit(cs)
    # another tenant, so the scan walks records it must leave alone
    cs = Changeset(actor="ingest:etl", actor_type="ingest", message="small tenant")
    for i in range(1_000):
        cs.create_node(f"small|n{i}", properties={"a": f"small-a-{i}"})
    led.commit(cs)
    build_s = time.perf_counter() - t0

    engine.delete_nodes_by_prefix("big|")
    t1 = time.perf_counter()
    r = led.redact(None, prefix="big")
    redact_s = time.perf_counter() - t1
    assert r.complete and r.records_tombstoned == nodes * per_node
    assert led.verify().result == "equal"
    with capsys.disabled():
        print(
            f"\nSC-003: {r.records_tombstoned} records ({build_s:.1f}s to build) redacted in {redact_s:.2f}s, "
            f"{r.batches} batches, longest {r.max_batch_ms} ms, {r.revisions_touched} revisions"
        )
    assert redact_s < 60
    assert r.max_batch_ms < 1000
