---
name: frontend-ui-pro-data-console
description: Professional data-console frontend UI skill for building polished operational dashboards, monitoring consoles, analytics workbenches, workflow status hubs, and model or data pipeline control rooms from business logic. Use when Codex needs to turn metrics, job states, validation results, workflow stages, comparisons, and drill-down analysis into strong console-style UI with clear scanning, dense signal presentation, and browser-verified finish.
---

# Frontend UI Pro Data Console

Use this skill when the page should feel like a professional control room, analytics workbench, or operational console.

## 1. Visual Direction

Default visual qualities:

- signal-first
- operationally dense
- highly scannable
- clear status language
- more “control room” than “settings form”

Best suited for:

- dashboards
- workflow status hubs
- model or job monitoring pages
- export / validation consoles
- report comparison pages
- operational drill-down views

## 2. Read These References

Read:

- `references/console-archetypes.md`
- `references/metric-status-layout.md`
- `references/drilldown-patterns.md`

Also use:

- `../frontend-ui-pro/SKILL.md`
- `../frontend-ui-pro/references/business-to-ui.md`
- `../frontend-ui-pro/references/verification-loop.md`

## 3. Core Biases

### Bias 1. Signal before chrome

The first screen should tell the operator:

- current health
- current stage
- blockers
- key metrics
- urgent actions

### Bias 2. Dense but legible

This style can be denser than generic admin UI, but only if hierarchy stays crisp.

Use:

- compact summary cards
- controlled chips
- well-grouped tables
- visible exception panels

### Bias 3. Status language is central

A data console should have a clear status system:

- ready / pending / processing / failed
- healthy / warning / critical
- complete / blocked / missing

Make status visible across cards, flows, tables, and detail areas.

### Bias 4. Support drill-down without losing context

Operators should be able to:

- scan global status
- inspect one record deeply
- compare exceptions
- return without losing place

Prefer drawers, side panels, tabs, or sectional drill-downs over full context switching where possible.

## 4. Default Page Archetypes

Prefer these:

- monitoring dashboard
- workflow hub
- comparison analysis page
- queue and exception console
- artifact delivery console

See:

- `references/console-archetypes.md`

## 5. Metrics And Status Layout

Always decide:

- which metric is headline
- which metric is supporting
- which status is blocking
- what deserves alert emphasis

See:

- `references/metric-status-layout.md`

## 6. Drill-Down Patterns

Use drill-downs intentionally:

- detail drawer
- split pane
- tabbed evidence region
- exception modal

See:

- `references/drilldown-patterns.md`

## 7. Delivery Standard

The final page should feel like a serious operational tool:

- fast to scan
- easy to compare
- clear under pressure
- strong enough for expert users

If it looks like a marketing dashboard or a generic CRUD page, keep iterating.
