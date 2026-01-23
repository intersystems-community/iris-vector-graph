"""
Main entry point for embedded Python fraud server

Run via: /usr/irissys/bin/irispython -m iris_fraud_server
"""

import sys
import os

# Check if running in embedded Python
try:
    import iris
except ImportError:
    print("ERROR: iris module not found. This must run via /usr/irissys/bin/irispython")
    sys.exit(1)

# Import and run the app
from iris_fraud_server.app import run_server

if __name__ == "__main__":
    run_server()