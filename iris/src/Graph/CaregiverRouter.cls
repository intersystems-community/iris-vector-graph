/// Caregiver Route Optimizer using Graph TSP
///
/// Use case: Home healthcare agency needs to optimize daily routes for caregivers
/// visiting multiple patients. Uses graph database to store patient locations and
/// travel times, then solves TSP to find optimal visit sequence.
///
/// Integration with Interoperability:
/// - Can be called from Business Process to optimize daily schedules
/// - Input: List of patient IDs to visit
/// - Output: Optimized route with travel times and ETAs
///
/// Example:
/// ```objectscript
/// Set patients = $ListBuild("patient:001", "patient:002", "patient:003")
/// Set sc = ##class(Graph.CaregiverRouter).OptimizeRoute(patients, .route, .totalTime)
/// ```
Class Graph.CaregiverRouter Extends %RegisteredObject
{

/// Optimize caregiver route using Greedy Nearest Neighbor TSP
///
/// Parameters:
/// - patientIds: $List of patient node IDs (e.g., "patient:001")
/// - route: Output $List of patient IDs in optimal visit order
/// - totalTime: Output total travel time in minutes
/// - startLocation: Optional starting location (default: first patient)
///
/// Returns: %Status
ClassMethod OptimizeRoute(
    patientIds As %List,
    Output route As %List,
    Output totalTime As %Float,
    startLocation As %String = "") As %Status
{
    Set route = ""
    Set totalTime = 0

    Try {
        // Validate input
        If $ListLength(patientIds) < 2 {
            Return $$$ERROR($$$GeneralError, "Need at least 2 patients to optimize route")
        }

        // Build graph of travel times between patients
        Set graph = ..BuildTravelTimeGraph(patientIds)
        If '$IsObject(graph) {
            Return $$$ERROR($$$GeneralError, "Failed to build travel time graph")
        }

        // Determine starting location
        Set start = startLocation
        If (start = "") {
            Set start = $ListGet(patientIds, 1)
        }

        // Solve TSP using greedy nearest neighbor
        Set sc = ..SolveGreedyTSP(graph, start, patientIds, .route, .totalTime)
        If $$$ISERR(sc) {
            Return sc
        }

        Return $$$OK

    } Catch ex {
        Return ex.AsStatus()
    }
}

/// Build graph of travel times from rdf_edges
///
/// Expects edges like:
/// - s: "patient:001"
/// - o_id: "patient:002"
/// - qualifiers: {"travel_time_minutes": 15, "distance_miles": 3.2}
ClassMethod BuildTravelTimeGraph(patientIds As %List) As %ArrayOfDataTypes [ Private ]
{
    Set graph = ##class(%ArrayOfDataTypes).%New()

    // Query all edges between patients
    Set sql = "SELECT s, o_id, qualifiers FROM rdf_edges WHERE s LIKE 'patient:%' AND o_id LIKE 'patient:%'"
    Set stmt = ##class(%SQL.Statement).%New()
    Do stmt.%Prepare(sql)
    Set rs = stmt.%Execute()

    While rs.%Next() {
        Set source = rs.%Get("s")
        Set target = rs.%Get("o_id")

        // Parse travel time from qualifiers JSON
        Set qualifiersJSON = rs.%Get("qualifiers")
        If (qualifiersJSON '= "") {
            Try {
                Set qualifiers = {}.%FromJSON(qualifiersJSON)
                Set travelTime = qualifiers."travel_time_minutes"

                // Store bidirectional edges (travel time is same both ways)
                Set key1 = source_"→"_target
                Set key2 = target_"→"_source
                Do graph.SetAt(travelTime, key1)
                Do graph.SetAt(travelTime, key2)

            } Catch parseEx {
                // Skip edges with invalid qualifiers
                Continue
            }
        }
    }

    Return graph
}

