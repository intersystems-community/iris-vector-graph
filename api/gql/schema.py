"""
GraphQL schema composition.

Combines Query, types, and loaders into complete Strawberry GraphQL schema.
"""

import strawberry
from .resolvers.query import Query

# Create GraphQL schema
schema = strawberry.Schema(
    query=Query,
    # mutation=Mutation,  # Phase 6: T025-T027
    # subscription=Subscription,  # Phase 7: T028-T029
)
