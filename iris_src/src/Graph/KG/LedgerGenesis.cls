/// Spec 213 — genesis capture and adoption (R8).
///
/// Capture(): scans the canonical tables in a deterministic order and emits mutation
/// records describing the whole structural graph, assigning a statement identity to
/// every relationship. Runs inside Graph.KG.Ledger.Enable's transaction.
///
/// Adopt(): writes records for a client-computed divergence WITHOUT touching the
/// canonical tables (the mutations already happened as unrecorded writes). Assigns
/// statement identities to adopted live relationships and closes statements whose
/// rows disappeared. Runs inside Graph.KG.Ledger.Commit's transaction (kind=adoption).
Class Graph.KG.LedgerGenesis Extends %RegisteredObject
{

ClassMethod Capture(pSeq As %Integer, pSchema As %String, ByRef pRecords, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    Set pRecords = $Get(pRecords, 0)
    // nodes
    Set tRS = ##class(Graph.KG.LedgerApply).Run("SELECT node_id FROM " _ pSchema _ ".nodes ORDER BY node_id")
    If tRS.%SQLCODE < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "genesis node scan failed: " _ tRS.%Message)
    While tRS.%Next() {
        Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "create_node", "node", tRS.%GetData(1), "", "N", "S", "", "genesis")
    }
    // labels
    Set tRS = ##class(Graph.KG.LedgerApply).Run("SELECT s, label FROM " _ pSchema _ ".rdf_labels ORDER BY s, label")
    If tRS.%SQLCODE < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "genesis label scan failed: " _ tRS.%Message)
    While tRS.%Next() {
        Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "add_label", "node", tRS.%GetData(1), tRS.%GetData(2), "N", "S", "", "genesis")
    }
    // properties (reserved names excluded, FR-014b)
    Set tRS = ##class(Graph.KG.LedgerApply).Run("SELECT s, ""key"", val FROM " _ pSchema _ ".rdf_props ORDER BY s, ""key""")
    If tRS.%SQLCODE < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "genesis prop scan failed: " _ tRS.%Message)
    While tRS.%Next() {
        Set tKey = tRS.%GetData(2)
        If ##class(Graph.KG.LedgerApply).IsReserved(tKey) Continue
        Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "set_prop", "node", tRS.%GetData(1), tKey, "N", "S" _ tRS.%GetData(3), "", "genesis")
    }
    // relationships
    Set tRS = ##class(Graph.KG.LedgerApply).Run("SELECT s, p, o_id, graph_id, qualifiers FROM " _ pSchema _ ".rdf_edges ORDER BY s, p, o_id, graph_id")
    If tRS.%SQLCODE < 0 Return ##class(Graph.KG.Ledger).Err("inconsistent", "genesis edge scan failed: " _ tRS.%Message)
    While tRS.%Next() {
        Set tS = tRS.%GetData(1), tP = tRS.%GetData(2), tO = tRS.%GetData(3), tG = tRS.%GetData(4)
        Set tQuals = ##class(Graph.KG.LedgerApply).ParseQuals(tRS.%GetData(5))
        Set tStmt = ##class(Graph.KG.LedgerApply).AllocStmt(pSeq, tS, tP, tO, tG, .pFirst, .pLast)
        Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "create_rel", "rel", tStmt, "", "N", "S" _ ##class(Graph.KG.LedgerApply).TupleJson(tS, tP, tO, tG), tG, "genesis")
        Kill tKeys
        Set tIter = tQuals.%GetIterator()
        While tIter.%GetNext(.tK, .tV) { Set tKeys(tK) = "" _ tV }
        Set tK = ""
        For {
            Set tK = $Order(tKeys(tK))
            Quit:tK=""
            Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "set_qual", "rel", tStmt, tK, "N", "S" _ tKeys(tK), tG, "genesis")
        }
    }
    Return ""
}

