-- operators_fixed.sql — SQL procedures for retrieval operators
-- Fixed based on working rag-templates patterns and actual kg_NodeEmbeddings table structure
-- Table structure: kg_NodeEmbeddings(node_id, id, emb) where emb contains CSV string embeddings

-- 1) KNN over vectors - Fixed to work with CSV string embeddings in emb column
CREATE OR REPLACE PROCEDURE kg_KNN_VEC(
  IN queryVector LONGVARCHAR,     -- JSON array string: "[0.1,0.2,0.3,...]"
  IN k INT DEFAULT 50,
  IN labelFilter VARCHAR(128) DEFAULT NULL
)
RETURNS TABLE (id VARCHAR(256), score DOUBLE)
LANGUAGE SQL
BEGIN
  -- Convert JSON array to CSV string format that matches our data
  DECLARE csvVector LONGVARCHAR;
  -- Remove brackets and spaces from JSON array to get CSV format
  SET csvVector = REPLACE(REPLACE(REPLACE(queryVector, '[', ''), ']', ''), ' ', '');

  IF labelFilter IS NULL THEN
    RETURN
    SELECT TOP (k)
        n.id,
        VECTOR_COSINE(
            TO_VECTOR(CONCAT('[', n.emb, ']')),
            TO_VECTOR(queryVector)
        ) AS score
    FROM kg_NodeEmbeddings n
    WHERE n.emb IS NOT NULL
    ORDER BY score DESC;
  ELSE
    RETURN
    SELECT TOP (k)
        n.id,
        VECTOR_COSINE(
            TO_VECTOR(CONCAT('[', n.emb, ']')),
            TO_VECTOR(queryVector)
        ) AS score
    FROM kg_NodeEmbeddings n
    LEFT JOIN rdf_labels L ON L.s = n.id
    WHERE n.emb IS NOT NULL
      AND L.label = labelFilter
    ORDER BY score DESC;
  END IF;
END;

-- 2) Text search using basic LIKE search in rdf_edges qualifiers
-- Note: IRIS %FIND requires specific setup, using simpler LIKE search for reliability
CREATE OR REPLACE PROCEDURE kg_TXT(
  IN q VARCHAR(4000),
  IN k INT DEFAULT 50
)
RETURNS TABLE (id VARCHAR(256), bm25 DOUBLE)
LANGUAGE SQL
BEGIN
  -- Search in rdf_edges qualifiers using LIKE
  -- Score based on number of matches (simple BM25 approximation)
  RETURN
  SELECT TOP (k)
    e.s AS id,
    (
      CASE WHEN e.qualifiers LIKE CONCAT('%', q, '%') THEN 1.0 ELSE 0.0 END +
      CASE WHEN e.o_id LIKE CONCAT('%', q, '%') THEN 0.5 ELSE 0.0 END
    ) AS bm25
  FROM rdf_edges e
  WHERE e.qualifiers LIKE CONCAT('%', q, '%')
     OR e.o_id LIKE CONCAT('%', q, '%')
  ORDER BY bm25 DESC;
END;

-- 3) Simple RRF fusion that works with our current data structure
CREATE OR REPLACE PROCEDURE kg_RRF_FUSE(
  IN k INT DEFAULT 50,
  IN k1 INT DEFAULT 200,
  IN k2 INT DEFAULT 200,
  IN c INT DEFAULT 60,
  IN queryVector LONGVARCHAR,
  IN qtext VARCHAR(4000)
)
RETURNS TABLE (id VARCHAR(256), rrf DOUBLE, vs DOUBLE, bm25 DOUBLE)
LANGUAGE SQL
BEGIN
  -- Note: This is a simplified version that doesn't use CTEs due to IRIS SQL limitations
  -- In practice, you would call kg_KNN_VEC and kg_TXT separately and fuse in application code

  -- For now, return vector results with placeholder text scores
  RETURN
  SELECT
    v.id,
    v.score AS rrf,     -- Using vector score as RRF score (simplified)
    v.score AS vs,      -- Vector score
    0.0 AS bm25         -- Placeholder text score
  FROM TABLE(kg_KNN_VEC(queryVector, k, NULL)) v
  ORDER BY v.score DESC
  FETCH FIRST k ROWS ONLY;
END;

-- 4) Simple graph path traversal
CREATE OR REPLACE PROCEDURE kg_GRAPH_PATH(
  IN src_id VARCHAR(256),
  IN pred1 VARCHAR(128),
  IN pred2 VARCHAR(128),
  IN max_hops INT DEFAULT 2
)
RETURNS TABLE (path_id BIGINT, step INT, s VARCHAR(256), p VARCHAR(128), o VARCHAR(256))
LANGUAGE SQL
BEGIN
  -- Simple two-step path: src --pred1--> x --pred2--> y
  RETURN
  SELECT 1 AS path_id, 1 AS step, e1.s, e1.p, e1.o_id
  FROM rdf_edges e1
  WHERE e1.s = src_id AND e1.p = pred1
  UNION ALL
  SELECT 1 AS path_id, 2 AS step, e2.s, e2.p, e2.o_id
  FROM rdf_edges e2
  WHERE e2.p = pred2
    AND EXISTS (
      SELECT 1 FROM rdf_edges e1
      WHERE e1.s = src_id AND e1.p = pred1 AND e1.o_id = e2.s
    );
END;

-- 5) Rerank procedure (passthrough to RRF for now)
CREATE OR REPLACE PROCEDURE kg_RERANK(
  IN topN INT,
  IN queryVector LONGVARCHAR,
  IN qtext VARCHAR(4000)
)
RETURNS TABLE (id VARCHAR(256), score DOUBLE)
LANGUAGE SQL
BEGIN
  RETURN
  SELECT id, rrf AS score
  FROM TABLE(kg_RRF_FUSE(topN, 200, 200, 60, queryVector, qtext));
END;