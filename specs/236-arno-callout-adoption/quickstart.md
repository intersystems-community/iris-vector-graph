<!-- markdownlint-disable MD013 -->

# Quickstart: verifying spec 236

Container: `ivg-iris-enterprise` (31972), per `tests/conftest.py`. The Native
Readers read USER's `^KG`, so the gate runs in USER (test-only on this
container); every other live test runs in the scratch namespace `IVG236`, which
the `ivg_236_scratch` fixture creates and deploys on first use.

```bash
export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972

# 1. routing, proof file, equivalence rules, cache keys, BFS gate text: no container
pytest tests/unit/test_236_*.py -q

# 2. scratch namespace, token write paths, cache, BFS size gate (container)
pytest tests/integration/test_236_capabilities.py \
       tests/integration/test_236_token_write_paths.py \
       tests/integration/test_236_algo_cache.py \
       tests/integration/test_236_bfs_size_gate.py -q

# 3. install the pinned library build (sha256 checked), deploy to USER, run the gate
scripts/enterprise-container.sh install-arno \
    ~/ws/arno/target-linux/release-1.1.0/libarno_callout-1.1.0-linux-arm64.so
scripts/enterprise-container.sh tcp-deploy
IVG_REQUIRE_ARNO=1 python scripts/arno_reader_gate.py             # report only
IVG_REQUIRE_ARNO=1 python scripts/arno_reader_gate.py --write-proof
IVG_REQUIRE_ARNO=1 python scripts/arno_reader_gate.py --check
IVG_REQUIRE_ARNO=1 pytest tests/e2e/test_236_reader_gate.py -q
```

Expected: step 1 passes with no container; step 2 passes; step 3 prints a
PASS/FAIL line per algorithm and fixture (on 1.1.0 every algorithm fails, with the
reason), `--write-proof` writes `docs/arno-reader-proof.json` with an empty
`algorithms` map, `--check` exits 0, and the gate test fails if the proof claims a
revision above what the loaded library reports.

Live tests assert a sentinel global before writing and rebuild their handles
after `BuildKG`/`sync` (research R3). The flip itself is fixed, so this is now a
belt and braces.
