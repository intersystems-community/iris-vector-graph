-- Migration: 000_base_schema_iris.sql
-- Purpose: Create base schema tables with IRIS-compatible syntax
-- Date: 2025-10-01
-- Dependencies: None (foundational schema)
-- Note: This is IRIS-specific version of schema.sql for migration testing

-- RDF-ish canonical tables

CREATE TABLE rdf_labels(
  s      VARCHAR(256) NOT NULL,
  label  VARCHAR(128) NOT NULL
);
CREATE INDEX idx_labels_label_s ON rdf_labels(label, s);
CREATE INDEX idx_labels_s_label ON rdf_labels(s, label);

CREATE TABLE rdf_props(
  s      VARCHAR(256) NOT NULL,
  key    VARCHAR(128) NOT NULL,
  val    VARCHAR(4000)
);
CREATE INDEX idx_props_s_key ON rdf_props(s, key);
CREATE INDEX idx_props_key_val ON rdf_props(key, val);

CREATE TABLE rdf_edges(
  edge_id  BIGINT PRIMARY KEY,
  s        VARCHAR(256) NOT NULL,
  p        VARCHAR(128) NOT NULL,
  o_id     VARCHAR(256) NOT NULL,
  qualifiers VARCHAR(4000)  -- IRIS doesn't support JSON in all versions, use VARCHAR
);
CREATE INDEX idx_edges_s_p ON rdf_edges(s, p);
CREATE INDEX idx_edges_p_oid ON rdf_edges(p, o_id);
CREATE INDEX idx_edges_s ON rdf_edges(s);
CREATE INDEX idx_edges_oid ON rdf_edges(o_id);  -- For bidirectional PageRank reverse edge lookups

-- Vector embeddings for nodes (768-dimensional)
CREATE TABLE kg_NodeEmbeddings(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(768) NOT NULL
);

-- HNSW index
CREATE INDEX HNSW_NodeEmb ON kg_NodeEmbeddings(emb)
  AS HNSW(M=16, efConstruction=100, Distance='Cosine');

-- Text docs for lexical search
CREATE TABLE docs(
  id    VARCHAR(256) PRIMARY KEY,
  text  VARCHAR(4000)
);

-- Text index for full-text search
CREATE INDEX idx_docs_text_find ON docs(text)
  TYPE BITMAP
  WITH PARAMETERS('type=word,language=en,stemmer=1,stopwords=1');
