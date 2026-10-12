# Alternative vs baseline: smoke-3

- side A: ref `6f17be0f` commit `6f17be0f6eab175038585494c31ae39c82e5c9e1`
- side B: ref `alt-integration` commit `f754f13551fe84a15de4584225978096eb4aec50`

Times in milliseconds. B/A is the ratio of medians. parity: B/A <= 1.10 or B's median inside A's rep spread; faster: B/A < 0.90 outside the spread; otherwise slower.

## bench-q1q6-s

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| S.Q1.ivg-os.hot_p50_ms | 0.47 | 0.47 | 0.54 | 0.54 | 1.15 | slower |
| S.Q1.ivg-os.hot_p90_ms | 0.57 | 0.57 | 0.83 | 0.83 | 1.46 | slower |
| S.Q2.ivg-arno.hot_p50_ms | 9.23 | 9.23 | 9.42 | 9.42 | 1.02 | parity |
| S.Q2.ivg-arno.hot_p90_ms | 9.41 | 9.41 | 9.98 | 9.98 | 1.06 | parity |
| S.Q2.ivg-os.hot_p50_ms | 7.97 | 7.97 | 8.08 | 8.08 | 1.01 | parity |
| S.Q2.ivg-os.hot_p90_ms | 8.82 | 8.82 | 9.91 | 9.91 | 1.12 | slower |
| S.Q3.ivg-arno.hot_p50_ms | 12.5 | 12.5 | 13.1 | 13.1 | 1.05 | parity |
| S.Q3.ivg-arno.hot_p90_ms | 13.6 | 13.6 | 17.1 | 17.1 | 1.26 | slower |
| S.Q3.ivg-os.hot_p50_ms | 11.3 | 11.3 | 11.5 | 11.5 | 1.02 | parity |
| S.Q3.ivg-os.hot_p90_ms | 11.7 | 11.7 | 12.6 | 12.6 | 1.07 | parity |
| S.Q4.ivg-arno.hot_p50_ms | 13.3 | 13.3 | 13.3 | 13.3 | 1 | parity |
| S.Q4.ivg-arno.hot_p90_ms | 14.2 | 14.2 | 13.9 | 13.9 | 0.975 | parity |
| S.Q4.ivg-os.hot_p50_ms | 11.7 | 11.7 | 11.6 | 11.6 | 0.992 | parity |
| S.Q4.ivg-os.hot_p90_ms | 12.6 | 12.6 | 13.4 | 13.4 | 1.07 | parity |
| S.Q5.ivg-os.hot_p50_ms | 0.28 | 0.28 | 0.38 | 0.38 | 1.36 | slower |
| S.Q5.ivg-os.hot_p90_ms | 0.32 | 0.32 | 0.44 | 0.44 | 1.38 | slower |
| S.Q6.ivg-os.hot_p50_ms | 0.23 | 0.23 | 0.2 | 0.2 | 0.87 | faster |
| S.Q6.ivg-os.hot_p90_ms | 0.26 | 0.26 | 0.23 | 0.23 | 0.885 | faster |
| wall_total | 2661 | 2661 | 5049 | 5049 | 1.9 | slower |

## cypher-parser-bench

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| q1.avg | 0.0009 | 0.0009 | 0.0013 | 0.0013 | 1.44 | slower |
| q1.p95 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 1 | parity |
| q2.avg | 0.0008 | 0.0008 | 0.0008 | 0.0008 | 1 | parity |
| q2.p95 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 1 | parity |
| q3.avg | 0.0007 | 0.0007 | 0.0007 | 0.0007 | 1 | parity |
| q3.p95 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 1 | parity |
| q4.avg | 0.001 | 0.001 | 0.001 | 0.001 | 1 | parity |
| q4.p95 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 1 | parity |
| q5.avg | 0.0007 | 0.0007 | 0.0007 | 0.0007 | 1 | parity |
| q5.p95 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 1 | parity |
| q6.avg | 0.0007 | 0.0007 | 0.0007 | 0.0007 | 1 | parity |
| q6.p95 | 0.0001 | 0.0001 | 0.0001 | 0.0001 | 1 | parity |
| wall_total | 578 | 578 | 616 | 616 | 1.07 | parity |

