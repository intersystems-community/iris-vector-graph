/// ToolSet grouping all Graph.KG knowledge graph tools with audit policy.
/// DependsOn: %AI.ToolSet's %Discover and %Invoke generators read the classes the Definition includes, so those
/// compile first; without it a multi-process compile of the package could run them before Graph.KG.MCPTools had its
/// inheritance resolved (ERROR #5289, at random).
Class Graph.KG.MCPToolSet Extends %AI.ToolSet [ DependsOn = Graph.KG.MCPTools ]
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
