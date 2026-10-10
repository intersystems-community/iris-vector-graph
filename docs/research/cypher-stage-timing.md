<!-- markdownlint-disable MD013 MD060 -->

# Where `execute_cypher` time goes (2026-10-07)

The experiment the cache investigation ended on: split `execute_cypher` into stages
on LOS-shaped queries and see whether translation or the connection probe is worth
removing. Nothing here is committed to a design; it decides whether a cache is worth
building.

## Setup

- `ivg-iris-enterprise`, scratch namespace `IVG236`, `arno-callout 1.1.0` loaded.
- Graph shaped like the one `productivity-framework`'s `query_builder.py` queries:
  5,000 `Proposition`, 500 `Goal`, 2,000 `Evidence` nodes; 29,500 props; 22,000 edges
  (`depends_on` x3 and `related_to` per proposition, `defeats` per evidence).
  Synthetic, so absolute times will differ from production.
- Each query run 200 times on one engine; stages timed by wrapping `parse_query`,
  `translate_to_sql`, `_reconnect_if_stale`, `store.execute_sql` and
  `_route_var_length`. Medians, milliseconds. `other` is the remainder (decode passes).
- `^KG`/`^NKG` built by `engine.sync()` first.

## Result (Arno library loaded, default settings)

| Query shape (LOS)                                     | Rows | Total | Probe | Parse | Translate |  SQL | Var-length route | Other |
| ----------------------------------------------------- | ---: | ----: | ----: | ----: | --------: | ---: | ---------------: | ----: |
| related goals: `[:related_to\|:depends_on*1..2]`      |    1 |  0.62 |  0.14 |  0.00 |      0.13 | 0.00 |             0.34 |  0.02 |
| defeated propositions: `OPTIONAL MATCH`, `collect`    |   50 |  7.09 |  0.22 |  0.00 |      0.23 | 6.48 |                - |  0.15 |
| dependency path: `[:depends_on*1..3]`, `length(path)` |    0 | 28.19 |  0.24 |  0.00 |      0.21 |    - |            27.66 |  0.07 |
| point lookup by property                              |    1 |  0.36 |  0.12 |  0.00 |      0.08 | 0.14 |                - |  0.03 |

Parse is about 0 because it is already cached (`lru_cache`). A second run with
`IVG_DISABLE_ARNO=1` ran about twice as slow across the board for stages that do not
involve Arno (translate 0.27 against 0.13 ms), so the machine was noisier; compare
shares within a run, not across runs.

## What it says

1. **Translate plus the connection probe are 0.2 to 0.4 ms per call.** That is 40 to
   55% of the two fast queries (point lookup, related goals) and 6% of the SQL-heavy
   one. The probe is a `SELECT 1` round trip on every call (`engine.py:509`, called
   from `query.py:783`). Removing it and retrying on failure is the cheapest gain. A
   translate cache saves the other 0.1 to 0.2 ms. Neither matters for the 7 ms query.
2. **Only the SQL-heavy query is big enough for a result cache to matter**, and that
   is the class with no sound invalidation signal (`research-token-audit.md`).
   Not recommended.
3. **Found while measuring: the dependency-path query is 28 ms with Arno and 0.9 ms
   without, and returns no rows either way.**
   - The 28 ms is the Arno BFS path: the log shows `Arno BFSJson failed, falling back
to BFSFastJsonSorted: Expecting value` on each call, so every call pays a failed
     native attempt (it exports the whole adjacency first) and then the ObjectScript
     answer. Cause of the parse failure not diagnosed.
   - The empty result is a separate bug: `MATCH path = (p {id: 'prop23'})-[:depends_on*1..3]->(dep)
RETURN dep.id, length(path) AS depth` returns 0 rows, while the same query
     without `length(path)` returns 39 and SQL shows three `depends_on` edges from
     that node. It returns 0 rows with no error, with and without Arno. This is the
     shape of `query_builder.py`'s dependency-chain query.

## Done since (same day)

- **Probe throttled.** `execute_cypher` now probes at most once per 30 s
  (`IVG_PROBE_IDLE_SECONDS`, `0` restores every call): `engine._probe_if_idle`,
  `tests/unit/test_cypher_probe_throttle.py`. Re-measured, the probe stage is 0.00 ms
  per call; total latency of the fast queries did not visibly change (0.36 to 0.39 ms)
  because this host's run-to-run noise (about 2x between runs) is larger than the 0.12 ms
  saved.
- **Arno BFS no longer builds an adjacency it will throw away.** `BFSJson` exported the
  whole adjacency (617,000 characters, 34 ms on 22,000 edges) before the size gate
  turned the call away. `ExportAdjacencyWithPreds` now takes a cap and stops with
  `"OVERSIZE"`. The dependency-path query fell from 28.2 ms to 2.8 ms.
- **`SORTED:0` is an empty answer.** It was parsed as JSON, logged as an Arno failure
  and answered a second time by the ObjectScript BFS.
- **The empty `length(path)` result is written up** in
  `docs/research/length-path-empty-result-report.md` (draft, not filed). Cause found:
  the var-length executor takes the first string SQL parameter as the source id, which
  is the label.

## Still open

- 2.6 ms remains on the dependency-path query against about 0.4 ms for the ObjectScript
  BFS alone: the Arno attempt (`_arno_call`, library reload check, the oversize
  answer) before the ObjectScript BFS. Skipping the Arno attempt when
  `^NKG("$meta")` edge count shows the graph cannot fit would remove it. Not done.
