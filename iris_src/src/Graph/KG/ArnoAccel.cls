/// Arno-accelerated graph analytics via $ZF(-4)/$ZF(-5) callout.
/// Native path: Rust reads pre-serialized graph JSON from a process-private shadow global
/// (^||ArnoKG.JSON.<gname>.graph_json) — one IrisGlobalGet per call, zero traversal overhead.
///
/// Load once per process with Load(path). All methods fall back to pure ObjectScript on failure.
///
/// ^ArnoAccel("lib_path") is configuration, shared by every process. The handle $ZF(-4,1) returns and the function
/// ids are valid only in the process that loaded the library, so they live in ^||ArnoAccel("dllid") and
/// ^||ArnoAccel("fid", fn). Spec 210 kept them in ^ArnoAccel, and a process then called through whatever numbers the
/// last process to load had left (tests/e2e/test_arno_handle_per_process_e2e.py). A process that has not loaded the
/// library loads it from lib_path on first use (IsAvailable, CallFn).
Class Graph.KG.ArnoAccel Extends %RegisteredObject
{

/// Where the library is when nothing names another path (ADMIN_GUIDE, "Where it looks").
Parameter DEFAULTLIBPATH = "/usr/irissys/mgr/libarno_callout.so";

/// The library path every loader uses (4.6.0 R132): pPath when given (the engine passes IVG_ARNO_LIB), else the path
/// the last successful load in this namespace recorded in ^ArnoAccel("lib_path"), else DEFAULTLIBPATH. Nothing is
/// loaded from /tmp unless it is named: before R132 this class defaulted to /tmp and the BFS loader to the default,
/// so one process could hold two builds.
ClassMethod LibPath(pPath As %String = "") As %String
{
	Quit:pPath'="" pPath
	Set tRecorded = $Get(^ArnoAccel("lib_path"))
	Quit:tRecorded'="" tRecorded
	Quit ..#DEFAULTLIBPATH
}

/// The file this process loaded the library from, "" when it holds no handle.
ClassMethod LoadedPath() As %String
{
	Quit $Get(^||ArnoAccel("path"))
}

/// 1 when the library file pPath (resolved as LibPath resolves it) is on this server's file system, else 0.
ClassMethod LibExists(pPath As %String = "") As %Boolean
{
	Quit ''##class(%File).Exists(..LibPath(pPath))
}

/// The $ZF(-4,1) handle for pPath, or 0 with the reason in pError. A file that is not there is not handed to IRIS to
/// load (4.6.0 R207): $ZF(-4,1) of a missing file writes two severity-1 lines to messages.log ("Failed to load dynamic
/// library", "Error loading dll") in every process that tries, and the library is optional and not distributed. Every
/// loader opens the library through this method.
ClassMethod OpenLibrary(pPath As %String, Output pError As %String) As %Integer
{
	Set pError = "", tId = 0
	If '##class(%File).Exists(pPath) {
		Set pError = "library file not found: "_pPath
		Quit 0
	}
	Try {
		Set tId = $ZF(-4, 1, pPath)
		If tId = 0 Set pError = "library not loaded: "_pPath
	} Catch tEx {
		Set pError = tEx.DisplayString(), tId = 0
	}
	Quit tId
}

ClassMethod Load(libPath As %String = "") As %Boolean
{
	Set libPath = ..LibPath(libPath)
	// The library's init entry moves the process to USER. Put it back: every step after
	// a Load would otherwise run against USER (spec 236 R3).
	Set tNs = $NAMESPACE
	// a failed load leaves no handle behind, not the one for the path loaded before
	Kill ^||ArnoAccel
	Try {
		Set dllid = ..OpenLibrary(libPath)
		If dllid = 0 Return 0
		Set ^||ArnoAccel("dllid") = dllid, ^||ArnoAccel("path") = libPath
		Set ^ArnoAccel("lib_path") = libPath
		// what releases before 4.5.2 kept here: another process's handle, never valid in this one
		Kill ^ArnoAccel("dllid"), ^ArnoAccel("fid")
		For fn = "version","init","kg_pagerank_global","kg_ppr_global","kg_wcc_global","kg_cdlp_global","kg_subgraph_global","kg_khop_sample","kg_random_walk","kg_neighbor_agg" {
			Set fid = $ZF(-4, 3, dllid, fn)
			Set ^||ArnoAccel("fid", fn) = fid
		}
		Set initfid = ^||ArnoAccel("fid", "init")
		Do $ZF(-5, dllid, initfid)
		Set $NAMESPACE = tNs
		Return 1
	} Catch {
		Kill ^||ArnoAccel
		Set $NAMESPACE = tNs
		Return 0
	}
}

ClassMethod GetLibPath() As %String
{
	Return $Get(^ArnoAccel("lib_path"), "")
}

/// This process's handle for the configured library, 0 when it holds none for lib_path. Never loads.
ClassMethod Handle() As %Integer [ Private ]
{
	Quit:$Get(^||ArnoAccel("path"))'=$Get(^ArnoAccel("lib_path"), $Char(0)) 0
	Quit $Get(^||ArnoAccel("dllid"), 0)
}

