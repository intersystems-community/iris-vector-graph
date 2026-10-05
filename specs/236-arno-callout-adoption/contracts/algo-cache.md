<!-- markdownlint-disable MD013 -->

# Contract: algorithm result cache

`Graph.KG.AlgoCache` (storage `^IVG.AlgoCache(graphKey, algorithm, paramHash)` =
`$ListBuild(token, payload)`). The graph is passed **by name**; the class derives the
subscript with `Graph.KG.GraphKey.ForIndex`, so the default graph is `0` and never the
ledger form.

- `Token() As %Integer`: the current `^KG("__version")`.
- `Lookup(graph, algorithm, paramHash) As %String`: one round trip. `"H<token>|<payload>"`
  when the stored token equals the current one, otherwise `"M<token>"`.
- `Get(graph, algorithm, paramHash) As %String`: the payload on a hit, otherwise `""`.
- `Put(graph, algorithm, paramHash, token, payload)`: stores the entry. `token` is the
  value read before compute started, so a write during compute leaves the entry stale,
  never wrongly fresh.
- `InvalidateGraph(graph)` / `InvalidateAll()`: kill a graph's subtree / everything.
  `Graph.KG.Eraser` calls the equivalent Kills on every erase path.

Python wrapper (`IRISGraphStore._cached_algorithm`, used by `execute_pagerank`, `execute_ppr`,
`execute_wcc`, `execute_cdlp`):

1. (large results only) if a private copy exists, re-read `Token()`; equal means serve it;
2. `Lookup`; a hit is served;
3. on a miss compute (Native or Owner);
4. `Put` with the token from step 2.

Not cached: `subgraph`, results above `MAX_PAYLOAD_CHARS` (1,000,000), calls with
`IVG_DISABLE_ALGO_CACHE=1`, and any store whose cache class raises (the store turns the
cache off and computes). The private layer only holds results of `FRONT_MIN_CHARS`
(20,000) or more, at most 8 of them per store.