## e2e-227-scale

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| wall_total | 47861 | 47861 | 44590 | 44590 | 0.932 | parity |

## e2e-gates-analytics

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/e2e/test_analytics_dispatch.py::TestBetweennessDispatch::test_arno_sideffect_does_not_send_to_lazy_kg | 1540 | 1540 | 2010 | 2010 | 1.31 | slower |
| tests/e2e/test_analytics_dispatch.py::TestBetweennessDispatch::test_lazykg_fires_when_os_brandes_raises | 430 | 430 | 400 | 400 | 0.93 | parity |
| tests/e2e/test_analytics_dispatch.py::TestBetweennessDispatch::test_objectscript_brandes_fires_by_default | 1810 | 1810 | 1930 | 1930 | 1.07 | parity |
| tests/e2e/test_analytics_dispatch.py::TestBetweennessDispatch::test_results_identical_across_tiers | 380 | 380 | 370 | 370 | 0.974 | parity |
| tests/e2e/test_analytics_dispatch.py::TestClosenessDispatch::test_all_tiers_produce_same_ranking | 840 | 840 | 790 | 790 | 0.94 | parity |
| tests/e2e/test_analytics_dispatch.py::TestClosenessDispatch::test_igraph_tier_fires_when_available | 2760 | 2760 | 2650 | 2650 | 0.96 | parity |
| tests/e2e/test_analytics_dispatch.py::TestClosenessDispatch::test_msbfs_tier_fires_when_igraph_serverside_patched_out | 2230 | 2230 | 2690 | 2690 | 1.21 | slower |
| tests/e2e/test_analytics_dispatch.py::TestClosenessDispatch::test_sequential_fallback_fires_when_both_fast_tiers_patched_out | 240 | 240 | 480 | 480 | 2 | slower |
| tests/e2e/test_analytics_dispatch.py::TestCrossAlgorithmConsistency::test_all_analytics_produce_non_empty_on_same_graph | 2100 | 2100 | 2520 | 2520 | 1.2 | slower |
| tests/e2e/test_analytics_dispatch.py::TestCrossAlgorithmConsistency::test_betweenness_timing_after_sync | 950 | 950 | 2350 | 2350 | 2.47 | slower |
| tests/e2e/test_analytics_dispatch.py::TestDegreeDispatch::test_degree_classmethod_fires_and_is_fast | 5410 | 5410 | 4510 | 4510 | 0.834 | faster |
| tests/e2e/test_analytics_dispatch.py::TestDegreeDispatch::test_degree_fallback_still_correct | 420 | 420 | 310 | 310 | 0.738 | faster |
| tests/e2e/test_analytics_dispatch.py::TestLeidenDispatch::test_lazykg_leiden_fires_when_serverside_patched_out | 1100 | 1100 | 940 | 940 | 0.855 | faster |
| tests/e2e/test_analytics_dispatch.py::TestLeidenDispatch::test_serverside_leiden_fires_when_igraph_available | 380 | 380 | 220 | 220 | 0.579 | faster |
| tests/e2e/test_betweenness_neighborhood_e2e.py::TestBetweennessNeighborhoodE2E::test_returns_nonempty_results | 220 | 220 | n/a | n/a | n/a | n/a |
| tests/e2e/test_msbfs_closeness.py::TestMSBFSClosenessParity::test_msbfs_faster_than_sequential | 5350 | 5350 | 5750 | 5750 | 1.07 | parity |
| tests/e2e/test_msbfs_closeness.py::TestMSBFSClosenessParity::test_msbfs_matches_networkx_harmonic | 560 | 560 | 1510 | 1510 | 2.7 | slower |
| tests/e2e/test_msbfs_closeness.py::TestMSBFSClosenessParity::test_msbfs_matches_sequential[in-classical] | 340 | 340 | 360 | 360 | 1.06 | parity |
| tests/e2e/test_msbfs_closeness.py::TestMSBFSClosenessParity::test_msbfs_matches_sequential[in-harmonic] | 610 | 610 | 380 | 380 | 0.623 | faster |
| tests/e2e/test_msbfs_closeness.py::TestMSBFSClosenessParity::test_msbfs_matches_sequential[out-classical] | 380 | 380 | 350 | 350 | 0.921 | parity |
| tests/e2e/test_msbfs_closeness.py::TestMSBFSClosenessParity::test_msbfs_matches_sequential[out-harmonic] | 320 | 320 | 340 | 340 | 1.06 | parity |
| wall_total | 48741 | 48741 | 49120 | 49120 | 1.01 | parity |