/// pItems: array of record-shaped objects
///   {"op","entity_kind","entity_id","attr","prior","new","graph"}
/// For create_rel the entity_id is ignored and "new" must be the tuple JSON; the server
/// assigns the identity. For delete_rel the entity_id is the statement identity to close.
ClassMethod Adopt(pItems As %DynamicArray, pSeq As %Integer, pSchema As %String, ByRef pRecords, ByRef pFirst As %String, ByRef pLast As %String) As %String
{
    Set pRecords = $Get(pRecords, 0)
    If '$IsObject(pItems) Return ##class(Graph.KG.Ledger).Err("failed_op", "adoption payload must be an array", "", 0)
    Kill tStmtByTuple
    For tI = 0:1:(pItems.%Size() - 1) {
        Set tItem = pItems.%Get(tI)
        If '$IsObject(tItem) Return ##class(Graph.KG.Ledger).Err("failed_op", "adoption item is not an object", "", tI)
        Set tOp = ##class(Graph.KG.Ledger).MetaGet(tItem, "op", "")
        Set tKind = ##class(Graph.KG.Ledger).MetaGet(tItem, "entity_kind", "")
        Set tId = ##class(Graph.KG.Ledger).MetaGet(tItem, "entity_id", "")
        Set tAttr = ##class(Graph.KG.Ledger).MetaGet(tItem, "attr", "")
        Set tG = ##class(Graph.KG.Ledger).MetaGet(tItem, "graph", "")
        Set tPrior = $Select(tItem.%IsDefined("prior") && (tItem.%GetTypeOf("prior") '= "null"): "S" _ tItem.%Get("prior"), 1: "N")
        Set tNew = $Select(tItem.%IsDefined("new") && (tItem.%GetTypeOf("new") '= "null"): "S" _ tItem.%Get("new"), 1: "N")
        If tOp = "create_rel" {
            Set tT = {}.%FromJSON($Extract(tNew, 2, *))
            Set tS = ##class(Graph.KG.Ledger).MetaGet(tT, "s", ""), tP = ##class(Graph.KG.Ledger).MetaGet(tT, "p", "")
            Set tO = ##class(Graph.KG.Ledger).MetaGet(tT, "o", ""), tTG = ##class(Graph.KG.Ledger).MetaGet(tT, "graph", "")
            Set tStmt = $Get(^IVG.Ledger("tuple", tS, tP, tO, ##class(Graph.KG.Ledger).GKey(tTG)), "")
            If tStmt = "" Set tStmt = ##class(Graph.KG.LedgerApply).AllocStmt(pSeq, tS, tP, tO, tTG, .pFirst, .pLast)
            Set tStmtByTuple(tS, tP, tO, ##class(Graph.KG.Ledger).GKey(tTG)) = tStmt
            Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "create_rel", "rel", tStmt, "", "N", tNew, tTG, "adoption")
            Continue
        }
        If tOp = "delete_rel" {
            Set tRow = $Get(^IVG.Ledger("stmt", tId), "")
            If tRow '= "" {
                Kill ^IVG.Ledger("tuple", $ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), ##class(Graph.KG.Ledger).GKey($ListGet(tRow, 4)))
                Set ^IVG.Ledger("stmt", tId) = $ListBuild($ListGet(tRow, 1), $ListGet(tRow, 2), $ListGet(tRow, 3), $ListGet(tRow, 4), $ListGet(tRow, 5), pSeq)
            }
            Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, "delete_rel", "rel", tId, "", tPrior, "N", tG, "adoption")
            Continue
        }
        If (tKind = "rel") && ((tOp = "set_qual") || (tOp = "remove_qual")) {
            // entity_id may be a statement id or a tuple JSON for a relationship adopted in this pass
            If $Extract(tId) = "{" {
                Set tT = {}.%FromJSON(tId)
                Set tS = ##class(Graph.KG.Ledger).MetaGet(tT, "s", ""), tP = ##class(Graph.KG.Ledger).MetaGet(tT, "p", "")
                Set tO = ##class(Graph.KG.Ledger).MetaGet(tT, "o", ""), tTG = ##class(Graph.KG.Ledger).MetaGet(tT, "graph", "")
                Set tId = $Get(tStmtByTuple(tS, tP, tO, ##class(Graph.KG.Ledger).GKey(tTG)), $Get(^IVG.Ledger("tuple", tS, tP, tO, ##class(Graph.KG.Ledger).GKey(tTG)), ""))
                If tId = "" Return ##class(Graph.KG.Ledger).Err("failed_op", "adoption qualifier change names an unknown relationship", "", tI)
            }
        }
        Do ##class(Graph.KG.LedgerApply).Rec(.pRecords, tOp, tKind, tId, tAttr, tPrior, tNew, tG, "adoption")
    }
    Return ""
}

}
