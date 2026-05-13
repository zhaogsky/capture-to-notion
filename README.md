# Capture to Notion

Capture to Notion is a Claude Code skill plus local CLI backend for planning and applying writes to Notion. It is intentionally scoped to capture workflows: choosing targets, scanning schemas, creating write plans, resolving relations, handling cover assets, and applying confirmed writes.

## Components

- `SKILL.md` — Claude-facing workflow and safety rules.
- `capture_to_notion/` — Python backend used by the CLI.
- `tests/` — regression tests for planning, scanning, writing, assets, and CLI behavior.
- `pyproject.toml` — package metadata and the `capture-to-notion` console script.

## Install or Reinstall

Install the editable CLI from this directory:

```bash
uv tool install --force --editable /Users/aaron/.claude/skills/capture-to-notion
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
capture-to-notion target inspect --alias books
```

Search for a target page or database:

```bash
capture-to-notion target search --query "书单"
```

Scan a confirmed target:

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

Create a write plan:

```bash
capture-to-notion capture plan --input input.json --output plan.json
```

The generated plan includes a top-level `summary` block for review before any write. Check `target_page`, `target_data_source`, `state`, `mapped_fields`, `key_fields`, `asset_actions`, `requires_confirmation`, and `warnings`. For book captures, `book_key_values_missing` means the plan is missing required metadata such as author, ISBN, or page count and must be confirmed or enriched before apply.

## Parser Profiles and Field Sources

Target cache entries can define `parser_profile` at the target level or data source level. Data source profiles override target profiles. The default book profile supplies only required/review field lists; it does not add business labels. Target scanning records Notion property names and official property types; it does not infer business record keys from property names unless a parser profile or explicit mapping supplies that key.

Use `labels` and `title_patterns` to control raw input parsing into normalized record keys. Use `required_schema_fields` for record keys that must map to Notion schema before a write plan can proceed, `required_value_fields` for record keys that must have extracted values, `summary_key_fields` for fields that should appear in the plan review summary, `trusted_field_sources` for mapping source labels that can satisfy required schema fields without confirmation, and `asset_trust_required_fields` for asset-backed record keys whose attachment mappings must also come from trusted sources before asset operations are planned.

`field_sources` records where each cached mapping came from. When `field_sources` are present, required mappings with a `trusted_field_sources` profile are trusted only when their source appears in that list; other sources trigger confirmation through warnings such as `untrusted_field_mapping`, `*_schema_incomplete`, or `*_key_values_missing`. `asset_trust_required_fields` reuses that same trust check for asset attachment planning, so untrusted asset mappings are removed before asset operations are generated. The default book profile trusts `explicit` and `profile`, and requires trusted asset mapping for `cover`. The planner does not infer business fields from Notion property names.

```json
{
  "parser_profile": {
    "book": {
      "labels": {
        "author": ["作者", "author"],
        "isbn": ["ISBN", "isbn"],
        "page_count": ["页数", "pages"]
      },
      "required_schema_fields": ["cover", "author", "isbn", "page_count", "state"],
      "required_value_fields": ["author", "isbn", "page_count"],
      "summary_key_fields": ["cover", "author", "isbn", "page_count"],
      "trusted_field_sources": ["explicit", "profile"],
      "asset_trust_required_fields": ["cover"]
    }
  },
  "data_sources": {
    "books": {
      "fields": {
        "author": "作者",
        "isbn": "ISBN"
      },
      "field_sources": {
        "author": "profile",
        "isbn": "explicit"
      }
    }
  }
}
```

Apply a confirmed plan:

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

Verify a written page with read-only checks:

```bash
capture-to-notion capture verify --page-id PAGE_ID
```

`capture verify` returns a JSON result with `verified`, `checks`, and `warnings`. Without a plan or explicit mapping, it only checks page presence and page cover URL accessibility; it does not infer title, status, author, ISBN, page count, or cover fields from property names. It does not write to Notion or download images. `capture apply` appends a top-level `verification` summary for written pages when page IDs are returned, using the write plan and target cache field mapping to verify written fields without hiding the apply result.

## Typical Workflow

1. Search or select the exact Notion target.
2. Scan the target before first use or after schema changes.
3. Build an input JSON file with the raw content, target hint, state, content type hint, and options.
4. Generate a plan with `capture-to-notion capture plan`.
5. Review the plan and warnings.
6. Apply only after the user confirms the target and write.

## Safety Boundary

Capture to Notion replaces Notion MCP for this workflow. Do not fall back to Notion MCP when scanning, planning, writing, validating, or reading structure for this skill. If the backend lacks an API operation or returns stale data, fix or extend this skill/backend instead, unless the user explicitly asks for a one-off MCP operation.

The tool must not silently write to Notion. First-time target use and any plan requiring confirmation should remain plan-first, apply-after-confirmation.

## Tests

Run the backend test suite from the skill directory:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```
