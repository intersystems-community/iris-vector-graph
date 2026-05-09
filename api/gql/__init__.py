"""
GraphQL API module for IRIS Vector Graph.

Provides the Strawberry GraphQL schema and context setup.
"""

from typing import TYPE_CHECKING, Optional, Any
from strawberry.fastapi import GraphQLRouter

if TYPE_CHECKING:
    from iris_vector_graph.engine import IRISGraphEngine


def create_app(engine: "IRISGraphEngine") -> "GraphQLRouter":
    """
    Create a Strawberry GraphQL application.
    
    Sets up the schema with context containing the engine and database connection.
    
    Args:
        engine: IRISGraphEngine instance
        
    Returns:
        GraphQLRouter for FastAPI integration
    """
    from .schema import schema
    
    async def get_context() -> dict[str, Any]:
        """Build GraphQL context with engine and connection."""
        return {
            "engine": engine,
            "db_connection": engine.conn,
            "owns_connection": False,  # Engine manages its own connection lifecycle
        }
    
    return GraphQLRouter(
        schema,
        context_getter=get_context,
    )
