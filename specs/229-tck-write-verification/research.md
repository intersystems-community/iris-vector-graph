# Research: TCK Write Verification

Measurements for spec 229 Phases 1–4. The corpus is `tests/tck/features/` (upstream
openCypher TCK, 3897 scenarios). Runs used `ivg-iris-enterprise` (port 31972), namespace
`TCKB`, multigraph mode on unless stated otherwise.

## Phase 1: side-effect columns in the corpus (T001)

1180 scenarios end with `And no side effects`. 244 end with a
`the side effects should be:` table. Across those tables, each column is used this many
times:

| Column           | Scenarios |
| ---------------- | --------- |
| `+nodes`         | 108       |
| `-nodes`         | 20        |
| `+relationships` | 67        |
| `-relationships` | 22        |
| `+labels`        | 58        |
| `-labels`        | 21        |
| `+properties`    | 109       |
| `-properties`    | 64        |

No other column appears.

## What `+labels` / `-labels` mean (T002)

The tck-api `SideEffectOps` models a graph state as four sets: node ids, relationship ids,
the distinct label names in use, and `(entity, key, value)` property triples. Each column
is a set difference between the state before and after the query. The corpus agrees with
this in every scenario read.

- `Create1 [3]`: `CREATE (:Label)` gives `+labels 1`.
- `Create1 [4]`: `CREATE (:Label), (:Label)` gives `+labels 1`. This counts names, not
  assignments.
- `Create1 [12]`: `CREATE (p:TheLabel {id: …})` gives `+labels 1`.
- `Set3 [3]`: on `CREATE (:A)`, `SET n:Foo` gives `+labels 1`. The existing `A` is not
  counted.
- `Set3 [2]` / `[4]`: `SET n:Foo:Bar` gives `+labels 2`.
- `Remove2 [1]`: `REMOVE n:L` on the only `L` node gives `-labels 1`.
- `Remove2 [2]`: `REMOVE n:Foo` from `(:Foo:Bar)` gives `-labels 1`.
- `Remove2 [3]`: `REMOVE n:L1:L3` gives `-labels 2`.
- `Merge5 [14]`: `CREATE (a:Foo), (b:Bar)` gives `+labels 2`.
- `Unwind1 [6]`: `MERGE (e:Event …)` twice gives `+labels 1`. The existing `Year` label
  is not counted.

The same model explains `+properties 1, -properties 1` for an overwritten property: one
triple leaves the set and one enters it.

## Mapping decision (T003)

`+labels`/`-labels` are measured as the set difference of `DISTINCT rdf_labels.label`,
excluding the harness's `TCK_*` isolation labels. plan.md's mapping table has been updated
to match.

| Set             | IVG source                                                        |
| --------------- | ----------------------------------------------------------------- |
| node ids        | `Graph_KG.nodes (graph_id, node_id)`                              |
| relationship id | `Graph_KG.rdf_edges.edge_id` (stable under `SET r.x`)             |
| label names     | `DISTINCT Graph_KG.rdf_labels.label`, minus `TCK_*`               |
| node property   | one `Graph_KG.rdf_props` row, value type-tagged (`1` ≠ `'1'`)     |
| edge property   | one key of the `rdf_edges.qualifiers` JSON object                 |
| unexpected      | any change to `rdf_reifications`; `^KG("out")` drift under a flag |

The one-row-per-property assumption is enforced rather than assumed. A second `rdf_props`
row for the same `(s, key)`, or qualifiers that are not a JSON object, raise
`SideEffectsAssumptionError` (T009).

An `rdf_props` row whose node has no `nodes` row still counts as a property triple. IVG
should never write one, so counting it makes the scenario fail. See `Merge1 [6]` below.

## Unmapped columns (T004)

None. Every column the corpus uses is mapped, so there are no candidate deferrals. An
unmapped column would fail its scenario with `unmapped side-effect column(s): …` (FR-004).

## Phase 3/4 results (T016, T025)

| Run                                                    | Passing   |
| ------------------------------------------------------ | --------- |
| r11 lenient baseline (`/tmp/tck_r11.tsv`)              | 3894/3897 |
| Tip, lenient, TCKB (`/tmp/tck_s0.tsv`)                 | 3894/3897 |
| **Strict (Phases 3+4), multigraph on** (`/tmp/tck_s1`) | 3842/3897 |
| Strict, multigraph off (`/tmp/tck_s1_mgoff`)           | 3842/3897 |

The strict run has 55 failures: 52 new ones (PASS→FAIL against r11) and the 3 that
already failed in r11. With multigraph off the failing set is identical, scenario for
scenario.

The 3 old failures are `Merge1 [9]`, `Merge1 [14]` and `Merge9 [4]`. All three are engine
gaps (a):

- `Merge1 [14]` raises `-119`.
- `Merge1 [9]` returns 4 rows where 2 are expected.
- `Merge9 [4]` returns 0 rows where 1 is expected.

cd89ab9 on `203-tck-remaining-scenarios` fixes them, but the measured tip (04815a3) does
not contain it.

`IVG_TCK_LENIENT=1` restores the old scoring. The create, delete, set and merge areas
return to their r11 numbers under it: 78, 41, 53 and 72. No scenario errored out of the harness: every failure is an assertion
carrying the exception text or the per-column counts.

The strict run took 853 s, against about 12 minutes for the lenient baseline. That is
under the 2× limit, so the five statements were not collapsed (T025).

### Harness bugs found and fixed while measuring

