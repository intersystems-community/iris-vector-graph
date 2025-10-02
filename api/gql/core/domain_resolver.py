"""
Domain Resolver Plugin System

Enables domain-specific type resolution on top of the generic Node interface.
Domains register resolvers that can convert generic node data into
domain-specific types (Protein, Gene, etc.).

Architecture:
- DomainResolver base class defines interface
- Each domain implements resolve_node() to create domain types
- CoreQuery.node() delegates to registered domain resolvers
"""

from typing import Optional, Dict, Any, List
from strawberry.types import Info
import strawberry


class DomainResolver:
    """
    Base class for domain-specific node resolvers.

    Domains inherit from this class and implement resolve_node()
    to provide typed access to their entities.
    """

    def __init__(self, db_connection: Any):
        """
        Initialize domain resolver with database connection.

        Args:
            db_connection: IRIS database connection
        """
        self.db = db_connection

    async def resolve_node(
        self,
        info: Info,
        node_id: str,
        labels: List[str],
        properties: Dict[str, Any],
        created_at: Any,
    ) -> Optional[Any]:
        """
        Resolve generic node data to domain-specific type.

        Args:
            info: GraphQL Info context
            node_id: Node ID
            labels: Node labels
            properties: Node properties
            created_at: Node creation timestamp

        Returns:
            Domain-specific type instance (Protein, Gene, etc.)
            or None if this resolver doesn't handle these labels
        """
        raise NotImplementedError("Domain resolvers must implement resolve_node()")

    def get_query_fields(self) -> Dict[str, Any]:
        """
        Return domain-specific query fields to add to Query type.

        Example:
            {
                "protein": protein_resolver_function,
                "gene": gene_resolver_function
            }

        Returns:
            Dictionary of field_name -> resolver function
        """
        return {}

    def get_mutation_fields(self) -> Dict[str, Any]:
        """
        Return domain-specific mutation fields to add to Mutation type.

        Example:
            {
                "createProtein": create_protein_mutation,
                "updateProtein": update_protein_mutation
            }

        Returns:
            Dictionary of field_name -> mutation function
        """
        return {}


class CompositeDomainResolver(DomainResolver):
    """
    Composite domain resolver that delegates to multiple domain resolvers.

    Allows registering multiple domains (biomedical, social, etc.)
    and tries each one until a match is found.
    """

    def __init__(self, db_connection: Any):
        super().__init__(db_connection)
        self.resolvers: List[DomainResolver] = []

    def register_domain(self, resolver: DomainResolver):
        """
        Register a domain resolver.

        Args:
            resolver: DomainResolver instance to add
        """
        self.resolvers.append(resolver)

    async def resolve_node(
        self,
        info: Info,
        node_id: str,
        labels: List[str],
        properties: Dict[str, Any],
        created_at: Any,
    ) -> Optional[Any]:
        """
        Try each registered domain resolver until one succeeds.

        Args:
            info: GraphQL Info context
            node_id: Node ID
            labels: Node labels
            properties: Node properties
            created_at: Node creation timestamp

        Returns:
            Domain-specific type instance or None
        """
        for resolver in self.resolvers:
            result = await resolver.resolve_node(
                info, node_id, labels, properties, created_at
            )
            if result is not None:
                return result

        return None

    def get_query_fields(self) -> Dict[str, Any]:
        """
        Combine query fields from all registered domains.

        Returns:
            Merged dictionary of all domain query fields
        """
        fields = {}
        for resolver in self.resolvers:
            fields.update(resolver.get_query_fields())
        return fields

    def get_mutation_fields(self) -> Dict[str, Any]:
        """
        Combine mutation fields from all registered domains.

        Returns:
            Merged dictionary of all domain mutation fields
        """
        fields = {}
        for resolver in self.resolvers:
            fields.update(resolver.get_mutation_fields())
        return fields
