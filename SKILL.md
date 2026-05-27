---
name: capture-to-notion
description: Use when the user asks to save, initialize, complete, summarize-and-store, or recommend a Notion destination for books, podcast episodes, meeting notes, articles, or external content.
---

# Capture to Notion Skill

Use this skill when the user asks to capture content into Notion, initialize or complete a book/podcast item, summarize content before saving it, scan a Notion target, or choose where content should be stored.

## Current Capability Boundary

The CLI can preflight captures, suggest targets, scan Notion pages/databases, plan captures, and apply confirmed write plans. Do not silently write to Notion: create or inspect deterministic preflight/plan facts first, then run `capture-to-notion capture apply --plan /path/to/plan.json --confirmed` only when the user has explicitly confirmed the target and write.

Supported first-version write behavior includes cached target/schema mapping, explicit author/podcast relation resolution, cover download/cache/upload fallback to external URL, and warning reporting when optional enrichment fails.

Capture plans depend on v2 write profiles, target graph schema, and trusted field mappings. Do not use Notion MCP or property-name guessing to fill business mappings. When `field_sources` are present, treat only trusted profile/user bindings as trusted for required mappings; warnings such as `*_schema_incomplete`, `*_key_values_missing`, and `untrusted_field_mapping` must be shown to the user for confirmation.

## V2 Notion Graph Cache

Capture to Notion uses only the v2 graph cache under `cache-v2/`. Legacy target cache files are not read by capture preflight, plan, or apply. If a target has no v2 graph/profile, route to scan/bind instead of falling back to old cache.

A v2 target consists of:
- a graph: pages, blocks, databases, data sources, and views from Notion API;
- a write profile: content type to canonical data source/view plus field mappings;
- an alias: user-facing name to graph/profile.

Writes always go to a data source. Views are display context and are validated when present.

## Summary Preprocessing

If the user asks to summarize, extract, condense,整理, 归纳, 提炼, or save a long transcript/article/notes after summarizing, summarize before building the capture input.

1. If a currently available `/summarize` skill can handle the source, use it first.
2. If `/summarize` is unavailable, cannot handle the source, or only documents a missing local CLI/backend, use the current AI session to summarize instead of blocking the workflow.
3. If neither `/summarize` nor the current AI session can access enough source content to summarize, ask the user to provide transcript/full text or confirm saving only link/metadata.
4. Use the summary as the main captured content or as a notes/summary candidate in `raw_input`, and include a source marker such as `summary_source: summarize_skill` or `summary_source: ai_fallback` in the input when useful for plan review.
5. Continue with the normal capture planning flow after summarization.

## Summary-Like Target Fields

A target/profile may mark mapped fields as summary-like with `summary_fields` and `summary_policy`. Treat those fields as generated summaries, not metadata copy targets.

When a summary-like field is planned:

1. Prefer a real content source: transcript, full article text, audio/video content, full show notes, or another source representing the body content.
2. Do not use page-visible metadata such as title, SEO description, or a short platform intro as a content summary.
3. If a content source is available, use a currently available `/summarize` skill first; if `/summarize` is unavailable or unusable, use the current AI session to summarize the content.
4. If no content source is available, do not write the summary-like field. Present the plan's `enrichment_requirements` and ask the user to provide a transcript/content source or confirm accepting metadata instead.
5. Keep this profile-driven: do not hardcode a Notion property name, page title, platform name, or content type in runtime logic.

## Preflight-First Workflow Gate

For any Notion capture, write, update, initialize, or completion request, run `capture-to-notion capture preflight --input ... --compact` before searching, scanning, planning, or applying. Do not decide the next step manually: route only by `workflow.planning.next_action` from preflight. Do not reinterpret, override, or skip the workflow route based on your own judgment.

Routing rules:

- `suggest_target`: only ask for or suggest a target. Do not run `capture plan` or `capture apply`.
- `choose_target`: only show candidates and wait for the user to choose an exact target. Do not plan or apply.
- `scan_target`: only run `target scan`, then rerun preflight. Do not plan until the rerun returns `capture_plan`.
- `sync_target_cache`: run `capture plan --compact` so the CLI can perform one scoped sync for the exact `workflow.target_resolution.sync` target, then rerun preflight internally. Do not search Notion, switch targets, or manually scan a fallback target.
- `confirm_risky_target`: explain the risk and wait for explicit confirmation or a different target. Do not plan or apply.
- `capture_plan`: run `capture plan --compact`, show the confirmation summary, and wait for explicit user confirmation before `capture apply --confirmed`.

Do not search Notion with generic content-type words such as book, podcast, article, video, or note. Search requires concrete identity facts such as title, author, ISBN, episode title, podcast name, project name, or a user-confirmed target name.

External URLs must not be fetched or parsed automatically unless the user explicitly requests it or confirms the recommended enrichment step. If a URL is present but identity or target facts are insufficient, recommend URL enrichment instead of generic target search.

