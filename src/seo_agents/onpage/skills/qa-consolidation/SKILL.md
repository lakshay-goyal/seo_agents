---
name: qa-consolidation
description: Scope gate, reference validation, dedup/conflicts, the <=15-change default view, and the before/after checklist computed from the final change list. Deterministic.
kind: deterministic
order: 12
---
## What the function does (`checks.check_qa`, `ops.gate`, `ops.consolidate`)
1. Scope gate: a head change (title, description, canonical, robots, OG/Twitter, JSON-LD) whose target is a shared
   shell/layout, or any head change on a `shared-shell` page (CSR SPA without prerender), becomes `blocked` with
   `depends_on: ["chg_prerender"]` (or `chg_per_route_head` for a shared layout) and is retargeted to the stack's
   per-route head recipe. Page-specific body content (H1, direct answer, sections, new links) on a shared file becomes
   `needs_input`.
2. Reference validation: no body insert into `<head>`, no per-instance edit on a shared template line, no `line: 1`
   placeholders. A target is concrete only when exploration marked it `verify: grounded` (or, for older output,
   confidence >= 0.8 and not computed/add).
3. Dedup by `dedup_key` (target key + op): the higher-impact change wins and lists the others in `supersedes`;
   different values on the same target are cross-linked in `conflicts_with`.
4. Split: `changes` = top {{DEFAULT_VIEW_CHANGES.max}} by impact/status/confidence, rest in `changes_more`; guidance in
   `advice`; owner facts in `client_inputs`.
5. Checklist `after` is derived from the final changes: true (a proposed change fixes it), "blocked",
   "needs_input", "needs_platform_change" or false. Nothing is assumed to pass.
6. `expected.html` is generated from the change list (review aid only).
