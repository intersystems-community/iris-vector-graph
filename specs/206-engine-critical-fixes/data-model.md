# Data Model: IVG Engine Critical Fixes (206)

No schema changes. No new globals. No new SQL tables.

## Globals affected

| Global subscript | A9 effect | A12 effect | A8.3 effect |
|---|---|---|---|
| `^KG("tout")` | preserved (no longer destroyed by BuildKG) | none | none |
| `^KG("tin")` | preserved | none | none |
| `^KG("tagg", bucket)` | preserved | none | deleted within `[bucketStart, bucketEnd]` |
| `^KG("bucket", bucket)` | preserved | none | deleted within `[bucketStart, bucketEnd]` |
| `^KG("labelset")` | preserved | none | none |
| `^KG("label")` | rebuilt as before | none | none |
| `^KG("prop")` | rebuilt as before | none | none |
| `^KG("out")` | rebuilt as before | none | none |
| `^KG("in")` | rebuilt as before | none | none |
| `^KG("deg")` | rebuilt as before | none | none |

## SQL tables affected

| Table | A12 effect |
|---|---|
| `Graph_KG.rdf_edges` | DELETE split: `WHERE s IN (...)` then `WHERE o_id IN (...)` |
| `Graph_KG.rdf_reifications` | DELETE split: subquery on `s` then subquery on `o_id` |
