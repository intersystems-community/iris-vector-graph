# Alternative vs baseline: launch-4

- side A: ref `6f17be0f` commit `6f17be0f6eab175038585494c31ae39c82e5c9e1`

Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; faster: B/A < 0.90 outside the spread; otherwise slower.

## integration-ledger-perf-full

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| ledger.sc004.commit100_median_ms | 21.1 | 21.1 | n/a | n/a | n/a | n/a |
| ledger.sc004.commit100_p95_ms | 22.9 | 22.9 | n/a | n/a | n/a | n/a |
| ledger.sc004.commit5000_p95_ms | 5602 | 5602 | n/a | n/a | n/a | n/a |
| ledger.sc004.tx100_median_ms | 50.2 | 50.2 | n/a | n/a | n/a | n/a |
| ledger.sc005.head_p95_ms | 0.202 | 0.202 | n/a | n/a | n/a | n/a |
| ledger.sc006_008.diff_adjacent_p95_ms | 3.32 | 3.32 | n/a | n/a | n/a | n/a |
| ledger.sc006_008.diff_full_ms | 1552 | 1552 | n/a | n/a | n/a | n/a |
| ledger.sc006_008.reconstruct_genesis_ms | 13.6 | 13.6 | n/a | n/a | n/a | n/a |
| ledger.sc006_008.reconstruct_head_ms | 1315 | 1315 | n/a | n/a | n/a | n/a |
| ledger.sc006_008.reconstruct_mid_ms | 662 | 662 | n/a | n/a | n/a | n/a |
| ledger.sc006_008.verify_ms | 8874 | 8874 | n/a | n/a | n/a | n/a |
| ledger.sc011.genesis_ms | 615 | 615 | n/a | n/a | n/a | n/a |
| tests/integration/test_ledger_perf.py::TestCommitLatency::test_sc004_commit_latency_and_overhead | 11460 | 11460 | n/a | n/a | n/a | n/a |
| tests/integration/test_ledger_perf.py::TestCommitLatency::test_sc005_head_read | 20.0 | 20.0 | n/a | n/a | n/a | n/a |
| tests/integration/test_ledger_perf.py::TestReferenceGraph::test_sc006_sc007_sc008 | 14350 | 14350 | n/a | n/a | n/a | n/a |
| tests/integration/test_ledger_perf.py::TestReferenceGraph::test_sc011_genesis_on_prepopulated_graph | 49550 | 49550 | n/a | n/a | n/a | n/a |
| tests/integration/test_ledger_perf.py::TestSoak::test_sc002_soak_ten_repetitions | 12470 | 12470 | n/a | n/a | n/a | n/a |
| wall_total | 132426 | 132426 | n/a | n/a | n/a | n/a |

## integration-nodepk-advanced

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/integration/test_nodepk_advanced_benchmarks.py::TestAdvancedQueryPatterns::test_complex_join_all_tables | 50.0 | 50.0 | n/a | n/a | n/a | n/a |
| tests/integration/test_nodepk_advanced_benchmarks.py::TestAdvancedQueryPatterns::test_concurrent_query_throughput | 2280 | 2280 | n/a | n/a | n/a | n/a |
| tests/integration/test_nodepk_advanced_benchmarks.py::TestAdvancedQueryPatterns::test_graph_traversal_with_node_validation | 90.0 | 90.0 | n/a | n/a | n/a | n/a |
| tests/integration/test_nodepk_advanced_benchmarks.py::TestAdvancedQueryPatterns::test_label_based_filtering_at_scale | 110 | 110 | n/a | n/a | n/a | n/a |
| tests/integration/test_nodepk_advanced_benchmarks.py::TestAdvancedQueryPatterns::test_vector_knn_search_with_fks | 10.0 | 10.0 | n/a | n/a | n/a | n/a |
| wall_total | 5948 | 5948 | n/a | n/a | n/a | n/a |

## perf-betweenness-full

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| ?.IVG BetweennessGlobal | 1074 | 1074 | n/a | n/a | n/a | n/a |
| tests/perf/test_betweenness_vs_gds.py::TestBetweennessVsGDS::test_betweenness_er2000 | 13310 | 13310 | n/a | n/a | n/a | n/a |
| tests/perf/test_betweenness_vs_gds.py::TestBetweennessVsGDS::test_betweenness_er500 | 3550 | 3550 | n/a | n/a | n/a | n/a |
| tests/perf/test_betweenness_vs_gds.py::TestBetweennessVsGDS::test_betweenness_full_suite | 21090 | 21090 | n/a | n/a | n/a | n/a |
| tests/perf/test_betweenness_vs_gds.py::TestBetweennessVsGDS::test_betweenness_karate | 680 | 680 | n/a | n/a | n/a | n/a |
| wall_total | 52114 | 52114 | n/a | n/a | n/a | n/a |

## perf-leiden-four-way

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| ?.ivg.mean | 144 | 144 | n/a | n/a | n/a | n/a |
| tests/perf/test_leiden_four_way.py::test_leiden_four_way_benchmark[erdos_renyi_n2000_p0.005-<lambda>-1.0] | 14160 | 14160 | n/a | n/a | n/a | n/a |
| tests/perf/test_leiden_four_way.py::test_leiden_four_way_benchmark[erdos_renyi_n500_p0.02-<lambda>-1.0] | 4650 | 4650 | n/a | n/a | n/a | n/a |
| tests/perf/test_leiden_four_way.py::test_leiden_four_way_benchmark[karate_club-_build_karate-1.0] | 4610 | 4610 | n/a | n/a | n/a | n/a |
| wall_total | 107892 | 107892 | n/a | n/a | n/a | n/a |

## temporal-preagg

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| wall_total | 150466 | 150466 | n/a | n/a | n/a | n/a |

## Failed or incomplete reps

- e2e-communities-perf side A rep 1: failed (no metrics)
- integration-ledger-perf-full side A rep 1: tests-failed (metrics kept): tests.integration.test_ledger_perf.TestReferenceGraph::test_sc011_genesis_on_prepopulated_graph

## Totals

parity: 0, faster: 0, slower: 0, n/a: 36
