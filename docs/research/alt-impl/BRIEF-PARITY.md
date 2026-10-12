<!-- markdownlint-disable MD013 MD060 -->

# Parity round: shared brief

Read [BRIEF.md](BRIEF.md) first (its rules still apply, with the container exceptions below), then
[PERF.md](PERF.md), [ADOPTION.md](ADOPTION.md), `docs/adr/0008-0010`, `docs/research/spikes/degree-cache/README.md` and the
three probe classes beside it (`Deg.cls`, `Bench.cls`, `Stress.cls`). Branch from `alt-integration` (it is a local branch; a
worktree sees it): your first action is `git switch -c alt/<your-package> alt-integration`.

## The goal of this round

The alternative path must match the baseline's (4.2.3, commit 6f17be0f) performance on every reader and write path, then be
benchmarked across the repo's whole benchmark breadth, against Neo4j and others. The PERF.md run showed where it is slower:
`KHopCount` 270x to 18,000x, degree centrality 2.7x, `PageRankGlobalJson` +16 %, `Build2HopStats` +35 %. All of it comes from
counting degree on every read. The spike proved a **generation-validated degree cache** removes the cost without giving up
exactness. This round builds that cache for real.

## The design (contract; do not change it, other agents code against it)

Stores, all under `^KG` with the graph key first (ADR-0001), derived, never authoritative:

- `^KG("gen", graph, src)`: integer generation of a source. Bumped with `$Increment` by every write that adds or removes an
  `out` entry of `src` (a rolled-back write over-bumps, which is harmless). Absent means 0.
- `^KG("degc", graph, src, key)`: `$ListBuild(gen, count)`. `key` is the predicate, or `$Char(1)` for "all predicates".
- Lock name `^IVG.AdjW(graph)` (no data lives there; only the lock name matters). Writers hold it in **shared** mode, readers
  probe it in **exclusive** mode with no wait.

Writer rule (every code path that sets or kills an `^KG("out", ...)` entry): `Lock +^IVG.AdjW(graph)#"S":5`, make the change, bump
`^KG("gen", graph, src)`, `Lock -^IVG.AdjW(graph)#"S"` (inside a transaction the unlock is deferred to commit or rollback, which
is what makes the probe work). Shared locks do not conflict with each other, so writers never wait for writers.

Reader rule, `Graph.KG.Adj.Degree(pGraph, pSrc, pPred = "")` (the signature stays as the earlier package defined it):

1. `gen = +$Get(^KG("gen", g, s))`; if the cache entry's generation equals `gen`, return its count.
2. Otherwise probe: `Lock +^IVG.AdjW(g)#"E":0`; remember `$Test`; if it succeeded release at once (`Lock -`).
3. Count the `out` entries (a parallel edge counts its `"#"` multiplicity, as `Adj.Degree` already does).
4. Store `(gen, count)` only if the probe succeeded AND `+$Get(^KG("gen", g, s)) = gen` still holds. Always return the count.

Both the order (read `gen` before the probe) and the "unchanged after" check are required for soundness; the spike shows the
failure when the probe is missing. `Graph.KG.Adj.NextSource` and `HasEdges` stay as they are.

## Container exceptions for this round (replacing BRIEF.md rule 4 for the agents named below)

Only the two **parity** agents and the **harness** agent may touch the container, and only `ivg-iris-enterprise`, only in a
namespace of their own that they create (named in their prompt), never in USER, IVGTEST, IVGSEC, IVGREKEY, IVGLEGACY, IVGFIX,
IVGPERFA, IVGPERFB or IVGPERFC. Do not delete database files or directories (that previously broke the container); to clean
up, kill your globals and leave the namespace, and say so. Recipe for creating a namespace and a database: read
`~/.claude/projects/-Users-tdyar-ws-iris-vector-graph/memory/scratch_namespaces_enterprise.md`, and the shell history in
`/tmp/perfdrv` for how the perf agent made `IVGPERFA`. Run ObjectScript with
`docker exec -i ivg-iris-enterprise iris session IRIS -U <NAMESPACE> < script`. No timing runs in this round: the lead runs all
timed benchmarks serially later, because concurrent load ruins them.

## Report

Same format as BRIEF.md, plus: **LIVE CHECKS RUN** (command and result) and **LIVE CHECKS NOT RUN**.
