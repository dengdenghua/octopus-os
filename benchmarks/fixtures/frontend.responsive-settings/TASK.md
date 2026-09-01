# Responsive settings screen

Implement the supplied `index.html` to match `reference.json`.

Acceptance requirements:

- Read both `index.html` and `reference.json` before editing.
- Use the isolated live preview URL in `EVAL_URL.txt` for browser regression checks.
- At desktop width the settings grid has exactly two columns; at 390px it has
  exactly one column and no horizontal overflow.
- Keep the content width at or below the reference maximum and preserve the
  fixture's existing visual tokens instead of introducing a framework.
- Keep both controls with ids `display-name` and `theme`.
- Give each control a visible explicit `<label for="...">`.
- A keyboard user can Tab to the controls in a sensible order.
- Verify desktop layout, mobile layout, labels, and keyboard focus.
