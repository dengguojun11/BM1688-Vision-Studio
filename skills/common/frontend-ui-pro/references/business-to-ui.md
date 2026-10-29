# Business To UI

Convert backend logic into interface structure before writing components.

## 1. Determine the page archetype

Use the business goal to choose the page shape.

### Operational dashboard

Use when users need:

- fast scanning
- health/status monitoring
- top-level KPIs
- quick action entry

Typical structure:

- page header
- KPI strip
- health/status cards
- recent items / alerts / queue panels
- action shortcuts

### Control center

Use when users need:

- centralized command and state awareness
- multiple subsystems on one page
- high signal density

Typical structure:

- header with current global state
- primary summary row
- grouped control sections
- active jobs / resources / warnings

### List + detail workspace

Use when users need:

- scan many records
- compare records quickly
- inspect one item deeply without leaving context

Typical structure:

- filters and actions on top
- main table/list
- sticky detail side panel or drawer

### Configuration center

Use when users need:

- edit rules, parameters, thresholds, presets
- understand setting groups and dependencies

Typical structure:

- overview summary
- grouped setting cards or sections
- contextual helper text
- validation and save states

### Workflow status board

Use when users need:

- understand a chain of stages
- know what is blocked, completed, pending, or failed

Typical structure:

- chain summary strip
- stage cards
- current blockers
- artifact and action areas

### Analysis page

Use when users need:

- compare outcomes
- inspect metrics
- understand trends or exceptions

Typical structure:

- headline metrics
- visual or structural comparisons
- exceptions table
- drill-down area

## 2. Determine information priority

Sort content into four levels:

- primary: the one thing users came for
- secondary: supporting signals needed for decisions
- contextual: details used after inspection
- background: logs, notes, or advanced fields

Primary content should be visible without scrolling when practical.

## 3. Determine interaction priority

Identify:

- primary action
- high-frequency actions
- dangerous actions
- bulk actions
- contextual actions

Do not give all actions equal visual weight.

## 4. Determine comparison needs

If users compare many entities:

- use tables or structured grids
- provide sticky headers
- provide status chips and compact summaries

If users inspect one object deeply:

- use side panels, drawers, tabs, or sectional detail layouts

## 5. Determine status model

Every business page should expose a status language:

- healthy / warning / failed
- ready / pending / blocked
- published / draft / archived
- matched / inconsistent / missing

Make this status system visible and consistent across cards, tables, and details.
