<!--
SYNC IMPACT REPORT
==================
Version change: 1.1.1 → 1.2.0 (MINOR — new principle VII added)
Bump rationale: Added Principle VII (Cypher Conformance Gates) after the
  direction-symmetry cross-join bug was found independently in two Cypher
  implementations (cbm HEAD 571196db and IVG ≤v2.5.1).

  The openCypher TCK (~1,615 scenarios across 220 .feature files) has no
  Python-native runner — the official harness is Scala/JVM only.  Running TCK
  requires building a behave step-definition harness: that work is tracked as a
  future spec, not a current requirement.  Principle VII therefore specifies the
  conformance obligation (what to test), names the TCK as the authoritative
  reference, and sets a direction-symmetry gate as the minimum immediate bar.

Modified principles:
  - VII: new principle added — Cypher Conformance Gates

No other principles changed.  Templates do not require updates.
-->

# iris-vector-graph Constitution

## Core Principles

### I. Library-First

All features MUST be implemented within the library codebase and remain self-contained,
independently testable, and documented. Application-specific workarounds are not acceptable
as the primary interface.

### II. Compatibility-First Interfaces

Existing public interfaces (library API and any CLI entry points) MUST remain backward
compatible unless a documented breaking change is explicitly approved. New features MUST
extend, not replace, existing usage patterns.

### III. Test-First (Non-Negotiable)

All feature work MUST follow a test-first approach. Tests are written or updated before
implementation and MUST fail before code changes are introduced.

### IV. Integration & End-to-End Testing for IRIS

Any feature that includes IRIS as a backend component MUST include comprehensive end-to-end
(e2e) tests that run against a live IRIS container. The following rules are non-negotiable:

- The IRIS container MUST be named and dedicated to this project (not shared or anonymous).
  This project uses two containers:
  - `ivg-iris-enterprise` (Enterprise) — **primary test container**, port 31972. Use for
    ALL integration and unit tests that require a live IRIS connection. `ivg-iris` has
    MaxServerConn=1, which causes license failures under concurrent test runs.
  - `ivg-iris` (Community Edition) — **disabled for general use**, port 21972. Only start
    this container when explicitly testing Community-Edition-specific limits (e.g. ≤2 CPU
    cores constraint). Never use it as the default test target.
  NEVER use a container name from another project (e.g. `los-iris`, `posos-iris`).
- Container lifecycle MUST be managed exclusively via `scripts/test-container.sh` (Community)
  or `scripts/enterprise-container.sh` (Enterprise). IRIS ports MUST NOT be hardcoded in
  test code — use `IVG_PORT` / `IVG_ARNO_PORT` env vars with the registry defaults.
- Enterprise Arno callout tests MUST use the `arno_iris_connection` fixture, which auto-skips
  when `ivg-iris-enterprise` is not running. NEVER use `iris_connection` (Community) for tests
  requiring `libarno_callout.so`.
- The environment variable `SKIP_IRIS_TESTS` MUST default to `"false"`. Tests always hit the
  live database unless explicitly overridden by the developer.
- Changes that affect database behavior or SQL translation MUST additionally include
  integration tests (in `tests/integration/`) that validate behavior at the SQL layer.
- Unit tests alone are insufficient to satisfy this principle for IRIS-backend features.

**Rationale**: This project is a knowledge graph engine built on InterSystems IRIS. Behavior
that cannot be observed without a live database (vector indexing, SQL translation, schema
migration, Cypher execution) cannot be validated by mocks. Skipping live tests has
historically caused regressions discovered only by downstream consumers (`posos`, `iris-vector-rag`).

### V. Simplicity and Clarity

Prefer the simplest design that meets requirements. Avoid unnecessary abstractions or
over-engineering. Every layer of indirection MUST be justified by a concrete requirement.

### VI. Grounding Rule (Verify Before You Write)

Any infrastructure detail — container names, port numbers, schema names, credentials,
package names, file paths — written into specs, tests, templates, or commit messages MUST
first be verified against the authoritative source in this repository before use.

**Authoritative sources**:

- Container name → `docker-compose.yml` (`container_name:` field)
- IRIS port → `docker-compose.yml` (`ports:` field)
- Package name / version → `pyproject.toml`
- Schema prefix → `iris_vector_graph/engine.py` (`set_schema_prefix(...)` call)
- Test infrastructure → `tests/conftest.py`

