---
name: capture-to-notion
description: Use when the user asks to save, initialize, complete, summarize-and-store, or recommend a Notion destination for books, podcast episodes, meeting notes, articles, or external content.
---

# Capture to Notion Skill

Use this skill when the user asks to capture content into Notion, initialize or complete a book/podcast item, summarize content before saving it, scan a Notion target, or choose where content should be stored.

## Current Capability Boundary

The CLI can preflight captures, suggest targets, scan Notion pages/databases, plan captures, apply confirmed write plans, and verify through its own commands. Do not silently write to Notion: create or inspect deterministic preflight/plan facts first, then run `capture-to-notion capture apply --plan /path/to/plan.json --confirmed` only when the user has explicitly confirmed the target and write.

Notion target resolution, cache/schema inspection, planning, applying, and verification must stay inside the agreed `capture-to-notion` CLI/Skill flow. AI/web research, URL parsing, source-material downloads, summarization, and `input.json` construction are allowed when requested or confirmed, but their results must feed back into the normal preflight/plan/apply flow and must not directly read, write, or verify Notion state. Do not write ad hoc Python/JS/shell scripts, import internal adapters, call the Notion API directly, hand-edit executable plan JSON, or use Notion MCP to read, write, or verify Notion state. If the CLI lacks the needed Notion read/write/verification ability, stop and tell the user the packaged capability is missing; ask whether to add that CLI capability.

Supported write behavior includes cached target/schema mapping, generic relation and people resolution by Notion property type, explicit asset actions, cover/file download-cache-upload on success, and warning reporting when optional enrichment fails. `download_and_attach` failures must not fall back to writing the source URL as an external file.

Capture plans depend on v2 write profiles, target graph schema, and trusted field mappings. Do not use Notion MCP or property-name guessing to fill business mappings. When `field_sources` are present, treat only trusted profile/user bindings as trusted for required mappings; warnings such as `*_schema_incomplete`, `*_key_values_missing`, and `untrusted_field_mapping` must be shown to the user for confirmation.

The CLI/backend is a capability and fact layer: it scans, resolves, plans, writes, verifies, and exposes optional location facts such as `target_path` and `target_path_complete`. It must not be treated as the layer that understands whether a human phrase like “under the podcast page” semantically matches the selected target. That target-path confirmation is the Skill AI orchestration layer's responsibility.

## V2 Notion Graph Cache

Capture to Notion uses only the v2 graph cache under `cache-v2/`. Legacy target cache files are not read by capture preflight, plan, or apply. If a target has no v2 graph/profile, route to scan/bind instead of falling back to old cache.

A v2 target consists of:
- a graph: pages, blocks, databases, data sources, and views from Notion API;
- a write profile: content type to canonical data source/view plus field mappings;
- an alias: user-facing name to graph/profile.

Notion object roles are generic and distinct:
- Page parent writes create ordinary child pages and store body content as blocks.
- Data source writes create/update structured pages conforming to schema.
- Database objects are containers used to discover data sources; writes do not create rows directly under databases.
- Block objects carry page body content; created via page creation or append-block operations.
- View objects are display/query context; not write parents.

## Generic Planning and Verification Rules

Keep all write decisions profile/cache/plan-driven, not business-name driven:

