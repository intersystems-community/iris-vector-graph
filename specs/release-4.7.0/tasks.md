<!-- markdownlint-disable MD013 -->

# IVG 4.7.0 tasks

Rows are `~/.cache/ivg-bench/rel-470/ranked.md`'s. Each row has a test task, written and seen failing on 4.6.5 first, then its fix task. A fix task includes its KNOWN_ISSUES entry and its CHANGELOG line.

## Setup

- [x] T001 Branch `release-4.7.0` from main `bc851871`; worktree `.claude/worktrees/rel-470`
- [x] T002 `scripts/dev/make_scratch_namespace.py` accepts and drops `IVG470*`; `--drop` removes the database directory (tests/unit/test_make_scratch_namespace.py; checked live with IVG470DIR)
- [ ] T003 Version 4.7.0 in pyproject.toml and `iris_vector_graph/__init__.py`; CHANGELOG `### v4.7.0` section

## Wave 1: algorithms

- [ ] T004 [W1-ALG] Row 8 test: alpha outside (0, 1) and maxIter < 1 raise `ValueError` naming the argument; alpha 0.5 still answers 0.6154 on the 4.6.5 E2E graph
- [ ] T005 [W1-ALG] Row 8 fix: `ivg.ppr` / `ppr()` accept any `alpha` and `maxIter` (alpha 2.0 answers 22.0 and -21.0); range checks in the translator's `_translate_ppr`, the engine's `ppr()` and the ObjectScript entry
- [ ] T006 [W1-ALG] Row 9 test: a→b plus a loner c, seed a: three rows, c = 0.0, matching networkx `pagerank(personalization=...)` (the dangling-node convention Tom kept on 2026-11-02 stays)
- [ ] T007 [W1-ALG] Row 9 fix: Personalized PageRank leaves out nodes with no edges; PPR answers every node of the graph it reads
- [ ] T008 [W1-ALG] Row 12 test: identical vectors score 1.0, orthogonal 0.0, opposite -1.0
- [ ] T009 [W1-ALG] Row 12 fix: VecIndex cosine `score` is cos − 1; `Graph.KG.VecIndex` cosine score = 1 − distance (VecIndex.cls:47, :383); CHANGELOG says the scale changed
- [ ] T010 [W1-ALG] Row 28 test: `top_k=1` answers one row on every route
- [ ] T011 [W1-ALG] Row 28 fix: Leiden `topK` keeps whole communities on the first route; apply `top_k` to rows on the first route

## Wave 1: the caller's transaction

