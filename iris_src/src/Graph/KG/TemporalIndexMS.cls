/// Supported ms-precision subclass of TemporalIndex.
/// TSUNIT="ms" with 300-second (300000ms) bucket width.
/// Use this class directly for millisecond-precision temporal edge ingest and analytics.
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
