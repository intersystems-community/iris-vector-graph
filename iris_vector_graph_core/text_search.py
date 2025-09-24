#!/usr/bin/env python3
"""
IRIS Text Search Engine - iFind Integration

Provides native IRIS text search capabilities using %FIND functions
with stemming, stopwords, and relevance ranking.
"""

import logging
from typing import List, Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)


class TextSearchEngine:
    """
    IRIS text search engine using native %FIND capabilities
    """

    def __init__(self, connection):
        """Initialize with IRIS database connection"""
        self.conn = connection

    def search_documents(self, query_text: str, k: int = 50, table_name: str = "docs") -> List[Tuple[str, float]]:
        """
        Search documents using IRIS %FIND functionality

        Args:
            query_text: Text query string
            k: Number of results to return
            table_name: Name of documents table

        Returns:
            List of (doc_id, relevance_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Use IRIS %FIND for full-text search with ranking
            sql = f"""
                SELECT TOP {k}
                    id,
                    %FIND.RANK() as relevance_score
                FROM {table_name}
                WHERE %FIND(text, ?)
                ORDER BY relevance_score DESC
            """

            cursor.execute(sql, [query_text])
            results = cursor.fetchall()
            return [(doc_id, float(score)) for doc_id, score in results]

        except Exception as e:
            logger.error(f"Document search failed: {e}")
            # Fallback to LIKE search if %FIND not available
            return self._fallback_text_search(query_text, k, table_name)
        finally:
            cursor.close()

    def _fallback_text_search(self, query_text: str, k: int, table_name: str) -> List[Tuple[str, float]]:
        """
        Fallback text search using LIKE patterns
        """
        cursor = self.conn.cursor()
        try:
            like_pattern = f'%{query_text}%'
            sql = f"""
                SELECT TOP {k}
                    id,
                    1.0 as relevance_score
                FROM {table_name}
                WHERE text LIKE ?
                ORDER BY id
            """

            cursor.execute(sql, [like_pattern])
            results = cursor.fetchall()
            return [(doc_id, float(score)) for doc_id, score in results]

        except Exception as e:
            logger.error(f"Fallback text search failed: {e}")
            return []
        finally:
            cursor.close()

    def search_entity_qualifiers(self, query_text: str, k: int = 50, min_confidence: int = 0) -> List[Dict[str, Any]]:
        """
        Search entity qualifiers using JSON_TABLE extraction

        Args:
            query_text: Text to search for
            k: Number of results
            min_confidence: Minimum confidence threshold

        Returns:
            List of entity matches with metadata
        """
        cursor = self.conn.cursor()
        try:
            sql = f"""
                SELECT TOP {k}
                    e.s as entity_id,
                    e.p as predicate,
                    e.o_id as object_id,
                    jt.confidence,
                    CASE WHEN e.qualifiers LIKE ? THEN 1.0 ELSE 0.5 END as text_match_score
                FROM rdf_edges e,
                     JSON_TABLE(
                        e.qualifiers, '$'
                        COLUMNS(confidence INTEGER PATH '$.confidence')
                     ) jt
                WHERE (e.qualifiers LIKE ? OR e.o_id LIKE ?)
                  AND (jt.confidence >= ? OR jt.confidence IS NULL)
                ORDER BY text_match_score DESC, jt.confidence DESC
            """

            like_pattern = f'%{query_text}%'
            params = [like_pattern, like_pattern, like_pattern, min_confidence]
            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'entity_id': row[0],
                    'predicate': row[1],
                    'object_id': row[2],
                    'confidence': row[3] if row[3] is not None else 0,
                    'text_match_score': row[4]
                })

            return results

        except Exception as e:
            logger.error(f"Entity qualifier search failed: {e}")
            raise
        finally:
            cursor.close()

    def extract_entity_names(self, query_text: str, k: int = 50) -> List[Tuple[str, str, float]]:
        """
        Extract entity names matching query text

        Args:
            query_text: Text to match against entity names
            k: Number of results

        Returns:
            List of (entity_id, entity_name, match_score) tuples
        """
        cursor = self.conn.cursor()
        try:
            # Search in properties for name-like fields
            sql = f"""
                SELECT TOP {k}
                    p.s as entity_id,
                    p.val as entity_name,
                    CASE
                        WHEN UPPER(p.val) = UPPER(?) THEN 1.0
                        WHEN UPPER(p.val) LIKE UPPER(?) THEN 0.8
                        ELSE 0.5
                    END as match_score
                FROM rdf_props p
                WHERE p.key IN ('name', 'title', 'label', 'description')
                  AND (UPPER(p.val) LIKE UPPER(?) OR UPPER(p.val) LIKE UPPER(?))
                ORDER BY match_score DESC, LENGTH(p.val) ASC
            """

            exact_match = query_text
            like_pattern = f'%{query_text}%'
            starts_pattern = f'{query_text}%'

            params = [exact_match, starts_pattern, like_pattern, starts_pattern]
            cursor.execute(sql, params)

            results = cursor.fetchall()
            return [(entity_id, entity_name, float(score)) for entity_id, entity_name, score in results]

        except Exception as e:
            logger.error(f"Entity name extraction failed: {e}")
            raise
        finally:
            cursor.close()

    def search_with_context(self, query_text: str, entity_types: Optional[List[str]] = None, k: int = 50) -> List[Dict[str, Any]]:
        """
        Context-aware text search with entity type filtering

        Args:
            query_text: Text query
            entity_types: Optional list of entity types to filter by
            k: Number of results

        Returns:
            List of search results with context
        """
        cursor = self.conn.cursor()
        try:
            # Build query with optional entity type filtering
            base_sql = f"""
                SELECT TOP {k}
                    p.s as entity_id,
                    p.val as matched_text,
                    p.key as property_type,
                    l.label as entity_type,
                    CASE
                        WHEN UPPER(p.val) LIKE UPPER(?) THEN 1.0
                        WHEN UPPER(p.val) LIKE UPPER(?) THEN 0.7
                        ELSE 0.4
                    END as relevance_score
                FROM rdf_props p
                LEFT JOIN rdf_labels l ON l.s = p.s
                WHERE UPPER(p.val) LIKE UPPER(?)
            """

            params = [f'{query_text}%', f'%{query_text}%', f'%{query_text}%']

            # Add entity type filtering if specified
            if entity_types:
                type_placeholders = ','.join(['?' for _ in entity_types])
                base_sql += f" AND l.label IN ({type_placeholders})"
                params.extend(entity_types)

            base_sql += " ORDER BY relevance_score DESC, LENGTH(p.val) ASC"

            cursor.execute(base_sql, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    'entity_id': row[0],
                    'matched_text': row[1],
                    'property_type': row[2],
                    'entity_type': row[3] if row[3] else 'unknown',
                    'relevance_score': float(row[4])
                })

            return results

        except Exception as e:
            logger.error(f"Context-aware search failed: {e}")
            raise
        finally:
            cursor.close()