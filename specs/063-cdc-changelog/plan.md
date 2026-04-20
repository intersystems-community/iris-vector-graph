# Plan: CDC Changelog (spec 063)

**Branch**: `063-cdc-changelog` | **Date**: 2026-04-19

## Summary

Pure Python changes — no new ObjectScript. CDC writes use the existing `_iris_obj().classMethodVoid` or direct `iris.createIRIS(conn)` native API to set `^IVG.CDC` global subscripts. Everything else is Python engine changes.

## Constitution Check
- [x] E2E tests fail before implementation, pass after
- [x] SKIP_IRIS_TESTS defaults "false"
- [x] cdc=False (default): zero impact on existing 531 tests

## Technical Context

**Write global**: `^IVG.CDC(ts_ms, seq) = $ListBuild(op, src, pred, dst, graph_id, "")`
- `ts_ms`: `int(time.time() * 1000)` — epoch milliseconds
- `seq`: `$Increment(^IVG.CDC.seq(ts_ms))` — concurrent-safe within same millisecond
- Written via `iris_obj.set("^IVG.CDC", ts_ms, seq, value)` (native API)

**Read global**: iterate `$Order(^IVG.CDC(ts))` from requested ts onward

## Files Changed

```
iris_vector_graph/engine.py   — 6 changes:
  __init__: add cdc= param, store self._cdc
  _write_cdc(): private helper
  create_edge: call _write_cdc after success
  delete_edge: call _write_cdc after success
  create_edge_temporal: call _write_cdc after success
  bulk_create_edges: call _write_cdc per edge after success
  import_rdf: call _write_cdc per inserted edge
  get_changes_since(ts): new method
  replay_changes(entries, record_replay=False): new method
  clear_changelog(before_ts=None): new method
  cdc: property

tests/unit/test_cdc_changelog.py  — NEW
```

## _write_cdc Implementation

```python
def _write_cdc(self, op: str, src: str, pred: str, dst: str, graph_id=None):
    if not self._cdc:
        return
    try:
        import time as _time
        ts_ms = int(_time.time() * 1000)
        iris_obj = self._iris_obj()
        seq = iris_obj.increment("^IVG.CDC.seq", str(ts_ms))
        val = f"{op}\x1f{src}\x1f{pred}\x1f{dst}\x1f{graph_id or ''}"
        iris_obj.set("^IVG.CDC", str(ts_ms), str(int(seq)), val)
    except Exception as e:
        logger.debug("CDC write failed (non-fatal): %s", e)
```

Note: `$ListBuild` is ObjectScript-specific. For Python-side CDC writes, use a delimited string (unit separator `\x1f`) — simple, portable, no ObjectScript needed. `get_changes_since` splits on `\x1f`.

## get_changes_since Implementation

```python
def get_changes_since(self, ts_ms: int) -> List[dict]:
    iris_obj = self._iris_obj()
    results = []
    cur_ts = str(ts_ms)
    while True:
        cur_ts = iris_obj.order("^IVG.CDC", cur_ts)
        if not cur_ts:
            break
        seq = ""
        while True:
            seq = iris_obj.order("^IVG.CDC", cur_ts, seq)
            if not seq:
                break
            val = iris_obj.get("^IVG.CDC", cur_ts, seq)
            if val:
                parts = str(val).split("\x1f")
                results.append({
                    "ts": int(cur_ts), "seq": int(seq),
                    "op": parts[0], "src": parts[1], "pred": parts[2],
                    "dst": parts[3], "graph_id": parts[4] or None,
                })
    return results
```

## replay_changes Implementation

Iterates entries, calls `create_edge` or `delete_edge` based on op. The target engine's `_cdc` flag controls whether replay is recorded — if `record_replay=True`, temporarily enables CDC with REPLAY_* op prefix.

## Constitution Check — E2E test requirement

Every FR has a failing test before implementation. The CDC test file will have:
- `test_cdc_disabled_by_default` — cdc=False, no entries
- `test_create_edge_writes_cdc` — FAILS before impl (no ^IVG.CDC written)
- `test_delete_edge_writes_cdc` — FAILS before impl
- `test_get_changes_since_millis` — FAILS before impl
- `test_replay_changes_idempotent` — FAILS before impl
- `test_replay_record_flag` — FAILS before impl
- `test_clear_changelog` — FAILS before impl
