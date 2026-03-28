# Data Model: NICHE Knowledge Graph Integer Index

**Feature**: 028-nkg-integer-index | **Date**: 2026-03-28

## ^NKG Global Structure

### Edge Storage (integer-encoded)

```
^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight    // out-edge
^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight    // in-edge
^NKG(-3, sIdx) = degree                      // node degree
```

Encoding: label index N → subscript -(N+1). Node indices are positive integers.

### Node Dictionary

```
^NKG("$ND", idx) = stringId     // index → string (e.g., 0 → "MESH:D003920")
^NKG("$NI", stringId) = idx     // string → index (reverse lookup)
```

Monotonic, never reclaimed. `$Increment(^NKG("$meta", "nodeCount"))` assigns next index.

### Master Label Set

```
^NKG("$LS", idx) = label        // index → string (e.g., 0 → "out")
^NKG("$LI", label) = idx        // string → index (reverse lookup)
```

Pre-populated structural labels: 0=out, 1=in, 2=deg. User predicates start at index 3.

### Metadata

```
^NKG("$meta", "version") = N          // monotonic, incremented on every mutation
^NKG("$meta", "nodeCount") = N        // total interned nodes
^NKG("$meta", "edgeCount") = N        // total edges (informational)
^NKG("$meta", "labelCount") = N       // total interned labels
```

## Modified Entity: GraphIndex.cls

```
GraphIndex (existing, extended)
├── InsertIndex()      # MODIFY: dual-write ^KG + ^NKG
├── DeleteIndex()      # MODIFY: remove ^NKG entries
├── UpdateIndex()      # MODIFY: update ^NKG entries
├── PurgeIndex()       # MODIFY: kill both ^KG and ^NKG
├── InternNode()       # NEW: string ID → monotonic integer index
├── InternLabel()      # NEW: label string → monotonic integer index
└── InitStructuralLabels()  # NEW: pre-populate out/in/deg labels
```

## Modified Entity: Traversal.cls

```
Traversal (existing, extended)
└── BuildKG()          # MODIFY: add ^NKG batch encoding pass after existing ^KG logic
```

## No SQL Schema Changes

`rdf_edges`, `kg_NodeEmbeddings`, `rdf_labels`, `rdf_props`, `nodes`, `fhir_bridges` — all unchanged.
