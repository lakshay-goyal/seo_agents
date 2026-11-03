"""Schema.org JSON-LD for on-page proposals: visible facts -> @id graph nodes -> strict validation.

- `schema_facts`: facts read from the page exactly as visitors see them (prices, ratings, steps, Q&A, dates, media).
- `schema_nodes`: node builders shared by the structured-data check and the article renderer.
- `schema_rules`: schema.org + Google structured-data rules; invalid optional nodes are pruned, never shipped.
"""
