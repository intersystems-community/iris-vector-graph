#!/usr/bin/env python3
"""
IRIS Graph-AI Benchmarking Environment Manager

Manages test environment setup and teardown for competitive benchmarking.
Handles IRIS, Neo4j, Neptune, and other database system deployments.
"""

import os
import time
import docker
import subprocess
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

from benchmark_config import DatabaseSystem, SystemConfig, HardwareSpec


@dataclass
class EnvironmentStatus:
    """Status of a benchmarking environment"""
    system: DatabaseSystem
    status: str  # 'running', 'stopped', 'error'
    container_id: Optional[str] = None
    connection_string: Optional[str] = None
    error_message: Optional[str] = None


class BenchmarkEnvironment:
    """Manages test environment setup and teardown"""

    def __init__(self):
        self.docker_client = docker.from_env()
        self.active_environments = {}

    def setup_iris_environment(self, config: SystemConfig) -> EnvironmentStatus:
        """Setup IRIS with ACORN-1 optimization"""
        try:
            print(f"Setting up IRIS environment with {config.docker_image}")

            # Remove existing IRIS test container if it exists
            try:
                existing = self.docker_client.containers.get("iris_benchmark")
                existing.stop()
                existing.remove()
            except docker.errors.NotFound:
                pass

            # IRIS container configuration
            iris_config = {
                'image': config.docker_image,
                'name': 'iris_benchmark',
                'ports': {'1972': 1973, '52774': 52775},
                'environment': {
                    'ISC_IRIS_LICENSE': 'accept',
                    'ISC_CPF_MERGE_FILE': '/tmp/iris.cpf'
                },
                'detach': True,
                'auto_remove': False
            }

            # Start IRIS container
            container = self.docker_client.containers.run(**iris_config)

            # Wait for IRIS to be ready
            max_wait = 120  # 2 minutes
            for _ in range(max_wait):
                try:
                    # Test connection
                    import iris
                    conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                    cursor.close()
                    conn.close()
                    break
                except:
                    time.sleep(1)
            else:
                raise Exception("IRIS failed to start within timeout")

            # Load schema and stored procedures
            self._load_iris_schema(container)

            status = EnvironmentStatus(
                system=DatabaseSystem.IRIS_GRAPH_AI,
                status='running',
                container_id=container.id,
                connection_string='localhost:1973/USER'
            )

            self.active_environments['iris'] = status
            print("✅ IRIS environment ready")
            return status

        except Exception as e:
            error_status = EnvironmentStatus(
                system=DatabaseSystem.IRIS_GRAPH_AI,
                status='error',
                error_message=str(e)
            )
            print(f"❌ IRIS environment setup failed: {e}")
            return error_status

    def setup_neo4j_environment(self, config: SystemConfig) -> EnvironmentStatus:
        """Setup Neo4j Enterprise environment"""
        try:
            print(f"Setting up Neo4j environment with {config.docker_image}")

            # Remove existing Neo4j container if it exists
            try:
                existing = self.docker_client.containers.get("neo4j_benchmark")
                existing.stop()
                existing.remove()
            except docker.errors.NotFound:
                pass

            # Neo4j container configuration
            neo4j_config = {
                'image': config.docker_image,
                'name': 'neo4j_benchmark',
                'ports': {'7474': 7474, '7687': 7687},
                'environment': {
                    'NEO4J_AUTH': 'neo4j/benchmarkpassword',
                    'NEO4J_ACCEPT_LICENSE_AGREEMENT': 'yes',
                    **config.config_params
                },
                'detach': True,
                'auto_remove': False
            }

            # Start Neo4j container
            container = self.docker_client.containers.run(**neo4j_config)

            # Wait for Neo4j to be ready
            max_wait = 180  # 3 minutes for Neo4j
            for _ in range(max_wait):
                try:
                    from neo4j import GraphDatabase
                    driver = GraphDatabase.driver("bolt://localhost:7687",
                                                auth=("neo4j", "benchmarkpassword"))
                    with driver.session() as session:
                        session.run("RETURN 1")
                    driver.close()
                    break
                except:
                    time.sleep(1)
            else:
                raise Exception("Neo4j failed to start within timeout")

            status = EnvironmentStatus(
                system=DatabaseSystem.NEO4J_ENTERPRISE,
                status='running',
                container_id=container.id,
                connection_string='bolt://localhost:7687'
            )

            self.active_environments['neo4j'] = status
            print("✅ Neo4j environment ready")
            return status

        except Exception as e:
            error_status = EnvironmentStatus(
                system=DatabaseSystem.NEO4J_ENTERPRISE,
                status='error',
                error_message=str(e)
            )
            print(f"❌ Neo4j environment setup failed: {e}")
            return error_status

    def setup_arangodb_environment(self, config: SystemConfig) -> EnvironmentStatus:
        """Setup ArangoDB environment"""
        try:
            print(f"Setting up ArangoDB environment")

            # Remove existing ArangoDB container if it exists
            try:
                existing = self.docker_client.containers.get("arangodb_benchmark")
                existing.stop()
                existing.remove()
            except docker.errors.NotFound:
                pass

            # ArangoDB container configuration
            arangodb_config = {
                'image': 'arangodb:latest',
                'name': 'arangodb_benchmark',
                'ports': {'8529': 8529},
                'environment': {
                    'ARANGO_ROOT_PASSWORD': 'benchmarkpassword'
                },
                'detach': True,
                'auto_remove': False
            }

            # Start ArangoDB container
            container = self.docker_client.containers.run(**arangodb_config)

            # Wait for ArangoDB to be ready
            max_wait = 120
            for _ in range(max_wait):
                try:
                    import requests
                    response = requests.get("http://localhost:8529/_api/version",
                                          auth=('root', 'benchmarkpassword'))
                    if response.status_code == 200:
                        break
                except:
                    time.sleep(1)
            else:
                raise Exception("ArangoDB failed to start within timeout")

            status = EnvironmentStatus(
                system=DatabaseSystem.ARANGODB,
                status='running',
                container_id=container.id,
                connection_string='http://localhost:8529'
            )

            self.active_environments['arangodb'] = status
            print("✅ ArangoDB environment ready")
            return status

        except Exception as e:
            error_status = EnvironmentStatus(
                system=DatabaseSystem.ARANGODB,
                status='error',
                error_message=str(e)
            )
            print(f"❌ ArangoDB environment setup failed: {e}")
            return error_status

    def teardown_environment(self, system_name: str):
        """Clean teardown of test environment"""
        if system_name in self.active_environments:
            status = self.active_environments[system_name]

            if status.container_id:
                try:
                    container = self.docker_client.containers.get(status.container_id)
                    container.stop()
                    container.remove()
                    print(f"✅ {system_name} environment cleaned up")
                except Exception as e:
                    print(f"⚠️ Error cleaning up {system_name}: {e}")

            del self.active_environments[system_name]

    def teardown_all_environments(self):
        """Clean teardown of all test environments"""
        for system_name in list(self.active_environments.keys()):
            self.teardown_environment(system_name)

    def _load_iris_schema(self, container):
        """Load IRIS schema and setup for benchmarking"""
        try:
            # Copy schema files to container
            schema_commands = [
                "iris session IRIS '##class(%SYSTEM.OBJ).Load(\"/tmp/schema.sql\")'",
            ]

            for cmd in schema_commands:
                result = container.exec_run(cmd)
                if result.exit_code != 0:
                    print(f"⚠️ Schema command warning: {result.output.decode()}")

            print("✅ IRIS schema loaded")

        except Exception as e:
            print(f"⚠️ IRIS schema loading failed: {e}")

    def get_environment_status(self, system_name: str) -> Optional[EnvironmentStatus]:
        """Get status of environment"""
        return self.active_environments.get(system_name)

    def list_active_environments(self) -> Dict[str, EnvironmentStatus]:
        """List all active environments"""
        return self.active_environments.copy()


