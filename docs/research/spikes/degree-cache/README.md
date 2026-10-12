<!-- markdownlint-disable MD013 MD060 -->

# Spike: a degree that is counted, without paying for it on every read

Question: [PERF.md](../../alt-impl/PERF.md) found that ADR-0010 ("degree is counted, never stored") makes some readers much
slower (`KHopCount` 270x to 18,000x, degree centrality 2.7x, PageRank +16 %). Can a better design keep the exactness and
remove most of the cost? Scratch namespace `IVGPERFB`, same generated graph as the performance run (499,984 edges, 48,889
sources, one 100,000-edge hub), load about 12. Probe classes here: `Deg.cls`, `Bench.cls`, `Stress.cls`; raw output in
`bench.out.txt`.

## The design under test

Writers do not keep a counter. They bump a per-source **generation** (`$Increment(^KG("gen", g, s))`) and take a **shared**
lock `Lock +^W(g)#"S"` (compatible with every other writer, released at commit or rollback). A degree read looks at a cache
entry `(gen, count)` for the source: if the generation matches, return the count; otherwise count the `out` entries and store
the result, **but only if** (a) the exclusive no-wait probe `Lock +^W(g)#"E":0` succeeded, meaning no writer was in flight,
and (b) the generation did not move during the count. Why each part exists:

- `$Increment` is safe for the generation, unlike for a counter: a rolled-back write leaves an over-bumped generation, which
  only makes a cache entry look stale (the version token argument).
- A reader in another process can see an uncommitted edge. Without the probe it caches that count at the bumped generation,
  the writer rolls back, and the entry stays valid and wrong. The shared/exclusive pair closes that window without ever making
  two writers wait for each other.

## Results

Correctness (the part that decides soundness):

| Test                                                                                                                                                 | Result                                                                                               |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Cross-process race, generation cache **without** the probe: reader caches during an uncommitted write, writer rolls back                             | **wrong after rollback** (3, truth 2)                                                                |
| Same race **with** the shared lock and probe                                                                                                         | correct after rollback (2); the uncommitted read is not cached                                       |
| 6,000 random put / remove / rolled-back put / rolled-back remove steps (2 seeds), cache vs recount for four nodes and one predicate after every step | 0 mismatches                                                                                         |
| One transaction of 200,000 guarded writes                                                                                                            | 0.23 s, commits, no lock-table problem                                                               |
| Four writers, 40 transactions of 50 writes each, overlapping sources in opposite orders                                                              | 0.10 s, 8,000 of 8,000 edges, no deadlock (the lock-based counter in the first spike never finished) |

Cost (microseconds or milliseconds as marked; three interleaved rounds for writes):

| Item                                                 | Today (stored counters)  | Counted (as built) | Generation cache, warm                              | Cold (first touch) |
| ---------------------------------------------------- | ------------------------ | ------------------ | --------------------------------------------------- | ------------------ |
| Edge write, µs per edge (`out`, `in`, version + ...) | 1.07 to 1.31 (deg, degp) |                    | 0.94 to 0.96 (gen only); 1.15 to 1.24 with the lock |                    |
| `KHopCount`-style, 20 seeds x 5, ms                  | 0.14                     | 49.9               | 0.06                                                | 9.8                |
| One hub call (100,000 entries), µs                   | 0.09                     | 7,607              | 0.48                                                | 7,600 once         |
| One 200-entry call, µs                               | 0.06                     | 17.4               | 0.43                                                | once               |
| Degree loop over all 48,889 sources, ms              | 12.3                     | 93.6               | 28                                                  | 119 to 141 once    |

The warm cache numbers include a class-method call per read; the stored column is an inline `$Get`, so the comparison
overstates the cache's cost by roughly the call overhead (about 0.3 to 0.4 µs a call).

## Reading

- The idea is sound. It keeps the property ADR-0010 wants (a degree that cannot drift, because it is always either a
  validated cache or a recount) and removes the reader cost: `KHopCount` goes from 18,000x slower to about 5x a bare read at
  sub-microsecond cost, the centrality-style loop from 7.6x to 2.3x of the stored loop, and these are the call-overhead
  figures, not walks.
- Writes do not get slower: generation only is 15 to 25 % cheaper than today's two counters; with the shared lock it is level
  with today's cost.
- The costs it does bring: the first read of each source after a write recounts it (the cold column), cache entries are one
  per source (and per predicate asked), and a reader writes the cache, so a read-only replica must fall back to counting.
- Rule it imposes on every writer: any write to `out`/`in` must bump the generation and hold the shared lock, or kill the cache
  subtree. This is only practical once there is one writer module, which is the `adjacency-owner` package. The allow-listed
  direct writers (Eraser, TemporalIndex shadow, TraversalBuild rebuild, snapshot restore) become the invalidation checklist,
  and the single-writer guard test can enforce it.
- Not covered: the cache living in a non-journaled global (journal volume), a multigraph `#` multiplicity change (it goes
  through the same writer so it bumps the generation, but no test here), and behaviour on mirrored or sharded instances.
