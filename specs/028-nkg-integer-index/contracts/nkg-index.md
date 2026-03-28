# Contracts: NICHE Knowledge Graph Integer Index

**Feature**: 028-nkg-integer-index | **Date**: 2026-03-28

## Contract 1: InternNode(id) → Integer

**Input**: String node ID (e.g., "MESH:D003920")
**Output**: Monotonic integer index (e.g., 0)
**Side effects**: Sets `^NKG("$ND", idx) = id` and `^NKG("$NI", id) = idx`
**Concurrency**: Fine-grained `Lock +^NKG("$NI", id)` prevents duplicate assignment
**Idempotent**: Returns existing index if node already interned

## Contract 2: InternLabel(label) → Integer

**Input**: String label (e.g., "binds")
**Output**: Monotonic integer index (e.g., 5)
**Side effects**: Sets `^NKG("$LS", idx) = label` and `^NKG("$LI", label) = idx`
**Pre-population**: On first call, initializes 0=out, 1=in, 2=deg if not present
**Concurrency**: Fine-grained `Lock +^NKG("$LI", label)`

## Contract 3: InsertIndex — dual-write

**Input**: Same as existing (pID, s, p, o, qualifiers)
**Behavior**: Writes to both `^KG` (existing) and `^NKG` (new integer-encoded)
**^NKG writes**:
- `^NKG(-1, sIdx, -(pIdx+1), oIdx) = weight`
- `^NKG(-2, oIdx, -(pIdx+1), sIdx) = weight`
- `$Increment(^NKG(-3, sIdx))`
- `$Increment(^NKG("$meta", "version"))`

## Contract 4: DeleteIndex — dual-delete

**Input**: Same as existing (pID, s, p, o, qualifiers)
**Behavior**: Removes from both `^KG` and `^NKG`
**^NKG removes**:
- `Kill ^NKG(-1, sIdx, -(pIdx+1), oIdx)`
- `Kill ^NKG(-2, oIdx, -(pIdx+1), sIdx)`
- `$Increment(^NKG(-3, sIdx), -1)`
- `$Increment(^NKG("$meta", "version"))`
**Note**: Node dictionary entries are NOT removed (monotonic, never reclaimed)

## Contract 5: BuildKG — batch ^NKG pass

After existing `^KG` population:
1. `Kill ^NKG`
2. Initialize structural labels (0=out, 1=in, 2=deg)
3. Iterate `^KG("out", src, pred, dst)` and intern nodes/labels
4. Write integer-encoded edges to `^NKG`
5. Increment `^NKG("$meta", "version")`

## Contract 6: PurgeIndex — dual-purge

**Behavior**: `Kill ^KG` AND `Kill ^NKG`
