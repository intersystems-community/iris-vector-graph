<!-- markdownlint-disable MD013 -->

# Plan: a paged temporal window reader with edge attributes (spec 255)

**Branch**: worktree of `alt-parity` · **Spec**: `spec.md` (Decisions 2026-10-22 settled)

## Decision 1 resolved: the `tlast` index

Measured 2026-10-22 on `ivg-iris-enterprise`, namespace IVGTEST, by the journal offset
(`%SYS.Journal.System.GetCurrentFileOffset`) before and after: `BulkInsert` of 3,000 edges with attributes costs
3,496–3,522 journal bytes per edge; one `Set ^KG("tlast", g, s, p) = ts` per edge costs 100–122 bytes, about 3%
(4.5% of opsreview's 2.25 kB per event). Spec 254's SC-003 allows 15%, so the index is (b), maintained on every
write. The probe is `~/.cache/ivg-bench/255/journal_probe.py`.

## Design

### Server: `Graph.KG.TemporalWindow` (new, reads only)

- `Open(graphId, specJSON)` → `{"h", "rows": [...], "done"}`. The spec names the window (`start`, `end`, both
  inclusive), the direction (`out` walks `^KG("tout")`, `in` walks `^KG("tin")`), the filters (`source`, `sources`,
  `prefix`, `predicates`), `attrs` and `page`. The first page comes back in the same call; a window that fits it is
  freed at once and needs no further call.
- `Next(h, n)` → the next page. `Close(h)` frees the handle.
- State in `^||IVG.TWin(h)`: the spec, the filter lists, and the resume key `(ts, s, p, o)` (Decision 2). Each page
  is a live walk from the key: memory is constant, no rows are held, and an edge purged after an earlier page is not
  returned. Process-private, so a dropped connection frees it.
- FR-004: with a filter, each timestamp costs a lookup, not a walk of every source at it. A single source is a
  `$Data`; a list is a leapfrog join of the sorted list against the sorted sources at that timestamp, which costs
  about the smaller of the two; a prefix is a range `$Order` from the prefix. A prefix a canonical number could
  start with (only digits, `-`, `.`) cannot use the range, because numbers collate before strings; it walks the
  timestamp's sources and compares, which is correct and as slow as today.
- A page stops at `page` rows or about 2 MB of JSON, whichever is first, so no page nears the string limit.
- Attributes are built the way `GetEdgeAttrs` builds them (a `%DynamicObject` over `^KG("edgeprop")`), so types
  come back identical.
- `Latest(graphId, specJSON)`: newest event per source (`sources`, `prefix` or every source), optionally of one
  `predicate` and `before` a timestamp (exclusive, as `PurgeRawBefore`'s end is), with attributes. Ties go to the
  last `(p, o)` in index order, i.e. the maximum in the reader's order. Pages by source (`after`, `limit`).

### Server: `^KG("tlast", g, s, p) = ts` (TemporalIndex, the only writer)

- `WriteStores` keeps the maximum timestamp per `(s, p)`. The node `^KG("tlast", g)` holds the value 1 when the
  index is complete for the graph: set by the first write into an empty graph, or by `RebuildLatest`. A graph
  written before this feature has entries but no flag, and `Latest` walks timestamps backwards for it (correct,
  slower); prefix and every-source reads there raise and name `RebuildLatest`.
- Kept exact by `DeleteTemporalEdge`, `EraseNodes`, `PurgeRawBefore`, `PurgeBefore`, `MigrateRaw`, `EraseGraph`,
  `EraseAll`. Recomputing a pair walks timestamps backwards from the removed one.
- With `before`, a source whose `tlast` is at or after the cutoff walks backwards from it, bounded by `lookback`
  when the caller gives one (Decision 1's (a), kept for this case).
- Inventory: `GraphStores` (graph-scoped), snapshot plan (exported), the single-owner and eraser guards.

### Python

- `engine.iter_edges_in_window(start, end, *, source=, sources=, source_prefix=, predicates=, direction=, attrs=True,
  page_size=1000, graph=)` → `TemporalWindowCursor`: an iterator of event dicts (the `get_edges_in_window` keys plus
  `attrs`), a context manager, `close()`; closed when exhausted, on `close()`, or when garbage-collected.
- `engine.get_latest_edges(sources=, source_prefix=, predicate=, before=, lookback=, attrs=True, graph=)` → list.
- `engine.rebuild_temporal_latest(graph=)` → number of `(s, p)` pairs indexed.
- FR-006: `execute_temporal_window_query` returns its result with `error` set on failure; `get_edges_in_window`
  raises `TemporalWindowError` (new, `iris_vector_graph.exceptions`) instead of returning `[]`.
- FR-008: `QueryWindow`, `QueryWindowInbound`, `GetEdgeAttrs` unchanged; their comments name the cap and the reader.

## Shared-graph source-first index (Decision 3)

Not built: SC-002 passes with the per-timestamp lookup (below).

## Measured (2026-10-22, `ivg-iris-enterprise`, IVGTEST, shared container)

`~/.cache/ivg-bench/255/sc_bench.py`: 5 entities per tenant, about 145 events per tenant per 5 minutes (opsreview's
11.6 k per 5-minute fleet window at 80 tenants), second timestamps, event ids as targets, 3 attributes; 15 minutes
loaded; p50 / p95 of 40 runs after a warm-up. Not opsreview's bench or VM, so comparable in shape, not to the digit.

| Read (hero tenant, 5-minute window, about 140 events) | 1 tenant  | 10 tenants | 80 tenants |
| ----------------------------------------------------- | --------- | ---------- | ---------- |
| `QueryWindow` + `GetEdgeAttrs` per edge (today)       | 22.1 ms   | 28.7 ms    | 33.8 ms    |
| `iter_edges_in_window(sources=5 entities)`            | 1.3 ms    | 1.1 ms     | 1.4 ms     |
| `iter_edges_in_window(source_prefix=)`                | 0.8 ms    | 1.0 ms     | 1.5 ms     |
| `get_latest_edges(5 entities)`                        | 0.15 ms   | 0.18 ms    | 0.14 ms    |

- SC-001: 1.3 ms one graph per tenant (≤ 3.6), 1.4 ms shared at 80 tenants (≤ 13.6). Pass.
- SC-002: 10 → 80 tenants, list 1.18×, prefix 1.50× in this run and 1.00× in the previous one (noise on a shared
  container). Pass, at the limit for the prefix in one run.
- SC-003: the 80-tenant 15-minute fleet window, 34.8 k events with attributes, 157 ms in 35 pages (4.5 µs an event);
  69.6 k in 398 ms (5.7 µs). 144 k extrapolates to 0.65–0.82 s with attributes, 0.4 s without. Under 1 s at p50; not
  measured at 144 k, to keep the load small.
- SC-004: 0.14 ms p50 for one tenant's latest at 80 tenants (≤ 3). Pass.

Two server-side choices came from the SC-003 profile: `$ZConvert(, "O", "JSON")` in place of `jsonEsc` (identical
output, checked) and attribute JSON built directly instead of a `%DynamicObject` per row, typed by comparing
`$ListBuild(v)` with `$ListBuild(+v)`, which is how `%Set` decides number or string.

## Tests

- Unit (no container): the Python cursor (paging, close on exhaustion, close on abandon, close on error), FR-006 on
  both layers, spec JSON built from the arguments, `get_latest_edges` chunking, inventory and single-owner guards
  including `tlast`, ObjectScript source rules (no `Quit value` in loops in the new class).
- E2E (IVGTEST): US1 equals `QueryWindow` + `GetEdgeAttrs` per edge, filters, union, inclusivity, ms timestamps,
  JSON-unsafe attribute text, `tsStart > tsEnd`, inbound and `suppressReverseIndex`; US2 a window past the string
  cap read in pages equal to a `^KG("tout")` walk, state footprint, close, `get_edges_in_window` raising; US3 latest
  against brute force with and without predicate and cutoff, absent sources, tlast kept exact by delete, purge and
  node erase, a pre-feature graph and `RebuildLatest`.