- When the user names a hierarchical target or location, compare the compact target/path facts (`target.target_path`, `target.target_path_complete`, and `review.target_semantics`) with the user's wording before asking to apply. If the path is missing, incomplete, or does not clearly match the user's intended hierarchy, do not claim the target is confirmed; ask the user to choose the exact target or scan the exact page/database. If the scanned page's real title/content and cache/profile output disagree, stop the write flow, rescan the real page to update cache, and if the mismatch remains, fix the scanner/cache fact extraction before presenting a new confirmable write plan. An alias/cache hit alone is not semantic confirmation.
- When the user targets a view/list/table/gallery/board/calendar/timeline, the plan must write to the underlying data source and satisfy the target view's safely translatable constraints. If the compact `review.view_constraints` shows conflicts or unresolved constraints, stop for confirmation instead of pretending the new or updated page will appear in that view.
- Relation fields are not successful until the plan/apply result resolves actual page IDs. If relation lookup is ambiguous, unresolved, or lacks a target data source, treat it as blocking when the profile marks it required or the compact `review.blocking_warnings` says so.
- When compact plan/review exposes `relation_resolution_requirements`, show the candidate pages (`page_id`, title/name, URL, last edited time when available) and ask the user to choose an existing page, create a new page, or explicitly skip; do not ask for final write confirmation while a blocking ambiguous relation remains unresolved.
- When compact plan/review exposes `people_resolution_requirements`, show the candidate users (`user_id`/`id`, name, email, avatar URL, type when available) and ask the user to choose an existing user or explicitly skip. People candidates must be selected by the user; do not automatically guess based on similar names.
- Required fields and expected values come from the write profile, scanned schema, user-confirmed mappings, and the executable plan. Do not infer required business fields from property names alone.
- Before asking the user to apply a plan, list the concrete pages involved from `summary.write_targets`: the main page to create/update, ordinary child pages, completion targets, and any `relation_target_page` to create. If a page has no `page_id` yet or a relation remains unresolved, say that explicitly instead of only showing the target database or field.
- Treat `relation_target_page` and other nested write targets as first-class planned writes, not relation side effects. If a nested target has `shell_page_risk`, `needs_enrichment`, `needs_user_choice`, or a blocking warning such as `relation_target_shell_page`, do not ask only “是否确认写入”; ask whether to补全、选择字段值、确认跳过, or change target/input.
- Use `review.enrichment_requirements` as the orchestration checklist for missing nested fields. `requirement_type=enrichment` means ask whether to use confirmed search/source content/user input or skip; `requirement_type=user_choice` means ask the user to choose from target options or provide an explicit value. Do not silently fetch external URLs or search just because a requirement exists.
- When the user provides or skips enrichment/user-choice requirements, rerun preflight/plan with `input.enrichment.requirement_decisions[]`. Each decision should copy the requirement identity (`target_type`, `source_record_key`, `source_value`, `target_data_source_id`, `field`) and use `action: "provide_value"`, `"choose_value"`, or `"skip"`; include `value` for provided/chosen values and `reason` for skips.
- Asset actions must be reviewed before writing. A cover/image may target a files property, the page cover, or both. Upload success can produce a Notion-hosted URL different from the source URL, so verification should check presence/accessibility for uploaded files and exact source URL only for page cover expectations.
- After apply, report verification by plan expectations. Prefer `review.verification_expectations.targets` when present so primary pages, nested relation target pages, required fields, relation IDs, files/cover expectations, pending enrichment fields, user-choice fields, and computed/skipped fields are reported separately.
- When reporting apply results for a targeted view, distinguish “written to the underlying data source” from “satisfies the target view's verifiable constraints.” If verification reports `view_visibility: not_guaranteed`, say the backend cannot guarantee the page appears in the view instead of implying view visibility.

## Summary Preprocessing

If the user asks to summarize, extract, condense,整理, 归纳, 提炼, or save a long transcript/article/notes after summarizing, summarize before building the capture input.

1. Use the current AI session with the built-in summary prompt by default. Do not require a separate `/summarize` skill, local CLI, or external summarization dependency for normal daily use.
2. Treat `/summarize` or other external parsing/summarization tools as optional enhancements only when the user explicitly requests them, the current AI session cannot access enough source content, the source needs specialized extraction such as long PDF/webpage/audio/video transcript parsing, or the user confirms the extra parsing step.
3. If enough source content is available in the conversation, summarize it directly and use the summary as the main captured content or as a notes/summary candidate in `raw_input`.
4. If only URL metadata, page intro, SEO description, or show notes are available, do not present the result as a full-content summary. Ask the user to provide transcript/full text, or ask whether to proceed with a limited metadata-based summary.
5. Include a source marker in `raw_input` or plan-review text when useful, such as `summary_source: user_provided_full_text`, `summary_source: ai_fallback`, `summary_source: metadata_only`, or `summary_source: page_intro_and_show_notes`.
6. Continue with the normal capture planning flow after summarization.

## Built-in Summary Prompt

When summary preprocessing is needed and enough source content is available in the current conversation, use this general-purpose summary prompt before building the capture input:

