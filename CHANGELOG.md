# Changelog

## 0.1.0 - 2026-05-11

### Renamed

- Renamed the Skill and CLI workflow to `capture-to-notion`.
- Replaced the old CLI/package names `notion-skill`, `notion-capture`, and `notion_skill` with `capture-to-notion` and `capture_to_notion`.
- Standardized the local configuration directory as `~/.config/capture-to-notion`.

### Migration Notes

- From this Skill directory, reinstall the editable CLI with:

```bash
uv tool install --force --editable .
```

- The old `notion-skill` CLI should not be used.
- If a previous local configuration exists under `~/.config/notion-skill`, migrate it deliberately rather than copying secrets into the Skill directory.
- Notion tokens belong in the tool's own local config or configured environment variable, not in Claude Code global settings and not in this Skill directory.
