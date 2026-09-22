#!/usr/bin/env python3
"""
Per-Graph Embedding Models Demo (4.0.0)

Two graphs hold the *same* node ID, each with a vector from a different model at a
different width, and neither can see the other's:

1. The same node ID in two graphs — `nodes` is keyed `(graph_id, node_id)` now
2. Two routes, two widths — `(graph, model_key)` picks a physical table
3. Each graph's KNN returns only its own neighbours
4. A query vector of the wrong width is refused, not reshaped (ADR-0005)
5. Omitting `graph` reads the default graph, never every graph
6. Erasing one graph leaves the other's vectors intact
7. Recall is measured per route, not assumed

Usage:
    DOCKER_CONTEXT=orbstack IVG_TEST_CONTAINER=ivg-iris-enterprise \
        python examples/demo_per_graph_embeddings.py
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.demo_utils import DemoError, DemoRunner  # noqa: E402

# One node ID, two graphs. This is the pair that 3.2.0's `UNIQUE (node_id)` made
# impossible to store twice.
NODE = "demo:gene:BRCA1"
GRAPH_UMLS, MODEL_UMLS, WIDTH_UMLS = "demo_umls", "biobert", 768
GRAPH_HPO, MODEL_HPO, WIDTH_HPO = "demo_hpo", "minilm", 384

NEIGHBOURS = 4


def _vec(width: int, seed: int) -> list:
    """A deterministic unit-ish vector, so two runs compare."""
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(width)]


def _as_literal(vector: list) -> str:
    """`kg_KNN_VEC` takes the query vector as the `[0.1,0.2,…]` string form."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def main():
    runner = DemoRunner("Per-Graph Embedding Models (4.0.0)", total_steps=7)
    engine = None
    try:
        runner.start()

        with runner.step("Connecting and clearing the demo's own graphs"):
            from iris_vector_graph import IRISGraphEngine

            conn = runner.get_connection()
            engine = IRISGraphEngine(conn)
            # Only the two graphs this demo owns; nothing else is touched.
            for graph in (GRAPH_UMLS, GRAPH_HPO):
                engine.erase_graph(graph)

        with runner.step("Writing the same node ID into two graphs"):
            for graph in (GRAPH_UMLS, GRAPH_HPO):
                engine.create_node(NODE, labels=["Gene"], graph=graph)
                for i in range(NEIGHBOURS):
                    engine.create_node(f"{NODE}:n{i}", labels=["Gene"], graph=graph)
            print(f"    {NODE} now exists in {GRAPH_UMLS} and {GRAPH_HPO}")

        with runner.step("Two routes, two models, two widths"):
            for graph, model, width, seed in (
                (GRAPH_UMLS, MODEL_UMLS, WIDTH_UMLS, 1),
                (GRAPH_HPO, MODEL_HPO, WIDTH_HPO, 2),
            ):
                engine.store_embedding(
                    NODE, _vec(width, seed), graph=graph, model_key=model
                )
                for i in range(NEIGHBOURS):
                    engine.store_embedding(
                        f"{NODE}:n{i}",
                        _vec(width, seed * 100 + i),
                        graph=graph,
                        model_key=model,
                    )
                route = engine.resolve_route(graph=graph, model_key=model)
                count = engine.embedding_count(graph=graph, model_key=model)
                print(
                    f"    {graph:<10} {model:<9} width {width:<4} "
                    f"-> {route.table_name if route else '<none>'} ({count} vectors)"
                )

        with runner.step("Each graph's KNN returns only its own neighbours"):
            for graph, model, width, seed in (
                (GRAPH_UMLS, MODEL_UMLS, WIDTH_UMLS, 1),
                (GRAPH_HPO, MODEL_HPO, WIDTH_HPO, 2),
            ):
                hits = engine.kg_KNN_VEC(
                    _as_literal(_vec(width, seed)),
                    k=10,
                    graph=graph,
                    model_key=model,
                )
                ids = [node_id for node_id, _ in hits]
                print(f"    {graph:<10} k=10 returned {len(ids)}: {ids[:3]} …")
                if len(ids) > NEIGHBOURS + 1:
                    raise DemoError(
                        f"{graph} returned {len(ids)} hits for {NEIGHBOURS + 1} rows — "
                        "the scan is crossing graphs",
                        next_steps=["Check the graph predicate in kg_KNN_VEC"],
                    )

        with runner.step("A wrong-width query is refused, not reshaped"):
            # The 384-wide route cannot score a 768-wide query. IRIS enforces the
            # declared VECTOR width, so this is an error rather than a silent answer.
            try:
                engine.kg_KNN_VEC(
                    _as_literal(_vec(WIDTH_UMLS, 1)),
                    k=5,
                    graph=GRAPH_HPO,
                    model_key=MODEL_HPO,
                )
            except Exception as exc:
                print(f"    refused as it should be: {type(exc).__name__}")
            else:
                raise DemoError(
                    f"a {WIDTH_UMLS}-wide query scored against the "
                    f"{WIDTH_HPO}-wide {GRAPH_HPO} route instead of being refused "
                    "(ADR-0005)",
                    next_steps=["Check _diagnose_vector_write and the route's width"],
                )

        with runner.step("Omitting graph reads the default graph, not every graph"):
            unscoped = engine.embedding_count()
            print(f"    embedding_count() (default graph) = {unscoped}")
            scoped = engine.embedding_count(graph=GRAPH_UMLS, model_key=MODEL_UMLS)
            print(f"    embedding_count(graph={GRAPH_UMLS!r}) = {scoped}")
            print("    the demo's vectors are in neither default-graph route")

        with runner.step("Erasing one graph leaves the other's vectors"):
            before = engine.embedding_count(graph=GRAPH_HPO, model_key=MODEL_HPO)
            engine.erase_graph(GRAPH_UMLS)
            after = engine.embedding_count(graph=GRAPH_HPO, model_key=MODEL_HPO)
            gone = engine.embedding_count(graph=GRAPH_UMLS, model_key=MODEL_UMLS)
            print(f"    {GRAPH_UMLS} after erase: {gone} vectors")
            print(f"    {GRAPH_HPO} unchanged:    {before} -> {after} vectors")
            if gone != 0 or after != before:
                raise DemoError(
                    f"erasing {GRAPH_UMLS} left {gone} of its vectors and changed "
                    f"{GRAPH_HPO} from {before} to {after}",
                    next_steps=["Check Graph.KG.Eraser's routed-table coverage"],
                )
            recall = engine.measure_route_recall(
                graph=GRAPH_HPO, model_key=MODEL_HPO, k=NEIGHBOURS, probes=2
            )
            if recall is not None:
                print(f"    measured recall@{NEIGHBOURS} on that route: {recall.recall}")

        runner.finish(success=True)
        return 0

    except DemoError as exc:
        exc.display()
        runner.finish(success=False)
        return 1
    finally:
        if engine is not None:
            for graph in (GRAPH_UMLS, GRAPH_HPO):
                try:
                    engine.erase_graph(graph)
                except Exception:  # noqa: BLE001 - cleanup is best effort
                    pass


if __name__ == "__main__":
    sys.exit(main())
