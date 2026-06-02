# Capture to Notion

[中文版](README.md)

Capture to Notion is a Claude Code Skill for safely writing books, podcast episodes, articles, notes, and related metadata into Notion. It includes a local CLI backend, but the default usage is Skill-first: ask Claude Code to capture, enrich, plan, or write something to Notion; Claude follows `SKILL.md` to preflight, plan, ask for confirmation, apply, and verify.

Core principle: **generate reviewable preflight / plan facts first, then apply only after explicit user confirmation**. The workflow does not silently write to Notion and does not fall back to Notion MCP.

## What It Is For

- Capture books, podcast episodes, articles, notes, and structured metadata into Notion.
- Initialize or enrich existing records, such as covers, authors, ISBN, page counts, and author pictures.
- Generate write plans from scanned Notion structures.
- Resolve relation / people fields and block unresolved or ambiguous writes.
- Treat Notion `files` properties as uploaded assets instead of saving only external image URLs.
- Verify written pages, fields, files, covers, and verifiable view constraints after apply.

## Install the Skill

Clone the repository into Claude Code's user Skill directory:

```bash
git clone https://github.com/zhaogsky/capture-to-notion.git ~/.claude/skills/capture-to-notion
```

If the current Claude Code session does not pick up the new Skill, restart the session.

Project structure:

- `SKILL.md` — Claude Code workflow, safety boundary, and confirmation rules.
- `capture_to_notion/` — local CLI backend used by the Skill.
- `tests/` — regression tests for scanning, planning, writing, assets, verification, and CLI behavior.
- `README.md` — Chinese documentation.

## Configure the Notion API Key

The main configuration is the Notion integration token. The default local config directory is:

```text
~/.config/capture-to-notion/
```

Recommended: store the token in an environment variable:

```bash
export NOTION_TOKEN="secret_xxx"
```

You can configure the token environment variable name in `~/.config/capture-to-notion/config.json`:

```json
{
  "notion": {
    "auth": {
      "env_token_name": "NOTION_TOKEN"
    },
    "api_version": "2026-03-11"
  }
}
```

If you choose to store the token directly in the config file, keep it only in your local `~/.config/capture-to-notion/config.json` and never commit it:

```json
{
  "notion": {
    "auth": {
      "token": "secret_xxx"
    },
    "api_version": "2026-03-11"
  }
}
```

Check whether configuration is available:

```bash
capture-to-notion doctor
```

`doctor` checks whether a token is configured without printing the raw token value.

## How to Use It in Claude Code

After installation and configuration, ask Claude Code naturally, for example:

```text
Initialize The Art of Possibility in my book list.
```

```text
Summarize this podcast episode and save it to my podcast database. Show me the write plan first.
```

```text
Add a cover image to this book and upload it into the Notion files property instead of writing only the image URL.
```

Normal Skill flow:

1. Claude parses intent, content type, target, and input shape.
2. Claude builds a temporary `input.json`.
3. The CLI runs `capture preflight --compact` first.
4. Claude follows `workflow.planning.next_action`: suggest target, choose target, scan target, sync cache, confirm risk, or create a plan.
5. When preflight allows it, the CLI runs `capture plan --compact`.
6. Claude shows target path, concrete write pages, field mapping, relation / people requirements, asset actions, warnings, and verification expectations.
7. The CLI runs `capture apply --confirmed` only after explicit user confirmation.
8. Results are verified against the plan expectations after apply.

## CLI Backend Commands

These commands are mainly used by the Skill, but they are useful for debugging and development.

Inspect local cache:

```bash
capture-to-notion cache inspect
```

List cached targets without calling Notion:

```bash
capture-to-notion target list
```

Search Notion targets:

```bash
capture-to-notion target search --query "书单" --limit 5 --compact
```

Scan a confirmed target:

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

Inspect one target:

```bash
capture-to-notion target inspect --alias books --compact
```

Bind a write profile and trusted Notion `files` asset field:

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

Preflight a capture:

```bash
capture-to-notion capture preflight --input input.json --compact
```

Create a write plan:

```bash
capture-to-notion capture plan --input input.json --output plan.json --compact
```

Apply only after user confirmation:

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

Verify a written page with read-only checks:

```bash
capture-to-notion capture verify --page-id PAGE_ID
```

## CLI Installation

If you only use this as a Claude Code Skill, the important steps are cloning it into `~/.claude/skills/capture-to-notion` and configuring the Notion token.

If you need the `capture-to-notion` command in your shell, install the Python package from the project directory with any preferred Python tooling:

```bash
python -m pip install -e .
```

If you use `uv`, you can install it as an editable tool:

```bash
uv tool install --force --editable .
```

Verify the command:

```bash
capture-to-notion --help
```

## Files Asset Uploads

Notion `files` properties are treated as asset upload targets. For covers, author pictures, or similar fields, the plan should show:

- `asset_actions` with `download_and_attach`
- full-plan `asset_operations`
- Notion upload entities in apply results, such as `file_upload`

If the target profile lacks trusted asset mapping, the planner does not automatically treat a normal URL as a files upload target. Use `target bind-profile --asset-field semantic=NotionFilesProperty` to bind it explicitly.

## Parser Profiles and Field Sources

Target caches can define `parser_profile`. It controls input parsing, required fields, summary fields, trusted field sources, and asset upload trust requirements.

`field_sources` records where each field mapping came from. For fields that require trusted mapping, only sources listed in `trusted_field_sources` are trusted directly; otherwise the plan emits confirmation or blocking warnings.

Common field sources:

- `explicit` — explicitly configured or user-confirmed mapping.
- `profile` — trusted mapping from the write profile.
- `user_binding` — ordinary binding, which may not satisfy asset or required-field trust gates.

## Safety Boundary

- Do not silently write to Notion.
- Do not fall back to Notion MCP in the Capture to Notion workflow.
- Do not bypass the CLI with direct Notion API calls or ad hoc scripts.
- Do not commit Notion tokens, caches, or local config.
- Scan or sync before planning when using a target for the first time or after schema changes.
- Ask the user to choose, enrich, skip, or confirm when relation, people, asset, or target risks remain unresolved.

## Tests

Run the full test suite:

```bash
python -m pytest -q
```

If using `uv`:

```bash
uv run pytest -q
```

For README-only or documentation-only edits, `git diff --check` is usually enough:

```bash
git diff --check
```

## Status

As of 2026-06-03, the supported core workflows are in regression-maintenance mode. Tests cover cache-first planning, compact output, target path display, relation/people safety, files asset uploads, apply safety checks, and post-apply verification.

`0.1.0` is an internal Beta for personal and internal daily use. See [RELEASE.md](RELEASE.md) for installation, verification, rollback, and version policy details.
