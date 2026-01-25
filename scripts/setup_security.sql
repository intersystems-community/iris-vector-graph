-- Production security setup for IRIS Vector Graph
-- Creates roles for reading and writing graph data

-- Graph Reader Role: Read-only access to all graph tables
CREATE ROLE graph_reader;
GRANT SELECT ON nodes TO graph_reader;
GRANT SELECT ON rdf_labels TO graph_reader;
GRANT SELECT ON rdf_props TO graph_reader;
GRANT SELECT ON rdf_edges TO graph_reader;
GRANT SELECT ON kg_NodeEmbeddings TO graph_reader;
GRANT SELECT ON docs TO graph_reader;

-- Graph Writer Role: Full access to all graph tables
CREATE ROLE graph_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON nodes TO graph_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON rdf_labels TO graph_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON rdf_props TO graph_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON rdf_edges TO graph_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON kg_NodeEmbeddings TO graph_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON docs TO graph_writer;
