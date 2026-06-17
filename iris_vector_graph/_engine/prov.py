"""PROV-O temporal alignment mixin for IRISGraphEngine.

Maps IVG temporal edges (^KG("tout"/"tin") globals) to W3C PROV-O vocabulary
without migrating storage. Vocabulary projection only — no schema changes.

Requires: pip install 'iris-vector-graph[rdf]'   (rdflib >= 6.0.0)

Vocabulary mapping:
    temporal edge     → prov:Activity
    source node       → prov:Entity  (via prov:used)
    target node       → prov:Entity
    ts_start (Unix)   → prov:startedAtTime "..."^^xsd:dateTime
    ts_end (Unix)     → prov:endedAtTime "..."^^xsd:dateTime  (omitted if None)
    edge predicate    → property on Activity (URI preserved)
    edge_id           → Activity IRI: urn:ivg:activity/{url-encoded-id}
    node_id           → Entity IRI: urn:ivg:entity/{id} or bare IRI if valid

Public methods (added to IRISGraphEngine via ProvMixin):

    prov_export(path, format, ts_start, ts_end) → {"activities", "entities", "path"}
        Serialize temporal edges as PROV-O Turtle or JSON-LD. Supports time-window
        filtering; empty result produces a valid but empty PROV-O file.

    prov_export_from_cypher(query, path, parameters, format) → {"activities", "path"}
        Export PROV-O for temporal edges involving nodes matched by a Cypher query.

    prov_as_dict(edge_id) → dict
        Return the PROV-O mapping for a single temporal edge without file I/O.
        Keys: activity, type, startedAtTime, endedAtTime (optional), used,
        predicate, object.
        Raises KeyError if edge_id not found.

See: docs/SEMANTIC_LAYER.md for vocabulary reference and agentic provenance patterns.
"""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROV_NS = "http://www.w3.org/ns/prov#"
_XSD_NS = "http://www.w3.org/2001/XMLSchema#"


def _require_rdflib():
    try:
        import rdflib
        return rdflib
    except ImportError as e:
        raise ImportError(
            "rdflib is required for PROV-O export. "
            "Install with: pip install 'iris-vector-graph[rdf]'"
        ) from e


def _ts_to_datetime(ts: int) -> str:
    """Convert Unix epoch integer to ISO 8601 UTC string for xsd:dateTime."""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return "1970-01-01T00:00:00Z"


def _node_to_iri(node_id: str, base_uri: str = "urn:ivg:") -> str:
    """Return a valid IRI for a node, prefixed with urn:ivg:entity/ if bare."""
    from iris_vector_graph._engine._rdf_utils import _mint_iri
    iri = _mint_iri(node_id, base_uri)
    if iri.startswith("urn:ivg:") and not iri.startswith("urn:ivg:entity/"):
        return iri.replace("urn:ivg:", "urn:ivg:entity/", 1)
    return iri


def _activity_iri(edge_id, base_uri: str = "urn:ivg:") -> str:
    from urllib.parse import quote
    safe_id = quote(str(edge_id), safe="")
    return f"urn:ivg:activity/{safe_id}"


