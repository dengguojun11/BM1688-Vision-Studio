# Layout Patterns

Use these patterns intentionally instead of improvising generic admin layouts.

## 1. Header Shell

Most professional operational pages benefit from:

- title
- concise subtitle
- one primary action
- one or two secondary actions
- optional global status chip

Do not overload the header with dense filters unless the page is filter-first.

## 2. KPI Strip

Use a KPI strip when the user needs an immediate read on the system.

Best practices:

- 3 to 6 cards
- short labels
- large numeric focus
- one supporting line or delta
- consistent heights

Avoid mixing long prose with KPI cards.

## 3. Filter Bar

Use a dedicated filter region when the list is a major decision tool.

Best practices:

- primary filters on one row
- advanced filters collapsible if necessary
- keep actions clearly separated from filters
- avoid making the filter area taller than the data region unless the task is configuration-heavy

## 4. Table + Side Panel

This is a high-value default for operational tools.

Use when:

- many records must be scanned
- one record needs deeper detail
- users should not navigate away constantly

Best practices:

- dense but readable table
- status and key identifiers early in the row
- side panel grouped into logical sections
- action buttons anchored near the top of the detail panel

## 5. Sectioned Configuration Layout

Use when editing policies, standards, thresholds, or settings.

Best practices:

- divide settings into 3 to 6 strong groups
- each group gets a title and one-sentence explanation
- show default, override, and dependency relationships clearly
- keep save state visible

## 6. Workflow Board

Use when the page reflects process stages.

Best practices:

- show stage progression left-to-right or top-to-bottom
- use strong visual status cues
- highlight current blocker
- expose artifacts and actions close to the relevant stage

## 7. Analysis Layout

Use when the page is for understanding results rather than controlling the system.

Best practices:

- summary first
- comparison second
- anomalies/exceptions third
- raw evidence last

## 8. Responsive Rules

Desktop:

- preserve density
- use columns and side panels when beneficial

Tablet:

- collapse side panel into drawer if needed
- keep header and filters structured

Mobile:

- collapse KPI strip into 1 to 2 columns
- stack filters
- convert large tables into prioritized record cards only if necessary

Do not treat responsive design as “everything stacks and hopes for the best.”
