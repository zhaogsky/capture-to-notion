# Capture to Notion 0.1.0 Release Notes

## Release status

`0.1.0` is an internal Beta release.

This means:

- The core capture workflow is ready for personal and internal daily use.
- Behavior may still change based on real usage feedback.
- Public stable API compatibility is not promised yet.
- Every Notion write remains plan-first and requires explicit confirmation.
- The workflow does not fall back to Notion MCP.

A future `1.0.0` release should only be considered after CI, coverage, real usage regressions, documentation, and migration paths are stable over time.

## Install or reinstall

From any directory, install the editable CLI:

```bash
uv tool install --force --editable /Users/aaron/.claude/skills/capture-to-notion
```

Verify the command:

```bash
capture-to-notion --help
capture-to-notion version
```

## Configuration

The default local configuration directory is:

```text
~/.config/capture-to-notion/
```

Store the Notion integration token in this tool's own local config or configured environment variable. Do not store the token in Claude Code global settings and do not commit it into the Skill directory.

Run the local diagnostic check:

```bash
capture-to-notion doctor
```

## Migrating from the old config directory

If `~/.config/notion-skill` exists, preview the migration first:

```bash
capture-to-notion config migrate
```

Apply the migration only after reviewing the dry-run output:

```bash
capture-to-notion config migrate --confirmed
```

The migration copies only allowlisted config assets, does not overwrite existing new config files, does not print tokens, and does not delete the old directory.

## Release verification

Before treating a checkout as the `0.1.0` internal Beta, run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Run the coverage gate:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest --with pytest-cov python -m pytest --cov=capture_to_notion --cov-report=term-missing --cov-fail-under=80
```

Expected result:

- pytest exits with code 0.
- coverage is at least 80%.

## Rollback

If a new checkout or editable install behaves incorrectly:

1. Stop using the new checkout.
2. Reinstall the previous known-good checkout with `uv tool install --force --editable <path>`.
3. Keep `~/.config/capture-to-notion/` unchanged unless the issue is explicitly caused by local config.
4. Use `capture-to-notion doctor` to verify the runtime paths and token state.

## Safety guarantees for this release

- No silent Notion writes.
- No Notion schema modification.
- No Notion MCP fallback.
- No token output in diagnostics.
- Confirmed writes use generated plans.
- Verification warnings are reported without hiding apply results.
