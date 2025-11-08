Class Graph.KG.PageRank
{

/// Build PPR-optimized Global structure from rdf_edges
/// Stores: ^PPR("out", source, target) = ""
///         ^PPR("in", target, source) = ""
///         ^PPR("outdeg", source) = count
///         ^PPR("nodes", nodeId) = ""
ClassMethod BuildPPRGraph() As %Status
{
  Try {
    Kill ^PPR

    // Build node list and edge structures
    &sql(DECLARE c1 CURSOR FOR SELECT DISTINCT node_id FROM SQLUser.nodes)
    &sql(OPEN c1)
    For  {
      &sql(FETCH c1 INTO :nodeId)
      Quit:SQLCODE'=0
      Set ^PPR("nodes", nodeId) = ""
      Set ^PPR("outdeg", nodeId) = 0
    }
    &sql(CLOSE c1)

    // Build adjacency lists
    &sql(DECLARE c2 CURSOR FOR SELECT s, o_id FROM SQLUser.rdf_edges)
    &sql(OPEN c2)
    For  {
      &sql(FETCH c2 INTO :source, :target)
      Quit:SQLCODE'=0
      Set ^PPR("out", source, target) = ""
      Set ^PPR("in", target, source) = ""
      Set ^PPR("outdeg", source) = $Get(^PPR("outdeg", source)) + 1
    }
    &sql(CLOSE c2)

    Quit $$$OK
  } Catch ex {
    Quit ex.AsStatus()
  }
}

/// Compute Personalized PageRank using embedded Python with direct Global access
/// This is MUCH faster than SQL queries for pointer-chasing graph operations
ClassMethod ComputePPR(
  seeds As %DynamicArray,
  dampingFactor As %Double = 0.85,
  maxIterations As %Integer = 100,
  tolerance As %Double = 0.000001
) As %DynamicObject [ Language = python ]
{
  import iris
  import json

  # Access PPR Global directly
  g = iris.gref("^PPR")

  # Parse parameters
  damping = float(dampingFactor)
  max_iter = int(maxIterations)
  tol = float(tolerance)

  # Get seed entities as Python list
  seed_list = []
  seed_size = seeds._Size()
  for i in range(1, seed_size + 1):
      seed_list.append(str(seeds._Get(i)))

  if not seed_list:
      raise ValueError("seed_entities cannot be empty")

  # Validate damping factor
  if not (0.0 <= damping <= 1.0):
      raise ValueError(f"damping_factor must be in [0.0, 1.0], got {damping}")

  # Get all nodes from Global
  all_nodes = []
  node_id = ""
  while True:
      node_id = g.order("nodes", node_id)
      if node_id == "":
          break
      all_nodes.append(node_id)

  if not all_nodes:
      return iris.cls('%DynamicObject')._New()

  num_nodes = len(all_nodes)

  # Initialize scores
  seed_set = set(seed_list)
  uniform_seed_score = 1.0 / len(seed_list)

  scores = {}
  personalization = {}
  for node_id in all_nodes:
      if node_id in seed_set:
          scores[node_id] = uniform_seed_score
          personalization[node_id] = uniform_seed_score
      else:
          scores[node_id] = 0.0
          personalization[node_id] = 0.0

  # Get outdegrees from Global (precomputed)
  outdegrees = {}
  for node_id in all_nodes:
      outdeg_str = g.get("outdeg", node_id)
      outdegrees[node_id] = int(outdeg_str) if outdeg_str else 0

  # Build incoming edges structure using Global traversal
  incoming = {}
  for target in all_nodes:
      incoming[target] = []
      source = ""
      while True:
          source = g.order("in", target, source)
          if source == "":
              break
          incoming[target].append(source)

  # Power iteration
  converged = False
  for iteration in range(1, max_iter + 1):
      prev_scores = scores.copy()
      new_scores = {}

      # Update each node's score using Global data
      for node_id in all_nodes:
          walk_score = 0.0

          # Sum contributions from incoming neighbors
          for source_id in incoming[node_id]:
              source_score = prev_scores[source_id]
              source_outdeg = outdegrees[source_id]

              if source_outdeg == 0:
                  # Sink node: distribute to all nodes
                  walk_score += source_score / num_nodes
              else:
                  walk_score += source_score / source_outdeg

          # PPR formula: (1 - alpha) * personalization + alpha * walk
          new_scores[node_id] = (
              (1 - damping) * personalization[node_id] +
              damping * walk_score
          )

      # Check convergence
      max_change = max(
          abs(new_scores[node_id] - prev_scores[node_id])
          for node_id in all_nodes
      )

      scores = new_scores

      if max_change < tol:
          converged = True
          break

  # Normalize scores to sum to 1.0
  total = sum(scores.values())
  if total > 0:
      scores = {k: v / total for k, v in scores.items()}

  # Filter to non-zero scores
  scores = {k: v for k, v in scores.items() if v > 1e-10}

  # Convert to IRIS DynamicObject
  result = iris.cls('%DynamicObject')._New()
  result.converged = converged
  result.iterations = iteration
  result.scores = iris.cls('%DynamicObject')._FromJSON(json.dumps(scores))

  return result
}

}
