# $ZF heap, argument size, and EXSTR for the Arno callout

Research spike for spec 236 (Arno adoption). Date: 2026-10-03. Container
measured: `ivg-iris-enterprise` (IRIS 2026.3.0AI build 113, ARM64 Linux,
Unicode build), library `/usr/irissys/mgr/libarno_callout.so` (5,043,920 bytes).

Labels: VERIFIED means a cited source or a measurement I ran (command and
result given). UNVERIFIED means inference or hearsay (source named).

## Question and short answer

What limits `$ZF` argument size, return size and heap, and is the Arno
maintainers' `OUT OF $ZF HEAP SPACE` at about 36K adjacency the `zfheap`
setting?

- Yes, it is `zfheap`. VERIFIED by measurement: the failure threshold is
  34,791 characters passing and 34,792 failing, and the documented heap
  arithmetic predicts that number to within 52 bytes.
- `kg_bfs_global` declares every string as a short string (`2b`/`2B`, at most
  65,535 UTF-16 units). The heap check fires first (above 34,791 chars at the
  default), then `<MAX $ZF STRING>` above 65,535 regardless of heap.
- Raising `zfheap` moves the first wall from 34,791 to 65,535 characters, with
  no restart. It cannot go past 65,535 for a `2b` argument. Past that, only
  EXSTR (`n`/`N` linkage, up to 3,641,144 characters) or passing data through
  globals or chunked appends works.
- Recommendation: spec 236 should treat "whole adjacency as one `$ZF`
  argument" as unsupported above about 34K characters, and should not depend
  on `zfheap`. See the recommendation section.

## Findings

### 1. The CPF parameter

- VERIFIED. Name `zfheap`, section `[config]`, value `ZFString,ZFSize`.
  `ZFString` is an integer 0-32,767 (default 0). `ZFSize` is an integer
  0-270,336 bytes (default 0).
  Source: <https://docs.intersystems.com/iris20262/csp/docbook/Doc.View.cls?KEY=RACS_zfheap>
- VERIFIED (same page). `ZFString` is the minimum number of characters reserved
  in the heap for each output string other than IRIS_EXSTR; 0 means the system
  default 32,767. `ZFSize` is the total heap in bytes for all purposes; 0 means
  the system default 135,168. Each input string is copied into the heap. Each
  output string allocates the larger of its length and `ZFString`. On a Unicode
  instance each character costs 2 bytes. "IRIS_EXSTR strings do not use the
  $ZF heap." InterSystems recommends leaving it at 0,0.
- VERIFIED (measured). The container has `zfheap=0,0` at
  `/usr/irissys/iris.cpf:919` (section `[config]` starts at line 891).
  `Config.config` exposes it as the properties `ZFString` and `ZFSize`
  (read with `Do ##class(Config.config).Get(.p)`; both 0). `IsUnicode()` is 1,
  so 2 bytes per character.