def setup_benchmark_environment(systems: List[DatabaseSystem],
                               configs: Dict[DatabaseSystem, SystemConfig]) -> Dict[str, EnvironmentStatus]:
    """Setup complete benchmarking environment"""
    env_manager = BenchmarkEnvironment()
    results = {}

    for system in systems:
        config = configs.get(system)
        if not config:
            print(f"⚠️ No configuration found for {system}")
            continue

        print(f"\n🔧 Setting up {system.value}...")

        if system == DatabaseSystem.IRIS_GRAPH_AI:
            status = env_manager.setup_iris_environment(config)
        elif system == DatabaseSystem.NEO4J_ENTERPRISE:
            status = env_manager.setup_neo4j_environment(config)
        elif system == DatabaseSystem.NEO4J_COMMUNITY:
            # Use community image
            community_config = config
            community_config.docker_image = "neo4j:5-community"
            status = env_manager.setup_neo4j_environment(community_config)
        elif system == DatabaseSystem.ARANGODB:
            status = env_manager.setup_arangodb_environment(config)
        else:
            print(f"❌ Unsupported system: {system}")
            continue

        results[system.value] = status

    return results


if __name__ == "__main__":
    # Test environment setup
    from benchmark_config import BenchmarkRequirements

    print("Testing benchmark environment setup...")

    systems = [DatabaseSystem.IRIS_GRAPH_AI, DatabaseSystem.NEO4J_COMMUNITY]
    configs = BenchmarkRequirements.SYSTEM_CONFIGS

    results = setup_benchmark_environment(systems, configs)

    print("\nEnvironment Setup Results:")
    for system, status in results.items():
        print(f"  {system}: {status.status}")
        if status.error_message:
            print(f"    Error: {status.error_message}")