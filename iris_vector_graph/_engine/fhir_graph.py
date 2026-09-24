"""FHIR repository as a named graph (spec 231).

The projection, sync and concept resolution run in ``Graph.KG.FHIRGraph`` in the
FHIR namespace, next to the repository's tables (plan.md, "Where the work runs").
These methods validate their arguments before any round trip, call the class
method, and turn its JSON reply into a value or a ``FHIRGraphError``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from iris_vector_graph._validate import validate_graph_name
from iris_vector_graph.exceptions import FHIRGraphError
from iris_vector_graph.fhir_links import parse_json_links

_CLS = "Graph.KG.FHIRGraph"

CROSSWALK_RELATIONS = ("exact", "broader", "narrower", "related")

_DENY_ENTRY = re.compile(r"^[A-Za-z]+\.[A-Za-z0-9_-]+$")
_SOURCE_KEY = re.compile(r"^[A-Za-z]+/[^/\s]+$")
_PARAM = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_HOPS = 10

# The short names Graph_KG.fhir_bridges uses, and the system URIs a FHIR token holds.
_SYSTEM_URIS = {
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10": "http://hl7.org/fhir/sid/icd-10",
    "SNOMEDCT": "http://snomed.info/sct",
    "SNOMED": "http://snomed.info/sct",
    "LOINC": "http://loinc.org",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
}


def fhir_graph_id(namespace: str, package: str) -> str:
    """``fhir:<namespace>:<pkg>`` for a repository; ``package`` is the resource
    classes package (``HSFHIR.X0001.R``) or its second piece (``X0001``)."""
    pkg = package.split(".")[1] if "." in package else package
    return validate_graph_name(f"fhir:{namespace}:{pkg}")


def code_system_uri(system: str) -> str:
    """The FHIR system URI for a ``fhir_bridges`` system name. A URI, or a name
    with no known URI, is returned unchanged rather than guessed."""
    return _SYSTEM_URIS.get(system.upper(), system)


def _fhir_graph(graph: Optional[str]) -> str:
    """A FHIR graph is always a named graph: the default graph is not one."""
    name = validate_graph_name(graph)
    if not name:
        raise ValueError("a FHIR graph id is required, e.g. 'fhir:IVGFHIR:X0001'")
    return name


def _strings(values, what: str) -> List[str]:
    out = list(values)
    for v in out:
        if not isinstance(v, str) or not v:
            raise ValueError(f"{what} must be non-empty strings, got {v!r}")
    return out


def _relations(relations) -> str:
    if relations is None:
        return ""
    rels = list(relations)
    for r in rels:
        if r not in CROSSWALK_RELATIONS:
            raise ValueError(f"relation {r!r} is not one of {CROSSWALK_RELATIONS}")
    return json.dumps(rels)


class FhirGraphMixin:
    """Engine methods for FHIR named graphs (FR-017)."""

    def _fhir_call(self, operation: str, *args) -> Dict[str, Any]:
        raw = self._iris_obj().classMethodValue(_CLS, operation, *args)
        reply = json.loads(raw) if raw else {}
        if reply.get("status") == "error":
            raise FHIRGraphError(operation, reply.get("error", "unknown error"))
        return reply

    # --------------------------------------------------------------- registry

    def fhir_graph_register(
        self,
        endpoint: str = "",
        denylist: Optional[List[str]] = None,
        interval_s: int = 60,
        json_links: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Register the connected namespace's FHIR repository as a named graph.

        ``endpoint`` picks the repository when the namespace has more than one.
        ``denylist`` holds ``Type.param`` entries whose references are not edges.
        ``json_links`` names links the search index cannot see (``Type.element`` or
        ``extension:<url>``); ``None`` keeps the default, ``[]`` turns them off.
        Re-registering updates the settings and keeps the watermarks; a changed
        denylist applies at the next rebuild, a changed json_links list rebuilds now
        (``"rebuilt"`` in the reply).
        """
        deny = list(denylist or [])
        for entry in deny:
            if not isinstance(entry, str) or not _DENY_ENTRY.match(entry):
                raise ValueError(f"denylist entry {entry!r} is not 'Type.param'")
        if not isinstance(interval_s, int) or interval_s < 60:
            raise ValueError(
                f"interval_s must be at least 60 (Task Manager counts minutes), got {interval_s!r}"
            )
        links = "" if json_links is None else json.dumps(parse_json_links(json_links))
        return self._fhir_call("Register", endpoint or "", json.dumps(deny), interval_s, links)

    def fhir_graph_rebuild(self, graph: str) -> Dict[str, Any]:
        """Reconcile the graph to the repository and reset its watermarks."""
        return self._fhir_call("Rebuild", _fhir_graph(graph))

    def fhir_graph_sync(self, graph: str) -> Dict[str, Any]:
        """Apply every change since the watermarks. ``{"status": "busy"}`` when a
        sync or rebuild of the same graph holds its lock."""
        return self._fhir_call("SyncOnce", _fhir_graph(graph))

    def fhir_graph_status(self, graph: str) -> Dict[str, Any]:
        return self._fhir_call("Status", _fhir_graph(graph))

    def fhir_link_report(self, graph: str, source: Optional[str] = None) -> Dict[str, Any]:
        """Every canonical and json-link reference in the graph, or one source key's,
        with its outcome: the edge target key or the unresolved reason."""
        name = _fhir_graph(graph)
        if source is not None and not (isinstance(source, str) and _SOURCE_KEY.match(source)):
            raise ValueError(f"source must be a 'Type/id' key, got {source!r}")
        return self._fhir_call("LinkReport", name, source or "")

    def fhir_graph_schedule(self, graph: str, interval_s: Optional[int] = None) -> Dict[str, Any]:
        """Create or update the graph's Task Manager task; ``None`` keeps the
        registered interval."""
        if interval_s is not None and (not isinstance(interval_s, int) or interval_s < 60):
            raise ValueError(
                f"interval_s must be at least 60 (Task Manager counts minutes), got {interval_s!r}"
            )
        return self._fhir_call("Schedule", _fhir_graph(graph), interval_s or 0)

    def fhir_graph_unschedule(self, graph: str) -> Dict[str, Any]:
        return self._fhir_call("Unschedule", _fhir_graph(graph))

    # --------------------------------------------------------------- concepts

    def fhir_resolve_concepts(
        self,
        graph: str,
        concept_graph: Optional[str],
        ids: List[str],
        *,
        params: Optional[List[str]] = None,
        relations: Optional[List[str]] = None,
    ) -> List[str]:
        """Keys of live resources in ``graph`` whose token param (default ``code``)
        matches a code crosswalked to one of ``ids`` in ``concept_graph``."""
        g = _fhir_graph(graph)
        cg = validate_graph_name(concept_graph)
        concept_ids = _strings(ids, "concept ids")
        params_json = ""
        if params is not None:
            ps = list(params)
            for p in ps:
                if not isinstance(p, str) or not _PARAM.match(p):
                    raise ValueError(f"param {p!r} is not a search param name")
            params_json = json.dumps(ps)
        relations_json = _relations(relations)
        if not concept_ids:
            return []
        reply = self._fhir_call(
            "ResolveConcepts", g, cg, json.dumps(concept_ids), params_json, relations_json
        )
        return list(reply.get("keys", []))

    def fhir_expand_concepts(
        self,
        concept_graph: Optional[str],
        ids: List[str],
        *,
        predicates: Optional[List[str]] = None,
        hops: int = 1,
    ) -> List[str]:
        """``ids`` and every concept reachable from them in at most ``hops`` over
        ``concept_graph``'s out-edges, optionally only along ``predicates``."""
        cg = validate_graph_name(concept_graph)
        if isinstance(hops, bool) or not isinstance(hops, int) or not 0 <= hops <= _MAX_HOPS:
            raise ValueError(f"hops must be an integer in 0..{_MAX_HOPS}, got {hops!r}")
        concept_ids = _strings(ids, "concept ids")
        preds = "" if predicates is None else json.dumps(_strings(predicates, "predicates"))
        reply = self._fhir_call("ExpandConcepts", cg, json.dumps(concept_ids), preds, hops)
        return list(reply.get("ids", []))

    def fhir_concept_ppr(
        self,
        graph: str,
        concept_graph: Optional[str],
        ids: List[str],
        *,
        hops: int = 1,
        predicates: Optional[List[str]] = None,
        params: Optional[List[str]] = None,
        relations: Optional[List[str]] = None,
        top_k: Optional[int] = 50,
        damping_factor: float = 0.85,
        max_iterations: int = 20,
    ) -> Dict[str, float]:
        """Expand concepts, resolve them to FHIR resources, and rank the FHIR graph
        around them: one operator per graph, joined as a pipeline (spec 231).

        Returns PPR scores keyed by FHIR key; empty when nothing resolves.
        """
        g = _fhir_graph(graph)
        concepts = (
            self.fhir_expand_concepts(concept_graph, ids, predicates=predicates, hops=hops)
            if hops
            else list(ids)
        )
        seeds = self.fhir_resolve_concepts(
            g, concept_graph, concepts, params=params, relations=relations
        )
        if not seeds:
            return {}
        return self.kg_PERSONALIZED_PAGERANK(
            seeds,
            damping_factor=damping_factor,
            max_iterations=max_iterations,
            return_top_k=top_k,
            bidirectional=True,
            graph=g,
        )

    # --------------------------------------------------------------- crosswalk

    _CROSSWALK_UPSERT = (
        "INSERT OR UPDATE INTO Graph_KG.code_crosswalk "
        "(code_system_uri, code, target_graph, target_node_id, relation, source, "
        "source_version, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def code_crosswalk_add(
        self,
        code_system_uri: str,
        code: str,
        target_node_id: str,
        *,
        target_graph: Optional[str] = None,
        relation: str = "exact",
        source: Optional[str] = None,
        source_version: Optional[str] = None,
        confidence: float = 1.0,
    ) -> None:
        """Map ``(code_system_uri, code)`` to a node; re-adding updates the row."""
        for name, value in (
            ("code_system_uri", code_system_uri),
            ("code", code),
            ("target_node_id", target_node_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} is required")
        if relation not in CROSSWALK_RELATIONS:
            raise ValueError(f"relation {relation!r} is not one of {CROSSWALK_RELATIONS}")
        params = [
            code_system_uri,
            code,
            validate_graph_name(target_graph),
            target_node_id,
            relation,
            source,
            source_version,
            float(confidence),
        ]
        cursor = self.conn.cursor()
        try:
            cursor.execute(self._CROSSWALK_UPSERT, params)
            self.conn.commit()
        finally:
            cursor.close()

    def migrate_fhir_bridges_to_crosswalk(self) -> Dict[str, int]:
        """Copy every ``fhir_bridges`` row into ``code_crosswalk`` (FR-015).

        Bridges point at default-graph nodes and say nothing about how close the
        codes are, so each becomes a ``related`` row in the default graph with
        ``source='fhir_bridges'`` and ``source_version=bridge_type``. An upsert,
        so running it again rewrites the same rows.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT fhir_code, kg_node_id, fhir_code_system, bridge_type, confidence "
                f"FROM {self._t('fhir_bridges')}"
            )
            rows = cursor.fetchall()
            for code, node, system, bridge_type, confidence in rows:
                cursor.execute(
                    self._CROSSWALK_UPSERT,
                    [
                        code_system_uri(system or "ICD10CM"),
                        code,
                        "",
                        node,
                        "related",
                        "fhir_bridges",
                        bridge_type,
                        1.0 if confidence is None else float(confidence),
                    ],
                )
            self.conn.commit()
            return {"migrated": len(rows)}
        finally:
            cursor.close()
