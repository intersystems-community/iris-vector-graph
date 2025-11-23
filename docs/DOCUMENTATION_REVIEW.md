# Documentation Review - November 2025

**Date**: 2025-11-23
**Version Reviewed**: 1.1.6
**Reviewer**: Claude Code
**Status**: 🟡 Needs Updates

---

## Executive Summary

The iris-vector-graph documentation is generally comprehensive but contains several outdated references and inconsistencies that need to be addressed. Most critical are:

1. **Outdated module names** (iris_vector_graph_core → iris_vector_graph)
2. **Missing PPR connection wrapper fix documentation** (v1.1.7 pending)
3. **Inconsistent port references** across different Docker configurations
4. **Package name inconsistencies** in installation instructions

---

## Critical Issues

### 1. Module Name References ❌

**Files Affected**:
- `docs/setup/QUICKSTART.md` (lines 165-167)
- `docs/setup/INSTALLATION.md` (lines 166-172)

**Issue**: Documentation references `iris_vector_graph_core` which was the old module name.

**Current State**:
```python
from iris_vector_graph_core.engine import IRISGraphEngine  # ❌ WRONG
```

**Should Be**:
```python
from iris_vector_graph import IRISGraphEngine  # ✅ CORRECT
```

**Impact**: Users following quickstart will get ImportError

**Fix Required**: Update all references from `iris_vector_graph_core` to `iris_vector_graph`

---

### 2. Missing Recent PPR Fix Documentation ⚠️

**What's Missing**:
- HIPPORAG_CONNECTION_FIX.md is in root, should be in docs/
- ppr-functional-index-deployment-summary.md (dated 2025-11-07) doesn't mention the v1.1.7 connection wrapper fix
- No mention of ConnectionManager compatibility in PPR documentation

**Recent Fix** (committed but not documented):
```python
# Handle both raw irissdk.IRISConnection and wrapped connection objects
raw_conn = conn
if hasattr(conn, '_connection'):  # Wrapped connection (e.g., from ConnectionManager)
    raw_conn = conn._connection
elif hasattr(conn, 'connection'):  # Alternative wrapper pattern
    raw_conn = conn.connection
```

**Files Modified**:
- `iris_vector_graph/ppr_functional_index.py` (lines 94-109)
- `iris_vector_graph/ppr_globals.py` (lines 27-34, 85-92)

**Impact**: Integration users (like HippoRAG) need to know about this compatibility layer

**Fix Required**:
1. Move HIPPORAG_CONNECTION_FIX.md to docs/ppr-optimization/
2. Update ppr-functional-index-deployment-summary.md with v1.1.7 changes
3. Add ConnectionManager integration notes to QUICKSTART.md

---

### 3. Port Configuration Inconsistencies 🔧

**Issue**: Different Docker configurations use different port mappings, but not all docs reflect this.

**Current Standards** (per CLAUDE.md):
- **Default IRIS**: `1972:1972` and `52773:52773` (docker-compose.yml)
- **Licensed IRIS (ACORN-1)**: `21972:1972` and `252773:52773` (docker-compose.acorn.yml)
- **HippoRAG Demo**: `41972:1972` and ports vary

**Documentation Issues**:
- INSTALLATION.md correctly documents this (lines 153-177) ✅
- QUICKSTART.md uses hardcoded 1973 in connection examples (line 150) ❌
- Some examples show 1972, others 1973, others 21972

**Fix Required**: Standardize all connection examples to use environment variables or clearly indicate which port mapping applies

---

### 4. Package Installation Inconsistencies 📦

**INSTALLATION.md** shows:
```bash
pip install iris-vector-graph        # Core features
pip install iris-vector-graph[ml]    # + Machine learning
pip install iris-vector-graph[dev]   # + Development tools
```

**pyproject.toml** shows:
```toml
[project.optional-dependencies]
dev = ["pytest", "black", "isort", "flake8", "mypy"]
ml = ["scikit-learn", "xgboost", ...]  # (if exists)
```

**Issue**: Need to verify optional-dependencies match documentation

**Fix Required**: Audit pyproject.toml to ensure optional dependencies match docs

---

## Minor Issues

### 5. Outdated Performance Benchmarks 📊

**docs/performance/ppr-functional-index-deployment-summary.md**:
- Shows Functional Index is 1,400x SLOWER (20,013ms vs 14ms)
- This contradicts recent improvements

**Current Reality** (from HIPPORAG_CONNECTION_FIX.md):
- Functional Index: 184ms (10K nodes)
- Pure Python: 1,631ms (10K nodes)
- **8.9x faster** with Functional Index

**Issue**: Performance documentation is severely outdated

**Fix Required**: Update performance section with current benchmarks

