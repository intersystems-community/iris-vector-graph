"""
Generic Core GraphQL Query Resolvers

Provides generic graph query operations that work with any domain.
Domain-specific resolvers (protein, gene, etc.) are in domain plugins.
"""

import strawberry
from typing import Optional, List
from strawberry.types import Info

from iris_vector_graph.constants import DEFAULT_GRAPH

from .types import Node, GenericNode, Edge, GraphStats, PropertyFilter, EdgeDirection


async def _load_node(
    info: Info, node_id: str, graph: Optional[str] = None
) -> Optional[Node]:
    """Load one node, resolving it to a domain type when a resolver claims its labels.

    Shared by `CoreQuery.node` and by `CoreQuery.nodes`. The latter used to call
    `await self.node(...)`, but `self` is the Strawberry root value, which is None on a
    query root, so every `nodes` call that matched a row raised
    `AttributeError: 'NoneType' object has no attribute 'node'`.

    `graph` is threaded through to `engine.get_node`, which reads one graph: since spec
    227 the same node ID can exist in several, and an unscoped read merged their labels
    and properties.
    """
    engine = info.context.get("engine")
    if not engine:
        return None

    node_data = engine.get_node(str(node_id), graph=DEFAULT_GRAPH if graph is None else graph)
    if not node_data:
        return None

    labels = node_data.get("labels", [])
    properties = node_data.get("properties", {})
    created_at = node_data.get("created_at")

    domain_resolver = info.context.get("domain_resolver")
    if domain_resolver:
        domain_node = await domain_resolver.resolve_node(
            info, str(node_id), labels, properties, created_at
        )
        if domain_node:
            return domain_node

    # Unknown label - return generic node
    return GenericNode(
        id=strawberry.ID(str(node_id)),
        labels=labels,
        properties=properties,
        created_at=created_at,
    )


