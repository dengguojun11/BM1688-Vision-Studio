---
name: frontend-ui-pro
description: Professional frontend UI skill for turning business logic, API structure, and workflow requirements into polished enterprise-grade web interfaces. Use when Codex needs to design or implement high-quality frontend pages, dashboards, workflow consoles, configuration centers, list/detail pages, or management UIs with strong information architecture, visual hierarchy, responsive layout, and browser-verified polish. Especially useful when the existing project UI is not a good reference and the page should be redesigned from first principles rather than imitating current code.
---

# Frontend UI Pro

Use this skill to produce professional, implementation-ready frontend UI from business logic rather than from weak existing visuals.

## 1. Default Working Mode

Always treat the task as a design-and-implementation problem, not just a code-writing problem.

Follow this sequence:

1. infer the page type from business logic
2. define information hierarchy
3. choose a layout pattern
4. define visual direction and component behavior
5. implement the UI
6. verify in a browser and iterate

If the task is large, start with one key page and make it excellent before expanding.

## 2. Read These References

Read these before implementation:

- `references/business-to-ui.md`
- `references/layout-patterns.md`
- `references/visual-polish.md`
- `references/verification-loop.md`

Read `templates/delivery-checklist.md` when preparing the final page or handoff.

## 3. Core Rules

### Rule 1. Do not inherit weak existing UI by default

If the current frontend is visually weak, use the existing code only for:

- data shape
- route behavior
- API integration
- state flow

Do not copy weak spacing, typography, card structure, table structure, or color decisions unless the user explicitly wants consistency.

### Rule 2. Design from business structure first

Before writing UI code, determine:

- what the page is for
- what the primary user action is
- what needs to be scanned quickly
- what needs comparison
- what needs configuration
- what deserves persistent visibility

### Rule 3. Prefer strong page archetypes

Map pages to proven archetypes such as:

- operational dashboard
- control center
- list + detail workspace
- configuration center
- workflow status board
- report analysis page
- asset library

Avoid generic “one column with random cards” layouts unless the task truly calls for it.

### Rule 4. Make hierarchy obvious

A good page should make it immediately clear:

- what matters most
- what is actionable
- what is contextual
- what is secondary

Use spacing, scale, grouping, and contrast to make this visible without explanation.

### Rule 5. Browser verification is mandatory

After implementation, verify the UI in a browser. Do not trust code appearance from source alone.

Use the `webapp-testing` skill when the page can be run locally.

## 4. Implementation Workflow

### Step 1. Convert business logic into page structure

Extract:

- page goal
- user roles
- primary actions
- key status signals
- required filters
- required comparisons
- required drill-down areas

Then select the most suitable layout archetype.

See:

- `references/business-to-ui.md`

### Step 2. Build the layout skeleton

Choose a layout pattern intentionally:

- header + KPI strip + filter bar + data region
- split workspace with list/table on the left and detail panel on the right
- multi-step workflow board
- dense configuration shell with grouped settings
- analysis view with summary, trends, exceptions, and raw records

See:

- `references/layout-patterns.md`

### Step 3. Apply visual polish rules

Define:

- typography scale
- spacing rhythm
- card structure
- table density
- status color system
- button hierarchy
- empty/loading/error states

See:

- `references/visual-polish.md`

### Step 4. Implement code

While implementing:

- keep layout logic explicit
- use CSS variables or tokens
- avoid accidental visual noise
- make mobile collapse intentionally, not incidentally
- prefer fewer but stronger patterns

### Step 5. Verify and iterate

Open the page, inspect the rendered result, and check:

- alignment
- density
- overflow
- hierarchy
- readability
- action clarity
- table usability
- responsive behavior

See:

- `references/verification-loop.md`

## 5. Output Expectations

A successful result should usually include:

- a clear visual direction
- a page structure matched to the business problem
- strong typography and spacing hierarchy
- refined tables/forms/cards instead of default boilerplate
- at least one deliberate high-value interaction or layout decision
- browser-verified usability

## 6. What To Avoid

- default-looking admin UI with weak hierarchy
- dumping backend fields directly into cards or tables
- identical card styling for every information type
- oversized padding without structure
- random gradient use without a purpose
- decorative complexity that weakens operational readability
- shipping without browser inspection
