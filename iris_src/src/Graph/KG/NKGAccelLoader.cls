Class Graph.KG.NKGAccelLoader Extends %RegisteredObject
{

ClassMethod UseNKG() As %Boolean
{
    If $Data(^||NKGAccel("useNKG")) Return ^||NKGAccel("useNKG")
    Set use = ($Data(^NKG("$meta")) > 0)
    Set ^||NKGAccel("useNKG") = use
    Return use
}
ClassMethod ResetCache()
{
    Kill ^||NKGAccel
}
ClassMethod Load(libPath As %String = "/usr/irissys/mgr/libarno_callout.so") As %Boolean
{
    If $Data(^||NKGAccel("dllid")) Return 1
    Set dllid = $ZF(-4, 1, libPath)
    If dllid = 0 Return 0
    Set ^||NKGAccel("dllid") = dllid
    Set ^||NKGAccel("libPath") = libPath
    Set ^||NKGAccel("bfs_fn") = $ZF(-4, 3, dllid, "kg_bfs_global")
    Return 1
}
ClassMethod Unload()
{
    Kill ^||NKGAccel
}
ClassMethod IsLoaded() As %Boolean
{
    If '$Data(^||NKGAccel("dllid")) Return 0
    Return (^||NKGAccel("bfs_fn") > 0)
}
ClassMethod InvalidateAdjCache()
{
    Kill ^ArnoKG("KG", "nkg_adj_ver")
    Kill ^ArnoKG("KG", "nkg_adj")
}
ClassMethod WarmAdjCache() As %Integer
{
    Do ..CacheNKGAdj()
    Return $Get(^ArnoKG("KG", "nkg_adj"), 0)
}
ClassMethod CacheNKGAdj() [ Private ]
{
    Set currentVer = $Get(^NKG("$meta", "version"), 0)
    Set cachedVer = $Get(^ArnoKG("KG", "nkg_adj_ver"), -1)
    If cachedVer = currentVer Quit
    Kill ^ArnoKG("KG", "nkg_adj")
    Set adj = ##class(Graph.KG.NKGAccelAdjacency).ExportAdjacency()
    If $Extract(adj, 1, 8) = "CHUNKED:" {
        Set n = $Piece($Piece(adj, ":", 2), "|", 1)
        For i = 1:1:n {
            Set ^ArnoKG("KG", "nkg_adj", i) = $Get(^||adj(i), "")
        }
        Set ^ArnoKG("KG", "nkg_adj") = n
    } ElseIf $Length(adj) <= 32000 {
        Set ^ArnoKG("KG", "nkg_adj") = 1
        Set ^ArnoKG("KG", "nkg_adj", 1) = adj
    } Else {
        Set pos = 1, chunkNum = 0
        While pos <= $Length(adj) {
            Set chunkNum = chunkNum + 1
            Set ^ArnoKG("KG", "nkg_adj", chunkNum) = $Extract(adj, pos, pos + 32000 - 1)
            Set pos = pos + 32000
        }
        Set ^ArnoKG("KG", "nkg_adj") = chunkNum
    }
    Set ^ArnoKG("KG", "nkg_adj_ver") = currentVer
}
ClassMethod Capabilities() As %String
{
    Set cap = {}
    Do cap.%Set("version", "0.1.0")
    Do cap.%Set("nkg_accel", 1, "boolean")
    Do cap.%Set("nkg_data", ..UseNKG(), "boolean")
    Do cap.%Set("global_set", ##class(%Dictionary.ClassDefinition).%ExistsId("User.GlobalSet"), "boolean")
    Do cap.%Set("vec_index", ##class(%Dictionary.ClassDefinition).%ExistsId("Graph.KG.VecIndex"), "boolean")
    Set hasRust = 0
    Try {
        If ##class(Graph.KG.ArnoAccel).IsAvailable() Set hasRust = 1
    } Catch {}
    Do cap.%Set("rust_callout", hasRust, "boolean")
    Set algos = ["khop", "ppr", "random_walk", "export_adjacency"]
    Do cap.%Set("algorithms", algos)
    Set rustAlgos = []
    If hasRust {
        Do rustAlgos.%Push("pagerank")
        Do rustAlgos.%Push("wcc")
        Do rustAlgos.%Push("cdlp")
        Do rustAlgos.%Push("bfs")
    }
    Do cap.%Set("rust_algorithms", rustAlgos)
    Do cap.%Set("bfs", hasRust, "boolean")
    Do cap.%Set("count_distinct_khop", 1, "boolean")
    Return cap.%ToJSON()
}
ClassMethod BuildNKGRust() As %String
{
    Try {
        If '##class(Graph.KG.ArnoAccel).IsAvailable() {
            Return "{""error"":""Rust callout not available — load ArnoAccel first""}"
        }
        Set dllid = $Get(^||ArnoAccel("dllid"), 0)
        If dllid = 0 Return "{""error"":""ArnoAccel DLL not loaded""}"
        Set fid = $ZF(-4, 3, dllid, "kg_build_nkg")
        If fid = 0 Return "{""error"":""kg_build_nkg not found in DLL""}"
        Return $ZF(-5, dllid, fid)
    } Catch ex {
        Return "{""error"":""Rust callout failed: "_ex.DisplayString()_"""}"
    }
}
ClassMethod GetFirstNKGNode() As %String
{
    Return $Get(^NKG("$ND", 0), "")
}
}
