"""The GraphQL context, built in one place.

Every generic resolver in `api/gql/core/resolvers.py` opens with
``engine = info.context.get("engine")`` and returns empty when the key is absent. There
are two app factories — `api.gql.create_app` and `api.main.create_app` — and through
3.2.0 only the first put `engine` in the context, so `/graphql` as mounted by
`api.main` answered `nodes`, `node` and `stats` with `[]`, `None` and zeros against a
populated namespace, reporting no error. One builder, so the two cannot drift again.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from iris_vector_graph.engine import IRISGraphEngine


def build_graphql_context(
    engine: "Optional[IRISGraphEngine]", *, loaders: bool = True
) -> dict[str, Any]:
    """The context dict both factories hand to Strawberry.

    `engine` may be None (an app created with no reachable IRIS); the key is still
    present and falsy, which is what the resolvers test. `loaders=False` skips the
    biomedical DataLoaders for callers that do not mount the biomedical schema.
    """
    conn = engine.conn if engine is not None else None
    context: dict[str, Any] = {
        "engine": engine,
        "db_connection": conn,
        # The engine owns its connection's lifecycle; the request must not close it.
        "owns_connection": False,
    }
    if loaders:
        from api.gql.loaders import (
            EdgeLoader,
            GeneLoader,
            LabelLoader,
            PathwayLoader,
            PropertyLoader,
            ProteinLoader,
        )

        context.update(
            protein_loader=ProteinLoader(conn),
            gene_loader=GeneLoader(conn),
            pathway_loader=PathwayLoader(conn),
            edge_loader=EdgeLoader(conn),
            property_loader=PropertyLoader(conn),
            label_loader=LabelLoader(conn),
        )
    return context
