/// Graph.KG.FHIRGraphSyncTask - one Task Manager task per FHIR graph (spec 231 FR-011).
///
/// Created and updated by Graph.KG.FHIRGraph.Schedule, deleted by Unschedule. Each run
/// is one SyncOnce. A run that finds the previous one still holding the graph's lock
/// returns busy, which is not a failure: the next run picks up where it left off.
Class Graph.KG.FHIRGraphSyncTask Extends %SYS.Task.Definition
{

Parameter TaskName = "IVG FHIR Graph Sync";

/// The FHIR graph to sync, e.g. fhir:IVGFHIR:X0001.
Property GraphId As %String(MAXLEN = 256) [ Required ];

Method OnTask() As %Status
{
	Set tSC = $$$OK
	Try {
		Set tOut = ##class(Graph.KG.FHIRGraph).SyncOnce(..GraphId)
		Set tRes = ##class(%DynamicAbstractObject).%FromJSON(tOut)
		If tRes.status = "error" {
			Set tSC = $$$ERROR($$$GeneralError, "FHIR graph sync " _ ..GraphId _ ": " _ tRes.error)
		}
	} Catch tEx {
		Set tSC = tEx.AsStatus()
	}
	Quit tSC
}

}
