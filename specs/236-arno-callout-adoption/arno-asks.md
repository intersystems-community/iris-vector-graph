<!-- markdownlint-disable MD013 -->

# Draft: asks for the Arno maintainers (not sent)

Drafted 2026-10-04 from spec 236. Nothing has been sent or filed. Evidence is in
`specs/236-arno-callout-adoption/research.md` (§ Evidence gate results) and
`docs/KNOWN_ISSUES.md` (§ Arno callout defects). The longer list of eleven asks is
`docs/research/arno-rzf-paths-forward.md`.

Tested build: `arno-callout 1.1.0+3b2123b8e7bb`, arm64, IRIS 2026.3.0AI.113.0.

## What ivg does now

ivg routes an algorithm to a Native Reader only when the library's `capabilities` report
has a reader revision at or above a minimum ivg has proven against its own ObjectScript
implementation (`docs/arno-reader-proof.json`, written by `scripts/arno_reader_gate.py`).
On 1.1.0 no reader passed, so nothing is routed. The asks below are what would change
that, in the order they matter.

## Asks

1. **`wcc` and `cdlp` need to return a node-to-group map.** `kg_wcc_global` answers
   `{"components":1,"largest":[{"root":"p0","size":8}]}` and `kg_cdlp_global`
   `{"communities":2,"largest":[...]}` (revision 2 each). ivg's column shape is one row
   per node (`id, component_id`). A summary cannot be compared with, or replace, the
   per-node answer. Is a per-node variant planned, and what would its revision be?
2. **`pagerank` and `ppr` scores differ from ivg's by more than rounding.** PageRank on
   a directed path of 8 nodes: node `p7` 0.1877 (Arno) against 0.1223 (ivg); on a star
   and a multi-component graph the same kind of gap. They agree on a complete cluster
   and on a symmetric ring. PPR differs by about 3e-5 relative on a star and a complete
   cluster, by 15% on the path, and lists zero-score nodes ivg omits. Which handling of
   dangling nodes and of teleport mass does Arno implement? If it is the textbook one,
   ivg will document the difference instead; if a flag selects ivg's, we would use it.
3. **`subgraph` edges lose the predicate.** Edges come back as
   `{"src","dst","type":"REL"}`; ivg's edges are `{"s","p","o"}` and callers filter on
   `p`. Can the predicate be returned, and can an edge-type filter be passed?
4. **Namespace.** `init` and every `kg_*_global` call move the process to USER and read
   USER's `^KG`, whatever namespace the caller is in. ivg is namespace-aware. Can the
   readers take the namespace (or a global reference including it) and leave the
   caller's namespace alone?
5. **Directed betweenness.** `kg_betweenness_global_v` is undirected by construction.
   Is a directed option planned? ivg keeps its exact directed implementation until then.
6. **`kg_bfs_compute`.** It returned `[]` on the probed build. Has `test_neg_int_keys`
   been run? The hypothesis was a negative-integer subscript that `ns.keys()` does not
   return.
7. **`kg_bfs_global` argument size.** The whole adjacency is one `2b` argument, so it
   fails at 34,792 characters (`<OUT OF $ZF HEAP SPACE>`) and 65,536 (`<MAX $ZF
   STRING>`). ivg now gates before the call. A resident adjacency, or an EXSTR
   (`1j`/`2j`) sibling of `kg_bfs_global`, would remove the limit; is either on the
   roadmap, and what is the symbol?
8. **x86_64.** The x86_64 build has never run on IRIS from our side (CI does not fetch
   the library). It needs a manual enterprise-tier run on x86_64 hardware: install it with
   `scripts/enterprise-container.sh install-arno <path>` (set
   `IVG_ARNO_EXPECTED_SHA256`), then `IVG_REQUIRE_ARNO=1 python scripts/arno_reader_gate.py`.
   Who can schedule that run?

## What we can send with it

- `python scripts/arno_reader_gate.py` output for 1.1.0 (per fixture, per algorithm, with
  the reason for each failure).
- The five fixture graphs: `tests/fixtures/arno_reader_graphs.py`.
