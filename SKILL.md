---
name: capture-to-notion
description: Use when the user asks to save, initialize, complete, summarize-and-store, or recommend a Notion destination for books, podcast episodes, meeting notes, articles, or external content.
---

# Capture to Notion Skill

Use this skill when the user asks to capture content into Notion, initialize or complete a book/podcast item, summarize content before saving it, scan a Notion target, or choose where content should be stored.

## Current Capability Boundary

The CLI can plan captures, suggest targets, scan Notion pages/databases, and apply confirmed write plans. Do not silently write to Notion: create or inspect a plan first, then run `capture-to-notion capture apply --plan /path/to/plan.json --confirmed` only when the user has explicitly confirmed the target and write.

Supported first-version write behavior includes cached target/schema mapping, explicit author/podcast relation resolution, cover download/cache/upload fallback to external URL, and warning reporting when optional enrichment fails.

## Summary Preprocessing

If the user asks to summarize, extract, condense,整理, 归纳, 提炼, or save a long transcript/article/notes after summarizing, summarize before building the capture input.

1. If a `/summarize` skill is available, use it first.
2. If `/summarize` is unavailable, do not stop or ask the user to install it; summarize the content yourself.
3. Use the summary as the main captured content or as a notes/summary candidate in `raw_input`.
4. Continue with the normal capture planning flow after summarization.

## Required Flow

1. Apply Summary Preprocessing when the request requires it.
2. If the target page/database has not been scanned or cached, run `capture-to-notion target search --query ...` first. If multiple results have the same title, show each candidate with title, parent path when available, page ID, URL, and last edited time, then wait for the user to choose the exact page ID. Do not scan, alias, plan, or apply until the user chooses one.
3. After the user identifies one exact target page/database, run `capture-to-notion target scan --page-id ... --alias ...` if it has not been scanned or cached.
4. Parse the user's request into `input.json`.
5. Include:
   - `raw_input`: the user's content, or the summarized content when Summary Preprocessing applied.
   - `target_hint`: target page or alias if the user gave one, otherwise `null`.
   - `state`: `initialized` or `completed`; map Chinese phrases like 初始化/想读/待听 to `initialized`, and 完成/已读/听完 to `completed`.
   - `content_type_hint`: `book`, `podcast_episode`, or `null`.
   - `options.allow_web_search`: `true`.
   - `options.allow_target_search`: `true`.
   - `options.allow_asset_download`: `true`.
6. Write that JSON to a temporary file.
7. Run:

```bash
capture-to-notion capture plan --input /path/to/input.json --output /path/to/plan.json
```

8. Present the returned plan to the user in concise Chinese.
9. If `requires_confirmation` is true, ask the user to confirm or choose a target before writing.
10. If the user explicitly confirms the plan and target, run:

```bash
capture-to-notion capture apply --plan /path/to/plan.json --confirmed
```

11. Present the apply result, including warnings, asset results, and `verification` warnings when present.

## Target Suggestion

If the user only asks where something should go, run:

```bash
capture-to-notion target suggest --input /path/to/input.json
```

Show 2-3 suggestions when available. If no suggestions are available, say that a target page must be selected or initialized first.

## Cache Inspect

If the user asks to inspect cache status/details, run:

```bash
capture-to-notion cache inspect
```

This command only inspects local cache and does not write to Notion.

## Output Style

Summarize plans like this:

```txt
我计划写入：
- 类型：book
- 目标页面：书单
- 目标数据库：Books
- 标题：...
- 状态：initialized
- 封面：计划下载并映射到「封面」字段；执行 apply 时会尝试下载/上传，失败则回退为外部 URL
- 写入：当前为计划展示；用户确认后才运行 `capture apply --confirmed`
- 需要确认：...
```

## Safety Rules

- Never silently write to Notion on first use of a target page.
- Never overwrite or modify Notion schema automatically.
- Never store business cache in Claude memory.
- If the plan has no target, ask the user to choose a Notion page.
- If cover handling fails, preserve the main plan and report the warning.
