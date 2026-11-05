"""4.6.0 R290: every statement Graph.KG.LedgerApply runs on a node's labels, properties or vectors names its graph.

The ledger keeps nodes in the default graph (R281). set_prop's lookup and UPDATE, DeleteNodeRows' deletes, the
incident-relationship lookup of delete_node, and the label and property statements of add_label, remove_label and
remove_prop matched by node id alone, so a node id held by two graphs was read and written in both. Each now carries
the class parameter DEFAULTGRAPH (`COALESCE(graph_id, '') = ''`, a literal: an empty string bound to a parameter
arrives as NULL), or writes graph_id ''. The live check, two graphs sharing an id and the database state after each
commit, is tests/e2e/test_460_r290_ledger_node_ops_graph_e2e.py.

Not scoped, on purpose: NodeExists (node_not_found means the id is unknown, see EnsureEndpoint) and the statements on
relationships, which carry the relationship's own graph.
"""

from __future__ import annotations

import re

from iris_vector_graph._engine import legacy

NODE_TABLES = ("rdf_labels", "rdf_props", "kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized")


def _source() -> str:
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    return read_class_sources(packaged_class_dir())["Graph.KG.LedgerApply"]


def _statements(src: str) -> list:
    """(method, statement text) for every ..Run( call."""
    out = []
    for name, body in re.findall(r"^ClassMethod (\w+)\(.*?\n\{\n(.*?)\n\}", src, re.M | re.S):
        for line in legacy._code(body).splitlines():
            if "..Run(" in line:
                out.append((name, line.strip()))
    return out


def test_the_parameter_is_the_default_graph_literal():
    assert "Parameter DEFAULTGRAPH = \"COALESCE(graph_id, '') = ''\";" in _source()


def test_every_statement_on_a_nodes_labels_properties_or_vectors_names_the_graph():
    loose = [(m, s) for m, s in _statements(_source())
             if re.search(r'\.(?:' + "|".join(NODE_TABLES) + r')\b', s)
             and "..#DEFAULTGRAPH" not in s and "graph_id" not in s]
    assert loose == []


def test_delete_node_rows_and_the_incident_lookup_are_the_default_graphs():
    stmts = _statements(_source())
    deletes = [s for m, s in stmts if m == "DeleteNodeRows"]
    assert len(deletes) == 5 and all("..#DEFAULTGRAPH" in s for s in deletes), deletes
    incident = [s for m, s in stmts if m == "OpDeleteNode" and "rdf_edges" in s]
    assert len(incident) == 1 and "..#DEFAULTGRAPH" in incident[0], incident


def test_set_prop_reads_updates_and_inserts_the_default_graphs_row():
    stmts = [s for m, s in _statements(_source()) if m == "SetProp"]
    assert len(stmts) == 3
    assert all("..#DEFAULTGRAPH" in s for s in stmts if "SELECT" in s or "UPDATE" in s)
    assert any("INSERT" in s and "graph_id) VALUES (?, ?, ?, '')" in s for s in stmts)
