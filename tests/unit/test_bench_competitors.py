"""Unit tests for scripts/bench/competitors (no database, no IRIS).

The expected numbers are the ones IVG recorded in
tests/benchmarks/results/bench_20261010_153012.json for datasets S and M
(Q1 186 / 749; BFS distinct reach 550, 615, 621 / 4398, 5440, 5488).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMP = ROOT / "scripts" / "bench" / "competitors"
sys.path.insert(0, str(COMP))


@pytest.fixture(scope="module")
def ds():
    import datasets

    return datasets


@pytest.fixture(scope="module")
def graph_s(ds):
    return ds.get_graph("S")


@pytest.fixture(scope="module")
def graph_m(ds):
    return ds.get_graph("M")


# --- T001 -------------------------------------------------------------------


def test_rmat_s_matches_ivg_load(graph_s):
    assert len(graph_s.nodes) == 1000
    assert graph_s.nodes[0] == "node_0" and graph_s.nodes[-1] == "node_999"
    # 5,000 generated, 4,363 distinct: rdf_edges keeps one row per (s, p, o)
    assert len(graph_s.edges) == 4363
    assert len(set(graph_s.edges)) == 4363
    assert graph_s.rel_type == "R"
    assert graph_s.node_prop == "node_id"


def test_rmat_m_distinct_edges(graph_m):
    assert len(graph_m.nodes) == 10000
    assert len(graph_m.edges) == 47641


def test_rmat_generator_is_the_repos(ds):
    """The edge list is the one tests/benchmarks/graph_gen.py produces, in first-seen order."""
    sys.path.insert(0, str(ROOT / "tests" / "benchmarks"))
    from graph_gen import RMATGenerator

    raw = RMATGenerator(seed=42).generate_edges(1000, 5000)
    seen, expect = set(), []
    for s, o in raw:
        if (s, o) not in seen:
            seen.add((s, o))
            expect.append((f"node_{s}", f"node_{o}"))
    assert ds.get_graph("S").edges == expect


def test_seed_is_node_0_with_recorded_degree(ds, graph_s, graph_m):
    assert ds.highest_out_degree_seed(graph_s) == "node_0"
    assert ds.highest_out_degree_seed(graph_m) == "node_0"
    assert ds.out_degree(graph_s, "node_0") == 186
    assert ds.out_degree(graph_m, "node_0") == 749


def test_reach_counts_match_recorded_ivg_results(ds, graph_s, graph_m):
    assert ds.reach_counts(graph_s, "node_0", 4) == [186, 550, 615, 621]
    assert ds.reach_counts(graph_m, "node_0", 4) == [749, 4398, 5440, 5488]


def test_continued_stream_differs_from_fresh_for_m(ds, graph_m):
    """bench_193_full.py builds one generator for all datasets, so M follows S in the stream."""
    m193 = ds.get_graph("M193")
    assert len(m193.nodes) == 10000
    assert m193.edges != graph_m.edges
    # and S is the same either way
    assert ds.get_graph("S193").edges == ds.get_graph("S").edges


def test_l_dataset_shape(ds):
    g = ds.get_graph("L")
    assert len(g.nodes) == 100000
    assert 400000 < len(g.edges) <= 500000
    assert ds.highest_out_degree_seed(g) == "node_0"


# --- T002 -------------------------------------------------------------------


def test_degree_checksum_stable_and_sensitive(ds, graph_s):
    c1 = ds.degree_checksum(graph_s)
    assert c1 == ds.degree_checksum(ds.get_graph("S"))
    assert re.fullmatch(r"[0-9a-f]{64}", c1)
    out_deg, in_deg = ds.degrees(graph_s)
    assert sum(out_deg.values()) == len(graph_s.edges) == sum(in_deg.values())
    assert c1 == ds.checksum_from_degrees(graph_s.nodes, out_deg, in_deg)
    bumped = dict(out_deg)
    bumped["node_1"] = bumped.get("node_1", 0) + 1
    assert ds.checksum_from_degrees(graph_s.nodes, bumped, in_deg) != c1


# --- T003 -------------------------------------------------------------------


def _tiny(ds, edges):
    nodes = sorted({x for e in edges for x in e})
    return ds.Graph(name="tiny", nodes=nodes, edges=edges, label="Node", node_prop="node_id", rel_type="R")


def test_bfs_order_records_edges_into_seen_nodes(ds):
    g = _tiny(
        ds,
        [("a", "b"), ("a", "c"), ("b", "d"), ("c", "b"), ("c", "d"), ("d", "a"), ("d", "e")],
    )
    rows = ds.ivg_bfs_edges(g, "a", 3)
    assert rows == [
        ("a", "b", 1),
        ("a", "c", 1),
        ("b", "d", 2),
        ("c", "b", 2),
        ("c", "d", 2),
        ("d", "a", 3),
        ("d", "e", 3),
    ]


def test_sp_pair_takes_first_edge_of_max_step_even_into_seed(ds):
    g = _tiny(
        ds,
        [("a", "b"), ("a", "c"), ("b", "d"), ("c", "b"), ("c", "d"), ("d", "a"), ("d", "e")],
    )
    assert ds.ivg_sp_pair(g, "a") == ("a", "a")


def test_sp_pair_ascii_order_not_numeric(ds):
    # node_10 sorts before node_9 in ASCII, as it does in a global subscript
    g = _tiny(ds, [("node_0", "node_9"), ("node_0", "node_10"), ("node_10", "node_2"), ("node_9", "node_1")])
    rows = ds.ivg_bfs_edges(g, "node_0", 2)
    assert [(s, o) for s, o, _ in rows] == [
        ("node_0", "node_10"),
        ("node_0", "node_9"),
        ("node_10", "node_2"),
        ("node_9", "node_1"),
    ]


def test_sp_pair_for_s_is_deterministic(ds, graph_s):
    pair = ds.ivg_sp_pair(graph_s, "node_0")
    assert pair == ds.ivg_sp_pair(graph_s, "node_0")
    assert pair[0] == "node_0" and pair[1] in set(graph_s.nodes)


def test_second_ldbc_seed_list_is_the_three_engine_one(ds):
    assert ds.LDBC_SEEDS[0] == "Person:933"
    assert ds.LDBC_SEEDS[1] == "Person:36226"


# --- T004 -------------------------------------------------------------------


def test_record_schema_and_jsonl(tmp_path):
    import jsonl

    rec = jsonl.make_record("neo4j", "bench_q", "Q1", [1.5, 2.0], {"dataset": "S", "seed": "node_0"})
    assert set(rec) == {"engine", "suite", "query", "metric", "values_ms", "params"}
    assert rec["metric"] == "latency_ms"
    jsonl.validate_record(rec)
    out = tmp_path / "r.jsonl"
    jsonl.write_record(out, rec)
    jsonl.write_record(out, rec)
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["engine"] == "neo4j"
    with pytest.raises(ValueError):
        jsonl.validate_record({"engine": "x"})
    with pytest.raises(ValueError):
        jsonl.validate_record({**rec, "values_ms": ["fast"]})


def test_time_runs_counts_warmup_and_runs():
    import jsonl

    calls = []

    def fn():
        calls.append(1)
        return 7

    values, last = jsonl.time_runs(fn, warmup=3, runs=5)
    assert len(calls) == 8
    assert len(values) == 5 and last == 7
    assert all(isinstance(v, float) and v >= 0 for v in values)


def test_time_runs_error_is_reported_not_swallowed():
    import jsonl

    def boom():
        raise RuntimeError("timed out")

    values, last = jsonl.time_runs(boom, warmup=1, runs=2, catch=True)
    assert values == [] and isinstance(last, RuntimeError)
    with pytest.raises(RuntimeError):
        jsonl.time_runs(boom, warmup=0, runs=1)


# --- query plan --------------------------------------------------------------


def test_traversal_plan_has_q1_to_q6_and_bench193(ds, graph_s):
    import run_queries

    plan = run_queries.traversal_plan(graph_s, "neo4j")
    ids = [q.id for q in plan]
    for want in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "B193_h2", "B193_h3", "B193_h4"]:
        assert want in ids
    q1 = next(q for q in plan if q.id == "Q1")
    assert q1.params == {"id": "node_0"}
    assert "node_id" in q1.cypher and ":R" in q1.cypher
    assert q1.expect == 186
    q2 = next(q for q in plan if q.id == "Q2")
    assert q2.expect == 550


def test_memgraph_plan_adds_bfs_operator_variants(graph_s):
    import run_queries

    ids = [q.id for q in run_queries.traversal_plan(graph_s, "memgraph")]
    assert "Q2_bfs" in ids and "Q4_bfs" in ids
    assert "Q2_bfs" not in [q.id for q in run_queries.traversal_plan(graph_s, "neo4j")]


def test_ldbc_plan_ic2_ic3_ic13(ds):
    import run_queries

    g = ds.Graph(
        name="LDBC10",
        nodes=["Person:933", "Person:36226"],
        edges=[("Person:933", "Person:36226")],
        label="Person",
        node_prop="id",
        rel_type="KNOWS",
    )
    plan = run_queries.ldbc_plan(g, "neo4j")
    assert [q.id for q in plan] == ["IC2", "IC3", "IC13", "COUNT_friends"]
    assert "shortestPath" in plan[2].cypher


# --- T005 -------------------------------------------------------------------


def test_reference_leiden_labels_are_reported(ds):
    import refalgos

    g = ds.get_graph("karate")
    res = refalgos.run_reference(g, "louvain", "networkx", seed=42)
    assert res["implementation"] == "networkx.louvain_communities"
    assert 2 <= res["communities"] <= 6
    res_i = refalgos.run_reference(g, "leiden", "igraph", seed=42)
    assert res_i["implementation"].startswith("leidenalg")
    assert 2 <= res_i["communities"] <= 6


def test_reference_pagerank_agrees_between_networkx_and_igraph(ds, graph_s):
    import refalgos

    a = refalgos.run_reference(graph_s, "pagerank", "networkx", return_scores=True)
    b = refalgos.run_reference(graph_s, "pagerank", "igraph", return_scores=True)
    assert refalgos.pearson(a["scores"], b["scores"]) > 0.99


def test_reference_wcc_and_degree(ds, graph_s):
    import refalgos

    a = refalgos.run_reference(graph_s, "wcc", "networkx")
    b = refalgos.run_reference(graph_s, "wcc", "igraph")
    assert a["components"] == b["components"] >= 1
    d = refalgos.run_reference(graph_s, "degree_out", "igraph", return_scores=True)
    assert d["scores"]["node_0"] == 186


def test_fixtures_match_head_to_head(ds):
    import networkx as nx

    assert len(ds.get_graph("karate").edges) == nx.karate_club_graph().number_of_edges() == 78
    er = ds.get_graph("er500")
    assert len(er.nodes) == 500
    assert len(er.edges) == nx.gnp_random_graph(500, 0.02, seed=42).number_of_edges()
    er2 = ds.get_graph("er2000")
    assert len(er2.nodes) == 2000
    btw = ds.get_graph("btw500")
    assert len(btw.edges) == nx.erdos_renyi_graph(500, 0.01, seed=42, directed=False).number_of_edges()


# --- T006 -------------------------------------------------------------------


def test_compose_declares_ports_and_password():
    text = (COMP / "docker-compose.yml").read_text()
    assert "7688" in text and "7689" in text
    assert "ivgbenchpw" in text
    assert "neo4j:5.24-community" in text
    assert "memgraph/memgraph" in text
    assert "server_memory_heap_max__size" in text and "server_memory_pagecache_size" in text


def test_helper_script_has_commands():
    text = (COMP / "competitors.sh").read_text()
    for cmd in ["start)", "stop)", "status)"]:
        assert cmd in text
    assert "docker compose" in text
    # only the explicit reset branch may delete anything
    before_reset = text.split("\n  reset)")[0]
    assert "docker rm" not in before_reset and "volume rm" not in before_reset
    assert "--yes" in text.split("\n  reset)")[1]


def test_default_credentials_match_head_to_head():
    import engines

    assert engines.NEO4J_URI_DEFAULT == "bolt://localhost:7688"
    assert engines.NEO4J_AUTH_DEFAULT == ("neo4j", "ivgbenchpw")
    assert engines.MEMGRAPH_URI_DEFAULT == "bolt://localhost:7689"


# --- helpers added with the runners ------------------------------------------


def test_sp_pair_distinct_skips_the_seed(ds, graph_s):
    seed, dst = ds.ivg_sp_pair(graph_s, "node_0")
    assert dst == "node_0"  # the IVG rule lands on the seed for S
    _, dst2 = ds.ivg_sp_pair_distinct(graph_s, "node_0")
    assert dst2 not in (None, "node_0")
    assert ds.directed_distance(graph_s, "node_0", dst2, 10) is not None


def test_reach_counts_include_seed_flag(ds, graph_s):
    with_seed = ds.reach_counts(graph_s, "node_0", 4)
    without = ds.reach_counts(graph_s, "node_0", 4, include_seed=False)
    # node_0 has a self loop (depth 1) and lies on cycles
    assert [a - b for a, b in zip(with_seed, without)] == [1, 1, 1, 1]


def test_distances(ds):
    g = _tiny(ds, [("a", "b"), ("b", "c"), ("d", "c")])
    assert ds.directed_distance(g, "a", "c") == 2
    assert ds.directed_distance(g, "a", "d") is None
    assert ds.undirected_distance(g, "a", "d") == 3
    assert ds.undirected_distance(g, "a", "a") == 0


def test_memgraph_legs_skip_when_procedure_missing(graph_s):
    import run_algos

    legs = run_algos.memgraph_legs(graph_s, procs=[])
    assert legs["pagerank"][0] is None and "skipped" in legs["pagerank"][2]
    assert legs["degree_out"][0] is not None  # plain Cypher needs no procedure
    legs = run_algos.memgraph_legs(graph_s, procs=["nxalg.pagerank"])
    assert "nxalg.pagerank" in legs["pagerank"][0]
    assert legs["louvain"][0] is None


def test_gds_legs_guard_exact_algorithms(ds, graph_s):
    import run_algos

    legs = run_algos.gds_legs(graph_s, "nat", "und")
    for algo in ["pagerank", "degree_out", "degree_both", "wcc", "scc", "betweenness_sampled200", "louvain", "leiden"]:
        assert algo in legs
    assert "samplingSeed: 42" in legs["betweenness_sampled200"][0]
    assert "randomSeed: 42" in legs["leiden"][0]
    assert "betweenness_exact" in legs
    small = ds.Graph(name="x", nodes=[str(i) for i in range(run_algos.GDS_EXACT_MAX_NODES + 1)], edges=[])
    assert "betweenness_exact" not in run_algos.gds_legs(small, "nat", "und")


def test_reference_guards_exact_algorithms(ds, graph_s):
    import refalgos

    ref = refalgos.Reference(graph_s, "networkx", exact_max_nodes=10)
    legs = ref.legs()
    assert legs["betweenness_exact"][0] is None and "skipped" in legs["betweenness_exact"][1]
    assert legs["betweenness_sampled200"][0] is not None


def test_memgraph_nx_legs_capped_by_edge_count(graph_s):
    import run_algos

    legs = run_algos.memgraph_legs(graph_s, procs=["nxalg.pagerank", "wcc.get_components"], nx_max_edges=10)
    assert legs["pagerank"][0] is None and "--memgraph-nx-max-edges" in legs["pagerank"][2]["skipped"]
    assert legs["wcc"][0] is None  # wcc.get_components is a Python module too
    uncapped = run_algos.memgraph_legs(graph_s, procs=["nxalg.pagerank", "wcc.get_components"])
    assert uncapped["pagerank"][0] is not None and uncapped["wcc"][0] is not None
    assert uncapped["degree_out"][0] is not None


def test_reference_heavy_algorithms_capped_by_edge_count(graph_s):
    import refalgos

    legs = refalgos.Reference(graph_s, "igraph", heavy_max_edges=100).legs()
    for algo in ("triangle_count", "louvain", "leiden"):
        assert legs[algo][0] is None and "--ref-heavy-max-edges" in legs[algo][1]["skipped"]
    assert legs["pagerank"][0] is not None


MAGE_PROCS = [
    "pagerank.get",
    "betweenness_centrality.get",
    "weakly_connected_components.get",
    "community_detection.get",
    "leiden_community_detection.get",
]


def test_memgraph_legs_use_the_native_mage_procedures_when_the_server_has_them(graph_s):
    import run_algos

    """The MAGE image adds native PageRank, betweenness, weakly-connected-components, Louvain-style and Leiden procedures.
    They replace the networkx-inside-the-server legs and the 'needs the MAGE image' skips, and are not size-capped."""
    legs = run_algos.memgraph_legs(graph_s, procs=MAGE_PROCS + ["nxalg.pagerank"], nx_max_edges=1)
    for algo, proc in (
        ("pagerank", "pagerank.get"),
        ("betweenness_exact", "betweenness_centrality.get"),
        ("wcc", "weakly_connected_components.get"),
        ("louvain", "community_detection.get"),
        ("leiden", "leiden_community_detection.get"),
    ):
        cypher, summ, params = legs[algo]
        assert cypher is not None and proc in cypher, (algo, cypher)
        assert "MAGE" in params["implementation"]
    # closeness and triangle counting still have no Memgraph procedure
    assert legs["closeness_harmonic"][0] is None and legs["triangle_count"][0] is None


def test_memgraph_legs_without_mage_are_unchanged(graph_s):
    import run_algos

    legs = run_algos.memgraph_legs(graph_s, procs=["nxalg.pagerank"])
    assert "nxalg.pagerank" in legs["pagerank"][0]
    assert legs["louvain"][0] is None and legs["leiden"][0] is None
