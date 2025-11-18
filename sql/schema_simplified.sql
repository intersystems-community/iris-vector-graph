-- Simplified schema without problematic text index for quick demo setup

CREATE TABLE IF NOT EXISTS rdf_labels(
  s      VARCHAR(256) NOT NULL,
  label  VARCHAR(128) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_labels_label_s ON rdf_labels(label, s);
CREATE INDEX IF NOT EXISTS idx_labels_s_label ON rdf_labels(s, label);

CREATE TABLE IF NOT EXISTS rdf_props(
  s      VARCHAR(256) NOT NULL,
  key    VARCHAR(128) NOT NULL,
  val    VARCHAR(4000)
);
CREATE INDEX IF NOT EXISTS idx_props_s_key ON rdf_props(s, key);
CREATE INDEX IF NOT EXISTS idx_props_key_val ON rdf_props(key, val);

CREATE TABLE IF NOT EXISTS rdf_edges(
  edge_id  BIGINT PRIMARY KEY,
  s        VARCHAR(256) NOT NULL,
  p        VARCHAR(128) NOT NULL,
  o_id     VARCHAR(256) NOT NULL,
  qualifiers VARCHAR(4000)
);
CREATE INDEX IF NOT EXISTS idx_edges_s_p ON rdf_edges(s, p);
CREATE INDEX IF NOT EXISTS idx_edges_p_oid ON rdf_edges(p, o_id);
CREATE INDEX IF NOT EXISTS idx_edges_s ON rdf_edges(s);

CREATE TABLE IF NOT EXISTS kg_NodeEmbeddings(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(768) NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_NodeEmbeddings_optimized(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(768) NOT NULL
);

CREATE TABLE IF NOT EXISTS docs(
  id    VARCHAR(256) PRIMARY KEY,
  text  VARCHAR(4000)
);
