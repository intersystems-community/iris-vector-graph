"""Seed graph for the biomedical demo: named graph `bio:demo`.

Thirty-odd human proteins, keyed by HGNC gene symbol, in three neighbourhoods the
page's scenarios walk: the p53 network, the EGFR/RAS/MAPK kinase cascade with its
approved inhibitors, and the glycolysis chain from GAPDH to LDHA. The interactions
are well-known ones; the confidence values are illustrative, not STRING scores.

    PYTHONPATH=src:. python -m iris_demo_server.services.bio_demo_data
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Tuple

GRAPH = "bio:demo"
ORGANISM = "Homo sapiens"
PREDICATE = "interacts_with"
INTERACTION_TYPES = {"binding", "phosphorylation", "activation", "inhibition", "regulation", "pathway"}

PROTEINS: List[Dict[str, str]] = [
    # p53 network
    {"symbol": "TP53", "name": "Cellular tumor antigen p53",
     "annotation": "Tumor suppressor transcription factor; induces cell cycle arrest, DNA repair or apoptosis after DNA damage."},
    {"symbol": "MDM2", "name": "E3 ubiquitin-protein ligase Mdm2",
     "annotation": "Ubiquitin ligase that targets p53 for proteasomal degradation; negative regulator of TP53."},
    {"symbol": "MDM4", "name": "Protein Mdm4",
     "annotation": "Binds the p53 transactivation domain and inhibits it; partner of MDM2."},
    {"symbol": "CDKN1A", "name": "Cyclin-dependent kinase inhibitor 1 (p21)",
     "annotation": "p53 target gene; cyclin-dependent kinase inhibitor that blocks CDK2 and causes G1 arrest."},
    {"symbol": "ATM", "name": "Serine-protein kinase ATM",
     "annotation": "Protein kinase activated by DNA double-strand breaks; phosphorylates TP53 and CHEK2."},
    {"symbol": "CHEK2", "name": "Serine/threonine-protein kinase Chk2",
     "annotation": "Checkpoint protein kinase that phosphorylates TP53 after DNA damage."},
    {"symbol": "BAX", "name": "Apoptosis regulator BAX",
     "annotation": "Pro-apoptotic BCL2 family member induced by p53."},
    {"symbol": "BCL2", "name": "Apoptosis regulator Bcl-2",
     "annotation": "Anti-apoptotic protein; target of the BH3-mimetic inhibitor venetoclax."},
    {"symbol": "EP300", "name": "Histone acetyltransferase p300",
     "annotation": "Transcriptional co-activator that acetylates p53."},
    {"symbol": "BRCA1", "name": "Breast cancer type 1 susceptibility protein",
     "annotation": "E3 ubiquitin ligase in homologous-recombination DNA repair."},
    # receptor tyrosine kinases and the RAS/MAPK cascade
    {"symbol": "EGFR", "name": "Epidermal growth factor receptor",
     "annotation": "Receptor tyrosine kinase; target of the kinase inhibitors gefitinib and erlotinib."},
    {"symbol": "ERBB2", "name": "Receptor tyrosine-protein kinase erbB-2 (HER2)",
     "annotation": "Receptor tyrosine kinase; target of the kinase inhibitor lapatinib and of trastuzumab."},
    {"symbol": "KIT", "name": "Mast/stem cell growth factor receptor Kit",
     "annotation": "Receptor tyrosine kinase; target of the kinase inhibitor imatinib in GIST."},
    {"symbol": "ABL1", "name": "Tyrosine-protein kinase ABL1",
     "annotation": "Non-receptor tyrosine kinase; the BCR-ABL1 fusion is the target of the kinase inhibitor imatinib."},
    {"symbol": "BCR", "name": "Breakpoint cluster region protein",
     "annotation": "Fusion partner of ABL1 in chronic myeloid leukaemia."},
    {"symbol": "GRB2", "name": "Growth factor receptor-bound protein 2",
     "annotation": "Adaptor linking activated receptor tyrosine kinases to SOS1 and RAS."},
    {"symbol": "SOS1", "name": "Son of sevenless homolog 1",
     "annotation": "Guanine nucleotide exchange factor that activates RAS."},
    {"symbol": "KRAS", "name": "GTPase KRas",
     "annotation": "Small GTPase upstream of RAF; the G12C mutant is targeted by sotorasib."},
    {"symbol": "BRAF", "name": "Serine/threonine-protein kinase B-raf",
     "annotation": "MAPK pathway kinase; the V600E mutant is the target of the kinase inhibitor vemurafenib."},
    {"symbol": "MAP2K1", "name": "Dual specificity mitogen-activated protein kinase kinase 1 (MEK1)",
     "annotation": "Kinase that phosphorylates ERK; target of the kinase inhibitor trametinib."},
    {"symbol": "MAPK1", "name": "Mitogen-activated protein kinase 1 (ERK2)",
     "annotation": "Kinase at the end of the RAS-RAF-MEK-ERK cascade."},
    {"symbol": "JAK2", "name": "Tyrosine-protein kinase JAK2",
     "annotation": "Cytokine-receptor kinase; target of the kinase inhibitor ruxolitinib."},
    {"symbol": "STAT3", "name": "Signal transducer and activator of transcription 3",
     "annotation": "Transcription factor phosphorylated by JAK2 and EGFR."},
    {"symbol": "PIK3CA", "name": "PI3-kinase subunit alpha (p110 alpha)",
     "annotation": "Lipid kinase; target of the kinase inhibitor alpelisib."},
    {"symbol": "AKT1", "name": "RAC-alpha serine/threonine-protein kinase",
     "annotation": "Kinase downstream of PI3K; phosphorylates MDM2 and promotes HK2 activity."},
    # glycolysis and its hypoxia switch
    {"symbol": "HIF1A", "name": "Hypoxia-inducible factor 1-alpha",
     "annotation": "Transcription factor that induces glycolytic genes such as HK2, PKM and LDHA under hypoxia."},
    {"symbol": "HK2", "name": "Hexokinase-2",
     "annotation": "Phosphorylates glucose, the first step of glycolysis."},
    {"symbol": "GAPDH", "name": "Glyceraldehyde-3-phosphate dehydrogenase",
     "annotation": "Glycolytic enzyme converting glyceraldehyde 3-phosphate to 1,3-bisphosphoglycerate."},
    {"symbol": "PGK1", "name": "Phosphoglycerate kinase 1",
     "annotation": "Glycolytic enzyme that makes ATP from 1,3-bisphosphoglycerate."},
    {"symbol": "PGAM1", "name": "Phosphoglycerate mutase 1",
     "annotation": "Glycolytic enzyme converting 3-phosphoglycerate to 2-phosphoglycerate."},
    {"symbol": "ENO1", "name": "Alpha-enolase",
     "annotation": "Glycolytic enzyme converting 2-phosphoglycerate to phosphoenolpyruvate."},
    {"symbol": "PKM", "name": "Pyruvate kinase PKM",
     "annotation": "Final glycolytic step, phosphoenolpyruvate to pyruvate."},
    {"symbol": "LDHA", "name": "L-lactate dehydrogenase A chain",
     "annotation": "Converts pyruvate to lactate; high in hypoxic tumours."},
]

#: (a, b, type, illustrative confidence). Undirected for search and paths.
INTERACTIONS: List[Tuple[str, str, str, float]] = [
    ("TP53", "MDM2", "inhibition", 0.99),
    ("TP53", "MDM4", "inhibition", 0.95),
    ("MDM2", "MDM4", "binding", 0.97),
    ("ATM", "TP53", "phosphorylation", 0.96),
    ("ATM", "CHEK2", "phosphorylation", 0.97),
    ("CHEK2", "TP53", "phosphorylation", 0.94),
    ("TP53", "CDKN1A", "activation", 0.98),
    ("TP53", "BAX", "activation", 0.93),
    ("BAX", "BCL2", "binding", 0.95),
    ("EP300", "TP53", "binding", 0.95),
    ("BRCA1", "TP53", "binding", 0.85),
    ("ATM", "BRCA1", "phosphorylation", 0.92),
    ("AKT1", "MDM2", "phosphorylation", 0.90),
    ("PIK3CA", "AKT1", "activation", 0.95),
    ("EGFR", "GRB2", "binding", 0.99),
    ("GRB2", "SOS1", "binding", 0.99),
    ("SOS1", "KRAS", "activation", 0.98),
    ("KRAS", "BRAF", "activation", 0.97),
    ("BRAF", "MAP2K1", "phosphorylation", 0.99),
    ("MAP2K1", "MAPK1", "phosphorylation", 0.99),
    ("EGFR", "ERBB2", "binding", 0.97),
    ("KRAS", "PIK3CA", "activation", 0.90),
    ("EGFR", "STAT3", "phosphorylation", 0.85),
    ("JAK2", "STAT3", "phosphorylation", 0.98),
    ("BCR", "ABL1", "binding", 0.99),
    ("ABL1", "GRB2", "binding", 0.80),
    ("KIT", "GRB2", "binding", 0.80),
    ("MAPK1", "TP53", "phosphorylation", 0.70),
    ("GAPDH", "PGK1", "pathway", 0.90),
    ("PGK1", "PGAM1", "pathway", 0.90),
    ("PGAM1", "ENO1", "pathway", 0.90),
    ("ENO1", "PKM", "pathway", 0.90),
    ("PKM", "LDHA", "pathway", 0.90),
    ("HIF1A", "LDHA", "regulation", 0.90),
    ("HIF1A", "PKM", "regulation", 0.85),
    ("HIF1A", "HK2", "regulation", 0.88),
    ("AKT1", "HK2", "phosphorylation", 0.75),
    ("TP53", "HIF1A", "binding", 0.80),
]

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "cancer_protein": {"query_text": "TP53", "query_type": "name", "top_k": 10},
    "metabolic_pathway": {"source_protein_id": "GAPDH", "target_protein_id": "LDHA", "max_hops": 5},
    "drug_target": {"query_text": "kinase inhibitor", "query_type": "function", "top_k": 15},
}


def node_id(symbol: str) -> str:
    return symbol if symbol.startswith("protein:") else f"protein:{symbol}"


def symbol(nid: str) -> str:
    return nid.split(":", 1)[1] if nid.startswith("protein:") else nid


def build_graph() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    nodes = [
        {"id": node_id(p["symbol"]), "labels": ["Protein"],
         "properties": {"symbol": p["symbol"], "name": p["name"], "annotation": p["annotation"],
                        "organism": ORGANISM}}
        for p in PROTEINS
    ]
    edges = [
        {"source": node_id(a), "predicate": PREDICATE, "target": node_id(b),
         "qualifiers": {"type": kind, "confidence": conf}}
        for a, b, kind, conf in INTERACTIONS
    ]
    return nodes, edges


# ------------------------------------------------------------------- connection

def connection_settings() -> Dict[str, Any]:
    """`IVG_BIO_*`, then the FHIR demo's `IVG_FHIR_*`, then this project's container.
    Both demos default to IVGFHIR on ivg-iris-enterprise (31972): IVG is deployed there,
    and the unit suite wipes USER."""
    def pick(name: str, default: str) -> str:
        return os.getenv(f"IVG_BIO_{name}") or os.getenv(f"IVG_FHIR_{name}") or default

    return {
        "hostname": pick("HOST", "localhost"),
        "port": int(pick("PORT", "31972")),
        "namespace": pick("NAMESPACE", "IVGFHIR"),
        "username": pick("USER", "_SYSTEM"),
        "password": pick("PASSWORD", "SYS"),
    }


def connect():
    import iris

    return iris.connect(**connection_settings())


def make_engine(conn):
    from iris_vector_graph.engine import IRISGraphEngine

    return IRISGraphEngine(conn, namespace=connection_settings()["namespace"])


def counts(conn) -> Tuple[int, int]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE graph_id = ?", (GRAPH,))
        n = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE graph_id = ?", (GRAPH,))
        e = cur.fetchone()[0]
    finally:
        cur.close()
    return int(n), int(e)


def seed(conn, engine, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Load `bio:demo`. A graph already holding exactly the seed is left alone; anything
    else in that graph is erased first, so a rerun converges."""
    nodes, edges = build_graph()
    if counts(conn) == (len(nodes), len(edges)):
        log(f"{GRAPH}: already seeded")
        return {"graph": GRAPH, "proteins": len(nodes), "interactions": len(edges), "seeded": False}
    engine.erase_graph(GRAPH)
    for n in nodes:
        engine.create_node(n["id"], labels=n["labels"], properties=n["properties"], graph=GRAPH)
    for e in edges:
        engine.create_edge(e["source"], e["predicate"], e["target"], weight=1.0,
                           qualifiers=e["qualifiers"], graph=GRAPH)
    got = counts(conn)
    if got != (len(nodes), len(edges)):
        raise RuntimeError(f"{GRAPH}: seeded {got}, expected {(len(nodes), len(edges))}")
    log(f"{GRAPH}: {len(nodes)} proteins, {len(edges)} interactions")
    return {"graph": GRAPH, "proteins": len(nodes), "interactions": len(edges), "seeded": True}


if __name__ == "__main__":
    c = connect()
    seed(c, make_engine(c))
