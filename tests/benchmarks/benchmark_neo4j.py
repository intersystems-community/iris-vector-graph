from neo4j import GraphDatabase
import time
import json
import argparse

def benchmark_neo4j(uri, user, password, src_node, max_depth):
    driver = GraphDatabase.driver(uri, auth=(user, password))
    results = {}
    
    with driver.session() as session:
        for depth in range(1, max_depth + 1):
            print(f"Benchmarking Neo4j Depth {depth}...")
            latencies = []
            counts = []
            
            # Use Cypher for BFS traversal
            query = f"""
            MATCH (n:Node {{id: $srcId}})-[:RELATED*1..{depth}]->(m)
            RETURN count(DISTINCT m) as count
            """
            
            for _ in range(3):
                start_time = time.time()
                res = session.run(query, srcId=src_node)
                count = res.single()["count"]
                duration = time.time() - start_time
                
                latencies.append(duration)
                counts.append(count)
            
            avg_latency = sum(latencies) / len(latencies)
            avg_count = sum(counts) / len(counts)
            
            results[depth] = {
                "avg_latency": avg_latency,
                "avg_count": avg_count
            }
            print(f"  Depth {depth}: {avg_latency:.4f}s, {avg_count:.0f} nodes reached")
            
    driver.close()
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", type=str, default="bolt://localhost:7687")
    parser.add_argument("--user", type=str, default="neo4j")
    parser.add_argument("--password", type=str, default="password")
    parser.add_argument("--src", type=str, default="node_0")
    parser.add_argument("--depth", type=int, default=6)
    
    args = parser.parse_args()
    
    res = benchmark_neo4j(args.uri, args.user, args.password, args.src, args.depth)
    
    with open("specs/013-bfs-refactoring/neo4j_benchmarks.json", "w") as f:
        json.dump(res, f, indent=2)
    print("\nResults saved to specs/013-bfs-refactoring/neo4j_benchmarks.json")