13 scenarios were failing because of the harness itself: `Set3 [2]–[7]` and
`Set6 [8]–[14]`. Scenario teardown ran `MATCH (n:TCK_x) DETACH DELETE n`, which runs into
engine defect D1 below. D1 removed the isolation label but left the node's other labels,
so a label left over from one scenario hid the next scenario's `+labels`.

Teardown now reads the ids of the label-scoped nodes before the Cypher delete. After the
delete it sweeps `rdf_edges`, `rdf_props`, `rdf_labels` and `nodes` for those ids
(`tests/tck/environment.py`, `tests/unit/tck/test_tck_teardown.py`). With that fix these 13
pass, and none of the 52 below is a harness bug.

### Newly failing scenarios, by class

- (a) product defect: 52 new, plus the 3 old ones (55 in all).
- (b) harness mapping gap: 0.
- (c) TCK expectation IVG cannot observe: 0.

| Area                  | r11 → strict | (a) |
| --------------------- | ------------ | --- |
| delete                | 41 → 16      | 25  |
| merge                 | 72 → 65      | 7   |
| create                | 78 → 74      | 4   |
| match                 | 381 → 377    | 4   |
| set                   | 53 → 50      | 3   |
| call                  | 52 → 50      | 2   |
| remove                | 33 → 31      | 2   |
| with-orderBy          | 292 → 290    | 2   |
| existentialSubqueries | 10 → 9       | 1   |
| graph                 | 61 → 60      | 1   |
| unwind                | 14 → 13      | 1   |

Every other area is unchanged.

### Defect clusters

**D1: DELETE leaves the node row and its properties (14).** A label-matched
`DELETE`/`DETACH DELETE` deletes the labels and edges first. The later subqueries then find
their nodes through those labels, so they match nothing, and `nodes` and `rdf_props`
survive.

- Scenarios: `Delete1 [1]–[3]`, `Delete2 [2]`, `Delete4 [1]–[2]`, `Delete6 [1]–[7]`.
  Each reports `-nodes: expected N, observed 0`.
- `Delete2 [3]` (a bidirectional match deletes no relationship) is in the same family.

**D2: queries raising that the lenient harness scored as empty (14).**

- `Delete5 [1]–[7]` raise `CypherParseError`. The parser does not accept `DELETE` of a
  list-index or map-access expression.
- `Delete3 [1]–[2]` raise `Undefined variable: p` for a named path in `DELETE`.
- `Create4 [1]` raises `CypherParseError` on the movie-graph script.
- `Match7 [22]` raises `VariableTypeConflict`.
- `Match5 [11]–[13]` raise `ValueError: max_hops must be >= min_hops`. The TCK expects an
  empty result for an empty interval.

**D3: SQL or driver errors from generated SQL (11).**

- `Create3 [2]–[3]` (WITH-CREATE): SQLCODE -12.
- `Delete6 [8]–[9]` and `Remove3 [8]–[9]` (`LIMIT 0`/`SKIP` after a write): driver null
  pointer.
- `Graph8 [7]`: -400.
- `ExistentialSubquery1 [4]`: parameter count mismatch.
- `WithOrderBy4 [17]`: -29.
- `WithOrderBy4 [18]`: undefined variable.
- `Merge1 [11]`: unsupported argument type.

**D4: MERGE and SET write semantics (11).**

- `Merge1 [6]`: a MERGE that matches still writes an orphan `rdf_props` row for a
  node id that does not exist.
- `Merge9 [2]`: `+properties` 8, expected 5.
- `Merge6 [1]`: `ON CREATE SET` is not applied.
- `Merge7 [2]`: `ON MATCH SET` is applied to a created relationship.
- `Merge5 [14]`: a list-valued relationship property is not stored.
- `Merge5 [20]`: nothing matches or writes after an earlier delete.
- `Unwind1 [6]`: `+nodes` 4, expected 2, because MERGE creates per row despite an
  earlier match.
- `Create3 [1]`: row multiplicity. N matched rows create one node (the known deviation
  noted in `_create_match_gate`).
- `Set4 [2]–[4]`: `MATCH (n:X {name: 'A'}) SET n = {…}` removes only `name`, not every
  property.

**D5: CALL of a registered zero-output procedure (2).** In `Call1 [1]–[2]` the call falls
through to `Graph_KG.VecSearch` (SQLCODE -30).

## SC-001 (T024)

I made a scratch copy of the tip in `/tmp/ivg229_sc1`. It is not a repository checkout,
so it touches no branch. In it, `_create_match_gate` always returns the ungated form. I ran
the create, merge, match, unwind, with, set, delete, remove and call areas strictly
against that copy.

**No upstream scenario fails because the gate is reverted.** The only difference is how
`Create3 [2]` and `[3]` fail:

- With the gate, both raise SQLCODE -12.
- Without it, both run, and report `+nodes: expected 4, observed 2` and
  `expected 10, observed 2`.

So the upstream corpus has no zero-row `MATCH` followed by a `CREATE` of inline nodes.
Upstream, SC-001 cannot be shown. The supplementary scenario T036 (Phase 6) is what will
show it.

The run also found something else: the gate itself produces the -12 in
`Create3 [2]–[3]`. That defect was hidden while errors counted as empty results.

## Test file locations

The unit tests are in `tests/unit/tck/`, not `tests/unit/` as tasks.md names them:

- `test_tck_side_effects.py`
- `test_tck_result_steps.py`
- `test_tck_teardown.py`
