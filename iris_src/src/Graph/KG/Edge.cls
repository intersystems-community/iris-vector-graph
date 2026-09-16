/// Graph.KG.Edge - RDF-style edge storage
///
/// Adjacency (^KG("out"/"in"/"deg"/"degp")) is NOT maintained by this class.
/// It is written by Graph.KG.EdgeScan:WriteAdjacency, which is graph-scoped.
/// This class carried a functional index (Graph.KG.GraphIndex) that wrote
/// ^KG("out", 0, ...) for every graph and ^KG("deg", s) with no graph key,
/// silently routing named-graph edges into the default graph. See ADR-0001.
///
/// IMPORTANT: Class must be Final to avoid %%CLASSNAME implicit clustering
Class Graph.KG.Edge Extends %Persistent [ Final, DdlAllowed, SqlTableName = rdf_edges ]
{

/// Subject node ID
Property s As %String(MAXLEN = 256) [ Required, SqlColumnNumber = 2 ];

/// Predicate (relationship type)
Property p As %String(MAXLEN = 128) [ Required, SqlColumnNumber = 3 ];

/// Object node ID
Property oId As %String(MAXLEN = 256) [ Required, SqlColumnNumber = 4, SqlFieldName = o_id ];

/// Optional qualifiers (JSON or other metadata)
Property qualifiers As %String(MAXLEN = "") [ SqlColumnNumber = 5 ];

/// Named graph identifier for USE GRAPH filtering
Property graphId As %String(COLLATION = "EXACT", MAXLEN = 500) [ SqlColumnNumber = 6, SqlFieldName = graph_id ];

/// UNIQUE constraint on (s, p, o_id) to prevent duplicate edges
Index uspo On (s, p, oId) [ Unique ];

Index idxS On s;

Index idxP On p;

Index idxSP On (s, p);

Index idxPOid On (p, oId);

}