/// Solve TSP using Greedy Nearest Neighbor algorithm
/// Time complexity: O(n²) - suitable for daily caregiver routes (typically 5-20 patients)
ClassMethod SolveGreedyTSP(
    graph As %ArrayOfDataTypes,
    start As %String,
    patientIds As %List,
    Output route As %List,
    Output totalTime As %Float) As %Status [ Private ]
{
    Set route = ""
    Set totalTime = 0

    // Initialize
    Set current = start
    Set $List(route, *+1) = current

    // Track visited patients
    Set visited = ##class(%ArrayOfDataTypes).%New()
    Do visited.SetAt(1, current)

    // Visit remaining patients
    Set numPatients = $ListLength(patientIds)
    For i=1:1:(numPatients-1) {
        // Find nearest unvisited patient
        Set nearest = ""
        Set minTime = 999999

        For j=1:1:numPatients {
            Set patient = $ListGet(patientIds, j)

            // Skip if already visited
            Continue:visited.IsDefined(patient)

            // Get travel time from current to this patient
            Set edgeKey = current_"→"_patient
            Set travelTime = graph.GetAt(edgeKey)

            If (travelTime '= "") && (travelTime < minTime) {
                Set minTime = travelTime
                Set nearest = patient
            }
        }

        // Check if we found a reachable patient
        If (nearest = "") {
            // No path exists - return error
            Return $$$ERROR($$$GeneralError, "No path found from "_current_" to remaining patients")
        }

        // Add to route
        Set $List(route, *+1) = nearest
        Set totalTime = totalTime + minTime
        Do visited.SetAt(1, nearest)
        Set current = nearest
    }

    Return $$$OK
}

/// Get detailed route information including patient details and ETAs
///
/// Returns array of patient visit details:
/// - patientId: Patient node ID
/// - name: Patient name from rdf_props
/// - address: Patient address
/// - arrivalTime: Estimated arrival time
/// - serviceMinutes: Expected service duration
ClassMethod GetRouteDetails(
    route As %List,
    startTime As %TimeStamp = "",
    Output details As %ListOfDataTypes) As %Status
{
    Set details = ##class(%ListOfDataTypes).%New()

    // Default start time: current time
    If (startTime = "") {
        Set startTime = $ZDateTime($Horolog, 3)
    }

    Set currentTime = startTime
    Set numStops = $ListLength(route)

    For i=1:1:numStops {
        Set patientId = $ListGet(route, i)

        // Get patient details from rdf_props
        Set patientInfo = ..GetPatientInfo(patientId)

        // Calculate arrival time (previous travel time already added to currentTime)
        Set visitDetail = {}
        Set visitDetail.patientId = patientId
        Set visitDetail.name = patientInfo.name
        Set visitDetail.address = patientInfo.address
        Set visitDetail.arrivalTime = currentTime
        Set visitDetail.serviceMinutes = +patientInfo.serviceMinutes
        Set visitDetail.position = i

        Do details.Insert(visitDetail)

        // Update current time (add service duration + travel to next)
        Set currentTime = ..AddMinutes(currentTime, +patientInfo.serviceMinutes)

        If (i < numStops) {
            // Add travel time to next patient
            Set nextPatient = $ListGet(route, i+1)
            Set travelTime = ..GetTravelTime(patientId, nextPatient)
            Set currentTime = ..AddMinutes(currentTime, travelTime)
        }
    }

    Return $$$OK
}

/// Get patient information from rdf_props table
ClassMethod GetPatientInfo(patientId As %String) As %DynamicObject [ Private ]
{
    Set info = {}
    Set info.name = "Unknown"
    Set info.address = ""
    Set info.serviceMinutes = 30  // Default 30 minutes

    Set sql = "SELECT key, val FROM rdf_props WHERE s = ?"
    Set stmt = ##class(%SQL.Statement).%New()
    Do stmt.%Prepare(sql)
    Set rs = stmt.%Execute(patientId)

    While rs.%Next() {
        Set key = rs.%Get("key")
        Set val = rs.%Get("val")

        If (key = "name") {
            Set info.name = val
        } ElseIf (key = "address") {
            Set info.address = val
        } ElseIf (key = "service_minutes") {
            Set info.serviceMinutes = val
        }
    }

    Return info
}

