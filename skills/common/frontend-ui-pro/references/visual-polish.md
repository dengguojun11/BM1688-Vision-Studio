# Visual Polish

This file exists to prevent average-looking UI.

## 1. Typography

Use typography to create hierarchy, not just labels.

Recommended pattern:

- page title: strong, compact, high confidence
- section title: clear but secondary
- metric number: visibly larger than everything around it
- support text: quieter and shorter

Avoid:

- all text looking the same weight
- oversized paragraphs in admin UIs
- weak page titles

## 2. Spacing

Use a consistent spacing rhythm.

Typical rhythm:

- 8
- 12
- 16
- 20
- 24
- 32

Use larger spacing between sections than inside sections.

Avoid:

- random one-off spacing values
- oversized padding on every card
- cramped filters and action bars

## 3. Cards

Cards should not all look identical.

Different card types:

- KPI card
- status card
- configuration group card
- evidence/detail card
- warning/exception card

Differentiate with structure, not just background color.

## 4. Tables

Professional data tables should:

- prioritize identifiers and status early
- use compact row density when the task is operational
- avoid excessive borders
- keep action columns disciplined
- use chips and grouped metadata for fast scanning

Avoid:

- very tall rows without reason
- wrapping every cell into multiple lines
- too many equal-priority columns

## 5. Color

Use color as a meaning system.

At minimum define:

- neutral scale
- primary brand/action color
- success
- warning
- danger
- info

Avoid:

- random accent colors
- gradients on everything
- using saturation where contrast and structure would work better

## 6. Buttons And Actions

Actions need hierarchy:

- primary action: strongest emphasis
- secondary action: available but quieter
- destructive action: clearly dangerous
- table row action: compact and contextual

Avoid five equally bright buttons in one row.

## 7. States

Design these explicitly:

- empty state
- loading state
- error state
- partial/incomplete state

Well-designed states make a page feel professional even before the happy path is used.

## 8. Finishing Pass

Before considering a page done, check:

- is the main goal visually obvious
- are sections easy to scan
- is there a strong rhythm
- are there weak default styles left over
- does anything feel generic or accidental
