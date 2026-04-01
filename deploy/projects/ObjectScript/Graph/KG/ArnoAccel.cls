/// Arno-accelerated graph analytics via $ZF(-4)/$ZF(-5) callout.
/// Native path: Rust reads pre-serialized graph JSON from a process-private shadow global
/// (^||ArnoKG.JSON.<gname>.graph_json) — one IrisGlobalGet per call, zero traversal overhead.
///
/// Load once per process with Load(path). All methods fall back to pure ObjectScript on failure.
Class Graph.KG.ArnoAccel Extends %RegisteredObject
{

ClassMethod Load(libPath As %String = "/tmp/libarno_callout.so") As %Boolean
{
	Try {
		Set dllid = $ZF(-4, 1, libPath)
		If dllid = 0 Return 0
		Set ^||ArnoAccel("dllid") = dllid
		For fn = "version","init","kg_pagerank_global","kg_ppr_global","kg_wcc_global","kg_cdlp_global","kg_subgraph_global","kg_khop_sample","kg_random_walk","kg_neighbor_agg" {
			Set fid = $ZF(-4, 3, dllid, fn)
			Set ^||ArnoAccel("fid", fn) = fid
		}
		Set initfid = ^||ArnoAccel("fid", "init")
		Do $ZF(-5, dllid, initfid)
		Return 1
	} Catch {
		Return 0
	}
}

ClassMethod IsAvailable() As %Boolean
{
	If '$Data(^||ArnoAccel("dllid")) Return 0
	Try {
		Set verid = $ZF(-4, 3, ^||ArnoAccel("dllid"), "version")
		Set ver = $ZF(-5, ^||ArnoAccel("dllid"), verid)
		Return (ver '= "")
	} Catch {
		Return 0
	}
}

ClassMethod CallFn(fn As %String, a1 As %String = "", a2 As %String = "", a3 As %String = "", a4 As %String = "") As %String [ Private ]
{
	Set dllid = $Get(^||ArnoAccel("dllid"), 0)
	If dllid = 0 $$$ThrowStatus($$$ERROR($$$GeneralError,"arno not loaded"))
	Set fid = $Get(^||ArnoAccel("fid", fn), 0)
	If fid = 0 $$$ThrowStatus($$$ERROR($$$GeneralError,"arno function not found: "_fn))
	If a1 = "" Return $ZF(-5, dllid, fid)
	If a2 = "" Return $ZF(-5, dllid, fid, a1)
	If a3 = "" Return $ZF(-5, dllid, fid, a1, a2)
	If a4 = "" Return $ZF(-5, dllid, fid, a1, a2, a3)
	Return $ZF(-5, dllid, fid, a1, a2, a3, a4)
}

/// Build and cache graph JSON in ^||ArnoKG.JSON.<gname>.graph_json.
/// Called automatically before each algorithm invocation.
ClassMethod BumpVersion()
{
	Set ^KG("__version") = $Get(^KG("__version"), 0) + 1
	Set tmp = $Increment(^NKG("$meta", "version"))
}

ClassMethod CacheGraphJson(gname As %String = "KG") As %String [ Private ]
{
	Set currentVer = $Get(^KG("__version"), 0)
	Set cachedVer = $Get(^ArnoKG(gname, "version"), -1)
	If cachedVer = currentVer {
		Return $Get(^ArnoKG(gname, "graph_json"), "")
	}
	Set json = ..BuildGraphJson()
	Set ^ArnoKG(gname, "graph_json") = json
	Set ^ArnoKG(gname, "version") = currentVer
	Return json
}

ClassMethod BuildGraphJson() As %String
{
	Set nodes = ##class(%DynamicArray).%New()
	Set edges = ##class(%DynamicArray).%New()

	Set node = ""
	For {
		Set node = $Order(^KG("deg", node))
		Quit:node=""
		Do nodes.%Push(node)
		Set seen(node) = ""
	}
	Set node = ""
	For {
		Set node = $Order(^KG("in", node))
		Quit:node=""
		If '$Data(seen(node)) {
			Do nodes.%Push(node)
			Set seen(node) = ""
		}
	}

	Set src = ""
	For {
		Set src = $Order(^KG("out", src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", src, pred))
			Quit:pred=""
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", src, pred, dst))
				Quit:dst=""
				Set e = ##class(%DynamicObject).%New()
				Do e.%Set("src", src)
				Do e.%Set("dst", dst)
				Do e.%Set("type", pred)
				Do edges.%Push(e)
			}
		}
	}

	Set graph = ##class(%DynamicObject).%New()
	Do graph.%Set("nodes", nodes)
	Do graph.%Set("edges", edges)
	Return graph.%ToJSON()
}

ClassMethod PageRankGlobalJson(damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_pagerank_global", "KG", damping, maxIter)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return ##class(Graph.KG.PageRank).PageRankGlobalJson(damping, maxIter)
}

ClassMethod PPRJson(seedJson As %String, damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_ppr_global", "KG", seedJson, damping, maxIter)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return ##class(Graph.KG.PageRank).RunJson(seedJson, damping, maxIter)
}

ClassMethod WCCJson(maxIter As %Integer = 100) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_wcc_global", "KG")
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return ##class(Graph.KG.Algorithms).WCCJson(maxIter)
}

ClassMethod CDLPJson(maxIter As %Integer = 10) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_cdlp_global", "KG", maxIter)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return ##class(Graph.KG.Algorithms).CDLPJson(maxIter)
}

ClassMethod SubgraphJson(seedJson As %String, hops As %Integer = 2, edgeTypesJson As %String = "", maxNodes As %Integer = 10000) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_subgraph_global", "KG", seedJson, hops, maxNodes)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return ##class(Graph.KG.Subgraph).SubgraphJson(seedJson, hops, edgeTypesJson, maxNodes)
}

/// Kumo-style k-hop neighborhood sampling with fanout limit.
/// Returns JSON: {"layers": [[{src,dst},...], ...], "seeds": [...]}
ClassMethod KhopSampleJson(seedJson As %String, hops As %Integer = 2, fanout As %Integer = 16) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_khop_sample", "KG", seedJson, hops, fanout)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return "{""layers"":[], ""error"": ""arno unavailable""}"
}

/// Random walk (DeepWalk/node2vec-style). Returns JSON array of walk sequences.
ClassMethod RandomWalkJson(seedJson As %String, walkLength As %Integer = 20, numWalks As %Integer = 10) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_random_walk", "KG", seedJson, walkLength, numWalks)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return "[]"
}

/// GraphSAGE-style mean neighbor aggregation. Returns JSON: {node: {agg_feat, degree}, ...}
ClassMethod NeighborAggJson(seedJson As %String, hops As %Integer = 2) As %String
{
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_neighbor_agg", "KG", seedJson, hops)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return "{}"
}

}