## e2e-gates-kernels

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/e2e/test_230_bulk_ingest_upsert.py::TestBulkIngestNodesUpsert::test_repeated_ids_and_labels_do_not_pace_the_node_ingest | 120 | 120 | 160 | 160 | 1.33 | slower |
| tests/e2e/test_230_bulk_ingest_upsert.py::TestBulkIngestUpsert::test_every_endpoint_lands_in_the_default_graph | 40.0 | 40.0 | 60.0 | 60.0 | 1.5 | slower |
| tests/e2e/test_230_bulk_ingest_upsert.py::TestBulkIngestUpsert::test_re_ingesting_the_same_batch_is_idempotent_and_still_fast | 60.0 | 60.0 | 80.0 | 80.0 | 1.33 | slower |
| tests/e2e/test_230_bulk_ingest_upsert.py::TestBulkIngestUpsert::test_repeated_endpoints_do_not_pace_the_ingest | 60.0 | 60.0 | 80.0 | 80.0 | 1.33 | slower |
| tests/e2e/test_approx_count_distinct.py::TestApproxCountDistinct::test_sc001_latency_under_10ms | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_approx_count_distinct.py::TestApproxCountDistinct::test_sc002_accuracy_within_6_5_percent | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_cypher_vl_path_bfs.py::TestCypherVLPathBFS::test_no_regression_single_hop | 40.0 | 40.0 | 50.0 | 50.0 | 1.25 | slower |
| tests/e2e/test_cypher_vl_path_bfs.py::TestCypherVLPathBFS::test_vl_path_exact_hops | 60.0 | 60.0 | 60.0 | 60.0 | 1 | parity |
| tests/e2e/test_ic3_exact_count.py::test_khop2_count_exact_fallback | 100 | 100 | 60.0 | 60.0 | 0.6 | faster |
| tests/e2e/test_ic3_exact_count.py::test_khop2_count_exact_matches_khop2_count | n/a | n/a | 10.0 | 10.0 | n/a | n/a |
| tests/e2e/test_ic3_exact_count.py::test_khop2_count_exact_under_1ms | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_ic3_exact_count.py::test_rebuild_nkg_under_30s | 220 | 220 | 190 | 190 | 0.864 | faster |
| tests/e2e/test_plaid_search_e2e.py::TestPLAIDSearchE2E::test_build_creates_valid_index | 2530 | 2530 | 2950 | 2950 | 1.17 | slower |
| tests/e2e/test_plaid_search_e2e.py::TestPLAIDSearchE2E::test_insert_makes_doc_searchable | 10.0 | 10.0 | 20.0 | 20.0 | 2 | slower |
| tests/e2e/test_plaid_search_e2e.py::TestPLAIDSearchE2E::test_search_latency | 50.0 | 50.0 | 100 | 100 | 2 | slower |
| tests/e2e/test_plaid_search_e2e.py::TestPLAIDSearchE2E::test_search_recall_vs_brute_force | 30.0 | 30.0 | 50.0 | 50.0 | 1.67 | slower |
| tests/e2e/test_plaid_search_e2e.py::TestPLAIDSearchE2E::test_search_returns_expected_top_result | 30.0 | 30.0 | 80.0 | 80.0 | 2.67 | slower |
| wall_total | 24074 | 24074 | 24958 | 24958 | 1.04 | parity |

