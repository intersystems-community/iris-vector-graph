/// Test-only: run ObjectScript lines in a namespace the way `iris session` ran them, over TCP (scripts/dev/tcp_setup.py
/// `run`), with no `iris session`, which Cortex XDR kills. Loaded into %SYS with IVGDev.Setup; never shipped.
/// Start JOBs a fresh process, as each `iris session` was one: it switches to the namespace, sends what the lines
/// Write to a file, and runs each line with Xecute in turn. A line that errors writes the error, as the terminal
/// printed it, and the next line runs. Halt (in a line, or the end of the lines) ends the process. The lines, the
/// state and the output file's name live in ^IRIS.TempIVGRun(id), which every namespace maps to IRISTEMP.
Class IVGDev.Run [ Abstract ]
{

/// Start a process running the lines ^IRIS.TempIVGRun(pId,"line",1..n) in namespace pNamespace; answers its pid,
/// or "FAIL <step>: <text>". %SYS is refused unless pAllowSys is 1. The output file is
/// <manager directory>/Temp/ivgrun_<pId>.txt.
ClassMethod Start(pId As %String, pNamespace As %String, pAllowSys As %Boolean = 0) As %String
{
    If ($ZConvert(pNamespace, "U") = "%SYS") && 'pAllowSys {
        Return "FAIL ns: %SYS refused (pass allow_sys)"
    }
    If '##class(%SYS.Namespace).Exists(pNamespace) {
        Return "FAIL ns: namespace "_pNamespace_" does not exist"
    }
    If '$Data(^IRIS.TempIVGRun(pId, "line")) {
        Return "FAIL lines: no lines stored under "_pId
    }
    If $Translate(pId, "abcdefghijklmnopqrstuvwxyz0123456789") '= "" {
        Return "FAIL id: "_pId_" is not lower-case alphanumeric"
    }
    Set tDir = ##class(%File).NormalizeDirectory($System.Util.ManagerDirectory()_"Temp")
    Do ##class(%File).CreateDirectoryChain(tDir)
    Set tFile = tDir_"ivgrun_"_pId_".txt"
    Set ^IRIS.TempIVGRun(pId, "file") = tFile
    Set ^IRIS.TempIVGRun(pId, "ns") = pNamespace
    Job ##class(IVGDev.Run).Child(pId)::10
    If '$Test {
        Return "FAIL job: the process did not start in 10 s"
    }
    Set ^IRIS.TempIVGRun(pId, "pid") = $ZChild
    Return $ZChild
}