```markdown
你是一个严谨的信息总结助手。

请基于我提供的内容，生成一份中文总结。

要求：

1. 忠于来源内容，不要编造来源中没有的信息。
2. 根据内容类型选择合适的总结方式，不要机械套用固定模板。
3. 如果内容中有重要的人物、时间、地点、产品、数字、事件、案例、结论或行动项，请在总结中自然保留。
4. 如果来源内容不完整，例如只有标题、简介、片段、Show Notes、网页摘要或元数据，请明确说明总结依据和限制。
5. 如果来源中存在不确定、推测或缺失的信息，不要把它写成确定事实。
6. 总结要清楚、完整、有层次，适合后续保存到知识库或继续处理。

输出：

请直接详细总结内容。必要时可以使用小标题或项目符号，但不要为了格式而强行拆分。
```

## Summary-Like Target Fields

A target/profile may mark mapped fields as summary-like with `summary_fields` and `summary_policy`. Treat those fields as generated summaries, not metadata copy targets.

When a summary-like field is planned:

1. Prefer a real content source: transcript, full article text, audio/video content, full show notes, or another source representing the body content.
2. If the content source is available in the current conversation, use the built-in summary prompt directly.
3. Do not use page-visible metadata such as title, SEO description, or a short platform intro as a full-content summary.
4. If only limited metadata, page intro, or show notes are available, the summary-like field may be written only as a limited summary with the source limitation clearly marked for plan review.
5. If no acceptable source is available for the target's expected summary quality, do not write the summary-like field. Present the plan's `enrichment_requirements` and ask the user to provide transcript/content source or confirm accepting a limited metadata-based summary.
6. Keep this profile-driven: do not hardcode a Notion property name, page title, platform name, or content type in runtime logic.

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
2. Skill AI first parses the user's intent and input shape before planning. Decide whether the user wants direct write, target recommendation, 补充信息, or URL-related enrichment advice; do not treat a URL, generic content type, or generic “save/store/capture” wording as permission to fetch, enrich, or write without the preflight route and user confirmation rules below.
   - Before forcing `scan_target` or `target bind-profile`, distinguish structured database entries from ordinary child pages. If an already-scanned page-only target is intended for saving an article, note, or plain text, route toward page-parent `capture_plan` rather than requiring profile binding.
3. Parse the user's request into `input.json`.
4. Include:
   - `raw_input`: the user's content, or the summarized content when Summary Preprocessing applied.
   - `target_hint`: target page or alias if the user gave one, otherwise `null`.
   - `state`: `initialized`, `completed`, a user-confirmed target option, or `null`. Only set state when the user explicitly gives a status phrase such as 初始化/想读/待听/完成/已读/听完/进行中, or when a trusted target profile maps it. Do not infer completed/initialized from “summarize”, “store”, “capture”, or other generic actions.
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
   - `sync_target_cache`: the CLI explicitly requests a scoped cache sync for the resolved target; run `capture plan --compact` and let the CLI perform that exact sync request before planning.
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

12. Present the compact stdout preflight conclusion and plan to the user in concise Chinese. Use the compact `review` section to surface target semantics, target path/location facts, concrete write pages from `summary.write_targets`, view constraints, expected fields, relation actions, nested `relation_target_page` plans, enrichment/user-choice requirements, asset actions, blocking warnings, and layered verification expectations. The `--output` plan file remains the complete executable JSON for apply.
13. If the user gave a path-like target intent and the compact path facts are incomplete or unclear, ask the user to confirm the exact target path or choose/scan the exact target before writing.
14. If `review.enrichment_requirements` is non-empty, group requirements by target page and ask the user whether to补全、选择字段值、提供输入、联网搜索（only if confirmed/requested）、or explicitly skip. Do not run `capture apply --confirmed` while blocking enrichment/user-choice requirements remain unresolved or unskipped. After the user answers, keep the original capture input and add `enrichment.requirement_decisions` entries for each provided/chosen/skipped requirement, then rerun preflight/plan.
15. If `review.template_options` is present, show the available templates and ask whether to use a template or skip templates before applying. If the plan reports `apply_status: unsupported`, say clearly that the backend can currently only display template facts and cannot actually apply that Notion template.
16. If `requires_confirmation` is true, ask the user to confirm or choose a target before writing only after explaining blocking warnings and unresolved nested targets. If `confirmation_reason` is `relation_target_shell_page`, the next prompt must be about completing, choosing values for, or explicitly skipping nested target fields, not just confirming the write.
17. If the compact review has blocking view constraints, unresolved required relations, required fields without values, nested target shell-page risks, required assets that cannot be written, or unresolved `verification_expectations.targets`, explain the blocker before requesting confirmation.
18. If the user explicitly confirms the plan and target, run:

