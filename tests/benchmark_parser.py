import time
import statistics
from iris_vector_graph.cypher.parser import parse_query

def benchmark_parser():
    queries = [
        "MATCH (a:Account) RETURN a.node_id LIMIT 5",
        "MATCH (t:Transaction)-[:FROM_ACCOUNT]->(a:Account) RETURN t.node_id, a.node_id",
        "MATCH (a:Account) WHERE a.risk_score > 0.5 RETURN a.node_id, a.risk_score",
        "MATCH (a:Account) WITH a, count(*) AS tc WHERE tc > 1 RETURN a.node_id",
        "MATCH (t:Transaction) RETURN count(t), sum(t.amount), avg(t.amount)",
        "MATCH (t:Transaction)-[r]->(a:Account) RETURN id(t), type(r) LIMIT 5"
    ]
    
    print("Cypher Parser Benchmarks (Recursive-Descent)")
    print("-" * 60)
    
    for q in queries:
        times = []
        for _ in range(100):
            start = time.perf_counter()
            parse_query(q)
            end = time.perf_counter()
            times.append((end - start) * 1000) # ms
            
        avg_time = statistics.mean(times)
        p95_time = statistics.quantiles(times, n=20)[18] # 95th percentile
        
        print(f"Query: {q}")
        print(f"  Avg: {avg_time:.4f} ms")
        print(f"  P95: {p95_time:.4f} ms")
        print("-" * 60)

if __name__ == "__main__":
    benchmark_parser()
