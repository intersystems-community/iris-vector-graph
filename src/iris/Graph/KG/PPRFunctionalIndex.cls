/// Functional Index for Personalized PageRank Graph Adjacency Maintenance
///
/// This class implements a Functional Index that automatically maintains graph
/// adjacency structures in IRIS Globals (^PPR) synchronized with SQL DML operations
/// on the rdf_edges table.
///
/// Global Structure:
/// - ^PPR("out", src, dst) = 1    // Outgoing edges (for updates/idempotence)
/// - ^PPR("in", dst, src) = 1     // Incoming edges (for updates/idempotence)
/// - ^PPR("deg", src) = count     // Outdegree cache (for normalization)
/// - ^PPR("outL", src) = $lb(...) // Packed outgoing neighbors (for fast $LISTNEXT iteration)
/// - ^PPR("inL", dst) = $lb(...)  // Packed incoming neighbors (for fast $LISTNEXT iteration)
/// - ^PPR("dang", v) = 1          // Dangling nodes (deg=0) for fast accumulation
///
/// Implementation Pattern:
/// Uses CodeMode = objectgenerator to emit ObjectScript code that the IRIS compiler
/// embeds as wrapper entry points (z<IndexName><Method>) in the table routine.
/// This is the canonical pattern for Functional Indexes.
///
/// Usage:
/// 1. Create index on rdf_edges table:
///    CREATE INDEX PPRAdj ON rdf_edges(s, o_id) AS Graph.KG.PPRFunctionalIndex;
/// 2. Rebuild for existing data:
///    DO ##class(Graph.KG.PPRFunctionalIndex).PurgeIndex()
///    // Rebuild via table build or re-insert data
///
/// Reference: specs/002-implement-functional-index/data-model.md
Class Graph.KG.PPRFunctionalIndex Extends %Library.FunctionalIndex
{

/// INSERT callback - invoked when edge is inserted into rdf_edges
///
/// Arguments (for Index On (s, o_id)):
///   pID: Row ID
///   pArg(1): Source node ID (s column)
///   pArg(2): Destination node ID (o_id column)
///
/// Actions:
///   1. Set ^PPR("out", src, dst) = 1
///   2. Set ^PPR("in", dst, src) = 1
///   3. Increment ^PPR("deg", src)
///   4. Update packed lists ^PPR("outL", src) and ^PPR("inL", dst) with $LISTBUILD
///   5. Update dangling node set if needed
///
/// CodeMode = objectgenerator emits ObjectScript that the table routine calls
ClassMethod InsertIndex(pID As %RawString, pArg... As %Binary) [ CodeMode = objectgenerator, ServerOnly = 1 ]
{
    If (%mode '= "method") {
        Do %code.WriteLine(" Set s=$Get(pArg(1)), d=$Get(pArg(2))")
        Do %code.WriteLine(" Quit:s=""""  Quit:d=""""")
        Do %code.WriteLine(" ; Set-style edges for updates/idempotence")
        Do %code.WriteLine(" Set ^PPR(""out"",s,d)=1, ^PPR(""in"",d,s)=1")
        Do %code.WriteLine(" Set ^PPR(""deg"",s)=$Get(^PPR(""deg"",s))+1")
        Do %code.WriteLine(" ; Remove src from dangling set (now has outdegree>0)")
        Do %code.WriteLine(" Kill ^PPR(""dang"",s)")
        Do %code.WriteLine(" ; Update packed outgoing list for src")
        Do %code.WriteLine(" Set L=$Get(^PPR(""outL"",s))")
        Do %code.WriteLine(" If L="""" Set ^PPR(""outL"",s)=$ListBuild(d)")
        Do %code.WriteLine(" Else  Set ^PPR(""outL"",s)=$ListBuild(d)_L")
        Do %code.WriteLine(" ; Update packed incoming list for dst")
        Do %code.WriteLine(" Set L=$Get(^PPR(""inL"",d))")
        Do %code.WriteLine(" If L="""" Set ^PPR(""inL"",d)=$ListBuild(s)")
        Do %code.WriteLine(" Else  Set ^PPR(""inL"",d)=$ListBuild(s)_L")
        Do %code.WriteLine(" ; Check if dst becomes non-dangling")
        Do %code.WriteLine(" If $Get(^PPR(""deg"",d))=0 Set ^PPR(""dang"",d)=1")
    }
    Quit $$$OK
}

/// UPDATE callback - invoked when edge is updated in rdf_edges
///
/// Arguments (for Index On (s, o_id)):
///   pID: Row ID
///   pArg(1): NEW source node ID (s)
///   pArg(2): NEW destination node ID (o_id)
///   pArg(3): OLD source node ID (s)
///   pArg(4): OLD destination node ID (o_id)
///
/// Actions:
///   1. If old edge differs from new, remove old edge + rebuild packed lists
///   2. If new edge provided, insert new edge + update packed lists
///   3. Update dangling node set if needed
ClassMethod UpdateIndex(pID As %RawString, pArg... As %Binary) [ CodeMode = objectgenerator, ServerOnly = 1 ]
{
    If (%mode '= "method") {
        Do %code.WriteLine(" Set ns=$Get(pArg(1)), nd=$Get(pArg(2))")
        Do %code.WriteLine(" Set os=$Get(pArg(3)), od=$Get(pArg(4))")
        Do %code.WriteLine(" ; Remove old edge if different")
        Do %code.WriteLine(" If (os'="""")&&(od'="""")&&((os'=ns)!(od'=nd)) {")
        Do %code.WriteLine("   Kill ^PPR(""out"",os,od), ^PPR(""in"",od,os)")
        Do %code.WriteLine("   Set ^PPR(""deg"",os)=$Get(^PPR(""deg"",os))-1")
        Do %code.WriteLine("   If ^PPR(""deg"",os)'>0 {")
        Do %code.WriteLine("     Kill ^PPR(""deg"",os)")
        Do %code.WriteLine("     Set ^PPR(""dang"",os)=1")
        Do %code.WriteLine("   }")
        Do %code.WriteLine("   ; Rebuild packed lists for old_src")
        Do %code.WriteLine("   Set L="""", nbr=""""")
        Do %code.WriteLine("   For { Set nbr=$Order(^PPR(""out"",os,nbr)) Quit:nbr=""""  Set L=L_$ListBuild(nbr) }")
        Do %code.WriteLine("   If L="""" Kill ^PPR(""outL"",os)")
        Do %code.WriteLine("   Else  Set ^PPR(""outL"",os)=L")
        Do %code.WriteLine("   ; Rebuild packed lists for old_dst")
        Do %code.WriteLine("   Set L="""", nbr=""""")
        Do %code.WriteLine("   For { Set nbr=$Order(^PPR(""in"",od,nbr)) Quit:nbr=""""  Set L=L_$ListBuild(nbr) }")
        Do %code.WriteLine("   If L="""" Kill ^PPR(""inL"",od)")
        Do %code.WriteLine("   Else  Set ^PPR(""inL"",od)=L")
        Do %code.WriteLine(" }")
        Do %code.WriteLine(" ; Insert new edge")
        Do %code.WriteLine(" If (ns'="""")&&(nd'="""") {")
        Do %code.WriteLine("   Set ^PPR(""out"",ns,nd)=1, ^PPR(""in"",nd,ns)=1")
        Do %code.WriteLine("   Set ^PPR(""deg"",ns)=$Get(^PPR(""deg"",ns))+1")
        Do %code.WriteLine("   Kill ^PPR(""dang"",ns)")
        Do %code.WriteLine("   ; Update packed lists for new_src")
        Do %code.WriteLine("   Set L=$Get(^PPR(""outL"",ns))")
        Do %code.WriteLine("   If L="""" Set ^PPR(""outL"",ns)=$ListBuild(nd)")
        Do %code.WriteLine("   Else  Set ^PPR(""outL"",ns)=$ListBuild(nd)_L")
        Do %code.WriteLine("   ; Update packed lists for new_dst")
        Do %code.WriteLine("   Set L=$Get(^PPR(""inL"",nd))")
        Do %code.WriteLine("   If L="""" Set ^PPR(""inL"",nd)=$ListBuild(ns)")
        Do %code.WriteLine("   Else  Set ^PPR(""inL"",nd)=$ListBuild(ns)_L")
        Do %code.WriteLine("   ; Check if dst becomes non-dangling")
        Do %code.WriteLine("   If $Get(^PPR(""deg"",nd))=0 Set ^PPR(""dang"",nd)=1")
        Do %code.WriteLine(" }")
    }
    Quit $$$OK
}

/// DELETE callback - invoked when edge is deleted from rdf_edges
///
/// Arguments (for Index On (s, o_id)):
///   pID: Row ID
///   pArg(1): Source node ID (s)
///   pArg(2): Destination node ID (o_id)
///
/// Actions:
///   1. Kill ^PPR("out", src, dst)
///   2. Kill ^PPR("in", dst, src)
///   3. Decrement ^PPR("deg", src), remove if zero
///   4. Rebuild packed lists ^PPR("outL", src) and ^PPR("inL", dst)
///   5. Update dangling node set if needed
ClassMethod DeleteIndex(pID As %RawString, pArg... As %Binary) [ CodeMode = objectgenerator, ServerOnly = 1 ]
{
    If (%mode '= "method") {
        Do %code.WriteLine(" Set s=$Get(pArg(1)), d=$Get(pArg(2))")
        Do %code.WriteLine(" Quit:s=""""  Quit:d=""""")
        Do %code.WriteLine(" ; Remove set-style edges")
        Do %code.WriteLine(" Kill ^PPR(""out"",s,d), ^PPR(""in"",d,s)")
        Do %code.WriteLine(" Set ^PPR(""deg"",s)=$Get(^PPR(""deg"",s))-1")
        Do %code.WriteLine(" If ^PPR(""deg"",s)'>0 {")
        Do %code.WriteLine("   Kill ^PPR(""deg"",s)")
        Do %code.WriteLine("   Set ^PPR(""dang"",s)=1")
        Do %code.WriteLine(" }")
        Do %code.WriteLine(" ; Rebuild packed outgoing list for src")
        Do %code.WriteLine(" Set L="""", nbr=""""")
        Do %code.WriteLine(" For { Set nbr=$Order(^PPR(""out"",s,nbr)) Quit:nbr=""""  Set L=L_$ListBuild(nbr) }")
        Do %code.WriteLine(" If L="""" Kill ^PPR(""outL"",s)")
        Do %code.WriteLine(" Else  Set ^PPR(""outL"",s)=L")
        Do %code.WriteLine(" ; Rebuild packed incoming list for dst")
        Do %code.WriteLine(" Set L="""", nbr=""""")
        Do %code.WriteLine(" For { Set nbr=$Order(^PPR(""in"",d,nbr)) Quit:nbr=""""  Set L=L_$ListBuild(nbr) }")
        Do %code.WriteLine(" If L="""" Kill ^PPR(""inL"",d)")
        Do %code.WriteLine(" Else  Set ^PPR(""inL"",d)=L")
    }
    Quit $$$OK
}

/// PURGE callback - invoked before index rebuild
///
/// Clears all ^PPR helper data to prepare for full rebuild.
/// Use before calling rebuild index operation or re-inserting data.
ClassMethod PurgeIndex() [ CodeMode = objectgenerator, ServerOnly = 1 ]
{
    If (%mode '= "method") {
        Do %code.WriteLine(" Kill ^PPR")
    }
    Quit $$$OK
}

/// Optional: Batch rebuild start (no-op for our use case)
ClassMethod SortBeginIndex() [ CodeMode = objectgenerator, ServerOnly = 1 ]
{
    If (%mode '= "method") {
        Do %code.WriteLine(" ; No batching needed - incremental updates work fine")
    }
    Quit $$$OK
}

/// Optional: Batch rebuild end (no-op for our use case)
ClassMethod SortEndIndex(pCommit As %Integer = 1) [ CodeMode = objectgenerator, ServerOnly = 1 ]
{
    If (%mode '= "method") {
        Do %code.WriteLine(" ; No batching needed - incremental updates work fine")
    }
    Quit $$$OK
}

}
