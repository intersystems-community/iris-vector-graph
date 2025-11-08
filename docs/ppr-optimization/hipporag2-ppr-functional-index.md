# HIPPORAG‑2: Personalized PageRank with IRIS **Functional Index + Globals**

**Owner:** Data Platforms (HIPPO‑RAG‑2)  
**Purpose:** Fast, zero‑copy *personalized PageRank (PPR)* over a graph stored in SQL tables while **chasing IRIS globals in place**. We use a **Functional Index** (callbacks in *Embedded Python*) to maintain adjacency & degree structures in globals, so the PPR kernel never rebuilds edges.

---

## 0) Context & Goals

- Workload: Biomedical/Scientific inquiry (citations, entity graphs, knowledge edges).  
- Constraints: Keep *authoritative* graph in **SQL** (`Edge(Src,Dst)`), but run graph math via **globals** for speed.  
- Goal: **Pointer‑chase** adjacency directly from globals; keep working state in **process‑private globals** (`^||`) so nothing persistent is touched; converge PPR quickly.

Why this design:
- Globals are IRIS’s native multidimensional store; pointer‑chasing with `$ORDER` (or `iris.gref().order()`) avoids heap allocations and joins.  
- A **Functional Index** updates our *graph helper globals* automatically on `INSERT/UPDATE/DELETE`, keeping them consistent with SQL.

References (overview):
- Functional Index base class `%Library.FunctionalIndex`: https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Library.FunctionalIndex  
- Embedded Python **Global Reference API** (`iris.gref`): https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GEPYTHON_reference_gref  
- Process‑Private Globals (`^||`): https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GCOS_ppg  
- Compiled storage dictionary (`%Dictionary.CompiledStorage`): https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Dictionary.CompiledStorage  
- Building indexes / rebuild lifecycle: https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GSOD_indexes

---

## 1) Storage Model (authoritative)

We store:
- `HIPPO.REG2.Edge(Src INT, Dst INT)` — directed edges.
- Optional `Node(Id INT, ...)` table for accounting/metadata (not required by PPR kernel).

### Where the data really lives
- For **class‑defined** persistent tables with the defaults, row data lives under a **data global** (for example `^Package.ClassD(RowID)=$lb(...)`), and index entries live under **index globals** (for example `^Package.ClassI("BySrc", key, id)=""`).  
- For **SQL‑created** tables (typical `CREATE TABLE`), IRIS often uses **hashed global names** and may place **each index in its own global location**. The **actual locations** are discoverable via `%Dictionary.CompiledStorage` (properties like `DataLocation`, `IndexLocation`, and entries in `Indices(Name, Location)`).  
  - Doc: https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Dictionary.CompiledStorage

We do **not** depend on a specific storage name: our functional index reads columns from the DML callback arguments and writes to *our own* graph helper globals with a stable name.

---

## 2) Functional Index Design (the key piece)

We define a functional index **type class** that intercepts DML and maintains these helper globals:

- `^HIPPOREG2PPR("out", src, dst) = 1` — adjacency (outbound)  
- `^HIPPOREG2PPR("in",  dst, src) = 1` — adjacency (inbound, optional but handy)  
- `^HIPPOREG2PPR("deg", src) = outdegree` — per‑node outdegree

**Why a functional index?**  
IRIS invokes the index’s **filing interface** on row insert/update/delete, so we can incrementally keep adjacency/degree consistent with SQL — no cron rebuilds or application‑side triggers required.

Functional index contract (overview):  
- Base: `%Library.FunctionalIndex`  
- Filing callbacks: `InsertIndex`, `UpdateIndex`, `DeleteIndex` (+ optional `PurgeIndex`, `SortBeginIndex`, `SortEndIndex` for rebuilds)  
- Docs: https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Library.FunctionalIndex  
- Extra reading (with `%FIND` pattern): https://dev.to/intersystems/functional-indices-for-lightning-fast-queries-on-many-to-many-relationship-tables-1cjk

Callbacks are implemented in **Embedded Python**, using `iris.gref()` to access globals:  
- Global API ref: https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GEPYTHON_reference_gref  
- Community walk‑through: https://community.intersystems.com/post/working-globals-embedded-python

---

## 3) Schema & Index: code (ObjectScript + Embedded Python)

### 3.1 `Edge` class (owns the index)

```objectscript
Class HIPPO.REG2.Edge Extends %Persistent [ DdlAllowed, Final ]
{
  Property Src As %Integer;
  Property Dst As %Integer;

  /// Functional index maintains ^HIPPOREG2PPR helpers
  Index PPR_Adj On (Src, Dst) As HIPPO.REG2.PPR.Index;
}
```

