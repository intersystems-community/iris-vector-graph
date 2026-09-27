# Spec 203 — openCypher TCK Remaining Scenario Fixes

**Feature Branch**: `203-tck-remaining-scenarios`
**Created**: 2026-08-05
**Status**: Complete (2026-09-26)
**Baseline**: 2930/3897 passing (75.2%) as of commit `78171ad`
**Target**: ≥3118/3897 (80%) — 188 additional scenarios
**Outcome**: 3896/3896 eligible, strict and typed, at `937d4b7` (merged to
`main` as `3ec5bec`). Multigraph off: 3889/3896. See `docs/TCK.md`.

---

## Background

Branch `201-opencypher-tck-harness` shipped a behave/Gherkin TCK harness and
reached 2930/3897 (75.2%). 965 scenarios still fail across 7 distinct root causes.
This spec tracks the translator fixes needed to reach 80%.

All work is in `iris_vector_graph/cypher/translator.py` and/or
`iris_vector_graph/_engine/query.py`. No ObjectScript, schema, or `.feature`
file changes.

---

## Clarifications

### Session 2026-08-05

- Q: Implementation priority order? → A: Non-temporal clusters first; temporal last (needs date/time type infrastructure throughout)
- Q: Does the 80% denominator include @wip scenarios? → A: No. 3897 is the total runnable set; @wip scenarios are excluded from both numerator and denominator.

---

## Failure Cluster Summary

| Cluster                    | Feature files                  | Failures | Priority | Root cause                                              |
| -------------------------- | ------------------------------ | -------- | -------- | ------------------------------------------------------- |
| A — Temporal               | Temporal1/2/3/5/7/8/9/10       | 674      | P3       | No temporal type support                                |
| B — Quantifier             | Quantifier1/2/3/4/6/9/10/11/12 | 72       | P1       | JSON_TABLE CTE `<UNDEFINED>`                            |
| C — XOR/boolean precedence | Precedence1/3/4                | 34       | P1       | 0/1 integer vs `true`/`false` string                    |
| D — ORDER BY sort types    | WithOrderBy1/2/4               | 63       | P2       | Boolean sort key (D1); temporal sort (D2, blocked by A) |
| E — Variable-length paths  | Match4/5/6/7/8/9               | 37       | P2       | BFS rel-type filter + depth accuracy                    |
| F — MERGE idempotency      | Merge1/2/3/4/5/6/7/9           | 18       | P1       | Relationship duplicate insert                           |
| G — Pattern expressions    | Pattern1/2                     | 9        | P3       | Path expr predicates; SyntaxError not raised            |
| H — Long-tail              | 31 feature files               | 58       | P2       | Mixed; per-cluster investigation                        |
| **Total**                  |                                | **965**  |          |                                                         |

To reach 80% (+188 scenarios), fixing C + F + B + D1 yields ~115–117.
Adding E and H long-tail covers the remainder.

---

## User Stories

### User Story 1 — Fix XOR/boolean result type (Cluster C, P1)

Boolean expressions folded at compile time (`true XOR false`, `true AND false OR true`)
emit SQL integer literals `0`/`1`. The TCK comparison layer expects string
`'false'`/`'true'`. 34 Precedence scenarios fail because of this type mismatch.

**Why P1**: Small, self-contained, high yield (30–34 / 34 fixed).

**Independent Test**: `RETURN true XOR false AS r` → `r = false` (not `0`)

**Acceptance Scenarios**:

1. **Given** `RETURN true OR true XOR true AS a`, **When** executed, **Then** `a = true`
2. **Given** `RETURN true XOR false AND false AS a, (true XOR false) AND false AS c`, **When** executed, **Then** `a = true, c = false`
3. **Given** `RETURN null OR false AS r`, **When** executed, **Then** `r = null`
4. **Given** existing Precedence1 suite, **When** all 25 scenarios run, **Then** all pass

---

### User Story 2 — Fix MERGE relationship idempotency (Cluster F, P1)

`MERGE (a)-[r:TYPE]->(b)` where the relationship already exists creates a
duplicate instead of matching. `count(r)` returns 2 instead of 1.