## e2e-stress-ingest

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/e2e/test_stress_ingest.py::TestBulkIngestEdges::test_bulk_ingest_edges_direct_kg | 30.0 | 30.0 | 60.0 | 60.0 | 2 | slower |
| tests/e2e/test_stress_ingest.py::TestBulkIngestEdges::test_bulk_ingest_throughput_10k | 1010 | 1010 | 1630 | 1630 | 1.61 | slower |
| tests/e2e/test_stress_ingest.py::TestEdgeIngest::test_bulk_create_edges_1k | 640 | 640 | 1240 | 1240 | 1.94 | slower |
| tests/e2e/test_stress_ingest.py::TestEdgeIngest::test_delete_edge | 40.0 | 40.0 | 40.0 | 40.0 | 1 | parity |
| tests/e2e/test_stress_ingest.py::TestEdgeIngest::test_edge_with_qualifiers | 50.0 | 50.0 | 110 | 110 | 2.2 | slower |
| tests/e2e/test_stress_ingest.py::TestEdgeIngest::test_self_loop_edge | n/a | n/a | 10.0 | 10.0 | n/a | n/a |
| tests/e2e/test_stress_ingest.py::TestEdgeIngest::test_single_edge_roundtrip | 100 | 100 | 100 | 100 | 1 | parity |
| tests/e2e/test_stress_ingest.py::TestIngestErrorRecovery::test_special_chars_in_node_id | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_stress_ingest.py::TestIngestErrorRecovery::test_very_large_property_value | n/a | n/a | 30.0 | 30.0 | n/a | n/a |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_bulk_create_nodes_10k | 1220 | 1220 | 1240 | 1240 | 1.02 | parity |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_bulk_create_nodes_1k | 300 | 300 | 320 | 320 | 1.07 | parity |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_delete_node | 230 | 230 | 280 | 280 | 1.22 | slower |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_duplicate_node_no_crash | 60.0 | 60.0 | 70.0 | 70.0 | 1.17 | slower |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_node_with_long_string_property | 10.0 | 10.0 | n/a | n/a | n/a | n/a |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_node_with_unicode_properties | 40.0 | 40.0 | 50.0 | 50.0 | 1.25 | slower |
| tests/e2e/test_stress_ingest.py::TestNodeIngest::test_single_node_roundtrip | 100 | 100 | 90.0 | 90.0 | 0.9 | parity |
| tests/e2e/test_stress_ingest.py::TestTemporalIngest::test_bulk_temporal_edges | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/e2e/test_stress_ingest.py::TestTemporalIngest::test_create_temporal_edge | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_stress_ingest.py::TestTemporalIngest::test_get_edges_in_window | n/a | n/a | 10.0 | 10.0 | n/a | n/a |
| tests/e2e/test_stress_ingest.py::TestTemporalIngest::test_purge_before | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_stress_ingest.py::TestTemporalIngest::test_temporal_aggregate_count | 30.0 | 30.0 | 40.0 | 40.0 | 1.33 | slower |
| wall_total | 37265 | 37265 | 41639 | 41639 | 1.12 | slower |

