/// Spec 260 D-NKG-SWAP lab probe: do the ^NKG readers (ObjectScript and the arno callout) answer
/// the same on an ^NKG with index gaps (what a rewrite leaves after a node is removed) as on a
/// dense ^NKG of the same graph? Lab only: the callout reads USER, so this runs in USER of a
/// throwaway container.
Class IVGLab.NkgGap Extends %RegisteredObject
{

/// A strongly connected test graph in the default graph, through Graph.KG.Adjacency: a ring both
/// ways (P1, P3) and chords both ways (P2) over pN nodes v1..vN.
ClassMethod Gen(pN As %Integer = 40) As %Integer
{
	Kill ^KG, ^NKG
	Set tE = 0
	For tI = 1:1:pN {
		Set tA = "v"_tI, tB = "v"_((tI # pN) + 1), tC = "v"_(((tI + 6) # pN) + 1)
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tA, "P1", tB, 1))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tB, "P3", tA, 1))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tA, "P2", tC, 2))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tC, "P2", tA, 2))
		Set tE = tE + 4
	}
	Quit tE
}

/// Names at the middle and at the highest index of ^NKG("$ND"), comma-separated.
ClassMethod GapNames() As %String
{
	Set tTop = $Order(^NKG("$ND", ""), -1)
	Quit $Get(^NKG("$ND", tTop \ 2))_","_$Get(^NKG("$ND", tTop))
}

/// Indices below nodeCount with no $ND entry.
ClassMethod Gaps() As %String
{
	Set tN = +$Get(^NKG("$meta", "nodeCount")), tGaps = ""
	For tI = 0:1:tN - 1 {
		Set:'$Data(^NKG("$ND", tI)) tGaps = tGaps_$Select(tGaps = "": "", 1: ",")_tI
	}
	Quit tN_":"_tGaps
}

/// Every reader's answer as JSON {reader: text}. An exception is recorded as "THROW: ...".
ClassMethod Probe(pSeed As %String = "v1", pTarget As %String = "v20") As %String
{
	Set tNs0 = $NAMESPACE
	Set tLoaded = ##class(Graph.KG.ArnoAccel).Load("/tmp/libarno_callout.so")
	Set $NAMESPACE = tNs0
	Do ##class(Graph.KG.NKGAccel).ResetCache()
	Set tOut = {"arno_loaded_in_probe": (tLoaded), "arno_version": (##class(Graph.KG.ArnoAccel).LibraryVersion())}
	Set tCalls = $ListBuild(
		$ListBuild("bfs", "Graph.KG.NKGAccel", "BFSJson", pSeed, "[]", 3, 0),
		$ListBuild("khop", "Graph.KG.NKGAccel", "KHopNeighbors", pSeed, 2, 1000),
		$ListBuild("shortest", "Graph.KG.NKGAccel", "ShortestPathNKG", pSeed, pTarget, 10),
		$ListBuild("count_distinct", "Graph.KG.NKGAccel", "CountDistinctKHop", pSeed, "", 2, "both"),
		$ListBuild("betweenness", "Graph.KG.NKGAccel", "BetweennessGlobal", 0, 10000, 200),
		$ListBuild("betweenness_nb", "Graph.KG.NKGAccel", "BetweennessNeighborhood", pSeed, 2, 200, 100),
		$ListBuild("closeness_msbfs_classical", "Graph.KG.NKGAccel", "ClosenessGlobalMSBFS", "classical", "out", 0, 10000),
		$ListBuild("closeness_msbfs_harmonic", "Graph.KG.NKGAccel", "ClosenessGlobalMSBFS", "harmonic", "out", 0, 10000),
		$ListBuild("closeness", "Graph.KG.NKGAccel", "ClosenessGlobal", "classical", "out", 0, 10000),
		$ListBuild("eigenvector", "Graph.KG.NKGAccel", "EigenvectorGlobal", 30, 0.0001, 10000),
		$ListBuild("pagerank_native", "Graph.KG.ArnoAccel", "PageRankNativeJson", 0.85, 20),
		$ListBuild("ppr_native", "Graph.KG.ArnoAccel", "PPRNativeJson", "["""_pSeed_"""]", 0.85, 20),
		$ListBuild("wcc_native", "Graph.KG.ArnoAccel", "WCCNativeJson"),
		$ListBuild("cdlp_native", "Graph.KG.ArnoAccel", "CDLPNativeJson", 10),
		$ListBuild("subgraph_native", "Graph.KG.ArnoAccel", "SubgraphNativeJson", "["""_pSeed_"""]", 2, 10000),
		$ListBuild("triangles", "Graph.KG.Communities", "TriangleCountJson", 10000),
		$ListBuild("kcore", "Graph.KG.Communities", "KCoreJson", 10000),
		$ListBuild("scc", "Graph.KG.Communities", "SCCJson", 10000),
		$ListBuild("leiden", "Graph.KG.Communities", "LeidenJson", 10, 1.0, 0.0001, 10000, 256, 7))
	For tI = 1:1:$ListLength(tCalls) {
		Set tC = $List(tCalls, tI), tName = $List(tC, 1)
		Set tNs = $NAMESPACE
		Try {
			Set tArgs = $ListLength(tC) - 3
			For tJ = 1:1:tArgs { Set tA(tJ) = $List(tC, tJ + 3) }
			Set tA = tArgs
			Set tR = $ClassMethod($List(tC, 2), $List(tC, 3), tA...)
		} Catch ex {
			Set tR = "THROW: "_ex.DisplayString()
		}
		Set $NAMESPACE = tNs
		Kill tA
		If (tName = "bfs") && ($Piece(tR, ":", 1) = "SORTED") {
			Set tTag = $Piece(tR, ":", 2), tRows = ""
			Set tSt = ""
			For {
				Set tSt = $Order(^ArnoKG("bfs_r", tTag, tSt))
				Quit:tSt=""
				Set tO = ""
				For {
					Set tO = $Order(^ArnoKG("bfs_r", tTag, tSt, tO))
					Quit:tO=""
					Set tRows = tRows_$Select(tRows = "": "", 1: ",")_""""_tSt_":"_tO_""""
				}
			}
			Set tR = "["_tRows_"]"
		}
		Do tOut.%Set(tName, $Extract(tR, 1, 30000))
	}
	// the NODEMAP the in-process adjacency export hands the callout: entries with no name
	Try {
		Set tX = ##class(Graph.KG.NKGAccelAdjacency).ExportAdjacency()
		Set tMap = $Get(^||adj(1)), tEmpty = 0
		For tL = 2:1:$Length(tMap, $Char(10)) {
			Set tLine = $Piece(tMap, $Char(10), tL)
			Continue:(tLine="")||(tLine="---")
			Set:$Piece(tLine, "=", 2, *)="" tEmpty = tEmpty + 1
		}
		Do tOut.%Set("export_nodemap", $Piece(tMap, $Char(10), 1)_" empty_names="_tEmpty)
	} Catch ex {
		Do tOut.%Set("export_nodemap", "THROW: "_ex.DisplayString())
	}
	Quit tOut.%ToJSON()
}

}
