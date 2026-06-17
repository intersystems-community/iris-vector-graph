"""RDF export mixin for IRISGraphEngine.

Provides:
- export_rdf(): full or filtered graph export to Turtle/NT/NQuads/JSON-LD
- export_rdf_from_cypher(): Cypher-result subgraph as RDF
- register_namespace() / list_namespaces(): persistent prefix registry
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph, _infer_format, _mint_iri, _to_literal

logger = logging.getLogger(__name__)


def _require_rdflib():
    try:
        import rdflib
        return rdflib
    except ImportError as e:
        raise ImportError(
            "rdflib is required for RDF export. "
            "Install with: pip install 'iris-vector-graph[rdf]'"
        ) from e


class RdfExportMixin:
    """RDF export capabilities for IRISGraphEngine."""

    def register_namespace(self, prefix: str, uri: str) -> None:
        """Persist a namespace prefix → URI mapping for use in Turtle/JSON-LD export.

        Args:
            prefix: Short prefix string (e.g. "ex").
            uri: Full namespace URI (e.g. "http://example.org/").
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                "UPDATE Graph_KG.rdf_namespaces SET uri = ? WHERE prefix = ?",
                [uri, prefix],
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO Graph_KG.rdf_namespaces (prefix, uri) VALUES (?, ?)",
                    [prefix, uri],
                )
            self.conn.commit()
        finally:
            cur.close()

    def list_namespaces(self) -> Dict[str, str]:
        """Return all registered namespace prefixes as {prefix: uri}."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT prefix, uri FROM Graph_KG.rdf_namespaces")
            return {row[0]: row[1] for row in cur.fetchall()}
        except Exception:
            return {}
        finally:
            cur.close()

    def export_rdf(
        self,
        path: str,
        format: Optional[str] = None,
        label_filter: Optional[List[str]] = None,
        graph_id: Optional[str] = None,
        node_ids: Optional[List[str]] = None,
        base_uri: str = "urn:ivg:",
    ) -> Dict:
        """Export the graph (or a filtered subgraph) to an RDF file.

        Args:
            path: Output file path.
            format: One of "turtle", "nt", "nquads", "json-ld". Inferred from
                extension if None.
            label_filter: Export only nodes with these labels.
            graph_id: Export only triples in this named graph.
            node_ids: Export only these specific nodes.
            base_uri: IRI prefix for bare string node IDs.

        Returns:
            dict with keys: triples, nodes, edges, path
        """
        _require_rdflib()
        fmt = format or _infer_format(path)

        g = _build_rdflib_graph(
            self.conn,
            label_filter=label_filter,
            graph_id=graph_id,
            node_ids=node_ids,
            base_uri=base_uri,
        )

        # Bind registered namespaces
        for prefix, uri in self.list_namespaces().items():
            try:
                from rdflib import Namespace
                g.bind(prefix, Namespace(uri))
            except Exception:
                pass

        g.serialize(destination=path, format=fmt)

        triple_count = len(g)
        subjects = set(g.subjects())
        node_count = len(subjects)
        # Count edge triples (subject predicate object where object is URIRef)
        from rdflib import URIRef
        from rdflib.namespace import RDF
        edge_count = sum(
            1 for s, p, o in g
            if isinstance(o, URIRef) and p != RDF.type
        )

        logger.info("Exported %d triples to %s (format=%s)", triple_count, path, fmt)
        return {"triples": triple_count, "nodes": node_count, "edges": edge_count, "path": path}

    def export_rdf_from_cypher(
        self,
        query: str,
        path: str,
        parameters: Optional[Dict] = None,
        format: Optional[str] = None,
        base_uri: str = "urn:ivg:",
    ) -> Dict:
        """Run a Cypher query and serialize the result nodes/edges as RDF.

        Args:
            query: openCypher query returning nodes and/or relationships.
            path: Output file path.
            parameters: Cypher bind parameters.
            format: RDF format. Inferred from extension if None.
            base_uri: IRI prefix for bare string node IDs.

        Returns:
            dict with keys: triples, path
        """
        rdflib = _require_rdflib()
        from rdflib import URIRef, Graph as RDFGraph
        from rdflib.namespace import RDF

        fmt = format or _infer_format(path)
        result = self.execute_cypher(query, parameters=parameters or {})

        g = RDFGraph()
        triple_count = 0

        # Project Cypher result rows as RDF triples
        # Columns named "s"/"subject" + "p"/"predicate" + "o"/"object" → SPO triple
        # Otherwise: first node column as subject, relationship as predicate, second as object
        cols = result.columns if result.columns else []

        for row in (result.rows or []):
            row_data = dict(zip(cols, row)) if cols else {}
            _project_row_as_rdf(g, row_data, cols, base_uri)
            triple_count = len(g)

        # Bind registered namespaces
        for prefix, uri in self.list_namespaces().items():
            try:
                from rdflib import Namespace
                g.bind(prefix, Namespace(uri))
            except Exception:
                pass

        g.serialize(destination=path, format=fmt)
        logger.info("Exported %d triples from Cypher to %s", triple_count, path)
        return {"triples": triple_count, "path": path}


def _project_row_as_rdf(g, row_data: dict, cols: list, base_uri: str) -> None:
    """Project a single Cypher result row as RDF triples into g."""
    try:
        from rdflib import URIRef, Literal
        from rdflib.namespace import RDF
    except ImportError:
        return

    def _u(v):
        if v is None:
            return None
        s = str(v)
        return URIRef(_mint_iri(s, base_uri))

    # Try explicit s/p/o column mapping
    if "s" in row_data and "p" in row_data and "o" in row_data:
        s, p, o = _u(row_data["s"]), _u(row_data["p"]), _u(row_data["o"])
        if s and p and o:
            g.add((s, p, o))
        return

    # Try node/relationship/node pattern (3 columns: n1, rel, n2 or similar)
    if len(cols) >= 3:
        s_val, p_val, o_val = row_data.get(cols[0]), row_data.get(cols[1]), row_data.get(cols[2])
        if s_val and p_val and o_val:
            s, p, o = _u(s_val), _u(p_val), _u(o_val)
            if s and p and o:
                g.add((s, p, o))
        return

    # Single node column → emit rdf:type triple if the value looks like a URI
    for col in cols:
        val = row_data.get(col)
        if val and isinstance(val, str) and val.startswith(("http://", "https://", "urn:")):
            g.add((_u(val), RDF.type, URIRef("urn:ivg:Node")))
