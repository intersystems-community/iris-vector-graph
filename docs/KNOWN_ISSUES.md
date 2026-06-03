# Known Issues

## IVF Index Test Fixture Pollution (Pre-Release v2.0.0)

**Status:** Documented as pre-existing  
**Impact:** 11 test failures in full suite (test_ivf_index.py + test_module_coverage.py async tests), all pass in isolation  
**Root Cause:** Session-scoped `iris_connection` fixture accumulates test data (nodes, edges, labels, props) from 1700+ prior tests with zero cleanup between test modules. IVF setup only drops embedding tables, not data tables.

**Evidence:**
- All 11 failing tests pass when run in isolation
- Data accumulation occurs across 9 test modules before IVF tests run
- Test modules: introspection_api, edge_embeddings, named_graphs, snapshot, cypher_filters, etc. have zero cleanup code
- IVF tests assume empty graph state

**Attempted Fixes:**
1. Function-scoped auto-use cleanup fixture — cleanup after test execution too late, pollution already accumulated
2. pytest_runtest_setup hook for module-boundary cleanup — fixtures not available at setup phase
3. Both approaches failed to prevent data leakage

**Workaround:** Run IVF tests in isolation or run only regression suite (1687/1698 pass):
```bash
pytest tests/unit/test_ivf_index.py -v            # All 11 pass in isolation
pytest tests/e2e/test_centrality_e2e.py -v        # All 39 pass
pytest -m regression                               # 1687 pass, 8/8 guards pass
```

**Next Steps for Fix:**
- Investigate whether per-module session fixtures or fixture factories could work
- Consider test data isolation patterns (unique prefixes per test module)
- Evaluate whether IVF test setup should gracefully handle pre-existing data instead of failing
