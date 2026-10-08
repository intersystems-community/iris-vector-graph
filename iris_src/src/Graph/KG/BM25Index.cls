/// Graph.KG.BM25Index — Pure ObjectScript BM25 lexical search index.
///
/// Implements Okapi BM25 (Robertson, 1994) over ^BM25Idx globals.
/// No SQL tables. No iFind Enterprise license required.
///
/// Storage (schema 2; spec 044 amendment 2026-10-08, ADR-0007):
///   ^BM25Idx(name, "cfg", "schema")     integer — 2; absent means schema 1
///   ^BM25Idx(name, "cfg", "N")          integer — document count
///   ^BM25Idx(name, "cfg", "totalLen")   integer — total tokens; avgdl = totalLen / N
///   ^BM25Idx(name, "cfg", "k1")         float   — BM25 k1 parameter
///   ^BM25Idx(name, "cfg", "b")          float   — BM25 b parameter
///   ^BM25Idx(name, "cfg", "vocab_size") integer — distinct token count
///   ^BM25Idx(name, "cfg", "upgrading")  1 while Upgrade runs; writers are refused
///   ^BM25Idx(name, "df",   term)        integer — documents containing the term
///   ^BM25Idx(name, "doc",  docId)       $List   — the document's distinct tokens
///   ^BM25Idx(name, "tf",   term, docId) integer — term frequency (term-first!)
///   ^BM25Idx(name, "len",  docId)       integer — document token count
///
/// IDF is computed at query time from df and N. A schema-1 index (pre 2026-10-08) has no
/// df, doc or totalLen, a stored "idf" per term and a stored "avgdl"; Search reads both
/// shapes, Insert and Delete refuse a schema-1 index until Upgrade has run.
///
/// Writers (Insert, Delete) take an index-level lock and run in a transaction with plain
/// Set, not $Increment: $Increment is not rolled back, so a failed write would leave the
/// counters wrong with nothing to detect it.
///
/// Stopwords: common English function words stored in ^BM25Idx("__stop__", word)
/// at class-load time. Filtered out during Tokenize to reduce IDF noise.
///
/// Usage:
///   set result = ##class(Graph.KG.BM25Index).Build("ncit", $ListBuild("name","definition"))
///   set hits   = ##class(Graph.KG.BM25Index).Search("ncit", "ankylosing spondylitis", 10)
Class Graph.KG.BM25Index Extends %RegisteredObject
{

// ================================================================
// InitStopwords — populate ^BM25Idx("__stop__") once per process
// ================================================================

ClassMethod InitStopwords() [ Private ]
{
    If $Data(^BM25Idx("__stop__", "the")) Return
    Set words = "a,an,the,and,or,but,in,on,at,to,for,of,with,by,from,is,are,was,were,be,been,being,have,has,had,do,does,did,will,would,could,should,may,might,shall,this,that,these,those,it,its,i,we,you,he,she,they,them,their,our,your,his,her,not,no,nor,so,yet,both,either,neither,each,all,any,such,as,if,than,then,there,here,when,where,which,who,what,how"
    Set n = $LENGTH(words, ",")
    For i = 1:1:n {
        Set w = $PIECE(words, ",", i)
        Set ^BM25Idx("__stop__", w) = 1
    }
}

// ================================================================
// Tokenize — private helper used by Build, Search, Insert, Delete
// ================================================================

ClassMethod Tokenize(text As %String) As %List [ Private ]
{
    Do ..InitStopwords()
    Set lower = $ZCONVERT(text, "L")
    // Try iFind tokenizer first (better punctuation handling, base IRIS)
    Set raw = ""
    Try {
        Set raw = ##class(%iFind.Utils).Analyze(lower, "en", 1)
    } Catch {}
    If raw = "" {
        // Fallback: iterate chars, accumulate alphanumeric runs
        Set tok = ""
        For i = 1:1:$LENGTH(lower) {
            Set c = $EXTRACT(lower, i)
            If c?1AN {
                Set tok = tok_c
            } ElseIf tok '= "" {
                Set raw = raw_$LISTBUILD(tok)
                Set tok = ""
            }
        }
        If tok '= "" Set raw = raw_$LISTBUILD(tok)
    }
    // Filter stopwords
    Set result = "", tIdx = 0
    While $LISTNEXT(raw, tIdx, tok) {
        If tok = "" Continue
        If $Data(^BM25Idx("__stop__", tok)) Continue
        Set result = result_$LISTBUILD(tok)
    }
    Return result
}

