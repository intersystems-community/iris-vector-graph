# Implementation Guide: Interactive IRIS Demo Web Server

**Feature**: 005-interactive-demo-web
**Status**: Ready for implementation
**Total Tasks**: 50 (3 setup ✅, 47 remaining)

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Start fraud detection API (required for integration)
docker-compose -f docker-compose.fraud-embedded.yml up -d

# 3. Start biomedical graph backend
docker-compose -f docker-compose.acorn.yml up -d

# 4. Run demo server (development mode)
cd src/iris_demo_server
uv run uvicorn app:app --reload --port 8200

# 5. Access demo
open http://localhost:8200
```

## Architecture Summary

### Tech Stack
- **Frontend**: FastHTML (server-rendered components) + HTMX (reactive updates) + D3.js (graph viz)
- **Backend**: FastHTML app integrating with:
  - Fraud API (`:8100/fraud/score`) - Licensed IRIS with 130M transactions
  - Biomedical graph (iris module) - Vector search, RRF fusion, pathways
- **Deployment**: IRIS ASGI registration (primary), uvicorn (dev fallback)
- **State**: Session-based (FastHTML signed cookies), no persistent DB

### Project Structure
```
src/iris_demo_server/
├── models/           # Pydantic data models (session, fraud, bio, metrics)
├── services/         # Backend clients (fraud_client, bio_client, demo_state, demo_data)
├── routes/           # FastHTML endpoints (fraud, biomedical, session)
├── templates/        # FT components (base, fraud/, biomedical/, guided_tour)
├── static/
│   ├── js/          # network_viz.js (D3 canvas), demo_helpers.js
│   └── css/         # Styles
├── demo_data/       # Synthetic data for DEMO_MODE=true
├── app.py           # FastHTML app entry point
└── register_asgi.py # IRIS ASGI registration

tests/demo/
├── contract/        # API contract tests (11 endpoints)
├── integration/     # End-to-end scenarios (8 acceptance tests)
└── e2e/            # Playwright tests (2 workflows)
```

## Implementation Workflow

### Phase 3.2: Tests First (TDD) - Tasks T004-T022

**CRITICAL**: All tests MUST be written and MUST FAIL before Phase 3.3 implementation.

#### Contract Tests (T004-T014) - Independent, Run in Parallel

Pattern for contract tests:
```python
# tests/demo/contract/test_fraud_score.py
import pytest
from fasthtml.common import *

def test_fraud_score_contract():
    """Test POST /api/fraud/score contract (FR-006, FR-007)"""
    # Arrange
    request_body = {
        "payer": "acct:test_user",
        "amount": 1500.00,
        "device": "dev:laptop",
        "merchant": "merch:electronics",
        "ip_address": "192.168.1.100"
    }

    # Act
    response = app.post("/api/fraud/score", json=request_body)

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    assert "metrics" in data

    # Validate FraudScoringResult schema
    result = data["result"]
    assert 0.0 <= result["fraud_probability"] <= 1.0
    assert result["risk_classification"] in ["low", "medium", "high", "critical"]
    assert isinstance(result["contributing_factors"], list)
    assert "scoring_timestamp" in result

    # Validate QueryPerformanceMetrics schema
    metrics = data["metrics"]
    assert "execution_time_ms" in metrics
    assert "backend_used" in metrics
    assert metrics["query_type"] == "fraud_score"
