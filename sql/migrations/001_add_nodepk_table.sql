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

-- NOTE: Foreign key constraints are added in migration 002_add_fk_constraints.sql
--       This allows nodes to be populated before constraints are enforced