---

### 6. Schema SQL File References 📄

**INSTALLATION.md** shows:
```bash
docker exec -i iris_db /usr/irissys/bin/irissession IRIS -U USER < sql/schema.sql
docker exec -i iris_db /usr/irissys/bin/irissession IRIS -U USER < sql/operators.sql
```

**Issue**: These commands assume SQL files are IRIS-ready, but recent issues with JSON datatype and comment filtering suggest users should use GraphSchema API instead

**Recommended Pattern**:
```python
from iris_vector_graph.schema import GraphSchema
schema = GraphSchema(conn)
result = schema.ensure_schema()
```

**Fix Required**: Update installation guide to recommend Python API over raw SQL execution

---

### 7. Missing Connection Wrapper Documentation 🔌

**Issue**: No documentation explains when/how to use connection wrappers with iris-vector-graph

**What's Needed**:
- Documentation of ConnectionManager pattern
- When to use raw vs wrapped connections
- How iris.createIRIS() unwrapping works
- Error messages users might see

**Fix Required**: Create docs/architecture/CONNECTION_WRAPPERS.md

---

## Documentation Structure Assessment

### Well-Organized ✅
- `/docs/setup/` - Clear setup guides
- `/docs/architecture/` - Good architectural docs
- `/docs/performance/` - Comprehensive benchmarks
- `/docs/ppr-optimization/` - Detailed PPR analysis

### Needs Improvement 🔧
- **Root directory clutter**: HIPPORAG_CONNECTION_FIX.md should be in docs/
- **Inconsistent dates**: Some docs dated 2025-11-07, others undated
- **No versioning**: Docs don't indicate which version they apply to
- **Missing index**: No docs/INDEX.md or docs/README.md to guide users

---

## Recommended Actions

### High Priority 🔴

1. **Fix module name references**
   - Files: QUICKSTART.md, INSTALLATION.md
   - Change: `iris_vector_graph_core` → `iris_vector_graph`

2. **Move and update PPR documentation**
   - Move: HIPPORAG_CONNECTION_FIX.md → docs/ppr-optimization/
   - Update: ppr-functional-index-deployment-summary.md with v1.1.7 changes
   - Add: Connection wrapper integration notes

3. **Standardize port references**
   - Use environment variables in examples
   - Add clear notes about which port mapping applies
   - Update hardcoded 1973 references

### Medium Priority 🟡

4. **Update performance benchmarks**
   - Correct PPR Functional Index performance claims
   - Add recent benchmark results
   - Mark outdated sections clearly

5. **Schema initialization guidance**
   - Recommend Python API over raw SQL
   - Document GraphSchema.ensure_schema()
   - Add troubleshooting for common errors

### Low Priority 🟢

6. **Create missing docs**
   - docs/architecture/CONNECTION_WRAPPERS.md
   - docs/INDEX.md (documentation map)
   - Version indicators in all docs

7. **Verify optional dependencies**
   - Audit pyproject.toml [ml] and [dev] extras
   - Update INSTALLATION.md if mismatches found

---

## Files Requiring Updates

### Immediate Updates Needed
- [ ] `docs/setup/QUICKSTART.md` - Module names, ports
- [ ] `docs/setup/INSTALLATION.md` - Module names, schema API
- [ ] `docs/ppr-optimization/ppr-functional-index-deployment-summary.md` - Performance, v1.1.7
- [ ] `HIPPORAG_CONNECTION_FIX.md` - Move to docs/

### Review and Verify
- [ ] `docs/performance/BENCHMARKS.md` - Check if current
- [ ] `docs/architecture/ACTUAL_SCHEMA.md` - Verify examples work
- [ ] `pyproject.toml` - Verify optional-dependencies

### New Documentation Needed
- [ ] `docs/architecture/CONNECTION_WRAPPERS.md` - New file
- [ ] `docs/INDEX.md` - New file

---

## Notes

### What's Working Well
- CLAUDE.md is comprehensive and accurate ✅
- Architecture documentation is detailed ✅
- Performance analysis is thorough ✅
- CHANGELOG.md is excellent (detailed bug analysis) ✅

### Process Improvements
- Add version indicators to all documentation
- Use consistent date format (YYYY-MM-DD)
- Add "Last Updated" headers
- Reference specific package versions

---

## Next Steps

1. Create TODO task list for documentation updates
2. Prioritize high-priority fixes
3. Verify all code examples actually work
4. Test installation guides on clean environment
5. Update CHANGELOG.md with v1.1.7 connection wrapper fix

---

**Review Status**: 🟡 In Progress
**Estimated Effort**: 4-6 hours to complete all fixes
**Priority**: Medium (doesn't block functionality, but affects user experience)
