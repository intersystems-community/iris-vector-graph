/// ToolSet grouping all Graph.KG knowledge graph tools with audit policy.
Class Graph.KG.MCPToolSet Extends %AI.ToolSet
{

XData Definition [ MimeType = application/xml ]
{
<ToolSet Name="Graph.KG.MCPToolSet">
	<Description>Knowledge graph tools: Cypher query, SQL query, graph loading, statistics, PageRank insight discovery, and evidence search</Description>
	<Policies>
		<Audit Class="%AI.Policy.ConsoleAudit" />
	</Policies>
	<Include Class="Graph.KG.MCPTools" />
</ToolSet>
}

}
