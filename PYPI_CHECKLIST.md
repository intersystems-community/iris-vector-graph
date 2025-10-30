# PyPI Publication Checklist

## Pre-Publication Review

### Package Metadata (pyproject.toml)
- [x] Package name: `iris-vector-graph`
- [x] Version: `1.0.0`
- [x] Description: Clear and concise
- [x] Author information: Tom Dyar
- [x] License: MIT
- [x] Python version requirement: `>=3.11`
- [x] Dependencies properly specified with version constraints
- [x] Optional extras defined: `dev`, `performance`, `visualization`, `ml`, `biodata`
- [ ] **TODO**: Review homepage/repository URLs
- [ ] **TODO**: Add keywords for PyPI search
- [ ] **TODO**: Add classifiers (Development Status, Intended Audience, Topic)

### Package Structure
- [x] Source code in `iris_vector_graph_core/`
- [x] `__init__.py` files in all package directories
- [x] Tests in `tests/` directory
- [x] Examples in `examples/`, `biomedical/`
- [ ] **TODO**: Add MANIFEST.in to include non-Python files (SQL, docs)
- [ ] **TODO**: Verify package build includes all necessary files

### Documentation
- [x] README.md with clear quick start (external vs embedded)
- [x] LICENSE file
- [x] CLAUDE.md for development guidance
- [ ] **TODO**: Create CHANGELOG.md for version tracking
- [ ] **TODO**: Add CONTRIBUTING.md guidelines
- [ ] **TODO**: Verify all docstrings are complete

### Code Quality
- [x] All contract tests passing (20/20)
- [x] Type hints throughout codebase
- [ ] **TODO**: Run `black .` for code formatting
- [ ] **TODO**: Run `isort .` for import sorting
- [ ] **TODO**: Run `flake8 .` and fix warnings
- [ ] **TODO**: Run `mypy iris_vector_graph_core/` and fix type issues

### Dependencies
- [x] All required dependencies in pyproject.toml
- [x] Optional dependencies in extras
- [ ] **TODO**: Verify minimum version constraints are correct
- [ ] **TODO**: Test installation in clean virtual environment
- [ ] **TODO**: Ensure `intersystems-irispython>=3.2.0` is accessible

### Security
- [ ] **TODO**: Remove any hardcoded credentials (check all files)
- [ ] **TODO**: Review .env.sample for sensitive data
- [ ] **TODO**: Scan for common security issues (bandit)
- [x] .gitignore excludes secrets

## Build and Test

### Local Build
```bash
# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build package
uv build  # or: python -m build

# Verify contents
tar tzf dist/iris-vector-graph-1.0.0.tar.gz
unzip -l dist/iris_vector_graph-1.0.0-py3-none-any.whl
```

### Test Installation
```bash
# Create clean venv
python -m venv test-venv
source test-venv/bin/activate

# Test installation from local build
pip install dist/iris-vector-graph-1.0.0.tar.gz

# Verify imports
python -c "import iris_vector_graph_core; print(iris_vector_graph_core.__version__)"

# Test with extras
pip install dist/iris-vector-graph-1.0.0.tar.gz[dev]

# Cleanup
deactivate
rm -rf test-venv
```

### TestPyPI Upload (Recommended First Step)
```bash
# Install twine
pip install twine

# Upload to TestPyPI
twine upload --repository testpypi dist/*

# Test installation from TestPyPI
pip install --index-url https://test.pypi.org/simple/ iris-vector-graph

# Verify it works
python -c "import iris_vector_graph_core"
```

## Publication

### Final Checks
- [ ] All tests passing
- [ ] Documentation reviewed
- [ ] Version number correct
- [ ] Git tag created: `git tag v1.0.0`
- [ ] Git tag pushed: `git push origin v1.0.0`

### Upload to PyPI
```bash
# Upload to production PyPI
twine upload dist/*

# Verify on PyPI
# https://pypi.org/project/iris-vector-graph/

# Test installation
pip install iris-vector-graph
```

## Post-Publication

### Announce
- [ ] Update README.md badges (PyPI version, downloads)
- [ ] Post announcement (if applicable)
- [ ] Update project status in TODO.md

### Monitor
- [ ] Check PyPI project page renders correctly
- [ ] Monitor initial installation issues
- [ ] Respond to early feedback

## Critical TODOs Before Publishing

1. **Add MANIFEST.in** to include SQL files, docs:
```
include README.md
include LICENSE
include CLAUDE.md
recursive-include sql *.sql
recursive-include docs *.md
recursive-include examples *.py *.md
```

2. **Update pyproject.toml** with PyPI metadata:
```toml
[project]
keywords = ["intersystems-iris", "vector-search", "knowledge-graph", "rag", "biomedical", "fraud-detection"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "Topic :: Database",
    "Topic :: Scientific/Engineering :: Bio-Informatics",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
```

3. **Create CHANGELOG.md** for v1.0.0

4. **Run linters** and fix all issues

5. **Test build** and installation in clean environment

## Notes

- External deployment is DEFAULT for simplicity
- Embedded deployment is ADVANCED/OPTIONAL (requires licensed IRIS)
- Ensure documentation clearly states IRIS database requirement
- Consider adding "Getting Started" tutorial for first-time users
