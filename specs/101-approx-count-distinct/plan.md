# Plan: Spec 101 — APPROX_COUNT_DISTINCT for Multi-Hop Cypher Traversal

## Technical Context

| Concern | Decision |
|---|---|
| HLL implementation | New `UpdateStructuralHLL` in GraphIndex.cls — reuses SHA1 pattern from TemporalIndex.cls, 256 registers keyed on integer NKG indices |
| Global storage | `^NKG("$agg", sIdx, pIdx, "hll")` — separate from temporal `^KG("tagg")`, integer-keyed for fast $Order merge |
| Cypher syntax | `approx_count_distinct(b)` new aggregate — detected in translator RETURN clause scan |
| Engine routing | Pre-BFS detection in `_execute_var_length_cypher`, calls `CountDistinctKHop` via `_call_classmethod_large` |
| Write paths | InsertIndex + BulkIngestEdges + BuildNKG — all three maintain sketches |
| Register count | 256 — compile-time `#DEFINE HLL_REGISTERS 256`, 6.5% std error |
| Error surfacing | `QueryMetadata.warnings` — `"approx_count_distinct: HLL-256, std_error=6.5%, registers=256"` |

## Constitution Check

- Test-first: failing e2e tests written before implementation ✓
- Enterprise IRIS verification required for ^NKG writes ✓
- No SQL for adjacency — uses ^NKG global directly ✓
- Engine methods only — no raw global access from Python ✓

## Phase 0: Research Findings

### HLL-256 via SHA1 in ObjectScript

The existing `UpdateHLL` in TemporalIndex.cls uses `$SYSTEM.Encryption.SHA1Hash` with
16 registers. Extending to 256 registers: first byte of SHA1 selects register (mod 256),
second byte determines leading-zero count. Same algorithm, wider register array.

$ListBuild with 256 elements is within IRIS limits (~1KB per list). Merge is 256 element
reads + 256 conditional writes per node in frontier — for a 29-node frontier that's
7,424 operations, ~1ms in ObjectScript.

### Cypher Translator Pattern

The translator already handles `COUNT(DISTINCT ...)` via regex in `_execute_var_length_cypher`.
`approx_count_distinct(...)` detection follows the same pattern, placed **before** the
existing `count_match` check so it intercepts first.

### BuildNKG Integration

BuildNKG already loops `^KG("out", 0, s, p, o)` to write `^NKG(-1/2/3)`. Adding
`UpdateStructuralHLL(sIdx, pIdx, oIdx)` in that loop adds ~5μs per edge. For 54M
LDBC SF10 edges, that's +270s to BuildNKG — acceptable for a one-time batch rebuild.
Incremental InsertIndex writes add negligible overhead (<1μs per edge at current load).

### BulkIngestEdges Integration

BulkIngestEdges is embedded Python writing to `^KG("out"/"in")` directly. It does NOT
call InsertIndex and does NOT currently write `^NKG` at all — that's why BuildNKG is
needed after bulk loads. For HLL consistency, we add `UpdateStructuralHLL` inside the
BulkIngestEdges Python loop via `iris.gref("^NKG")` direct writes (same pattern as
the existing gref edge writes).

## Phase 1: Data Model

### Global Structure

```
^NKG("$agg", sIdx, pIdx, "hll") = $ListBuild(r1, r2, ..., r256)
```

- `sIdx`: Integer node index (from `^NKG("$NI", nodeId)`)
- `pIdx`: Integer predicate index (from `^NKG("$LI", predicate)`)
- `"hll"`: Fixed subscript key
- Value: $ListBuild of 256 integers (leading-zero counts per register)

Each entry is ~256 bytes. Total for LDBC SF10 with 5 predicates per node: 80MB.

### New Methods

#### GraphIndex.cls — UpdateStructuralHLL

```objectscript
ClassMethod UpdateStructuralHLL(sIdx As %Integer, pIdx As %Integer, oIdx As %Integer)
{
    Set hashInput = oIdx _ ""
    Set hashBytes = $SYSTEM.Encryption.SHA1Hash(hashInput)
    Set regIdx = ($ASCII(hashBytes, 1) # 256) + 1
    Set b1 = $ASCII(hashBytes, 2)
    Set lz = 1
    If b1 = 0 { Set lz = 9 } Else {
        While (b1 # 2) = 0 { Set lz = lz + 1, b1 = b1 \ 2 }
    }
    Set hll = $Get(^NKG("$agg", sIdx, pIdx, "hll"), ..EmptyHLL())
    If lz > $List(hll, regIdx) {
        Set $List(hll, regIdx) = lz
        Set ^NKG("$agg", sIdx, pIdx, "hll") = hll
    }
}
```

#### GraphIndex.cls — EmptyHLL, MergeHLL, EstimateHLL

