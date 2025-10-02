// Hybrid Vector + Graph Query in openCypher
// Use case: Find nodes similar to query vector, then explore their graph neighborhood
//
// SQL equivalent from test_nodepk_advanced_benchmarks.py:305-350
// This demonstrates how IRIS could expose the same query via openCypher interface

// ============================================================================
// Data Model Mapping (SQL -> openCypher)
// ============================================================================
// SQL Tables               -> Cypher Graph Elements
// ---------------------------------------------------------------------
// nodes                    -> (:Node {id: "PROTEIN:123"})
// rdf_labels               -> (:Node:Protein) - label becomes node type
// rdf_props                -> (:Node {key: value}) - properties
// rdf_edges                -> ()-[:INTERACTS_WITH]->() - relationship
// kg_NodeEmbeddings        -> (:Node {embedding: [...]}) - vector property

// ============================================================================
// Example: Hybrid Query - Vector Similarity + 1-Hop Graph Expansion
// ============================================================================

// Step 1: Vector k-NN search (top 20 most similar nodes)
// Note: openCypher doesn't have native vector search, so this would require
//       a custom function or procedure (e.g., db.index.vector.queryNodes)
CALL db.index.vector.queryNodes('node_embeddings', 20, $queryVector) YIELD node AS centerNode, score AS similarity

// Step 2: Graph expansion (1-hop neighbors)
MATCH (centerNode)-[edge]->(neighbor:Node)

// Step 3: Return results with labels and properties
RETURN
    centerNode.id AS center_node,
    similarity,
    type(edge) AS edge_predicate,
    neighbor.id AS neighbor_node,
    labels(neighbor) AS neighbor_labels,
    neighbor AS neighbor_properties
ORDER BY similarity DESC

// ============================================================================
// Alternative: More Cypher-idiomatic pattern matching
// ============================================================================

// Find similar nodes and their 1-hop neighborhood in single query
CALL db.index.vector.queryNodes('node_embeddings', 20, $queryVector) YIELD node, score
MATCH (node)-[rel]->(neighbor)
OPTIONAL MATCH (neighbor)-[:HAS_LABEL]->(label)
RETURN
    node.id,
    score,
    collect({
        predicate: type(rel),
        neighbor: neighbor.id,
        labels: collect(label.name)
    }) AS connections
ORDER BY score DESC

// ============================================================================
// Example: 2-Hop Graph Traversal (from test_graph_traversal_with_node_validation)
// ============================================================================

// SQL version (lines 259-272 in test_nodepk_advanced_benchmarks.py):
// SELECT e1.s, e1.p, e1.o_id, e2.p, e2.o_id
// FROM rdf_edges e1
// INNER JOIN rdf_edges e2 ON e1.o_id = e2.s
// WHERE e1.s = ?

// openCypher version:
MATCH path = (start:Node {id: $startNodeId})-[edge1]->(intermediate)-[edge2]->(destination)
RETURN
    start.id AS start_node,
    type(edge1) AS edge1_predicate,
    intermediate.id AS intermediate_node,
    type(edge2) AS edge2_predicate,
    destination.id AS destination_node

// ============================================================================
// Example: Filtered Vector Search by Label
// ============================================================================

// SQL version:
// SELECT TOP 10 e.id, VECTOR_DOT_PRODUCT(e.emb, TO_VECTOR(?))
// FROM kg_NodeEmbeddings e
// INNER JOIN rdf_labels l ON e.id = l.s
// WHERE l.label = 'protein'

// openCypher version:
CALL db.index.vector.queryNodes('node_embeddings', 10, $queryVector) YIELD node, score
WHERE 'Protein' IN labels(node)
RETURN node.id, score
ORDER BY score DESC

// ============================================================================
// Example: Complex Multi-Table Join (from test_complex_join_all_tables)
// ============================================================================

// SQL version (lines 388-399):
// SELECT n.node_id, l.label, p.key, p.val, e.p, e.o_id
// FROM nodes n
// INNER JOIN rdf_labels l ON n.node_id = l.s
// INNER JOIN rdf_props p ON n.node_id = p.s
// LEFT JOIN rdf_edges e ON n.node_id = e.s

// openCypher version (much more natural!):
MATCH (n:Node)
WHERE n.id STARTS WITH $nodePrefix
OPTIONAL MATCH (n)-[rel]->(connected)
RETURN
    n.id AS node_id,
    labels(n) AS labels,
    properties(n) AS properties,
    collect({
        predicate: type(rel),
        connected_node: connected.id
    }) AS edges

// ============================================================================
// Data Model Design: SQL Tables -> Property Graph
// ============================================================================

// Recommended graph schema for openCypher interface:
//
// Nodes:
//   (:Node {id: "PROTEIN:123", created_at: timestamp})
//   - id: node_id from nodes table (PRIMARY KEY)
//   - labels: from rdf_labels table (e.g., :Protein, :Gene, :Pathway)
//   - properties: from rdf_props table (key-value pairs as node properties)
//   - embedding: from kg_NodeEmbeddings table (768-dimensional vector)
//
// Relationships:
//   (:Node)-[:INTERACTS_WITH]->(:Node)
//   - type: predicate from rdf_edges.p
//   - properties: qualifiers from rdf_edges.qualifiers (JSON)
//
// Indexes:
//   - Vector index on Node.embedding (for k-NN search)
//   - B-tree index on Node.id (for lookups)
//   - Full-text index on Node properties (for text search)

// ============================================================================
// Performance Comparison: SQL vs Cypher
// ============================================================================

// SQL Strengths:
// - Native VECTOR_DOT_PRODUCT with HNSW index (~1-10ms for k-NN)
// - Explicit JOIN control for query optimization
// - Well-understood query plans and indexes
//
// Cypher Strengths:
// - More intuitive pattern matching for graph traversals
// - Cleaner syntax for multi-hop queries
// - Natural fit for graph-centric queries
//
// Hybrid Approach (Best of Both):
// - Use SQL for vector operations (HNSW-optimized)
// - Use Cypher for graph pattern matching
// - IRIS could expose both interfaces on same data!

// ============================================================================
// Example: Multi-Modal RAG Query
// ============================================================================

// Use case: "Find proteins similar to my cancer biomarker,
//            then show pathways they participate in,
//            and drugs that target those pathways"

CALL db.index.vector.queryNodes('protein_embeddings', 10, $cancerBiomarkerEmbedding)
YIELD node AS protein, score AS similarity

MATCH (protein)-[:PARTICIPATES_IN]->(pathway:Pathway)
MATCH (drug:Drug)-[:TARGETS]->(pathway)

RETURN
    protein.id AS similar_protein,
    similarity,
    collect(DISTINCT pathway.name) AS pathways,
    collect(DISTINCT drug.name) AS targeting_drugs
ORDER BY similarity DESC
LIMIT 20

// This is MUCH cleaner than the SQL equivalent with 5+ JOINs!
