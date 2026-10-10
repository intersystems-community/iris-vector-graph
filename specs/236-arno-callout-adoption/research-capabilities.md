# Research: calling arno-callout `capabilities` from ivg

All measured 2026-10-03 in `ivg-iris-enterprise` (IRIS 2026.3.0AI.113.0, aarch64, USER namespace).
1.1.0 .so sha256 verified `da28ca71...ab03` on host and after `docker cp` inside the container.

## Answer

`capabilities` is called exactly like `version`: `$ZF(-4,3,dllid,"capabilities")` then `$ZF(-5,dllid,funcid)`.
`$ZF(-3,path,"capabilities")` also works. `$ZF(-6,...)` does not work for ANY arno function, including `version`
(not a capabilities problem).

## Arno source (VERIFIED by reading)

- `~/ws/arno/iris-integration/arno-callout/src/lib.rs:78` `#[rzf] pub fn capabilities() -> ShortString`, no args.
- `contract_tests.rs:31` descriptor `("capabilities", "2B")`: zero args, returns 2-byte-length string.
- `READERS` are only populated with feature `zf_global`.
- Smoke recipe: `~/ws/arno/iris-integration/arno-callout/tests/e2e_release_smoke.sh`:
  `Set f=$ZF(-4,3,id,"capabilities") Write "CAPS|",$ZF(-5,id,f),!` after `Set id=$ZF(-4,1,path)`. It never uses -6.
  Expected readers count 11, wcc=2, cdlp=2, ppr=2.

## Call forms on the 1.1.0 .so (VERIFIED)

Session: `docker exec -i ivg-iris-enterprise iris session IRIS -U USER`, `Set id=$ZF(-4,1,"/tmp/libarno_callout_110.so")`.

| Form | Result |
| --- | --- |
| `$ZF(-4,1,path)` | id `-31670400` (NEGATIVE, nonzero; failure is 0, so `dllid = 0` tests are fine) |
| `$ZF(-4,3,id,"capabilities")` | funcid `2` |
| `$ZF(-5,id,f)` | OK: `{"version":"arno-callout 1.1.0+3b2123b8e7bb","readers":{"betweenness":1,"bfs":1,"cdlp":2,"kcore":1,"leiden":1,"pagerank":1,"ppr":2,"scc":1,"subgraph":1,"triangle_count":1,"wcc":2}}` |
| `$ZF(-3,path,"capabilities")` | OK, same JSON |
| `$ZF(-6,id,f)` | `<VALUE OUT OF RANGE>` |
| `$ZF(-6,id,"capabilities")` | `<VALUE OUT OF RANGE>` |
| `$ZF(-6,id,$ZF(-4,3,id,"version"))` | `<VALUE OUT OF RANGE>` (so -6 is unusable with a -4 dllid for every function) |
| `$ZF(-5,id,$ZF(-4,3,id,"version"))` / `$ZF(-3,path,"version")` | `arno-callout 1.1.0+3b2123b8e7bb` |

Why -6 fails: UNVERIFIED. The docs search (Algolia) returned 0 hits, so no citation. Hypothesis: `$ZF(-6,libid,funcid,...)`
takes a library id from IRIS's own `$ZF` library table (small integer), not the opaque negative handle that `$ZF(-4,1)` returns.
Irrelevant to ivg: use -4/-5, as the whole existing loader does.

## Old build (/tmp/libarno_callout.so, 1.0.0+a353ad4537a8, sha 35ea20ae...) (VERIFIED)

- `$ZF(-4,1,old)` loads fine (id -32129152).
- `$ZF(-4,3,old,"capabilities")` does NOT return 0. It THROWS `<ILLEGAL VALUE>` in the `Set` statement. So `f` stays undefined.
  Error text: `<ILLEGAL VALUE>` (not `<FUNCTION>`/`<ZFUNCTION>`).
- `$ZF(-3,old,"capabilities")` also `<ILLEGAL VALUE>`.
- Unguarded `$ZF(-5,o,f)` after that is `<UNDEFINED>` on `f`; `$ZF(-5,o,0)` is `<VALUE OUT OF RANGE>`.
- Implication: the existing idiom `Set fid = $ZF(-4,3,dllid,name)` then `If fid = 0` never sees 0 for a missing name. It needs Try/Catch.
- Catchable in ObjectScript Try/Catch: yes (`e.Name="<ILLEGAL VALUE>"`).
- Via Python Native API `classMethodValue` with an uncaught throw: `RuntimeError: <ILLEGAL VALUE>Raw+2^IVGSpike.Caps.1`.
- The failure leaves no bad state: in the same session/connection a later `$ZF(-4,3,old,"version")` + `$ZF(-5)` returned `arno-callout 1.0.0+a353ad4537a8`;
  over Python, Raw(old) raised, then Version(old) OK, Safe(old) returned `{}`, Safe(new) returned the JSON, repeated Safe(old) `{}`.

