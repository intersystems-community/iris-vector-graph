/// Graph.KG.Edge - RDF-style edge storage with functional graph index
/// 
/// Uses Graph.KG.GraphIndex to maintain ^KG adjacency list global:
///   ^KG("out", s, p, o) = rowID
///   ^KG("in", o, p, s) = rowID  
///   ^KG("deg", node) = count
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

/// Functional index that populates ^KG graph global
Index GraphIdx On (s, p, oId, qualifiers) As Graph.KG.GraphIndex;

}