// ================================================================
// Build — index all nodes over specified text properties
// ================================================================

/// pGraph is the graph whose nodes this index covers. "" is the default graph,
/// never "every graph": an index built over two graphs merged the same node_id
/// from both into one document and answered a scoped search with the other
/// graph's nodes (spec 230 FR-009).
ClassMethod Build(name As %String, propsCSV As %String, k1 As %Double = 1.5, b As %Double = 0.75, pGraph As %String = "") As %String
{
    Kill ^BM25Idx(name)

    // Build an IN-list literal from propsCSV for the single batch query.
    // e.g. "name,definition" → "'name','definition'"
    Set inList = ""
    Set nProps = $LENGTH(propsCSV, ",")
    For pi = 1:1:nProps {
        Set propKey = $ZSTRIP($PIECE(propsCSV, ",", pi), "<>W")
        If propKey = "" Continue
        Set escaped = $Replace(propKey, "'", "''")
        Set inList = inList _ $Select(inList="":"", 1:",") _ "'" _ escaped _ "'"
    }
    If inList = "" Return "{""indexed"":0,""avgdl"":0,""vocab_size"":0}"

    // One query fetches all (node_id, val) pairs for the requested properties.
    // Ordered by node_id so we can accumulate per-doc by scanning in sequence.
    //
    // Both sides of the join carry the graph. Restricting only the driving table
    // would still pull the other graph's property rows onto this graph's node,
    // because a node_id is unique per graph and not per namespace since 227.
    //
    // COALESCE on both sides of each comparison: graph_id is nullable on an
    // installation upgraded by ADD COLUMN, and an empty parameter reaches IRIS as
    // SQL NULL, so a bare `= ?` for the default graph matches nothing.
    Set sql = "SELECT n.node_id, p.val FROM Graph_KG.nodes n "
            _ "LEFT JOIN Graph_KG.rdf_props p ON p.s = n.node_id "
            _ "AND COALESCE(p.graph_id, '') = COALESCE(n.graph_id, '') "
            _ "AND p.key IN ("_inList_") "
            _ "WHERE COALESCE(n.graph_id, '') = COALESCE(?, '') "
            _ "ORDER BY n.node_id"
    Set stmt = ##class(%SQL.Statement).%New()
    Do stmt.%Prepare(sql)
    Set rs = stmt.%Execute(pGraph)

    Set N = 0
    Set totalLen = 0
    Set vocabSize = 0
    Set curDoc = ""
    Set docText = ""

    While rs.%Next() {
        Set docId = rs.%Get("node_id")
        Set val = rs.%GetData(2)  // p.val (NULL when no matching prop row)

        If docId '= curDoc {
            // Flush previous doc
            If curDoc '= "" {
                Do ..IndexDoc(name, curDoc, docText, .N, .totalLen, .vocabSize)
            }
            Set curDoc = docId
            Set docText = $Select(val="":"", 1:val)
        } Else {
            If val '= "" Set docText = docText _ " " _ val
        }
    }
    // Flush last doc
    If curDoc '= "" {
        Do ..IndexDoc(name, curDoc, docText, .N, .totalLen, .vocabSize)
    }

    // df and doc were written as each document was indexed; nothing is recounted here.
    Set ^BM25Idx(name, "cfg", "N") = N
    Set ^BM25Idx(name, "cfg", "totalLen") = totalLen
    Set ^BM25Idx(name, "cfg", "k1") = k1
    Set ^BM25Idx(name, "cfg", "b") = b
    Set ^BM25Idx(name, "cfg", "vocab_size") = vocabSize
    // The global is keyed by index name alone, so the graph an index covers is
    // recoverable only from here — Info reports it, and a caller comparing two
    // indexes has no other way to tell which graph each one answers for.
    Set ^BM25Idx(name, "cfg", "graph") = pGraph
    // Last: a reader that sees schema 2 finds everything schema 2 promises.
    Set ^BM25Idx(name, "cfg", "schema") = 2
    Set avgdl = $Select(N > 0: totalLen / N, 1: 0)

    Set graphJSON = $Replace($Replace(pGraph, "\", "\\"), """", "\""")
    Return "{""indexed"":"_N_",""avgdl"":"_avgdl_",""vocab_size"":"_vocabSize_",""graph"":"""_graphJSON_"""}"
}

// Accumulate tf, df and the token record for one document — shared by Build batch loop.
ClassMethod IndexDoc(name As %String, docId As %String, docText As %String, ByRef N As %Integer, ByRef totalLen As %Integer, ByRef vocabSize As %Integer) [ Private ]
{
    Set tokens = ..Tokenize(docText)
    Set docLen = $LISTLENGTH(tokens)
    If docLen = 0 Return
    Kill tfMap
    Set tIdx = 0
    While $LISTNEXT(tokens, tIdx, tok) {
        If tok = "" Continue
        Set tfMap(tok) = $Get(tfMap(tok), 0) + 1
    }
    Set rec = ""
    Set tok = ""
    For {
        Set tok = $Order(tfMap(tok))
        Quit:tok=""
        Set ^BM25Idx(name, "tf", tok, docId) = tfMap(tok)
        Set df = +$Get(^BM25Idx(name, "df", tok), 0)
        If df = 0 Set vocabSize = vocabSize + 1
        Set ^BM25Idx(name, "df", tok) = df + 1
        Set rec = rec _ $LISTBUILD(tok)
    }
    Set ^BM25Idx(name, "doc", docId) = rec
    Set ^BM25Idx(name, "len", docId) = docLen
    Set N = N + 1
    Set totalLen = totalLen + docLen
}

// ================================================================
// Search — BM25 ranked retrieval
// ================================================================

ClassMethod Search(name As %String, queryText As %String, k As %Integer = 10) As %String
{
    // Load config
    Set N = +$Get(^BM25Idx(name, "cfg", "N"), 0)
    If N = 0 || (queryText = "") Return "[]"

    // Schema 2 derives avgdl and IDF from N, totalLen and df; schema 1 reads what Build
    // and Insert stored. The schema is read once, before anything else, so a Search never
    // mixes the two shapes (Upgrade sets it last).
    Set schema = +$Get(^BM25Idx(name, "cfg", "schema"), 1)
    If schema >= 2 {
        Set avgdl = +$Get(^BM25Idx(name, "cfg", "totalLen"), 0) / N
    } Else {
        Set avgdl = +$Get(^BM25Idx(name, "cfg", "avgdl"), 1)
    }
    If avgdl <= 0 Set avgdl = 1
    Set k1 = +$Get(^BM25Idx(name, "cfg", "k1"), 1.5)
    Set b = +$Get(^BM25Idx(name, "cfg", "b"), 0.75)

    // Tokenize query
    Set queryTokens = ..Tokenize(queryText)
    If $LISTLENGTH(queryTokens) = 0 Return "[]"

    // Accumulate BM25 scores per doc in process-private array
    Kill ^||bm25scores

    Set tIdx = 0
    While $LISTNEXT(queryTokens, tIdx, tok) {
        If tok = "" Continue
        If schema >= 2 {
            Set df = +$Get(^BM25Idx(name, "df", tok), 0)
            If df = 0 Continue
            Set idf = $ZLN((N - df + 0.5) / (df + 0.5) + 1)
        } Else {
            Set idf = +$Get(^BM25Idx(name, "idf", tok), 0)
            If idf = 0 {
                // Upgrade removes the stored idf after it sets schema 2; a Search that
                // began on schema 1 finishes with the same numbers computed from df.
                Set df = +$Get(^BM25Idx(name, "df", tok), 0)
                If df = 0 || (+$Get(^BM25Idx(name, "cfg", "schema"), 1) < 2) Continue
                Set idf = $ZLN((N - df + 0.5) / (df + 0.5) + 1)
            }
        }

        // Iterate posting list for this term
        Set docId = ""
        For {
            Set docId = $Order(^BM25Idx(name, "tf", tok, docId), 1, tf)
            Quit:docId=""
            Set docLen = +$Get(^BM25Idx(name, "len", docId), 1)

            // BM25 score contribution
            // Parenthesised because ObjectScript evaluates strictly left to right. Written
            // as `tf + k1 * (1 - b + b * docLen / avgdl)` this was
            // `(tf + k1) * (((1 - b) + b) * docLen / avgdl)`, which equals BM25 only when
            // every document has the average length.
            Set numer = tf * (k1 + 1)
            Set denom = tf + (k1 * ((1 - b) + (b * (docLen / avgdl))))
            Set contrib = idf * numer / denom
            Set ^||bm25scores(docId) = $Get(^||bm25scores(docId), 0) + contrib
        }
    }

    // Sort by score descending — collect top k
    // Scale score to a 12-decimal fixed-point integer subscript so $Order
    // gives ascending-numeric order; we iterate in reverse with $Order(...,-1).
    // This avoids the "10M offset" overflow that breaks when BM25 scores exceed it.
    Kill ^||bm25sorted
    Set docId = ""
    For {
        Set docId = $Order(^||bm25scores(docId), 1, score)
        Quit:docId=""
        // Multiply by 1e12 and round to integer → subscript safe for any BM25 score
        Set sortKey = $NUMBER(score * 1000000000000, 0)
        Set ^||bm25sorted(sortKey, docId) = score
    }

    // Walk in descending order using $Order with direction -1
    Set result = "["
    Set count = 0
    Set sortKey = ""
    For {
        If count >= k Quit
        Set sortKey = $Order(^||bm25sorted(sortKey), -1)
        Quit:sortKey=""
        Set docId = ""
        For {
            If count >= k Quit
            Set docId = $Order(^||bm25sorted(sortKey, docId), 1, score)
            Quit:docId=""
            If count > 0 Set result = result_","
            Set safeId = $Replace(docId, """", "\""")
            // ObjectScript writes 0.5 as ".5", which is not JSON; a score under 1 was
            // unparseable by every caller.
            Set scoreText = $Select($Extract(score) = ".": "0"_score, 1: score)
            Set result = result_"{""id"":"""_safeId_""",""score"":"_scoreText_"}"
            Set count = count + 1
        }
    }
    Set result = result_"]"

    Kill ^||bm25scores, ^||bm25sorted
    Return result
}

// ================================================================
// Insert / Delete — incremental, O(distinct tokens of the document)
// ================================================================

/// Seconds a writer waits for the index write lock before giving up with an error.
Parameter LOCKTIMEOUT = 30;

/// Refuse a write to an index that cannot take one. A name with no "N" is a new index and
/// is writable; the first Insert creates it as schema 2.
ClassMethod RequireWritable(name As %String) [ Private ]
{
    If '$Data(^BM25Idx(name, "cfg", "N")) Return
    If +$Get(^BM25Idx(name, "cfg", "upgrading"), 0) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "BM25 index '"_name_"' is being upgraded: writes are refused until Upgrade finishes"))
    }
    If +$Get(^BM25Idx(name, "cfg", "schema"), 1) < 2 {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "BM25 index '"_name_"' is schema 1: run Graph.KG.BM25Index.Upgrade(name) before writing to it"))
    }
}

ClassMethod LockWrite(name As %String) [ Private ]
{
    Lock +^BM25Idx(name, "lock"):..#LOCKTIMEOUT
    If '$Test {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "BM25 index '"_name_"' is locked by another writer (waited "_..#LOCKTIMEOUT_" s)"))
    }
}

/// Take one document out of the index using its own token record: its tf entries go, each
/// token's df drops by one (the node and a vocab_size count go at zero), N and totalLen
/// follow. Cost is the document's distinct tokens. Returns 1 if the document was there.
/// Caller holds the write lock and is inside a transaction.
ClassMethod RemoveDoc(name As %String, docId As %String) As %Integer [ Private ]
{
    If '$Data(^BM25Idx(name, "len", docId)) && '$Data(^BM25Idx(name, "doc", docId)) Return 0
    Set oldLen = +$Get(^BM25Idx(name, "len", docId), 0)
    Set rec = $Get(^BM25Idx(name, "doc", docId))
    Set p = 0
    While $LISTNEXT(rec, p, tok) {
        Kill ^BM25Idx(name, "tf", tok, docId)
        Set df = +$Get(^BM25Idx(name, "df", tok), 0) - 1
        If df <= 0 {
            Kill ^BM25Idx(name, "df", tok)
            Set ^BM25Idx(name, "cfg", "vocab_size") = +$Get(^BM25Idx(name, "cfg", "vocab_size"), 0) - 1
        } Else {
            Set ^BM25Idx(name, "df", tok) = df
        }
    }
    Kill ^BM25Idx(name, "doc", docId)
    Kill ^BM25Idx(name, "len", docId)
    Set ^BM25Idx(name, "cfg", "N") = +$Get(^BM25Idx(name, "cfg", "N"), 0) - 1
    Set ^BM25Idx(name, "cfg", "totalLen") = +$Get(^BM25Idx(name, "cfg", "totalLen"), 0) - oldLen
    Return 1
}

/// Add or replace one document. Returns 1 if the document is in the index afterwards and 0
/// if its text has no indexable tokens, in which case it is not (a replace with such text
/// removes the document). Raises if the index is schema 1 or being upgraded.
ClassMethod Insert(name As %String, docId As %String, text As %String) As %Integer
{
    Do ..RequireWritable(name)

    // Tokenize outside the lock: it is the only CPU-heavy part.
    Set tokens = ..Tokenize(text)
    Set docLen = $LISTLENGTH(tokens)
    Kill tfMap
    Set tIdx = 0
    While $LISTNEXT(tokens, tIdx, tok) {
        If tok = "" Continue
        Set tfMap(tok) = $Get(tfMap(tok), 0) + 1
    }

    Do ..LockWrite(name)
    Try {
        // Checked again under the lock: Upgrade may have started while this waited.
        Do ..RequireWritable(name)
        TStart
        If '$Data(^BM25Idx(name, "cfg", "N")) {
            Set ^BM25Idx(name, "cfg", "N") = 0
            Set ^BM25Idx(name, "cfg", "totalLen") = 0
            Set ^BM25Idx(name, "cfg", "vocab_size") = 0
            Set ^BM25Idx(name, "cfg", "schema") = 2
        }
        Do ..RemoveDoc(name, docId)
        If docLen > 0 {
            Set rec = ""
            Set tok = ""
            For {
                Set tok = $Order(tfMap(tok))
                Quit:tok=""
                Set ^BM25Idx(name, "tf", tok, docId) = tfMap(tok)
                Set df = +$Get(^BM25Idx(name, "df", tok), 0)
                If df = 0 Set ^BM25Idx(name, "cfg", "vocab_size") = +$Get(^BM25Idx(name, "cfg", "vocab_size"), 0) + 1
                Set ^BM25Idx(name, "df", tok) = df + 1
                Set rec = rec _ $LISTBUILD(tok)
            }
            Set ^BM25Idx(name, "doc", docId) = rec
            Set ^BM25Idx(name, "len", docId) = docLen
            Set ^BM25Idx(name, "cfg", "N") = ^BM25Idx(name, "cfg", "N") + 1
            Set ^BM25Idx(name, "cfg", "totalLen") = ^BM25Idx(name, "cfg", "totalLen") + docLen
        }
        TCommit
    } Catch ex {
        If $TLevel > 0 TRollback
        Lock -^BM25Idx(name, "lock")
        Throw ex
    }
    Lock -^BM25Idx(name, "lock")
    Return $Select(docLen > 0: 1, 1: 0)
}

/// Remove one document. Returns 1 if it was in the index, 0 if not.
ClassMethod Delete(name As %String, docId As %String) As %Integer
{
    If '$Data(^BM25Idx(name, "cfg", "N")) Return 0
    Do ..RequireWritable(name)
    Do ..LockWrite(name)
    Set removed = 0
    Try {
        Do ..RequireWritable(name)
        TStart
        Set removed = ..RemoveDoc(name, docId)
        TCommit
    } Catch ex {
        If $TLevel > 0 TRollback
        Lock -^BM25Idx(name, "lock")
        Throw ex
    }
    Lock -^BM25Idx(name, "lock")
    Return removed
}

// ================================================================
// Upgrade — convert a schema-1 index to schema 2 in one pass
// ================================================================

/// Schema 1 -> 2. One walk of tf builds df and the per-document token records; N, totalLen
/// and vocab_size are recomputed from len and df; "schema" is set to 2 only after all of
/// that, and the stored idf is removed only after that. Writers are refused while it runs
/// (cfg "upgrading"); Search keeps working throughout, on schema 1 until the flip. Safe to
/// re-run after an interruption: it starts by clearing df and doc.
/// Returns {"upgraded":1,"docs":N,"vocab":V,"postings":P}, or {"upgraded":0,"already":1}.
ClassMethod Upgrade(name As %String) As %String
{
    If '$Data(^BM25Idx(name, "cfg", "N")) Return "{""upgraded"":0,""missing"":1}"
    If (+$Get(^BM25Idx(name, "cfg", "schema"), 1) >= 2) && '$Get(^BM25Idx(name, "cfg", "upgrading"), 0) {
        Return "{""upgraded"":0,""already"":1}"
    }
    // The flag first, so new writers are refused; then the lock, which waits out one in flight.
    Set ^BM25Idx(name, "cfg", "upgrading") = 1
    Lock +^BM25Idx(name, "lock"):..#LOCKTIMEOUT
    If '$Test {
        Kill ^BM25Idx(name, "cfg", "upgrading")
        $$$ThrowStatus($$$ERROR($$$GeneralError, "BM25 index '"_name_"' is locked by a writer that did not finish in "_..#LOCKTIMEOUT_" s"))
    }
    Set postings = 0, vocab = 0, docs = 0, total = 0
    Try {
        Kill ^BM25Idx(name, "df"), ^BM25Idx(name, "doc")
        Set tok = ""
        For {
            Set tok = $Order(^BM25Idx(name, "tf", tok))
            Quit:tok=""
            Set df = 0
            Set d = ""
            For {
                Set d = $Order(^BM25Idx(name, "tf", tok, d))
                Quit:d=""
                Set df = df + 1
                Set ^BM25Idx(name, "doc", d) = $Get(^BM25Idx(name, "doc", d)) _ $LISTBUILD(tok)
            }
            Set ^BM25Idx(name, "df", tok) = df
            Set postings = postings + df
            Set vocab = vocab + 1
        }
        Set d = ""
        For {
            Set d = $Order(^BM25Idx(name, "len", d), 1, l)
            Quit:d=""
            Set docs = docs + 1
            Set total = total + l
        }
        Set ^BM25Idx(name, "cfg", "N") = docs
        Set ^BM25Idx(name, "cfg", "totalLen") = total
        Set ^BM25Idx(name, "cfg", "vocab_size") = vocab
        // The flip. Everything above is in place before any reader can see this.
        Set ^BM25Idx(name, "cfg", "schema") = 2
        Kill ^BM25Idx(name, "idf")
        Kill ^BM25Idx(name, "cfg", "avgdl")
    } Catch ex {
        Kill ^BM25Idx(name, "cfg", "upgrading")
        Lock -^BM25Idx(name, "lock")
        Throw ex
    }
    Kill ^BM25Idx(name, "cfg", "upgrading")
    Lock -^BM25Idx(name, "lock")
    Return "{""upgraded"":1,""docs"":"_docs_",""vocab"":"_vocab_",""postings"":"_postings_"}"
}

// ================================================================
// Drop — remove entire index
// ================================================================

ClassMethod Drop(name As %String) As %Integer
{
    Kill ^BM25Idx(name)
    Return 1
}

// ================================================================
// Info — return index metadata
// ================================================================

ClassMethod Info(name As %String) As %String
{
    Set N = $Get(^BM25Idx(name, "cfg", "N"), "")
    If N = "" Return "{}"
    Set schema = +$Get(^BM25Idx(name, "cfg", "schema"), 1)
    If schema >= 2 {
        Set avgdl = $Select(N > 0: +$Get(^BM25Idx(name, "cfg", "totalLen"), 0) / N, 1: 0)
    } Else {
        Set avgdl = +$Get(^BM25Idx(name, "cfg", "avgdl"), 0)
    }
    Set vocabSize = +$Get(^BM25Idx(name, "cfg", "vocab_size"), 0)
    // An index left over from a pre-230 build has no graph subscript and covered
    // every graph in the namespace. It reports "" — the same value the default
    // graph reports — so a stale index is indistinguishable here; rebuild it
    // rather than reading a graph off it.
    Set graph = $Get(^BM25Idx(name, "cfg", "graph"), "")
    Set graphJSON = $Replace($Replace(graph, "\", "\\"), """", "\""")
    Return "{""N"":"_N_",""avgdl"":"_avgdl_",""vocab_size"":"_vocabSize_",""graph"":"""_graphJSON_""",""schema"":"_schema_"}"
}

// ================================================================
// SearchProc — SQL stored procedure wrapper for Cypher translator
// ================================================================

ClassMethod SearchProc(name As %String, queryText As %String, k As %Integer) As %String [ SqlProc, SqlName = kg_BM25 ]
{
    Return ..Search(name, queryText, k)
}

ClassMethod List() As %String
{
	Set result = "", name = ""
	For { Set name = $Order(^BM25Idx(name)) Quit:name=""  Set result = result _ $Select(result="":"", 1:",") _ name }
	Return result
}

}