/// Get travel time between two patients from graph
ClassMethod GetTravelTime(fromPatient As %String, toPatient As %String) As %Float [ Private ]
{
    Set sql = "SELECT qualifiers FROM rdf_edges WHERE s = ? AND o_id = ?"
    Set rs = ##class(%SQL.Statement).%ExecDirect(, sql, fromPatient, toPatient)

    If rs.%Next() {
        Try {
            Set qualifiers = {}.%FromJSON(rs.%Get("qualifiers"))
            Return +qualifiers."travel_time_minutes"
        } Catch {
            Return 15  // Default 15 minutes
        }
    }

    Return 15  // Default if no edge exists
}

/// Add minutes to a timestamp
ClassMethod AddMinutes(timestamp As %TimeStamp, minutes As %Integer) As %TimeStamp [ Private ]
{
    Set h = $ZDateH(timestamp, 3)
    Set newH = $ListBuild($List(h,1), $List(h,2) + (minutes * 60))
    Return $ZDateTime(newH, 3)
}

/// Example method for Interoperability Business Process
///
/// This shows how a Business Process could call the optimizer
ClassMethod OptimizeDailySchedule(
    caregiverId As %String,
    scheduleDate As %Date,
    Output optimizedRoute As %List,
    Output scheduleDetails As %ListOfDataTypes) As %Status
{
    Try {
        // 1. Get list of patients assigned to caregiver for this date
        Set patients = ..GetAssignedPatients(caregiverId, scheduleDate)

        If $ListLength(patients) = 0 {
            Return $$$ERROR($$$GeneralError, "No patients assigned for date")
        }

        // 2. Optimize route using TSP
        Set sc = ..OptimizeRoute(patients, .optimizedRoute, .totalTime)
        If $$$ISERR(sc) Return sc

        // 3. Get detailed schedule with ETAs
        Set startTime = scheduleDate_" 08:00:00"  // Start at 8 AM
        Set sc = ..GetRouteDetails(optimizedRoute, startTime, .scheduleDetails)
        If $$$ISERR(sc) Return sc

        // 4. Log optimization results
        Do ..LogOptimization(caregiverId, scheduleDate, totalTime, scheduleDetails)

        Return $$$OK

    } Catch ex {
        Return ex.AsStatus()
    }
}

/// Get patients assigned to caregiver on specific date
/// (This would query your scheduling system)
ClassMethod GetAssignedPatients(
    caregiverId As %String,
    scheduleDate As %Date) As %List [ Private ]
{
    // Example: Query scheduling table
    // In real system, this would query your patient scheduling database

    Set patients = ""

    Set sql = "SELECT DISTINCT s FROM rdf_labels WHERE label = 'Patient' AND s IN ("
    Set sql = sql_"SELECT s FROM rdf_props WHERE key = 'assigned_caregiver' AND val = ?)"

    Set stmt = ##class(%SQL.Statement).%New()
    Do stmt.%Prepare(sql)
    Set rs = stmt.%Execute(caregiverId)

    While rs.%Next() {
        Set $List(patients, *+1) = rs.%Get("s")
    }

    Return patients
}

/// Log optimization for audit trail
ClassMethod LogOptimization(
    caregiverId As %String,
    scheduleDate As %Date,
    totalTime As %Float,
    scheduleDetails As %ListOfDataTypes) [ Private ]
{
    Set log = {}
    Set log.caregiver = caregiverId
    Set log.date = scheduleDate
    Set log.totalTravelMinutes = totalTime
    Set log.numPatients = scheduleDetails.Count()
    Set log.optimizedAt = $ZDateTime($Horolog, 3)

    // In production, write to audit table or send to logging service
    Write "Optimized route for ", caregiverId, " on ", scheduleDate, !
    Write "  Patients: ", scheduleDetails.Count(), !
    Write "  Travel time: ", totalTime, " minutes", !
}

}