## e2e-stress-search

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/e2e/test_stress_search.py::TestBM25::test_bm25_build_search_lifecycle | 380 | 380 | 500 | 500 | 1.32 | slower |
| tests/e2e/test_stress_search.py::TestBM25::test_bm25_drop_and_rebuild | 300 | 300 | 290 | 290 | 0.967 | parity |
| tests/e2e/test_stress_search.py::TestBM25::test_bm25_info | 300 | 300 | 270 | 270 | 0.9 | parity |
| tests/e2e/test_stress_search.py::TestBM25::test_bm25_search_empty_query | 240 | 240 | 310 | 310 | 1.29 | slower |
| tests/e2e/test_stress_search.py::TestBM25::test_bm25_search_returns_scores | 290 | 290 | 460 | 460 | 1.59 | slower |
| tests/e2e/test_stress_search.py::TestHybridSearch::test_rrf_fusion_no_crash | 550 | 550 | 620 | 620 | 1.13 | slower |
| tests/e2e/test_stress_search.py::TestHybridSearch::test_vector_graph_search | 280 | 280 | 470 | 470 | 1.68 | slower |
| tests/e2e/test_stress_search.py::TestIVFIndex::test_ivf_build_and_search | 4910 | 4910 | 4310 | 4310 | 0.878 | faster |
| tests/e2e/test_stress_search.py::TestIVFIndex::test_ivf_drop_and_rebuild | 410 | 410 | 620 | 620 | 1.51 | slower |
| tests/e2e/test_stress_search.py::TestIVFIndex::test_ivf_info_after_build | 200 | 200 | 340 | 340 | 1.7 | slower |
| tests/e2e/test_stress_search.py::TestIVFIndex::test_ivf_search_latency | 220 | 220 | 360 | 360 | 1.64 | slower |
| tests/e2e/test_stress_search.py::TestIVFIndex::test_ivf_search_nprobe_parameter | 190 | 190 | 320 | 320 | 1.68 | slower |
| tests/e2e/test_stress_search.py::TestNodeEmbeddings::test_embed_nodes_by_label | 50.0 | 50.0 | 60.0 | 60.0 | 1.2 | slower |
| tests/e2e/test_stress_search.py::TestNodeEmbeddings::test_get_embedding_roundtrip | 20.0 | 20.0 | 40.0 | 40.0 | 2 | slower |
| tests/e2e/test_stress_search.py::TestNodeEmbeddings::test_store_embeddings_batch | 20.0 | 20.0 | 40.0 | 40.0 | 2 | slower |
| tests/e2e/test_stress_search.py::TestVecIndex::test_vec_build_required_before_search | 10.0 | 10.0 | 20.0 | 20.0 | 2 | slower |
| tests/e2e/test_stress_search.py::TestVecIndex::test_vec_bulk_insert | 20.0 | 20.0 | 20.0 | 20.0 | 1 | parity |
| tests/e2e/test_stress_search.py::TestVecIndex::test_vec_create_and_search | 20.0 | 20.0 | 30.0 | 30.0 | 1.5 | slower |
| tests/e2e/test_stress_search.py::TestVecIndex::test_vec_lifecycle_create_build_search_drop | 20.0 | 20.0 | 20.0 | 20.0 | 1 | parity |
| wall_total | 28076 | 28076 | 31216 | 31216 | 1.11 | slower |

## e2e-stress-traversal

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_count_distinct | 170 | 170 | 200 | 200 | 1.18 | slower |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_match_all_nodes_with_label | 30.0 | 30.0 | 40.0 | 40.0 | 1.33 | slower |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_match_relationship_pattern | 50.0 | 50.0 | 50.0 | 50.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_match_two_hop_path | 340 | 340 | 330 | 330 | 0.971 | parity |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_match_with_property_filter | 110 | 110 | 110 | 110 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_optional_match_returns_null | 50.0 | 50.0 | 50.0 | 50.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_order_by_limit | 80.0 | 80.0 | 90.0 | 90.0 | 1.12 | slower |
| tests/e2e/test_stress_traversal.py::TestBasicCypher::test_where_in_list | 40.0 | 40.0 | 30.0 | 30.0 | 0.75 | faster |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_cypher_aggregation_no_rows | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_cypher_create_and_match | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_cypher_property_types_roundtrip | 60.0 | 60.0 | 70.0 | 70.0 | 1.17 | slower |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_cypher_with_null_parameter | 20.0 | 20.0 | 20.0 | 20.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_empty_graph_match_returns_empty | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_large_limit_no_crash | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestCypherEdgeCases::test_match_with_no_results_returns_empty_columns | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestCypherParseErrors::test_valid_but_complex_query | 240 | 240 | 250 | 250 | 1.04 | parity |
| tests/e2e/test_stress_traversal.py::TestVariableLengthPaths::test_vl_1_to_2_hops | 20.0 | 20.0 | 20.0 | 20.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestVariableLengthPaths::test_vl_deep_path_no_crash | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestVariableLengthPaths::test_vl_exact_hops | 20.0 | 20.0 | 10.0 | 10.0 | 0.5 | faster |
| tests/e2e/test_stress_traversal.py::TestVariableLengthPaths::test_vl_undirected | 10.0 | 10.0 | 10.0 | 10.0 | 1 | parity |
| tests/e2e/test_stress_traversal.py::TestVariableLengthPaths::test_vl_with_limit_is_fast | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| wall_total | 22486 | 22486 | 27286 | 27286 | 1.21 | slower |

