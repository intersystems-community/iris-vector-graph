"""
Integration tests for PPR Functional Index.

Tests verify:
1. Real-time edge insertion reflects in PPR
2. Concurrent query isolation
3. Sink node handling
4. Invalid seed entity error handling

Reference: specs/002-implement-functional-index/contracts/ppr_runner.yaml
"""

import pytest


@pytest.mark.requires_database
@pytest.mark.integration
def test_ppr_reflects_edge_insertion():
    """
    Test: Insert edge → compute PPR → verify scores changed (no rebuild needed).

    Reference: contracts/ppr_runner.yaml → test_regression_after_edge_insert
    MUST FAIL: Full integration not yet working (T017, T020)
    """
    pytest.skip("T011: Test stub created. Implementation pending T017, T020.")


@pytest.mark.requires_database
@pytest.mark.integration
def test_concurrent_queries_isolated():
    """
    Test: Run 2 PPR queries in parallel threads, verify no cross-contamination.

    Reference: contracts/ppr_runner.yaml → test_concurrent_queries_isolated
    MUST FAIL: Process-private globals not yet implemented (T017)
    """
    pytest.skip("T012: Test stub created. Implementation pending T017.")


@pytest.mark.requires_database
@pytest.mark.integration
def test_sink_node_handling():
    """
    Test: Graph with sink node (zero outdegree) → verify score distribution.

    Reference: contracts/ppr_runner.yaml → test_sink_node_handling
    MUST FAIL: Sink node logic not yet implemented (T018)
    """
    pytest.skip("T013: Test stub created. Implementation pending T018.")


@pytest.mark.requires_database
@pytest.mark.integration
def test_invalid_seed_entity_error():
    """
    Test: Provide non-existent seed → verify ValueError raised.

    Reference: contracts/ppr_runner.yaml → test_invalid_seed_entity_error
    MUST FAIL: Seed validation not yet implemented (T019)
    """
    pytest.skip("T014: Test stub created. Implementation pending T019.")
