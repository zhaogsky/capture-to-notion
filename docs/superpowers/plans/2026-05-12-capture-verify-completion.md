# Capture Verify Completion — Status

**Status:** Completed and superseded by the current implementation.

This plan is retained only as a historical record. Do not execute the old checklist from this file.

## Current state

Implemented in the codebase:

- `verify_capture_page()` is generic and read-only.
- Standalone `capture verify --page-id` without a plan or explicit mapping checks only page presence and page cover.
- Field verification uses caller-provided checks plus `field_mapping` and schema, not Notion property-name guessing.
- `capture apply` attaches a top-level `verification` summary for written pages when possible.
- Apply-time verification derives checks from the write plan and target cache schema.
- Verification warnings do not hide the apply result.

## Current constraints

- Verifier must not infer title, state, author, ISBN, page count, cover, or any business field from Notion property names.
- Relation verification checks relation values on the captured page only; relation target page validation requires explicit future cache/plan/mapping support.
- Files and page cover URL checks must use Notion page property value / page cover shapes.
- Do not hardcode author picture or any other business-specific related-page field.
- Do not add Notion MCP fallback.

## Verification source

Use current tests as the source of truth:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_capture_apply.py -q
```