**Why P1**: Data-correctness bug; affects all MERGE-on-relationship tests. High yield (15–18/18).

**Independent Test**: `MERGE` on existing rel → `count(r) = 1` not `2`

**Acceptance Scenarios**:

1. **Given** nodes A and B with a TYPE relationship between them, **When** `MERGE (a)-[r:TYPE]->(b)` runs, **Then** `count(r) = 1` and no new relationship is created
2. **Given** no relationship between A and B, **When** `MERGE (a)-[r:TYPE]->(b)`, **Then** exactly 1 relationship created
3. **Given** an `ON MATCH SET r.hit = true` clause, **When** the relationship already exists, **Then** `r.hit = true` is set
4. **Given** an `ON CREATE SET r.new = true` clause, **When** the relationship does not exist, **Then** `r.new = true` is set

---

### User Story 3 — Fix quantifier JSON_TABLE CTE reference (Cluster B, P1)

`any(x IN list WHERE pred)` and `all(x IN list WHERE pred)` generate SQL where a
CTE column is passed directly as the `JSON_TABLE` source. IRIS raises
`<UNDEFINED>` (`%sqlcq` compilation failure) because CTE columns cannot be used
directly as `JSON_TABLE` source expressions in all contexts.

**Why P1**: IRIS crashes (fatal SQL error) rather than returning a wrong result. Affects 72 scenarios.

**Independent Test**: `WITH [1, null, true] AS list RETURN any(x IN list WHERE false) AS r` → `r = false` (no crash)

**Acceptance Scenarios**:

1. **Given** `any(x IN [1, null, true] WHERE false)`, **Then** result is `false`
2. **Given** `any(x IN [1, 2, 3] WHERE x > 2)`, **Then** result is `true`
3. **Given** `all(x IN [1, 2, 3] WHERE x > 0)`, **Then** result is `true`
4. **Given** `all(x IN [1, 2, null] WHERE x > 0)`, **Then** result is `null`
5. **Given** a multi-WITH-stage query building a list with `rand()` and `reverse()`, **When** `any(x IN list WHERE false)` evaluated at end, **Then** no `<UNDEFINED>` IRIS error and result is `false`

---

### User Story 4 — Fix boolean sort ordering (Cluster D1, P2)

`ORDER BY <boolean>` when the value is an UNWIND result of `[true, false]`
produces undefined order because the translator's boolean-detection cast
(`ISNUMERIC(...) = 1 THEN CAST(... AS DOUBLE)`) results in NULL for `'true'`/`'false'`
strings in IRIS.

**Why P2**: 20 WithOrderBy1 scenarios. Fix is localised to the UNWIND sort-key path.

**Independent Test**: `UNWIND [true, false] AS b WITH b ORDER BY b LIMIT 1 RETURN b` → `false`

**Acceptance Scenarios**:

1. **Given** `UNWIND [true, false] ORDER BY bools LIMIT 1`, **Then** `bools = false`
2. **Given** `UNWIND [true, false] ORDER BY bools DESC LIMIT 1`, **Then** `bools = true`
3. **Given** `UNWIND [true, false, null] ORDER BY bools LIMIT 2`, **Then** `false, true` (null sorts last)

---

### User Story 5 — Fix variable-length path expansion (Cluster E, P2)

`MATCH (a)-[:TYPE*]->(c)` returns only direct neighbors instead of the full
transitive closure. The BFS engine (`^KG`) is invoked but relationship-type
filtering at each hop is either missing or returns only depth-1 results.

**Why P2**: 37 Match scenarios across 6 feature files. Some are currently returning partial results (1 hop instead of 3).

**Independent Test**: 3-level `LIKES` chain → `MATCH (a:A)-[:LIKES*]->(c) RETURN c.name` returns nodes at depths 1, 2, and 3

**Acceptance Scenarios**:

