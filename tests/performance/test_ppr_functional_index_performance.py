"""
Performance tests for PPR Functional Index.

Tests verify:
1. <10ms latency for 10K-node graphs
2. <100ms latency for 100K-node graphs
3. Linear scaling

Reference: specs/002-implement-functional-index/contracts/ppr_runner.yaml
"""

import pytest


@pytest.mark.requires_database
@pytest.mark.performance
def test_ppr_10k_nodes_latency():
    """
    Test: <10ms for 10K nodes.

    Reference: contracts/ppr_runner.yaml → test_performance_10k_nodes
    MUST FAIL: Performance target not yet met (T017)
    """
    pytest.skip("T015: Test stub created. Implementation pending T017.")
