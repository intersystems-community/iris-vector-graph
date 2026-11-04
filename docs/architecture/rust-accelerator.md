<!-- markdownlint-disable MD013 -->

# The Arno library

For contributors and curious operators: what the optional Rust library does inside IVG, how IVG loads it, and when an
algorithm actually uses it. IVG works without it; every algorithm and traversal has an ObjectScript implementation,
the **Owner**, which is the reference the library is held to.

The library (`arno-callout`, a `libarno_callout.so` built from the separate Arno project) is **not distributed with
IVG**. A contributor installs it into the test container with `scripts/enterprise-container.sh install-arno`, which
checks the arm64 build's sha256 (arno-callout 1.2.1 by default).

## How IVG calls it

```text
IRISGraphEngine ─ IRISSQLStore ─ Native API ─▶ Graph.KG.ArnoAccel (ObjectScript)
                                                  │  $ZF(-4) load, $ZF(-5) call
                                                  ▼
                                              libarno_callout.so (Rust) ─ reads ^NKG, returns JSON
```

- **Configuration is per namespace, the handle per process.** `^ArnoAccel("lib_path")` names the library file.
  `Graph.KG.ArnoAccel.Load(path)` loads it with `$ZF(-4,1)`, records the path, and keeps the handle and the function
  IDs in the process-private `^||ArnoAccel`. Any other process loads the configured path on its first call
  (`IsAvailable`, `CallFn`). Before 4.6.0 the handle was kept in `^ArnoAccel`, shared, although it is valid only in the
  process that loaded it.
- **The namespace is restored after every call.** The library's entry points move the process to USER; since 4.4.1
  `ArnoAccel` puts `$NAMESPACE` back after each one, and the entry points that read USER's globals refuse elsewhere.
- **The library reads `^NKG`**, the integer-keyed copy of the edges that `sync()` rebuilds
  ([globals](ARCHITECTURE.md#nkg-the-integer-keyed-copy)). `^NKG` has no graph dimension, so a walk confined to a
  named graph never uses the library ([known issue](../KNOWN_ISSUES.md#named-graphs-are-not-accelerated-by-arno)).
- `IVG_DISABLE_ARNO=1` keeps the Python side from trying the library for triangle count, components, k-core and
  Leiden.

## When an algorithm uses it: the reader gate

For PageRank, personalized PageRank, WCC, CDLP and subgraph extraction the library is used only when it is a proven
**Native Reader** for that algorithm: the loaded build reports a reader revision at or above the one recorded in
`docs/dev/arno-reader-proof.json`, which `scripts/arno_reader_gate.py` writes after comparing the library's answers
with the Owner's on a set of graphs (`iris_vector_graph/arno_readers.py`). Otherwise the Owner answers. Betweenness is
never routed to the library: its betweenness is undirected and the Owner's is exact and directed.

With arno-callout 1.2.1 and the 4.6.0 proof (revision 2), **only global PageRank passes**; personalized PageRank, WCC,
CDLP and subgraph answer from ObjectScript. While the default graph is in multigraph mode the Owner answers PageRank
too, because the library weighs a parallel edge once.

The proof is read from the file `IVG_ARNO_PROOF` names, or else from the copy the wheel carries
(`iris_vector_graph/_data/arno-reader-proof.json`), or, in a source checkout, from `docs/dev/arno-reader-proof.json`.
Before 4.6.0 the wheel did not carry it, so an installed IVG found no proof and routed every algorithm to its Owner.

Other library entry points (`KhopSampleJson`, `RandomWalkJson`, `NeighborAggJson`, the two-hop statistics, BFS over
`^NKG`) are used when the library is loaded, without a gate. `NKGAccel.BuildNKGRust` refuses since 4.6.0: `^NKG` is
built in ObjectScript (`Graph.KG.TraversalBuild.BuildNKG`).

## Seeing what is in use

`print(engine.status())` shows `Acceleration: Arno <version>` or `Acceleration: ObjectScript (Arno not loaded)`, and the
status object's `arno.loaded` says the same. The store logs which path answered each algorithm call. Measured differences between
the paths are in [Benchmarks](../BENCHMARKS.md#graph-algorithms).