1. **Given** chain `A -[:LIKES]-> n00, n00 -[:LIKES]-> n000`, **When** `MATCH (a:A)-[:LIKES*]->(c)`, **Then** both `n00` and `n000` appear in results
2. **Given** unbounded `*`, **When** a cycle exists, **Then** no infinite loop (max_hops=100 respected)
3. **Given** `*1..2`, **When** 3-level chain, **Then** only depths 1 and 2 returned
4. **Given** mixed relationship types, **When** `[:LIKES*]`, **Then** only LIKES hops traversed

---

### User Story 6 — Temporal expression support (Cluster A, P3)

`date()`, `time()`, `localtime()`, `datetime()`, `localdatetime()`, `duration()`
constructor functions plus accessor properties and temporal arithmetic are
unimplemented or broken. 674 scenarios fail.

**Why P3**: Largest cluster, but requires substantial new infrastructure. Treat
as a sub-spec with phased delivery once non-temporal clusters are resolved.

**Independent Test**: `RETURN date({year: 2020, month: 6, day: 14}).year AS y` → `y = 2020`

**Acceptance Scenarios**:

1. **Given** `date({year: 1910, month: 5, day: 6})`, **When** compared to `date({year: 1980, month: 12, day: 24})`, **Then** 1910 date is less
2. **Given** `ORDER BY date_property ASC`, **Then** results are in chronological order
3. **Given** `date + duration({days: 1})`, **Then** result is next day
4. **Given** `date.year`, `date.month`, `date.day` accessor, **Then** correct integer returned

---

## Edge Cases

- `any(x IN [] WHERE pred)` → always `false` (empty list — vacuous false)
- `all(x IN [] WHERE pred)` → always `true` (vacuous truth)
- `MERGE` on a self-loop `(a)-[:T]->(a)` — INSERT and SELECT must both use same node for s and o_id
- `ORDER BY null` — nulls sort last ascending, first descending in Cypher
- `null XOR null` → `null` (not `false`)
- `null AND false` → `false` (short-circuit); `null AND true` → `null`
- `date({year: 1, month: 1, day: 1})` — minimum valid date
- VLP `*0..` — zero-length path (node matches itself); separate from `*1..`

---

## Requirements

### Functional Requirements

- **FR-001**: Boolean constant expressions evaluated at translate time MUST produce
  result rows where boolean columns surface as `true`/`false`, not integer `0`/`1`.
- **FR-002**: `MERGE (a)-[r:T]->(b)` MUST check `rdf_edges` for an existing
  `(s, p, o_id)` triple before inserting; must be duplicate-safe at SQL level.
- **FR-003**: `any()` and `all()` quantifier expressions MUST NOT pass a raw CTE
  column reference as `JSON_TABLE` source; wrap in a scalar subquery or materialise first.
- **FR-004**: Sort keys for boolean UNWIND values MUST map `'false'` → 0, `'true'` → 1
  so that ascending order yields `false` before `true`.
- **FR-005**: VLP `MATCH (a)-[:T*]->(b)` MUST traverse all hops up to `max_hops`
  and apply relationship-type filter at each hop.
- **FR-006**: `date({year, month, day})` MUST produce a value sortable and comparable
  in IRIS SQL without lexicographic collation.
- **FR-007**: No scenario currently passing (baseline 2930) MUST regress.

### Non-Functional Requirements

- **NFR-001**: Each root-cause fix MUST have ≥1 unit test asserting the corrected SQL shape or behaviour.
- **NFR-002**: TCK full-suite wall-clock time MUST NOT increase by more than 30 seconds vs baseline.
- **NFR-003**: All changes MUST be backward-compatible with existing `execute_cypher` callers.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Pass rate ≥ 80.0% (≥3118/3897 runnable scenarios)
- **SC-002**: Clusters C (Precedence) and F (Merge) each reach 100% for their affected feature files
- **SC-003**: No regression below 2930 baseline at any commit
- **SC-004**: Each cluster fix is independently commitable and verifiable via targeted `python -m behave tests/tck/features/<cluster>/` run

---

## Regressions and Ported Work (2026-09-25)