```bash
capture-to-notion capture apply --plan /path/to/plan.json --confirmed
```

19. Present the apply result, including warnings, asset results, and `verification` warnings when present. When reporting written locations, default to the final complete written path from `written_targets[].created_path` or equivalent full path only; do not separately list the parent target path unless it is needed for disambiguation or troubleshooting. When `verification_expectations.targets` exists, report apply/verify status per target page rather than collapsing nested target status into the primary page.

## Apply Safety Failure Handling

Treat apply-time safety failures as stale or unsafe plans, not as backend recovery tasks.

- If `capture apply --confirmed` returns `update_page_safety_failed:*`, `NotionNotFoundError`, or a page-not-found error for an update target, stop the current write flow. Do not retry the same plan and do not ask the backend to auto-create, unarchive, relocate, or search.
- Explain the failed invariant in user terms: archived/in trash page, missing page, parent data source mismatch, or explicit current-title mismatch.
- Offer 2-3 orchestration choices, such as regenerating a new-create plan without `existing_page_id`, asking the user to restore the archived page before retrying, or re-running target/page resolution when the live page points somewhere else.
- When the user chooses a path, build a fresh `input.json`, rerun preflight and plan, then ask for explicit confirmation before applying. Never hand-edit the executable plan JSON to remove or replace `existing_page_id`.

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
- 页面：<从 summary.write_targets 列出 primary_page、child_page、relation_target_page 等；新建时写标题，更新时写 page_id + 标题>
- 标题：<title>
- 状态：<state>
- 目标语义：<page_parent / data_source / existing_page / view-backed data source>
- 目标路径：<完整路径；若 target_path_complete=false，说明需要用户确认或重新扫描>
- 视图约束：<已满足 / 冲突 / 无法安全转写>
- 关键字段：
  - <字段1>：<值的短摘要>
  - <字段2>：<值的短摘要>
  - <字段3>：<值的短摘要>
- Relation / 嵌套页面：<已解析 page_id / 预计创建 relation_target_page / 阻塞原因>
- 待补全/待选择：<按 review.enrichment_requirements 分组列出 enrichment 和 user_choice；无则写无>
- 图片/文件：<files 字段 / page cover / 上传或外链策略>
- 验证预期：<优先按 review.verification_expectations.targets 分目标页面列出 required fields / relations / assets / page cover / pending enrichment / user choices / computed fields>
- 摘要来源：<逐字稿 / 页面简介 + Show Notes / 用户输入>
- 未写入字段：<重要但本次不写入的字段及原因>
- 风险/限制：<例如“未找到逐字稿”“relation target 只会创建空壳页”“relation 未解析”“view 约束冲突”“图片不可访问”>
- 下一步：<若有 blocking enrichment/user_choice/shell_page_risk，询问补全/选择/跳过；只有无阻塞或用户明确确认后才运行 `capture apply --confirmed`>

是否要先补全/选择/跳过上述阻塞项，还是确认当前计划写入？
```

Keep the plan detailed enough to verify the target page, operation, important field values, omissions, and risks. Do not include full JSON, full search results, or long previews unless the user asks.

## Safety Rules

- Never silently write to Notion on first use of a target page.
- Never overwrite or modify Notion schema automatically.
- Never store business cache in Claude memory.
- Stay cache-first: when reliable target cache or schema facts already exist, use them before considering a re-scan; when preflight requests `sync_target_cache`, let `capture plan` perform the scoped cache sync instead of choosing another target.
- Do not use Notion MCP as a fallback; stay within the capture-to-notion Skill backend and CLI flow.
- Do not bypass the packaged CLI with ad hoc scripts, direct imports of internal adapters, direct Notion API calls, manual executable-plan edits, or custom verification probes. If `capture-to-notion` cannot do the required action, report the capability gap and ask before extending the CLI.
- External URLs are not automatically parsed or fetched by default; recommend or ask first.
- Missing `/summarize` CLI/backend is not a blocker by itself; when the user requested summarization and enough content is available to the current AI session, use AI fallback summarization and continue to preflight/plan.
- If preflight or plan has no target, ask the user to choose a Notion page.
- If preflight marks the target as risky or ambiguous, explain it and wait before planning or writing.
- If cover handling fails, preserve the main plan and report the warning.
- Do not treat page creation/update as success by itself; success requires apply verification to satisfy the plan's required fields, relation IDs, files/cover expectations, and view-derived constraints.
