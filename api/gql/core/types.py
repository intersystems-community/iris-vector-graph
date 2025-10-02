"""
Generic Core GraphQL Types for IRIS Vector Graph API

This module provides the generic graph schema that works with any domain.
Domain-specific types (Protein, Gene, etc.) extend these core types.

Design: Generic Node interface + Edge type + utility types
"""

import strawberry
from typing import List, Optional
from datetime import datetime
import json as json_module
from enum import Enum


# Custom scalar types
@strawberry.scalar(
    serialize=lambda v: json_module.dumps(v) if isinstance(v, dict) else v,
    parse_value=lambda v: json_module.loads(v) if isinstance(v, str) else v,
)
class JSON:
    """Arbitrary JSON data from rdf_props"""
    pass


DateTime = strawberry.scalar(
    datetime,
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
)


# Node interface - base for all graph entities
@strawberry.interface
class Node:
    """
    Generic node interface for graph entities.

    This interface provides the core functionality for any graph node,
    regardless of domain. Domain-specific types (Protein, Gene, etc.)
    implement this interface and add typed convenience fields.

    All nodes in the database (from any domain) can be queried using
    this interface via Query.node(id) or Query.nodes(labels).
    """
    id: strawberry.ID
    labels: List[str]
    properties: JSON
    created_at: DateTime = strawberry.field(name="createdAt")

    @strawberry.field
    def property(self, key: str) -> Optional[str]:
        """
        Generic property accessor.

        Get any property value by key from the properties JSON.
        This enables querying properties not defined as typed fields.

        Example:
            node { property(key: "custom_annotation") }

        Args:
            key: Property key to retrieve

        Returns:
            Property value as string, or None if not found
        """
        if isinstance(self.properties, dict):
            return self.properties.get(key)
        return None


@strawberry.type
class GenericNode(Node):
    """
    Concrete implementation of Node for unknown/generic entity types.

    Used when querying nodes with labels that don't have a specific
    domain type defined. Provides access to all node data via the
    Node interface methods.

    Example:
        query {
          node(id: "CUSTOM:123") {
            __typename  # "GenericNode"
            labels      # ["CustomEntity"]
            property(key: "name")
          }
        }
    """
    pass


@strawberry.enum
class EdgeDirection(Enum):
    """Direction for edge traversal"""
    OUTGOING = "OUTGOING"
    INCOMING = "INCOMING"
    BOTH = "BOTH"


@strawberry.type
class Edge:
    """
    Generic graph edge/relationship between nodes.

    Represents a directed edge in the graph with optional properties
    stored as qualifiers.
    """
    id: strawberry.ID
    source: Node
    target: Node
    type: str
    qualifiers: Optional[JSON] = None

    @strawberry.field
    def qualifier(self, key: str) -> Optional[str]:
        """
        Get edge qualifier (edge property) by key.

        Args:
            key: Qualifier key to retrieve

        Returns:
            Qualifier value as string, or None if not found
        """
        if isinstance(self.qualifiers, dict):
            return self.qualifiers.get(key)
        return None


@strawberry.type
class SimilarNode:
    """
    Result type for vector similarity search.

    Generic version that works with any node type.
    """
    node: Node
    similarity: float
    distance: Optional[float] = None


@strawberry.type
class GraphStats:
    """Graph statistics aggregates"""
    total_nodes: int = strawberry.field(name="totalNodes")
    total_edges: int = strawberry.field(name="totalEdges")
    nodes_by_label: JSON = strawberry.field(name="nodesByLabel")
    edges_by_type: JSON = strawberry.field(name="edgesByType")


@strawberry.input
class PropertyFilter:
    """Filter for node properties in Query.nodes()"""
    key: str
    value: str
    operator: Optional[str] = "equals"  # equals, contains, starts_with, etc.