**Never assume. Never copy from another project. Always look first.**

Violation of this rule caused the `los-iris` incident (Feb 2026): a container name from
an unrelated project was propagated into the constitution, all spec artifacts, and test
code before being caught. The fix required amending 8+ files. The cost is not acceptable.

### VII. Cypher Conformance Gates

#### Immediate gate (required now)

Any spec that modifies the Cypher translator (`cypher/translator.py`,
`cypher/parser.py`, or any `_engine/` mixin that generates SQL from a Cypher pattern)
MUST include an E2E integration test asserting **direction-symmetry**:

> For any directed pattern `(a)-[:R]->(b)` and its mirror `(b)<-[:R]-(a)`, when one
> of the variables is pre-bound from a preceding MATCH clause, both forms MUST return
> identical result sets against a live IRIS database.

This gate exists because the openCypher TCK has no scenario asserting direction
equivalence for directed patterns with a pre-bound target — the gap that let the same
cross-join bug ship independently in cbm (`571196db`) and IVG (≤v2.5.1).  Unit tests
cannot catch it; only a live database reveals wrong row counts.

Reference: `tests/integration/test_cypher_advanced.py::test_integration_direction_symmetry_optional_match`

#### Conformance resources to consult when changing the translator

- **openCypher TCK** (`github.com/opencypher/openCypher`, `tck/features/`):
  ~1,615 scenarios across 220 `.feature` files.  No Python runner exists — official
  harness is Scala/JVM only.  Key files for IVG's scope:
  `clauses/match/Match7.feature` (bound-target patterns),
  `clauses/match/` (pattern matching),
  `expressions/aggregation/` (count, collect),
  `expressions/existentialSubqueries/` (EXISTS).
- **openCypher OPTIONAL MATCH CIP**: `CIP2015-09-16-OPTIONAL-MATCH.adoc` in the
  same repo — normative semantics for pre-bound variable behavior.
- **cbm bug record**: `productivity-framework/specs/073-suite-failure-repair/CBM-BUG-optional-match-cross-join.md`

#### Future obligation (tracked, not yet required)

IVG has **not run the openCypher TCK**.  Spec 201 (`201-opencypher-tck-harness`)
MUST deliver a `behave`-based harness that runs **all 1,339 TCK scenarios**.
Every scenario that runs must pass at 100%.  Scenarios IVG cannot pass are tagged
`@wip` with a documented reason; only four categories are known `@wip` at spec
time: `expressions/graph` (Neo4j-specific), `expressions/temporal` (IRIS extension
model), `expressions/pattern` (partial), and `@NegativeTest` scenarios requiring
exact Neo4j error message text.  All other TCK categories — including all mutation
clauses — are in scope and must pass.

The `@wip` count MUST NOT increase on any PR.  Until the harness ships, the
direction-symmetry gate above is the minimum bar.

**Unit tests alone are insufficient** for translator changes that affect JOIN shape.

## Additional Constraints

- Use the existing RDF schema (`nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`,
  `kg_NodeEmbeddings`) unless a schema change is explicitly approved and documented.
- Numeric comparisons MUST be deterministic and documented when values are stored as strings.
- `iris-vector-rag` MUST NOT be added as a dependency of `iris-vector-graph`. The two
  packages are siblings; shared behavior belongs in `iris-vector-graph` or a new shared
  package, not through cross-dependency.

## Development Workflow

- All work MUST be traceable to a spec and plan.
- Feature changes MUST be grouped by user story to support incremental delivery.
- Every plan for a feature with an IRIS backend component MUST include an explicit e2e test
  task group (per Principle IV) as a non-optional phase, not as a polish/optional item.
- Before writing any infrastructure detail into a spec or test, verify it against the
  authoritative source (Principle VI). This is a blocking prerequisite, not a suggestion.

## Governance

This constitution supersedes all other development guidance for this repository. Any
amendments MUST be documented and explicitly approved before implementation begins.
Version increments follow semantic versioning: MAJOR for backward-incompatible governance
changes, MINOR for new or materially expanded principles, PATCH for clarifications.

**Version**: 1.2.0 | **Ratified**: 2026-01-31 | **Last Amended**: 2026-08-01
