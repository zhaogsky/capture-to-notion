# Capture to Notion

Capture to Notion is a Claude Code skill plus local CLI backend for planning and applying writes to Notion. It is intentionally scoped to capture workflows: choosing targets, scanning schemas, creating write plans, resolving relations, handling cover/file assets, and applying confirmed writes.

## Components

- `SKILL.md` — Claude-facing workflow and safety rules.
- `capture_to_notion/` — Python backend used by the CLI.
- `tests/` — regression tests for planning, scanning, writing, assets, and CLI behavior.
- `pyproject.toml` — package metadata and the `capture-to-notion` console script.

## Development Status

As of 2026-06-03, the project is in regression-maintenance mode for the currently supported workflows. Core capture, cache-first planning, migration governance, compact output, relation/people safety, asset uploads, verification, and reusable workflow norms are covered by tests. Future work should be opened as small follow-up tasks only when real usage feedback, long-tail golden cases, structure changes, or a second similar Skill creates new requirements.

## Release Status

`0.1.0` is an internal Beta release for personal and internal daily use. See [RELEASE.md](RELEASE.md) for installation, verification, rollback, and version policy details.

## Install or Reinstall

Install the editable CLI from this directory:

```bash
uv tool install --force --editable .
```

Verify the command:

```bash
capture-to-notion --help
```

## Configuration

Default local configuration lives at:

```text
~/.config/capture-to-notion/
```

Override it for tests or isolated runs:

```bash
CAPTURE_TO_NOTION_CONFIG_DIR=/tmp/capture-to-notion capture-to-notion cache inspect
```

The Notion integration token is configured in the tool's local config, not Claude Code global settings. Keep secrets out of this skill directory.

## Diagnostics

Print version, package path, and runtime path details:

```bash
capture-to-notion version
```

Run a read-only local diagnostic for config paths, whether a token is configured, and whether a legacy config directory exists. The command does not print the raw token value:

```bash
capture-to-notion doctor
```

`doctor` also warns when cached targets predate `field_sources`; rescan those targets before relying on trusted mapping gates.

Preview a safe migration from the legacy `~/.config/notion-skill` directory without writing anything:

```bash
capture-to-notion config migrate
```

Apply the migration only after review:

```bash
capture-to-notion config migrate --confirmed
```

The migration command only copies allowlisted config assets (`config.json`, `states.json`, `aliases.json`, and `targets/*.json`), never prints token values, does not overwrite files already present in the new config root, and does not delete the legacy directory.

For migration history and rename details, see `CHANGELOG.md`.

## Common Commands

Inspect local cache:

```bash
capture-to-notion cache inspect
```

List cached Notion targets without calling Notion:

```bash
capture-to-notion target list
```

Inspect one cached target by alias:

```bash
capture-to-notion target inspect --alias books --compact
```

Search for a target page or database:

```bash
capture-to-notion target search --query "书单" --limit 5 --compact
```

Scan a confirmed target:

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

Bind a write profile and trusted files asset field:

```bash
capture-to-notion target bind-profile \
  --alias books \
  --graph-id GRAPH_ID \
  --profile-id PROFILE_ID \
  --content-type book \
  --data-source-id DATA_SOURCE_ID \
  --field title=Name \
  --asset-field cover=Cover
```

Preview capture readiness with compact output:

```bash
capture-to-notion capture preflight --input input.json --compact
```

Create a write plan:

```bash
capture-to-notion capture plan --input input.json --output plan.json --compact
```

The generated plan includes a top-level `summary` block for review before any write. Check `target_page`, `target_data_source`, `state`, `mapped_fields`, `key_fields`, `asset_actions`, `requires_confirmation`, and `warnings`. For book captures, `book_key_values_missing` means the plan is missing required metadata such as author, ISBN, or page count and must be confirmed or enriched before apply.

Apply a confirmed plan:

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

Verify a written page with read-only checks:

```bash
capture-to-notion capture verify --page-id PAGE_ID
```

`capture verify` returns a JSON result with `verified`, `checks`, and `warnings`. Without a plan or explicit mapping, it only checks page presence and page cover URL accessibility; it does not infer title, status, author, ISBN, page count, or cover fields from property names. It does not write to Notion or download images. `capture apply` appends a top-level `verification` summary for written pages when page IDs are returned, using the write plan and target cache field mapping to verify written fields without hiding the apply result.

## Parser Profiles and Field Sources

Target cache entries can define `parser_profile` at the target level or data source level. Data source profiles override target profiles. The default book profile supplies only required/review field lists; it does not add business labels. Target scanning records Notion property names and official property types; it does not infer business record keys from property names unless a parser profile or explicit mapping supplies that key.

Use `labels` and `title_patterns` to control raw input parsing into normalized record keys. Use `required_schema_fields` for record keys that must map to Notion schema before a write plan can proceed, `required_value_fields` for record keys that must have extracted values, `summary_key_fields` for fields that should appear in the plan review summary, `trusted_field_sources` for mapping source labels that can satisfy required schema fields without confirmation, and `asset_trust_required_fields` for asset-backed record keys whose attachment mappings must also come from trusted sources before asset operations are planned.

`field_sources` records where each cached mapping came from. When `field_sources` are present, required mappings with a `trusted_field_sources` profile are trusted only when their source appears in that list; other sources trigger confirmation through warnings such as `untrusted_field_mapping`, `*_schema_incomplete`, or `*_key_values_missing`. `asset_trust_required_fields` reuses that same trust check for asset attachment planning, so untrusted asset mappings are removed before asset operations are generated. The default book profile trusts `explicit` and `profile`, and requires trusted asset mapping for `cover`. The planner does not infer business fields from Notion property names.

## Typical Workflow

1. Run `capture preflight --compact` first.
2. Follow `workflow.planning.next_action` instead of manually choosing the next step.
3. Search, scan, bind, or sync only when preflight asks for it.
4. Generate a compact write plan and review target path, fields, relation/people requirements, asset actions, warnings, and verification expectations.
5. Apply only after the user explicitly confirms the target and write.
6. Verify by plan expectations after apply.

## Safety Boundary

Capture to Notion replaces Notion MCP for this workflow. Do not fall back to Notion MCP when scanning, planning, writing, validating, or reading structure for this skill. If the backend lacks an API operation or returns stale data, fix or extend this skill/backend instead, unless the user explicitly asks for a one-off MCP operation.

The tool must not silently write to Notion. First-time target use and any plan requiring confirmation should remain plan-first, apply-after-confirmation.

## Tests

Run the backend test suite from the skill directory:

```bash
uv run pytest -q
```
