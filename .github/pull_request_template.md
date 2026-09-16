## Summary

Describe the change and why it is needed.

## Validation

- [ ] `ruff check scripts tests launcher.pyw`
- [ ] `pytest -q`
- [ ] `scripts/build-release.py`
- [ ] Relevant interactive Windows checks completed, if applicable

## Security / compatibility impact

Describe any effect on Windows privileges, remote exposure, authentication assumptions, credentials, MCP protocol behavior, or dependency compatibility. Write `None` when there is no impact.

## Publication hygiene

- [ ] No `.run/`, `.venv/`, logs, exports, screenshots, credentials, tokens, tunnel URLs, or machine-specific artifacts are included.
- [ ] User-facing documentation is updated where needed.
