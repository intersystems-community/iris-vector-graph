# IRIS Interactive Demo Web Server

Interactive demonstration server showcasing IRIS capabilities for **Financial Services (fraud detection)** and **Biomedical Research (protein networks)**.

## Quick Start

```bash
# Install dependencies
uv sync

# Start backends
docker-compose -f ../../docker-compose.fraud-embedded.yml up -d
docker-compose -f ../../docker-compose.acorn.yml up -d

# Run demo server
uv run uvicorn app:app --reload --port 8200

# Access demo
open http://localhost:8200
```

## Features

### Financial Services Demo
- **Real-time fraud scoring**: Submit transactions, get risk assessment <2s
- **Bitemporal time-travel**: "What did we know at approval time?"
- **Audit trails**: Complete version history with chargeback workflow
- **Late arrival detection**: Flag suspicious settlement delays >24h

### Biomedical Demo
- **Protein search**: Vector similarity + text search with RRF fusion
- **Pathway queries**: Multi-hop protein interaction networks
- **Interactive visualization**: D3.js force-directed graphs
- **Network expansion**: Click nodes to explore connections

## Project Structure

```
iris_demo_server/
├── models/           # Pydantic models (session, fraud, biomedical, metrics)
├── services/         # Backend clients (fraud_client, bio_client, demo_state, demo_data)
├── routes/           # FastHTML endpoints (fraud, biomedical, session)
├── templates/        # FT components (base, fraud/, biomedical/, guided_tour)
├── static/
│   ├── js/          # network_viz.js (D3), demo_helpers.js
│   └── css/         # Styles
├── demo_data/       # Synthetic data (DEMO_MODE=true)
├── app.py           # FastHTML app entry point
└── register_asgi.py # IRIS ASGI registration
```

## Development Status

**Phase 3.1 Setup**: ✅ Complete (project structure, dependencies, linting)
**Phase 3.2 Tests**: ⏳ Ready (19 TDD tests to write)
**Phase 3.3 Implementation**: ⏳ Pending (27 tasks)
**Phase 3.4 Polish**: ⏳ Pending (6 tasks)

See [STATUS.md](../../specs/005-interactive-demo-web/STATUS.md) for details.

## Implementation Guide

For complete implementation patterns and examples, see:
- [IMPLEMENTATION_GUIDE.md](../../specs/005-interactive-demo-web/IMPLEMENTATION_GUIDE.md)

Key patterns:
- **Models**: Pydantic with validators (fraud.py, biomedical.py)
- **Services**: Resilient HTTP clients with circuit breaker (fraud_client.py)
- **Routes**: FastHTML async endpoints (fraud.py, biomedical.py)
- **Templates**: FT components with HTMX (base.py, fraud/, biomedical/)

## Environment Variables

```bash
# Demo mode (use synthetic data)
DEMO_MODE=false  # Set to 'true' for external demos

# Fraud API
FRAUD_API_URL=http://localhost:8100

# Biomedical graph
IRIS_HOST=localhost
IRIS_PORT=21972  # ACORN-1 (or 1972 for community)
IRIS_NAMESPACE=USER
IRIS_USER=_SYSTEM
IRIS_PASSWORD=SYS
```

## Testing

```bash
# Contract tests (API schemas)
pytest tests/demo/contract/ -v

# Integration tests (live backends required)
pytest tests/demo/integration/ -v -m integration

# E2E tests (Playwright)
pytest tests/demo/e2e/ -v --headed
```

## Architecture

- **Frontend**: FastHTML (server-rendered) + HTMX (reactive updates) + D3.js (viz)
- **State**: Session-based (FastHTML signed cookies), no persistent DB
- **Integration**: Fraud API (`:8100`), Biomedical graph (IRIS vector search)
- **Deployment**: IRIS ASGI registration (primary), uvicorn (dev)

## Performance Targets

- FR-002: Query responses <2 seconds
- Fraud API: <10ms backend calls
- Vector search: <200ms with HNSW
- HTMX swaps: <100ms UI updates
- D3 graphs: 60 FPS with 500 nodes

## References

- **Spec**: [../specs/005-interactive-demo-web/spec.md](../../specs/005-interactive-demo-web/spec.md)
- **Tasks**: [../specs/005-interactive-demo-web/tasks.md](../../specs/005-interactive-demo-web/tasks.md)
- **Quickstart**: [../specs/005-interactive-demo-web/quickstart.md](../../specs/005-interactive-demo-web/quickstart.md)
- **API Contract**: [../specs/005-interactive-demo-web/contracts/openapi.yaml](../../specs/005-interactive-demo-web/contracts/openapi.yaml)
