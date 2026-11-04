/// Graph.KG.EmbedQueueTask - the Task Manager task that works the embedding queue (spec 199).
///
/// Created by Graph.KG.EmbedQueue.StartBackgroundTask, one per namespace, on demand. Each run
/// calls Graph.KG.EmbedQueue.ProcessBatch, BatchSize entries at a time, until no entry is
/// PENDING or a batch changes nothing. Before 4.6.0 StartBackgroundTask named this class and
/// no release shipped it, so the task could not run (R087).
Class Graph.KG.EmbedQueueTask Extends %SYS.Task.Definition
{

Parameter TaskName = "IVG EmbedQueue Worker";

/// Entries per ProcessBatch call.
Property BatchSize As %Integer [ InitialExpression = 100 ];

Method OnTask() As %Status
{
	Set tSC = $$$OK
	Try {
		Set tSize = $Select(..BatchSize > 0: ..BatchSize, 1: 100)
		For {
			Set tBefore = ##class(Graph.KG.EmbedQueue).PendingCount()
			Quit:tBefore=0
			Set tRes = ##class(%DynamicAbstractObject).%FromJSON(##class(Graph.KG.EmbedQueue).ProcessBatch(tSize))
			// A batch that settled no entry would settle none on the next pass either
			Quit:(tRes.processed + tRes.errors)=0
			Quit:##class(Graph.KG.EmbedQueue).PendingCount()>=tBefore
		}
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

}
