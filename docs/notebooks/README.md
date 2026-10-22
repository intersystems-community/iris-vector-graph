<!-- markdownlint-disable MD013 -->

# Demos and walkthroughs

Every demo connects to an IRIS container. Use `ivg-iris-enterprise` (SQL port 31972):
`scripts/enterprise-container.sh up`. Run them with the project's interpreter, `.venv/bin/python`. In VS Code, select
it as the notebook's kernel (Select Another Kernel → Python Environments → Enter interpreter path →
`.venv/bin/python`). Or run `.venv/bin/jupyter lab docs/notebooks/`.

## Start here

| Demo                                             | What it shows                                                                                                                                                                                                                                                                                                       | Data                                                                                            | Run                                                                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| [`ivg_walkthrough.ipynb`](ivg_walkthrough.ipynb) | What a graph database is and how IVG builds one on IRIS: an opsreview-shaped graph, Cypher next to the SQL it needs, the four `Graph_KG` tables and the `^KG` adjacency global, the native executor and the SQL path, temporal edges (window reads, buckets, retention), the ledger, vectors, and a map of the code | Built by the notebook (a few hundred nodes), in its own `IVGDEMO` namespace, reset on every run | Run All, about 40 s. Rebuild the file with `.venv/bin/python docs/notebooks/build_ivg_walkthrough.py --execute` |

## Notebooks

| Demo                                                                     | What it shows                                                                                                                                                         | Data                                                                    |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| [`biomed_drkg_showcase.ipynb`](biomed_drkg_showcase.ipynb)               | Graph analytics on the Drug Repurposing Knowledge Graph: centrality, communities, hybrid vector and graph retrieval                                                   | DRKG: 97,238 nodes, 5.9 M edges (downloaded and loaded by the notebook) |
| [`graphrag_temporal_prov_demo.ipynb`](graphrag_temporal_prov_demo.ipynb) | GraphRAG over a small clinical graph: Leiden communities, summaries from Claude written back as temporal edges, RDF export, SHACL validation and a PROV-O audit trail | Built by the notebook; needs `[rdf]` extras and `ANTHROPIC_API_KEY`     |

## Scripts (`examples/`)

| Demo                           | What it shows                                                                                                               | Run                                                                                             |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `demo_fraud_detection.py`      | Fraud patterns: money-laundering rings, mule accounts (high-degree nodes), anomalies by vector similarity, an alert summary | `.venv/bin/python examples/demo_fraud_detection.py`                                             |
| `demo_fraud_detection_sql.py`  | The same fraud network queried in SQL, for BI tools and joins beyond Cypher                                                 | `.venv/bin/python examples/demo_fraud_detection_sql.py`                                         |
| `demo_biomedical.py`           | Protein-interaction traversal, vector similarity and hybrid vector plus text search                                         | `.venv/bin/python examples/demo_biomedical.py`                                                  |
| `demo_per_graph_embeddings.py` | Two named graphs with the same node id, each with its own embedding model and width                                         | `IVG_TEST_CONTAINER=ivg-iris-enterprise .venv/bin/python examples/demo_per_graph_embeddings.py` |
| `demo_rdf_semantic_layer.py`   | RDF export, SHACL validation, PROV-O provenance from temporal edges                                                         | `.venv/bin/python examples/demo_rdf_semantic_layer.py` (needs `[rdf]` extras)                   |
| `demo_working_system.py`       | Vector search, text search, reciprocal rank fusion and traversal, one by one                                                | `.venv/bin/python examples/demo_working_system.py`                                              |

The fraud and biomedical scripts expect their datasets to be loaded already (each script's first steps check for
them). Their status on 2026-10-22: not re-run since the 4.x changes; run one before showing it.
