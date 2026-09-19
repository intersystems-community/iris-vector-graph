# ADR-0006 — Embedding identity lives in a registry, not the revision ledger

**Status**: Accepted
**Date**: 2026-09-19
**Specs**: 226 (embedding identity and width contract), 213 (graph revision ledger)
**Relates to**: ADR-0004

## Decision

The recorded expectation of which embedding model and vector width a table holds lives
in a dedicated table, `Graph_KG.embedding_registry` — one row per embedding table,
carrying mechanism, normalized model identifier, raw declared configuration, width,
dtype, the time the identity was set, and a reserved `graph_id VARCHAR(256) DEFAULT ''`.

It does **not** live in `^IVG.Ledger` / `Graph_KG.ledger_revisions`, and it does not
live in the per-row `metadata` column of the embedding tables.

## Context

`docs/KNOWN_ISSUES.md:367`, writing up this exact defect, concluded: "Detecting the
disagreement itself needs a stored expectation — the ledger is the natural place." That
line is the reason this ADR exists. Following it would put the contract somewhere it
cannot be enforced.

Three candidate homes were considered against one requirement: an embedding write must
be refusable _before_ it happens, on every installation, including one that has never
written an embedding and one that has no history feature enabled.

**The revision ledger.** Disqualified on four counts, each independently fatal:

- It is **opt-in and off by default**. An installation that never called `Enable()` has
  no ledger at all, so the contract would be absent exactly where an unguarded second
  writer is most likely.
- Embedding writes sit **entirely outside** it. `WriteRevision` is reached only from
  `Enable()` and `CommitInTxn()`; no embedding path passes through a changeset.
  Recording embedding identity there would mean inventing a revision for something that
  is not a structural mutation.
- `Graph_KG.ledger_stats` is a **namespace-wide singleton** and `ledger_revisions` has
  no `graph_id`, so it could not carry per-table identity today, nor per-graph identity
  later.
- It is a **history**, and this is a current-state assertion that must be read on every
  write. Deriving "what does this table hold now" by folding a revision log is the wrong
  access pattern for a hot-path check.

**The per-row `metadata` column.** Disqualified: it is per-row, so it cannot describe an
**empty** table — and an empty table already has an identity, because its column
declaration has a width. It also offers no place to refuse a _first_ writer's conflict,
and `kg_EdgeEmbeddings` has no `metadata` column at all, so the design would be
asymmetric across the three embedding tables from the start.

**A dedicated registry table.** Chosen. It exists on every installation that has a
schema, it can describe an empty table, it is a single-row read on the write path, it is
symmetric across all three embedding tables, and a conditional update gives an atomic
first-writer claim without coordinating with any other subsystem.

## Consequences

- The contract is available on installations with no ledger, which is most of them.
- Enforcement is a single indexed read per embedding write. Spec 226 requires it to
  raise, and requires the existing `try`/`except` around dimension migration in
  `_engine/schema.py` to be narrowed so it cannot convert a refusal into a log line.
- The registry is state, not history. It records when an identity was set, not every
  identity a table has ever had. If an audit trail of model changes is wanted later, it
  can be added to the ledger _as well_ — the two are not in competition.
- `graph_id` is reserved from day one so that per-graph embedding models (a named
  non-goal of spec 226, blocked by the absence of `graph_id` on the embedding tables and
  by `uq_nodes_nodeid UNIQUE (node_id)` on `Graph_KG.nodes`) can arrive without a schema
  migration. 3.2.0 treats the column as "all graphs" and nothing may read it otherwise.
- `docs/KNOWN_ISSUES.md:367` is corrected so nobody re-derives the ledger conclusion
  from the write-up of the bug.

## Alternatives considered

**Keep the expectation client-side, in `embedding_config` on the engine instance.** This
is the status quo and it is the defect: the value dies with the process, so two
concurrently configured engines cannot see each other. `_migrate_vector_dimensions`
compares each column against _the calling engine's_ configured width, which is why two
writers at different widths each conclude the schema agrees with them.

**Infer identity from the data.** Impossible for the case that matters. Two models of the
same width produce vectors that are indistinguishable by inspection — same length, same
dtype, same value range. Identity must be _declared_ and _recorded_; it cannot be
recovered.

**Rely on the column declaration alone.** It catches a width conflict (IRIS raises
`SQLCODE -104` at INSERT — see ADR-0005) and cannot catch a model conflict at equal
width, which is the silent, result-corrupting case.
