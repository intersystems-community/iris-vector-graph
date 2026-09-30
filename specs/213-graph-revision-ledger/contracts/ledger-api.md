<!-- markdownlint-disable MD013 -->

# Contract: Python Ledger API

Public surface added to `iris_vector_graph`. Everything here is additive;
nothing existing changes signature (Principle II).

## Entry point

```python
engine = IRISGraphEngine(conn, namespace="USER")
ledger = engine.ledger            # GraphLedger, lazily constructed, one per engine
```

`engine.ledger` exists whether or not a ledger is enabled. Methods that need an
enabled ledger raise `LedgerNotEnabledError`.

## GraphLedger

| Method                                                                                                                                                                                                             | Returns                                                                                                                     | Errors                                                                                                                                                                                                                          | Spec                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| `enable(*, strict: bool = False, max_ops: int = 50_000, recon_bound: int = 250_000) -> RevisionInfo`                                                                                                               | genesis (first call) or current head (idempotent); on re-enable after disable returns the adoption revision or current head | `LedgerLockTimeoutError`                                                                                                                                                                                                        | FR-001, FR-001b, FR-002 |
| `disable() -> None`                                                                                                                                                                                                | –                                                                                                                           | – (idempotent)                                                                                                                                                                                                                  | FR-001a                 |
| `set_strict(flag: bool) -> None`                                                                                                                                                                                   | –                                                                                                                           | `LedgerNotEnabledError`                                                                                                                                                                                                         | FR-042                  |
| `head() -> RevisionInfo`                                                                                                                                                                                           | current head, read-only                                                                                                     | `LedgerNotEnabledError`                                                                                                                                                                                                         | FR-003                  |
| `commit(changeset: Changeset) -> CommitResult`                                                                                                                                                                     | new revision, or original outcome with `replayed=True`                                                                      | `LedgerDisabledError`, `EmptyChangesetError`, `ChangesetTooLargeError`, `StaleHeadError`, `UnknownRevisionError`, `IdempotencyConflictError`, `ChangesetOperationError`, `LedgerLockTimeoutError`, `LedgerTransactionOpenError` | FR-005 to FR-021        |
| `history(*, after_seq: int = 0, limit: int = 100, actor: str \| None = None, actor_type: str \| None = None, since_ms: int \| None = None, until_ms: int \| None = None, descending: bool = False) -> HistoryPage` | `revisions: list[RevisionInfo]`, `next_after_seq: int \| None`                                                              | –                                                                                                                                                                                                                               | FR-024, FR-025          |
| `get_revision(revision_id: str) -> Revision`                                                                                                                                                                       | `info: RevisionInfo`, `records: list[MutationRecord]` (ordered)                                                             | `UnknownRevisionError`                                                                                                                                                                                                          | FR-026                  |
| `diff(from_id: str, to_id: str) -> Diff`                                                                                                                                                                           | sorted net, lifecycle-aware                                                                                                 | `UnknownRevisionError`                                                                                                                                                                                                          | FR-027 to FR-029a       |
| `reconstruct(revision_id: str, *, stream: bool = False) -> GraphState \| Iterator[Entity]`                                                                                                                         | logical graph at revision                                                                                                   | `UnknownRevisionError`, `ReconstructionTooLargeError` (only when `stream=False`)                                                                                                                                                | FR-035, FR-036          |
| `export_reconstruction(revision_id: str, path: str) -> ExportSummary`                                                                                                                                              | streams to NDJSON; never bounded                                                                                            | `UnknownRevisionError`                                                                                                                                                                                                          | FR-037                  |
| `verify(*, adopt: bool = False, actor: str = "system:ledger-verify") -> VerificationReport`                                                                                                                        | equal or diverges; adoption revision if requested and needed                                                                | `LedgerNotEnabledError`, commit errors when `adopt=True`                                                                                                                                                                        | FR-038, FR-039          |
| `stats() -> LedgerStats`                                                                                                                                                                                           | counters from `Graph_KG.ledger_stats`                                                                                       | –                                                                                                                                                                                                                               | FR-050, FR-051          |
| `register_metrics_hook(fn: Callable[[dict], None]) -> None`                                                                                                                                                        | –                                                                                                                           | –                                                                                                                                                                                                                               | FR-052                  |