## integration-bidirectional-ppr-perf

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/integration/test_bidirectional_ppr.py::TestIndexOptimization::test_fallback_without_index_acceptable | 25410 | 25410 | 6560 | 6560 | 0.258 | faster |
| tests/integration/test_bidirectional_ppr.py::TestPerformance::test_bidirectional_performance_acceptable | 40.0 | 40.0 | 40.0 | 40.0 | 1 | parity |
| tests/integration/test_bidirectional_ppr.py::TestPerformance::test_forward_only_no_regression | 20.0 | 20.0 | 20.0 | 20.0 | 1 | parity |
| wall_total | 98210 | 98210 | 54809 | 54809 | 0.558 | faster |

## integration-ledger-perf-quick

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| ledger.sc004.commit100_median_ms | 21.1 | 21.1 | 21.6 | 21.6 | 1.03 | parity |
| ledger.sc004.commit100_p95_ms | 88.1 | 88.1 | 24.6 | 24.6 | 0.28 | faster |
| ledger.sc004.commit5000_p95_ms | 1364 | 1364 | 1151 | 1151 | 0.844 | faster |
| ledger.sc004.tx100_median_ms | 48.1 | 48.1 | 48.6 | 48.6 | 1.01 | parity |
| ledger.sc005.head_p95_ms | 0.25 | 0.25 | 0.499 | 0.499 | 2 | slower |
| tests/integration/test_ledger_perf.py::TestCommitLatency::test_sc004_commit_latency_and_overhead | 8470 | 8470 | 7220 | 7220 | 0.852 | faster |
| tests/integration/test_ledger_perf.py::TestCommitLatency::test_sc005_head_read | 20.0 | 20.0 | 170 | 170 | 8.5 | slower |
| wall_total | 29390 | 29390 | 36830 | 36830 | 1.25 | slower |

## integration-nodepk-graph-analytics

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/integration/test_nodepk_graph_analytics.py::TestGraphAnalytics::test_community_detection_label_propagation | 40.0 | 40.0 | 40.0 | 40.0 | 1 | parity |
| tests/integration/test_nodepk_graph_analytics.py::TestGraphAnalytics::test_connected_components_pregel | 30.0 | 30.0 | 30.0 | 30.0 | 1 | parity |
| tests/integration/test_nodepk_graph_analytics.py::TestGraphAnalytics::test_degree_centrality | 150 | 150 | 160 | 160 | 1.07 | parity |
| tests/integration/test_nodepk_graph_analytics.py::TestGraphAnalytics::test_pagerank_iterative | 80.0 | 80.0 | 90.0 | 90.0 | 1.12 | slower |
| tests/integration/test_nodepk_graph_analytics.py::TestGraphAnalytics::test_shortest_path_bfs | 40.0 | 40.0 | 30.0 | 30.0 | 0.75 | faster |
| wall_total | 34308 | 34308 | 40234 | 40234 | 1.17 | slower |

## integration-nodepk-performance

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/integration/test_nodepk_performance.py::TestNodePKPerformance::test_bulk_insert_1000_per_second | 2360 | 2360 | 2090 | 2090 | 0.886 | faster |
| tests/integration/test_nodepk_performance.py::TestNodePKPerformance::test_edge_insert_degradation_under_10_percent | 660 | 660 | 660 | 660 | 1 | parity |
| wall_total | 22432 | 22432 | 20789 | 20789 | 0.927 | parity |

## nkg-enterprise-khop

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| wall_total | 5751 | 5751 | 17435 | 17435 | 3.03 | slower |

## perf-227-single-graph-latency

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| legacy_scan_median | 1.34 | 1.34 | 1.38 | 1.38 | 1.03 | parity |
| scoped_median | 1.46 | 1.46 | 1.53 | 1.53 | 1.05 | parity |
| wall_total | 17802 | 17802 | 22372 | 22372 | 1.26 | slower |

## perf-betweenness-quick

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| tests/perf/test_betweenness_vs_gds.py::TestBetweennessVsGDS::test_betweenness_er500 | 3130 | 3130 | 30260 | 30260 | 9.67 | slower |
| tests/perf/test_betweenness_vs_gds.py::TestBetweennessVsGDS::test_betweenness_karate | 8240 | 8240 | 21820 | 21820 | 2.65 | slower |
| wall_total | 29258 | 29258 | 77603 | 77603 | 2.65 | slower |

