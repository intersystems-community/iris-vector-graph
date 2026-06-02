# iris-vector-graph Development Guidelines

## CRITICAL: External Actions Require Explicit Permission

**NEVER without explicit "file it" / "submit it" / "push it" instruction:**
- Create GitHub issues, PRs, Jira tickets
- Post to Slack, Teams, email
- Push to any remote git repository
- Deploy to any server or cloud service

Drafting is always OK. Filing/sending/deploying requires explicit permission.

## IRIS Test Containers

| Container | Port | Purpose | Start Command |
|-----------|------|---------|---------------|
| `ivg-iris` | 21972 | Community — primary tests | `scripts/test-container.sh up` |
| `ivg-iris-enterprise` | 31972 | Enterprise + Arno/rzf | `scripts/enterprise-container.sh up` |

Registry: `~/ws/productivity-framework/tools/lab_manager/config/iris-container-registry.yaml`

Arno tests (`TestBFSArnoE2E`) use `arno_iris_connection` fixture — auto-skip when enterprise container not running. Never hard-fail on Community-only machines.

## Active Technologies
- Python 3.10+ (`pyproject.toml`)
- ObjectScript (IRIS 2024.1+) — `iris_src/src/`
- `iris-devtester>=1.14.0`, `pytest>=7.4.0`

## Project Structure
```
iris_vector_graph/   # Core library (engine.py facade + _engine/ mixins)
iris_src/src/        # ObjectScript classes (Graph.KG.*)
scripts/             # test-container.sh, enterprise-container.sh
tests/unit/          # Unit + E2E tests
```

## Commands
```bash
pytest                                    # All tests
scripts/test-container.sh up             # Start Community container
scripts/enterprise-container.sh up       # Start Enterprise + Arno container
ruff check .                              # Lint
```

## Code Style
Python 3.10+, ObjectScript (IRIS 2024.1+): standard conventions.
