-- Rollback: 001_rollback_nodepk.sql
-- Purpose: Remove nodes table and foreign key constraints added by 001_add_nodepk_table.sql
-- Date: 2025-10-01
-- Feature: NodePK (Explicit Node Identity) - ROLLBACK
-- WARNING: This will remove referential integrity enforcement!

-- Drop foreign key constraints from dependent tables
-- Note: IF EXISTS clause prevents errors if constraints don't exist yet

-- Remove FK constraints from rdf_edges (source and destination)
ALTER TABLE rdf_edges DROP CONSTRAINT IF EXISTS fk_edges_source;
ALTER TABLE rdf_edges DROP CONSTRAINT IF EXISTS fk_edges_dest;

-- Remove FK constraint from rdf_labels
ALTER TABLE rdf_labels DROP CONSTRAINT IF EXISTS fk_labels_node;

-- Remove FK constraint from rdf_props
ALTER TABLE rdf_props DROP CONSTRAINT IF EXISTS fk_props_node;

-- Remove FK constraint from kg_NodeEmbeddings
ALTER TABLE kg_NodeEmbeddings DROP CONSTRAINT IF EXISTS fk_embeddings_node;

-- Drop the nodes table
-- WARNING: This will permanently delete all node records!
-- Ensure all FK constraints are dropped first to avoid dependency errors
DROP TABLE IF EXISTS nodes;

-- Log completion
-- SELECT 'NodePK rollback complete - referential integrity constraints removed' AS status;