## Two libraries in one process (VERIFIED)

- Old and new loaded in the same process get distinct dllids (-32129152 vs -32129208). Function ids for the same name differ (`kg_bfs_global` 56 vs 57).
- Both respond independently: old `version` = 1.0.0, new = 1.1.0, interleaved. `$ZF(-4,2,new)` returned 0 and the old lib kept working; `$ZF(-4,2,old)` returned 0.
- Caveat (UNVERIFIED): loading the 1.1.0 lib in the production-path process was only tested in throwaway `iris session` processes and one Native API connection.
  ivg's loader stores dllid in `^||NKGAccel("dllid")` and `^ArnoAccel("dllid")`. A probe that loads a second copy must not overwrite those.

## Recommended implementation for ivg

Add to `Graph.KG.ArnoAccel` a read-only method that does NOT touch `^ArnoAccel` or `^||NKGAccel`:

```objectscript
/// Reader revisions of the loaded callout build, as JSON. "{}" when the build
/// predates the capabilities entry (1.0.x) or no library is loaded.
ClassMethod ReaderRevisions() As %String
{
    Set tOut = "{}"
    Try {
        Set tId = $Get(^ArnoAccel("dllid"), 0)
        If tId = 0 Quit
        Set tF = $ZF(-4, 3, tId, "capabilities")   // throws <ILLEGAL VALUE> on 1.0.x
        If tF = 0 Quit
        Set tOut = $ZF(-5, tId, tF)
    } Catch tEx {
        Set tOut = "{}"
    }
    Quit tOut
}
```

- Prefer reusing the dllid the loader already recorded (`^ArnoAccel("dllid")` / `^||NKGAccel("dllid")`), so the process keeps one library.
  If the process has not loaded yet, mirror `IsAvailable`/`Load` first (`..IsAvailable()`), or use the `^||NKGAccel("libPath")` fallback chain from `NKGAccelTraversal.BFSJson`.
  Note `$ZF(-4,1,samePath)` was not tested for returning the same id on repeat.
- Python: `json.loads(_call_classmethod(conn, "Graph.KG.ArnoAccel", "ReaderRevisions"))`, where `_call_classmethod` is `iris.createIRIS(conn).classMethodValue(...)` (`iris_vector_graph/schema.py:93`).
  Empty dict means "old build, route every algorithm to the pure ObjectScript path".
  Compare per reader: `readers.get("wcc", 0) >= 2`, etc.
- The version string in the JSON equals the `version` entry output, so one call also yields identity.
- The Try/Catch (not a `= 0` test) is load-bearing for old builds. The empty-string check in `IsAvailable` works only because `version` exists in both.

## Verified vs unverified

VERIFIED (above): -4/-5 and -3 forms on 1.1.0; -6 failing for all names; old-build `<ILLEGAL VALUE>` and its catchability in ObjectScript and Python;
no bad state after failure; two dllids coexisting; unload order independence; sha256 of the test .so.
UNVERIFIED: why -6 fails; a repeated `$ZF(-4,1)` id semantics; behaviour inside a TCP job process that never ran Load;
behaviour of the x86_64 build; the Community container.

## Surprises

1. Missing symbol THROWS at `$ZF(-4,3,...)`, it does not return 0. Any `fid = 0` fallback in the loader is dead code for missing names.
2. `$ZF(-4,1)` ids are large negative numbers; only 0 means failure.
3. The earlier spike's `<VALUE OUT OF RANGE>` was caused by using `$ZF(-6)`, which fails even on `version`. Nothing to do with the old library.
4. `/tmp` in the container is sticky: files `docker cp`'d as root could only be removed with `docker exec -u root`.

## Cleanup (VERIFIED)

Unloaded both dlls; dropped scratch class `IVGSpike.Caps` (`%ExistsId` = 0); removed `/tmp/libarno_callout_110.so` and `/tmp/IVGSpikeCaps.cls` from the container.
`/tmp/libarno_callout.so` untouched (sha still 35ea20ae...). `docker/enterprise/libarno_callout.so` and `~/ws/arno` untouched. No other repo files changed.
