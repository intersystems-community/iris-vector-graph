"""Data models for iris-vector-graph."""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class SubgraphData:
    """Complete bounded subgraph extracted via kg_SUBGRAPH.

    Contains all nodes, edges, properties, labels, and optionally embeddings
    within k hops of seed nodes.
    """
    nodes: List[str] = field(default_factory=list)
    edges: List[Tuple[str, str, str]] = field(default_factory=list)
    node_properties: Dict[str, Dict[str, str]] = field(default_factory=dict)
    node_labels: Dict[str, List[str]] = field(default_factory=dict)
    node_embeddings: Dict[str, List[float]] = field(default_factory=dict)
    seed_ids: List[str] = field(default_factory=list)