## perf-head-to-head

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| er_2000.ivg.betweenness_ms | 105 | 105 | 66.7 | 66.7 | 0.633 | faster |
| er_2000.ivg.closeness_ms | 189 | 189 | 179 | 179 | 0.946 | parity |
| er_2000.ivg.degree_ms | 46.1 | 46.1 | 13.8 | 13.8 | 0.299 | faster |
| er_2000.ivg.leiden_ms | 174 | 174 | 142 | 142 | 0.815 | faster |
| er_2000.ivg.load_ms | 27353 | 27353 | 9345 | 9345 | 0.342 | faster |
| er_500.ivg.betweenness_ms | 60.6 | 60.6 | 75.4 | 75.4 | 1.24 | slower |
| er_500.ivg.closeness_ms | 20.0 | 20.0 | 19.9 | 19.9 | 0.995 | parity |
| er_500.ivg.degree_ms | 3.3 | 3.3 | 26.6 | 26.6 | 8.06 | slower |
| er_500.ivg.leiden_ms | 29.7 | 29.7 | 28.1 | 28.1 | 0.946 | parity |
| er_500.ivg.load_ms | 23508 | 23508 | 25440 | 25440 | 1.08 | parity |
| karate.ivg.betweenness_ms | 32.3 | 32.3 | 58.0 | 58.0 | 1.8 | slower |
| karate.ivg.closeness_ms | 0.7 | 0.7 | 0.9 | 0.9 | 1.29 | slower |
| karate.ivg.degree_ms | 7.5 | 7.5 | 7.4 | 7.4 | 0.987 | parity |
| karate.ivg.leiden_ms | 129 | 129 | 120 | 120 | 0.928 | parity |
| karate.ivg.load_ms | 1344 | 1344 | 1656 | 1656 | 1.23 | slower |
| tests/perf/test_head_to_head.py::test_head_to_head | 55820 | 55820 | 40140 | 40140 | 0.719 | faster |
| wall_total | 75833 | 75833 | 55755 | 55755 | 0.735 | faster |

## perf-scale-benchmark

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| wall_total | 18788 | 18788 | 18268 | 18268 | 0.972 | parity |

## three-engine

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| wall_total | 67563 | 67563 | 28256 | 28256 | 0.418 | faster |

## unit-cypher-benchmark