## Low-Token Execution Protocol

Default to the lowest-token deterministic path.

1. If the user names a known target or alias, use that alias/cache first.
   - Do not run `target search` before `capture preflight` unless the target is unknown.
   - If preflight returns `workflow.planning.next_action == "capture_plan"`, go directly to `capture plan --compact`.
2. Always use compact stdout for planning commands:
   - `capture-to-notion capture preflight --input ... --compact`
   - `capture-to-notion capture plan --input ... --output ... --compact`
3. Do not paste raw JSON to the user unless debugging is explicitly requested.
   - Convert compact preflight/plan output into the minimal confirmation template below.
   - Keep the full executable plan only in the `--output` JSON file.
4. Only run `target search` when:
   - `target_missing`, `target_not_resolved`, or `ambiguous_target` is returned;
   - the user explicitly asks to search;
   - no reliable alias/cache exists.
5. When `target search` is necessary, use the smallest useful query and a limited result set.
   - Prefer exact program/page names.
   - Use `--limit 5 --compact` by default.
   - Do not inspect or read the full saved search output unless needed.
6. When discussing current behavior or token cost, verify current Skill/CLI implementation first.
   - Do not describe cache-first, alias reuse, compact preflight/plan, or confirmation gating as missing unless just verified missing.

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

7. Interpret `workflow.planning.next_action` as the only next-step router:
   - `suggest_target`: no reliable target yet; recommend target suggestion or ask the user to specify one.
   - `choose_target`: multiple likely targets or unresolved target identity; show candidates and wait for one exact page ID/data source ID.
   - `scan_target`: target cache/schema facts are missing or stale; scan the exact target, then rerun preflight.
   - `sync_target_cache`: target cache is missing location facts for the resolved target; run `capture plan --compact` and let the CLI perform one scoped sync for the exact sync request before planning.
   - `confirm_risky_target`: target may be writable but has structural or trust warnings; explain the risk and ask before continuing.
   - `capture_plan`: deterministic facts are sufficient; continue to `capture plan`.
8. If the target page/database has not been scanned or cached and preflight indicates cache is needed, run `capture-to-notion target search --query ... --limit 5 --compact` first. If multiple plausible results remain, show only the title, page/data source ID, and last edited time unless the user asks for more context; then wait for the user to choose the exact page ID. Do not scan, alias, plan, or apply until the user chooses one.
9. After the user identifies one exact target page/database, run `capture-to-notion target scan --page-id ... --alias ...` if preflight or cache status shows it has not been scanned or cached.
10. External URLs are not automatically parsed or fetched by default. If preflight suggests URL parsing/enrichment, first recommend it or ask for confirmation; do not silently parse/fetch the URL.
11. Only when preflight returns `workflow.planning.next_action == "capture_plan"` or `"sync_target_cache"`, run:

```bash
capture-to-notion capture plan --input /path/to/input.json --output /path/to/plan.json --compact
```

If the v2 graph/profile cannot cover the requested target or mapping, stop at the preflight route and rebuild/bind the v2 cache explicitly before planning. Do not refresh or fall back to legacy cache during capture plan.

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

When a plan is ready, show a concise but reviewable confirmation template:

```txt
计划写入：
- 目标：<target page> / <data source>
- 操作：新建 / 更新
- 页面：<新建时写标题；更新时写 page_id + 标题>
- 标题：<title>
- 状态：<state>
- 关键字段：
  - <字段1>：<值的短摘要>
  - <字段2>：<值的短摘要>
  - <字段3>：<值的短摘要>
- 摘要来源：<逐字稿 / 页面简介 + Show Notes / 用户输入>
- 未写入字段：<重要但本次不写入的字段及原因>
- 风险/限制：<例如“未找到逐字稿”“搜索未命中同名页，预计新建”>
- 确认后动作：运行 `capture apply --confirmed`

是否确认写入？
```

Keep the plan detailed enough to verify the target page, operation, important field values, omissions, and risks. Do not include full JSON, full search results, or long previews unless the user asks.

## Safety Rules

- Never silently write to Notion on first use of a target page.
- Never overwrite or modify Notion schema automatically.
- Never store business cache in Claude memory.
- Stay cache-first: when reliable target cache or schema facts already exist, use them before considering a re-scan; when preflight requests `sync_target_cache`, let `capture plan` perform the scoped cache sync instead of choosing another target.
- Do not use Notion MCP as a fallback; stay within the capture-to-notion Skill backend and CLI flow.
- External URLs are not automatically parsed or fetched by default; recommend or ask first.
- Missing `/summarize` CLI/backend is not a blocker by itself; when the user requested summarization and enough content is available to the current AI session, use AI fallback summarization and continue to preflight/plan.
- If preflight or plan has no target, ask the user to choose a Notion page.
- If preflight marks the target as risky or ambiguous, explain it and wait before planning or writing.
- If cover handling fails, preserve the main plan and report the warning.
