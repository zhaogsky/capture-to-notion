# Capture to Notion

[中文版](README.md)

Capture to Notion is a Claude Code Skill for saving books, articles, podcast episodes, notes, and related metadata into Notion.

Tell Claude Code what you want to save and where it should go. The Skill checks configuration and target information first, creates a reviewable write plan, and writes to Notion only after you confirm.

## What It Can Do

- Save books, articles, podcast episodes, and notes into Notion.
- Create a plan before writing so you can review the target, fields, and content.
- Write only after explicit user confirmation.
- Upload covers, avatars, and images into Notion `files` properties.
- Resolve and validate relation / people fields.
- Verify results after apply.

## Install

### 1. Download the Skill

Clone this repository into Claude Code's Skill directory:

```bash
git clone https://github.com/zhaogsky/capture-to-notion.git ~/.claude/skills/capture-to-notion
```

If the current Claude Code session does not pick up the new Skill, restart the session.

### 2. Install the local CLI backend

The Skill calls the local `capture-to-notion` command to scan, plan, and write to Notion.

Enter the project directory:

```bash
cd ~/.claude/skills/capture-to-notion
```

Install the command:

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

## Configure the Notion API Key

### 1. Create a Notion integration

Create a Notion integration and copy its API key. It usually looks like:

```text
secret_xxx
```

You must also invite the integration to the Notion page or database you want to write to. Otherwise, the API key is valid but the integration has no permission to access the target.

### 2. Write the local config file

The recommended setup is storing the API key in a local config file.

The current default config file path is:

```text
~/.config/capture-to-notion/config.json
```

On Windows, the current implementation resolves this to:

```text
C:\Users\<your-name>\.config\capture-to-notion\config.json
```

Create the config directory:

```bash
mkdir -p ~/.config/capture-to-notion
```

Edit the config file:

```bash
nano ~/.config/capture-to-notion/config.json
```

Add:

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

Replace `secret_xxx` with your own Notion API key.

This file should stay on your local machine. Do not commit it to GitHub or share it with others.

### 3. Check the setup

Run:

```bash
capture-to-notion doctor
```

`doctor` checks the config directory, runtime paths, and whether a Notion token is configured. It does not print the raw token.

Note: the Skill does not complete every environment check during installation. Run `doctor` once before first use. Before actual writes, the Skill also runs preflight checks for the target, cache, and write plan.

## How to Use It in Claude Code

After installation and configuration, ask Claude Code naturally:

```text
Save this book to my Notion reading list and set its status to want-to-read.
```

```text
Summarize this article and save it to my Notion reading database. Show me the write plan first.
```

```text
Save this podcast episode to Notion and fill in the title, show name, link, and summary.
```

```text
Add an avatar to this author record and upload it into the Notion files property instead of saving only the image URL.
```

```text
Save these meeting notes under my Notion project notes page and confirm the target path first.
```

These are examples only. In real use, replace them with your own Notion pages, databases, and fields.

## Skill Workflow

For each Notion write, the Skill follows this flow:

1. Understand your request: content, target, and whether you want to write directly or enrich first.
2. Build an input file.
3. Run preflight to check target, cache, config, and next action.
4. If the target has not been scanned, ask to search, choose, or scan it.
5. Create a write plan.
6. Show target path, field mapping, concrete write pages, file upload actions, and warnings.
7. Apply only after explicit confirmation.
8. Verify results against the plan.

Core principle: **plan first, confirm second, write last**.

## Common CLI Commands

Most daily usage happens through Claude Code conversation. These commands are mostly for setup, debugging, and advanced use.

Check setup:

```bash
capture-to-notion doctor
```

Search Notion targets:

```bash
capture-to-notion target search --query "books" --limit 5 --compact
```

Scan a target:

```bash
capture-to-notion target scan --page-id PAGE_ID --alias books
```

Preflight a capture:

```bash
capture-to-notion capture preflight --input input.json --compact
```

Create a write plan:

```bash
capture-to-notion capture plan --input input.json --output plan.json --compact
```

Apply after confirmation:

```bash
capture-to-notion capture apply --plan plan.json --confirmed
```

## File Uploads

If a Notion property type is `files`, this Skill treats images as file assets.

For covers, author avatars, and similar images, the plan should show:

- `download_and_attach`
- `asset_actions`
- `asset_operations`
- Notion uploaded file entities in the write result

This is different from only writing an external image URL into Notion.

## Safety

- The Skill does not silently write to Notion.
- It creates a plan before writing.
- It applies only after explicit user confirmation.
- The Notion API key stays in your local config file.
- Do not commit `~/.config/capture-to-notion/config.json`, caches, or tokens.
- The Capture to Notion workflow does not fall back to Notion MCP.

## Advanced Config

Regular users should use the direct `config.json` token setup above.

Developers or CI users can use an environment variable instead. The config file can be:

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

Then set:

```bash
export NOTION_TOKEN="secret_xxx"
```

Regular users can ignore this section.

## Tests

For development:

```bash
python -m pytest -q
```

If using `uv`:

```bash
uv run pytest -q
```

For README-only or documentation-only changes, a diff check is usually enough:

```bash
git diff --check
```
