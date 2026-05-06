# Spec 099: LDBC Full-Schema Loader for IVG Benchmarking

**Feature Branch**: `099-ldbc-full-schema-loader`  
**Created**: 2026-05-04  
**Status**: Draft  
**Purpose**: Enable measurement of all 14 LDBC SNB IC queries (not just the 5 knows-only queries)

---

## Problem

IVG can currently only measure 5 of 14 LDBC IC queries because the benchmark only loads
`person_knows_person` (friendship graph). The other 9 queries require:

| Query | Missing data |
|-------|-------------|
| IC2 | Post nodes, hasCreator edges |
| IC3 | Person location, Post/Comment |
| IC4 | Post, Tag, post_hasTag |
| IC5 | Forum, forum_hasMember |
| IC6 | Post, Tag (complex 2-hop) |
| IC7 | Comment, likes, replyOf |
| IC8 | Comment replyOf chains |
| IC9 | Post + Comment + dates |
| IC11 | Organisation, workAt |
| IC12 | Comment, hasTag (expert) |

---

## LDBC SF10 Data Available

All files already extracted from `/tmp/sf10_out/social_network-sf10-CsvBasic-LongDateFormatter/`:

| File | Rows | Key columns |
|------|------|-------------|
| `dynamic/person_knows_person_0_0.csv` | 1.94M | Person.id, Person.id |
| `dynamic/post_0_0.csv` | 7.4M | id, creationDate, content, length |
| `dynamic/comment_0_0.csv` | 21.9M | id, creationDate, content |
| `dynamic/person_likes_post_0_0.csv` | 8.8M | Person.id, Post.id, creationDate |
| `dynamic/person_likes_comment_0_0.csv` | 19.9M | Person.id, Comment.id |
| `dynamic/post_hasCreator_person_0_0.csv` | 7.4M | Post.id, Person.id |
| `dynamic/comment_hasCreator_person_0_0.csv` | 21.9M | Comment.id, Person.id |
| `dynamic/post_hasTag_tag_0_0.csv` | 7.6M | Post.id, Tag.id |
| `dynamic/person_hasInterest_tag_0_0.csv` | 1.5M | Person.id, Tag.id |
| `dynamic/person_workAt_organisation_0_0.csv` | 143K | Person.id, Organisation.id |
| `dynamic/person_studyAt_organisation_0_0.csv` | 52K | Person.id, Organisation.id |
| `dynamic/person_isLocatedIn_place_0_0.csv` | 65K | Person.id, Place.id |
| `dynamic/forum_0_0.csv` | 595K | id, title |
| `dynamic/forum_hasMember_person_0_0.csv` | (need to extract) | Forum.id, Person.id |
| `dynamic/comment_replyOf_post_0_0.csv` | (need to extract) | Comment.id, Post.id |
| `dynamic/comment_replyOf_comment_0_0.csv` | (need to extract) | Comment.id, Comment.id |
| `static/tag_0_0.csv` | (extracted) | id, name |
| `static/organisation_0_0.csv` | (extracted) | id, type, name |
| `static/place_0_0.csv` | (extracted) | id, name |

---

## IVG Graph Model

IVG uses a simple `(s, predicate, o)` triple store. Map LDBC entities as follows:

| LDBC entity/relationship | IVG node_id format | IVG predicate |
|--------------------------|-------------------|---------------|
| Person | `person_{id}` | — |
| Post | `post_{id}` | — |
| Comment | `comment_{id}` | — |
| Forum | `forum_{id}` | — |
| Tag | `tag_{id}` | — |
| Organisation | `org_{id}` | — |
| Place | `place_{id}` | — |
| person_knows_person | `person_{id}` --KNOWS--> `person_{id}` | KNOWS |
| post_hasCreator | `post_{id}` --HAS_CREATOR--> `person_{id}` | HAS_CREATOR |
| comment_hasCreator | `comment_{id}` --HAS_CREATOR--> `person_{id}` | HAS_CREATOR |
| person_likes_post | `person_{id}` --LIKES--> `post_{id}` | LIKES |
| person_likes_comment | `person_{id}` --LIKES--> `comment_{id}` | LIKES |
| post_hasTag | `post_{id}` --HAS_TAG--> `tag_{id}` | HAS_TAG |
| person_hasInterest | `person_{id}` --INTERESTED_IN--> `tag_{id}` | INTERESTED_IN |
| person_workAt | `person_{id}` --WORKS_AT--> `org_{id}` | WORKS_AT |
| person_studyAt | `person_{id}` --STUDIED_AT--> `org_{id}` | STUDIED_AT |
| person_isLocatedIn | `person_{id}` --LOCATED_IN--> `place_{id}` | LOCATED_IN |
| forum_hasMember | `forum_{id}` --HAS_MEMBER--> `person_{id}` | HAS_MEMBER |
| comment_replyOf_post | `comment_{id}` --REPLY_OF--> `post_{id}` | REPLY_OF |
| comment_replyOf_comment | `comment_{id}` --REPLY_OF--> `comment_{id}` | REPLY_OF |

Properties (for IC queries that filter by date, content, etc.) stored in `rdf_props` via
IVG's existing property storage.

