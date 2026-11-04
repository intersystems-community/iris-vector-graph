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
from iris_vector_graph.schema_name import DEFAULT_SCHEMA

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
    schema: str = DEFAULT_SCHEMA,
):
    """Build an rdflib ConjunctiveGraph from IRIS rdf_edges/rdf_props/rdf_labels.

    Args:
        conn: IRIS DBAPI connection.
        label_filter: Only include nodes with these labels.
        graph_id: Only include triples in this named graph.
        node_ids: Only include these specific nodes and their edges.
        base_uri: IRI prefix for bare string node IDs.
        schema: The IVG Schema the tables are in (the engine's; spec 262).
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

    # Where a row's triples go: its named graph's context, or the default graph. Before 4.6.0 (R100) only edges were
    # put in their graph; a named graph's rdf:type, literal and qualifier triples went to the default graph.
    def _target(gid: Optional[str]):
        return g.get_context(URIRef(gid)) if gid else g

    # Build SQL WHERE fragment for node scoping
    def _node_filter_clause(col: str) -> str:
        if node_ids:
            placeholders = ",".join(["?" for _ in node_ids])
            return f" AND {col} IN ({placeholders})"
        return ""

    def _node_filter_params() -> list:
        return list(node_ids) if node_ids else []

    # Build SQL WHERE fragment for graph scoping.
    #
    # One fragment for all three tables, because they were not all scoped: only
    # rdf_edges carried a graph predicate, so exporting one named graph wrote that
    # graph's edges beside *every* graph's rdf:type and literal triples. Both
    # rdf_labels and rdf_props have a graph_id column — it is the leading column of
    # each of their primary keys — and both were read with a bare WHERE 1=1
    # (spec 230, FR-001c).
    #
    # Three meanings, kept distinct:
    #   None -> every graph, which is what export_rdf documents and an export tool
    #           should default to;
    #   ""   -> the default graph, as it means everywhere else in IVG. `if graph_id:`
    #           could not tell this from None, so asking for the default graph
    #           returned the whole namespace;
    #   name -> that graph. COALESCE is not used here: a named graph must not also
    #           match rows that predate the backfill and spell "no graph" as NULL.
    def _graph_filter_clause() -> str:
        if graph_id is None:
            return ""
        if graph_id == "":
            return " AND COALESCE(graph_id, '') = ''"
        return " AND graph_id = ?"

    def _graph_filter_params() -> list:
        return [graph_id] if graph_id else []

    # A row of `alias` whose subject carries one of `label_filter`'s labels in the row's own graph. Before 4.6.0
    # (R099) rdf_props had no label condition at all, so a filtered export held every node's literals, and the
    # edge condition matched a label from any graph.
    def _label_filter_clause(alias: str) -> str:
        if not label_filter:
            return ""
        marks = ",".join(["?" for _ in label_filter])
        return (
            f" AND EXISTS (SELECT 1 FROM {schema}.rdf_labels lf WHERE lf.s = {alias}.s"
            f" AND COALESCE(lf.graph_id, '') = COALESCE({alias}.graph_id, '') AND lf.label IN ({marks}))"
        )

    def _label_filter_params() -> list:
        return list(label_filter) if label_filter else []

    # -- rdf_labels → rdf:type triples --
    label_sql = f"SELECT s, label, graph_id FROM {schema}.rdf_labels WHERE 1=1"
    label_params: list = []
    label_sql += _graph_filter_clause()
    label_params.extend(_graph_filter_params())
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
        for s, label, gid in rows:
            _target(gid).add((_uri(s), RDF.type, _uri(label)))

    # -- rdf_props → literal triples --
    props_sql = f'SELECT s, "key", val, graph_id FROM {schema}.rdf_props pr WHERE 1=1'
    props_params: list = []
    props_sql += _graph_filter_clause()
    props_params.extend(_graph_filter_params())
    props_sql += _label_filter_clause("pr")
    props_params.extend(_label_filter_params())
    props_sql += _node_filter_clause("s")
    props_params.extend(_node_filter_params())

    cursor.execute(props_sql, props_params)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        for s, key, val, gid in rows:
            subj = _uri(s)
            pred = _uri(key) if key.startswith(("http://", "https://", "urn:")) else URIRef(f"urn:ivg:prop/{key}")
            lit = _to_literal(val)
            if lit is not None:
                _target(gid).add((subj, pred, lit))

    # -- rdf_edges → SPO triples (with optional named graph and qualifiers) --
    edges_sql = f"SELECT s, p, o_id, qualifiers, graph_id FROM {schema}.rdf_edges ed WHERE 1=1"
    edges_params: list = []
    edges_sql += _graph_filter_clause()
    edges_params.extend(_graph_filter_params())
    # an edge whose source has those labels
    edges_sql += _label_filter_clause("ed")
    edges_params.extend(_label_filter_params())
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
            ctx = _target(gid)
            ctx.add((subj, pred, obj))

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
                        ctx.add((stmt_node, RDF.subject, subj))
                        ctx.add((stmt_node, RDF.predicate, pred))
                        ctx.add((stmt_node, RDF.object, obj))
                        ctx.add((reifier, RDF_REIFIES, stmt_node))
                        for qk, qv in quals.items():
                            if qk in ("inferred",):
                                continue
                            qpred = URIRef(f"urn:ivg:qualifier/{qk}")
                            ctx.add((reifier, qpred, _to_literal(str(qv)) or Literal(str(qv))))
                except (ValueError, TypeError):
                    pass

    cursor.close()
    return g
