"""An independent reference for IVG's graph algorithms (4.6.0 R306).

`graphs` generates small directed multigraphs, seeded, with the shapes that broke IVG's algorithms (nodes with no
edge, self-loops, parallel edges, ties, ids that sort differently in Python and in IRIS). `reference` computes, from
networkx or from the documented rule, the answer each public algorithm is documented to give on such a graph. Neither
reads IVG: an expected answer never comes from IVG's own output.
"""
