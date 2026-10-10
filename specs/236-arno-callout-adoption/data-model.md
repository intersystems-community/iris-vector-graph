<!-- markdownlint-disable MD013 MD033 MD060 -->

# Data Model: Arno Callout Adoption

## Reader Report (runtime, from the library)

| Field   | Type            | Notes                                    |
| ------- | --------------- | ---------------------------------------- |
| version | string          | `arno-callout <semver>+<sha>`; logs only |
| readers | map name -> int | reader revision; absent name = no reader |

Empty map when the library is absent, old, or the report is unparseable.

## Proof Record (`docs/arno-reader-proof.json`)

| Field                             | Type   | Notes                                               |
| --------------------------------- | ------ | --------------------------------------------------- |
| library.version                   | string | build the record was proven on                      |
| library.sha256                    | string | per-platform checksums (arm64, x86_64 if run)       |
| proven_at                         | date   | gate run date                                       |
| algorithms.<name>.min_revision    | int    | routing minimum                                     |
| algorithms.<name>.fixtures        | list   | fixture ids attempted (at least 5)                  |
| algorithms.<name>.dropped         | list   | fixtures dropped, with reason (e.g. unstable Owner) |
| algorithms.<name>.repeats         | int    | repeated native calls (5)                           |
| algorithms.<name>.cdlp_similarity | number | cdlp only; threshold used                           |

Validation: `min_revision` is a positive integer; an algorithm absent from the
file is not proven. Loaded once per process; missing or invalid file = all
Owners, with a logged diagnostic.

## Routing Decision (derived)

`route(algorithm, reader_report, proof, graph="") -> "native" | "owner"`.
Native only when `reader_report.readers[algorithm] >= proof.algorithms[algorithm].min_revision`
the algorithm is in the in-scope set, and `graph` is the default graph. Betweenness is never native.

## Cached Result (`^IVG.AlgoCache`)

Key: `(graphKey, algorithm, paramHash)`. Value: `token` (the `^KG("__version")`
value at compute time) and `payload` (JSON of the answer).

| Rule         | Detail                                                                   |
| ------------ | ------------------------------------------------------------------------ |
| hit          | stored token equals current token                                        |
| miss         | different token, or absent entry                                         |
| bypass       | algorithm not cacheable (subgraph), payload too large, or cache disabled |
| invalidation | token change; erase paths kill the graph subtree                         |
| paramHash    | sha256 of canonical JSON of parameters, sorted keys                      |

State transitions: absent -> stored (compute) -> stale (token moves) -> stored
(recompute overwrites). No deletion needed for correctness.

## Graph-Version Token

`^KG("__version")`: a namespace-wide integer counter incremented with
`$Increment` by every `^KG` mutation path (audit matrix in
`research-token-audit.md`). Never decreases; never reset by loaders after the
fixes.