> Marking the class **`Final`** keeps the callback argument order simple (no extra clustered columns are injected ahead of your properties). See functional index docs for argument ordering details.  
> Index lifecycle & rebuilds: https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GSOD_indexes

### 3.2 Functional index type class

```objectscript
Class HIPPO.REG2.PPR.Index Extends %Library.FunctionalIndex
{

  /// INSERT callback
  ClassMethod InsertIndex(pID As %RawString, pArg... As %Binary) [ Language = python ]
  {
      import iris
      g = iris.gref('^HIPPOREG2PPR')
      args = list(pArg)
      if len(args) < 2: return
      src, dst = args[0], args[1]
      if src is None or dst is None: return

      # adjacency
      g.set(1, ['out', src, dst])
      g.set(1, ['in',  dst, src])

      # outdegree++
      cur = g.get(['deg', src]); cur = int(cur) if cur is not None else 0
      g.set(cur + 1, ['deg', src])
  }

  /// UPDATE callback: new values first, then old values
  ClassMethod UpdateIndex(pID As %RawString, pArg... As %Binary) [ Language = python ]
  {
      import iris
      g = iris.gref('^HIPPOREG2PPR')
      args = list(pArg)
      if len(args) < 4: return
      nsrc, ndst, osrc, odst = args[0], args[1], args[2], args[3]

      if osrc is not None and odst is not None:
          g.kill(['out', osrc, odst])
          g.kill(['in',  odst, osrc])
          cur = g.get(['deg', osrc]); cur = int(cur) if cur is not None else 0
          g.set(max(0, cur - 1), ['deg', osrc])

      if nsrc is not None and ndst is not None:
          g.set(1, ['out', nsrc, ndst])
          g.set(1, ['in',  ndst, nsrc])
          cur = g.get(['deg', nsrc]); cur = int(cur) if cur is not None else 0
          g.set(cur + 1, ['deg', nsrc])
  }

  /// DELETE callback
  ClassMethod DeleteIndex(pID As %RawString, pArg... As %Binary) [ Language = python ]
  {
      import iris
      g = iris.gref('^HIPPOREG2PPR')
      args = list(pArg)
      if len(args) < 2: return
      src, dst = args[0], args[1]
      if src is None or dst is None: return

      g.kill(['out', src, dst])
      g.kill(['in',  dst, src])
      cur = g.get(['deg', src]); cur = int(cur) if cur is not None else 0
      g.set(max(0, cur - 1), ['deg', src])
  }

  /// Optional: purge helpers (used by index rebuild)
  ClassMethod PurgeIndex() [ Language = python ]
  {
      import iris
      iris.gref('^HIPPOREG2PPR').kill()
  }

  ClassMethod SortBeginIndex() [ Language = python ] { return }
  ClassMethod SortEndIndex(pCommit As %Integer = 1) [ Language = python ] { return }

  /// SQL‑visible helper (debug)
  ClassMethod OutDeg(nodeId As %Integer) As %Integer [ Language = python, SqlProc ]
  {
      import iris
      v = iris.gref('^HIPPOREG2PPR').get(['deg', nodeId])
      return int(v) if v is not None else 0
  }
}
```

### Notes
- Filing interface + optional `%FIND` patterns are documented here:  
  `%Library.FunctionalIndex`: https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Library.FunctionalIndex  
  DEV article with `%SQL.AbstractFind`: https://dev.to/intersystems/functional-indices-for-lightning-fast-queries-on-many-to-many-relationship-tables-1cjk
- Python global API (`iris.gref`): https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GEPYTHON_reference_gref

---

## 4) PPR Kernel (pointer‑chasing + process‑private state)

We keep working vectors (`x`, `p`, `deg`, etc.) in **process‑private globals** `^||ppr($JOB, ...)` so they vanish when the job ends and never require journaling.

**Process‑private globals doc:** https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GCOS_ppg

Sketch (ObjectScript‑style):

```objectscript
Class HIPPO.REG2.PPR.Runner
{

ClassMethod Run(seedsCSV As %String = "", alpha As %Float = 0.85, maxIter As %Integer = 50, tol As %Float = 1e-8) As %Status
{
  KILL ^||ppr($JOB)
  // Build node set & degrees from ^HIPPOREG2PPR("out", src, dst) and ^HIPPOREG2PPR("deg", src)
  NEW src,dst SET src=""
  FOR  SET src=$ORDER(^HIPPOREG2PPR("out",src)) QUIT:src=""  {
    SET ^||ppr($JOB,"nodes",src)=""
    SET ^||ppr($JOB,"deg",src)=$GET(^HIPPOREG2PPR("deg",src),0)
    SET dst=""
    FOR  SET dst=$ORDER(^HIPPOREG2PPR("out",src,dst)) QUIT:dst=""  {
      SET ^||ppr($JOB,"nodes",dst)=""
    }
  }

  // init personalization p and rank x ...
  // power iteration:
  //   sum inbound using ^HIPPOREG2PPR("in", v, u) or
  //   sum outbound contributions by scanning neighbors

  QUIT $$$OK
}

}
```

