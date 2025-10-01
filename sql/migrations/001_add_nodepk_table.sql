-- Migration: 001_add_nodepk_table.sql
-- Purpose: Create explicit nodes table with primary key constraint for referential integrity
-- Date: 2025-10-01
-- Feature: NodePK (Explicit Node Identity)
-- Dependencies: None (foundational migration)

-- Create nodes table
-- This table serves as the central registry of all node identifiers in the graph
-- All other tables (rdf_edges, rdf_labels, rdf_props, kg_NodeEmbeddings) will reference this table
CREATE TABLE IF NOT EXISTS nodes(
  node_id VARCHAR(256) PRIMARY KEY NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Performance note: PRIMARY KEY automatically creates B-tree index on node_id
-- Expected lookup performance: <1ms for single node lookup even at 1M+ nodes scale

-- Add foreign key constraints (T016-T020)
-- These enforce referential integrity: edges, labels, props, and embeddings must reference valid nodes

-- T016: FK constraint for rdf_edges source node
ALTER TABLE rdf_edges ADD CONSTRAINT fk_edges_source
  FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT;

-- T017: FK constraint for rdf_edges destination node
ALTER TABLE rdf_edges ADD CONSTRAINT fk_edges_dest
  FOREIGN KEY (o_id) REFERENCES nodes(node_id) ON DELETE RESTRICT;

-- T018: FK constraint for rdf_labels
ALTER TABLE rdf_labels ADD CONSTRAINT fk_labels_node
  FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT;

-- T019: FK constraint for rdf_props
ALTER TABLE rdf_props ADD CONSTRAINT fk_props_node
  FOREIGN KEY (s) REFERENCES nodes(node_id) ON DELETE RESTRICT;

-- T020: FK constraint for kg_NodeEmbeddings
ALTER TABLE kg_NodeEmbeddings ADD CONSTRAINT fk_embeddings_node
  FOREIGN KEY (id) REFERENCES nodes(node_id) ON DELETE RESTRICT;