Same-namespace TCK runs (24 areas, `ivg-iris-enterprise`, scratch namespace)
showed scenarios that passed at the 201 branch point and failed on `main`, and
fixes that lived only on agent branches. Both are folded into this spec.

### Bisected regressions (commit `8ef268b`)

| Scenario               | Commit    | Fix                                 |
| ---------------------- | --------- | ----------------------------------- |
| WithOrderBy2/4         | `a6e0d6d` | ORDER BY checks aggregation scope   |
| Precedence1 [23], [25] | `bcdd94b` | List predicates computed once       |
| Comparison1 [10]       | `5332b6f` | `{id: <int>}` matches the property  |
| Temporal5 [1]          | `074270f` | weekYear uses `MOD()`, not `%`      |
| Graph3 [9]             | `feb1056` | `labels(<non-node>)` is a TypeError |

### Ported from agent branches

- **Temporal3 [3]–[11]** (`worktree-agent-a96a31b`): `_build_temporal_from_variable_map`
  was on `main` with no caller. Map-based `date`/`localdatetime`/`datetime`/`time`
  constructors now call it, and `time(var)` projects localtime, localdatetime and
  datetime values. Temporal3 183/183.
- **Temporal7 [6]**: the hours → days carry from `3b88ee5` is removed; a duration
  keeps days and seconds apart. Temporal7 17/18; [3] @1.1 (time comparison across
  offsets) fails on the agent tip too.
- **Precedence1 [26], [28]** (`worktree-agent-a634adb`): `b IN <JSON list>` is one
  scalar subquery returning 1, 0 or NULL, which the 3VL CASE passes through
  instead of repeating. `<=`/`>=` skip the NaN decomposition for that operand.
  Precedence1 72/72.

### Regression gate

- **FR-008**: A change to the translator MUST NOT turn any TCK scenario that
  passes on `main` into a failure, measured by a same-namespace run of both trees.

Result: `main` 2661/3079, this branch 2802/3079, no scenario that passes on
`main` fails here.

### Temporal runtime functions (full TCK)

Full TCK (all 3897 scenarios, `USER` namespace) went from 3600 at `6cc0d21` to
3724 (95.6%), with no regressions. Temporal rose from 957/1004 to 1002/1004.

- `SQLUser.CY_TEMPORAL_ARITH(a, op, b)` handles:
  - temporal ± duration
  - duration ± duration
  - duration × or ÷ a number
  - a numeric fallback for everything else

  `+`, `-`, `*` and `/` route to it when an operand is a temporal constructor
  or a runtime value starting with `P`. `LIST_CONCAT` also delegates
  scalar + scalar to it.

- `SQLUser.CY_DURATION_FIELD(v, f)` returns duration accessors as totals, as
  Neo4j does (`months` of `P1Y4M` is 16). Seconds are floored and
  `nanosecondsOfSecond` is never negative.
- `SQLUser.CY_TEMPORAL_FIELD(v, f)` covers the date, datetime and localdatetime
  accessors:
  - ISO `week` and `weekYear`
  - `dayOfQuarter` in leap years
  - `timezone` and `offset` with a `[Zone]` suffix
  - `epochSeconds` and `epochMillis` after applying the offset
- An accessor on a value whose type is only known at runtime (`WITH x AS d
RETURN d.hour`) dispatches on the string's shape.
- No-arg `date()`, `time()`, `localtime()`, `localdatetime()` and `datetime()`
  read one clock per statement instead of returning NULL.
- `duration.inSeconds(zoned, local)` reads the local operand in the zoned
  operand's zone. Map-built datetimes take their DST offset at the wall time,
  not at midnight.
- Ordering two zoned `time`/`datetime` literals compares instants.

Still failing in temporal: Temporal10 [9] and [10]. Their year ±999999999 dates
are outside Python's `datetime` range.

---

## Out of Scope

- GQL / ISO 39075 conformance
- IRIS ObjectScript or `Graph_KG` schema changes
- Vendored `.feature` file edits
- Fixing `@wip`-tagged scenarios excluded from the 3897 count
- Replacing the `tests/integration/test_cypher_*.py` integration suite
