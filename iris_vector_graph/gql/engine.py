from typing import List, Dict, Any, Set, Optional
import logging
from ..engine import IRISGraphEngine

logger = logging.getLogger(__name__)

class GQLGraphEngine:
    """
    Extends IRISGraphEngine with introspection capabilities for GraphQL schema generation.
    """
    def __init__(self, engine: IRISGraphEngine):
        self.engine = engine
        self.conn = engine.conn

    def get_labels(self) -> List[str]:
        """
        Discovers all distinct node labels in the graph.
        """
        cursor = self.conn.cursor()
        try:
            # Query distinct labels from rdf_labels table
            cursor.execute("SELECT DISTINCT label FROM Graph_KG.rdf_labels")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to discover labels: {e}")
            return []
        finally:
            cursor.close()

    def get_sampled_properties(self, label: str, sample_limit: int = 1000) -> Set[str]:
        """
        Samples nodes of a given label to discover unique property keys.
        """
        cursor = self.conn.cursor()
        try:
            # Join rdf_labels and rdf_props to find keys for a specific label
            # Using TOP clause for sampling as per SC-002/SC-003 and FR-002
            sql = f"""
                SELECT DISTINCT p."key"
                FROM Graph_KG.rdf_props p
                JOIN Graph_KG.rdf_labels l ON p.s = l.s
                WHERE l.label = ?
                AND p.s IN (
                    SELECT TOP {sample_limit} s 
                    FROM Graph_KG.rdf_labels 
                    WHERE label = ?
                )
            """
            cursor.execute(sql, [label, label])
            return {row[0] for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Failed to sample properties for label {label}: {e}")
            return set()
        finally:
            cursor.close()

    def get_schema_metadata(self, sample_limit: int = 1000) -> Dict[str, Set[str]]:
        """
        Returns a map of labels to their discovered property sets.
        """
        labels = self.get_labels()
        metadata = {}
        for label in labels:
            properties = self.get_sampled_properties(label, sample_limit)
            metadata[label] = properties
        return metadata
