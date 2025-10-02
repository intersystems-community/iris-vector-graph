-- Migration: 002_add_fk_constraints.sql
-- Purpose: Add foreign key constraints to enforce referential integrity
-- Date: 2025-10-01
-- Feature: NodePK (Explicit Node Identity) - FK Constraints
-- Dependencies: 001_add_nodepk_table.sql (nodes table must exist)
-- Prerequisites: nodes table must be populated with all existing node IDs before running this

-- T016: FK constraint for rdf_edges source node
ALTER TABLE rdf_edges ADD CONSTRAINT fk_edges_source
  FOREIGN KEY (s) REFERENCES nodes(node_id);

-- T017: FK constraint for rdf_edges destination node
ALTER TABLE rdf_edges ADD CONSTRAINT fk_edges_dest
  FOREIGN KEY (o_id) REFERENCES nodes(node_id);

-- T018: FK constraint for rdf_labels
ALTER TABLE rdf_labels ADD CONSTRAINT fk_labels_node
  FOREIGN KEY (s) REFERENCES nodes(node_id);

-- T019: FK constraint for rdf_props
ALTER TABLE rdf_props ADD CONSTRAINT fk_props_node
  FOREIGN KEY (s) REFERENCES nodes(node_id);

-- T020: FK constraint for kg_NodeEmbeddings
-- NOTE: Skipped for test environment - kg_NodeEmbeddings requires VECTOR type support
-- ALTER TABLE kg_NodeEmbeddings ADD CONSTRAINT fk_embeddings_node
--   FOREIGN KEY (id) REFERENCES nodes(node_id);