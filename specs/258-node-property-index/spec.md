<!-- markdownlint-disable MD013 -->

# Feature Specification: a node's properties readable in one global reference

**Feature Branch**: not created (draft)
**Created**: 2026-10-23
**Status**: Draft; the prototype below does not support it (2026-10-23)
**Input**: spec 257's measurements. Once the walk is compiled, the queries furthest behind Neo4j spend their time on
property reads and grouping, not on control flow.

## What the measurements say

A property read in the native executor is two global references. The first is a `$Order` on `rdf_props`' unique index
`(graph_id, s, key, ID)`; the second is a `$Get` of the row in the data global and a `$ListGet` of `val`. Over all
106,594 SNB SF1 forums, warm, in label order, that is 0.8 us a read. Inside BI04's compiled walk the same read
measured 1.5 us: its forums are met in no order.

BI04 at SF1 on the laptop: 22,134 matches, 13,722 distinct forums, and 84 ms in one process against Neo4j's 18 ms. Its
compiled walk with the grouping removed takes 14 ms. Replacing only the title read with a constant takes the walk from
72 ms to 52.

## The idea

Keep each node's scalar properties where the adjacency already lives: `^KG("p", graph key, node, key) = value`. It is
maintained by a functional index on `rdf_props`, as spec 252 keeps `^KG("out")`/`("in")`. A read is then one `$Get` on
a subtree that is local to the node, and a node's checks and its grouping keys share the blocks. The executor
(interpreted and compiled) and the SQL path's BFS routes read it; `rdf_props` stays the table of record.

## Prototype result (2026-10-23): the gain is small

A scratch global `^ZIVGP(graph, node, key) = value` held the properties BI04, BI18 and BI03 read (1,327,696 values,
built in 1.5 s). Their compiled walks were rewritten to read it with one `$Data`/`$Get`, everything else unchanged,
and timed on the same laptop between two runs of the unchanged walks (`Exec.Run`, best of 5):

| Query | Two references | One reference | Gain |
| ----- | -------------: | ------------: | ---: |
| BI04  |        91.4 ms |       85.5 ms |   6% |
| BI18  |        50.0 ms |       45.2 ms |  10% |
| BI03  |        31.4 ms |       30.4 ms |   3% |

Replacing BI04's title read with a constant had saved 20 ms. So the cost is the random access to a node's value, not
the second global reference, and a node-local layout removes little of it. The gaps to Neo4j on these queries
(4-5x) are not closed by a storage change of this kind. What remains is the cost of each global access in a walk
that touches many nodes once.

## Open questions

1. Size: SF1 holds about 30M property rows. Should the subtree hold every property, or only those under a length
   (the 128-character `val_key` prefix)?
2. Should large values (embeddings, JSON) stay out of it, and how does a reader tell?
3. The upgrade build on an existing install: a `%BuildIndices` over `rdf_props` (minutes at SF1).
4. Interaction with the ledger and the temporal store (both write `rdf_props` through SQL, so the index sees them).

## Success criteria (draft)

- SF1, laptop and AWS: BI04, BI18, BI03 and the large label-scan aggregates at least 1.5x faster; no query slower by
  more than 10%.
- TCK 3896/3896 in shadow mode; the spec 252 drift tests extended to the property subtree.
