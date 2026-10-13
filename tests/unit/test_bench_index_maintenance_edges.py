"""benchmarks/bench_index_maintenance_strategy.py must generate distinct edges.

Its generator used to make source and target both functions of ``i % n_nodes``, so every lap of the node count repeated the same
pairs. One big ``executemany`` then spent most of its time on rejected duplicate rows (about 40 ms each) and never finished.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "bench_index_maintenance_strategy.py"


def load():
    spec = importlib.util.spec_from_file_location("bench_idx_maint", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_edges_are_distinct_even_when_the_edge_count_exceeds_the_node_count_many_times():
    mod = load()
    for n_edges in (20_000, 100_000):
        n_nodes = max(1000, n_edges // 10)
        edges = mod._edges("p", n_nodes, n_edges)
        assert len(edges) == n_edges
        assert len(set(edges)) == n_edges, "duplicate triples would be rejected row by row at 40 ms each"


def test_edges_use_only_seeded_nodes_and_never_self_loop():
    mod = load()
    n_nodes = 1000
    edges = mod._edges("p", n_nodes, 20_000)
    names = {f"p_n{i}" for i in range(n_nodes)}
    assert all(s in names and o in names and s != o for s, _, o in edges)
