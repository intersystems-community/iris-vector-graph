/// Test subclass of TemporalIndex with TSUNIT="ms" and BUCKETMS=300000.
/// Used only in integration tests (spec 204 Phase 6 T046-T048).
/// Never call this from production code — use Graph.KG.TemporalIndex directly.
Class Graph.KG.TemporalIndexMS Extends Graph.KG.TemporalIndex
{

Parameter TSUNIT As %String = "ms";

Parameter BUCKETMS As %Integer = 300000;

/// Return the edge count stored in ^KG("bucket", bucket, node).
/// Used by tests to verify bucket key assignment without embedded Python.
ClassMethod GetBucketCount(bucket As %Integer, node As %String) As %Integer
{
	Return +$Get(^KG("bucket", bucket, node), 0)
}

}