/// The started process. Every line runs, in order, until one halts; "end" is set only when none did.
ClassMethod Child(pId As %String)
{
    Set ^IRIS.TempIVGRun(pId, "started") = 1
    Set tFile = ^IRIS.TempIVGRun(pId, "file")
    Set tNS = ^IRIS.TempIVGRun(pId, "ns")
    Set tCount = 0
    Set tKey = ""
    For {
        Set tKey = $Order(^IRIS.TempIVGRun(pId, "line", tKey), 1, tLine)
        Quit:tKey=""
        Set tCount = tCount + 1
        Set tLines(tCount) = tLine
    }
    Open tFile:("WNS":/TRANSLATE=1:/IOTABLE="UTF8"):5
    If '$Test {
        Set ^IRIS.TempIVGRun(pId, "error") = "could not open "_tFile
        Return
    }
    Use tFile
    Try {
        Set $Namespace = tNS
    } Catch tEx {
        Write tEx.DisplayString(), !
        Close tFile
        Set ^IRIS.TempIVGRun(pId, "error") = "namespace "_tNS_": "_tEx.DisplayString()
        Return
    }
    For tI = 1:1:tCount {
        Set ^IRIS.TempIVGRun(pId, "at") = tI
        Try {
            Xecute tLines(tI)
        } Catch tEx {
            // as the terminal printed it: <NAME>, where (not this runner's own line), *data
            Set tText = tEx.DisplayString()
            If tEx.%IsA("%Exception.SystemException") {
                Set tWhere = $Select(tEx.Location [ "^IVGDev.Run": "", 1: tEx.Location)
                Set tText = tEx.Name_tWhere_$Select(tEx.Data '= "": " *"_tEx.Data, 1: "")
            }
            Write:$X>0 !
            Write tText, !
            Set ^IRIS.TempIVGRun(pId, "failed", tI) = tText
        }
        // a line may Use another device; what it writes after that line is still this run's output
        Use tFile
        // the terminal's next prompt started on a new line: `write 1` then `write 0` printed "1", "0" on two lines
        Write:$X>0 !
    }
    Close tFile
    Set ^IRIS.TempIVGRun(pId, "end") = 1
    Return
}

/// "running", "ended" (every line ran) or "halted" (the process is gone and a line, or an error outside one, ended
/// it before the last line finished). Until the process has marked itself started it is "running": a process just
/// JOBbed may not be listed yet, and would read as gone.
ClassMethod State(pId As %String) As %String
{
    Set tPid = $Get(^IRIS.TempIVGRun(pId, "pid"))
    If '$Get(^IRIS.TempIVGRun(pId, "started")) {
        Return "running"
    }
    If $Get(^IRIS.TempIVGRun(pId, "end")) {
        // the process sets "end" just before it quits: wait for it to be gone, so its file is closed
        Return $Select(..Alive(tPid): "running", 1: "ended")
    }
    Return $Select(..Alive(tPid): "running", 1: "halted")
}

ClassMethod Alive(pPid As %String) As %Boolean [ Private ]
{
    If pPid = "" {
        Return 0
    }
    Return ($Data(^$JOB(pPid)) > 0)
}

/// The bytes the process wrote (UTF-8), from byte pFrom (1 = the start), at most pMax bytes: counted in bytes, so a
/// caller reading in chunks never splits or repeats a character's bytes.
ClassMethod Output(pId As %String, pFrom As %Integer = 1, pMax As %Integer = 1000000) As %String
{
    Set tFile = $Get(^IRIS.TempIVGRun(pId, "file"))
    If (tFile = "") || '##class(%File).Exists(tFile) {
        Return ""
    }
    Set tStream = ##class(%Stream.FileBinary).%New()
    Set tSC = tStream.LinkToFile(tFile)
    If $$$ISERR(tSC) {
        Return ""
    }
    If pFrom > 1 {
        Do tStream.MoveTo(pFrom)
    }
    Set tLen = pMax
    Return tStream.Read(.tLen)
}

/// The lines that errored, "line<tab>error" each, separated by $Char(10).
ClassMethod Failures(pId As %String) As %String
{
    Set tOut = ""
    Set tKey = ""
    For {
        Set tKey = $Order(^IRIS.TempIVGRun(pId, "failed", tKey), 1, tText)
        Quit:tKey=""
        Set tOut = tOut_$Select(tOut = "": "", 1: $Char(10))_tKey_$Char(9)_tText
    }
    Return tOut
}

/// The setup error of the process (it could not open its file or reach the namespace); "" when none.
ClassMethod SetupError(pId As %String) As %String
{
    Return $Get(^IRIS.TempIVGRun(pId, "error"))
}

/// End the process if it still runs (a timeout), delete its file and its ^IRIS.TempIVGRun entry.
ClassMethod Cleanup(pId As %String) As %String
{
    Set tPid = $Get(^IRIS.TempIVGRun(pId, "pid"))
    If (tPid '= "") && ..Alive(tPid) {
        Do $System.Process.Terminate(tPid)
    }
    Set tFile = $Get(^IRIS.TempIVGRun(pId, "file"))
    If (tFile '= "") && ##class(%File).Exists(tFile) {
        Do ##class(%File).Delete(tFile)
    }
    Kill ^IRIS.TempIVGRun(pId)
    Return ""
}

}
