/// Supported ms-precision subclass of TemporalIndex.
/// TSUNIT="ms" with 300-second (300000ms) bucket width.
/// Use this class directly for millisecond-precision temporal edge ingest and analytics.
Class Graph.KG.TemporalIndexMS Extends Graph.KG.TemporalIndex
{

Parameter TSUNIT As %String = "ms";

Parameter BUCKETMS As %Integer = 300000;

/// Return the edge count stored in ^KG("bucket", graphKey, bucket, node).
/// graphId="" = default graph (key 0). Used by tests to verify bucket key assignment.
ClassMethod GetBucketCount(graphId As %String = "", bucket As %Integer, node As %String) As %Integer
{
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
	Return +$Get(^KG("bucket", tGKey, bucket, node), 0)
}

}
