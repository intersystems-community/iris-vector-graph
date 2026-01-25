#!/usr/bin/env python3
"""
Setup IRIS container for Vector Graph development.
Uses iris-devtester for automatic dynamic port allocation and password handling.
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

try:
    from iris_devtester.containers.iris_container import IRISContainer
    from iris_devtester.ports import PortRegistry
except ImportError:
    print("Error: iris-devtester not found. Run 'uv sync' first.")
    sys.exit(1)

def main():
    print("🚀 Setting up IRIS Vector Graph database...")
    
    # Initialize port registry
    registry = PortRegistry()
    
    # Create container
    # Using intersystemsdc/iris-community:latest-em which is ARM64 friendly (Apple Silicon)
    container = IRISContainer(
        image='intersystemsdc/iris-community:latest-em',
        port_registry=registry,
        project_path=os.getcwd()
    )
    
    print("📦 Starting IRIS container (this may take a minute)...")
    container.start()
    
    assigned_port = container.get_assigned_port()
    container_name = container.get_container_name()
    
    print(f"✅ IRIS is up and running!")
    print(f"   Container: {container_name}")
    print(f"   Port:      {assigned_port}")
    
    # Update .env file
    env_path = Path(".env")
    env_content = f"""IRIS_HOST=localhost
IRIS_PORT={assigned_port}
IRIS_NAMESPACE=USER
IRIS_USER=_SYSTEM
IRIS_PASSWORD=SYS
IRIS_CONTAINER={container_name}
"""
    env_path.write_text(env_content)
    print(f"📝 Updated .env with port {assigned_port}")
    
    # Initialize schema
    print("🏗️  Initializing schema...")
    conn = container.get_connection()
    cursor = conn.cursor()
    
    schema_files = [
        'sql/schema.sql',
        'sql/operators_fixed.sql'
    ]
    
    for schema_file in schema_files:
        path = Path(schema_file)
        if path.exists():
            print(f"   Running {schema_file}...")
            content = path.read_text()
            # Basic splitter for simple SQL files
            for stmt in content.split(';'):
                stmt = stmt.strip()
                if stmt and not stmt.startswith('--'):
                    try:
                        cursor.execute(stmt)
                    except Exception as e:
                        if 'already exists' not in str(e).lower():
                            print(f"   ⚠️  Warning in {schema_file}: {str(e)[:100]}")
    
    conn.commit()
    conn.close()
    
    print("\n✨ Setup complete! You can now start the API:")
    print("   uvicorn api.main:app --reload")

if __name__ == "__main__":
    main()
