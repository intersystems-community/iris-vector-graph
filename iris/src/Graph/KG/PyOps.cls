Class Graph.KG.PyOps
{

ClassMethod VectorSearch(vec As %DynamicArray, k As %Integer = 50, label As %String = "", ByRef out As %DynamicArray) As %Status [ Language = python ]
{
  import iris
  import json

  if vec is None or vec.%Size() == 0:
      return iris.system.Status.Error("vector required")
  if vec.%Size() != 768:
      return iris.system.Status.Error("Expected 768-dimensional vector for OpenAI text-embedding-ada-002")

  # Convert DynamicArray to JSON string for SQL procedure
  vector_list = []
  for i in range(1, vec.%Size() + 1):
      vector_list.append(float(vec.%Get(i)))

  vector_json = json.dumps(vector_list)
  k = int(k) if k else 50
  label = str(label) if label else None

  sql = "CALL kg_KNN_VEC(?, ?, ?)"
  rs = iris.sql.exec(sql, vector_json, k, label)
  rows = rs.fetchall()
  out = iris.cls('%DynamicArray')._New()
  for r in rows:
      out.%Push(iris.cls('%DynamicObject')._FromJSON(json.dumps({"id": r[0], "score": float(r[1])})))
  return iris.system.Status.OK()
}

ClassMethod HybridSearch(vec As %DynamicArray, text As %String, k As %Integer = 50, c As %Integer = 60, ByRef out As %DynamicArray) As %Status [ Language = python ]
{
  import iris
  import json

  if vec is None or vec.%Size() != 768:
      return iris.system.Status.Error("Expected 768-dimensional vector for OpenAI text-embedding-ada-002")

  # Convert DynamicArray to JSON string for SQL procedure
  vector_list = []
  for i in range(1, vec.%Size() + 1):
      vector_list.append(float(vec.%Get(i)))

  vector_json = json.dumps(vector_list)
  k = int(k) if k else 50
  c = int(c) if c else 60
  text = str(text) if text else ""

  sql = "CALL kg_RRF_FUSE(?, ?, ?, ?, ?, ?)"
  rs = iris.sql.exec(sql, k, 200, 200, c, vector_json, text)
  rows = rs.fetchall()
  out = iris.cls('%DynamicArray')._New()
  for r in rows:
      obj = {"id": r[0], "score": float(r[1]), "extras": {"vs": float(r[2]) if r[2] is not None else None, "bm25": float(r[3]) if r[3] is not None else None}}
      out.%Push(iris.cls('%DynamicObject')._FromJSON(json.dumps(obj)))
  return iris.system.Status.OK()
}

ClassMethod MetaPath(srcId As %String, preds As %DynamicArray, maxHops As %Integer = 2, dstLabel As %String = "", ByRef out As %DynamicArray) As %Status [ Language = python ]
{
  import iris
  res = iris.cls("Graph.KG.Traversal").BFS_JSON(srcId, preds, int(maxHops), str(dstLabel) if dstLabel else "")
  out = res
  return iris.system.Status.OK()
}

}
