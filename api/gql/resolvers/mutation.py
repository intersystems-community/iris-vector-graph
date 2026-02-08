"""
GraphQL Mutation resolvers for IRIS Vector Graph API.

Implements create, update, delete operations with FK validation.
"""

import strawberry
from typing import Optional
from datetime import datetime
import json as json_module

from api.gql.types import (
    Protein,
    CreateProteinInput,
    UpdateProteinInput,
)


@strawberry.type
class Mutation:
    """GraphQL mutation root type"""

    @strawberry.mutation
    async def create_protein(
        self,
        info: strawberry.Info,
        input: CreateProteinInput
    ) -> Protein:
        """
        Create a new protein with optional embedding vector.

        Creates:
        - nodes.node_id entry
        - rdf_labels entry with "Protein" label
        - rdf_props entries for name, function, organism
        - kg_NodeEmbeddings entry if embedding provided

        Raises GraphQL error if protein ID already exists.
        """
        db_connection = info.context.get("db_connection")
        if not db_connection:
            raise Exception("Database connection not available")

        cursor = db_connection.cursor()

        # Check if protein already exists
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(input.id),))
        if cursor.fetchone()[0] > 0:
            raise Exception(f"Protein with ID {input.id} already exists")

        try:
            # All operations in a single transaction
            # Phase 1: Core Node data
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", (str(input.id),))
            cursor.execute("INSERT INTO rdf_labels (s, label) VALUES (?, ?)", (str(input.id), "Protein"))
            
            # Phase 2: Properties
            prop_data = [
                (str(input.id), "name", input.name)
            ]
            if input.function:
                prop_data.append((str(input.id), "function", input.function))
            if input.organism:
                prop_data.append((str(input.id), "organism", input.organism))
            
            cursor.executemany("INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)", prop_data)

            # Phase 3: Embedding if provided
            if input.embedding and len(input.embedding) > 0:
                if len(input.embedding) != 768:
                    raise Exception(f"Embedding must be 768-dimensional, got {len(input.embedding)}")
                emb_str = "[" + ",".join([str(x) for x in input.embedding]) + "]"
                cursor.execute(
                    "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                    (str(input.id), emb_str)
                )

            db_connection.commit()

            # Load created protein using ProteinLoader
            protein_loader = info.context["protein_loader"]
            protein_data = await protein_loader.load(str(input.id))

            if not protein_data:
                raise Exception(f"Failed to load created protein {input.id}")

            # Return Protein object
            return Protein(
                id=strawberry.ID(protein_data["id"]),
                labels=protein_data.get("labels", []),
                properties=protein_data.get("properties", {}),
                created_at=protein_data.get("created_at"),
                name=protein_data.get("name", ""),
                function=protein_data.get("function"),
                organism=protein_data.get("organism"),
                confidence=protein_data.get("confidence"),
            )

        except Exception as e:
            db_connection.rollback()
            raise Exception(f"Failed to create protein: {str(e)}")

    @strawberry.mutation
    async def update_protein(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
        input: UpdateProteinInput
    ) -> Protein:
        """
        Update an existing protein's fields.

        Only updates fields provided in input (partial update).
        Raises GraphQL error if protein not found.
        """
        db_connection = info.context.get("db_connection")
        if not db_connection:
            raise Exception("Database connection not available")

        cursor = db_connection.cursor()

        # Check if protein exists
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(id),))
        if cursor.fetchone()[0] == 0:
            raise Exception(f"Protein with ID {id} not found")

        try:
            # Batch all property updates in one transaction
            updates = []
            if input.name is not None:
                updates.append(("name", input.name))
            if input.function is not None:
                updates.append(("function", input.function))
            if input.confidence is not None:
                updates.append(("confidence", str(input.confidence)))

            for key, val in updates:
                # Use UPSERT pattern: Try update, if 0 rows, then insert
                cursor.execute(
                    "UPDATE rdf_props SET val = ? WHERE s = ? AND key = ?",
                    (val, str(id), key)
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                        (str(id), key, val)
                    )

            db_connection.commit()

            # Load updated protein
            protein_loader = info.context["protein_loader"]
            # Clear cache for this protein (if cached)
            try:
                protein_loader.clear(str(id))
            except KeyError:
                pass  # Not in cache, no need to clear
            protein_data = await protein_loader.load(str(id))

            if not protein_data:
                raise Exception(f"Failed to load updated protein {id}")

            return Protein(
                id=strawberry.ID(protein_data["id"]),
                labels=protein_data.get("labels", []),
                properties=protein_data.get("properties", {}),
                created_at=protein_data.get("created_at"),
                name=protein_data.get("name", ""),
                function=protein_data.get("function"),
                organism=protein_data.get("organism"),
                confidence=protein_data.get("confidence"),
            )

        except Exception as e:
            db_connection.rollback()
            raise Exception(f"Failed to update protein: {str(e)}")

    @strawberry.mutation
    async def delete_protein(
        self,
        info: strawberry.Info,
        id: strawberry.ID
    ) -> bool:
        """
        Delete a protein and all related data.

        Deletes (in order to respect FK constraints):
        1. kg_NodeEmbeddings (FK to nodes)
        2. rdf_edges (source and destination FKs)
        3. rdf_props (FK to nodes)
        4. rdf_labels (FK to nodes)
        5. nodes (primary table)

        Returns True if protein deleted successfully.
        Raises GraphQL error if protein not found.
        """
        db_connection = info.context.get("db_connection")
        if not db_connection:
            raise Exception("Database connection not available")

        cursor = db_connection.cursor()

        # Check if protein exists
        cursor.execute("SELECT COUNT(*) FROM nodes WHERE node_id = ?", (str(id),))
        if cursor.fetchone()[0] == 0:
            raise Exception(f"Protein with ID {id} not found")

        try:
            # Delete in reverse order of FK dependencies

            # 1. Delete embedding (FK to nodes)
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id = ?", (str(id),))

            # 2. Delete edges (both source and destination)
            cursor.execute("DELETE FROM rdf_edges WHERE s = ? OR o_id = ?", (str(id), str(id)))

            # 3. Delete properties
            cursor.execute("DELETE FROM rdf_props WHERE s = ?", (str(id),))

            # 4. Delete labels
            cursor.execute("DELETE FROM rdf_labels WHERE s = ?", (str(id),))

            # 5. Delete node
            cursor.execute("DELETE FROM nodes WHERE node_id = ?", (str(id),))

            db_connection.commit()

            # Clear DataLoader cache (if cached)
            protein_loader = info.context["protein_loader"]
            try:
                protein_loader.clear(str(id))
            except KeyError:
                pass  # Not in cache, no need to clear

            return True

        except Exception as e:
            db_connection.rollback()
            raise Exception(f"Failed to delete protein: {str(e)}")
