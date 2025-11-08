/// Personalized PageRank computation using Embedded Python with ^PPR Functional Index
///
/// This class provides an embedded Python implementation of PPR that directly
/// accesses the ^PPR Global maintained by PPRFunctionalIndex, avoiding the
/// overhead of the external Python client API.
///
/// Performance target: <100ms for 10K nodes using embedded Python
/// Future: <10ms for 10K nodes using pure ObjectScript
///
/// Usage from external Python:
///   import iris
///   conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
///   irispy = iris.createIRIS(conn)
///   result = irispy.classMethodValue(
///       'Graph.KG.PPRCompute', 'ComputePPR',
///       '["BENCH_NODE_0"]',  # seed_entities as JSON
///       0.85,                 # damping_factor
///       100,                  # max_iterations
///       1e-6                  # tolerance
///   )
///   scores = json.loads(result)
///
Class Graph.KG.PPRCompute Extends %RegisteredObject
{

/// Compute Personalized PageRank using embedded Python with ^PPR Globals
///
/// Arguments:
///   seedEntitiesJSON - JSON array of seed node IDs (e.g., '["NODE1","NODE2"]')
///   dampingFactor - Damping factor (default 0.85)
///   maxIterations - Maximum iterations (default 100)
///   tolerance - Convergence tolerance (default 1e-6)
///
/// Returns:
///   JSON object mapping node_id -> PPR score
///   Example: '{"NODE1": 0.25, "NODE2": 0.15, ...}'
ClassMethod ComputePPR(
    seedEntitiesJSON As %String = "[]",
    dampingFactor As %Numeric = 0.85,
    maxIterations As %Integer = 100,
    tolerance As %Numeric = 1e-6
) As %String [ Language = python ]
{
    import iris
    import json

    # Parse seed entities
    seed_entities = json.loads(seedEntitiesJSON)

    if not seed_entities:
        raise ValueError("seed_entities cannot be empty")

    if not (0.0 <= dampingFactor <= 1.0):
        raise ValueError(f"dampingFactor must be in [0.0, 1.0], got {dampingFactor}")

    # Use iris.gref() - call iterator methods on gref itself with key as list
    g = iris.gref('^PPR')

    # Verify we can see the Global (data() returns 10 or 11 for branch with descendants)
    deg_status = g.data(['deg'])
    if deg_status not in (10, 11):
        # Try extended global reference if unqualified doesn't work
        g = iris.gref('^|"USER"|PPR')
        deg_status = g.data(['deg'])
        if deg_status not in (10, 11):
            return json.dumps({
                "error": "Cannot see ^PPR Global",
                "deg_status": deg_status,
                "hint": "Check Global mappings in Portal"
            })

    # Step 1: Build node set from ^PPR("deg", *) using order() - single-level iteration
    node_set = set()
    node_degrees = {}

    # Track API call counts for profiling
    order_calls = 0
    get_calls = 0

    # Iterate using order(['deg', key]) - like $ORDER, fastest method
    k = ''  # Start with empty string
    while True:
        k = g.order(['deg', k])
        order_calls += 1
        if k is None:
            break
        outdeg = g.get(['deg', k])
        get_calls += 1
        if outdeg is not None:
            node_set.add(k)
            node_degrees[k] = int(outdeg)

    # Also collect sink nodes from ^PPR("in", *, *)
    # Iterate first level of "in" branch to find all targets
    seen_targets = set()
    k = ''
    while True:
        k = g.order(['in', k])
        order_calls += 1
        if k is None:
            break
        if k not in seen_targets:
            seen_targets.add(k)
            if k not in node_set:
                node_set.add(k)
                node_degrees[k] = 0  # Sink node

    nodes = list(node_set)

    if not nodes:
        # Return error if no nodes found
        return json.dumps({"error": "No nodes found in graph"})

    N = len(nodes)
    node_to_idx = {node: idx for idx, node in enumerate(nodes)}

    # Validate seed entities exist in graph
    invalid_seeds = [seed for seed in seed_entities if seed not in node_to_idx]
    if invalid_seeds:
        raise ValueError(
            f"Seed entities not found in graph: {invalid_seeds}. "
            f"Available nodes: {len(nodes)}"
        )

    # Step 2: Initialize scores (using pure Python lists)
    scores = [0.0] * N
    personalization = [0.0] * N

    seed_set = set(seed_entities)
    uniform_seed = 1.0 / len(seed_entities)

    for i, node_id in enumerate(nodes):
        if node_id in seed_set:
            scores[i] = uniform_seed
            personalization[i] = uniform_seed

    # Step 3: Power iteration with Global traversal
    for iteration in range(1, maxIterations + 1):
        prev_scores = scores[:]

        # Initialize with teleportation term
        new_scores = [(1 - dampingFactor) * p for p in personalization]

        # Traverse incoming edges via ^PPR("in", target, source)
        # Use order(['in', target_id, key]) to iterate sources for each target
        for target_idx, target_id in enumerate(nodes):
            incoming_score = 0.0

            # Iterate over sources using order() - like $ORDER on ^PPR("in", target_id, src)
            src = ''
            while True:
                src = g.order(['in', target_id, src])
                order_calls += 1
                if src is None:
                    break
                if src in node_to_idx:
                    src_idx = node_to_idx[src]
                    src_outdeg = node_degrees.get(src, 1)

                    # Handle sink nodes: distribute uniformly if outdeg == 0
                    if src_outdeg == 0:
                        incoming_score += prev_scores[src_idx] / N
                    else:
                        incoming_score += prev_scores[src_idx] / src_outdeg

            new_scores[target_idx] += dampingFactor * incoming_score

        scores = new_scores

        # Check convergence
        max_change = max(abs(scores[i] - prev_scores[i]) for i in range(N))
        if max_change < tolerance:
            break

    # Step 4: Normalize scores
    total = sum(scores)
    if total > 0:
        scores = [s / total for s in scores]

    # Step 5: Filter and return as JSON
    score_dict = {
        nodes[i]: scores[i]
        for i in range(N)
        if scores[i] > 1e-10
    }

    # Add profiling data as special key
    score_dict['__profile__'] = {
        'order_calls': order_calls,
        'get_calls': get_calls,
        'iterations': iteration,
        'nodes': N
    }

    return json.dumps(score_dict)
}

}
