# Quickstart: BFS Traversal Refactoring

**Feature**: 013-bfs-refactoring  
**Date**: 2026-01-26

## Overview

This refactoring improves the readability and performance of the `Graph.KG.Traversal:BFS_JSON` method. It introduces specialized helper methods and direct object instantiation.

## Benchmarking Execution

To establish a performance baseline or verify improvements, run the benchmarking script from the repository root:

```bash
# Run baseline against existing code
python tests/benchmarks/bfs_benchmark.py --mode baseline --nodes 10000

# Run verification against refactored code
python tests/benchmarks/bfs_benchmark.py --mode refactored --nodes 10000
```

Results will be saved to `specs/013-bfs-refactoring/benchmarks.json`.

## Manual Verification (IRIS Terminal)

You can verify the refactored logic directly in the IRIS terminal:

```objectscript
// 1. Rebuild the KG cache
Do ##class(Graph.KG.Traversal).BuildKG()

// 2. Perform a multi-hop BFS
Set preds = ["part_of", "caused_by"]
Set results = ##class(Graph.KG.Traversal).BFS_JSON("node_123", preds, 2)

// 3. Inspect results
Write results.%ToJSON()
```

## Key Changes

### 1. Refactored `BFS_JSON`
- Reduced nesting depth from 5 to 3.
- Logic for `_traverse_with_predicate` and `_traverse_all_predicates` extracted.

### 2. Direct Object Creation
- Replaced `out._Push(iris.cls('%DynamicObject')._FromJSON(json.dumps(stepObj)))`
- With `obj = iris.cls('%DynamicObject')._New(); obj.s = s; ...; out._Push(obj)`

## Performance Expectations

| Metric | Target |
|--------|--------|
| Total Traversal Latency | <= 5% regression |
| Object Instantiation | >= 20% improvement |
| Max Nesting Depth | <= 3 levels |