ClassMethod IsAvailable() As %Boolean
{
	If '$Data(^ArnoAccel("lib_path")) Return 0
	// a handle this process loaded from another path is not the configured library
	Set tId = ..Handle()
	If tId '= 0 {
		Try {
			Set verid = $ZF(-4, 3, tId, "version")
			Set ver = $ZF(-5, tId, verid)
			If ver '= "" Return 1
		} Catch {}
	}
	Return ..Load(^ArnoAccel("lib_path"))
}

ClassMethod CallFn(fn As %String, a1 As %String = "", a2 As %String = "", a3 As %String = "", a4 As %String = "") As %String [ Private ]
{
	Set dllid = ..Handle()
	If dllid = 0 {
		Do ..IsAvailable()
		Set dllid = ..Handle()
	}
	If dllid = 0 $$$ThrowStatus($$$ERROR($$$GeneralError,"arno not loaded"))
	Set fid = $Get(^||ArnoAccel("fid", fn), 0)
	If fid = 0 $$$ThrowStatus($$$ERROR($$$GeneralError,"arno function not found: "_fn))
	// the library's readers leave the process in USER: put the caller's namespace back, or every later step on this
	// connection runs against USER (4.4.0 E2E gate; spec 236 R3 did the same for Load and Build2HopExactStats)
	Set tNs = $NAMESPACE
	Try {
		If a1 = "" {
			Set tOut = $ZF(-5, dllid, fid)
		} ElseIf a2 = "" {
			Set tOut = $ZF(-5, dllid, fid, a1)
		} ElseIf a3 = "" {
			Set tOut = $ZF(-5, dllid, fid, a1, a2)
		} ElseIf a4 = "" {
			Set tOut = $ZF(-5, dllid, fid, a1, a2, a3)
		} Else {
			Set tOut = $ZF(-5, dllid, fid, a1, a2, a3, a4)
		}
	} Catch tEx {
		Set $NAMESPACE = tNs
		Throw tEx
	}
	Set $NAMESPACE = tNs
	Quit tOut
}

/// The library's kg_khop_sample, kg_random_walk and kg_neighbor_agg read USER's graph whatever namespace calls them;
/// elsewhere the answer would be USER's, so they are refused there.
ClassMethod ReadsUser() As %Boolean [ Private ]
{
	Quit $NAMESPACE = "USER"
}

/// Reader revisions of the loaded callout build, as a JSON object {"<reader>": <int>}.
/// "{}" when no library is loaded in this process, the build predates the
/// `capabilities` entry (1.0.x: the lookup throws <ILLEGAL VALUE>, not 0), or the report
/// is not JSON. Never throws. Reuses the handle Load recorded in this process; loads nothing itself.
ClassMethod ReaderRevisions() As %String
{
	Set tOut = "{}"
	Try {
		Set tId = ..Handle()
		If tId '= 0 {
			Set tFn = $ZF(-4, 3, tId, "capabilities")
			If tFn '= 0 {
				Set tObj = {}.%FromJSON($ZF(-5, tId, tFn))
				If $IsObject(tObj.readers) Set tOut = tObj.readers.%ToJSON()
			}
		}
	} Catch tEx {
		Set tOut = "{}"
	}
	Quit tOut
}

/// The loaded build's version string ("arno-callout X.Y.Z+<sha>"), "" when none is loaded.
/// For logs and the proof file; routing never reads it.
ClassMethod LibraryVersion() As %String
{
	Set tOut = ""
	Try {
		Set tId = ..Handle()
		If tId '= 0 Set tOut = $ZF(-5, tId, $ZF(-4, 3, tId, "version"))
	} Catch tEx {
		Set tOut = ""
	}
	Quit tOut
}

/// Calls a Rust kg_*_global reader; an "ERROR..." answer is thrown so the caller falls
/// back to its Owner.
ClassMethod NativeResult(fn As %String, a1 As %String = "", a2 As %String = "", a3 As %String = "", a4 As %String = "") As %String [ Private ]
{
	// The readers walk USER's ^KG whatever namespace the caller is in, and leave the
	// process in USER. Answering from them elsewhere would return another namespace's
	// graph, so refuse and let the Owner answer.
	If $NAMESPACE '= "USER" $$$ThrowStatus($$$ERROR($$$GeneralError, "arno "_fn_" reads USER only, not "_$NAMESPACE))
	Set tResult = ..CallFn(fn, a1, a2, a3, a4)
	If $Extract(tResult, 1, 5) = "ERROR" $$$ThrowStatus($$$ERROR($$$GeneralError, "arno "_fn_": "_tResult))
	Quit tResult
}

