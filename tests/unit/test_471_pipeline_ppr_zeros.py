"""4.7.1 H2: ``fhir_bridge.unified_clinical_pipeline`` keeps only the nodes PPR reached.

From 4.7.0 (row 9) ``kg_PERSONALIZED_PAGERANK`` answers every node of the graph, 0.0 for one the walk never reaches.
``fhir_concept_ppr`` and ``kg_PPR_GUIDED_SUBGRAPH`` drop those rows; the pipeline took them into ``ppr_results`` and its
provenance ``result_count``, so a patient's ranked list grew by every unrelated node of the graph. The engine here
answers what 4.7.0's does on a graph of two reached nodes and two that are not: the pipeline answers the two.
"""

import warnings
from unittest.mock import MagicMock, patch

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.fhir_bridge import unified_clinical_pipeline

CONDITIONS = {"conditions": [{"code": "E11.9", "system": "icd-10-cm", "display": "T2DM"}], "error": None}


def _run(answer):
    engine = MagicMock(spec=IRISGraphEngine)
    engine.get_kg_anchors.return_value = ["mesh:D003924"]
    engine.kg_PERSONALIZED_PAGERANK.return_value = answer
    with patch("iris_vector_graph.fhir_bridge.fhir_search_conditions", return_value=CONDITIONS), \
            warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = unified_clinical_pipeline(engine=engine, query="diabetes", fhir_base_url="http://fhir.test",
                                           patient_id="p1", ppr_top_k=4)
    engine.kg_PERSONALIZED_PAGERANK.assert_called_once_with(seed_entities=["mesh:D003924"], return_top_k=4)
    return result


def test_the_pipeline_leaves_out_nodes_the_walk_never_reached():
    result = _run({"mesh:D003924": 0.6, "mesh:D001249": 0.4, "mesh:D000001": 0.0, "mesh:D000002": 0.0})
    assert result["status"] == "ok"
    assert result["ppr_results"] == {"mesh:D003924": 0.6, "mesh:D001249": 0.4}
    assert list(result["ppr_results"]) == ["mesh:D003924", "mesh:D001249"]
    step = next(s for s in result["provenance"]["steps"] if s["step"] == "ppr")
    assert step == {"step": "ppr", "result_count": 2}


def test_an_anchor_alone_is_connectivity():
    """The seed always scores, so a reached set of one is still an answer; nothing at all is not."""
    assert _run({"mesh:D003924": 1.0, "mesh:D000001": 0.0})["ppr_results"] == {"mesh:D003924": 1.0}
    assert _run({})["status"] == "anchors_resolved_but_no_graph_connectivity"