## Changeset builder

```python
from iris_vector_graph.ledger import Changeset

cs = Changeset(actor="ingest:etl-42", actor_type="ingest",
               message="nightly equipment sync",
               source={"system": "cmms", "batch": "2026-09-05"},
               correlation_id="run-7f3a",
               expected_head=head.revision_id,
               idempotency_key="cmms-2026-09-05-batch-1")

cs.create_node("pump-7", labels=["Equipment"], properties={"status": "ok"})
cs.upsert_node("tank-2", labels=["Vessel"])
cs.add_label("pump-7", "Critical")
cs.remove_label("pump-7", "Critical")
cs.set_property("pump-7", "status", "warn")
cs.remove_property("pump-7", "status")
r = cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"weight": "1.0"}, graph=None)
cs.upsert_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"capacity": "100"})
cs.set_qualifier(r, "capacity", "120")               # r is an OpRef resolved at commit
cs.set_qualifier(("pump-7", "FEEDS", "tank-2", None), "verified", "true")
cs.replace_qualifiers(stmt_id="1042", qualifiers={"capacity": "120"})
cs.remove_qualifier(stmt_id="1042", key="verified")
cs.delete_relationship(stmt_id="1042")
cs.delete_node("pump-7", mode="strict")              # or mode="detach"

result = engine.ledger.commit(cs)
result.revision.revision_id, result.revision.seq, result.replayed, result.stmt_ids
```

Rules the builder enforces client-side (server re-validates):

- Operation count ≥ 1; reserved property names rejected immediately.
- Relationship addressing is exactly one of `stmt_id`, a 4-tuple, or an
  `OpRef` returned by an earlier `create_relationship`/`upsert_relationship`
  in the same changeset.
- `canonical_json()` and `fingerprint()` are deterministic (R14).

## Wire format to the server

`Changeset.canonical_json()` conforms to `contracts/changeset.schema.json`.
The server method returns a JSON object conforming to the `CommitResponse`
definition in the same schema (`ok`, `revision`, `replayed`, `stmt_ids`) or
`CommitError` (`error`, `op_index`, `reason`, `head`).

## Strict-mode guard on existing APIs

When the ledger is enabled with `strict=True`, these existing calls raise
`LedgerStrictModeError` and change nothing: `create_node`, `store_node`,
`create_edge`, `store_edge`, `set_edge_weight`, `delete_edge`, `delete_node`,
`bulk_create_nodes`, `bulk_create_edges`, `bulk_ingest_edges`,
`bulk_delete_nodes`, `drop_graph`, `reify_edge`, `delete_reification`,
`materialize_inference`, `retract_inference`, `import_graph_ndjson`,
`restore_snapshot`, `load_networkx`, `execute_cypher` with any updating clause,
and the store's `write_nodes`, `write_edges`, `delete_nodes`, `delete_edges`.
`create_edge_temporal(..., graph=...)` succeeds but skips the structural
mirror. `BulkLoader` and raw SQL are not guarded (documented).

## Status extension

`engine.status().ledger` is a `LedgerStatus` dataclass: `state`, `strict`,
`head_seq`, `head_id`, `commits_ok`, `rejections: dict[str, int]`,
`last_verify_ms`, `last_verify_result`. Present with `state="never-enabled"`
when no ledger exists, so existing `status()` consumers see one new field.

## Errors

All raise `LedgerError` subclasses (see data-model.md). `StaleHeadError.current_head`
carries the head observed at serialization. `ChangesetOperationError.op_index`
is zero-based (FR-008).
