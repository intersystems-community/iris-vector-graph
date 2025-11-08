/// Native ObjectScript Personalized PageRank using ^PPR Functional Index
///
/// This class provides a pure ObjectScript implementation of PPR that directly
/// traverses the ^PPR Global maintained by PPRFunctionalIndex with zero Python overhead.
///
/// Performance target: <10ms for 10K nodes, <1ms for 1K nodes
///
/// Usage from Python:
///   import iris
///   conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
///   irispy = iris.createIRIS(conn)
///   result_json = irispy.classMethodValue(
///       'Graph.KG.PPRNative', 'ComputePPR',
///       '["BENCH_NODE_0"]',  // seed_entities as JSON
///       0.85,                 // damping_factor
///       100,                  // max_iterations
///       1e-6                  // tolerance
///   )
///   scores = json.loads(result_json)
///
Class Graph.KG.PPRNative Extends %RegisteredObject
{

/// Compute Personalized PageRank using native ObjectScript with ^PPR Globals
///
/// Arguments:
///   seedEntitiesJSON - JSON array of seed node IDs (e.g., '["NODE1","NODE2"]')
///   alpha - Damping factor (default 0.85)
///   maxIter - Maximum iterations (default 100)
///   tol - Convergence tolerance (default 1e-6)
///
/// Returns:
///   JSON object mapping node_id -> PPR score
///   Example: '{"NODE1": 0.25, "NODE2": 0.15, ...}'
ClassMethod ComputePPR(
    seedEntitiesJSON As %String = "[]",
    alpha As %Numeric = 0.85,
    maxIter As %Integer = 100,
    tol As %Numeric = 0.000001
) As %String
{
    // Parse seed entities from JSON
    Set seedArray = ##class(%DynamicArray).%FromJSON(seedEntitiesJSON)
    Set seedCount = seedArray.%Size()

    If seedCount = 0 {
        Quit "{""error"": ""seed_entities cannot be empty""}"
    }

    If (alpha < 0) || (alpha > 1) {
        Quit "{""error"": ""alpha must be in [0.0, 1.0]""}"
    }

    // Step 1: Build node set and index mapping from ^PPR("deg", *)
    // Use $ORDER for this one-time build - packed lists used in iteration
    Set N = 0

    // Nodes with outgoing edges
    Set v = ""
    For {
        Set v = $Order(^PPR("deg", v))
        Quit:v=""

        Set nodeIdx(v) = $Increment(N)
        Set nodes(N) = v
        Set outdeg(v) = ^PPR("deg", v)
    }

    // Sink nodes from ^PPR("dang", *) or ^PPR("inL", *) without outdegree
    Set v = ""
    For {
        Set v = $Order(^PPR("dang", v))
        Quit:v=""

        If '$Data(nodeIdx(v)) {
            Set nodeIdx(v) = $Increment(N)
            Set nodes(N) = v
            Set outdeg(v) = 0  // Sink node
        }
    }

    // Also check for any nodes in ^PPR("inL", *) that aren't yet indexed
    Set v = ""
    For {
        Set v = $Order(^PPR("inL", v))
        Quit:v=""

        If '$Data(nodeIdx(v)) {
            Set nodeIdx(v) = $Increment(N)
            Set nodes(N) = v
            Set outdeg(v) = 0  // Must be sink if not in deg
        }
    }

    If N = 0 {
        Quit "{""error"": ""No nodes found in graph""}"
    }

    // Step 2: Initialize personalization vector p(v) and scores x(v)
    // Uniform personalization over seed entities
    Set uniformSeed = 1.0 / seedCount

    For i = 0:1:(seedCount - 1) {
        Set seed = seedArray.%Get(i)

        // Validate seed exists in graph
        If '$Data(nodeIdx(seed)) {
            Quit "{""error"": ""Seed entity not found: "_seed_"""}"
        }

        Set p(seed) = uniformSeed
        Set x(seed) = uniformSeed
    }

    // Initialize non-seed nodes to 0
    For i = 1:1:N {
        Set v = nodes(i)
        Set:'$Data(x(v)) x(v) = 0
        Set:'$Data(p(v)) p(v) = 0
    }

    // Dangling nodes already in ^PPR("dang", *) - no need to compute

    // Step 3: Power iteration using inbound edges
    For iter = 1:1:maxIter {

        // Calculate dangling node mass using ^PPR("dang", *)
        Set totalDang = 0
        Set v = ""
        For {
            Set v = $Order(^PPR("dang", v))
            Quit:v=""

            Set totalDang = totalDang + $Get(x(v), 0)
        }

        // Distribute dangling mass uniformly
        Set dangPerNode = totalDang / N

        // Compute new scores via inbound link accumulation
        Set v = ""
        For {
            Set v = $Order(x(v))
            Quit:v=""

            // Teleportation term + dangling distribution
            Set sum = (1 - alpha) * p(v) + (alpha * dangPerNode)

            // Accumulate from incoming neighbors using PACKED LIST (fast!)
            Set inList = $Get(^PPR("inL", v))
            If inList '= "" {
                Set ptr = 0
                While $ListNext(inList, ptr, u) {
                    Set d = $Get(outdeg(u), 0)
                    If d > 0 {
                        Set sum = sum + (alpha * x(u) / d)
                    }
                }
            }

            Set x1(v) = sum
        }

        // Check convergence
        Set delta = 0
        Set v = ""
        For {
            Set v = $Order(x(v))
            Quit:v=""

            Set diff = $ZAbs(x1(v) - x(v))
            If diff > delta Set delta = diff
        }

        // Copy x1 -> x
        Kill x
        Merge x = x1
        Kill x1

        // Exit if converged
        If delta < tol Quit
    }

    // Step 4: Normalize scores to sum to 1.0
    Set normalizer = 0
    Set v = ""
    For {
        Set v = $Order(x(v))
        Quit:v=""
        Set normalizer = normalizer + x(v)
    }

    If normalizer > 0 {
        Set v = ""
        For {
            Set v = $Order(x(v))
            Quit:v=""
            Set x(v) = x(v) / normalizer
        }
    }

    // Step 5: Build JSON result (filter scores > 1e-10)
    Set result = ##class(%DynamicObject).%New()

    Set v = ""
    For {
        Set v = $Order(x(v))
        Quit:v=""

        If x(v) > 0.0000000001 {
            Do result.%Set(v, x(v))
        }
    }

    // Return JSON string
    Quit result.%ToJSON()
}

}
