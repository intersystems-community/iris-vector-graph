-- Graph_KG.rdf_reifications: RDF 1.2 reification junction table
-- Maps reifier nodes to the edges they annotate.
-- FK integrity enforced at application level (matching IVG delete_node() pattern).
CREATE TABLE IF NOT EXISTS Graph_KG.rdf_reifications (
    reifier_id VARCHAR(256) %EXACT NOT NULL,
    edge_id BIGINT NOT NULL,
    CONSTRAINT pk_reifications PRIMARY KEY (reifier_id)
);
CREATE INDEX idx_reif_edge ON Graph_KG.rdf_reifications (edge_id);
