# Verification Loop

Professional UI work is not done when the code compiles.

## 1. Render First

Open the page in a browser and inspect the rendered result.

Use local browser verification, screenshots, or Playwright checks.

If the task is interactive, use the `webapp-testing` skill.

## 2. Inspect These Areas

### Layout

- alignment
- section rhythm
- density
- overflow
- sticky behavior if applicable

### Hierarchy

- can the eye find the main information immediately
- do KPI areas overpower filters or vice versa
- are details too loud compared with primary signals

### Action clarity

- is the primary action obvious
- are destructive actions clearly separated
- are row actions too noisy

### Tables and forms

- do rows scan quickly
- do columns feel prioritized
- are form groups understandable
- are helper texts useful rather than verbose

### Responsive behavior

- does the page still feel deliberate on narrow widths
- does information collapse in a meaningful order

## 3. Iterate

When something feels wrong, fix the structure first, not just cosmetics.

Use this order:

1. information order
2. layout grouping
3. spacing
4. typography
5. color and decorative detail

## 4. Completion Standard

Do not stop at “works.”

The page should be:

- useful
- visually legible
- structurally intentional
- internally consistent
- good enough to show without apology
