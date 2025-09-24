-- sql/graph_path_globals.sql — Globals-based GRAPH_PATH implementation
-- Uses IRIS B-tree globals for efficient traversal instead of recursive CTEs

-- Replace the SQL stub with globals-based traversal using Embedded Python
CREATE OR REPLACE PROCEDURE kg_GRAPH_PATH(
  IN  src_id VARCHAR(256),
  IN  pred1 VARCHAR(128),
  IN  pred2 VARCHAR(128),
  IN  max_hops INT DEFAULT 2
)
RETURNS TABLE (path_id BIGINT, step INT, s VARCHAR(256), p VARCHAR(128), o VARCHAR(256))
LANGUAGE PYTHON
BEGIN
import iris

def graph_path_traversal(src_id, pred_sequence, max_hops, dst_label=""):
    """
    B-tree optimized graph traversal using IRIS globals

    Args:
        src_id: Starting node ID
        pred_sequence: List of predicates to follow in sequence
        max_hops: Maximum number of hops to traverse
        dst_label: Optional destination label filter

    Returns:
        List of (path_id, step, s, p, o) tuples
    """
    g = iris.gref("^KG")

    # Initialize traversal state
    seen = {src_id}
    current_frontier = {src_id}
    results = []
    path_counter = 0

    # Track paths for reconstruction
    path_parents = {}  # node -> (parent_node, predicate, step)

    for hop in range(1, max_hops + 1):
        if not current_frontier:
            break

        next_frontier = set()

        # Determine predicate for this hop
        wanted_predicate = None
        if pred_sequence and hop <= len(pred_sequence):
            wanted_predicate = pred_sequence[hop - 1]

        # Expand current frontier
        for source_node in current_frontier:
            if wanted_predicate:
                # Specific predicate traversal
                target_node = ""
                while True:
                    try:
                        target_node = g.next("out", source_node, wanted_predicate, target_node)
                        if target_node == "":
                            break

                        # Apply destination label filter if specified
                        if dst_label and not g.get("label", dst_label, target_node):
                            continue

                        # Record this edge in results
                        results.append((path_counter, hop, source_node, wanted_predicate, target_node))

                        # Track path for reconstruction
                        if target_node not in seen:
                            path_parents[target_node] = (source_node, wanted_predicate, hop)
                            next_frontier.add(target_node)

                    except Exception as e:
                        # Handle any global access errors
                        break
            else:
                # All predicates traversal
                predicate = ""
                while True:
                    try:
                        predicate = g.next("out", source_node, predicate)
                        if predicate == "":
                            break

                        # For each predicate, traverse all targets
                        target_node = ""
                        while True:
                            try:
                                target_node = g.next("out", source_node, predicate, target_node)
                                if target_node == "":
                                    break

                                # Apply destination label filter if specified
                                if dst_label and not g.get("label", dst_label, target_node):
                                    continue

                                # Record this edge in results
                                results.append((path_counter, hop, source_node, predicate, target_node))

                                # Track path for reconstruction
                                if target_node not in seen:
                                    path_parents[target_node] = (source_node, predicate, hop)
                                    next_frontier.add(target_node)

                            except Exception as e:
                                break

                    except Exception as e:
                        break

        # Update seen nodes and frontier for next iteration
        seen.update(next_frontier)
        current_frontier = next_frontier
        path_counter += 1

    return results

# Main procedure logic
try:
    # Parse predicate sequence from input parameters
    pred_sequence = []
    if pred1 and pred1.strip():
        pred_sequence.append(pred1.strip())
    if pred2 and pred2.strip():
        pred_sequence.append(pred2.strip())

    # Execute traversal
    path_results = graph_path_traversal(src_id, pred_sequence, max_hops)

    # Return results in the expected table format
    for path_id, step, s, p, o in path_results:
        yield (path_id, step, s, p, o)

except Exception as e:
    # Return empty result on error
    # In production, might want to log errors
    pass

END;

-- Enhanced version with bidirectional search capability
CREATE OR REPLACE PROCEDURE kg_SHORTEST_PATH(
  IN  src_id VARCHAR(256),
  IN  dst_id VARCHAR(256),
  IN  max_hops INT DEFAULT 6,
  IN  predicate_filter VARCHAR(128) DEFAULT NULL
)
RETURNS TABLE (path_id BIGINT, step INT, s VARCHAR(256), p VARCHAR(128), o VARCHAR(256))
LANGUAGE PYTHON
BEGIN
import iris

