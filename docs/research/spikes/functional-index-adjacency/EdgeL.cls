Class IVGSpike.EdgeL Extends %Persistent [ SqlTableName = EdgeL ]
{

Property GraphId As %String(COLLATION = "EXACT", MAXLEN = 256) [ InitialExpression = "", Required ];

Property S As %String(COLLATION = "EXACT", MAXLEN = 256) [ Required ];

Property P As %String(COLLATION = "EXACT", MAXLEN = 256) [ Required ];

Property O As %String(COLLATION = "EXACT", MAXLEN = 256) [ Required ];

Index Spo On (GraphId, S, P, O) [ Unique ];

Index Adj On (GraphId, S, P, O) As IVGSpike.AdjIndexL;

}
