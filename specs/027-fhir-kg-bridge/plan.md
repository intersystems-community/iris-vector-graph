# Implementation Plan: FHIR-KG Clinical Bridge

**Branch**: `027-fhir-kg-bridge` | **Date**: 2026-05-10 | **Spec**: specs/027-fhir-kg-bridge/spec.md
**Input**: Feature specification from `/specs/027-fhir-kg-bridge/spec.md`

## Summary

Implement the FHIR-to-KG bridge layer: `get_kg_anchors()` resolves ICD-10 codes to KG node IDs via the bridge table, `unified_clinical_pipeline()` chains FHIR search → anchor resolution → PPR walk with full provenance. MCP tool wrappers enable AI agents to access these functions. Pure Python against existing schema.

## Technical Context

**Language/Version**: Python 3.11+ (project target per AGENTS.md)
**Primary Dependencies**: `iris_vector_graph` (engine, schema), `requests` (FHIR REST client)
**Storage**: Existing `Graph_KG.fhir_bridges` table + `Graph_KG.nodes`
**Testing**: pytest, live IRIS container (gqs-ivg-test), synthetic patient fixtures
**Target Platform**: Linux/macOS, IRIS 2025.1+ (Community or Enterprise)
**Project Type**: Library extension (new module `iris_vector_graph/fhir_bridge.py`)
**Performance Goals**: Anchor resolution <10ms for 3 ICD codes; post-FHIR pipeline <500ms
**Constraints**: No new IRIS classes; FHIR client timeout configurable (default 10s); graceful degradation when bridge table empty
**Scale/Scope**: 50K+ bridge mappings, 3 demo patients for testing, 2 MCP tools

## Constitution Check

- [x] A dedicated, named IRIS container (`iris_vector_graph`) managed by `iris-devtester`
- [x] An explicit e2e test phase (non-optional) covering all user stories
- [x] `SKIP_IRIS_TESTS` defaulting to `"false"` in all new test files
- [x] No hardcoded IRIS ports; resolved via iris_connection fixture from conftest
- [x] Principle VI: container name verified from docker-compose.yml
- [x] Principle I: Library-first — `fhir_bridge.py` is a self-contained module
- [x] Principle II: Backward compatible — no existing API changes
- [x] Principle III: Test-first — tests written before implementation
- [x] Principle V: Simplicity — single new file, no new abstractions

## Project Structure

```text
iris_vector_graph/
├── fhir_bridge.py           # NEW: get_kg_anchors, unified_clinical_pipeline, FHIR client
├── engine.py                # Unchanged (uses existing kg_PERSONALIZED_PAGERANK)
└── schema.py                # Unchanged (fhir_bridges table already defined)

tests/
├── unit/test_fhir_bridge.py # Unit tests with mocked FHIR responses
└── e2e/test_fhir_bridge_e2e.py  # E2E tests against live IRIS + synthetic patients

specs/027-fhir-kg-bridge/
├── spec.md
├── plan.md (this file)
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── fhir_bridge_api.md
└── tasks.md
```

## Phase 0: Research

No NEEDS CLARIFICATION items remain after the clarify session. Key decisions already made:

| Decision | Rationale | Alternatives Considered |
|----------|-----------|------------------------|
| ICD codes as input (not cross-namespace SQL) | Decouples IVG from FHIR schema; caller extracts codes | Cross-namespace SQL query to FHIR tables |
| FHIR client timeout independent (10s default) | Separates network concerns from KG processing latency | Pipeline-wide 500ms timeout (too restrictive) |
| Partial results on empty PPR | Lets caller decide if anchors alone are useful | Silent empty return; exception |
| Pure Python + existing schema | No new IRIS classes needed; fastest path to production | New ObjectScript persistent class |
| `requests` for FHIR client | Standard, handles BasicAuth, well-tested | `httpx` (async not needed here); `fhirclient` (too heavy) |

## Phase 1: Design

### Data Model

Already exists — see `specs/027-fhir-kg-bridge/data-model.md`. Key table:

```
Graph_KG.fhir_bridges
├── fhir_code: VARCHAR — ICD-10-CM code (e.g., "E11.9")
├── fhir_code_system: VARCHAR — vocabulary identifier ("ICD-10-CM")
├── kg_node_id: VARCHAR — target KG node (e.g., "mesh:D003924")
├── bridge_type: VARCHAR — mapping type ("icd10_to_mesh")
├── confidence: FLOAT — mapping confidence score
└── source_cui: VARCHAR — UMLS CUI for provenance
```

### API Contract

**`get_kg_anchors(engine, icd_codes: list[str]) -> list[str]`**
- Input: list of ICD-10 codes
- Output: list of KG node IDs that exist in Graph_KG.nodes
- Empty input → empty output
- Empty bridge table → empty output + log warning

**`unified_clinical_pipeline(engine, query, fhir_base_url, fhir_auth=None, top_k=10, ppr_top_k=20, vector_search_param=None) -> dict`**
- Output: `{"anchors": [...], "ppr_results": [...], "provenance": [...], "fhir_patients": [...], "status": "ok"|"anchors_resolved_but_no_graph_connectivity"|"no_bridges_loaded"}`
- FHIR timeout: configurable via kwarg (default 10s)
- Post-FHIR processing: <500ms

**MCP Tools (P2):**
- `FHIRSearchTool(base_url, auth, resource_types)` — searches FHIR, returns structured summary
- `GetPatientKGNeighborhoodTool(engine, fhir_base_url)` — patient_id → graph neighborhood

### Quickstart

```python
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.fhir_bridge import get_kg_anchors, unified_clinical_pipeline

engine = IRISGraphEngine(conn, embedding_dimension=768)

# Direct anchor resolution
anchors = get_kg_anchors(engine, ["E11.9", "I10"])
# → ["mesh:D003924", "mesh:D006973"]

# Full pipeline
result = unified_clinical_pipeline(
    engine,
    query="diabetes hypertension management",
    fhir_base_url="http://localhost:52773/fhir/r4",
    top_k=10,
)
# → {"anchors": [...], "ppr_results": [...], "provenance": [...], "status": "ok"}
```

## Agent Context Update

Technologies added by this feature:
- `requests` (FHIR REST client, already in `[full]` extras)
- `iris_vector_graph/fhir_bridge.py` (new module)
- MCP tool pattern (same as existing mindwalk_tools.py)