/// The Rust readers. Routed to only when the proof file accepts the loaded revision
/// (iris_vector_graph.arno_readers.route); the Owner-delegating *Json methods above
/// are unchanged.
/// Refused while the default graph is in multigraph mode (spec 234): the library reads the ^NKG export, which lists a
/// keyed triple once, so it would weigh a parallel edge 1 where Graph.KG.PageRank weighs it by its "#" count. The
/// refusal is a throw, so the caller's Owner answers.
ClassMethod PageRankNativeJson(damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	If ##class(Graph.KG.GraphMode).IsMulti("") {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "arno kg_pagerank_global: the default graph is a multigraph, and the library weighs a parallel edge once"))
	}
	Quit ..NativeResult("kg_pagerank_global", "^KG", damping, maxIter)
}

ClassMethod PPRNativeJson(seedJson As %String, damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	Quit ..NativeResult("kg_ppr_global", "^KG", seedJson, damping, maxIter)
}

ClassMethod WCCNativeJson() As %String
{
	Quit ..NativeResult("kg_wcc_global", "^KG")
}

ClassMethod CDLPNativeJson(maxIter As %Integer = 10) As %String
{
	Quit ..NativeResult("kg_cdlp_global", "^KG", maxIter)
}

ClassMethod SubgraphNativeJson(seedJson As %String, hops As %Integer = 2, maxNodes As %Integer = 10000) As %String
{
	Quit ..NativeResult("kg_subgraph_global", "^KG", seedJson, hops, maxNodes)
}

/// Build and cache graph JSON in ^||ArnoKG.JSON.<gname>.graph_json.
/// Called automatically before each algorithm invocation.
///
/// $Increment, not Set: it is atomic, so two writers never take the same value, and a
/// rolled-back transaction does not undo it, so a value a reader has seen is never
/// handed out again (spec 236, research-token-audit section 4.1).
ClassMethod BumpVersion()
{
	Set tmp = $Increment(^KG("__version"))
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

/// The default graph only: every Arno entry point here serves default-graph callers,
/// and ^KG("out"/"in") carry the graph key as their first subscript.
ClassMethod BuildGraphJson() As %String
{
	Set nodes = ##class(%DynamicArray).%New()
	Set edges = ##class(%DynamicArray).%New()
	Set tG = ##class(Graph.KG.GraphKey).ForIndex("")

	Set node = ""
	For {
		Set node = $Order(^KG("out", tG, node))
		Quit:node=""
		Do nodes.%Push(node)
		Set seen(node) = ""
	}
	Set node = ""
	For {
		Set node = $Order(^KG("in", tG, node))
		Quit:node=""
		If '$Data(seen(node)) {
			Do nodes.%Push(node)
			Set seen(node) = ""
		}
	}

	Set src = ""
	For {
		Set src = $Order(^KG("out", tG, src))
		Quit:src=""
		Set pred = ""
		For {
			Set pred = $Order(^KG("out", tG, src, pred))
			Quit:pred=""
			Set dst = ""
			For {
				Set dst = $Order(^KG("out", tG, src, pred, dst))
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

/// The Rust kg_*_global readers walk the live ^KG unreliably (first call sees only the
/// first source; later calls disagree). Answer from ObjectScript until Arno fixes them.
/// See docs/KNOWN_ISSUES.md and tests/unit/test_411_arno_kg_readers.py.
ClassMethod PageRankGlobalJson(damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	Return ##class(Graph.KG.PageRank).PageRankGlobalJson(damping, maxIter)
}

/// ObjectScript answer; see PageRankGlobalJson.
ClassMethod PPRJson(seedJson As %String, damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	Return ##class(Graph.KG.PageRank).RunJson(seedJson, damping, maxIter)
}

/// ObjectScript answer; see PageRankGlobalJson.
ClassMethod WCCJson(maxIter As %Integer = 100) As %String
{
	Return ##class(Graph.KG.Algorithms).WCCJson(maxIter)
}

/// ObjectScript answer; see PageRankGlobalJson.
ClassMethod CDLPJson(maxIter As %Integer = 10) As %String
{
	Return ##class(Graph.KG.Algorithms).CDLPJson(maxIter)
}

/// ObjectScript answer; see PageRankGlobalJson.
ClassMethod SubgraphJson(seedJson As %String, hops As %Integer = 2, edgeTypesJson As %String = "", maxNodes As %Integer = 10000) As %String
{
	Return ##class(Graph.KG.Subgraph).SubgraphJson(seedJson, hops, edgeTypesJson, maxNodes)
}

/// Kumo-style k-hop neighborhood sampling with fanout limit.
/// Returns JSON: {"layers": [[{src,dst},...], ...], "seeds": [...]}
ClassMethod KhopSampleJson(seedJson As %String, hops As %Integer = 2, fanout As %Integer = 16) As %String
{
	Quit:'..ReadsUser() "{""layers"":[], ""error"": ""arno reads USER only, not "_$NAMESPACE_"""}"
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
	Quit:'..ReadsUser() "[]"
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
	Quit:'..ReadsUser() "{}"
	Try {
		Do ..CacheGraphJson("KG")
		Set result = ..CallFn("kg_neighbor_agg", "KG", seedJson, hops)
		If $Extract(result, 1, 5) '= "ERROR" Return result
	} Catch {
	}
	Return "{}"
}

}
