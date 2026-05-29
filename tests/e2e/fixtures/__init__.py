"""Graph fixtures for centrality algorithm e2e tests (Spec 162).

Provides reusable graph builders for the four centrality algorithms:
- Erdős-Rényi for general parity testing against networkx
- Disconnected components for harmonic/classical Closeness divergence
- Directed cycle for Eigenvector uniformity property
- High-fanout for Brandes memBudgetMB skip behavior

Each builder returns a dict with:
- nodes: list[str] of node IDs
- edges: list[tuple[str, str, str]] (subject, predicate, object)
- nx_graph: networkx.DiGraph reference for parity comparison
"""
