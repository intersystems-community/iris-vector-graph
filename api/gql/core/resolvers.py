"""
Generic Core GraphQL Query Resolvers

Provides generic graph query operations that work with any domain.
Domain-specific resolvers (protein, gene, etc.) are in domain plugins.
"""

import strawberry
from typing import Optional, List
from strawberry.types import Info

from .types import Node, GenericNode, Edge, GraphStats, PropertyFilter, EdgeDirection


@strawberry.type
class CoreQuery:
    """
    Generic graph query operations.

    These resolvers work with any domain - they query the underlying
    NodePK schema (nodes, rdf_labels, rdf_props, rdf_edges) directly.
    """

    @strawberry.field
    async def node(self, info: Info, id: strawberry.ID) -> Optional[Node]:
        """
        Query any node by ID, regardless of label/type.

        Returns a Node interface which can be:
        - GenericNode for unknown labels
        - Domain-specific type (Protein, Gene, etc.) if label matches

        Example:
            query {
              node(id: "PROTEIN:TP53") {
                __typename
                id
                labels
                property(key: "name")

                ... on Protein {
                  name
                  function
                }
              }
            }

        Args:
            id: Node ID (any domain)

        Returns:
            Node object if found, None otherwise
        """
        db_connection = info.context.get("db_connection")
        if not db_connection:
            return None

        cursor = db_connection.cursor()

        # Query nodes table directly
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(id),))
        if cursor.fetchone()[0] == 0:
            return None

        # Load labels
        cursor.execute("SELECT label FROM rdf_labels WHERE s = ?", (str(id),))
        labels = [row[0] for row in cursor.fetchall()]

        # Load properties
        cursor.execute("SELECT key, val FROM rdf_props WHERE s = ?", (str(id),))
        properties = {row[0]: row[1] for row in cursor.fetchall()}

        # Load created_at
        cursor.execute("SELECT created_at FROM nodes WHERE node_id = ?", (str(id),))
        created_at = cursor.fetchone()[0]

        # Try to resolve to domain-specific type using domain resolvers
        domain_resolver = info.context.get("domain_resolver")
        if domain_resolver:
            domain_node = await domain_resolver.resolve_node(
                info, str(id), labels, properties, created_at
            )
            if domain_node:
                return domain_node

        # Unknown label - return generic node
        return GenericNode(
            id=strawberry.ID(str(id)),
            labels=labels,
            properties=properties,
            created_at=created_at,
        )

    @strawberry.field
    async def nodes(
        self,
        info: Info,
        labels: Optional[List[str]] = None,
        where: Optional[PropertyFilter] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Node]:
        """
        Query multiple nodes by label and/or property filter.

        Example:
            query {
              nodes(labels: ["Protein"], where: {key: "organism", value: "Homo sapiens"}) {
                property(key: "name")
              }
            }

        Args:
            labels: Filter by node labels (e.g., ["Protein", "Gene"])
            where: Filter by property key/value
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            List of Node objects
        """
        db_connection = info.context.get("db_connection")
        if not db_connection:
            return []

        cursor = db_connection.cursor()

        # Build query based on filters
        if labels and where:
            # Filter by both labels and properties
            query = """
                SELECT DISTINCT n.node_id
                FROM nodes n
                JOIN rdf_labels l ON l.s = n.node_id
                JOIN rdf_props p ON p.s = n.node_id
                WHERE l.label IN ({})
                  AND p.key = ?
                  AND p.val = ?
                ORDER BY n.created_at DESC
                LIMIT ? OFFSET ?
            """.format(",".join(["?" for _ in labels]))
            params = labels + [where.key, where.value, limit, offset]
        elif labels:
            # Filter by labels only
            query = """
                SELECT DISTINCT n.node_id
                FROM nodes n
                JOIN rdf_labels l ON l.s = n.node_id
                WHERE l.label IN ({})
                ORDER BY n.created_at DESC
                LIMIT ? OFFSET ?
            """.format(",".join(["?" for _ in labels]))
            params = labels + [limit, offset]
        elif where:
            # Filter by properties only
            query = """
                SELECT DISTINCT n.node_id
                FROM nodes n
                JOIN rdf_props p ON p.s = n.node_id
                WHERE p.key = ?
                  AND p.val = ?
                ORDER BY n.created_at DESC
                LIMIT ? OFFSET ?
            """
            params = [where.key, where.value, limit, offset]
        else:
            # No filters - return all nodes
            query = """
                SELECT node_id
                FROM nodes
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params = [limit, offset]

        cursor.execute(query, params)
        node_ids = [row[0] for row in cursor.fetchall()]

        # Load each node using node() resolver
        nodes = []
        for node_id in node_ids:
            node = await self.node(info, strawberry.ID(node_id))
            if node:
                nodes.append(node)

        return nodes

    @strawberry.field
    async def stats(self, info: Info) -> GraphStats:
        """
        Get graph statistics.

        Returns counts and aggregates for nodes and edges.

        Example:
            query {
              stats {
                totalNodes
                totalEdges
                nodesByLabel
                edgesByType
              }
            }
        """
        db_connection = info.context.get("db_connection")
        if not db_connection:
            return GraphStats(
                total_nodes=0,
                total_edges=0,
                nodes_by_label={},
                edges_by_type={},
            )

        cursor = db_connection.cursor()

        # Total nodes
        cursor.execute("SELECT COUNT(*) FROM nodes")
        total_nodes = cursor.fetchone()[0]

        # Total edges
        cursor.execute("SELECT COUNT(*) FROM rdf_edges")
        total_edges = cursor.fetchone()[0]

        # Nodes by label
        cursor.execute("SELECT label, COUNT(*) FROM rdf_labels GROUP BY label")
        nodes_by_label = {row[0]: row[1] for row in cursor.fetchall()}

        # Edges by type
        cursor.execute("SELECT p, COUNT(*) FROM rdf_edges GROUP BY p")
        edges_by_type = {row[0]: row[1] for row in cursor.fetchall()}

        return GraphStats(
            total_nodes=total_nodes,
            total_edges=total_edges,
            nodes_by_label=nodes_by_label,
            edges_by_type=edges_by_type,
        )
