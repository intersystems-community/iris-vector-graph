#!/usr/bin/env python3
"""
NetworkX Integration for IRIS Graph-AI
Supports loading standard graph formats into IRIS using NetworkX
"""

import argparse
import json
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
import traceback

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False
    print("Error: NetworkX not available. Install with: pip install networkx")
    sys.exit(1)

try:
    import iris
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    print("Error: IRIS Python driver not available. Install with: pip install intersystems_irispython")
    sys.exit(1)

import pandas as pd
import numpy as np
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NetworkXIRISLoader:
    """
    High-performance loader for graph data using NetworkX → IRIS
    """

    def __init__(self, connection_params: Dict = None):
        """
        Initialize loader with IRIS connection parameters
        """
        self.conn_params = connection_params or {
            'hostname': os.getenv('IRIS_HOST', 'localhost'),
            'port': int(os.getenv('IRIS_PORT', 1973)),
            'namespace': os.getenv('IRIS_NAMESPACE', 'USER'),
            'username': os.getenv('IRIS_USER', '_SYSTEM'),
            'password': os.getenv('IRIS_PASSWORD', 'SYS')
        }
        self.conn = None

    def connect(self):
        """Establish IRIS connection with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.conn = iris.connect(**self.conn_params)

                # Test connection
                cursor = self.conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()

                logger.info("✓ Connected to IRIS")
                return True

            except Exception as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    logger.error(f"Failed to connect after {max_retries} attempts")
                    return False

    def load_format(self, file_path: str, format_type: str = None, **kwargs) -> nx.Graph:
        """
        Load graph from various formats using NetworkX
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Auto-detect format if not specified
        if format_type is None:
            format_type = self._detect_format(file_path)

        logger.info(f"Loading {format_type.upper()} file: {file_path}")

        try:
            if format_type == 'graphml':
                return nx.read_graphml(file_path)

            elif format_type == 'gml':
                return nx.read_gml(file_path)

            elif format_type == 'edgelist' or format_type == 'tsv':
                # Handle TSV/CSV edge lists
                delimiter = kwargs.get('delimiter', '\t' if format_type == 'tsv' else ' ')
                return nx.read_edgelist(file_path, delimiter=delimiter, data=True)

            elif format_type == 'csv':
                return self._load_csv_edgelist(file_path, **kwargs)

            elif format_type == 'adjlist':
                return nx.read_adjlist(file_path)

            elif format_type == 'pajek':
                return nx.read_pajek(file_path)

            elif format_type == 'gexf':
                return nx.read_gexf(file_path)

            elif format_type == 'jsonl':
                return self._load_jsonl_edges(file_path)

            else:
                raise ValueError(f"Unsupported format: {format_type}")

        except Exception as e:
            logger.error(f"Failed to load {format_type} file: {e}")
            raise

    def _detect_format(self, file_path: Path) -> str:
        """Auto-detect graph file format"""
        suffix = file_path.suffix.lower()

        format_map = {
            '.graphml': 'graphml',
            '.gml': 'gml',
            '.tsv': 'tsv',
            '.csv': 'csv',
            '.txt': 'edgelist',
            '.edges': 'edgelist',
            '.adjlist': 'adjlist',
            '.net': 'pajek',
            '.gexf': 'gexf',
            '.jsonl': 'jsonl'
        }

        return format_map.get(suffix, 'edgelist')

    def _load_csv_edgelist(self, file_path: Path, **kwargs) -> nx.Graph:
        """Load CSV edge list with proper handling"""
        df = pd.read_csv(file_path)

        # Detect column names
        source_col = kwargs.get('source_col', self._find_column(df, ['source', 'src', 'from', 'node1']))
        target_col = kwargs.get('target_col', self._find_column(df, ['target', 'dst', 'to', 'node2']))

        if not source_col or not target_col:
            raise ValueError("Could not identify source and target columns")

        # Create graph
        G = nx.DiGraph() if kwargs.get('directed', True) else nx.Graph()

        for _, row in df.iterrows():
            source = str(row[source_col])
            target = str(row[target_col])

            # Add edge attributes
            attrs = {col: row[col] for col in df.columns if col not in [source_col, target_col]}
            G.add_edge(source, target, **attrs)

        return G

    def _find_column(self, df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
        """Find column name from candidates"""
        df_cols = [col.lower() for col in df.columns]
        for candidate in candidates:
            if candidate.lower() in df_cols:
                return df.columns[df_cols.index(candidate.lower())]
        return None

    def _load_jsonl_edges(self, file_path: Path) -> nx.Graph:
        """Load JSONL format edges"""
        G = nx.DiGraph()

        with open(file_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())
                    source = data['source']
                    target = data['target']

                    # Extract attributes
                    attrs = {k: v for k, v in data.items() if k not in ['source', 'target']}
                    G.add_edge(source, target, **attrs)

                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON at line {line_num}: {e}")
                except KeyError as e:
                    logger.warning(f"Missing required field at line {line_num}: {e}")

        return G

    def import_graph(self, G: nx.Graph, node_type: str = 'entity',
                    batch_size: int = 5000, clear_existing: bool = False) -> Dict:
        """
        Import NetworkX graph into IRIS with optimized batching
        """
        if not self.conn:
            raise ConnectionError("Not connected to IRIS")

        start_time = time.time()
        stats = {'nodes': 0, 'edges': 0, 'properties': 0}

        try:
            cursor = self.conn.cursor()

            # Clear existing data if requested
            if clear_existing:
                logger.info("Clearing existing graph data...")
                cursor.execute("DELETE FROM rdf_edges")
                cursor.execute("DELETE FROM rdf_labels")
                cursor.execute("DELETE FROM rdf_props")

            # Insert node labels in batches
            logger.info(f"Importing {G.number_of_nodes()} nodes...")
            node_batches = [list(G.nodes())[i:i+batch_size]
                           for i in range(0, G.number_of_nodes(), batch_size)]

            for batch_num, node_batch in enumerate(node_batches):
                label_data = [(node, node_type) for node in node_batch]
                cursor.executemany(
                    "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                    label_data
                )
                stats['nodes'] += len(label_data)
                logger.info(f"Nodes batch {batch_num + 1}/{len(node_batches)}: {stats['nodes']} total")

            # Insert node properties
            logger.info("Importing node properties...")
            prop_data = []
            for node, attrs in G.nodes(data=True):
                for key, value in attrs.items():
                    if value is not None:
                        prop_data.append((node, key, str(value)))

            if prop_data:
                # Process properties in batches
                prop_batches = [prop_data[i:i+batch_size]
                               for i in range(0, len(prop_data), batch_size)]

                for batch_num, prop_batch in enumerate(prop_batches):
                    cursor.executemany(
                        "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                        prop_batch
                    )
                    stats['properties'] += len(prop_batch)
                    logger.info(f"Properties batch {batch_num + 1}/{len(prop_batches)}: {stats['properties']} total")

            # Insert edges in batches
            logger.info(f"Importing {G.number_of_edges()} edges...")
            edge_batches = [list(G.edges(data=True))[i:i+batch_size]
                           for i in range(0, G.number_of_edges(), batch_size)]

            for batch_num, edge_batch in enumerate(edge_batches):
                edge_data = []
                for source, target, attrs in edge_batch:
                    # Extract relationship type
                    relation = attrs.pop('relation', 'interacts_with')

                    # Prepare qualifiers
                    qualifiers = json.dumps(attrs) if attrs else '{}'

                    edge_data.append((source, relation, target, qualifiers))

                cursor.executemany(
                    "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                    edge_data
                )
                stats['edges'] += len(edge_data)
                logger.info(f"Edges batch {batch_num + 1}/{len(edge_batches)}: {stats['edges']} total")

            cursor.close()
            elapsed = time.time() - start_time

            logger.info(f"✓ Import completed in {elapsed:.2f}s")
            logger.info(f"  Nodes: {stats['nodes']}")
            logger.info(f"  Edges: {stats['edges']}")
            logger.info(f"  Properties: {stats['properties']}")

            return {
                'success': True,
                'elapsed_time': elapsed,
                'stats': stats
            }

        except Exception as e:
            logger.error(f"Import failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'stats': stats
            }

    def export_graph(self, output_path: str, format_type: str = 'graphml',
                    node_filter: str = None, limit: int = None) -> bool:
        """
        Export IRIS graph to NetworkX-compatible format
        """
        if not self.conn:
            raise ConnectionError("Not connected to IRIS")

        try:
            cursor = self.conn.cursor()

            # Build query with optional filters
            edge_query = """
                SELECT DISTINCT e.s, e.p, e.o_id, e.qualifiers
                FROM rdf_edges e
            """

            params = []
            if node_filter:
                edge_query += """
                    JOIN rdf_labels l1 ON e.s = l1.s
                    WHERE l1.label = ?
                """
                params.append(node_filter)

            if limit:
                edge_query += f" LIMIT {limit}"

            # Load edges
            cursor.execute(edge_query, params)
            edges = cursor.fetchall()

            # Build NetworkX graph
            G = nx.DiGraph()
            for source, predicate, target, qualifiers in edges:
                # Parse qualifiers
                try:
                    attrs = json.loads(qualifiers) if qualifiers else {}
                    attrs['relation'] = predicate
                except json.JSONDecodeError:
                    attrs = {'relation': predicate}

                G.add_edge(source, target, **attrs)

            # Add node properties
            prop_query = "SELECT s, key, val FROM rdf_props"
            if node_filter:
                prop_query += """
                    WHERE s IN (
                        SELECT s FROM rdf_labels WHERE label = ?
                    )
                """
                cursor.execute(prop_query, [node_filter])
            else:
                cursor.execute(prop_query)

            props = cursor.fetchall()
            for node, key, value in props:
                if node in G:
                    G.nodes[node][key] = value

            cursor.close()

            # Export to specified format
            output_path = Path(output_path)
            if format_type == 'graphml':
                nx.write_graphml(G, output_path)
            elif format_type == 'gml':
                nx.write_gml(G, output_path)
            elif format_type == 'edgelist':
                nx.write_edgelist(G, output_path, data=True)
            elif format_type == 'adjlist':
                nx.write_adjlist(G, output_path)
            elif format_type == 'gexf':
                nx.write_gexf(G, output_path)
            else:
                raise ValueError(f"Unsupported export format: {format_type}")

            logger.info(f"✓ Exported {G.number_of_nodes()} nodes, {G.number_of_edges()} edges to {output_path}")
            return True

        except Exception as e:
            logger.error(f"Export failed: {e}")
            return False

    def close(self):
        """Close IRIS connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("✓ IRIS connection closed")


def main():
    """Command-line interface for NetworkX-IRIS integration"""
    parser = argparse.ArgumentParser(
        description="Load graph data into IRIS using NetworkX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Load GraphML file
  python networkx_loader.py load protein_network.graphml --node-type protein

  # Load TSV edge list
  python networkx_loader.py load interactions.tsv --format tsv --delimiter tab

  # Load CSV with custom columns
  python networkx_loader.py load data.csv --format csv --source-col gene1 --target-col gene2

  # Export IRIS graph to GraphML
  python networkx_loader.py export output.graphml --format graphml --node-filter protein

  # Clear and reload
  python networkx_loader.py load network.gml --clear-existing
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Load command
    load_parser = subparsers.add_parser('load', help='Load graph file into IRIS')
    load_parser.add_argument('file_path', help='Path to graph file')
    load_parser.add_argument('--format', choices=['graphml', 'gml', 'tsv', 'csv', 'edgelist', 'adjlist', 'pajek', 'gexf', 'jsonl'],
                            help='File format (auto-detected if not specified)')
    load_parser.add_argument('--node-type', default='entity', help='Node type label (default: entity)')
    load_parser.add_argument('--batch-size', type=int, default=5000, help='Batch size for inserts (default: 5000)')
    load_parser.add_argument('--clear-existing', action='store_true', help='Clear existing graph data before loading')
    load_parser.add_argument('--delimiter', default='\t', help='Delimiter for edge list files (default: tab)')
    load_parser.add_argument('--source-col', help='Source column name for CSV files')
    load_parser.add_argument('--target-col', help='Target column name for CSV files')
    load_parser.add_argument('--directed', action='store_true', default=True, help='Create directed graph (default: True)')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export IRIS graph to file')
    export_parser.add_argument('output_path', help='Output file path')
    export_parser.add_argument('--format', default='graphml', choices=['graphml', 'gml', 'edgelist', 'adjlist', 'gexf'],
                              help='Output format (default: graphml)')
    export_parser.add_argument('--node-filter', help='Filter by node type label')
    export_parser.add_argument('--limit', type=int, help='Limit number of edges to export')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Initialize loader
    loader = NetworkXIRISLoader()

    try:
        # Connect to IRIS
        if not loader.connect():
            sys.exit(1)

        if args.command == 'load':
            # Load and import graph
            start_time = time.time()

            # Prepare kwargs for format-specific options
            load_kwargs = {}
            if hasattr(args, 'delimiter'):
                load_kwargs['delimiter'] = '\t' if args.delimiter == 'tab' else args.delimiter
            if hasattr(args, 'source_col') and args.source_col:
                load_kwargs['source_col'] = args.source_col
            if hasattr(args, 'target_col') and args.target_col:
                load_kwargs['target_col'] = args.target_col
            if hasattr(args, 'directed'):
                load_kwargs['directed'] = args.directed

            # Load graph
            G = loader.load_format(args.file_path, args.format, **load_kwargs)
            logger.info(f"✓ Loaded NetworkX graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

            # Import to IRIS
            result = loader.import_graph(
                G,
                node_type=args.node_type,
                batch_size=args.batch_size,
                clear_existing=args.clear_existing
            )

            if result['success']:
                total_time = time.time() - start_time
                logger.info(f"✓ Total time: {total_time:.2f}s")
                logger.info(f"✓ Performance: {result['stats']['nodes'] / total_time:.0f} nodes/sec, {result['stats']['edges'] / total_time:.0f} edges/sec")
            else:
                logger.error(f"❌ Load failed: {result.get('error', 'Unknown error')}")
                sys.exit(1)

        elif args.command == 'export':
            # Export graph
            success = loader.export_graph(
                args.output_path,
                format_type=args.format,
                node_filter=args.node_filter,
                limit=args.limit
            )

            if not success:
                sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        loader.close()

if __name__ == "__main__":
    main()