/// Request message for schedule optimization
Class Graph.Messages.OptimizeScheduleRequest Extends Ens.Request
{

/// Caregiver ID
Property CaregiverId As %String(MAXLEN = 100) [ Required ];

/// Schedule date (YYYY-MM-DD format)
Property ScheduleDate As %Date [ Required ];

/// Start time for route (HH:MM:SS format, default 08:00:00)
Property StartTime As %String [ InitialExpression = "08:00:00" ];

/// Optional: Force recalculation even if route already optimized today
Property ForceRecalculation As %Boolean [ InitialExpression = 0 ];

Storage Default
{
<Data name="OptimizeScheduleRequestDefaultData">
<Subscript>"OptimizeScheduleRequest"</Subscript>
<Value name="1">
<Value>CaregiverId</Value>
</Value>
<Value name="2">
<Value>ScheduleDate</Value>
</Value>
<Value name="3">
<Value>StartTime</Value>
</Value>
<Value name="4">
<Value>ForceRecalculation</Value>
</Value>
</Data>
<DefaultData>OptimizeScheduleRequestDefaultData</DefaultData>
<Type>%Storage.Persistent</Type>
}

}
