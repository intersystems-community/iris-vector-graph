"""Parsing the output of scripts/bench_migration_4_0_0.py into harness metrics (milliseconds)."""

from scripts.bench.alt import run_migration as rmig

SAMPLE = """seeded 20000 nodes, 20000 vectors (dim 128) in 1.7s
  step embeddings            6.6s
  step docs                  1.0s
  step edge_vectors          0.4s
  step kg_node_stores        7.8s
apply: 16.1s total for 20000 vectors (0.81 ms/vector)
  rekey                      3.53s         1 calls
refused: None
"""


def test_steps_and_total_become_milliseconds():
    m = rmig.parse(SAMPLE)
    assert m["step.embeddings"] == 6600.0 and m["step.kg_node_stores"] == 7800.0
    assert m["apply.total"] == 16100.0
    assert m["profile.rekey"] == 3530.0


def test_a_refused_migration_is_reported_not_silently_zero():
    m = rmig.parse(SAMPLE.replace("refused: None", "refused: blockers present"))
    assert m["refused"] == 1.0
