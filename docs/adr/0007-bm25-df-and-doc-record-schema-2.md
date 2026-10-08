<!-- markdownlint-disable MD013 MD024 MD060 -->

# BM25 schema 2 keeps document frequency and a per-document token record, under a write lock

`BM25Index.Insert` recounted each token's document frequency by walking its posting list, and a replace walked the whole vocabulary to find a document's old postings, so one insert cost seconds on a 337,861-document index. Schema 2 keeps `df(term)` and one `$ListBuild` token record per document (`doc(docId)`), computes IDF at query time, and does every write under an index-level lock in a transaction with plain `Set`. Writers are refused on a schema-1 index until `Upgrade` has run.

## Considered options

- **One `doc` node per token** (the original request): about 27 million extra nodes on the cluster index; a `$ListBuild` per document is one read and about 1/80th the nodes.
- **`$Increment` for the counters, no lock**: IRIS does not undo `$Increment` on `TROLLBACK` (measured in spec 236), so a failed insert leaves `df` or `N` wrong with nothing to detect it. A lock plus a transaction with plain `Set` is rolled back as a unit. It serializes writers per index, which costs nothing when there is one.
- **Keep the stored `idf`**: correct only for the terms of the last inserted document, so scores drift with `N`. Computed at query time it is always right. Scores on an index that has had incremental inserts change when it is upgraded.
- **Let `Insert` fall back to the old walks on an unmigrated index**: keeps the O(N) cost, and documents inserted during `Upgrade`'s pass would be missing from `df`. Refusing with an error naming `Upgrade` is simple and cannot lose a write.

## Consequences

The on-disk format is versioned (`cfg schema`) and `Search` reads both shapes. Snapshots of `^BM25Idx` grow by one node per document.
