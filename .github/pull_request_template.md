## What does this change?

## How did you test it on the live site?
<!-- Login method, OS, which flow, what happened. The maintainer can't test against info-kierowca.pl, so this is required for anything site-facing. -->

## Checklist
- [ ] "Allow edits by maintainers" is ticked
- [ ] Tests and pyflakes pass (`uv run python -m unittest discover -s tests -v`, `uv run python -m pyflakes src tools tests`)
- [ ] New UI text has a Polish translation in `web/localization.py`
- [ ] No personal data (PKK, cookies, names) in code, tests, logs or screenshots
