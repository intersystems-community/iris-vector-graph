/// Every SQL procedure and function IVG's classes project (4.6.0 R084, spec 262). Each method forwards to the
/// Graph.KG or IVG code that does the work, with that method's arguments and return type, under the SQL name
/// callers have used since 4.5 (CypherFn_IVG*, kg_BM25, ledger_records, ...).
///
/// A [ SqlProc ] projects into the SQL schema of its class's package, so this class is compiled into the IVG
/// Schema's own package: it ships as Graph.KG.SqlProcs, which is that package for Graph_KG, and the deploy's source
/// rewrite renames it for any other schema (IVGALT.SqlProcs, MY.APP.SqlProcs). The code classes it calls stay in
/// Graph.KG and project nothing. IVGSCHEMACLASS marks it as IVG's, so a class of the same name the deploy did not
/// make is never loaded over (schema_name.SCHEMA_CLASSES).
Class Graph.KG.SqlProcs Extends %RegisteredObject [ Final ]
{

Parameter IVGSCHEMACLASS = 1;

// ---------------------------------------------------------------- Graph.KG.CypherFn: Cypher expression helpers

ClassMethod CypherFnIVGREGEXMATCH(s As %String = "", p As %String = "") As %Integer [ SqlName = CypherFn_IVGREGEXMATCH, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGREGEXMATCH(s, p)
}

ClassMethod CypherFnIVGJSONARRAYLENGTH(json As %String = "") As %Integer [ SqlName = CypherFn_IVGJSONARRAYLENGTH, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGJSONARRAYLENGTH(json)
}

ClassMethod CypherFnIVGJSONVALUE(json As %String = "", path As %String = "") As %String [ SqlName = CypherFn_IVGJSONVALUE, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGJSONVALUE(json, path)
}

ClassMethod CypherFnIVGJSONKEYS(json As %String = "") As %String [ SqlName = CypherFn_IVGJSONKEYS, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGJSONKEYS(json)
}

ClassMethod CypherFnIVGLISTREVERSE(json As %String = "") As %String [ SqlName = CypherFn_IVGLISTREVERSE, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGLISTREVERSE(json)
}

ClassMethod CypherFnIVGSTRSPLIT(s As %String = "", delim As %String = "") As %String [ SqlName = CypherFn_IVGSTRSPLIT, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGSTRSPLIT(s, delim)
}

ClassMethod CypherFnIVGNEWID() As %String [ SqlName = CypherFn_IVGNEWID, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGNEWID()
}

ClassMethod CypherFnIVGRAND() As %Decimal [ SqlName = CypherFn_IVGRAND, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGRAND()
}

ClassMethod CypherFnIVGLISTHEAD(json As %String = "") As %String [ SqlName = CypherFn_IVGLISTHEAD, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGLISTHEAD(json)
}

ClassMethod CypherFnIVGLISTLAST(json As %String = "") As %String [ SqlName = CypherFn_IVGLISTLAST, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGLISTLAST(json)
}

ClassMethod CypherFnIVGLISTTAIL(json As %String = "") As %String [ SqlName = CypherFn_IVGLISTTAIL, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGLISTTAIL(json)
}

ClassMethod CypherFnIVGLISTGET(json As %String = "", idx As %String = "") As %String [ SqlName = CypherFn_IVGLISTGET, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGLISTGET(json, idx)
}

ClassMethod CypherFnIVGTYPEERROR(msg As %String = "TypeError") As %String [ SqlName = CypherFn_IVGTYPEERROR, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGTYPEERROR(msg)
}

ClassMethod CypherFnIVGMAPGET(json As %String = "", key As %String = "") As %String [ SqlName = CypherFn_IVGMAPGET, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGMAPGET(json, key)
}

ClassMethod CypherFnIVGJSONSET(json As %String = "", key As %String = "", val As %String = "") As %String [ SqlName = CypherFn_IVGJSONSET, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGJSONSET(json, key, val)
}

ClassMethod CypherFnIVGJSONREMOVE(json As %String = "", key As %String = "") As %String [ SqlName = CypherFn_IVGJSONREMOVE, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGJSONREMOVE(json, key)
}

ClassMethod CypherFnIVGRANGE(startVal As %Integer = 0, endVal As %Integer = 0, stepVal As %Integer = 1) As %String [ SqlName = CypherFn_IVGRANGE, SqlProc ]
{
    Quit ##class(Graph.KG.CypherFn).IVGRANGE(startVal, endVal, stepVal)
}

ClassMethod JSONKEYS(json As %String = "") As %String [ SqlName = JSON_KEYS, SqlProc ]
{
    Quit ##class(Graph.KG.JsonKeys).JSONKEYS(json)
}

// ---------------------------------------------------------------- IVG.Percentile: percentileDisc / percentileCont

ClassMethod PercentilePDISC(vals As %String, p As %Double) As %String [ SqlName = Percentile_PDISC, SqlProc ]
{
    Quit ##class(IVG.Percentile).PDISC(vals, p)
}

ClassMethod PercentilePCONT(vals As %String, p As %Double) As %Double [ SqlName = Percentile_PCONT, SqlProc ]
{
    Quit ##class(IVG.Percentile).PCONT(vals, p)
}

// ---------------------------------------------------------------- search, edge scans, paths

ClassMethod kgBM25(name As %String, queryText As %String, k As %Integer) As %String [ SqlName = kg_BM25, SqlProc ]
{
    Quit ##class(Graph.KG.BM25Index).SearchProc(name, queryText, k)
}

ClassMethod kgIVF(name As %String, queryVecB64 As %String, k As %Integer = 10, nprobe As %Integer = 8) As %String [ SqlName = kg_IVF, SqlProc ]
{
    Quit ##class(Graph.KG.IVFIndex).SearchProc(name, queryVecB64, k, nprobe)
}

ClassMethod MatchEdges(sourceId As %String, predicate As %String, shard As %Integer = 0, graph = 0) As %String [ SqlName = MatchEdges, SqlProc ]
{
    Quit ##class(Graph.KG.EdgeScan).MatchEdges(sourceId, predicate, shard, graph)
}

ClassMethod MatchEdgesAllGraphs(sourceId As %String, predicate As %String, shard As %Integer = 0) As %String [ SqlName = MatchEdgesAllGraphs, SqlProc ]
{
    Quit ##class(Graph.KG.EdgeScan).MatchEdgesAllGraphs(sourceId, predicate, shard)
}

ClassMethod DijkstraPath(srcId As %String, dstId As %String, weightProp As %String = "", maxCost As %Double = 9999, maxHops As %Integer = 10, direction As %String = "out") As %String [ SqlName = DijkstraPath, SqlProc ]
{
    Quit ##class(Graph.KG.Traversal).DijkstraProc(srcId, dstId, weightProp, maxCost, maxHops, direction)
}

// ---------------------------------------------------------------- admin and the ledger

ClassMethod backfilltemporalrows() As %Integer [ SqlName = backfill_temporal_rows, SqlProc ]
{
    Quit ##class(Graph.KG.TemporalIndex).BackfillStructuralRows()
}

ClassMethod rebuildedgeindices(pSchema As %String = "Graph_KG") As %Integer [ SqlName = rebuild_edge_indices, SqlProc ]
{
    Quit ##class(Graph.KG.Ledger).RebuildEdgeIndices(pSchema)
}

/// Graph.KG.Ledger's Records query, as the procedure the Python ledger client pages through.
Query LedgerRecords(pSeqFrom As %Integer, pSeqTo As %Integer, pOrdinalFrom As %Integer = 1, pPageSize As %Integer = 5000) As %Query(ROWSPEC = "seq:%Integer,ordinal:%Integer,op:%String,entity_kind:%String,entity_id:%String,attr:%String,prior:%String,prior_null:%Integer,new:%String,new_null:%Integer,graph:%String,flags:%String,digest:%String,redacted_op:%String") [ SqlName = ledger_records, SqlProc ]
{
}

ClassMethod LedgerRecordsExecute(ByRef qHandle As %Binary, pSeqFrom As %Integer, pSeqTo As %Integer, pOrdinalFrom As %Integer = 1, pPageSize As %Integer = 5000) As %Status
{
    Quit ##class(Graph.KG.Ledger).RecordsExecute(.qHandle, pSeqFrom, pSeqTo, pOrdinalFrom, pPageSize)
}

ClassMethod LedgerRecordsFetch(ByRef qHandle As %Binary, ByRef Row As %List, ByRef AtEnd As %Integer = 0) As %Status [ PlaceAfter = LedgerRecordsExecute ]
{
    Quit ##class(Graph.KG.Ledger).RecordsFetch(.qHandle, .Row, .AtEnd)
}

ClassMethod LedgerRecordsClose(ByRef qHandle As %Binary) As %Status [ PlaceAfter = LedgerRecordsExecute ]
{
    Quit ##class(Graph.KG.Ledger).RecordsClose(.qHandle)
}

}
