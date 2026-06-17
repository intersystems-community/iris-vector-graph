"""SHACL Core validation mixin for IRISGraphEngine.

Provides:
- validate_shacl(): validate the graph against SHACL Core shapes
- ValidationReport / Violation dataclasses
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_SHACL_NS = "http://www.w3.org/ns/shacl#"


@dataclass
class Violation:
    """A single SHACL constraint failure."""
    focus_node: str
    shape: str
    message: str
    severity: str  # "Violation" | "Warning" | "Info"
    path: Optional[str] = None
    value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "focus_node": self.focus_node,
            "shape": self.shape,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
            "value": self.value,
        }


@dataclass
class ValidationReport:
    """Result of engine.validate_shacl()."""
    conforms: bool
    violations: List[Violation] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.conforms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conforms": self.conforms,
            "violations": [v.to_dict() for v in self.violations],
        }


def _require_rdflib():
    try:
        import rdflib
        return rdflib
    except ImportError as e:
        raise ImportError(
            "rdflib is required for SHACL validation. "
            "Install with: pip install 'iris-vector-graph[rdf]'"
        ) from e


def _require_pyshacl():
    try:
        import pyshacl
        return pyshacl
    except ImportError as e:
        raise ImportError(
            "pyshacl is required for SHACL validation. "
            "Install with: pip install 'iris-vector-graph[rdf]'"
        ) from e


def _load_shapes_graph(shapes_source) -> "rdflib.Graph":
    """Load a shapes graph from file path, URL, string, or rdflib Graph."""
    rdflib = _require_rdflib()
    from rdflib import Graph as RDFGraph

    # Already an rdflib Graph
    if hasattr(shapes_source, "triples"):
        return shapes_source

    if not isinstance(shapes_source, str):
        raise ValueError(
            f"shapes_source must be a file path, URL, Turtle string, or rdflib.Graph. "
            f"Got: {type(shapes_source)}"
        )

    # HTTP/HTTPS URL
    if shapes_source.startswith(("http://", "https://")):
        import urllib.request
        import urllib.error
        try:
            with urllib.request.urlopen(shapes_source, timeout=30) as resp:
                content = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise IOError(f"Could not fetch shapes from {shapes_source}: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise IOError(f"Could not fetch shapes from {shapes_source}: {e.reason}") from e
        g = RDFGraph()
        g.parse(data=content, format="turtle")
        return g

    # File path
    import os
    if os.path.exists(shapes_source):
        g = RDFGraph()
        g.parse(shapes_source)
        return g

    # Inline Turtle/JSON-LD string
    g = RDFGraph()
    fmt = "json-ld" if shapes_source.strip().startswith("{") else "turtle"
    g.parse(data=shapes_source, format=fmt)
    return g


def _parse_shacl_report(report_graph, conforms: bool) -> ValidationReport:
    """Parse a pyshacl validation report graph into ValidationReport."""
    try:
        from rdflib import URIRef
        from rdflib.namespace import RDF
    except ImportError:
        return ValidationReport(conforms=conforms)

    SH = "http://www.w3.org/ns/shacl#"
    SH_ValidationResult = URIRef(SH + "ValidationResult")
    SH_focusNode = URIRef(SH + "focusNode")
    SH_sourceShape = URIRef(SH + "sourceShape")
    SH_resultMessage = URIRef(SH + "resultMessage")
    SH_resultSeverity = URIRef(SH + "resultSeverity")
    SH_resultPath = URIRef(SH + "resultPath")
    SH_value = URIRef(SH + "value")
    SH_Violation = URIRef(SH + "Violation")
    SH_Warning = URIRef(SH + "Warning")
    SH_Info = URIRef(SH + "Info")

    _severity_map = {
        str(SH_Violation): "Violation",
        str(SH_Warning): "Warning",
        str(SH_Info): "Info",
    }

    violations = []
    for result_node in report_graph.subjects(RDF.type, SH_ValidationResult):
        focus = next(report_graph.objects(result_node, SH_focusNode), None)
        shape = next(report_graph.objects(result_node, SH_sourceShape), None)
        message = next(report_graph.objects(result_node, SH_resultMessage), None)
        severity_uri = next(report_graph.objects(result_node, SH_resultSeverity), None)
        path = next(report_graph.objects(result_node, SH_resultPath), None)
        value = next(report_graph.objects(result_node, SH_value), None)

        sev_str = _severity_map.get(str(severity_uri) if severity_uri else "", "Violation")

        violations.append(Violation(
            focus_node=str(focus) if focus else "",
            shape=str(shape) if shape else "",
            message=str(message) if message else "",
            severity=sev_str,
            path=str(path) if path else None,
            value=str(value) if value else None,
        ))

    return ValidationReport(conforms=conforms, violations=violations)


class ShaclMixin:
    """SHACL Core validation capabilities for IRISGraphEngine."""

    def validate_shacl(
        self,
        shapes_source,
        node_ids: Optional[List[str]] = None,
    ) -> ValidationReport:
        """Validate the graph against SHACL Core shapes.

        Args:
            shapes_source: One of:
                - File path string (Turtle or JSON-LD shapes file)
                - HTTP/HTTPS URL to published SHACL shapes
                - Turtle/JSON-LD string
                - rdflib.Graph object
            node_ids: If provided, only validate these focus nodes (loads only
                their triples into the data graph for memory efficiency).

        Returns:
            ValidationReport with conforms flag and list of Violation objects.

        Raises:
            ImportError: If pyshacl or rdflib are not installed.
            IOError: If shapes URL is unreachable.
        """
        _require_rdflib()
        pyshacl = _require_pyshacl()

        from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph

        shapes_graph = _load_shapes_graph(shapes_source)
        data_graph = _build_rdflib_graph(self.conn, node_ids=node_ids)

        conforms, report_graph, _ = pyshacl.validate(
            data_graph,
            shacl_graph=shapes_graph,
            inference="none",
            abort_on_first=False,
        )

        return _parse_shacl_report(report_graph, bool(conforms))
