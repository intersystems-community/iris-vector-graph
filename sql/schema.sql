-- schema.sql — base objects
-- Materialized tables for RDF-ish data

CREATE TABLE IF NOT EXISTS rdf_labels(
  s      VARCHAR(256) NOT NULL,
  label  VARCHAR(128) NOT NULL
);
CREATE INDEX idx_labels_label_s ON rdf_labels(label, s);
CREATE INDEX idx_labels_s_label ON rdf_labels(s, label);

CREATE TABLE IF NOT EXISTS rdf_props(
  s      VARCHAR(256) NOT NULL,
  key    VARCHAR(128) NOT NULL,
  val    VARCHAR(4000)
);
CREATE INDEX idx_props_s_key ON rdf_props(s, key);
CREATE INDEX idx_props_key_val ON rdf_props(key, val);

CREATE TABLE IF NOT EXISTS rdf_edges(
  edge_id  BIGINT IDENTITY PRIMARY KEY,
  s        VARCHAR(256) NOT NULL,
  p        VARCHAR(128) NOT NULL,
  o_id     VARCHAR(256) NOT NULL,
  qualifiers JSON
);
CREATE INDEX idx_edges_s_p ON rdf_edges(s, p);
CREATE INDEX idx_edges_p_oid ON rdf_edges(p, o_id);
CREATE INDEX idx_edges_s ON rdf_edges(s);
CREATE INDEX idx_edges_oid ON rdf_edges(o_id);

CREATE TABLE IF NOT EXISTS kg_NodeEmbeddings(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(FLOAT, 768) NOT NULL
);

CREATE INDEX HNSW_NodeEmb ON kg_NodeEmbeddings(emb)
  AS HNSW(M=16, efConstruction=100, Distance='Cosine');

CREATE TABLE IF NOT EXISTS docs(
  id    VARCHAR(256) PRIMARY KEY,
  text  VARCHAR(4000)
);

CREATE INDEX idx_docs_text_find ON docs(text)
  TYPE BITMAP
  WITH PARAMETERS('type=word,language=en,stemmer=1,stopwords=1');

CREATE TABLE IF NOT EXISTS nodes (
    node_id VARCHAR(256) PRIMARY KEY NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
