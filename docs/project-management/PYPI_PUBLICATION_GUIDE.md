# PyPI Publication Guide

## Package Ready for Publication ✅

The `iris-vector-graph` v1.0.0 package has been successfully built and validated:

- ✅ Package metadata updated (Thomas Dyar <thomas.dyar@intersystems.com>)
- ✅ Package structure verified (iris_vector_graph_core)
- ✅ Source and wheel distributions built
- ✅ Installation tested in clean virtual environment
- ✅ Core functionality imports verified
- ✅ Test suite passed (unit tests and contract tests)
- ✅ Twine validation passed

**Distribution files ready:**
- `dist/iris_vector_graph-1.0.0-py3-none-any.whl` (33KB)
- `dist/iris_vector_graph-1.0.0.tar.gz` (701KB)

---

## Prerequisites

### 1. Create PyPI Account

If you don't already have accounts:
- **TestPyPI**: https://test.pypi.org/account/register/
- **Production PyPI**: https://pypi.org/account/register/

### 2. Create API Tokens

**For TestPyPI:**
1. Go to https://test.pypi.org/manage/account/token/
2. Create a new API token with scope "Entire account (all projects)"
3. Copy the token (starts with `pypi-`)
4. Save it securely - you won't be able to see it again

**For Production PyPI:**
1. Go to https://pypi.org/manage/account/token/
2. Create a new API token with scope "Entire account (all projects)"
3. Copy the token (starts with `pypi-`)
4. Save it securely

### 3. Configure PyPI Credentials

Create or update `~/.pypirc`:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-YOUR-PRODUCTION-TOKEN-HERE

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-YOUR-TESTPYPI-TOKEN-HERE
```

**Security Note**: Make sure `~/.pypirc` has restricted permissions:
```bash
chmod 600 ~/.pypirc
```

---

## Upload Process

### Step 1: Upload to TestPyPI (Recommended)

Test the upload process with TestPyPI first:

```bash
# Upload to TestPyPI
python -m twine upload --repository testpypi dist/*

# Expected output:
# Uploading distributions to https://test.pypi.org/legacy/
# Uploading iris_vector_graph-1.0.0-py3-none-any.whl
# Uploading iris_vector_graph-1.0.0.tar.gz
# View at: https://test.pypi.org/project/iris-vector-graph/1.0.0/
```

### Step 2: Test Installation from TestPyPI

Verify the package can be installed from TestPyPI:

```bash
# Create clean test environment
python -m venv /tmp/test-pypi-install
source /tmp/test-pypi-install/bin/activate

# Install from TestPyPI (use --index-url for test.pypi.org)
# Note: Dependencies will be installed from production PyPI
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    iris-vector-graph

# Test import
python -c "import iris_vector_graph_core; print(f'Version: {iris_vector_graph_core.__version__}')"

# Expected output: Version: 1.0.0

# Deactivate and cleanup
deactivate
rm -rf /tmp/test-pypi-install
```

### Step 3: Upload to Production PyPI

Once TestPyPI installation is verified, upload to production:

```bash
# Upload to production PyPI
python -m twine upload dist/*

# Expected output:
# Uploading distributions to https://upload.pypi.org/legacy/
# Uploading iris_vector_graph-1.0.0-py3-none-any.whl
# Uploading iris_vector_graph-1.0.0.tar.gz
# View at: https://pypi.org/project/iris-vector-graph/1.0.0/
```

### Step 4: Verify Production Installation

Test the production package:

```bash
# Create clean test environment
python -m venv /tmp/test-prod-install
source /tmp/test-prod-install/bin/activate

# Install from production PyPI
pip install iris-vector-graph

# Test import
python -c "
from iris_vector_graph_core import IRISGraphEngine, GraphSchema, VectorOptimizer
print('✅ Package installed successfully from PyPI')
"

# Deactivate and cleanup
deactivate
rm -rf /tmp/test-prod-install
```

---

## Post-Publication Steps

### 1. Create GitHub Release

```bash
# Create and push git tag
git tag -a v1.0.0 -m "Release v1.0.0: Initial PyPI publication

- High-performance biomedical knowledge graph
- IRIS-native vector search with HNSW optimization
- GraphQL and openCypher query interfaces
- Python + ObjectScript TSP implementations
- Comprehensive documentation and examples
"

git push origin v1.0.0

# Create GitHub release via web UI or gh CLI
gh release create v1.0.0 \
    --title "v1.0.0 - Initial Public Release" \
    --notes "See CHANGELOG.md for full release notes" \
    dist/iris_vector_graph-1.0.0-py3-none-any.whl \
    dist/iris_vector_graph-1.0.0.tar.gz
```

### 2. Update Documentation

Update `README.md` to include PyPI installation instructions:

```markdown
## Installation

Install from PyPI:

```bash
pip install iris-vector-graph
```

For development with optional dependencies:

```bash
# With biomedical data tools
pip install iris-vector-graph[biodata]

# With development tools
pip install iris-vector-graph[dev]

# With ML/performance tools
pip install iris-vector-graph[ml,performance]
```
\`\`\`

### 3. Announce Release

Consider announcing the release:
- InterSystems Developer Community
- GitHub Discussions
- Project documentation site
- Social media (LinkedIn, Twitter, etc.)

---

## Troubleshooting

### Issue: "File already exists"

If you get an error that the file already exists on PyPI:

```
HTTPError: 400 Bad Request from https://upload.pypi.org/legacy/
File already exists.
```

This means v1.0.0 has already been published. You cannot overwrite a published version. Options:
1. If this is a mistake, you can publish a new patch version (v1.0.1)
2. If you need to make changes, increment the version in `pyproject.toml`

### Issue: "Invalid or non-existent authentication"

If you get authentication errors:
1. Verify your API token is correct in `~/.pypirc`
2. Ensure the token has the correct permissions
3. Check that you're using `username = __token__` (not your PyPI username)

### Issue: Package description rendering issues

If the README doesn't render correctly on PyPI:
1. Check that README.md follows CommonMark specification
2. Validate with: `python -m twine check dist/*`
3. Preview on TestPyPI before publishing to production

---

## Version Management

For future releases:

1. **Update version** in `pyproject.toml`:
   ```toml
   version = "1.0.1"  # or "1.1.0", "2.0.0", etc.
   ```

2. **Update CHANGELOG.md** with release notes

3. **Rebuild package**:
   ```bash
   rm -rf dist/ build/
   python -m build
   ```

4. **Follow upload process** from Step 1 above

---

## Package URLs

After publication, your package will be available at:

- **Production PyPI**: https://pypi.org/project/iris-vector-graph/
- **TestPyPI**: https://test.pypi.org/project/iris-vector-graph/
- **GitHub**: https://github.com/intersystems-community/iris-vector-graph
- **Documentation**: https://github.com/intersystems-community/iris-vector-graph/tree/main/docs

---

## Summary Checklist

Before uploading to production PyPI:

- [ ] Package built successfully (`dist/` contains wheel and tarball)
- [ ] `twine check dist/*` passes
- [ ] Tested installation in clean virtual environment
- [ ] Core imports work correctly
- [ ] TestPyPI upload successful
- [ ] TestPyPI installation verified
- [ ] README renders correctly on TestPyPI
- [ ] Version number is correct and hasn't been published before
- [ ] CHANGELOG.md is up to date
- [ ] Git repository is clean (all changes committed)

Once all checks pass, proceed with production upload!
