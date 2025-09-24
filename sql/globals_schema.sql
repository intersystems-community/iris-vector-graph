-- sql/globals_schema.sql — IRIS Globals schema for B-tree optimized graph traversal
-- This complements the SQL tables with read-optimized global structures

-- Define global mappings for efficient graph traversal
-- These globals leverage IRIS B-tree storage for optimal $ORDER performance

/*
Global Structure Design:

^KG("out", s, p, o) = ""          ; forward adjacency (s -> p -> o)
^KG("in",  o, p, s) = ""          ; reverse adjacency (o <- p <- s)

^KG("label",  label, s) = ""      ; type/label postings (label -> nodes)
^KG("prop",   key, val, s) = ""   ; property postings (key:value -> nodes)
^KG("prop:",  key, s) = ""        ; property existence (key -> nodes)
^KG("qual",   qkey, qval, edgeId) = ""   ; qualifier postings

^KG("edge", edgeId) = $lb(s,p,o)  ; edge record storage
^KG("deg",  s) = total_out_degree ; degree statistics
^KG("degp", s, p) = out_degree_per_predicate ; per-predicate degree

^KG("stats", "pred", p) = $lb(meanDeg, selectivity, hotFlag) ; predicate statistics
*/

-- Helper function to populate globals from SQL tables
-- This will be called during data ingestion or as a batch rebuild

-- Procedure to build globals from existing SQL data
CREATE OR REPLACE PROCEDURE kg_BUILD_GLOBALS()
LANGUAGE OBJECTSCRIPT
BEGIN
  // Clear existing globals
  KILL ^KG

  // Build forward and reverse adjacency from rdf_edges
  &SQL(DECLARE edges_cursor CURSOR FOR
    SELECT s, p, o_id, edge_id FROM rdf_edges)
  &SQL(OPEN edges_cursor)

  FOR {
    &SQL(FETCH edges_cursor INTO :s, :p, :o, :edgeId)
    QUIT:SQLCODE'=0

    // Forward adjacency: s -> p -> o
    SET ^KG("out", s, p, o) = ""

    // Reverse adjacency: o <- p <- s
    SET ^KG("in", o, p, s) = ""

    // Edge record
    SET ^KG("edge", edgeId) = $LISTBUILD(s, p, o)

    // Update degree counters
    SET ^KG("deg", s) = $GET(^KG("deg", s)) + 1
    SET ^KG("degp", s, p) = $GET(^KG("degp", s, p)) + 1
  }

  &SQL(CLOSE edges_cursor)

  // Build label postings from rdf_labels
  &SQL(DECLARE labels_cursor CURSOR FOR
    SELECT s, label FROM rdf_labels)
  &SQL(OPEN labels_cursor)

  FOR {
    &SQL(FETCH labels_cursor INTO :s, :label)
    QUIT:SQLCODE'=0

    SET ^KG("label", label, s) = ""
  }

  &SQL(CLOSE labels_cursor)

  // Build property postings from rdf_props
  &SQL(DECLARE props_cursor CURSOR FOR
    SELECT s, key, val FROM rdf_props)
  &SQL(OPEN props_cursor)

  FOR {
    &SQL(FETCH props_cursor INTO :s, :key, :val)
    QUIT:SQLCODE'=0

    // Property existence
    SET ^KG("prop:", key, s) = ""

    // Property value posting
    IF val'="" SET ^KG("prop", key, val, s) = ""
  }

  &SQL(CLOSE props_cursor)

  // Calculate predicate statistics
  SET p = ""
  FOR {
    SET p = $ORDER(^KG("degp", "", p))
    QUIT:p=""

    SET sumDeg = 0, count = 0
    SET s = ""
    FOR {
      SET s = $ORDER(^KG("degp", s, p))
      QUIT:s=""
      SET deg = ^KG("degp", s, p)
      SET sumDeg = sumDeg + deg
      SET count = count + 1
    }

    SET meanDeg = $SELECT(count>0: sumDeg/count, 1: 0)
    SET selectivity = $SELECT(count>0: count/1000, 1: 1) // Rough selectivity estimate
    SET hotFlag = $SELECT(meanDeg>10: 1, 1: 0)

    SET ^KG("stats", "pred", p) = $LISTBUILD(meanDeg, selectivity, hotFlag)
  }

  RETURN "Globals built successfully"
END;

-- Trigger to maintain globals on edge insertion
-- Note: In production, consider batch updates for better performance
CREATE TRIGGER kg_edges_insert_trigger
AFTER INSERT ON rdf_edges
FOR EACH ROW
BEGIN
  // Update globals on edge insert
  QUIT  // Implementation would call ObjectScript to update ^KG
END;

-- Trigger to maintain globals on edge deletion
CREATE TRIGGER kg_edges_delete_trigger
AFTER DELETE ON rdf_edges
FOR EACH ROW
BEGIN
  // Update globals on edge delete
  QUIT  // Implementation would call ObjectScript to update ^KG
END;