```objectscript
ClassMethod EmptyHLL() As %List [ CodeMode = expression ]
{ $ListBuild(0,0,...) } // 256 zeros

ClassMethod MergeHLL(ByRef merged As %List, other As %List)
{
    For i=1:1:256 {
        If $List(other,i) > $List(merged,i) Set $List(merged,i) = $List(other,i)
    }
}

ClassMethod EstimateHLL(hll As %List) As %Integer
{
    // alpha_256 = 0.7213 / (1 + 1.079/256)
    Set alpha = 0.7182
    Set Z = 0
    For i=1:1:256 { Set Z = Z + (1 / (2 ** $List(hll,i))) }
    If Z = 0 Return 0
    Return $Number(alpha * 256 * 256 / Z, 0)
}
```

#### NKGAccel.cls — CountDistinctKHop

```objectscript
ClassMethod CountDistinctKHop(srcId As %String, predsJson As %String = "",
                               maxHops As %Integer = 2, direction As %String = "both") As %String
{
    Set sIdx = ##class(Graph.KG.GraphIndex).GetNodeIdx(srcId)
    If sIdx = "" Return "{""estimate"":0,""registers"":256,""std_error"":0.065}"

    Set preds = ""
    If (predsJson '= "") && (predsJson '= "null") && (predsJson '= "[]") {
        Set preds = ##class(%DynamicArray).%FromJSON(predsJson)
    }

    Set merged = ##class(Graph.KG.GraphIndex).EmptyHLL()
    Kill seen, frontier
    Set seen(sIdx) = ""
    Set frontier(sIdx) = ""

    For hop = 1:1:maxHops {
        Kill nextFrontier
        Set s = ""
        For {
            Set s = $Order(frontier(s))
            Quit:s=""
            // Merge HLL sketches for each predicate
            If $IsObject(preds) {
                For pi = 0:1:(preds.%Size()-1) {
                    Set pName = preds.%Get(pi)
                    Set pIdx = ##class(Graph.KG.GraphIndex).GetLabelIdx(pName)
                    If pIdx = "" Continue
                    Set hll = $Get(^NKG("$agg", s, pIdx, "hll"))
                    If hll '= "" Do ##class(Graph.KG.GraphIndex).MergeHLL(.merged, hll)
                }
            } Else {
                Set pIdx = ""
                For {
                    Set pIdx = $Order(^NKG("$agg", s, pIdx))
                    Quit:pIdx=""
                    Set hll = $Get(^NKG("$agg", s, pIdx, "hll"))
                    If hll '= "" Do ##class(Graph.KG.GraphIndex).MergeHLL(.merged, hll)
                }
            }
            // Expand next frontier via ^NKG adjacency
            If direction = "out" || (direction = "both") {
                Set pIdx2 = ""
                For {
                    Set pIdx2 = $Order(^NKG(-1, s, pIdx2))
                    Quit:pIdx2=""
                    Set oIdx = ""
                    For {
                        Set oIdx = $Order(^NKG(-1, s, pIdx2, oIdx))
                        Quit:oIdx=""
                        If '$Data(seen(oIdx)) {
                            Set seen(oIdx) = ""
                            Set nextFrontier(oIdx) = ""
                        }
                    }
                }
            }
        }
        If '$Data(nextFrontier) Quit
        Merge frontier = nextFrontier
    }

    Set estimate = ##class(Graph.KG.GraphIndex).EstimateHLL(merged)
    Return "{""estimate"":"_estimate_",""registers"":256,""std_error"":0.065}"
}
```

### Cypher Translator

Add detection in `iris_vector_graph/cypher/translator.py` RETURN clause scan:

```python
# Detect approx_count_distinct(x) AS col
APPROX_COUNT_RE = re.compile(
    r'\bapprox_count_distinct\s*\(\s*\w+\s*\)\s+AS\s+(\w+)',
    re.IGNORECASE
)
```

### Engine Routing

In `_execute_var_length_cypher`, add before the `count_match` check:

```python
approx_match = APPROX_COUNT_RE.search(cypher_query)
if approx_match:
    col_name = approx_match.group(1)
    raw = str(_call_classmethod(
        self.conn, "Graph.KG.NKGAccel", "CountDistinctKHop",
        source_id, predicates_json, max_hops, vl.get("direction", "both"),
    ))
    result = _json.loads(raw)
    return {
        "columns": [col_name],
        "rows": [[result["estimate"]]],
        "sql": f"CountDistinctKHop({source_id}, {predicates_json}, {max_hops})",
        "params": [],
        "metadata": QueryMetadata(
            warnings=[
                f"approx_count_distinct: HLL-{result['registers']}, "
                f"std_error={result['std_error']*100:.1f}%"
            ]
        ),
    }
```

Note: `approx_count_distinct` is detected from the **raw Cypher string**, not from the
SQL stub — because the translator currently produces a `COUNT(DISTINCT ...)` SQL stub
for this pattern, which would be indistinguishable from the exact path. The raw Cypher
detection happens before translation.

## Phase 2: Task Decomposition

See tasks.md.
