---
name: capture-to-notion
description: Use when the user asks to save, initialize, complete, summarize-and-store, or recommend a Notion destination for books, podcast episodes, meeting notes, articles, or external content.
---

# Capture to Notion Skill

Use this skill when the user asks to capture content into Notion, initialize or complete a book/podcast item, summarize content before saving it, scan a Notion target, or choose where content should be stored.

## Current Capability Boundary

The CLI can preflight captures, suggest targets, scan Notion pages/databases, plan captures, and apply confirmed write plans. Do not silently write to Notion: create or inspect deterministic preflight/plan facts first, then run `capture-to-notion capture apply --plan /path/to/plan.json --confirmed` only when the user has explicitly confirmed the target and write.

Supported first-version write behavior includes cached target/schema mapping, explicit author/podcast relation resolution, cover download/cache/upload fallback to external URL, and warning reporting when optional enrichment fails.

Capture plans depend on target cache `parser_profile`, `fields`, and `field_sources`. Do not use Notion MCP or property-name guessing to fill business mappings. When `field_sources` are present, treat only sources listed in the active parser profile `trusted_field_sources` as trusted for required mappings; warnings such as `*_schema_incomplete`, `*_key_values_missing`, and `untrusted_field_mapping` must be shown to the user for confirmation.

## Summary Preprocessing

If the user asks to summarize, extract, condense,整理, 归纳, 提炼, or save a long transcript/article/notes after summarizing, summarize before building the capture input.

1. If a currently available `/summarize` skill can handle the source, use it first.
2. If no currently available `/summarize` skill can handle the source, stop summary preprocessing and report that summary generation is unavailable; do not silently fall back to default model summarization.
3. Use the summary as the main captured content or as a notes/summary candidate in `raw_input`.
4. Continue with the normal capture planning flow after summarization.

## Summary-Like Target Fields

A target/profile may mark mapped fields as summary-like with `summary_fields` and `summary_policy`. Treat those fields as generated summaries, not metadata copy targets.

When a summary-like field is planned:

1. Prefer a real content source: transcript, full article text, audio/video content, full show notes, or another source representing the body content.
2. Do not use page-visible metadata such as title, SEO description, or a short platform intro as a content summary.
3. If a content source is available, use a currently available `/summarize` skill; if no such skill can handle it, stop and report that summary generation is unavailable.
4. If no content source is available, do not write the summary-like field. Present the plan's `enrichment_requirements` and ask the user to provide a transcript/content source or confirm accepting metadata instead.
5. Keep this profile-driven: do not hardcode a Notion property name, page title, platform name, or content type in runtime logic.

## Required Flow

1. Apply Summary Preprocessing when the request requires it.
2. Skill AI first parses the user's intent and input shape before planning. Decide whether the user wants direct write, target recommendation,补充信息, or URL-related enrichment advice.
3. Parse the user's request into `input.json`.
4. Include:
   - `raw_input`: the user's content, or the summarized content when Summary Preprocessing applied.
   - `target_hint`: target page or alias if the user gave one, otherwise `null`.
   - `state`: `initialized` or `completed`; map Chinese phrases like 初始化/想读/待听 to `initialized`, and 完成/已读/听完 to `completed`.
   - `content_type_hint`: `book`, `podcast_episode`, or `null`.
   - `intent_hint` (optional): such as `direct_write`, `recommend_target`, `enrich_before_write`, `complete_existing_item`.
   - `input_shape_hint` (optional): such as `plain_text`, `structured_notes`, `external_url`, `mixed_input`.
   - `target_context_hint` (optional): current known target context, alias, page path, or existing cache clue.
   - `target_scope_hint` (optional): whether the user points to a specific page, a database-like area, or leaves target open.
   - `user_requested_action` (optional): the user's explicit requested next step, such as `write`, `recommend`, `scan`, `parse_url`, `summarize_then_store`.
   - `options.allow_web_search`: `true`.
   - `options.allow_target_search`: `true`.
   - `options.allow_asset_download`: `true`.
5. Write that JSON to a temporary file.
6. Run:

```bash
capture-to-notion capture preflight --input /path/to/input.json --compact
```

7. Interpret preflight facts first, then decide the next user-facing recommendation:
   - `target_missing`: no reliable target yet; recommend target suggestion or ask the user to specify one.
   - `cache_missing`: target cache/schema facts are missing; if the user already identified one exact target, run `capture-to-notion target scan --page-id ... --alias ...` first.
   - `risky_target`: target may be writable but has structural or trust warnings; explain the risk and ask before continuing.
   - `ambiguous_target`: multiple likely targets or unresolved target identity; show candidates and wait for the user to choose one exact page ID before scan/plan/apply.
   - `direct_plan_allowed`: deterministic facts are sufficient; continue to `capture plan`.
   - `url_parse_suggested` / `ask_before_parse`: external URL parsing or enrichment may help, but it is only a recommendation-stage action.
8. If the target page/database has not been scanned or cached and preflight indicates cache is needed, run `capture-to-notion target search --query ...` first. If multiple results have the same title, show each candidate with title, parent path when available, page ID, URL, and last edited time, then wait for the user to choose the exact page ID. Do not scan, alias, plan, or apply until the user chooses one.
9. After the user identifies one exact target page/database, run `capture-to-notion target scan --page-id ... --alias ...` if preflight or cache status shows it has not been scanned or cached.
10. External URLs are not automatically parsed or fetched by default. If preflight suggests URL parsing/enrichment, first recommend it or ask for confirmation; do not silently parse/fetch the URL.
11. When preflight shows direct planning is allowed, run:

```bash
capture-to-notion capture plan --input /path/to/input.json --output /path/to/plan.json --compact
```

12. Present the compact stdout preflight conclusion and plan to the user in concise Chinese. The `--output` plan file remains the complete executable JSON for apply.
13. If `requires_confirmation` is true, ask the user to confirm or choose a target before writing.
14. If the user explicitly confirms the plan and target, run:

```bash
capture-to-notion capture apply --plan /path/to/plan.json --confirmed
```

15. Present the apply result, including warnings, asset results, and `verification` warnings when present.

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

Summarize preflight/plan results like this:

```txt
预检结论：可直接规划 / 需先选目标 / 建议先扫描缓存 / 如要解析 URL 需先确认
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
- Stay cache-first: when reliable target cache or schema facts already exist, use them before considering a re-scan.
- Do not use Notion MCP as a fallback; stay within the capture-to-notion Skill backend and CLI flow.
- External URLs are not automatically parsed or fetched by default; recommend or ask first.
- If preflight or plan has no target, ask the user to choose a Notion page.
- If preflight marks the target as risky or ambiguous, explain it and wait before planning or writing.
- If cover handling fails, preserve the main plan and report the warning.