---

## Loader Design

Single Python script `tests/benchmarks/ldbc_full_loader.py` using the IVG engine:

```python
class LDBCFullLoader:
    def __init__(self, conn, data_dir):
        self.conn = conn
        self.engine = IRISGraphEngine(conn)
        self.o = iris.createIRIS(conn)
        self.data_dir = data_dir

    def load_all(self, batch_size=50_000):
        self.clear()
        self.load_nodes()      # engine.create_node() per entity type
        self.load_edges()      # BulkIngestEdges for all relationship files
        self.load_properties() # engine.execute_cypher() SET for node properties
        self.build_indices()   # BuildKG -> BuildNKG -> WarmAdjCache

    def load_nodes(self):
        # Use engine.execute_cypher("CREATE (n {node_id:$id, ...})")
        # or bulk: engine.bulk_create_nodes(nodes_list)
        # Persons: id, firstName, lastName, birthday, gender, locationIP, browserUsed

    def load_edges(self):
        # Use BulkIngestEdges (fastest path — embedded Python gref)
        # Each file: [(src_node_id, dst_node_id), ...] + predicate string
        # E.g.: [("post_123", "person_933")] with predicate "HAS_CREATOR"

    def load_properties(self):
        # Use engine.execute_cypher() to SET properties on created nodes
        # Post: creationDate, content, length
        # Comment: creationDate, content
        # Person: firstName, lastName, birthday, gender

    def build_indices(self):
        # engine triggers BuildKG internally or call explicitly
        _call_classmethod(self.conn, 'Graph.KG.Traversal', 'BuildKG')
        _call_classmethod(self.conn, 'Graph.KG.Traversal', 'BuildNKG')
        self.o.classMethodVoid('Graph.KG.NKGAccel', 'InvalidateAdjCache')
```

### Why engine + Cypher, not raw SQL

- **Correctness**: engine methods maintain `^KG`, `^NKG`, and SQL indices consistently
- **Portability**: no hardcoded SQL table names — works across IRIS versions
- **Properties**: Cypher `SET n.firstName = $v` uses IVG's property storage correctly
- **Validation**: engine enforces schema constraints; raw SQL bypasses them
- **BulkIngestEdges exception**: for edge-only files (relationships without properties),
  `BulkIngestEdges` remains the fastest path (135K e/s vs 16K via SQL) — acceptable
  because it writes directly to `^KG` globals which the engine also manages

---

## IC Query Implementations

For each unmeasured IC, provide a Python function using IVG's Cypher engine:

### IC2 — 20 most recent posts/comments by friends
```cypher
MATCH (p {node_id: $pid})-[:KNOWS]-(f)-[:HAS_CREATOR]-(msg)
WHERE msg.node_id STARTS WITH 'post_' OR msg.node_id STARTS WITH 'comment_'
RETURN msg.node_id, msg.creationDate
ORDER BY msg.creationDate DESC LIMIT 20
```

### IC3 — Friends + friends-of-friends in countries X/Y
```cypher
MATCH (p {node_id: $pid})-[:KNOWS*1..2]-(f)-[:LOCATED_IN]->(place {node_id: $place})
RETURN DISTINCT f.node_id
```
**Note**: This uses variable-length path `[*1..2]` — requires spec 100 fix to work correctly.

### IC4 — New topics (tags on friends' posts not seen before)
```cypher  
MATCH (p {node_id: $pid})-[:KNOWS]->(f)<-[:HAS_CREATOR]-(post)-[:HAS_TAG]->(tag)
WHERE post.creationDate >= $start AND post.creationDate < $end
RETURN DISTINCT tag.node_id, count(post) AS cnt
ORDER BY cnt DESC LIMIT 10
```

### IC10 — Friend recommendation (already works at 0.07ms)
```cypher
MATCH (p {node_id: $pid})-[:KNOWS]->(f)-[:KNOWS]->(fof)
WHERE NOT (p)-[:KNOWS]-(fof) AND fof.node_id <> $pid
RETURN DISTINCT fof.node_id LIMIT 10
```

---

## Scope

**In scope**: Loader, IC2/IC3/IC4/IC5/IC7/IC8/IC9/IC11/IC12 benchmark functions  
**Out of scope**: Making IC queries pass LDBC correctness validation (that's spec 100+)  
**Goal**: Measure IVG latency on all 14 IC patterns, comparable to GES FDR tables

---

## Acceptance Criteria

- **SC-001**: `LDBCFullLoader.load_all()` completes on SF10 in < 25 minutes full, < 5 minutes with `--skip-comments` flag
- **SC-002**: All 14 IC query functions execute without error (correct results not required)
- **SC-003**: Latency table for all 14 ICs measured and compared to GES SF100 baseline
- **SC-004**: `tests/benchmarks/ldbc_ic_benchmark.py` produces a machine-readable results JSON

---

## Dependencies

- Spec 100 (variable-length Cypher path fix) — IC3, IC6, IC12 use `[*1..2]` paths
- LDBC SF10 files in `/tmp/sf10_out/` (already available)
- BulkIngestEdges + BuildNKG already work (spec 096 complete)
