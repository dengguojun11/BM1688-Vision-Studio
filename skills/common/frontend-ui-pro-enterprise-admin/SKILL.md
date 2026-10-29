---
name: frontend-ui-pro-enterprise-admin
description: Professional enterprise-admin frontend UI skill for building polished management backoffice interfaces from business logic rather than weak existing frontend references. Use when Codex needs to implement admin pages such as management consoles, configuration centers, approval systems, operations dashboards, workflow tracking pages, list-detail workspaces, and enterprise data tables with strong structure, restraint, readability, and browser-verified finish.
---

# Frontend UI Pro Enterprise Admin

Use this skill when the target page should feel like a mature enterprise product rather than a generic generated admin page.

## 1. Visual Direction

Default visual qualities:

- calm and professional
- restrained color use
- dense but readable
- strong operational clarity
- modular and systematic

This skill should bias toward:

- high-quality admin console design
- configuration centers
- management workspaces
- status-heavy workflow pages
- enterprise data management views

It should not drift into marketing-site aesthetics unless the user explicitly wants that.

## 2. Read These References

Read:

- `references/page-archetypes.md`
- `references/admin-layout-rules.md`
- `references/admin-table-form-rules.md`

Also use:

- `../frontend-ui-pro/SKILL.md`
- `../frontend-ui-pro/references/visual-polish.md`
- `../frontend-ui-pro/references/verification-loop.md`

## 3. Core Biases

### Bias 1. Structure over decoration

Enterprise admin quality comes from:

- hierarchy
- information grouping
- action clarity
- density control
- status readability

Do not try to compensate for weak structure with more gradients, shadows, or decoration.

### Bias 2. Strong shells

Prefer page shells like:

- header + KPI strip + filter bar + table + detail drawer
- overview + grouped management panels
- workflow chain + artifact/status blocks + action rail
- settings summary + grouped configuration cards

### Bias 3. Tables are first-class UI

For enterprise admin work, tables are not secondary.

Design them deliberately:

- prioritize key identifiers
- use compact row height
- use disciplined chips and metadata
- make row actions quiet but accessible
- avoid wide noisy action clusters

### Bias 4. Safe, mature visual language

Prefer:

- neutral palettes
- 1 strong primary accent
- subtle borders
- measured radius
- controlled shadows
- crisp typography

Avoid:

- over-stylized gradients
- playful consumer-app styling
- excessive empty space

## 4. Default Page Archetypes

Use these by default:

- management list workspace
- policy/config center
- operational workflow board
- status control center
- audit/review queue

See:

- `references/page-archetypes.md`

## 5. Admin Layout Rules

Always ensure:

- top area explains page intent quickly
- filters never overpower the data region
- primary actions are obvious
- section boundaries are clean
- status language is consistent
- long forms are grouped and chunked

See:

- `references/admin-layout-rules.md`

## 6. Tables And Forms

When building data tables or forms:

- prioritize scan speed
- reduce field noise
- group related settings
- use helper text only where it reduces mistakes
- make destructive actions visually distinct

See:

- `references/admin-table-form-rules.md`

## 7. Delivery Standard

The final UI should feel:

- deliberate
- trustworthy
- efficient
- clean under pressure

If it still looks like a default CRUD scaffold, keep iterating.
