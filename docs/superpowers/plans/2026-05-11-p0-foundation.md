# Capture to Notion P0 Foundation — Status

**Status:** Completed and superseded by the current implementation.

This plan is retained only as a historical record. Do not execute the old checklist from this file.

## Current state

Implemented in the codebase:

- Package naming and CLI entrypoint use `capture-to-notion` / `capture_to_notion`.
- `CHANGELOG.md` records rename and migration history.
- `capture-to-notion version` reports version, package path, and runtime paths without creating config files or leaking secrets.
- `capture-to-notion doctor` reports read-only diagnostics for config paths, token presence, and legacy config directory warnings without printing token values.
- `tests/test_p0_foundation.py` covers naming, version, doctor, secret redaction, and README command documentation.
- README files document install, config, diagnostics, and common commands.

## Current constraints

- Keep package metadata as `name = "capture-to-notion"` and script `capture-to-notion = "capture_to_notion.cli:main"`.
- Runtime config should remain under `CAPTURE_TO_NOTION_CONFIG_DIR` or `~/.config/capture-to-notion`.
- Diagnostics must not instantiate the Notion adapter or print token values.
- Do not revive legacy `notion-skill` / `notion-capture` command naming.
- Do not store Notion integration tokens in Claude Code global settings.

## Verification source

Use current tests as the source of truth:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_p0_foundation.py -q
```