```

**Tasks T004-T014** (11 contract tests):
1. T004: test_fraud_score.py (POST /api/fraud/score)
2. T005: test_fraud_bitemporal.py (POST /api/fraud/bitemporal)
3. T006: test_fraud_audit.py (GET /api/fraud/audit/{event_id})
4. T007: test_fraud_late_arrivals.py (GET /api/fraud/late-arrivals)
5. T008: test_bio_search.py (POST /api/bio/search)
6. T009: test_bio_pathway.py (POST /api/bio/pathway)
7. T010: test_bio_hybrid.py (POST /api/bio/hybrid-search)
8. T011: test_bio_expand.py (GET /api/bio/network/{protein_id}/expand)
9. T012: test_session_mode.py (POST /api/session/switch-mode)
10. T013: test_session_history.py (GET /api/session/history)
11. T014: test_session_export.py (POST /api/session/export)

**Run all contract tests**:
```bash
pytest tests/demo/contract/ -v
# Expected: 11 failed (no implementation yet)
```

#### Integration Tests (T015-T022) - Independent, Run in Parallel

Pattern for integration tests:
```python
# tests/demo/integration/test_fraud_scoring.py
import pytest
from src.iris_demo_server.services.fraud_client import FraudAPIClient

@pytest.mark.integration
@pytest.mark.asyncio
async def test_fraud_scoring_e2e():
    """Test fraud scoring end-to-end (Scenario 1 from quickstart.md)"""
    # Arrange
    client = FraudAPIClient(base_url="http://localhost:8100", demo_mode=False)
    transaction = {
        "payer": "acct:demo_user_001",
        "amount": 1500.00,
        "device": "dev:laptop_chrome",
        "merchant": "merch:electronics_store",
        "ip_address": "192.168.1.100"
    }

    # Act
    import time
    start = time.time()
    result = await client.score_transaction(transaction)
    execution_time = (time.time() - start) * 1000

    # Assert
    assert execution_time < 2000  # FR-002: <2s response
    assert 0.0 <= result.fraud_probability <= 1.0
    assert result.risk_classification in ["low", "medium", "high", "critical"]
    assert len(result.contributing_factors) > 0
    assert result.scoring_model in ["MLP", "graph_centrality"]
```

**Tasks T015-T022** (8 integration tests):
1. T015: test_fraud_scoring.py (real-time scoring <2s)
2. T016: test_bitemporal.py (time-travel queries)
3. T017: test_late_arrivals.py (settlement delay detection)
4. T018: test_audit_trail.py (complete version history)
5. T019: test_protein_search.py (top-k similarity results)
6. T020: test_pathway.py (multi-hop pathways)
7. T021: test_hybrid_search.py (RRF fusion)
8. T022: test_network_expansion.py (node neighbors)

**Run all integration tests** (requires live backends):
```bash
# Start backends first
docker-compose -f docker-compose.fraud-embedded.yml up -d
docker-compose -f docker-compose.acorn.yml up -d

pytest tests/demo/integration/ -v -m integration
# Expected: 8 failed (no implementation yet)
```

### Phase 3.3: Core Implementation - Tasks T023-T041

**DEPENDENCY**: Phase 3.2 tests MUST be failing before starting

#### Models (T023-T026) - Independent, Run in Parallel

Pattern for models (using Pydantic for validation):
```python
# src/iris_demo_server/models/fraud.py
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from enum import Enum