| metric | A median | A min | B median | B min | B/A | verdict |
|---|---:|---:|---:|---:|---:|---|
| test_01_point_lookup.p50 | 0.4 | 0.4 | 0.4 | 0.4 | 1 | parity |
| test_01_point_lookup.p95 | 0.5 | 0.5 | 0.7 | 0.7 | 1.4 | slower |
| test_02_label_filter.p50 | 0.4 | 0.4 | 0.4 | 0.4 | 1 | parity |
| test_02_label_filter.p95 | 0.6 | 0.6 | 0.8 | 0.8 | 1.33 | slower |
| test_03_in_list.p50 | 0.4 | 0.4 | 0.4 | 0.4 | 1 | parity |
| test_03_in_list.p95 | 1.3 | 1.3 | 1.6 | 1.6 | 1.23 | slower |
| test_04_1hop_traversal.p50 | 0.3 | 0.3 | 0.4 | 0.4 | 1.33 | slower |
| test_04_1hop_traversal.p95 | 0.5 | 0.5 | 0.5 | 0.5 | 1 | parity |
| test_05_2hop_traversal.p50 | 0.4 | 0.4 | 0.4 | 0.4 | 1 | parity |
| test_05_2hop_traversal.p95 | 0.6 | 0.6 | 2.8 | 2.8 | 4.67 | slower |
| test_06_aggregation_count.p50 | 0.6 | 0.6 | 0.5 | 0.5 | 0.833 | faster |
| test_06_aggregation_count.p95 | 0.8 | 0.8 | 0.7 | 0.7 | 0.875 | faster |
| test_07_degree_count.p50 | 0.5 | 0.5 | 0.8 | 0.8 | 1.6 | slower |
| test_07_degree_count.p95 | 1 | 1 | 1.3 | 1.3 | 1.3 | slower |
| test_08_varlength_bfs.p50 | 1.2 | 1.2 | 1.6 | 1.6 | 1.33 | slower |
| test_08_varlength_bfs.p95 | 2.3 | 2.3 | 1.8 | 1.8 | 0.783 | faster |
| test_09_where_label_and_prop.p50 | 0.5 | 0.5 | 0.5 | 0.5 | 1 | parity |
| test_09_where_label_and_prop.p95 | 0.5 | 0.5 | 1.4 | 1.4 | 2.8 | slower |
| test_10_with_having.p50 | 0.5 | 0.5 | 0.6 | 0.6 | 1.2 | slower |
| test_10_with_having.p95 | 0.5 | 0.5 | 19.0 | 19.0 | 38.0 | slower |
| test_11_set_map_merge.p50 | 1.4 | 1.4 | 1.6 | 1.6 | 1.14 | slower |
| test_11_set_map_merge.p95 | 1.5 | 1.5 | 2.4 | 2.4 | 1.6 | slower |
| test_12_union.p50 | 0.4 | 0.4 | 0.6 | 0.6 | 1.5 | slower |
| test_12_union.p95 | 0.5 | 0.5 | 1 | 1 | 2 | slower |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_01_point_lookup | 50.0 | 50.0 | 50.0 | 50.0 | 1 | parity |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_02_label_filter | 150 | 150 | 120 | 120 | 0.8 | faster |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_03_in_list | 90.0 | 90.0 | 80.0 | 80.0 | 0.889 | faster |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_04_1hop_traversal | 180 | 180 | 170 | 170 | 0.944 | parity |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_05_2hop_traversal | 450 | 450 | 380 | 380 | 0.844 | faster |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_06_aggregation_count | 160 | 160 | 180 | 180 | 1.12 | slower |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_07_degree_count | 300 | 300 | 260 | 260 | 0.867 | faster |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_08_varlength_bfs | 90.0 | 90.0 | 100 | 100 | 1.11 | slower |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_09_where_label_and_prop | 390 | 390 | 320 | 320 | 0.821 | faster |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_10_with_having | 150 | 150 | 150 | 150 | 1 | parity |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_11_set_map_merge | 20.0 | 20.0 | 30.0 | 30.0 | 1.5 | slower |
| tests/unit/test_cypher_benchmark.py::TestCypherBenchmark::test_12_union | 250 | 250 | 230 | 230 | 0.92 | parity |
| wall_total | 57198 | 57198 | 36030 | 36030 | 0.63 | faster |

## Failed or incomplete reps

- e2e-gates-analytics side A rep 1: tests-failed (metrics kept): tests.e2e.test_betweenness_neighborhood_e2e.TestBetweennessNeighborhoodE2E::test_returns_nonempty_results, tests.e2e.test_betweenness_neighborhood_e2e.TestBetweennessNeighborhoodE2E::test_seed_node_highest_score, tests.e2e.test_betweenness_neighborhood_e2e.TestBetweennessNeighborhoodE2E::test_different_seeds_different_hubs
- e2e-gates-analytics side B rep 1: tests-failed (metrics kept): tests.e2e.test_betweenness_neighborhood_e2e.TestBetweennessNeighborhoodE2E::test_returns_nonempty_results, tests.e2e.test_betweenness_neighborhood_e2e.TestBetweennessNeighborhoodE2E::test_seed_node_highest_score, tests.e2e.test_betweenness_neighborhood_e2e.TestBetweennessNeighborhoodE2E::test_different_seeds_different_hubs
- e2e-stress-ingest side A rep 1: tests-failed (metrics kept): tests.e2e.test_stress_ingest.TestBulkIngestEdges::test_bulk_ingest_throughput_10k
- e2e-stress-ingest side B rep 1: tests-failed (metrics kept): tests.e2e.test_stress_ingest.TestBulkIngestEdges::test_bulk_ingest_throughput_10k
- perf-227-single-graph-latency side B rep 1: tests-failed (metrics kept): tests.perf.test_227_single_graph_latency::test_the_scoped_search_stays_within_the_budget

## Totals

parity: 94, faster: 35, slower: 86, n/a: 6