- [ ] T012 [W1-TXN] Row 3 test: caller inserts a row, calls each, rolls back: the row and the IVG writes are gone (test_464_caller_txn_e2e's pattern); setup, which must commit DDL, refuses an open caller transaction with a message instead
- [ ] T013 [W1-TXN] Row 3 fix: `bulk_load_session`, `embed_nodes` and `initialize_schema()` commit the caller's open transaction; nest in the caller's transaction through `Graph.KG.Txn`, as 4.6.4 did for the writers
- [ ] T014 [W1-TXN] Row 4 test: ledger commit inside a caller transaction, caller rolls back: no revision row and no graph change; caller commits: both present
- [ ] T015 [W1-TXN] Row 4 fix: The ledger cannot join a caller's transaction; ledger commit nests in `Graph.KG.Txn`
- [ ] T016 [W1-TXN] Row 13 test: the caller's dicts compare equal to deep copies taken before the call, on both paths
- [ ] T017 [W1-TXN] Row 13 fix: `bulk_create_nodes` adds `"id"` to the caller's own properties dict; copy before adding `id`

- [ ] T083 [W1-ALG] Row 8b (found during 4.7.0, silent wrong): global `kg_PAGERANK` and `gds.pageRank.stream` take any damping/maxIterations; `PageRank.Run` scores a seed that is not a node at 1.0; `MCPTools.PPRWalk` returns "" on success; VecIndex with an unknown metric reports score = +distance; each test first
- [ ] T084 [W1-ALG] Row 9b: `CALL ivg.ppr ... ORDER BY score DESC LIMIT k` passes k to kg_PPR's row limit (CALL 5.8 ms → 159 ms at 100k nodes when the zeros fill the 1000 cap)
- [ ] T085 [W1-TXN] Row 3c (found during 4.7.0, silent): `BulkLoader.build_graph_globals` sends ObjectScript as SQL (-51) and never rebuilds ^KG; `attach_embeddings_to_table` silently embeds 0 rows when the node is missing; tests first
- [ ] T080 [W1-TXN] Row 3b test (found during 4.7.0, same class as row 3): each remaining `conn.commit()` path (embed_edges, fhir.py, fhir_graph.py, rdf_export.py, snapshot.py, bulk_loader.py, bulk_create_edges' disable_indexes branch, arno_bridge.py, ledger enable/redact on from_connect): caller row + call + rollback leaves neither, or the call refuses with $TLEVEL > 0 and the caller's row is still uncommitted
- [ ] T081 [W1-TXN] Row 3b fix: those paths join the caller's transaction or refuse it
- [ ] T082 [W1-CY] Row 33b (found during 4.7.0, silent wrong): a clause after a handler procedure's YIELD (`CALL db.labels() YIELD label WITH label WHERE label = 'A' RETURN label`) was dropped and every label answered; it raises or is applied

## Wave 1: Cypher

- [ ] T018 [W1-CY] Row 10 test: `create_node(... {"s": "30"})` and a Cypher-stored `'30'`: `properties(n)['s']` is the string `'30'` on the SQL and native paths, and a stored number stays a number
- [ ] T019 [W1-CY] Row 10 fix: `properties(n)` reads a stored string `'30'` as the number 30; type the map's values as `n.s` does
- [ ] T020 [W1-CY] Row 33 test: `CALL ivg.pagerank({}) YIELD node, score WHERE score > 0 RETURN node` answers the nodes; `WHERE score > 1` answers none
- [ ] T021 [W1-CY] Row 33 fix: `CALL ... YIELD ... WHERE` is a parse error; parser and translator accept `WHERE` after `YIELD`
- [ ] T022 [W1-CY] Row 34 test: answers `[['a', True]]`, and `False` for a node without `name`
- [ ] T023 [W1-CY] Row 34 fix: `RETURN n.id, exists(n.name)` fails, SQLCODE -12
- [ ] T024 [W1-CY] Row 35 test: `'30'` becomes 30 and reads back as the number; `'x'` becomes null as openCypher says
- [ ] T025 [W1-CY] Row 35 fix: `SET n.p = toInteger(n.p)` fails, SQLCODE -23, nothing written

- [ ] T086 [W1-CY] Row 35b (found during 4.7.0, silent): Python writers' equal-text retype; LedgerApply replay retype; `_translate_expr_for_update` silent `except` storing null; `SET n.q = n.missing + 1` leaving a NULL row; `toBoolean('yes')` answering true; tests first

## Wave 2: writers

- [ ] T026 [W2-WR] Row 11 test: reify an edge, DETACH DELETE its source: 0 `rdf_reifications` rows for it, and the reifier node handled as documented
- [ ] T027 [W2-WR] Row 11 fix: `DETACH DELETE` leaves the node's `rdf_reifications` row
- [ ] T028 [W2-WR] Row 14 test: a batch holding one 129-character label raises (or reports the refused row) and the other rows are written or none are, as documented
- [ ] T029 [W2-WR] Row 14 fix: The driver's `executemany` skips a row IRIS refuses, so batch writers answer success without it
- [ ] T030 [W2-WR] Row 16 test: with a plain `idx_edges_adj`, setup replaces it or refuses, and `status()` reports it; an edge written afterwards is in `^KG`
- [ ] T031 [W2-WR] Row 16 fix: `ensure_indexes` accepts a plain index named `idx_edges_adj`; writes then stop maintaining `^KG` and `status()` does not say
- [ ] T032 [W2-WR] Row 17 test: another process holds `^IVG.AdjW(graph)`: `create_edge` raises `GraphWriteError` and writes no row (first check whether 4.6.3's raise already covers it)
- [ ] T033 [W2-WR] Row 17 fix: `create_edge` answers True without `^KG` when the adjacency writer lock is not granted

## Wave 2: relationships and time

- [ ] T034 [W2-TIME] Row 15 test: +00:53:28 in a `python:3.12-slim` container and on macOS; probe `~/.cache/ivg-bench/tzprobe/probe.py`
- [ ] T035 [W2-TIME] Row 15 fix: Named zones read the host's tz database first (Stockholm 1818: +01:12:12 on Debian, +00:53:28 on macOS); one helper reading the `tzdata` wheel first, used at the three `ZoneInfo` sites in translator.py; `tzdata` a dependency; drop the `tests/tck/environment_dependent.txt` entry and the "IVG does not pin tzdata" text in docs/dev/TCK.md
- [ ] T036 [W2-TIME] Row 18 test: the 36 cases leave `KNOWN_WRONG_461` in tests/oracle/crosstype.py and agree with the oracle
- [ ] T037 [W2-TIME] Row 18 fix: A date or time read by position from a `collect()`ed list compares as its text (36 cases)
- [ ] T038 [W2-TIME] Row 19 test: answers each relationship as `RETURN collect(r)` does; `size(rs)`, `rs[0]` and a DELETE of an element still work
- [ ] T039 [W2-TIME] Row 19 fix: `WITH collect(r) AS rs RETURN rs` answers the types (`['R']`), not the relationships
- [ ] T040 [W2-TIME] Row 20 test: `MATCH p = (a)-[:R]->(b) RETURN nodes(p), relationships(p)` answers the nodes and relationships
- [ ] T041 [W2-TIME] Row 20 fix: `nodes(p)` answers node ids and `relationships(p)` types

- [ ] T087 [W2-TIME] Row 20b (found during 4.7.0, silent): `reverse(relationships(p))` reverses the JSON text; `rs[0]` of a WITH-collected relationship list answers the type; `datetime({..., timezone: 'Europe/Stockholm'})` drops the offset's seconds; `_iana_tz_offset` reads the offset at midnight, not the wall time; tests first

## Wave 2: the reporters' environment

- [ ] T042 [W2-ENV] Row 1 test: #13's own script on Community 2026.1 (a throwaway container) and enterprise 2026.3: both engines answer `[['InterSystems', 2]]`; reproduce on 2026.1 first
- [ ] T043 [W2-ENV] Row 1 fix: #13 (ArnolfJp): `count(DISTINCT ...)` + `ORDER BY` + `LIMIT` fails on a `from_connect()` engine on IRIS 2026.1
- [ ] T044 [W2-ENV] Row 5 test: fresh 2026.1 namespace: `initialize_schema()`'s summary names no failed class other than the `%AI` skips
- [ ] T045 [W2-ENV] Row 5 fix: python-devx: `IVG.CypherEngine`, `IVG.Percentile` are not compiled by setup on 2026.1
- [ ] T046 [W2-ENV] Row 2 test: paired run, same container, 4.4.1 then 4.7.0, same script, after `TUNE TABLE`: within 10% of 4.4.1, or the gap explained and accepted by Tom
- [ ] T047 [W2-ENV] Row 2 fix: python-devx: 10k nodes + 30k edges load in 74.7 s on 4.6.2 against 41.7 s on 4.4.1
- [ ] T048 [W2-ENV] Row 29 test: reading-role `status()` on a complete namespace reports no missing routine, and says what it could not check
- [ ] T049 [W2-ENV] Row 29 fix: `status()` as a reader reports routines it cannot see as missing (13 on 2026.3)

## Wave 3: value reading

- [ ] T050 [W3-VAL] Row 21 test: `collect(n.flag)` then `UNWIND`: `true`, `false` and `''` come back as themselves
- [ ] T051 [W3-VAL] Row 21 fix (also `collect(exists(n.p))` and `collect(x IS NOT NULL)` answering [1, 0], found during 4.7.0): An unwound boolean or `''` of a list built at run time reads as `1`, `0` or null
- [ ] T052 [W3-VAL] Row 22 test: `collect(e)` over `[[1], true]` answers `[[1], true]`
- [ ] T053 [W3-VAL] Row 22 fix (also `UNWIND [date(...), 'b'] AS e RETURN e < 'c'` answering true, not null, found during 4.7.0): An UNWIND of a run-time list reads a nested list, map or boolean as a string
- [ ] T054 [W3-VAL] Row 23 test: store `'true'`, `'[1, 2]'` and `"\u0000"` through each writer: each reads back as the same string
- [ ] T055 [W3-VAL] Row 23 fix: A stored string spelling a boolean, list or map reads as one; `"\u0000"` reads as `''`

## Wave 3: number filters

- [ ] T056 [W3-NUM] Row 24 test: 0.95 on one node, `'.95'` on another: `= 0.95` finds the first only, `<> 0.95` the second
- [ ] T057 [W3-NUM] Row 24 fix: A number filter also matches a string that spells the number

## Wave 3: setup and snapshots

- [ ] T058 [W3-SET] Row 25 test: a second `initialize_schema()` with another width raises `EmbeddingIdentityConflict` before changing the column
- [ ] T059 [W3-SET] Row 25 fix: Two processes with different `embedding_dimension` re-declare the vector column without a warning
- [ ] T060 [W3-SET] Row 26 test: snapshot and restore a registered FHIR graph: `fhir_graphs` and `code_crosswalk` rows equal before and after
- [ ] T061 [W3-SET] Row 26 fix: A snapshot leaves out `fhir_graphs`; a restore does not bring back a FHIR graph's registration or crosswalk

## Waiting on Tom

- [ ] T062 [TOM] Row 6 test (Tom 2026-11-10: on by default): with defaults, a purge leaves no edgeless endpoint it created; a node with other edges or properties stays
- [ ] T063 [TOM] Row 6 fix: opsreview: a temporal purge keeps the endpoint nodes it leaves bare unless `retire_bare_nodes=True`; the default becomes True (Tom, 2026-11-10), `False` keeps the old behaviour; CHANGELOG says the default changed
- [ ] T064 [TOM] Row 27 test: outside multigraph mode: a parallel edge or an error, as Tom decides
- [ ] T065 [TOM] Row 27 fix: `CREATE` of a relationship that already exists writes nothing and says nothing; waits on Tom's decision

## Waiting on the reporter

- [ ] T066 [REPORTER] Row 7 test: the reporter's query on their graph on 4.6.5 or later; not reproduced here on a 305-edge hub (405 / 405)
- [ ] T067 [REPORTER] Row 7 fix: Mindwalk: variable-length `RETURN DISTINCT b.id AS dst LIMIT 25` from a 305-edge node answered no rows (4.2.2); close if it answers; otherwise reproduce from their data

## Docs

- [ ] T068 KNOWN_ISSUES: remove or rewrite each fixed entry; the three "a fix is planned for 4.6.1" lines (rows 19, 20 and the vector-search entry) say what is true
- [ ] T069 CHANGELOG 4.7.0: each fix with its before/after values; the opened-against-closed count
- [ ] T070 GitHub #12 (fixed in 4.6.0, R089) and #13: draft the closing comments for Tom (`/wordcraft`, `/no-ai-slop`); post only on his word

## Gates (plan.md), on the final commit

- [ ] T071 Unit suite, 0 failed
- [ ] T072 Full E2E on dpgenai1, no product failure
- [ ] T088 `tests/integration` on the final commit, 0 failed (no gate ran it before 4.7.0)
- [ ] T073 `scripts/ci-parity.sh` exit 0
- [ ] T074 TCK enterprise 2026.3 and Community 2026.1, drivers 5.4.0 and 5.4.1, 3896 / 3896 each
- [ ] T075 Quickstart 5 / 5
- [ ] T076 Speed: touched paths paired with 4.6.5 after `TUNE TABLE`; row 2 against 4.4.1
- [ ] T077 Newcomer walkthroughs (expert, Python developer) on the built wheel: 0 blocker, 0 wrong
- [ ] T078 Opened against closed: at least 28 of 55 closed
- [ ] T079 Sign-off in PRE_RELEASE_CHECKLIST.md; publish only on Tom's "push it"
