#!/usr/bin/env python3
"""
Biomedical Schema Extensions

Domain-specific schema extensions for biomedical knowledge graphs.
Builds on the base RDF schema with biomedical entity types and relationships.
"""

from typing import Dict, List, Optional

from iris_vector_graph.schema import GraphSchema


class BiomedicalSchema(GraphSchema):
    """Biomedical-specific schema management"""

    @staticmethod
    def get_biomedical_schema_sql() -> str:
        """
        Returns biomedical-specific schema extensions

        Returns:
            SQL string for creating biomedical-specific tables
        """
        base_schema = GraphSchema.get_base_schema_sql()

        biomedical_extensions = """
-- Biomedical-specific extensions

-- Protein information
CREATE TABLE IF NOT EXISTS biomedical_proteins(
  protein_id VARCHAR(256) PRIMARY KEY,
  uniprot_id VARCHAR(50),
  gene_name VARCHAR(100),
  protein_name VARCHAR(500),
  organism VARCHAR(100),
  sequence_length INTEGER,
  molecular_weight FLOAT
);
CREATE INDEX IF NOT EXISTS idx_proteins_uniprot ON biomedical_proteins(uniprot_id);
CREATE INDEX IF NOT EXISTS idx_proteins_gene ON biomedical_proteins(gene_name);

-- Gene information
CREATE TABLE IF NOT EXISTS biomedical_genes(
  gene_id VARCHAR(256) PRIMARY KEY,
  gene_symbol VARCHAR(50),
  gene_name VARCHAR(500),
  entrez_id VARCHAR(50),
  ensembl_id VARCHAR(50),
  chromosome VARCHAR(10),
  start_position BIGINT,
  end_position BIGINT,
  strand CHAR(1)
);
CREATE INDEX IF NOT EXISTS idx_genes_symbol ON biomedical_genes(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_genes_entrez ON biomedical_genes(entrez_id);

-- Disease information
CREATE TABLE IF NOT EXISTS biomedical_diseases(
  disease_id VARCHAR(256) PRIMARY KEY,
  disease_name VARCHAR(500),
  mesh_id VARCHAR(50),
  doid VARCHAR(50),
  icd10_code VARCHAR(20),
  disease_class VARCHAR(100),
  description VARCHAR(4000)
);
CREATE INDEX IF NOT EXISTS idx_diseases_mesh ON biomedical_diseases(mesh_id);
CREATE INDEX IF NOT EXISTS idx_diseases_name ON biomedical_diseases(disease_name);

-- Drug/Compound information
CREATE TABLE IF NOT EXISTS biomedical_drugs(
  drug_id VARCHAR(256) PRIMARY KEY,
  drug_name VARCHAR(500),
  drugbank_id VARCHAR(50),
  pubchem_cid VARCHAR(50),
  chembl_id VARCHAR(50),
  smiles VARCHAR(2000),
  molecular_formula VARCHAR(200),
  indication VARCHAR(2000)
);
CREATE INDEX IF NOT EXISTS idx_drugs_drugbank ON biomedical_drugs(drugbank_id);
CREATE INDEX IF NOT EXISTS idx_drugs_name ON biomedical_drugs(drug_name);

-- Clinical trials information
CREATE TABLE IF NOT EXISTS biomedical_trials(
  trial_id VARCHAR(256) PRIMARY KEY,
  nct_id VARCHAR(50),
  title VARCHAR(1000),
  phase VARCHAR(20),
  status VARCHAR(50),
  start_date DATE,
  completion_date DATE,
  sponsor VARCHAR(500),
  condition VARCHAR(1000)
);
CREATE INDEX IF NOT EXISTS idx_trials_nct ON biomedical_trials(nct_id);
CREATE INDEX IF NOT EXISTS idx_trials_phase ON biomedical_trials(phase);

-- Publication information
CREATE TABLE IF NOT EXISTS biomedical_publications(
  publication_id VARCHAR(256) PRIMARY KEY,
  pmid VARCHAR(50),
  doi VARCHAR(200),
  title VARCHAR(2000),
  authors VARCHAR(4000),
  journal VARCHAR(500),
  publication_date DATE,
  abstract VARCHAR(4000)
);
CREATE INDEX IF NOT EXISTS idx_pubs_pmid ON biomedical_publications(pmid);
CREATE INDEX IF NOT EXISTS idx_pubs_journal ON biomedical_publications(journal);

-- Evidence and confidence tracking
CREATE TABLE IF NOT EXISTS biomedical_evidence(
  evidence_id VARCHAR(256) PRIMARY KEY,
  edge_id BIGINT,
  evidence_type VARCHAR(100), -- 'experimental', 'computational', 'literature'
  confidence_score FLOAT,
  source_db VARCHAR(100),
  source_id VARCHAR(256),
  publication_support VARCHAR(4000), -- JSON array of PMIDs
  creation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (edge_id) REFERENCES rdf_edges(edge_id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_edge ON biomedical_evidence(edge_id);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON biomedical_evidence(evidence_type);
"""

        return base_schema + biomedical_extensions

    @staticmethod
    def get_biomedical_predicates() -> List[str]:
        """
        Returns list of biomedical-specific predicates/relationships

        Returns:
            List of predicate strings
        """
        return [
            # Protein relationships
            "interacts_with",
            "binds_to",
            "phosphorylates",
            "ubiquitinates",
            "regulates",
            "inhibits",
            "activates",
            # Gene relationships
            "encodes",
            "co_expressed_with",
            "co_regulated_with",
            "orthologous_to",
            "paralogous_to",
            # Disease relationships
            "associates_with",
            "causes",
            "risk_factor_for",
            "comorbid_with",
            "phenotype_of",
            # Drug relationships
            "treats",
            "targets",
            "contraindicated_for",
            "side_effect_of",
            "metabolized_by",
            "transported_by",
            # Pathway relationships
            "part_of_pathway",
            "upstream_of",
            "downstream_of",
            "catalyzes",
            # Anatomical relationships
            "expressed_in",
            "located_in",
            "part_of",
            "develops_from",
            # Functional relationships
            "has_function",
            "has_role",
            "participates_in",
            "involved_in",
        ]

    @staticmethod
    def get_biomedical_entity_types() -> List[str]:
        """
        Returns list of biomedical entity types

        Returns:
            List of entity type strings
        """
        return [
            # Molecular entities
            "protein",
            "gene",
            "rna",
            "dna",
            "transcript",
            "variant",
            "mutation",
            # Chemical entities
            "drug",
            "compound",
            "metabolite",
            "lipid",
            "carbohydrate",
            # Biological processes
            "pathway",
            "biological_process",
            "molecular_function",
            "cellular_component",
            # Diseases and phenotypes
            "disease",
            "disorder",
            "phenotype",
            "symptom",
            "syndrome",
            # Anatomical entities
            "tissue",
            "organ",
            "cell_type",
            "cell_line",
            "organism",
            "species",
            # Research entities
            "publication",
            "clinical_trial",
            "assay",
            "experiment",
            "database",
        ]

    @staticmethod
    def validate_biomedical_schema(cursor) -> Dict[str, bool]:
        """
        Validates biomedical-specific schema tables

        Args:
            cursor: Database cursor

        Returns:
            Dictionary mapping table names to existence status
        """
        # First validate base schema
        base_status = GraphSchema.validate_schema(cursor)

        # Then validate biomedical extensions
        biomedical_tables = [
            "biomedical_proteins",
            "biomedical_genes",
            "biomedical_diseases",
            "biomedical_drugs",
            "biomedical_trials",
            "biomedical_publications",
            "biomedical_evidence",
        ]

        biomedical_status = {}
        for table in biomedical_tables:
            try:
                cursor.execute(f"SELECT TOP 1 * FROM {table}")
                biomedical_status[table] = True
            except Exception:
                biomedical_status[table] = False

        # Combine base and biomedical status
        return {**base_status, **biomedical_status}

    @staticmethod
    def create_biomedical_views(cursor):
        """
        Creates useful biomedical views for common queries

        Args:
            cursor: Database cursor
        """
        views_sql = """
-- View for protein-disease associations
CREATE OR REPLACE VIEW protein_disease_associations AS
SELECT DISTINCT
    p.protein_id,
    p.protein_name,
    d.disease_id,
    d.disease_name,
    e.p as relationship_type,
    JSON_VALUE(e.qualifiers, '$.confidence') as confidence
FROM biomedical_proteins p
JOIN rdf_edges e ON e.s = p.protein_id
JOIN biomedical_diseases d ON d.disease_id = e.o_id
WHERE e.p IN ('associates_with', 'linked_to', 'causes');

-- View for drug-target interactions
CREATE OR REPLACE VIEW drug_target_interactions AS
SELECT DISTINCT
    dr.drug_id,
    dr.drug_name,
    p.protein_id,
    p.protein_name,
    e.p as interaction_type,
    JSON_VALUE(e.qualifiers, '$.confidence') as confidence
FROM biomedical_drugs dr
JOIN rdf_edges e ON e.s = dr.drug_id
JOIN biomedical_proteins p ON p.protein_id = e.o_id
WHERE e.p IN ('targets', 'binds_to', 'inhibits', 'activates');

-- View for gene-disease associations
CREATE OR REPLACE VIEW gene_disease_associations AS
SELECT DISTINCT
    g.gene_id,
    g.gene_symbol,
    d.disease_id,
    d.disease_name,
    e.p as association_type,
    JSON_VALUE(e.qualifiers, '$.confidence') as confidence
FROM biomedical_genes g
JOIN rdf_edges e ON e.s = g.gene_id
JOIN biomedical_diseases d ON d.disease_id = e.o_id
WHERE e.p IN ('associates_with', 'risk_factor_for', 'causes');
"""

        # Execute each view creation separately
        for view_sql in views_sql.split("CREATE OR REPLACE VIEW")[1:]:
            try:
                cursor.execute("CREATE OR REPLACE VIEW" + view_sql)
            except Exception as e:
                # Views might not be supported, continue
                pass