- VERIFIED (measured). Runtime change without a restart is possible, and takes
  effect for new processes. Docs say the same ("effective for new InterSystems
  IRIS processes", RACS_zfheap). See the measurements section for what I did
  and reverted.
- VERIFIED (measured). It applies to `$ZF(-5)` and `$ZF(-3)`: both failed and
  passed at the same sizes (table below).
- UNVERIFIED for `$ZF(-4)` and `$ZF(-6)`. `$ZF(-4)` loads and looks up
  libraries and takes no payload. `$ZF(-6)` raised `<VALUE OUT OF RANGE>`
  on the library id returned by `$ZF(-4,1,...)` (`-31541376`) and function id
  (56), for every payload including empty, so I could not exercise it. The docs
  describe the heap as a property of the callout gateway generally and do not
  split it by `$ZF` variant. Treat `-6` as sharing the heap until tested.

### 2. Argument and return size limits

- VERIFIED (source). Linkage types from the Callout Library docs
  (<https://docs.intersystems.com/iris20262/csp/docbook/Doc.View.cls?KEY=BGCL_library>,
  section "ZFEntry Linkage Options"):
  - `1b`/`b` (ZARRAY 8-bit), `2b`/`s` (ZWARRAY UTF-16), `4b`: short strings,
    up to 65,535 characters in the 2026.2 docs. The 2021.1 docs said 32,767.
  - `1j`/`j`, `2j`/`n`, `4j`: IRIS_EXSTR, "up to the string length limit"
    (3,641,144 characters in the 2021.1 docs; the 2026.2 page no longer gives a
    number for this row).
  - `c`/`1c`/`2c`/`w`/`4c`: null-terminated strings, truncated at the first
    `$CHAR(0)`.
  - Up to 32 parameters. Uppercase means in/out. One output parameter is the
    return value. Several outputs are returned as one comma-delimited string.
- VERIFIED (header). `iris-callin.h` in the container
  (`/usr/irissys/dev/iris-callin/include/iris-callin.h:71`) defines
  `IRIS_MAXSTRLEN` as 32767U, and the short string structs size `str[]` to it.
  That is the Callin constant, and Arno's own guard
  (`kg_ffi.rs:3497`, `IRIS_MAXSTRLEN - 100`) uses it as the safe result size.
- VERIFIED (measured). For a `2b` input the hard ceiling is 65,535 characters.
  65,536 raises `<MAX $ZF STRING>` at any heap size (tested with `ZFSize` =
  270,336). So the stated 65,535 is right for the input side on this build, and
  the rzf readme's "32KB limit" for short strings is conservative.
- VERIFIED (source, Arno bug report). Returns over the ceiling also raise
  `<MAX $ZF STRING>`. Arno's reader functions chunk large results through a
  global for this reason. See
  `~/ws/arno/BUG_REPORT_kg_bfs_global_maxstring.md`. I did not test a return
  over 32,667 characters myself. UNVERIFIED (Arno maintainers' bug report): the
  claim that the exception "corrupts the connection's driver state". My
  one-shot `iris session` processes would not show it.
- VERIFIED (source). `ZFString` does not limit input strings (RACS_zfheap). It
  reserves space for output strings. "An application must not return a string
  longer than this reserved space" (RACS_zfheap, Description). So a return
  above `ZFString` (default 32,767) is out of contract.

### 3. What the loaded Arno library declares

- VERIFIED (measured, `strings` on the `.so` in the container). The ZFEntry
  linkage for `kg_bfs_global` is `2b2b2b2b8i8i2B`: four short-string inputs
  (adjacency, result global, seed, predicates), two 64-bit ints, one short
  string output. So the adjacency is a `2b` argument, which explains both
  errors.
- VERIFIED (source). The Rust entry points use `ShortString`, built on
  `rzf`. `rzf`'s readme says `String` uses EXSTR (`IRIS_EXSTRP`, 3.6 MB) and
  `ShortString` uses `ZWARRAYP`
  (`~/.cargo/git/checkouts/rzf-36821d54475dd860/*/readme.md:764-787`).
  Its CI script confirms the linkages: `String` is `n` in / `N` out,
  `&[u8]` is `j`/`J`, `ShortString` is `2b`/`2B`
  (`.../ci/iris_cmd`, around lines 246-277).
  Arno's `kg_bfs_global`, `kg_bfs_compute`, `kg_adj_append` and others all
  declare `ShortString`
  (`~/ws/arno/iris-integration/arno-callout/src/lib.rs:290-297, 820-890`).
  Changing them to `String` is a one-line signature change in Arno per entry
  point. It changes the linkage string in the table to `n`/`N`.
- VERIFIED (measured). The loaded library exports `kg_bfs_global`,
  `kg_bfs_compute`, `kg_bfs_read_chunk`, `kg_adj_append`, `kg_wcc_run`,
  `kg_cdlp_run`, `kg_pagerank_run`, `kg_scc_run`, `kg_triangle_count_run`,
  `kg_kcore_run`, `kg_leiden_run`. It has no `capabilities` entry: the source
  has one (`lib.rs:78`) but the deployed build predates it.
  `$ZF(-4,3,...)` function ids: `version`=1, `kg_bfs_global`=56,
  `kg_bfs_compute`=57.
- VERIFIED (source). `kg_bfs_compute(adj_global, result_global, seed,
preds_json, max_hops, max_results)` reads the NKG-shaped global
  `^<adj_global>(-1, src, -(pred+1), dst)` with node names in
  `^<g>("$ND", idx)` and predicates in `^<g>("$LI", name)`, writes chunked
  results to `^<result_global>("bfs_result", n)` in 9,000-byte pieces, and
  hard-codes namespace `USER` (`kg_ffi.rs:3080-3160, 3547-3585`, and
  `write_result_chunks`).
- VERIFIED (measured). `kg_bfs_compute` does not work in this deployment.
  On a 50-node and a 5,000-node NKG-shaped scratch global in `USER` (with
  `$ND`, `$LI`), called via `$ZF(-5)` twice, it returned `[]` every time, for
  2 and 3,000 hops. The Arno source comment says reading globals from a
  `$ZF(-5)` context "silently returns empty" because the callin table is
  initialised only for `$ZF(-6)` (`kg_ffi.rs` doc comment above
  `ffi_kg_bfs_global`). That matches, and it matches the known `kg_*_global`
  reader defects in `docs/KNOWN_ISSUES.md:585-592`. UNVERIFIED: that it would
  work under `$ZF(-6)`; I could not call `-6` (previous section).
  Nothing in IVG calls `kg_bfs_compute`.

### 4. What the repo already knows

- VERIFIED (file). `docs/KNOWN_ISSUES.md:599-602`: 300 nodes (30,532 chars)
  pass, 600 (61,132) raise `<OUT OF $ZF HEAP SPACE>`, 1,000 (101,864) and up
  raise `<MAX $ZF STRING>`. All three fit the measured model below.
- VERIFIED (file). `NKGAccelTraversal.cls:341-363`: `BFSJson` builds
  the whole adjacency with `ExportAdjacencyWithPreds()` and passes it as one
  argument to `$ZF(-5, dllid, fnid, adjStr, ...)`, catching any error and
  falling back to `BFSFastJsonSorted`. The function id comes from
  `$ZF(-4,3,dllid,"kg_bfs_global")` (`NKGAccelLoader.cls:22` and
  `NKGAccelTraversal.cls:346`).
- Note on `ExportAdjacencyWithPreds` (`NKGAccelAdjacency.cls:239-284`): it
  already returns `CHUNKED:n` with `^||adjWP(i)` pieces above 3,000,000
  characters, so the string it builds is bounded by ObjectScript string limits,
  not by the callout.
- `docs/cypher-gap-recommendations.md:929-1020` (proposal for a
  `kg_bfs_global(global_name, src, preds_json, max_hops, ...)` that reads
  `^KG` itself) describes a global-reading signature that does not match the
  shipped adjacency-string signature. UNVERIFIED that any version of Arno
  ever shipped it in that form; the deployed library takes the string.

## Measurements

Command shape (run per size in a fresh `iris session IRIS -U USER` process so
a failure cannot affect the next):

```objectscript
Set dll=$ZF(-4,1,"/usr/irissys/mgr/libarno_callout.so")
Set fn=$ZF(-4,3,dll,"kg_bfs_global")
// adj = synthetic chain, "nNNNNNN"_$C(31)_"E"_$C(31)_"nNNNNNN"_$C(10) lines,
// truncated to N characters
Set r=$ZF(-5,dll,fn,adj,"IVGSpike","n     1","[]",2,0)
```

Default `zfheap=0,0` (ZFString 32,767 units reserved, heap 135,168 bytes):

| Payload chars | Entry                | Result                    |
| ------------: | -------------------- | ------------------------- |
|        10,000 | `kg_bfs_global` (-5) | OK, 111-char JSON         |
|        30,000 | `kg_bfs_global` (-5) | OK                        |
|        34,000 | `kg_bfs_global` (-5) | OK                        |
|        34,791 | `kg_bfs_global` (-5) | OK (largest passing size) |
|        34,792 | `kg_bfs_global` (-5) | `<OUT OF $ZF HEAP SPACE>` |
|        36,000 | `kg_bfs_global` (-5) | `<OUT OF $ZF HEAP SPACE>` |
|        65,000 | `kg_bfs_global` (-5) | `<OUT OF $ZF HEAP SPACE>` |
|        65,535 | `kg_bfs_global` (-5) | `<OUT OF $ZF HEAP SPACE>` |
|        65,536 | `kg_bfs_global` (-5) | `<MAX $ZF STRING>`        |
|       100,000 | `kg_bfs_global` (-5) | `<MAX $ZF STRING>`        |
|        34,000 | `kg_bfs_global` (-3) | OK                        |
|        36,000 | `kg_bfs_global` (-3) | `<OUT OF $ZF HEAP SPACE>` |
|        66,000 | `kg_bfs_global` (-3) | `<MAX $ZF STRING>`        |
|   any, incl 0 | `kg_bfs_global` (-6) | `<VALUE OUT OF RANGE>`    |

Model check. Heap need = 2 bytes x (input chars + other string args, 17 chars
here) + output reservation 2 x 32,767 = 65,534. At 34,791 chars:
2 x 34,791 + 65,534 = 135,116, which is 52 bytes under 135,168; the other
string arguments cost 34 bytes. At 34,792 it is over. The model fits the
measurement exactly, so I treat the mechanism as VERIFIED. It also fits the
repo's 4.1.1 numbers (30,532 passes; 61,132 fails) and the Arno maintainers'
"about 36K". On an 8-bit (non-Unicode) build the docs imply 1 byte per character,
so the heap threshold would be about 100,000 characters, above the 65,535 cap;
UNVERIFIED, I only have a Unicode build.

Raising the heap at runtime (the only change I made to the container):

1. Saved a copy of `/usr/irissys/iris.cpf` (md5 `34751b1a...`).
2. In `%SYS`: `Do ##class(Config.config).Get(.p)`, set `p("ZFSize")=270336`,
   `##class(Config.config).Modify(.p)`. Returned OK, no restart. The CPF line
   became `zfheap=0,270336`.
3. With `ZFSize=270336`:

   | Payload chars | Result             |
   | ------------: | ------------------ |
   |        34,792 | OK                 |
   |        65,535 | OK                 |
   |        65,536 | `<MAX $ZF STRING>` |

4. Reverted: set `ZFSize=0` and `ZFString=0`, `Modify` returned OK, CPF line is
   `zfheap=0,0` again, and `diff` against the saved copy reports the file
   identical. 36,000 chars failed with `<OUT OF $ZF HEAP SPACE>` again
   afterwards. Side effect: IRIS wrote a dated backup
   `/usr/irissys/iris.cpf_20261003` (the daily backup on first modification);
   it is identical to the current `iris.cpf`. I left it. I removed my temporary
   copy in `/tmp`.

Chunked appends (the `kg_adj_append` plus `*_run` route), default heap:

- 23 calls to `kg_adj_append`, each at most 30,000 chars: all OK, buffer
  686,670 chars.
- `kg_wcc_run` on that buffer (40,000 nodes, 80,000 edges): returned
  `{"components":1,...}`.

So 686,670 characters, 20 times the single-argument limit, went through at the
default heap. The buffer is a Rust static in the loaded library, so it lives
per IRIS process (the `.so` is loaded into each job). The format is the
integer form `src:dst,dst\n`, not the string-node `src<US>pred<US>dst\n` form
`kg_bfs_global` takes. There is no `kg_bfs_run`, so BFS cannot use this route
today.

Cleanup: scratch global `^IVGSpike` was killed (`$Data` = 0 afterwards). Only
`ivg-iris-enterprise` was touched. No other container, no commit, no push.

## Options for passing large payloads

- Status quo, one `2b` argument. Reach: 34,791 chars at default. Fails above
  with a catchable error; `BFSJson` already falls back to ObjectScript.
- Raise `zfheap` ZFSize to 270,336. Reach: 65,535 chars. Needs a CPF change
  and new processes; InterSystems advises 0,0; the hard 65,535 cap remains.
- `kg_adj_append` chunks then `*_run`. Reach: hundreds of thousands of chars.
  Works today (measured) but only for wcc, cdlp, pagerank, scc, triangle,
  kcore, leiden. No BFS.
- `kg_bfs_compute` (globals in, globals out). Not limited by `$ZF`. Returned
  `[]` under `$ZF(-5)` here; hard-codes `USER`; tied to the NKG layout.
- EXSTR (`n`/`N`) signatures in Arno. Reach: 3,641,144 chars. Needs an Arno
  source change and a rebuilt `.so`; no heap use; the 3.6 MB string must still
  be built in ObjectScript.
- ObjectScript BFS (`BFSFastJsonSorted`). Unlimited; slower, but it is already
  the fallback and the proven answer.

Trade-offs in words:

- Raising `zfheap` is a deployment setting that every target instance must
  carry. It only doubles the headroom. IVG cannot assume it, and a wrong value
  shows up as a runtime error, not at install.
- Chunked append is the approach Arno already ships for the `*_run` family.
  It needs a `kg_bfs_run` to help BFS. The buffer is per process and must be
  drained by the matching `*_run` call, so a failed run leaves stale state
  until the next `take`.
- EXSTR is the designed answer to "big payload". In Rust it is `rzf::String`
  instead of `ShortString`; the linkage becomes `n` in and `N` out. It also
  lifts the result side, so the CHUNKED result workaround could go for most
  sizes. It needs the Arno maintainers; IVG cannot do it.
- Globals (`kg_bfs_compute`) avoid the size limit entirely in design but, in
  the deployed build, do not return data under `$ZF(-5)`.

## Recommendation for spec 236

1. State the size limit as a requirement on behaviour, not on a setting: the
   Arno BFS path must return the same answer as the ObjectScript path at every
   graph size, and must not raise for a graph whose exported adjacency exceeds
   the callout limit. The current 4.1.1 code does this by catching the error.
   Keep that fallback and test it at 34,791 and 34,792 characters (the
   boundary, on a default-heap container) and at 65,536+.
2. Do not write the number 32,767 or 65,535 as "the" limit. The effective
   limit of the shipped signature is `(ZFSize - 2 x ZFString_reserved) / 2`
   minus the other arguments, capped at 65,535. At defaults that is 34,791
   characters on a Unicode build. Say that it depends on `ZFSize` and
   character width, and that no IVG code may rely on `zfheap` being raised.
3. Do not put a `zfheap` change in scope. It is per-instance, needs new
   processes, is discouraged by InterSystems, and gains only 2x.
4. In scope for IVG: a pre-check that measures the exported adjacency length
   before calling `$ZF`, and skips the call (going straight to the fallback)
   above a threshold, so a large graph does not pay the export and a failed
   call every time. Log or report "callout skipped: payload N > limit" so it is
   visible.
5. Out of scope, request from the Arno maintainers (separate work in their
   repo): (a) switch `kg_bfs_global` and the other large-argument entries from
   `ShortString` to `String` (EXSTR, `n`/`N`), or (b) add `kg_bfs_run` after
   `kg_adj_append`, or (c) fix global reads under `$ZF(-5)` so `kg_bfs_compute`
   works. Option (a) is the smallest change and removes the heap dependence.
6. Record that `zfheap`'s documented behaviour is "effective for new
   processes": IVG background jobs started before a change keep the old heap.

## Open questions

- Does `$ZF(-6)` share the same heap, and why does it reject the library id
  from `$ZF(-4,1,...)` with `<VALUE OUT OF RANGE>`? Is the id negative, or does
  `-6` want a 1-based sequence number (the docs say `-5`/`-6` take sequence
  numbers)? Needs a test with the right id, which decides whether
  `kg_bfs_compute` works at all (Arno says global reads need a `-6`
  context).
- Does the return side cap at 32,767 (`ZFString` default) or at 65,535 for a
  `2B` output, and what happens between? Not measured; needs an Arno entry that
  returns a controlled length.
- EXSTR behaviour in practice: is there an IRIS kernel limit for an `n` input
  below 3,641,144 on this build (the 2026.2 docs say only "the string length
  limit")? Does a 3 MB argument build in ObjectScript need `LongStrings`? Not
  tested because the deployed library has no EXSTR entry point.
- 8-bit (non-Unicode) builds: expected heap threshold about 100K characters,
  past the 65,535 cap, so only `<MAX $ZF STRING>` would appear. Not measured.
- The bug report's claim that `<MAX $ZF STRING>` corrupts a long-lived
  connection (ivg Python DBAPI session) was not reproduced; worth a test with
  a persistent connection before the spec claims the fallback is safe there.
- The deployed `.so` has no `capabilities` entry. Spec 236's version gate
  cannot read it from this build.
