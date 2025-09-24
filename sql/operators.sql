-- operators.sql — SQL procedures for retrieval operators
-- NOTE: Procedure syntax targets IRIS SQL.

-- 1) KNN over vectors (uses HNSW when TOP + ORDER BY VECTOR_* DESC)
CREATE OR REPLACE PROCEDURE kg_KNN_VEC(
  IN  queryVector VARCHAR(32000),  -- JSON array of 768 floats
  IN  k INT DEFAULT 50,
  IN  labelFilter VARCHAR(128) DEFAULT NULL
)
RETURNS TABLE (id VARCHAR(256), score DOUBLE)
LANGUAGE SQL
BEGIN
  DECLARE qvec VECTOR(768);
  SET qvec = TO_VECTOR(queryVector);  -- Parse JSON array to VECTOR

  RETURN
  SELECT TOP (k) n.id, VECTOR_COSINE(n.emb, qvec) AS score
  FROM kg_NodeEmbeddings n
  LEFT JOIN rdf_labels L ON L.s = n.id
  WHERE labelFilter IS NULL OR L.label = labelFilter
  ORDER BY VECTOR_COSINE(n.emb, qvec) DESC;
END;

-- 2) Lexical search using IRIS %FIND for full-text search
CREATE OR REPLACE PROCEDURE kg_TXT(
  IN q VARCHAR(4000),
  IN k INT DEFAULT 50
)
RETURNS TABLE (id VARCHAR(256), bm25 DOUBLE)
LANGUAGE SQL
BEGIN
  -- Use IRIS %FIND for proper text search with ranking
  -- %FIND returns relevance scores which we use as BM25 approximation
  RETURN
  SELECT TOP (k)
    d.id,
    %FIND.Rank(d.text, q) AS bm25
  FROM docs d
  WHERE %FIND(d.text, q) > 0   -- %FIND returns 0 for no match, >0 for matches
  ORDER BY %FIND.Rank(d.text, q) DESC;
END;

-- 3) Reciprocal Rank Fusion (vector + text)
CREATE OR REPLACE PROCEDURE kg_RRF_FUSE(
  IN k INT DEFAULT 50,
  IN k1 INT DEFAULT 200,
  IN k2 INT DEFAULT 200,
  IN c INT DEFAULT 60,
  IN queryVector VARCHAR(32000),  -- JSON array of 768 floats
  IN qtext VARCHAR(4000)
)
RETURNS TABLE (id VARCHAR(256), rrf DOUBLE, vs DOUBLE, bm25 DOUBLE)
LANGUAGE SQL
BEGIN
  WITH V AS (
    SELECT ROW_NUMBER() OVER (ORDER BY score DESC) AS r, id, score AS vs
    FROM TABLE(kg_KNN_VEC(queryVector, k1, NULL))
  ),
  K AS (
    SELECT ROW_NUMBER() OVER (ORDER BY bm25 DESC) AS r, id, bm25
    FROM TABLE(kg_TXT(qtext, k2))
  ),
  F AS (
    SELECT COALESCE(V.id, K.id) AS id,
           (1.0/(c + COALESCE(V.r, 1000000000))) +
           (1.0/(c + COALESCE(K.r, 1000000000))) AS rrf,
           V.vs, K.bm25
    FROM V FULL OUTER JOIN K ON V.id = K.id
  )
  SELECT id, rrf, vs, bm25
  FROM F
  ORDER BY rrf DESC
  FETCH FIRST k ROWS ONLY;
END;

-- 4) Meta-path / constrained path (stub)
-- Implement this in ObjectScript or Embedded Python for performance;
-- Here we provide a table function stub for compatibility.
CREATE OR REPLACE PROCEDURE kg_GRAPH_PATH(
  IN  src_id VARCHAR(256),
  IN  pred1 VARCHAR(128),
  IN  pred2 VARCHAR(128),
  IN  max_hops INT DEFAULT 2
)
RETURNS TABLE (path_id BIGINT, step INT, s VARCHAR(256), p VARCHAR(128), o VARCHAR(256))
LANGUAGE SQL
BEGIN
  -- Simple two-step example: src --pred1--> x --pred2--> y
  RETURN
  SELECT 1 AS path_id, 1 AS step, e1.s, e1.p, e1.o_id FROM rdf_edges e1 WHERE e1.s = src_id AND e1.p = pred1
  UNION ALL
  SELECT 1 AS path_id, 2 AS step, e2.s, e2.p, e2.o_id FROM rdf_edges e2
  WHERE e2.p = pred2 AND EXISTS (
      SELECT 1 FROM rdf_edges e1 WHERE e1.s = src_id AND e1.p = pred1 AND e1.o_id = e2.s
  );
END;

-- 5) Rerank (stub) — replace with external model call later; here it's a passthrough
CREATE OR REPLACE PROCEDURE kg_RERANK(
  IN  topN INT,
  IN  queryVector VARCHAR(32000),  -- JSON array of 768 floats
  IN  qtext VARCHAR(4000)
)
RETURNS TABLE (id VARCHAR(256), score DOUBLE)
LANGUAGE SQL
BEGIN
  RETURN
  SELECT id, rrf AS score FROM TABLE(kg_RRF_FUSE(topN, 200, 200, 60, queryVector, qtext));
END;
