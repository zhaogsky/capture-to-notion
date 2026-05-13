# P1 Target Visibility and Golden Cases — Status

**Status:** Completed and superseded by the current implementation.

This plan is retained only as a historical record. Do not execute the old checklist from this file.

## Current state

Implemented in the codebase:

- Local-only target visibility commands:
  - `capture-to-notion target list`
  - `capture-to-notion target inspect`
- Target cache summary/detail helpers in `CacheStore`.
- Golden planner regression cases in `tests/test_golden_cases.py`.
- README coverage for the target visibility commands.

## Current constraints

- Target list/inspect must remain local-cache-only and must not instantiate the Notion adapter.
- Golden cases should use explicit target cache mappings and parser profiles for content-specific parsing.
- Do not reintroduce schema-level business field inference.
- Do not add Notion MCP fallback.

## Verification source

Use current tests as the source of truth:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_cli_target.py tests/test_golden_cases.py -q
```