@strawberry.type
class CoreQuery:
    """
    Generic graph query operations.

    These resolvers work with any domain - they query the underlying
    NodePK schema (nodes, rdf_labels, rdf_props, rdf_edges) directly.
    """

    @strawberry.field
    async def node(
        self, info: Info, id: strawberry.ID, graph: Optional[str] = None
    ) -> Optional[Node]:
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
            graph: Named graph to read from; omitted means the default graph.

        Returns:
            Node object if found, None otherwise
        """
        return await _load_node(info, str(id), graph=graph)

    @strawberry.field
    async def nodes(
        self,
        info: Info,
        labels: Optional[List[str]] = None,
        where: Optional[PropertyFilter] = None,
        limit: int = 100,
        offset: int = 0,
        order_by: Optional[str] = None,
        order_direction: Optional[str] = "DESC",
        graph: Optional[str] = None,
    ) -> List[Node]:
        """
        Query multiple nodes by label and/or property filter.

        Example:
            query {
              nodes(
                labels: ["Drug"],
                where: {key: "status", value: "approved", operator: "equals"},
                orderBy: "name",
                orderDirection: "ASC",
                limit: 20,
                offset: 0
              ) {
                property(key: "name")
              }
            }

        Args:
            labels: Filter by node labels (e.g., ["Protein", "Gene"])
            where: Filter by property key/value with optional operator
                   (equals, contains, starts_with, ends_with, gt, lt, gte, lte)
            limit: Maximum number of results
            offset: Offset for pagination
            order_by: Property key to sort by, or "id" / "created_at" (default)
            order_direction: "ASC" or "DESC" (default DESC)
            graph: Named graph to read from; omitted means the default graph. Every
                statement is scoped to it, and every join carries `graph_id` — since
                spec 227 one node ID can live in several graphs, and an unscoped read
                returned other graphs' nodes and joined their label rows to this
                graph's nodes.

        Returns:
            List of Node objects
        """
        engine = info.context.get("engine")
        if not engine:
            return []

        graph_id = DEFAULT_GRAPH if graph is None else graph
        db_connection = engine.conn

        # Validate and sanitize sort direction
        direction = "DESC" if order_direction not in ("ASC", "DESC") else order_direction

        # Build ORDER BY clause and any extra JOIN needed for property ordering
        order_join = ""
        order_params_prefix: list = []
        if order_by is None or order_by == "created_at":
            order_clause = f"ORDER BY n.created_at {direction}"
        elif order_by == "id":
            order_clause = f"ORDER BY n.node_id {direction}"
        else:
            # Order by a property value — LEFT JOIN so nodes without the property still
            # appear. The schema prefix is not optional: an unqualified `rdf_props`
            # resolves against the connection's default schema (SQLUser), so
            # `orderBy: "<property>"` failed at Query Open rather than ordering.
            order_join = (
                "LEFT JOIN Graph_KG.rdf_props order_p ON order_p.s = n.node_id "
                "AND order_p.graph_id = n.graph_id AND order_p.key = ?"
            )
            order_params_prefix = [order_by]
            order_clause = f"ORDER BY order_p.val {direction}"

        # Build WHERE condition for property filter (operator-aware)
        def _where_condition(val_placeholder: str = "?") -> str:
            op = (where.operator or "equals").lower() if where else "equals"
            if op == "contains":
                return f"p.val LIKE '%' || {val_placeholder} || '%'"
            if op == "starts_with":
                return f"p.val LIKE {val_placeholder} || '%'"
            if op == "ends_with":
                return f"p.val LIKE '%' || {val_placeholder}"
            if op == "gt":
                return f"CAST(p.val AS DOUBLE) > CAST({val_placeholder} AS DOUBLE)"
            if op == "lt":
                return f"CAST(p.val AS DOUBLE) < CAST({val_placeholder} AS DOUBLE)"
            if op == "gte":
                return f"CAST(p.val AS DOUBLE) >= CAST({val_placeholder} AS DOUBLE)"
            if op == "lte":
                return f"CAST(p.val AS DOUBLE) <= CAST({val_placeholder} AS DOUBLE)"
            return f"p.val = {val_placeholder}"  # default: equals

        cursor = db_connection.cursor()

        # Build query based on filters
        # Parameters are ordered by where their `?` appears in the SQL text, which is why
        # the property-order join's parameter sits between the JOIN parameters and the
        # WHERE ones rather than always leading: with both `where` and `orderBy` set, the
        # old unconditional `order_params_prefix + [where.key]` bound the two the wrong
        # way round, filtering on the sort key and sorting on the filter key.
        if labels and where:
            query = """
                SELECT DISTINCT n.node_id
                FROM Graph_KG.nodes n
                JOIN Graph_KG.rdf_labels l ON l.s = n.node_id AND l.graph_id = n.graph_id
                JOIN Graph_KG.rdf_props p ON p.s = n.node_id AND p.graph_id = n.graph_id AND p.key = ?
                {order_join}
                WHERE n.graph_id = ?
                  AND l.label IN ({placeholders})
                  AND {where_cond}
                {order_clause}
                LIMIT ? OFFSET ?
            """.format(
                order_join=order_join,
                placeholders=",".join(["?" for _ in labels]),
                where_cond=_where_condition(),
                order_clause=order_clause,
            )
            params = (
                [where.key]
                + order_params_prefix
                + [graph_id]
                + labels
                + [where.value, limit, offset]
            )
        elif labels:
            query = """
                SELECT DISTINCT n.node_id
                FROM Graph_KG.nodes n
                JOIN Graph_KG.rdf_labels l ON l.s = n.node_id AND l.graph_id = n.graph_id
                {order_join}
                WHERE n.graph_id = ?
                  AND l.label IN ({placeholders})
                {order_clause}
                LIMIT ? OFFSET ?
            """.format(
                order_join=order_join,
                placeholders=",".join(["?" for _ in labels]),
                order_clause=order_clause,
            )
            params = order_params_prefix + [graph_id] + labels + [limit, offset]
        elif where:
            query = """
                SELECT DISTINCT n.node_id
                FROM Graph_KG.nodes n
                JOIN Graph_KG.rdf_props p ON p.s = n.node_id AND p.graph_id = n.graph_id AND p.key = ?
                {order_join}
                WHERE n.graph_id = ?
                  AND {where_cond}
                {order_clause}
                LIMIT ? OFFSET ?
            """.format(
                order_join=order_join,
                where_cond=_where_condition(),
                order_clause=order_clause,
            )
            params = (
                [where.key] + order_params_prefix + [graph_id, where.value, limit, offset]
            )
        else:
            query = """
                SELECT n.node_id
                FROM Graph_KG.nodes n
                {order_join}
                WHERE n.graph_id = ?
                {order_clause}
                LIMIT ? OFFSET ?
            """.format(order_join=order_join, order_clause=order_clause)
            params = order_params_prefix + [graph_id, limit, offset]

        cursor.execute(query, params)
        node_ids = [row[0] for row in cursor.fetchall()]

        if not node_ids:
            return []

        # There used to be a batch branch here keyed on `info.context["node_loader"]`, with
        # this loop as its fallback. No `NodeLoader` class exists in `api/gql/loaders.py`
        # and no app factory ever put that key in the context, so the branch never ran; it
        # also read no graph, which would have reintroduced the cross-graph merge below.
        nodes = []
        for node_id in node_ids:
            node = await _load_node(info, node_id, graph=graph_id)
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
        engine = info.context.get("engine")
        if not engine:
            return GraphStats(
                total_nodes=0,
                total_edges=0,
                nodes_by_label={},
                edges_by_type={},
            )

        try:
            result_nodes = engine.execute_cypher("MATCH (n) RETURN count(n) AS c")
            total_nodes = result_nodes.rows[0][0] if result_nodes.rows else 0

            result_edges = engine.execute_cypher("MATCH ()-[r]->() RETURN count(r) AS c")
            total_edges = result_edges.rows[0][0] if result_edges.rows else 0

            result_labels = engine.execute_cypher("MATCH (n) RETURN distinct labels(n) AS label, count(n) AS cnt")
            nodes_by_label = {}
            if result_labels.rows:
                for row in result_labels.rows:
                    label = row[0]
                    count = row[1]
                    if label and isinstance(label, list):
                        for l in label:
                            nodes_by_label[l] = nodes_by_label.get(l, 0) + count

            result_types = engine.execute_cypher("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS cnt")
            edges_by_type = {}
            if result_types.rows:
                for row in result_types.rows:
                    edges_by_type[row[0]] = row[1]

            return GraphStats(
                total_nodes=total_nodes,
                total_edges=total_edges,
                nodes_by_label=nodes_by_label,
                edges_by_type=edges_by_type,
            )
        except Exception:
            return GraphStats(
                total_nodes=0,
                total_edges=0,
                nodes_by_label={},
                edges_by_type={},
            )
