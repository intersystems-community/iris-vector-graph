"""
IRISCapabilities — runtime capability detection for the ObjectScript/.cls layer.

Determined once by initialize_schema() and cached on the engine instance.
"""
from dataclasses import dataclass


@dataclass
class IRISCapabilities:
    """Tracks which IRIS server-side capabilities are available at runtime."""

    objectscript_deployed: bool = False
    """True when Graph.KG.Edge (and Graph.KG.PageRank, Graph.KG.Traversal) are compiled in IRIS."""

    kg_built: bool = False
    """True when Graph.KG.Traversal.BuildKG() has been run and ^KG globals are populated."""

    graphoperators_deployed: bool = False
    """True when iris.vector.graph.GraphOperators is compiled (kg_KNN_VEC, kg_RRF_FUSE etc.)."""
