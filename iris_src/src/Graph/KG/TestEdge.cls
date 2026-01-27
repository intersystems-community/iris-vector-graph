Class Graph.KG.TestEdge Extends %Persistent
{
Property s As %String;
Property p As %String;
Property oId As %String;
Index GraphIdx On (s, p, oId) [ Type = Graph.KG.GraphIndex ]
}
