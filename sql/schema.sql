-- schema.sql — base objects
-- Materialized tables for RDF-ish data

CREATE TABLE Graph_KG.nodes (
    node_id VARCHAR(256) PRIMARY KEY NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Graph_KG.rdf_labels(
  s      VARCHAR(256) NOT NULL,
  label  VARCHAR(128) NOT NULL,
  CONSTRAINT fk_labels_node FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id)
);
CREATE INDEX idx_labels_label_s ON Graph_KG.rdf_labels(label, s);
CREATE INDEX idx_labels_s_label ON Graph_KG.rdf_labels(s, label);

CREATE TABLE Graph_KG.rdf_props(
  s      VARCHAR(256) NOT NULL,
  key    VARCHAR(128) NOT NULL,
  val    VARCHAR(4000)
);
CREATE INDEX idx_props_s_key ON Graph_KG.rdf_props(s, key);
CREATE INDEX idx_props_key_val ON Graph_KG.rdf_props(key, val);

CREATE TABLE Graph_KG.rdf_edges(
  edge_id  BIGINT IDENTITY PRIMARY KEY,
  s        VARCHAR(256) NOT NULL,
  p        VARCHAR(128) NOT NULL,
  o_id     VARCHAR(256) NOT NULL,
  qualifiers JSON,
  CONSTRAINT fk_edges_source FOREIGN KEY (s) REFERENCES Graph_KG.nodes(node_id),
  CONSTRAINT fk_edges_dest FOREIGN KEY (o_id) REFERENCES Graph_KG.nodes(node_id),
  CONSTRAINT u_spo UNIQUE (s, p, o_id)
);
CREATE INDEX idx_edges_s_p ON Graph_KG.rdf_edges(s, p);
CREATE INDEX idx_edges_p_oid ON Graph_KG.rdf_edges(p, o_id);
CREATE INDEX idx_edges_s ON Graph_KG.rdf_edges(s);
CREATE INDEX idx_edges_oid ON Graph_KG.rdf_edges(o_id);

CREATE TABLE Graph_KG.kg_NodeEmbeddings(
  id   VARCHAR(256) PRIMARY KEY,
  emb  VECTOR(FLOAT, 768) NOT NULL,
  CONSTRAINT fk_embeddings_node FOREIGN KEY (id) REFERENCES Graph_KG.nodes(node_id)
);

CREATE INDEX HNSW_NodeEmb ON Graph_KG.kg_NodeEmbeddings(emb)
  AS HNSW(M=16, efConstruction=100, Distance='Cosine');

CREATE TABLE Graph_KG.docs(
  id    VARCHAR(256) PRIMARY KEY,
  text  VARCHAR(4000)
);

-- NOTE: iFind index requires ObjectScript or Management Portal to create
-- Skip for DB API compatibility; create via IRIS session if needed:
-- CREATE INDEX idx_docs_text_find ON Graph_KG.docs(text) [ TYPE = %iFind.Index ];
