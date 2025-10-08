# Research: Interactive Demo Web Application Technologies

**Date**: 2025-01-06
**Feature**: Interactive IRIS Demo Web Interface (005-interactive-demo-web)

## 1. FastHTML + HTMX Integration

### Decision: Server-Side Session Management with FT Component Architecture

**Rationale**: FastHTML's parameter injection system (allowing `session`, `sess`, `htmx` parameters) reduces boilerplate while maintaining security through signed cookies. The FT object architecture provides type safety and composability while creating direct correspondence to HTMX attributes (`hx_post`, `hx_swap`, etc.). This eliminates complex client-side state synchronization while maintaining responsiveness through hypermedia-driven updates.

**Alternatives Considered**:
- **Traditional SPA with JSON APIs**: Rejected due to complexity overhead and unnecessary JavaScript framework dependencies
- **Server-side only (no HTMX)**: Rejected due to poor UX from full page reloads
- **Client-side session storage**: Rejected due to security concerns

**Implementation Notes**:
- Use FastHTML's built-in signed cookie sessions with parameter injection
- Implement FT object-based component design for declarative HTMX attributes
- Leverage out-of-band swaps (`hx-swap-oob`) for multi-target updates from single requests
- Key patterns: Click-to-edit, target-specific updates, server-side form validation with immediate feedback

---

## 2. D3.js Force-Directed Network Graphs

### Decision: Canvas Rendering with Barnes-Hut Approximation and Hierarchical Clustering

**Rationale**: Canvas provides 10-100x performance improvement over SVG for large networks (500+ nodes) by eliminating per-node DOM overhead. Barnes-Hut reduces force calculation complexity from O(n²) to O(n log n). For biological networks (protein interactions), hierarchical clustering reduces cognitive load by grouping related entities while maintaining exploration capabilities.

**Alternatives Considered**:
- **Pure SVG**: Rejected for 500+ nodes due to DOM overhead causing <10 FPS
- **WebGL**: Considered but rejected for Phase 1 due to complexity; revisit for 10,000+ nodes
- **Static layouts**: Rejected as force-directed provides better automatic organization
- **External graph libraries**: Rejected to maintain control over HTMX integration

**Implementation Notes**:
- **Rendering**: Canvas for network (500+ nodes), SVG overlay for interactive controls
- **Force optimization**: Barnes-Hut approximation with d3-force-reuse plugin (10-90% performance gain)
- **Layout**: Hierarchical clustering with progressive disclosure (expand/collapse meta-nodes)
- **Spatial optimization**: Quadtree spatial indexing for hit detection and neighbor finding
- **Performance targets**: <2s initial layout, 60 FPS during interaction, <16ms hit detection

---

## 3. Bitemporal Query UI Patterns

### Decision: Two-Dimensional Timeline with Temporal Context Switcher

**Rationale**: Two-dimensional visualization directly maps to bitemporal concepts (valid-time vs system-time), making temporal relationships spatially intuitive. Dual timestamp pickers address the cognitive challenge of reasoning about multiple time dimensions by providing explicit controls for each axis. This approach aligns with MarkLogic and XTDB patterns proven in production systems.

**Alternatives Considered**:
- **Single timeline with mode toggle**: Rejected as users lose context when switching between time dimensions
- **Table-based view**: Rejected as poor for pattern recognition and temporal relationships
- **3D visualization**: Rejected due to navigation complexity and accessibility issues
- **Text-based temporal queries**: Rejected as requiring expertise in temporal query languages

**Implementation Notes**:
- **Primary interface**: Dual timestamp pickers with "as-of" view toggle
- **Diff visualization**: Side-by-side comparison with highlighted changes (removed/added/changed)
- **Audit trail**: Interactive timeline with event markers and drill-down
- **Key UX principles**: Temporal context awareness, visual temporal anchoring, progressive complexity disclosure
- **Consistent temporal metaphors**: Use spatial positioning (left=past, right=future)

