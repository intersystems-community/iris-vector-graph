/// Response message for schedule optimization
Class Graph.Messages.OptimizeScheduleResponse Extends Ens.Response
{

/// Success flag
Property Success As %Boolean;

/// Optimized route as $List of patient IDs
Property OptimizedRoute As %List;

/// Total travel time in minutes
Property TotalTravelMinutes As %Float;

/// Number of patients in route
Property NumPatients As %Integer;

/// JSON string containing detailed schedule (patient names, addresses, ETAs)
Property ScheduleDetails As %String(MAXLEN = "");

/// Error message if optimization failed
Property ErrorMessage As %String(MAXLEN = 500);

Storage Default
{
<Data name="OptimizeScheduleResponseDefaultData">
<Subscript>"OptimizeScheduleResponse"</Subscript>
<Value name="1">
<Value>Success</Value>
</Value>
<Value name="2">
<Value>OptimizedRoute</Value>
</Value>
<Value name="3">
<Value>TotalTravelMinutes</Value>
</Value>
<Value name="4">
<Value>NumPatients</Value>
</Value>
<Value name="5">
<Value>ScheduleDetails</Value>
</Value>
<Value name="6">
<Value>ErrorMessage</Value>
</Value>
</Data>
<DefaultData>OptimizeScheduleResponseDefaultData</DefaultData>
<Type>%Storage.Persistent</Type>
}

}
