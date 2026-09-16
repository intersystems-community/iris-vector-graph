# ADR-0003 — The graph key has two derivations, never a conversion

**Status**: Accepted
**Date**: 2026-09-14
**Specs**: 213 (revision ledger), 214 (structural adjacency), 223 (temporal index)
**Relates to**: ADR-0001

## Decision

Two incompatible internal forms of the default-graph key exist, and both remain:

| Tree            | Default graph | Named graph              |
| --------------- | ------------- | ------------------------ |
| `^KG(...)`      | integer `0`   | `$ZStrip(graphId, "*C")` |
| `^IVG.Ledger()` | `$Char(1)`    | `graphId`                |

`Graph.KG.GraphKey` owns both derivations and nothing else does:

- `ForIndex(graphId)` returns the `^KG` form.
- `ForLedger(graphId)` returns the `^IVG.Ledger` form.

Both derive from `graphId`. **There is no function that converts one key into the
other**, and none may be added. A caller about to touch a tree asks for the form
belonging to that tree; a caller holding only a key and not the `graphId` it came from
is a design error.

Both derivations reject `"0"` and `$Char(1)` as graph names.

## Context

`Ledger.GKey()` shipped in v2.16.0 mapping `""` to `$Char(1)`, while every `^KG`
writer independently mapped `""` to `0`. No rationale for the divergence was recorded
in the commit, a comment, a spec, a test, or ADR-0001 — which is scoped to `^KG` and
never mentions the ledger.

The divergence was not benign. `LedgerApply.OpCreateRel` derived a key with `GKey()`
and then used it to write `^KG("out"/"in")`, so every ledger-created default-graph edge
landed in a subtree no reader scans, making it invisible to BFS, centrality and
variable-length Cypher. Sixteen sites re-derived the `^KG` form inline, so no module
owned the mapping and nothing could catch the mismatch.

The `$Char(1)` form turns out to be defensible on grounds nobody wrote down. IRIS
canonicalizes the subscript `"0"` and the subscript `0` to the same key, so a graph
legitimately named `"0"` **is** the default graph under the `^KG` convention.
`$Char(1)` has no such collision. (`"00"` and `"0.0"` are not canonical numeric forms
and stay distinct, so the collision set is exactly the one name.) Recording this is
half the purpose of this ADR: the convention survives because it has a reason, and the
reason was nearly lost.

## Alternatives considered

**Unify on `0` now.** Cheaper in call sites — 11 ledger sites against 16 `^KG` sites
plus roughly 20 hardcoded `Set tGKey = 0` — and it fixes the leak for free. Rejected
for v3.1.0 because rewriting `^IVG.Ledger("tuple", …)` subscripts is a storage-layout
migration on a tree with no version stamp, and because unifying on `0` inherits the
`"0"` collision. Deferred to the next major version, where it must land together with
a `^IVG.Ledger("meta")` layout stamp.

**Unify on `$Char(1)`.** Avoids the collision but contradicts ADR-0001, which is a
committed storage contract, and touches far more code. Rejected.

**A conversion function** (`LedgerKeyToIndexKey`). Rejected on inspection: its only
possible caller is one that already picked the wrong key and needs to recover. That is
the defect, not the fix.

## Consequences

- No module may derive a graph key inline. `Graph.KG.GraphKey` is the only source.
- A graph may not be named `"0"`. Existing deployments may already contain one; the
  validator rejects new ones and `verify_graph` reports existing violations.
- Spec 214's prohibition on `\x01` in `graph_id` is now enforced rather than asserted.
- The two forms are permanently visible in one file, side by side, so the next reader
  cannot repeat the mistake by not knowing the other exists.
- Unifying the two remains desirable and remains a major-version change.