---

## 4. HTTP Client Patterns for Demo Apps

### Decision: httpx AsyncClient with Circuit Breaker and Exponential Backoff

**Rationale**: httpx AsyncClient with persistent connection pools provides optimal performance through connection reuse (reduces TCP handshake overhead). Circuit breakers prevent cascading failures by stopping requests to failing services, while exponential backoff with jitter prevents thundering herd problems. For demos, graceful degradation ensures presentations succeed even with backend issues.

**Alternatives Considered**:
- **requests library**: Rejected due to lack of async support and connection pooling limitations
- **aiohttp**: Considered but rejected in favor of httpx's superior API and type hints
- **Simple retry without circuit breaker**: Rejected as it exhausts resources during outages
- **No retry logic**: Rejected as demos would fail on transient network issues

**Implementation Notes**:
- **Client configuration**: Single long-lived httpx.AsyncClient with connection pooling
- **Retry strategy**: Exponential backoff with jitter for transient failures (max 3 retries)
- **Circuit breaker**: 5 failures → open circuit for 60s → attempt recovery
- **Graceful degradation**: Fallback to cached demo data when circuit open
- **Connection pooling**: max_connections=100, max_keepalive=20, HTTP/2 enabled
- **Timeout configuration**: 30s total, 10s connect timeout

---

## 5. Demo Data Management

### Decision: Faker-Based Synthetic Generation with Environment Toggle

**Rationale**: Faker provides realistic synthetic data while guaranteeing zero PII exposure. Environment-based toggling separates concerns (production vs demo) without UI complexity. Pre-generating and committing demo data ensures instant availability without runtime generation overhead, critical for live demos.

**Alternatives Considered**:
- **Manual demo data creation**: Rejected due to maintenance burden and inconsistency
- **Anonymized production data**: Rejected due to GDPR/privacy risks and complexity
- **UI toggle for demo mode**: Rejected as it adds UI complexity and potential production contamination
- **Random data without Faker**: Rejected as it lacks semantic coherence

**Implementation Notes**:
- **Generation**: Faker library with custom biomedical providers (protein names, pathways)
- **Demo mode**: Environment variable `DEMO_MODE=true` with runtime detection
- **Storage**: Pre-generated demo datasets committed to repo (instant load)
- **Sanitization**: Microsoft Presidio for PII detection + SHA-256 hashing for identifiers (if converting production data)
- **Deterministic seed**: Use seed=42 for reproducible demos
- **Visual indicator**: Demo mode banner in UI when DEMO_MODE=true

---

## Architecture Summary

**Technology Stack**:
- **Backend**: FastHTML with HTMX for server-side rendering and reactive updates
- **Visualization**: D3.js with Canvas rendering (500+ nodes) and hierarchical clustering
- **HTTP Client**: httpx AsyncClient with circuit breaker and exponential backoff
- **Demo Data**: Faker-based generation with environment toggle
- **Session Management**: FastHTML signed cookie sessions (server-side)

**Critical Success Factors**:
1. **Performance**: Canvas rendering + Barnes-Hut + connection pooling achieves <100ms interactions
2. **Resilience**: Circuit breaker + demo data fallback ensures demos never fail
3. **UX**: Progressive enhancement maintains accessibility while providing modern interactivity
4. **Privacy**: Faker ensures zero PII exposure in demo environments

**All [NEEDS CLARIFICATION] items resolved**:
1. ✅ Deployment target: Internal first, environment toggle for external demos
2. ✅ Authentication: None initially (internal network), add basic auth if external
3. ✅ Data privacy: DEMO_MODE toggle (real data internal, synthetic data external)
4. ✅ Graph visualization limits: Auto-cluster at 500 nodes, progressive disclosure
5. ✅ Export formats: JSON + CSV (PDF deferred to future iteration)

---

**Phase 0 Complete**: Ready for Phase 1 (Design & Contracts)
