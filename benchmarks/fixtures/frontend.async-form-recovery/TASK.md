# Async form recovery

Repair the supplied account form without replacing the fixture app.

Acceptance requirements:

- Use the isolated live preview URL in `EVAL_URL.txt` for browser regression checks.
- A slow validation result must never overwrite a newer fast input result.
- Preserve the user's current input value during validation and submission.
- Add a visible `<label for="email">` and announce validation status with
  `role="status"`/`role="alert"` or an appropriate `aria-live` value.
- Pressing Enter in the email field submits the current value and stores it in
  `data-submitted-email` on the form.
- Add a separate persistent regression test for the race. Test code must not
  auto-run inside `index.html`, mutate the production form on page load, or
  overwrite the real status text.
- Verify the slow-then-fast race and keyboard submission against a clean page.