Or in Embedded Python, use `iris.gref('^HIPPOREG2PPR').order([...])` to iterate.

---

## 5) Optional: reading storage globals directly

If you prefer not to maintain `^HIPPOREG2PPR`, you can chase the **storage index globals** the class compiler created for `Edge`:

- For a classic class layout, you’ll see structures like `^YourClassI("BySrc", src, rowId)=""` and `^YourClassI("ByDst", dst, rowId)=""`.  
- For SQL‑created tables, use `%Dictionary.CompiledStorage` to **resolve the actual global names/locations** at runtime and then `$ORDER` through those nodes.

Dictionary class doc (properties like `DataLocation`, `IndexLocation`, `Indices`):  
https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Dictionary.CompiledStorage

---

## 6) Lifecycle

1. **Define** `HIPPO.REG2.Edge` and `HIPPO.REG2.PPR.Index` (above).  
2. **Deploy** the index (DDL).  
3. If the table already has rows:  
   - Call `PurgeIndex()` to clear helpers.  
   - Run an **index rebuild** on `HIPPO.REG2.Edge` so IRIS invokes `InsertIndex` for each row.  
   - Index lifecycle docs: https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GSOD_indexes
4. PPR jobs can now run; helpers are kept up‑to‑date on every DML.

---

## 7) Comparison: Globals‑only vs SQL‑only vs FunctionalIndex‑augmented

| Approach | What you chase | Pros | Cons | When to use |
|---|---|---|---|---|
| **Globals‑only** (storage indices) | Class storage index globals (e.g., `^ClassI("ByDst",dst,rowId)`) | Zero duplication; no extra maintenance | Storage names differ for SQL‑created tables; denormalizing degrees requires a pass; brittle if schema storage changes | You control class defs & storage; want minimal moving parts |
| **SQL‑only** | `SELECT`/JOINs over `Edge` | Familiar; optimizer help | PageRank steps cause many joins/aggregations; memory shuffles; slower | Small graphs or infrequent PPR |
| **FunctionalIndex‑augmented** (**recommended**) | **`^HIPPOREG2PPR`** (out/in/deg) | O(1) degree; fast adjacency; always in sync via callbacks; no sql join cost per step | Extra global space; callback code to maintain | HIPPO‑RAG‑2 online scoring; frequent PPR; low‑latency graph ops |

---

## 8) Testing & Validation

- Insert `Edge` rows; verify helpers:  
  - `SELECT HIPPO_REG2_PPR_Index_OutDeg(?)` (or call `OutDeg(?)`)  
  - Inspect `^HIPPOREG2PPR("out",src,*)` and `("in",dst,*)`
- Update and delete edges; assert adjacency/degree reflect changes.  
- Rebuild index; ensure `PurgeIndex()` + rebuild restores helpers.

---

## 9) Risks & Mitigations

- **Hot‑spot nodes**: popular `src` can concentrate updates. *Mitigate*: shard workloads; batch inserts; consider background rebuild for bulk loads.  
- **Callback failures**: exceptions in Python stop DML. *Mitigate*: keep callbacks minimal; add try/except + logging; multi‑phase bulk load (`PurgeIndex()` + rebuild).  
- **Storage drift** (if chasing compiler globals): prefer the functional‑index helpers to decouple from storage layout.

---

## 10) Reference Links (clickable)

- `%Library.FunctionalIndex` (contract & callbacks):  
  https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Library.FunctionalIndex
- Global Reference API (`iris.gref`):  
  https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GEPYTHON_reference_gref
- Process‑Private Globals (`^||`):  
  https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GCOS_ppg
- Index lifecycle & rebuilds:  
  https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GSOD_indexes
- Compiled storage dictionary (`%Dictionary.CompiledStorage`):  
  https://docs.intersystems.com/irislatest/csp/documatic/%25CSP.Documatic.cls?CLASSNAME=%25Dictionary.CompiledStorage
- Functional indices + `%FIND` (`%SQL.AbstractFind`) example:  
  https://dev.to/intersystems/functional-indices-for-lightning-fast-queries-on-many-to-many-relationship-tables-1cjk
- Community: Working with globals in Embedded Python:  
  https://community.intersystems.com/post/working-globals-embedded-python