def bidirectional_search(src_id, dst_id, max_hops, pred_filter=None):
    """
    Bidirectional BFS for shortest path using IRIS globals
    """
    g = iris.gref("^KG")

    # Initialize forward and backward search
    forward_frontier = {src_id}
    backward_frontier = {dst_id}
    forward_visited = {src_id: 0}  # node -> distance
    backward_visited = {dst_id: 0}
    forward_parents = {}  # node -> (parent, predicate)
    backward_parents = {}

    for depth in range(max_hops // 2 + 1):
        if not forward_frontier and not backward_frontier:
            break

        # Check for intersection
        intersection = forward_visited.keys() & backward_visited.keys()
        if len(intersection) > 1:  # More than just the source node
            # Found shortest path
            meeting_point = min(intersection - {src_id},
                              key=lambda x: forward_visited[x] + backward_visited[x])
            return reconstruct_path(meeting_point, forward_parents, backward_parents,
                                  forward_visited, backward_visited)

        # Expand smaller frontier first (optimization)
        if len(forward_frontier) <= len(backward_frontier):
            forward_frontier = expand_frontier(g, forward_frontier, forward_visited,
                                             forward_parents, "out", pred_filter)
        else:
            backward_frontier = expand_frontier(g, backward_frontier, backward_visited,
                                              backward_parents, "in", pred_filter)

    return []  # No path found

def expand_frontier(g, frontier, visited, parents, direction, pred_filter):
    """Expand frontier in given direction"""
    next_frontier = set()

    for node in frontier:
        if pred_filter:
            # Specific predicate only
            next_node = ""
            while True:
                try:
                    next_node = g.next(direction, node, pred_filter, next_node)
                    if next_node == "":
                        break
                    if next_node not in visited:
                        visited[next_node] = visited[node] + 1
                        parents[next_node] = (node, pred_filter)
                        next_frontier.add(next_node)
                except:
                    break
        else:
            # All predicates
            predicate = ""
            while True:
                try:
                    predicate = g.next(direction, node, predicate)
                    if predicate == "":
                        break

                    next_node = ""
                    while True:
                        try:
                            next_node = g.next(direction, node, predicate, next_node)
                            if next_node == "":
                                break
                            if next_node not in visited:
                                visited[next_node] = visited[node] + 1
                                parents[next_node] = (node, predicate)
                                next_frontier.add(next_node)
                        except:
                            break
                except:
                    break

    return next_frontier

def reconstruct_path(meeting_point, forward_parents, backward_parents,
                    forward_visited, backward_visited):
    """Reconstruct shortest path from bidirectional search"""
    path = []

    # Reconstruct forward path
    current = meeting_point
    forward_path = []
    while current in forward_parents:
        parent, predicate = forward_parents[current]
        forward_path.append((parent, predicate, current))
        current = parent
    forward_path.reverse()

    # Reconstruct backward path
    current = meeting_point
    backward_path = []
    while current in backward_parents:
        parent, predicate = backward_parents[current]
        backward_path.append((current, predicate, parent))
        current = parent

    # Combine paths
    step = 1
    for s, p, o in forward_path:
        path.append((1, step, s, p, o))
        step += 1

    for s, p, o in backward_path:
        path.append((1, step, s, p, o))
        step += 1

    return path

# Main shortest path logic
try:
    shortest_path = bidirectional_search(src_id, dst_id, max_hops, predicate_filter)

    for path_id, step, s, p, o in shortest_path:
        yield (path_id, step, s, p, o)

except Exception as e:
    pass

END;

-- Utility procedure to get node statistics for query planning
CREATE OR REPLACE PROCEDURE kg_NODE_STATS(
  IN  node_id VARCHAR(256)
)
RETURNS TABLE (metric VARCHAR(50), value DOUBLE)
LANGUAGE PYTHON
BEGIN
import iris

try:
    g = iris.gref("^KG")

    # Get degree statistics
    out_degree = g.get("deg", node_id) or 0

    # Count predicates
    predicate_count = 0
    predicate = ""
    while True:
        try:
            predicate = g.next("degp", node_id, predicate)
            if predicate == "":
                break
            predicate_count += 1
        except:
            break

    # Return statistics
    yield ("out_degree", float(out_degree))
    yield ("predicate_count", float(predicate_count))

    # Check if node has specific labels
    common_labels = ["Gene", "Disease", "Drug", "Protein"]
    for label in common_labels:
        has_label = 1.0 if g.get("label", label, node_id) else 0.0
        yield (f"has_label_{label}", has_label)

except Exception as e:
    yield ("error", 1.0)

END;