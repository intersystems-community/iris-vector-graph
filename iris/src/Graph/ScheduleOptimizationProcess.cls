/// Business Process for Daily Caregiver Schedule Optimization
///
/// This Business Process:
/// 1. Receives schedule optimization request
/// 2. Calls Graph TSP optimizer to find optimal route
/// 3. Sends optimized schedule to caregiver via Business Operation
/// 4. Updates scheduling system
///
/// Typical flow:
/// - Triggered daily at 6 AM by scheduled task
/// - Or triggered by manual schedule change
/// - Optimizes all caregiver routes for the day
/// - Sends mobile app notifications with optimized routes
Class Graph.ScheduleOptimizationProcess Extends Ens.BusinessProcess
{

/// Request message containing schedule optimization parameters
Property Request As Graph.Messages.OptimizeScheduleRequest;

/// Response containing optimized route
Property Response As Graph.Messages.OptimizeScheduleResponse;

/// Main business process method
Method OnRequest(
    pRequest As Graph.Messages.OptimizeScheduleRequest,
    Output pResponse As Graph.Messages.OptimizeScheduleResponse) As %Status
{
    Try {
        Set pResponse = ##class(Graph.Messages.OptimizeScheduleResponse).%New()

        // Log incoming request
        $$$TRACE("Optimizing schedule for caregiver "_pRequest.CaregiverId_" on "_pRequest.ScheduleDate)

        // 1. Get patient assignments from scheduling system
        Set sc = ..CallSchedulingSystem(pRequest, .patients)
        If $$$ISERR(sc) Quit

        // 2. Call TSP optimizer
        Set sc = ##class(Graph.CaregiverRouter).OptimizeRoute(
            patients,
            .optimizedRoute,
            .totalTime
        )
        If $$$ISERR(sc) {
            Set pResponse.Success = 0
            Set pResponse.ErrorMessage = "Route optimization failed: "_$System.Status.GetErrorText(sc)
            Quit
        }

        // 3. Get detailed schedule with ETAs
        Set startTime = pRequest.ScheduleDate_" "_pRequest.StartTime
        Set sc = ##class(Graph.CaregiverRouter).GetRouteDetails(
            optimizedRoute,
            startTime,
            .scheduleDetails
        )
        If $$$ISERR(sc) Quit

        // 4. Send optimized schedule to caregiver
        Set sc = ..SendScheduleToCaregiver(pRequest.CaregiverId, scheduleDetails)
        If $$$ISERR(sc) Quit

        // 5. Update scheduling database
        Set sc = ..UpdateScheduleDatabase(pRequest.CaregiverId, optimizedRoute, scheduleDetails)
        If $$$ISERR(sc) Quit

        // 6. Build response
        Set pResponse.Success = 1
        Set pResponse.OptimizedRoute = optimizedRoute
        Set pResponse.TotalTravelMinutes = totalTime
        Set pResponse.NumPatients = $ListLength(optimizedRoute)
        Set pResponse.ScheduleDetails = ..SerializeSchedule(scheduleDetails)

        $$$TRACE("Successfully optimized route: "_$ListLength(optimizedRoute)_" patients, "_totalTime_" minutes travel")

        Return $$$OK

    } Catch ex {
        Set pResponse.Success = 0
        Set pResponse.ErrorMessage = "Exception: "_ex.DisplayString()
        Return ex.AsStatus()
    }
}

/// Call scheduling system to get patient assignments
Method CallSchedulingSystem(
    pRequest As Graph.Messages.OptimizeScheduleRequest,
    Output patients As %List) As %Status [ Private ]
{
    // In real implementation, this would call your scheduling database
    // via SQL or another Business Operation

    // Example: Send request to Scheduling BO
    Set msg = ##class(Ens.StringRequest).%New()
    Set msg.StringValue = "GetPatients:"_pRequest.CaregiverId_":"_pRequest.ScheduleDate

    Set sc = ..SendRequestSync("SchedulingOperation", msg, .response)
    If $$$ISERR(sc) Return sc

    // Parse patient list from response
    Set patients = ..ParsePatientList(response.StringValue)

    Return $$$OK
}

