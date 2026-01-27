from neo4j import GraphDatabase
import csv
import argparse
import time

def load_to_neo4j(uri, user, password, csv_file):
    driver = GraphDatabase.driver(uri, auth=(user, password))
    
    with driver.session() as session:
        # Clean existing data
        print("Cleaning Neo4j...")
        session.run("MATCH (n) DETACH DELETE n")
        
        print(f"Loading {csv_file} into Neo4j...")
        start_time = time.time()
        
        # Load nodes and relationships in bulk
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            # Group into batches for performance
            batch_size = 5000
            batch = []
            count = 0
            for row in reader:
                batch.append(row)
                if len(batch) >= batch_size:
                    session.execute_write(create_batch, batch)
                    count += len(batch)
                    print(f"  Loaded {count} edges...")
                    batch = []
            if batch:
                session.execute_write(create_batch, batch)
                count += len(batch)
        
        duration = time.time() - start_time
        print(f"Neo4j load complete: {count} edges in {duration:.2f}s")
    
    driver.close()

def create_batch(tx, batch):
    query = """
    UNWIND $batch AS row
    MERGE (s:Node {id: row.s})
    MERGE (o:Node {id: row.o_id})
    MERGE (s)-[:RELATED {type: row.p}]->(o)
    """
    tx.run(query, batch=batch)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, required=True)
    parser.add_argument("--uri", type=str, default="bolt://localhost:7687")
    parser.add_argument("--user", type=str, default="neo4j")
    parser.add_argument("--password", type=str, default="password")
    
    args = parser.parse_args()
    load_to_neo4j(args.uri, args.user, args.password, args.csv)
