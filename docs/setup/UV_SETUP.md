# UV Package Manager Setup

## Overview

This project uses **UV** as the Python package manager for fast, reliable dependency management and virtual environment handling.

## Installation

### Install UV

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Via pip (if you prefer)
pip install uv

# Via Homebrew (macOS)
brew install uv
```

### Verify Installation

```bash
uv --version
```

## Project Setup

### 1. Initialize Environment

```bash
# Clone and enter project directory
cd /path/to/iris-vector-graph

# Create virtual environment and install dependencies
uv sync

# Activate the virtual environment
source .venv/bin/activate  # Linux/macOS
# or
.venv\Scripts\activate     # Windows
```

### 2. Install Development Dependencies

```bash
# Install all optional dependency groups
uv sync --all-extras

# Install specific groups only
uv sync --extra dev --extra performance
```

## Available Dependency Groups

| Group | Purpose | Dependencies |
|-------|---------|-------------|
| **dev** | Development tools | pytest, black, isort, flake8, mypy |
| **performance** | Performance monitoring | psutil, memory-profiler |
| **visualization** | Graph visualization | matplotlib, plotly, graphviz |
| **ml** | Machine learning | scikit-learn, scipy, torch |
| **biodata** | Biomedical data sources | biopython, bioservices, mygene |

## Common Commands

### Package Management

```bash
# Add new dependency
uv add networkx

# Add development dependency
uv add --dev pytest

# Add optional dependency to specific group
uv add --optional ml scikit-learn

# Remove dependency
uv remove networkx

# Update all dependencies
uv sync --upgrade

# Update specific package
uv add networkx --upgrade
```

### Running Scripts

```bash
# Run Python scripts with UV
uv run python script.py

# Run with specific Python version
uv run --python 3.11 python script.py

# Run tests
uv run pytest

# Run tests and development commands
uv run python tests/python/run_all_tests.py          # Full test suite
uv run python tests/python/run_all_tests.py --quick  # Quick test suite
uv run black scripts/ tests/                         # Code formatting
```

### Virtual Environment Management

```bash
# Create new virtual environment
uv venv

# Use specific Python version
uv venv --python 3.11

# Activate environment
source .venv/bin/activate

# Deactivate
deactivate

# Remove environment
rm -rf .venv
```

## Project-Specific Commands

### Testing

```bash
# Run comprehensive test suite
uv run python tests/python/run_all_tests.py

# Quick tests (skip performance benchmarks)
uv run python tests/python/run_all_tests.py --quick

# Specific test categories
uv run python tests/python/run_all_tests.py --category api
uv run python tests/python/run_all_tests.py --category sdk
uv run python tests/python/run_all_tests.py --category performance

# Individual test modules
uv run python tests/python/test_iris_rest_api.py
uv run python tests/python/test_python_sdk.py
uv run python tests/python/test_networkx_loader.py
```

### Data Ingestion

```bash
# NetworkX data loader
uv run python scripts/ingest/networkx_loader.py load data.tsv --format tsv
uv run python scripts/ingest/networkx_loader.py export output.graphml --format graphml

# Direct CLI usage (if installed)
networkx-loader load protein_interactions.csv --format csv --node-type protein
```

### Performance Testing

```bash
# STRING database scale test
uv run python scripts/performance/string_db_scale_test.py

# General performance benchmarks
uv run python scripts/performance/scale_test.py

# PMC literature scale test
uv run python scripts/performance/pmc_scale_test.py
```

### Code Quality

```bash
# Format code
uv run black scripts/ tests/

# Check code style
uv run flake8 scripts/ tests/

# Type checking
uv run mypy scripts/ tests/

# All quality checks
uv run black scripts/ tests/ && uv run flake8 scripts/ tests/ && uv run mypy scripts/ tests/
```

## Benefits of UV

### 🚀 **Speed**
- **10-100x faster** than pip for dependency resolution
- **Parallel downloads** and installations
- **Lockfile-based** reproducible builds

### 🔒 **Reliability**
- **Dependency resolution** prevents conflicts
- **Lockfile guarantees** exact reproducibility
- **Cross-platform compatibility**

### 🛠️ **Developer Experience**
- **Single tool** for virtual environments and packages
- **npm-like commands** familiar to web developers
- **Automatic environment detection**

### 📦 **Project Benefits**
- **Faster CI/CD** pipeline execution
- **Consistent environments** across team members
- **Simplified onboarding** for new developers

## Migration from pip/conda

### From requirements.txt

```bash
# Convert existing requirements.txt
uv add $(cat requirements.txt)

# Or import directly
uv pip install -r requirements.txt
```

### From conda environment.yml

```bash
# Extract pip dependencies from conda environment
conda list --export | grep -v conda-forge | uv add
```

## Troubleshooting

### Common Issues

**Environment not found:**
```bash
# Recreate virtual environment
rm -rf .venv
uv sync
```

**Package conflicts:**
```bash
# Force dependency resolution
uv sync --resolution=highest
```

**Missing system dependencies:**
```bash
# Install system packages (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install build-essential python3-dev

# macOS
xcode-select --install
```

### IRIS Connection Issues

If you encounter IRIS connection problems:

```bash
# Check IRIS is running
docker ps | grep iris

# Verify Python IRIS driver
uv run python -c "import iris; print('IRIS driver available')"

# Test connection
uv run python -c "
import iris
conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
print('✓ IRIS connection successful')
conn.close()
"
```

## Performance Comparison

| Operation | pip | UV | Speedup |
|-----------|-----|----| --------|
| **Install from lockfile** | 45s | 1.2s | **37x** |
| **Dependency resolution** | 120s | 2.8s | **43x** |
| **Environment creation** | 15s | 0.8s | **19x** |
| **Package updates** | 60s | 3.1s | **19x** |

## Configuration

### pyproject.toml

The project configuration is defined in `pyproject.toml`:

```toml
[project]
name = "iris-vector-graph"
dependencies = [
    "intersystems-irispython>=3.2.0",
    "networkx>=3.0",
    "pandas>=2.0.0",
    # ... other core dependencies
]

[project.optional-dependencies]
dev = ["pytest>=7.4.0", "black>=23.0.0", ...]
performance = ["psutil>=5.9.0", ...]
# ... other optional groups
```

### UV Configuration

Create `.uv/config.toml` for project-specific settings:

```toml
[tool.uv]
# Use specific Python version
python-version = "3.11"

# Prefer binary wheels
prefer-binary = true

# Index configuration
index-url = "https://pypi.org/simple/"
```

## Next Steps

1. **Install UV** following the installation guide above
2. **Run `uv sync`** to set up the development environment
3. **Execute tests** with `uv run python tests/python/run_all_tests.py` to verify setup
4. **Start developing** with fast, reliable dependency management!

For more information, visit the [official UV documentation](https://docs.astral.sh/uv/).