/// Send optimized schedule to caregiver (mobile app, email, SMS)
Method SendScheduleToCaregiver(
    caregiverId As %String,
    scheduleDetails As %ListOfDataTypes) As %Status [ Private ]
{
    // Build notification message
    Set msg = ##class(Graph.Messages.ScheduleNotification).%New()
    Set msg.CaregiverId = caregiverId
    Set msg.RouteDetails = ..SerializeSchedule(scheduleDetails)

    // Send via mobile notification service
    Set sc = ..SendRequestAsync("MobileNotificationOperation", msg)
    If $$$ISERR(sc) Return sc

    $$$TRACE("Sent optimized schedule to caregiver "_caregiverId)

    Return $$$OK
}

/// Update scheduling database with optimized route
Method UpdateScheduleDatabase(
    caregiverId As %String,
    route As %List,
    scheduleDetails As %ListOfDataTypes) As %Status [ Private ]
{
    // Send update to scheduling database operation
    Set msg = ##class(Graph.Messages.UpdateScheduleRequest).%New()
    Set msg.CaregiverId = caregiverId
    Set msg.OptimizedRoute = route
    Set msg.RouteDetails = ..SerializeSchedule(scheduleDetails)

    Set sc = ..SendRequestSync("SchedulingDatabaseOperation", msg, .response)
    If $$$ISERR(sc) Return sc

    Return $$$OK
}

/// Convert schedule details to JSON string
Method SerializeSchedule(scheduleDetails As %ListOfDataTypes) As %String [ Private ]
{
    Set array = []

    For i=1:1:scheduleDetails.Count() {
        Set detail = scheduleDetails.GetAt(i)
        Do array.%Push(detail)
    }

    Return array.%ToJSON()
}

/// Parse patient list from string
Method ParsePatientList(patientString As %String) As %List [ Private ]
{
    Set patients = ""
    For i=1:1:$Length(patientString, ",") {
        Set patientId = $Piece(patientString, ",", i)
        If (patientId '= "") {
            Set $List(patients, *+1) = patientId
        }
    }
    Return patients
}

/// XData block defining process flow (for BPL visual designer)
XData BPL [ XMLNamespace = "http://www.intersystems.com/bpl" ]
{
<process language='objectscript' request='Graph.Messages.OptimizeScheduleRequest' response='Graph.Messages.OptimizeScheduleResponse'>
<context>
<property name='PatientList' type='%String' instantiate='0' />
<property name='OptimizedRoute' type='%String' instantiate='0' />
</context>
<sequence>

<!-- Get patient assignments -->
<call name='GetPatients' target='SchedulingOperation' async='0'>
<request type='Ens.StringRequest'>
<assign property='callrequest.StringValue' value='"GetPatients:"_request.CaregiverId_":"_request.ScheduleDate' />
</request>
<response type='Ens.StringResponse'>
<assign property='context.PatientList' value='callresponse.StringValue' />
</response>
</call>

<!-- Code block to optimize route -->
<code>
<![CDATA[
    // Parse patient list
    Set patients = ..ParsePatientList(context.PatientList)

    // Optimize using TSP
    Set sc = ##class(Graph.CaregiverRouter).OptimizeRoute(patients, .route, .totalTime)
    If $$$ISERR(sc) Return sc

    Set context.OptimizedRoute = ..ListToString(route)
    Set response.TotalTravelMinutes = totalTime
]]>
</code>

<!-- Send schedule to caregiver -->
<call name='NotifyCaregiver' target='MobileNotificationOperation' async='1'>
<request type='Graph.Messages.ScheduleNotification'>
<assign property='callrequest.CaregiverId' value='request.CaregiverId' />
<assign property='callrequest.RouteDetails' value='context.OptimizedRoute' />
</request>
</call>

<!-- Update database -->
<call name='UpdateDatabase' target='SchedulingDatabaseOperation' async='0'>
<request type='Graph.Messages.UpdateScheduleRequest'>
<assign property='callrequest.CaregiverId' value='request.CaregiverId' />
<assign property='callrequest.OptimizedRoute' value='context.OptimizedRoute' />
</request>
</call>

<assign property='response.Success' value='1' />

</sequence>
</process>
}

}
