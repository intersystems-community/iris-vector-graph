# ADR-0004 — Erasure removes content, not history

**Status**: Accepted
**Date**: 2026-09-14
**Specs**: 213 (revision ledger), 214 (structural adjacency), 223 (temporal index)
**Relates to**: ADR-0002

## Decision

`Graph.KG.Eraser` removes graph content from every store that holds it — relational
rows, `^KG` adjacency, the temporal index, `^NKG`, and the ledger's live statement and
tuple indexes — inside a single transaction it owns itself.

It does **not** remove `^IVG.Ledger("rec", seq, ordinal)`. The operation records of an
erased graph survive erasure.

The invariant erasure guarantees is therefore narrower than "no trace remains": **no
live statement outlives its content.** After an erasure, no `^IVG.Ledger("stmt", …)`
row claims to be live without a row in the store to back it.

Erasure either completes or leaves the graph untouched. A partial erasure is a defect,
not an outcome, and the Eraser raises rather than returning a count on failure. A
returned `0` means nothing matched.

## Context

A revision is a set of mutations committed atomically, and nothing constrains a
Changeset to a single graph. `^IVG.Ledger("rec", seq, ordinal)` carries the graph in
value position 7 — per _record_, not per revision — so one revision's ordinals can
belong to several graphs. Pruning `rec` by graph would punch holes in the ordinal
sequence of revisions that also describe graphs the caller did not ask to erase,
destroying their history and their replayability.

The statement and tuple indexes are different in kind: they are per-statement and
wholly attributable to one graph, and leaving them behind is what actually breaks. A
stale `^IVG.Ledger("stmt", id)` with an empty `deletedSeq` asserts that a statement is
live when its row is gone, and the next ledger operation touching it fails with
`"statement N has no row in the store (ledger inconsistency)"`.

`^IVG.Ledger("tuple", s, p, o, graphKey)` places the graph key last and so cannot be
`$Order`ed or `Kill`ed by graph — the shape ADR-0001 rejects for `^KG`, for this exact
reason. The Eraser therefore makes one pass over `("stmt")`, which carries the graph in
value position 4, and derives each tuple key from the statement it is removing. One
scan, both trees consistent.

The transaction lives in ObjectScript rather than Python because the Native API rides
the same connection as the DBAPI cursor, so `Kill` and SQL `DELETE` share one
transaction context — but `$TLevel` is invisible from Python, leaving a caller unable to
reason about the state after a mid-flight failure. `Graph.KG.Ledger` already owns its
own `TSTART/TCOMMIT` with an `If $TLevel > 0 { TROLLBACK }` unwind; the Eraser borrows
that pattern rather than inventing one.

## Alternatives considered

**Refuse to erase when the ledger holds revisions.** Clean, and makes erasure and
recorded history mutually exclusive by construction. Rejected: it makes `erase_graph`
unusable on precisely the ledger-enabled deployments that most need it.

**Erase stores and mark the ledger stale**, pruning lazily on the next
`LedgerGenesis` rebuild. Rejected: it defers a known-inconsistent state into a rebuild
that may never run. This is the `_nkg_dirty` pattern, and `drop_graph` is the evidence
against it.

**Prune `rec` by graph.** Rejected as described above — it destroys history the caller
did not ask to erase.

**Best-effort erasure**, SQL transactional with globals cleaned afterward and a dirty
flag on failure. This is what `drop_graph` does today; it is the source of the drift
this work exists to remove.

## Consequences

- Erasure is not a privacy primitive on its own. A caller who must remove all trace of
  a graph, including its operation history, must additionally purge the ledger — which
  removes other graphs' history too. That trade-off is deliberate and should be
  surfaced in any documentation that mentions erasure and data deletion together.
- `verify_graph(graph)` reporting nothing does not imply the ledger has no record of
  that graph. It implies no live statement is stranded.
- Every deletion path funnels through the Eraser's transaction, so a write path that
  touches one store without the others is a bug rather than a documented mode.
- `bulk_delete_adjacency` is removed rather than deprecated: its contract was to
  produce drift.
