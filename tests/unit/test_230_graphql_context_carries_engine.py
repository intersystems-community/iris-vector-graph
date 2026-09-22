"""The GraphQL context must carry the engine, whichever factory built the app.

`api/gql/core/resolvers.py` opens every generic resolver with

    engine = info.context.get("engine")
    if not engine:
        return []

and there are two app factories. `api.gql.create_app` put `engine` in the context;
`api.main.create_app` — the one that mounts `/graphql`, the one `api/main.py:__main__`
serves and the one the docs and tests use — built a context of loaders and
`db_connection` only. So `nodes`, `node` and `stats` answered `[]`, `None` and zeros
against a fully populated namespace, with no GraphQL error to say why.

`tests/e2e/test_stress_api.py::test_gql_nodes_query_with_label` had been asking for
`nodes(label: ...)`, which GraphQL rejects at validation, so it failed on the spelling
and never reached the empty result underneath. Correcting the spelling to `labels` is
what exposed this.

A stub engine is enough: the claim is about what goes in the context, not about what the
resolvers then do with it.
"""

import pytest

from api.gql.context import build_graphql_context


class _StubEngine:
    """Enough engine for the context builder: it only reads `.conn`."""

    def __init__(self):
        self.conn = object()


LOADER_KEYS = (
    "protein_loader",
    "gene_loader",
    "pathway_loader",
    "edge_loader",
    "property_loader",
    "label_loader",
)


def test_the_context_carries_the_engine():
    engine = _StubEngine()
    ctx = build_graphql_context(engine)
    assert ctx["engine"] is engine, (
        "without `engine` in the context every CoreQuery resolver returns empty and "
        f"reports no error; context keys were {sorted(ctx)}"
    )


def test_the_context_carries_the_connection_and_loaders():
    engine = _StubEngine()
    ctx = build_graphql_context(engine)
    assert ctx["db_connection"] is engine.conn
    assert ctx["owns_connection"] is False, "closing the engine's connection per request"
    for key in LOADER_KEYS:
        assert ctx.get(key) is not None, key


def test_an_engineless_app_gets_a_present_but_falsy_engine():
    # `create_app()` with no reachable IRIS must not fabricate an engine, and the key
    # must still exist: the resolvers test it with `.get("engine")`.
    ctx = build_graphql_context(None)
    assert "engine" in ctx
    assert ctx["engine"] is None
    assert ctx["db_connection"] is None


def test_loaders_can_be_left_out():
    ctx = build_graphql_context(_StubEngine(), loaders=False)
    assert ctx["engine"] is not None
    for key in LOADER_KEYS:
        assert key not in ctx


@pytest.mark.parametrize("module", ["api.main", "api.gql"])
def test_both_factories_build_their_context_from_the_one_builder(module):
    """A second hand-rolled context dict is how the two drifted in the first place."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert "build_graphql_context" in src, (
        f"{module}.create_app builds its own GraphQL context dict; use "
        "api.gql.context.build_graphql_context so the two cannot disagree"
    )
