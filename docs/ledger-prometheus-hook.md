<!-- markdownlint-disable MD013 -->

# Ledger metrics for Prometheus

For application developers and IRIS operators who run the revision ledger (spec 213) and want its commit counters in
Prometheus. IVG ships no exporter; this page is a reference implementation to copy.

The ledger reports each commit outcome three ways, with no extra dependency:

- a log line per outcome (`ledger.commit outcome=... revision_id=... actor=...`, logger
  `iris_vector_graph.ledger`, level INFO), with the event dictionary below in the record's `ledger_event`;
- a hook you register on `engine.ledger`, called with that dictionary;
- the counters IRIS keeps in the IVG Schema's `ledger_stats` table, read by `engine.ledger.stats()` and shown in
  `engine.status().ledger`.

Hook counts are per Python process. `ledger_stats` counts every process's commits, so it is the one to use for totals;
the hook adds what the table does not keep, such as commit latency.

## The event a hook receives

```python
{
    "outcome": "success",       # success | replayed | stale_head | unknown_head | idempotency_conflict |
                                # failed_op | too_large | disabled | not_enabled | lock_timeout | inconsistent
    "revision_id": "…32 hex…",  # None unless a revision was created or replayed
    "seq": 42,                  # None unless a revision was created or replayed
    "actor": "ingest:etl-42",
    "actor_type": "ingest",
    "op_count": 17,
    "duration_ms": 41.2,        # the round trip of the commit call, in this process
    "reason": None,             # the server's reason text when the commit was refused
}
```

`engine.ledger.enable()` writes the genesis revision without calling the hook; `engine.ledger.commit()` calls it once
per call, whatever the outcome. A hook that raises is logged at WARNING and the commit is unaffected.

## A minimal exporter

The setup: an engine on a namespace IVG is installed in.

```python
import iris
from iris_vector_graph import IRISGraphEngine
from iris_vector_graph.ledger import Changeset

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn)
engine.initialize_schema()
```

The exporter counts outcomes and latency from the hook, and reads the totals from `ledger_stats` when it renders:

```python
import threading
from collections import Counter


class LedgerPrometheus:
    """Ledger events and counters in Prometheus text format."""

    def __init__(self, engine):
        self._engine = engine
        self._lock = threading.Lock()
        self._outcomes = Counter()
        self._duration_sum = 0.0
        self._duration_count = 0
        engine.ledger.register_metrics_hook(self.on_event)

    def on_event(self, event: dict) -> None:
        with self._lock:
            self._outcomes[event["outcome"]] += 1
            self._duration_sum += float(event["duration_ms"])
            self._duration_count += 1

    def outcomes(self) -> dict:
        with self._lock:
            return dict(self._outcomes)

    def render(self) -> str:
        lines = ["# TYPE ivg_ledger_commit_outcomes_total counter"]
        with self._lock:
            for outcome, n in sorted(self._outcomes.items()):
                lines.append(f'ivg_ledger_commit_outcomes_total{{outcome="{outcome}"}} {n}')
            lines += [
                "# TYPE ivg_ledger_commit_duration_ms_sum counter",
                f"ivg_ledger_commit_duration_ms_sum {self._duration_sum:.3f}",
                "# TYPE ivg_ledger_commit_duration_ms_count counter",
                f"ivg_ledger_commit_duration_ms_count {self._duration_count}",
            ]
        st = self._engine.ledger.stats()  # every process's counters, from ledger_stats
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

Enable the ledger, commit one changeset, and read what the exporter saw. The genesis revision is seq 1 and the
changeset seq 2. `commits_ok` counts every revision written, the genesis one included, so it is 2:

```python
exporter = LedgerPrometheus(engine)
engine.ledger.enable()

cs = Changeset(actor="ingest:etl-42", actor_type="ingest")
cs.create_node("pump-7", labels=["Equipment"])
engine.ledger.commit(cs).revision.seq
# → 2
```

```python
exporter.outcomes()
# → {'success': 1}
```

The rendered text without its comments and the latency lines, which differ run to run. `stats().rejections` holds
one counter per refusal kind, by its column name in `ledger_stats`:

```python
[line for line in exporter.render().splitlines() if not line.startswith(("#", "ivg_ledger_commit_duration"))]
# → ['ivg_ledger_commit_outcomes_total{outcome="success"} 1', 'ivg_ledger_head_seq 2', 'ivg_ledger_commits_ok_total 2', 'ivg_ledger_rejections_total{kind="rej_disabled"} 0', 'ivg_ledger_rejections_total{kind="rej_failed_op"} 0', 'ivg_ledger_rejections_total{kind="rej_idem_conflict"} 0', 'ivg_ledger_rejections_total{kind="rej_lock_timeout"} 0', 'ivg_ledger_rejections_total{kind="rej_size_limit"} 0', 'ivg_ledger_rejections_total{kind="rej_stale_head"} 0', 'ivg_ledger_rejections_total{kind="rej_strict_block"} 0', 'ivg_ledger_rejections_total{kind="rej_unknown_head"} 0']
```

Serve `render()` at `/metrics` from any HTTP handler (FastAPI, `http.server`). Keep the hook fast: it runs inside the
commit call. IVG's own HTTP server publishes graph and index gauges at its `/metrics`, not these
([HTTP server](ADMIN_API.md#get-metrics)).
