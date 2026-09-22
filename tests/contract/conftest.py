"""Contract tests were written as TDD stubs, before the implementation.

This directory used to be skipped wholesale — `pytest_collection_modifyitems`
added `pytest.mark.skip` to every item whose path contained "contract", with no
condition attached. That made 48 tests permanently invisible, including the
`test_cypher_api*.py` cases for behaviour that shipped several releases ago, and
it read as a green directory rather than as an unmeasured one.

The blanket skip is now opt-outable: set `IVG_RUN_CONTRACT=1` to collect them
for real. It is still the default because several files here are stubs that
would error rather than fail — `test_graphql_schema.py` asserts `ImportError`
from `api.graphql.schema`, a module path that never existed (the GraphQL layer
shipped as `api.gql`), and `test_ppr_api.py` names an undefined
`iris_connection` inside a fixture.
"""

import os

import pytest

collect_ignore_glob = []

_RUN = os.environ.get("IVG_RUN_CONTRACT", "").lower() in {"1", "true", "yes"}


def pytest_collection_modifyitems(items, config):
    if _RUN:
        return
    for item in items:
        if "contract" in str(item.fspath):
            item.add_marker(
                pytest.mark.skip(
                    reason="TDD stub: set IVG_RUN_CONTRACT=1 to collect these for real"
                )
            )


@pytest.fixture()
def engine(iris_connection):
    """An `IRISGraphEngine` on the test container.

    `tests/integration/conftest.py` has one of these but contract tests cannot see it,
    and `test_ppr_api.py` worked around that with five class-level
    `def engine(self, engine)` fixtures — each requesting itself, which pytest reports
    as "recursive dependency involving fixture 'engine'" for all 13 of its tests.
    """
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(iris_connection)


@pytest.fixture(autouse=True)
def _api_app_on_the_test_connection(request):
    """Rebuild the FastAPI app around the test container's connection.

    `api.main` builds its module-level `app` at import time from `IRIS_HOST` /
    `IRIS_PORT`, which default to `localhost:1972` — not the test container — and
    swallows the failure: `except Exception: app = create_app(engine=None)`. With
    `engine=None` two separate things are unreachable, and neither says so
    usefully:

    * the REST dependency finds nothing on `app.state` and answers
      `HTTPException(500, "IRIS database connection is unavailable")`, so every
      contract assertion read `assert 500 == 200` / `assert 500 == 400`;
    * `create_app`'s `get_context` closes over `conn = None`, so `/graphql`
      resolvers raise `AttributeError: 'NoneType' object has no attribute
      'cursor'` — writing `app.state.db_connection` afterwards cannot reach that
      closure.

    So the app is constructed here instead, from the session `iris_connection`,
    and bound to the test module's `app` global (which each test reads when it
    calls `TestClient(app)`). Only modules that import `app` are touched, so the
    GraphQL stub files do not drag a container into their own run.
    """
    module = request.module
    if getattr(module, "app", None) is None:
        yield
        return

    from api.main import create_app

    from iris_vector_graph import IRISGraphEngine

    conn = request.getfixturevalue("iris_connection")
    previous = module.app
    module.app = create_app(engine=IRISGraphEngine(conn))
    try:
        yield
    finally:
        module.app = previous
