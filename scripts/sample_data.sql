-- scripts/sample_data.sql — tiny dataset

TRUNCATE TABLE rdf_labels;
TRUNCATE TABLE rdf_props;
TRUNCATE TABLE rdf_edges;
TRUNCATE TABLE kg_NodeEmbeddings;
TRUNCATE TABLE docs;

INSERT INTO rdf_labels(s,label) VALUES
 ('HGNC:11998','Gene'), ('DOID:162','Disease'), ('CHEMBL:123','Drug');

INSERT INTO rdf_props(s,key,val) VALUES
 ('HGNC:11998','symbol','TP53'),
 ('DOID:162','name','Cancer'),
 ('CHEMBL:123','name','SomeDrug');

INSERT INTO rdf_edges(s,p,o_id,qualifiers) VALUES
 ('CHEMBL:123','targets','HGNC:11998', NULL),
 ('HGNC:11998','ASSOCIATED_WITH','DOID:162', NULL);

INSERT INTO kg_NodeEmbeddings(id, emb) VALUES
 ('HGNC:11998', VECTOR_CONSTRUCT(0.1, 0.2, 0.9)),
 ('DOID:162',   VECTOR_CONSTRUCT(0.2, 0.1, 0.8)),
 ('CHEMBL:123', VECTOR_CONSTRUCT(0.9, 0.1, 0.2));

INSERT INTO docs(id, text) VALUES
 ('HGNC:11998', 'TP53 tumor suppressor gene'),
 ('DOID:162',   'A disease category'),
 ('CHEMBL:123', 'Drug info text here');
