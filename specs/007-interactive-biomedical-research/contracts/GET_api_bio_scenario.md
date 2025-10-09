# Contract: GET /api/bio/scenario/{scenario_name}

**Endpoint**: `/api/bio/scenario/{scenario_name}`
**Method**: GET
**Purpose**: Load pre-configured demo scenarios with sample data

**Requirements**: FR-029 (sample queries and guided tours)

---

## Request Parameters

**Path**:
- `scenario_name` (string, required): One of ["cancer_protein", "metabolic_pathway", "drug_target"]

**Example**: `/api/bio/scenario/cancer_protein`

---

## Response (200 OK)

Returns HTML form pre-filled with scenario data (HTMX swaps into `#search-form` div).

**cancer_protein scenario**:
```html
<form hx-post="/api/bio/search" hx-target="#results">
  <input name="query_text" value="TP53" />
  <select name="query_type">
    <option value="name" selected>By Name</option>
  </select>
  <input name="top_k" value="10" type="number" />
  <button type="submit">Search Proteins</button>
</form>
```

**metabolic_pathway scenario**:
```html
<form hx-post="/api/bio/pathway" hx-target="#results">
  <input name="source_protein_id" value="ENSP00000306407" />  <!-- GAPDH -->
  <input name="target_protein_id" value="ENSP00000316649" />  <!-- LDHA -->
  <input name="max_hops" value="2" type="number" />
  <button type="submit">Find Pathway</button>
</form>
```

---

## Error Responses

**404 Not Found** (invalid scenario):
```json
{
  "detail": "Scenario 'invalid_name' not found. Available: cancer_protein, metabolic_pathway, drug_target"
}
```

---

## Implementation Notes

- Mirrors fraud demo scenario pattern (GET /api/fraud/scenario/{name})
- Returns FastHTML form components
- HTMX swaps form into page without reload
- Each scenario demonstrates different capability (similarity search, pathways, hybrid search)
