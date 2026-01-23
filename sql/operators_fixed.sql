-- operators_fixed.sql — SQL procedures for retrieval operators

-- 1) KNN over vectors
CREATE OR REPLACE PROCEDURE kg_KNN_VEC(
  IN queryVector LONGVARCHAR,     -- JSON array string: "[0.1,0.2,0.3,...]"
  IN k INT DEFAULT 50,
  IN labelFilter VARCHAR(128) DEFAULT NULL
)
RETURNS TABLE (id VARCHAR(256), score DOUBLE)
LANGUAGE SQL
BEGIN
  IF labelFilter IS NULL THEN
    SELECT TOP :k
        n.id,
        VECTOR_COSINE(n.emb, TO_VECTOR(:queryVector)) AS score
    FROM kg_NodeEmbeddings n
    WHERE n.emb IS NOT NULL
    ORDER BY score DESC;
  ELSE
    SELECT TOP :k
        n.id,
        VECTOR_COSINE(n.emb, TO_VECTOR(:queryVector)) AS score
    FROM kg_NodeEmbeddings n
    JOIN rdf_labels L ON L.s = n.id
    WHERE n.emb IS NOT NULL
      AND L.label = :labelFilter
    ORDER BY score DESC;
  END IF;
END;

-- 2) Text search using basic LIKE search
CREATE OR REPLACE PROCEDURE kg_TXT(
  IN q VARCHAR(4000),
  IN k INT DEFAULT 50
)
RETURNS TABLE (id VARCHAR(256), bm25 DOUBLE)
LANGUAGE SQL
BEGIN
  SELECT TOP :k
    e.s AS id,
    (
      CASE WHEN e.qualifiers LIKE CONCAT('%', :q, '%') THEN 1.0 ELSE 0.0 END +
      CASE WHEN e.o_id LIKE CONCAT('%', :q, '%') THEN 0.5 ELSE 0.0 END
    ) AS bm25
  FROM rdf_edges e
  WHERE e.qualifiers LIKE CONCAT('%', :q, '%')
     OR e.o_id LIKE CONCAT('%', :q, '%')
  ORDER BY bm25 DESC;
END;

-- 3) RRF fusion
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
  SELECT TOP :k
    v.id,
    v.score AS rrf,
    v.score AS vs,
    0.0 AS bm25
  FROM TABLE(kg_KNN_VEC(:queryVector, :k, NULL)) v
  ORDER BY v.score DESC;
END;

-- 4) Graph path traversal
CREATE OR REPLACE PROCEDURE kg_GRAPH_PATH(
  IN src_id VARCHAR(256),
  IN pred1 VARCHAR(128),
  IN pred2 VARCHAR(128),
  IN max_hops INT DEFAULT 2
)
RETURNS TABLE (path_id BIGINT, step INT, s VARCHAR(256), p VARCHAR(128), o VARCHAR(256))
LANGUAGE SQL
BEGIN
  SELECT 1 AS path_id, 1 AS step, e1.s, e1.p, e1.o_id
  FROM rdf_edges e1
  WHERE e1.s = :src_id AND e1.p = :pred1
  UNION ALL
  SELECT 1 AS path_id, 2 AS step, e2.s, e2.p, e2.o_id
  FROM rdf_edges e2
  WHERE e2.p = :pred2
    AND EXISTS (
      SELECT 1 FROM rdf_edges e1
      WHERE e1.s = :src_id AND e1.p = :pred1 AND e1.o_id = e2.s
    );
END;

-- 5) Rerank procedure
CREATE OR REPLACE PROCEDURE kg_RERANK(
  IN topN INT,
  IN queryVector LONGVARCHAR,
  IN qtext VARCHAR(4000)
)
RETURNS TABLE (id VARCHAR(256), score DOUBLE)
LANGUAGE SQL
BEGIN
  SELECT id, rrf AS score
  FROM TABLE(kg_RRF_FUSE(:topN, 200, 200, 60, :queryVector, :qtext));
END;
