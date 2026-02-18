"""
GraphQL Schema Composition

Composes the GraphQL schema by combining:
1. Generic core queries (node, nodes, stats) from CoreQuery
2. Biomedical domain queries (protein, gene, pathway) from biomedical domain

This demonstrates the hybrid architecture: generic core + domain extension.
"""

import strawberry
from typing import Optional, List, Dict, Any, cast
from strawberry.extensions import SchemaExtension

from .core.types import JSON, Node, GraphStats, PropertyFilter
from .core.resolvers import CoreQuery
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


class DatabaseConnectionExtension(SchemaExtension):
    """
    Strawberry extension to manage database connection lifecycle.
    
    Ensures connections are properly closed after each GraphQL request,
    which is critical for IRIS Community Edition's 5-connection limit.
    
    When context contains 'owns_connection': True, the extension will
    close the db_connection after the request. When False or absent,
    the connection is assumed to be externally managed (e.g., in tests).
    """
    
    def on_request_end(self):
        """Close database connection when request ends if we own it."""
        context = self.execution_context.context
        # Only close if we created/own the connection (not externally provided)
        if context and context.get("owns_connection", False):
            if "db_connection" in context:
                try:
                    context["db_connection"].close()
                except Exception:
                    pass  # Connection may already be closed

# Import biomedical domain types and resolvers
# NOTE: Biomedical is an EXAMPLE domain - you can create your own domains
# following the same pattern
try:
    from examples.domains.biomedical.types import (
        Protein,
        Gene,
        Pathway,
        CreateProteinInput,
        UpdateProteinInput,
    )
    from examples.domains.biomedical.resolver import BiomedicalDomainResolver

    BIOMEDICAL_AVAILABLE = True
except ImportError:
    BIOMEDICAL_AVAILABLE = False
    Protein = None
    Gene = None
    Pathway = None
    CreateProteinInput = None
    UpdateProteinInput = None
    BiomedicalDomainResolver = None


@strawberry.type
class Query(CoreQuery):
    """
    Combined Query type with core + biomedical domain queries.

    Inherits generic queries from CoreQuery (node, nodes, stats)
    and adds biomedical-specific queries (protein, gene, pathway).

    To add a new domain, create similar query methods and import
    the domain types.
    """

    # Biomedical domain queries (if available)
    if BIOMEDICAL_AVAILABLE:

        @strawberry.field
        async def protein(
            info: strawberry.Info, id: strawberry.ID
        ) -> Optional[Protein]:
            """
            Query a protein by ID.

            Biomedical domain-specific query. Convenience wrapper around
            Query.node() that returns Protein type directly.
            """
            # Use BiomedicalDomainResolver to get protein
            biomed_resolver = BiomedicalDomainResolver(
                info.context.get("db_connection")
            )
            return await biomed_resolver._protein_query(info, id)

        @strawberry.field
        async def gene(
            info: strawberry.Info, id: strawberry.ID
        ) -> Optional[Gene]:
            """Query a gene by ID."""
            biomed_resolver = BiomedicalDomainResolver(
                info.context.get("db_connection")
            )
            return await biomed_resolver._gene_query(info, id)

        @strawberry.field
        async def pathway(
            info: strawberry.Info, id: strawberry.ID
        ) -> Optional[Pathway]:
            """Query a pathway by ID."""
            biomed_resolver = BiomedicalDomainResolver(
                info.context.get("db_connection")
            )
            return await biomed_resolver._pathway_query(info, id)

    @strawberry.field(description="Execute a Cypher query and return JSON results.")
    async def execute_cypher(
        self,
        info: strawberry.Info,
        query: str,
        params: Optional[JSON] = None,
    ) -> JSON:
        """Run openCypher, translate to SQL, and return rows as JSON objects."""
        db_connection = info.context.get("db_connection")
        if not db_connection:
            return cast(JSON, {"error": "database connection unavailable"})

        cursor = db_connection.cursor()
        try:
            cypher_ast = parse_query(query)
            translator_params: Optional[Dict[str, Any]] = None
            if isinstance(params, dict):
                translator_params = cast(Dict[str, Any], params)
            sql_query = translate_to_sql(cypher_ast, params=translator_params)

            rows: List[List[Any]] = []
            columns: List[str] = []

            if sql_query.is_transactional:
                cursor.execute("START TRANSACTION")
                try:
                    statements = (
                        sql_query.sql
                        if isinstance(sql_query.sql, list)
                        else [sql_query.sql]
                    )
                    all_params = sql_query.parameters or []

                    for idx, statement in enumerate(statements):
                        params_for_stmt = (
                            all_params[idx] if idx < len(all_params) else []
                        )
                        cursor.execute(statement, params_for_stmt)
                        if idx == len(statements) - 1 and cursor.description:
                            columns = [desc[0] for desc in cursor.description]
                            rows = cursor.fetchall()
                    cursor.execute("COMMIT")
                except Exception:
                    cursor.execute("ROLLBACK")
                    raise
            else:
                sql_text = (
                    sql_query.sql
                    if isinstance(sql_query.sql, str)
                    else "\n".join(sql_query.sql)
                )
                exec_params = sql_query.parameters[0] if sql_query.parameters else []
                cursor.execute(sql_text, exec_params)
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()

            results: List[Dict[str, Any]] = []
            if columns:
                results = [dict(zip(columns, row)) for row in rows]

            return cast(JSON, results)
        except Exception as exc:
            return cast(JSON, {"error": str(exc)})
        finally:
            try:
                cursor.close()
            except Exception:
                pass


@strawberry.type
class Mutation:
    """
    Mutation type with biomedical domain mutations.

    Biomedical mutations: createProtein, updateProtein, deleteProtein
    """

    if BIOMEDICAL_AVAILABLE:

        @strawberry.mutation
        async def create_protein(
            info: strawberry.Info, input: CreateProteinInput
        ) -> Protein:
            """Create a new protein."""
            biomed_resolver = BiomedicalDomainResolver(
                info.context.get("db_connection")
            )
            return await biomed_resolver._create_protein_mutation(info, input)

        @strawberry.mutation
        async def update_protein(
            info: strawberry.Info, id: strawberry.ID, input: UpdateProteinInput
        ) -> Protein:
            """Update an existing protein."""
            biomed_resolver = BiomedicalDomainResolver(
                info.context.get("db_connection")
            )
            return await biomed_resolver._update_protein_mutation(info, id, input)

        @strawberry.mutation
        async def delete_protein(
            info: strawberry.Info, id: strawberry.ID
        ) -> bool:
            """Delete a protein."""
            biomed_resolver = BiomedicalDomainResolver(
                info.context.get("db_connection")
            )
            return await biomed_resolver._delete_protein_mutation(info, id)


# Create schema with connection management extension
schema = strawberry.Schema(
    query=Query,
    mutation=Mutation if BIOMEDICAL_AVAILABLE else None,
    extensions=[DatabaseConnectionExtension],
)
