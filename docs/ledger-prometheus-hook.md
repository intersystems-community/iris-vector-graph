<!-- markdownlint-disable MD013 -->

# Ledger metrics: Prometheus exposition hook (reference)

The revision ledger (spec 213) exposes counters three ways with no extra
dependency: a structured log line per commit outcome, the engine status report
(`engine.status().ledger`), and the queryable table `Graph_KG.ledger_stats`.
For scraping, register a hook on the ledger client. The hook receives one event
dictionary per commit outcome; how you publish it is up to you.

This document is a reference implementation only. Nothing here is part of the
library; copy what you need.

## Event shape

```python
{
    "outcome": "success",          # success | replayed | stale_head | unknown_head |
                                   # idempotency_conflict | failed_op | too_large |
                                   # disabled | not_enabled | lock_timeout | inconsistent
    "revision_id": "…32 hex…",     # None unless a revision was created or replayed
    "seq": 42,                     # None unless a revision was created or replayed
    "actor": "ingest:etl-42",
    "actor_type": "ingest",
    "op_count": 17,
    "duration_ms": 41.2,
    "reason": None,                # server reason text on rejection
}
```

## Minimal exporter

```python
import threading
from collections import Counter

class LedgerPrometheus:
    """Accumulates ledger events and renders Prometheus text exposition."""

    def __init__(self, engine):
        self._lock = threading.Lock()
        self._outcomes = Counter()
        self._duration_sum = 0.0
        self._duration_count = 0
        engine.ledger.register_metrics_hook(self.on_event)
        self._engine = engine

    def on_event(self, event: dict) -> None:
        with self._lock:
            self._outcomes[event["outcome"]] += 1
            self._duration_sum += float(event["duration_ms"])
            self._duration_count += 1

    def render(self) -> str:
        lines = [
            "# TYPE ivg_ledger_commit_outcomes_total counter",
        ]
        with self._lock:
            for outcome, n in sorted(self._outcomes.items()):
                lines.append(f'ivg_ledger_commit_outcomes_total{{outcome="{outcome}"}} {n}')
            lines += [
                "# TYPE ivg_ledger_commit_duration_ms_sum counter",
                f"ivg_ledger_commit_duration_ms_sum {self._duration_sum:.3f}",
                "# TYPE ivg_ledger_commit_duration_ms_count counter",
                f"ivg_ledger_commit_duration_ms_count {self._duration_count}",
            ]
        # authoritative counters from the server-side statistics table
        st = self._engine.ledger.stats()
        lines += [
            "# TYPE ivg_ledger_head_seq gauge",
            f"ivg_ledger_head_seq {st.head_seq}",
            "# TYPE ivg_ledger_commits_ok_total counter",
            f"ivg_ledger_commits_ok_total {st.commits_ok}",
            "# TYPE ivg_ledger_rejections_total counter",
        ]
        for kind, n in sorted(st.rejections.items()):
            lines.append(f'ivg_ledger_rejections_total{{kind="{kind}"}} {n}')
        return "\n".join(lines) + "\n"
```

Serve `render()` from any HTTP handler (FastAPI, `http.server`, the IRIS
`/api/monitor` sidecar pattern) at `/metrics`. Hooks must return quickly and
must not raise; the ledger logs and swallows hook exceptions so a broken
exporter can never fail a commit.

## Cross-process note

Hook counters are per Python process. The `Graph_KG.ledger_stats` table is
the cross-process source of truth; the exporter above merges both, using the
table for totals and the hook for per-outcome latency.