class RiskClassification(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class FraudTransactionQuery(BaseModel):
    """User-submitted transaction for fraud scoring (FR-006)"""
    payer: str = Field(..., pattern=r'^acct:.+', max_length=100)
    payee: Optional[str] = Field(None, pattern=r'^acct:.+')
    amount: Decimal = Field(..., gt=0, le=1_000_000.00)
    device: str = Field(..., pattern=r'^dev:.+', max_length=100)
    merchant: Optional[str] = Field(None, pattern=r'^merch:.+')
    ip_address: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_schema_extra = {
            "example": {
                "payer": "acct:user_12345",
                "amount": 1500.00,
                "device": "dev:laptop_001",
                "merchant": "merch:store_789",
                "ip_address": "192.168.1.100"
            }
        }

class FraudScoringResult(BaseModel):
    """Fraud probability and risk assessment (FR-007)"""
    fraud_probability: float = Field(..., ge=0.0, le=1.0)
    risk_classification: RiskClassification
    contributing_factors: List[str]
    scoring_timestamp: datetime
    scoring_model: str
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    @field_validator('risk_classification', mode='before')
    @classmethod
    def classify_risk(cls, v, info):
        """Auto-classify based on fraud_probability if not provided"""
        if isinstance(v, str):
            return v
        prob = info.data.get('fraud_probability', 0.0)
        if prob < 0.30:
            return RiskClassification.LOW
        elif prob < 0.60:
            return RiskClassification.MEDIUM
        elif prob < 0.85:
            return RiskClassification.HIGH
        else:
            return RiskClassification.CRITICAL
```

**Tasks T023-T026** (4 model files):
1. T023: models/session.py (DemoSession, QueryHistoryEntry)
2. T024: models/fraud.py (FraudTransactionQuery, FraudScoringResult, BitemporalQuery/Result, LateArrivalTransaction)
3. T025: models/biomedical.py (ProteinQuery, ProteinSearchResult, PathwayQuery, InteractionNetwork)
4. T026: models/metrics.py (QueryPerformanceMetrics)

#### Services (T027-T030) - Independent After Models, Run in Parallel

Pattern for HTTP client with circuit breaker:
```python
# src/iris_demo_server/services/fraud_client.py
import httpx
import asyncio
import time
from typing import Optional, Dict, Any
from pathlib import Path
import json

class CircuitBreaker:
    """Exponential backoff circuit breaker"""
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half_open

    def is_open(self) -> bool:
        if self.state == "open":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "half_open"
                return False
            return True
        return False

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"

class FraudAPIClient:
    """Resilient fraud API client with circuit breaker"""
    def __init__(self, base_url: str = "http://localhost:8100", demo_mode: bool = False):
        self.base_url = base_url
        self.demo_mode = demo_mode
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            http2=True
        )
        self.circuit_breaker = CircuitBreaker()
        self._demo_data = self._load_demo_data()

    def _load_demo_data(self) -> Dict[str, Any]:
        """Load cached demo data"""
        demo_file = Path(__file__).parent.parent / "demo_data" / "fraud_scores.json"
        if demo_file.exists():
            return json.loads(demo_file.read_text())
        return {"default_score": 0.15, "risk": "low"}

    async def score_transaction(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Score transaction with circuit breaker fallback"""
        if self.demo_mode or self.circuit_breaker.is_open():
            return self._get_demo_score(transaction)

        try:
            response = await self.client.post(
                f"{self.base_url}/fraud/score",
                json=transaction
            )
            response.raise_for_status()
            self.circuit_breaker.record_success()
            return response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            self.circuit_breaker.record_failure()
            return self._get_demo_score(transaction)

    def _get_demo_score(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback to demo data"""
        # Simple heuristic for demo
        amount = transaction.get("amount", 0)
        if amount > 10000:
            prob = 0.85
            risk = "critical"
        elif amount > 5000:
            prob = 0.65
            risk = "high"
        elif amount > 2000:
            prob = 0.35
            risk = "medium"
        else:
            prob = 0.15
            risk = "low"

        return {
            "fraud_probability": prob,
            "risk_classification": risk,
            "contributing_factors": ["Demo mode - heuristic scoring"],
            "scoring_timestamp": datetime.utcnow().isoformat(),
            "scoring_model": "demo_heuristic",
            "confidence": 1.0
        }
```

**Tasks T027-T030** (4 service files):
1. T027: services/fraud_client.py (ResilientAPIClient, CircuitBreaker)
2. T028: services/bio_client.py (IRIS graph client for proteins)
3. T029: services/demo_state.py (Session management, mode switching)
4. T030: services/demo_data.py (Faker-based synthetic data generator)

#### Routes (T031-T036) - Sequential After Services

Pattern for FastHTML routes:
```python
# src/iris_demo_server/routes/fraud.py
from fasthtml.common import *
from typing import Dict, Any
from ..models.fraud import FraudTransactionQuery, FraudScoringResult
from ..models.metrics import QueryPerformanceMetrics
from ..services.fraud_client import FraudAPIClient
import time

def fraud_routes(app: FastHTML):
    """Register fraud detection demo routes"""
    fraud_client = FraudAPIClient(
        base_url="http://localhost:8100",
        demo_mode=os.getenv("DEMO_MODE", "false").lower() == "true"
    )

    @app.post("/api/fraud/score")
    async def score_fraud_transaction(query: FraudTransactionQuery):
        """Score transaction for fraud (FR-006, FR-007)"""
        start_time = time.time()

        try:
            # Call fraud API
            result = await fraud_client.score_transaction(query.model_dump())

            # Build response
            scoring_result = FraudScoringResult(**result)
            metrics = QueryPerformanceMetrics(
                query_type="fraud_score",
                execution_time_ms=int((time.time() - start_time) * 1000),
                backend_used="fraud_api" if not fraud_client.circuit_breaker.is_open() else "cached_demo",
                result_count=1,
                search_methods=["MLP"],
                timestamp=datetime.utcnow()
            )

            return {
                "result": scoring_result.model_dump(),
                "metrics": metrics.model_dump()
            }
        except Exception as e:
            # Error handling
            raise HTTPException(status_code=503, detail=str(e))

    # Additional fraud routes...
```

**Tasks T031-T036** (6 route/app files):
1. T031: routes/fraud.py (4 fraud endpoints)
2. T032: routes/biomedical.py (4 biomedical endpoints)
3. T033: routes/session.py (3 session endpoints)
4. T034: app.py (FastHTML app, route registration)
5. T035: app.py error handling middleware
6. T036: services/metrics_logger.py (structured logging)

#### Templates (T037-T041b) - Independent After Routes, Run in Parallel

Pattern for FastHTML templates:
```python
# src/iris_demo_server/templates/base.py
from fasthtml.common import *

def base_layout(content, mode: str = "fraud"):
    """Base HTML layout with HTMX"""
    return Html(
        Head(
            Title("IRIS Interactive Demo"),
            Link(rel="stylesheet", href="/static/css/demo.css"),
            Script(src="https://unpkg.com/htmx.org@2.0.0"),
            Script(src="https://d3js.org/d3.v7.min.js"),
        ),
        Body(
            # Header with mode tabs
            Div(
                H1("IRIS Capabilities Demo"),
                Div(
                    A("Financial Services",
                      href="/",
                      cls=f"tab {'active' if mode == 'fraud' else ''}",
                      hx_get="/",
                      hx_swap="outerHTML",
                      hx_target="body"),
                    A("Biomedical Research",
                      href="/biomedical",
                      cls=f"tab {'active' if mode == 'biomedical' else ''}",
                      hx_get="/biomedical",
                      hx_swap="outerHTML",
                      hx_target="body"),
                    cls="tabs"
                ),
                cls="header"
            ),

            # DEMO_MODE banner
            Div(
                "⚠️ Demo Mode Active - Using synthetic data",
                cls="demo-banner"
            ) if os.getenv("DEMO_MODE") == "true" else None,

            # Main content
            Div(content, id="main-content", cls="container"),

            # Scripts
            Script(src="/static/js/demo_helpers.js"),
            cls=f"mode-{mode}"
        )
    )
```

**Tasks T037-T041b** (6 template/static files):
1. T037: templates/base.py (base layout, tab navigation)
2. T038: templates/fraud/ (scoring_form, bitemporal_query, audit_trail, results_table)
3. T039: templates/biomedical/ (protein_search, pathway_query, results_table, network_viz_container)
4. T040: static/js/network_viz.js (D3 canvas rendering, Barnes-Hut)
5. T041: static/js/demo_helpers.js (HTMX utilities, formatters)
6. T041b: templates/guided_tour.py (FR-020 onboarding)

### Phase 3.4: Integration & Polish - Tasks T042-T049

#### E2E Tests (T042-T043)

Pattern for Playwright tests:
```python
# tests/demo/e2e/test_fraud_e2e.py
from playwright.sync_api import Page, expect

def test_fraud_demo_workflow(page: Page):
    """Test complete fraud demo workflow (quickstart.md Steps 1-4)"""
    # Step 1: Load homepage
    page.goto("http://localhost:8200")
    expect(page.locator("h1")).to_contain_text("IRIS Capabilities Demo")
    expect(page.locator(".tab.active")).to_contain_text("Financial Services")

    # Step 2: Submit transaction
    page.fill("input[name='payer']", "acct:demo_user_001")
    page.fill("input[name='amount']", "1500.00")
    page.fill("input[name='device']", "dev:laptop_chrome")
    page.fill("input[name='merchant']", "merch:electronics_store")
    page.click("button[type='submit']")

    # Verify fraud score displayed within 2s
    expect(page.locator("#fraud-score")).to_be_visible(timeout=2000)
    score_text = page.locator("#fraud-score").inner_text()
    assert "probability" in score_text.lower()

    # Step 3: Bitemporal query
    page.click("a[href='/fraud/bitemporal']")
    page.fill("input[name='event_id']", "txn_2025_001")
    page.fill("input[name='system_time']", "2025-01-15T14:00:00Z")
    page.click("button#query-bitemporal")

    expect(page.locator("#historical-state")).to_be_visible()

    # Step 4: Audit trail
    page.click("a[href='/fraud/audit']")
    page.fill("input[name='event_id']", "txn_2025_001")
    page.click("button#view-audit")

    expect(page.locator(".version-timeline")).to_be_visible()
    versions = page.locator(".version-item").count()
    assert versions >= 4  # Chargeback workflow has 4 versions
```

**Tasks T042-T043** (2 E2E test files):
1. T042: e2e/test_fraud_e2e.py (fraud demo workflow, Steps 1-4)
2. T043: e2e/test_bio_e2e.py (biomedical demo workflow, Steps 5-8)

#### Deployment (T044-T046)

IRIS ASGI registration pattern:
```python
# src/iris_demo_server/register_asgi.py
"""Register FastHTML app with IRIS web server via ASGI"""
import os
from pathlib import Path

def register_demo_server():
    """Register demo server with IRIS ASGI support"""
    # Note: iris-devtools integration would go here
    # For now, document the manual registration steps

    registration_script = """
# IRIS ASGI Registration for Demo Server

## Prerequisites
- IRIS 2024.1+ with embedded Python enabled
- iris-devtools package installed

## Registration Steps

1. Import ASGI module in IRIS:
```objectscript
Do ##class(%SYS.Python).Import("iris_asgi")
```

2. Register FastHTML app:
```python
import iris_asgi
from src.iris_demo_server.app import app

iris_asgi.register_app(
    app=app,
    route_prefix="/demo",
    port=52773
)
```

3. Verify registration:
```bash
curl http://localhost:52773/demo/
```

## Fallback: uvicorn Development Mode
```bash
cd src/iris_demo_server
uvicorn app:app --reload --port 8200
```
"""

    print(registration_script)

if __name__ == "__main__":
    register_demo_server()
```

**Tasks T044-T046** (3 deployment files):
1. T044: register_asgi.py (IRIS ASGI registration script)
2. T045: docker/Dockerfile.demo (IRIS base + demo server)
3. T046: docker-compose.demo.yml (full demo stack)

#### Documentation & Validation (T047-T049)

**Tasks T047-T049** (3 validation tasks):
1. T047: Update quickstart.md with deployment commands
2. T048: Execute complete quickstart.md walkthrough
3. T049: Performance validation (scripts/performance/test_demo_performance.py)

## Quality Gates

### After Phase 3.2 (Tests)
```bash
# Verify all 19 tests are failing
pytest tests/demo/ -v
# Expected: 19 failed, 0 passed

# Commit checkpoint
git add tests/demo/
git commit -m "feat(demo): add contract and integration tests (TDD red phase)"
```

### After Phase 3.3 (Implementation)
```bash
# Verify tests now pass
pytest tests/demo/ -v
# Expected: 19 passed (TDD green phase)

# Run linting
black src/iris_demo_server/
isort src/iris_demo_server/
flake8 src/iris_demo_server/
mypy src/iris_demo_server/

# Commit checkpoint
git add src/iris_demo_server/
git commit -m "feat(demo): implement core demo server (TDD green phase)"
```

### After Phase 3.4 (E2E + Deployment)
```bash
# Verify E2E tests pass
pytest tests/demo/e2e/ -v --headed
# Expected: 2 passed

# Verify deployment works
docker-compose -f docker-compose.demo.yml up -d
curl http://localhost:52773/demo/

# Run quickstart walkthrough
./specs/005-interactive-demo-web/quickstart.md

# Commit final
git add docker/ specs/
git commit -m "feat(demo): add deployment and E2E validation"
```

## Performance Targets

- **FR-002**: Query responses <2 seconds
- **Fraud API**: <10ms backend calls
- **Vector search**: <200ms with HNSW
- **HTMX swaps**: <100ms UI updates
- **D3 graphs**: 60 FPS with 500 nodes
- **Session state**: <16ms cookie read/write

## Troubleshooting

### Demo server won't start
```bash
# Check port availability
lsof -i :8200

# Check dependencies
uv sync
uv run python -c "import fasthtml; print('FastHTML OK')"
```

### Tests failing due to backend unavailable
```bash
# Start fraud API
docker-compose -f docker-compose.fraud-embedded.yml up -d
curl http://localhost:8100/fraud/health

# Start biomedical graph
docker-compose -f docker-compose.acorn.yml up -d
docker exec -it iris-acorn-1 /bin/bash -c "irissession IRIS -U USER"
```

### Slow performance (<2s requirement failing)
```bash
# Enable demo mode for cached data
export DEMO_MODE=true
docker-compose -f docker-compose.demo.yml restart

# Check backend health
docker logs iris-fraud-embedded --tail 50
```

## Next Steps

1. **Start with MVD** (Minimum Viable Demo):
   - Implement T004 (fraud score contract test)
   - Implement T023, T024 (models)
   - Implement T027 (fraud client)
   - Implement T031, T034 (routes, app)
   - Implement T037, T038 (templates)
   - Verify end-to-end: `pytest tests/demo/contract/test_fraud_score.py -v`

2. **Expand systematically**:
   - Add remaining fraud endpoints (bitemporal, audit, late-arrivals)
   - Add biomedical endpoints (search, pathway, hybrid, expand)
   - Add session management (switch-mode, history, export)
   - Add E2E tests (Playwright workflows)

3. **Deploy and validate**:
   - Register with IRIS ASGI
   - Run complete quickstart walkthrough
   - Performance benchmarks
   - Documentation screenshots

## References

- **Spec**: [spec.md](./spec.md) - WHAT users need (24 requirements)
- **Plan**: [plan.md](./plan.md) - HOW to build (architecture, constitution)
- **Research**: [research.md](./research.md) - Technology decisions (FastHTML, D3, circuits)
- **Data Model**: [data-model.md](./data-model.md) - Entities and relationships (12 models)
- **Contracts**: [contracts/openapi.yaml](./contracts/openapi.yaml) - API specifications (11 endpoints)
- **Quickstart**: [quickstart.md](./quickstart.md) - End-to-end walkthrough (10 steps)
- **Tasks**: [tasks.md](./tasks.md) - Complete task breakdown (50 tasks)

---

**Implementation Status**: Setup complete (T001-T003 ✅), Ready for Phase 3.2 (Tests First)
