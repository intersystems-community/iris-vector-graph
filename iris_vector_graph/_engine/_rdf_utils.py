"""Shared RDF utility functions for rdf_export.py and shacl.py.

Kept as a private module (_rdf_utils) to avoid cross-mixin import dependencies —
both RdfExportMixin and ShaclMixin need _build_rdflib_graph() without importing
each other.

Public surface (for internal use only):
    _build_rdflib_graph(conn, ...) — streaming cursor over rdf_labels/rdf_props/rdf_edges
                                     → rdflib.ConjunctiveGraph; supports all filter params
    _infer_format(path)            — file extension → rdflib format string
    _mint_iri(node_id, base_uri)   — bare string IDs → urn:ivg: URIs; valid IRIs pass through
    _to_literal(val)               — typed rdflib.Literal from stored string values
"""
from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    pass

_RDF_IRI = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDFS_IRI = "http://www.w3.org/2000/01/rdf-schema#"
_XSD_IRI = "http://www.w3.org/2001/XMLSchema#"


def _mint_iri(node_id: str, base_uri: str = "urn:ivg:") -> str:
    """Return a valid IRI for a node_id. Bare strings get a urn:ivg: prefix."""
    if node_id.startswith(("http://", "https://", "urn:", "ftp://")):
        return node_id
    return f"{base_uri}{node_id}"


def _infer_format(path: str) -> str:
    """Infer rdflib format string from file extension."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "ttl": "turtle",
        "nt": "nt",
        "nq": "nquads",
        "nquads": "nquads",
        "jsonld": "json-ld",
        "json": "json-ld",
        "trig": "trig",
        "n3": "n3",
    }.get(ext, "turtle")


def _to_literal(val: str):
    """Convert a stored string value to an appropriate rdflib Literal."""
    try:
        import rdflib
        from rdflib import XSD
    except ImportError:
        return val

    if val is None:
        return None
    s = str(val)
    # Try integer
    try:
        int_val = int(s)
        if str(int_val) == s:
            return rdflib.Literal(int_val, datatype=XSD.integer)
    except (ValueError, OverflowError):
        pass
    # Try float
    try:
        float_val = float(s)
        return rdflib.Literal(float_val, datatype=XSD.decimal)
    except ValueError:
        pass
    # Boolean
    if s.lower() in ("true", "false"):
        return rdflib.Literal(s.lower() == "true", datatype=XSD.boolean)
    return rdflib.Literal(s)


def _build_rdflib_graph(
    conn,
    label_filter: Optional[List[str]] = None,
    graph_id: Optional[str] = None,
    node_ids: Optional[List[str]] = None,
    base_uri: str = "urn:ivg:",
    batch_size: int = 500,
):
    """Build an rdflib ConjunctiveGraph from IRIS rdf_edges/rdf_props/rdf_labels.

    Args:
        conn: IRIS DBAPI connection.
        label_filter: Only include nodes with these labels.
        graph_id: Only include triples in this named graph.
        node_ids: Only include these specific nodes and their edges.
        base_uri: IRI prefix for bare string node IDs.
        batch_size: Rows per cursor batch fetch.

    Returns:
        rdflib.ConjunctiveGraph
    """
    try:
        import rdflib
        from rdflib import URIRef, BNode, Literal, ConjunctiveGraph, Graph as RDFGraph
        from rdflib.namespace import RDF, RDFS, XSD
    except ImportError as e:
        raise ImportError(
            "rdflib is required for RDF operations. "
            "Install with: pip install 'iris-vector-graph[rdf]'"
        ) from e

    g = ConjunctiveGraph()
    RDF_REIFIES = URIRef(_RDF_IRI + "reifies")
    cursor = conn.cursor()

    def _uri(nid: str) -> URIRef:
        return URIRef(_mint_iri(nid, base_uri))

    def _named(gid: Optional[str]):
        return URIRef(gid) if gid else None

    # Build SQL WHERE fragment for node scoping
    def _node_filter_clause(col: str) -> str:
        if node_ids:
            placeholders = ",".join(["?" for _ in node_ids])
            return f" AND {col} IN ({placeholders})"
        return ""

    def _node_filter_params() -> list:
        return list(node_ids) if node_ids else []

    # -- rdf_labels → rdf:type triples --
    label_sql = "SELECT s, label FROM Graph_KG.rdf_labels WHERE 1=1"
    label_params: list = []
    if label_filter:
        label_sql += " AND label IN (" + ",".join(["?" for _ in label_filter]) + ")"
        label_params.extend(label_filter)
    label_sql += _node_filter_clause("s")
    label_params.extend(_node_filter_params())

    cursor.execute(label_sql, label_params)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for s, label in rows:
            subj = _uri(s)
            type_obj = _uri(label)
            g.add((subj, RDF.type, type_obj))

    # -- rdf_props → literal triples --
    props_sql = 'SELECT s, "key", val FROM Graph_KG.rdf_props WHERE 1=1'
    props_params: list = []
    props_sql += _node_filter_clause("s")
    props_params.extend(_node_filter_params())

    cursor.execute(props_sql, props_params)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for s, key, val in rows:
            subj = _uri(s)
            pred = _uri(key) if key.startswith(("http://", "https://", "urn:")) else URIRef(f"urn:ivg:prop/{key}")
            lit = _to_literal(val)
            if lit is not None:
                g.add((subj, pred, lit))

    # -- rdf_edges → SPO triples (with optional named graph and qualifiers) --
    edges_sql = "SELECT s, p, o_id, qualifiers, graph_id FROM Graph_KG.rdf_edges WHERE 1=1"
    edges_params: list = []
    if graph_id:
        edges_sql += " AND graph_id = ?"
        edges_params.append(graph_id)
    if label_filter:
        # Scope to nodes that have those labels
        edges_sql += " AND s IN (SELECT s FROM Graph_KG.rdf_labels WHERE label IN (" + ",".join(["?" for _ in label_filter]) + "))"
        edges_params.extend(label_filter)
    edges_sql += _node_filter_clause("s")
    edges_params.extend(_node_filter_params())

    cursor.execute(edges_sql, edges_params)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for s, p, o_id, qualifiers_json, gid in rows:
            subj = _uri(s)
            pred = _uri(p)
            obj = _uri(o_id)
            named = _named(gid)

            if named:
                ctx = g.get_context(named)
                ctx.add((subj, pred, obj))
            else:
                g.add((subj, pred, obj))

            # Qualifier reification using rdf:reifies as plain SPO triple
            if qualifiers_json:
                try:
                    quals = _json.loads(qualifiers_json) if isinstance(qualifiers_json, str) else qualifiers_json
                    if quals and isinstance(quals, dict):
                        reifier = BNode()
                        # rdf:reifies points from reifier to the statement triple
                        # Encoded as a plain triple: reifier rdf:reifies <<s p o>>
                        # rdflib 6/7 has no native triple-term support, so we encode
                        # the statement identity as a structured blank node pattern
                        stmt_node = BNode()
                        g.add((stmt_node, RDF.subject, subj))
                        g.add((stmt_node, RDF.predicate, pred))
                        g.add((stmt_node, RDF.object, obj))
                        g.add((reifier, RDF_REIFIES, stmt_node))
                        for qk, qv in quals.items():
                            if qk in ("inferred",):
                                continue
                            qpred = URIRef(f"urn:ivg:qualifier/{qk}")
                            g.add((reifier, qpred, _to_literal(str(qv)) or Literal(str(qv))))
                except (ValueError, TypeError):
                    pass

    cursor.close()
    return g
