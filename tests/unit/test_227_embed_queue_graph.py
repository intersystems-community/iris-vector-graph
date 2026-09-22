"""Spec 227 T035 — the embed queue carries the graph it was enqueued for.

The queue is the one write path that leaves Python entirely: a caller enqueues, an
ObjectScript worker (or the Python batch worker) writes the vector later, in a
different process, with no access to whatever `graph=` the caller passed. If the
graph is not stored on the entry it is gone, and the worker writes every vector
into the default graph — which is both a wrong answer and, once
`UNIQUE (graph_id, node_id)` exists, a collision between two graphs' entries for
the same node.

Asserted against the class source, not a live instance. The enterprise container
is the gate for whether this compiles and runs (T024/T025); what this file pins is
that the subscript exists on every path that writes an entry and on every path
that reads one back. A subscript written by `Enqueue` and dropped by
`ClaimPendingBatch` is the same bug as never writing it.
"""

import re
from pathlib import Path

import pytest

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "EmbedQueue.cls"


@pytest.fixture
def source():
    return CLS.read_text()


def _method(source: str, name: str) -> str:
    """One ClassMethod body, from its signature to the next ClassMethod."""
    m = re.search(
        rf"ClassMethod {name}\(.*?(?=\nClassMethod |\n\}}\s*$)", source, re.DOTALL
    )
    assert m, f"no ClassMethod {name} in EmbedQueue.cls"
    return m.group(0)


# --- the subscript is documented --------------------------------------------------


def test_the_graph_subscript_is_in_the_schema_comment(source):
    """The header comment is the only schema this global has."""
    assert re.search(r'\^EmbedQueue\(reqId,\s*"graph"\)', source), (
        "the graph subscript is undocumented; the header comment is the only place "
        "the shape of ^EmbedQueue is written down"
    )


# --- every write path stores it ---------------------------------------------------


@pytest.mark.parametrize("method", ["Enqueue", "BulkEnqueueText"])
def test_the_write_paths_store_the_graph(source, method):
    body = _method(source, method)
    assert re.search(r'Set \^EmbedQueue\([^)]*,\s*"graph"\) = ', body), (
        f"{method} does not store the graph: {body}"
    )


@pytest.mark.parametrize("method", ["Enqueue", "BulkEnqueue", "BulkEnqueueText"])
def test_the_write_paths_accept_a_graph_defaulting_to_empty(source, method):
    """`graph As %String = ""` — the default graph, which is what a 3.2.0 caller
    who cannot pass one is already writing into."""
    signature = re.search(rf"ClassMethod {method}\((.*?)\) As ", source, re.DOTALL)
    assert signature, method
    assert re.search(r'graph As %String = ""', signature.group(1)), (
        f"{method} takes no graph: {signature.group(1)}"
    )


def test_bulk_enqueue_passes_the_graph_through(source):
    """`BulkEnqueue` delegates to `Enqueue`; a graph it accepts and drops is worse
    than one it never accepted, because the signature says it was handled."""
    body = _method(source, "BulkEnqueue")
    # The text argument in between is not this test's business — BulkEnqueue has no
    # text to pass, so whatever placeholder it uses is fine. What must be there is the
    # graph in the last position.
    assert re.search(r"\.\.Enqueue\(nodeId,\s*embeddingConfig,[^)]*,\s*graph\s*\)", body), (
        f"BulkEnqueue does not thread the graph into Enqueue: {body}"
    )


def test_enqueue_refuses_to_overwrite_another_graphs_pending_request(source):
    """reqId is the bare node id, so one node has one pending entry across every
    graph. Under spec 227 two graphs can both want a vector for `patient:1`; the
    second enqueue would overwrite the first with no trace. Refused instead, naming
    both graphs — a shortfall in BulkEnqueue's count is visible, a lost request is
    not. The reqId is deliberately not graph-qualified: a graph name may contain any
    character a subscript may, so no delimiter would be unambiguous."""
    body = _method(source, "Enqueue")
    assert re.search(r'\$Get\(\^EmbedQueue\(nodeId,\s*"graph"\)\)', body), (
        f"Enqueue never looks at the graph of the entry it is about to replace: {body}"
    )
    assert "$$$ERROR" in body, (
        f"Enqueue overwrites another graph's pending request silently: {body}"
    )


# --- every read path returns it ---------------------------------------------------


@pytest.mark.parametrize("method", ["ClaimPendingBatch", "GetEntry"])
def test_the_read_paths_return_the_graph(source, method):
    body = _method(source, method)
    assert re.search(r'Set obj\.graph = \$Get\(\^EmbedQueue\(reqId,\s*"graph"\)\)', body), (
        f"{method} does not return the graph, so the worker cannot know where to "
        f"write: {body}"
    )


# --- the write target ------------------------------------------------------------


def test_process_batch_writes_the_graph_on_the_row(source):
    body = _method(source, "ProcessBatch")
    assert "kg_NodeEmbeddings" in body
    assert re.search(r"\(\s*graph_id\s*,\s*node_id\s*,\s*emb\s*\)", body), (
        f"the INSERT does not name graph_id and node_id: {body}"
    )
    assert not re.search(r"\(\s*id\s*,\s*emb\s*\)", body), (
        "the INSERT still names the removed `id` column"
    )


def test_process_batch_checks_the_node_is_in_that_graph(source):
    """FR-006: a pre-upgrade entry has no graph subscript, so it defaults to the
    default graph. If its node is not there, the entry must fail with a stated
    reason rather than writing a row whose FK has nothing to point at."""
    body = _method(source, "ProcessBatch")
    assert "Graph_KG.nodes" in body, (
        f"ProcessBatch never checks that the node exists in the graph it is about "
        f"to write: {body}"
    )
    assert re.search(r'Set \^EmbedQueue\(reqId,\s*"error"\)', body), body
    assert "graph" in body.lower()


def test_the_error_message_names_the_graph(source):
    """"node not found" sends the reader looking for a missing node. The node is
    usually there — in another graph."""
    body = _method(source, "ProcessBatch")
    assert re.search(r'"[^"]*graph[^"]*"\s*_', body) or re.search(
        r'_\s*graph\s*_', body
    ), f"the failure does not say which graph was searched: {body}"
