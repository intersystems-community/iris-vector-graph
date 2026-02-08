from iris_devtester.utils.dbapi_compat import get_connection
import json

def test():
    conn = get_connection("localhost", 1972, "USER", "_SYSTEM", "SYS")
    cursor = conn.cursor()
    
    # Clean up
    cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s = 'DEBUG:1'")
    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s = 'DEBUG:1'")
    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id = 'DEBUG:1'")
    conn.commit()
    
    print("Cleaned up")
    
    # Try Phase 1
    cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", ["DEBUG:1"])
    conn.commit()
    print("Inserted node")
    
    # Try Phase 2
    cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)", ["DEBUG:1", "Test"])
    conn.commit()
    print("Inserted label")
    
    print("Success")

if __name__ == "__main__":
    test()