class ProvMixin:
    """PROV-O temporal alignment capabilities for IRISGraphEngine."""

    def prov_as_dict(self, edge_id) -> Dict[str, Any]:
        """Return the PROV-O mapping for a single temporal edge as a dict.

        Args:
            edge_id: Temporal edge identifier (int or str key in ^KG globals).

        Returns:
            dict with keys: activity, type, startedAtTime, endedAtTime (optional),
            used, predicate, object.

        Raises:
            KeyError: If edge_id not found.
        """
        edges = self._get_temporal_edges_by_id([edge_id])
        if not edges:
            raise KeyError(f"Temporal edge not found: {edge_id!r}")
        return _edge_to_prov_dict(edges[0])

    def prov_export(
        self,
        path: str,
        format: str = "turtle",
        ts_start: Optional[int] = None,
        ts_end: Optional[int] = None,
    ) -> Dict:
        """Serialize temporal edges as W3C PROV-O.

        Args:
            path: Output file path.
            format: "turtle" or "json-ld".
            ts_start: Unix timestamp lower bound (inclusive).
            ts_end: Unix timestamp upper bound (inclusive).

        Returns:
            dict with keys: activities, entities, path.
        """
        _require_rdflib()
        edges = self._get_temporal_edges_window(ts_start, ts_end)
        g = _build_prov_graph(edges)
        g.serialize(destination=path, format=format)

        activity_count = len(edges)
        entity_ids = set()
        for e in edges:
            entity_ids.add(e.get("source", ""))
            entity_ids.add(e.get("target", ""))
        entity_count = len({x for x in entity_ids if x})

        logger.info("Exported %d PROV-O activities to %s", activity_count, path)
        return {"activities": activity_count, "entities": entity_count, "path": path}

    def prov_export_from_cypher(
        self,
        query: str,
        path: str,
        parameters: Optional[Dict] = None,
        format: str = "turtle",
    ) -> Dict:
        """Export PROV-O for temporal edges matching a Cypher query.

        The query should return rows that identify temporal edge sources/targets
        or edge IDs. Any node_id values found in result rows are used to scope
        the temporal edge export.

        Args:
            query: openCypher query.
            path: Output file path.
            parameters: Cypher bind parameters.
            format: "turtle" or "json-ld".

        Returns:
            dict with keys: activities, path.
        """
        _require_rdflib()
        result = self.execute_cypher(query, parameters=parameters or {})

        # Collect node IDs from result to scope temporal edges
        node_ids = set()
        for row in (result.rows or []):
            for val in row:
                if val and isinstance(val, str):
                    node_ids.add(val)

        edges = self._get_temporal_edges_for_nodes(list(node_ids)) if node_ids else []
        g = _build_prov_graph(edges)
        g.serialize(destination=path, format=format)

        return {"activities": len(edges), "path": path}

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_temporal_edges_window(
        self, ts_start: Optional[int], ts_end: Optional[int]
    ) -> List[Dict]:
        """Fetch temporal edges within an optional time window."""
        try:
            import json
            iris_obj = self._iris_obj()
            result_json = iris_obj.classMethodValue(
                "Graph.KG.TemporalIndex",
                "QueryWindow",
                "",  # s_filter: all sources
                "",  # p_filter: all predicates
                int(ts_start) if ts_start is not None else 0,
                int(ts_end) if ts_end is not None else 9999999999,
            )
            edges_raw = json.loads(str(result_json))
            return [
                {
                    "edge_id": f"{e['s']}|{e['p']}|{e['o']}|{e['ts']}",
                    "source": e["s"],
                    "predicate": e["p"],
                    "target": e["o"],
                    "ts_start": e["ts"],
                    "ts_end": e.get("ts_end"),
                }
                for e in edges_raw
            ]
        except Exception as ex:
            logger.warning("prov_export: could not query temporal edges: %s", ex)
            return []

    def _get_temporal_edges_for_nodes(self, node_ids: List[str]) -> List[Dict]:
        """Fetch temporal edges where source or target is in node_ids."""
        all_edges = self._get_temporal_edges_window(None, None)
        node_set = set(node_ids)
        return [e for e in all_edges if e["source"] in node_set or e["target"] in node_set]

    def _get_temporal_edges_by_id(self, edge_ids) -> List[Dict]:
        """Fetch specific temporal edges by composite ID."""
        all_edges = self._get_temporal_edges_window(None, None)
        id_set = {str(eid) for eid in edge_ids}
        return [e for e in all_edges if str(e.get("edge_id", "")) in id_set]


def _edge_to_prov_dict(edge: Dict) -> Dict[str, Any]:
    result = {
        "activity": _activity_iri(edge.get("edge_id", "")),
        "type": "prov:Activity",
        "startedAtTime": _ts_to_datetime(edge.get("ts_start", 0)),
        "used": _node_to_iri(edge.get("source", "")),
        "predicate": edge.get("predicate", ""),
        "object": _node_to_iri(edge.get("target", "")),
    }
    if edge.get("ts_end") is not None:
        result["endedAtTime"] = _ts_to_datetime(edge["ts_end"])
    return result


def _build_prov_graph(edges: List[Dict]):
    """Build an rdflib Graph of PROV-O triples from temporal edge dicts."""
    import rdflib
    from rdflib import URIRef, Literal, Graph as RDFGraph
    from rdflib.namespace import RDF, XSD

    PROV = rdflib.Namespace(_PROV_NS)
    g = RDFGraph()
    g.bind("prov", PROV)
    g.bind("xsd", XSD)

    for edge in edges:
        activity_uri = URIRef(_activity_iri(edge.get("edge_id", "")))
        source_uri = URIRef(_node_to_iri(edge.get("source", "")))
        target_uri = URIRef(_node_to_iri(edge.get("target", "")))
        pred_str = edge.get("predicate", "")
        pred_uri = URIRef(pred_str) if pred_str.startswith(("http://", "https://", "urn:")) else URIRef(f"urn:ivg:pred/{pred_str}")

        # Activity
        g.add((activity_uri, RDF.type, PROV.Activity))
        g.add((activity_uri, PROV.startedAtTime, Literal(_ts_to_datetime(edge.get("ts_start", 0)), datatype=XSD.dateTime)))
        if edge.get("ts_end") is not None:
            g.add((activity_uri, PROV.endedAtTime, Literal(_ts_to_datetime(edge["ts_end"]), datatype=XSD.dateTime)))

        # Entities
        g.add((source_uri, RDF.type, PROV.Entity))
        g.add((target_uri, RDF.type, PROV.Entity))

        # Relationships
        g.add((activity_uri, PROV.used, source_uri))
        g.add((activity_uri, pred_uri, target_uri))

    return g
