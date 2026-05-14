# Capture Preflight Structure Analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cache-first capture preflight layer and generic structure analyzer so the Skill can first understand user intent, inspect target structure facts, and recommend direct write/search/URL parsing/user input/target confirmation before planning or applying.

**Architecture:** Keep natural-language intent and final recommendation wording in the Skill AI, while Python provides deterministic structure facts: cache state, data source candidates, property capabilities, risk flags, safe actions, and blocked actions. Add `capture preflight` before `capture plan`; keep `planner.py` as the WritePlan builder and avoid page-specific or business-field hardcoding in schema/planner/verifier logic.

**Tech Stack:** Python 3, pytest, existing `capture_to_notion` CLI modules, JSON cache under `~/.config/capture-to-notion/`, Notion schema objects normalized by existing `schema.py`.

---

## Scope and Constraints

- Do not write Notion data from preflight.
- Do not call Notion API from preflight; use `CacheStore` and existing target cache only.
- Do not parse or fetch external URLs automatically in preflight.
- Do not hardcode concrete business fields or target pages in core logic.
- Risk keywords must be centralized in an overridable policy object, not scattered through branches.
- Skill AI owns intent understanding and user-facing recommendation; Python owns structure facts and safety constraints.
- Do not create git commits unless the user explicitly requests a commit after implementation.

## File Structure

- Create: `capture_to_notion/structure_analyzer.py` — converts cached Notion target structures into generic data source candidates, property capabilities, and risk flags.
- Create: `capture_to_notion/preflight.py` — builds capture preflight JSON from `CaptureInput` + `CacheStore`, using cache and structure analyzer only.
- Modify: `capture_to_notion/models.py` — add optional intent/input/target hint fields to `CaptureInput` while preserving backward compatibility.
- Modify: `capture_to_notion/cli.py` — add `capture preflight --input ...` command.
- Modify: `capture_to_notion/config.py` — optionally expose default structure-analysis policy loading if config already has a clean config data path; otherwise keep policy injection in analyzer for this slice.
- Modify: `capture_to_notion/planner.py` — only add defensive tests/fixes if current plan generation bypasses existing confirmation barriers; do not move recommendation logic here.
- Modify: `SKILL.md` — make preflight the first backend action after Skill AI intent/input parsing.
- Test: `tests/test_preflight.py` — target/cache/safe-action preflight tests.
- Test: `tests/test_structure_analyzer.py` — property capability, candidate, and risk-policy tests.
- Test: `tests/test_cli.py` or existing CLI test file — CLI preflight command tests.
- Test: existing planner tests only if needed for defensive behavior.

---

### Task 1: Extend CaptureInput hints

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/models.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py`

- [ ] **Step 1: Write the failing tests**

Create `/Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py` with the following initial tests:

```python
from capture_to_notion.models import CaptureInput


def test_capture_input_accepts_preflight_hints():
    capture = CaptureInput.from_dict(
        {
            "raw_input": "小宇宙单集：https://example.com/episode/1",
            "intent_hint": "capture_write",
            "input_shape_hint": "external_url",
            "target_hint": "后互联网时代的乱弹",
            "target_context_hint": "播客",
            "target_scope_hint": "inside_child_page",
            "user_requested_action": "write_after_recommendation",
            "content_type_hint": "podcast_episode",
        }
    )

    assert capture.intent_hint == "capture_write"
    assert capture.input_shape_hint == "external_url"
    assert capture.target_context_hint == "播客"
    assert capture.target_scope_hint == "inside_child_page"
    assert capture.user_requested_action == "write_after_recommendation"


def test_capture_input_remains_backward_compatible_without_preflight_hints():
    capture = CaptureInput.from_dict({"raw_input": "把《可能性的艺术》初始化到书单", "target_hint": "书单"})

    assert capture.intent_hint is None
    assert capture.input_shape_hint is None
    assert capture.target_context_hint is None
    assert capture.target_scope_hint is None
    assert capture.user_requested_action is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_preflight.py::test_capture_input_accepts_preflight_hints tests/test_preflight.py::test_capture_input_remains_backward_compatible_without_preflight_hints -q
```

Expected: FAIL with `AttributeError` for missing hint attributes.

- [ ] **Step 3: Add hint fields to `CaptureInput`**

In `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/models.py`, update the dataclass:

```python
@dataclass
class CaptureInput:
    raw_input: str
    target_hint: str | None = None
    state: str | None = "initialized"
    content_type_hint: str | None = None
    user_intent: str = "capture_to_notion"
    options: CaptureOptions = field(default_factory=CaptureOptions)
    intent_hint: str | None = None
    input_shape_hint: str | None = None
    target_context_hint: str | None = None
    target_scope_hint: str | None = None
    user_requested_action: str | None = None
```

Update `from_dict()` to pass through those keys:

```python
return cls(
    raw_input=data["raw_input"],
    target_hint=data.get("target_hint"),
    state=data.get("state", "initialized"),
    content_type_hint=data.get("content_type_hint"),
    user_intent=data.get("user_intent", "capture_to_notion"),
    options=options,
    intent_hint=data.get("intent_hint"),
    input_shape_hint=data.get("input_shape_hint"),
    target_context_hint=data.get("target_context_hint"),
    target_scope_hint=data.get("target_scope_hint"),
    user_requested_action=data.get("user_requested_action"),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_preflight.py::test_capture_input_accepts_preflight_hints tests/test_preflight.py::test_capture_input_remains_backward_compatible_without_preflight_hints -q
```

Expected: PASS.

---

### Task 2: Add generic structure analyzer property capabilities

**Files:**
- Create: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/structure_analyzer.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_structure_analyzer.py`

- [ ] **Step 1: Write failing tests for property capabilities**

Create `/Users/aaron/.claude/skills/capture-to-notion/tests/test_structure_analyzer.py`:

```python
from capture_to_notion.structure_analyzer import analyze_target_structure


def test_analyze_target_structure_groups_properties_by_notion_type():
    structure = {
        "target": {"page_id": "page-1", "title": "目标页面"},
        "data_sources": {
            "ds-1": {
                "data_source_id": "ds-1",
                "title": "Entries",
                "schema": {
                    "标题": {"name": "标题", "type": "title"},
                    "正文": {"name": "正文", "type": "rich_text"},
                    "日期": {"name": "日期", "type": "date"},
                    "状态": {
                        "name": "状态",
                        "type": "select",
                        "options": [{"name": "未开始", "color": "blue"}],
                    },
                    "附件": {"name": "附件", "type": "files"},
                    "来源": {"name": "来源", "type": "url"},
                    "关联": {"name": "关联", "type": "relation", "target_database_id": "db-2"},
                },
            }
        },
    }

    result = analyze_target_structure(structure)

    candidate = result["data_source_candidates"][0]
    assert candidate["data_source_id"] == "ds-1"
    assert candidate["property_capabilities"]["title"] == ["标题"]
    assert candidate["property_capabilities"]["rich_text"] == ["正文"]
    assert candidate["property_capabilities"]["date"] == ["日期"]
    assert candidate["property_capabilities"]["files"] == ["附件"]
    assert candidate["property_capabilities"]["url"] == ["来源"]
    assert candidate["property_capabilities"]["relations"] == [
        {"field": "关联", "target_database_id": "db-2"}
    ]
    assert candidate["property_capabilities"]["select"] == [
        {"field": "状态", "options": ["未开始"]}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_structure_analyzer.py::test_analyze_target_structure_groups_properties_by_notion_type -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'capture_to_notion.structure_analyzer'`.

- [ ] **Step 3: Implement minimal analyzer**

Create `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/structure_analyzer.py`:

```python
from __future__ import annotations

from typing import Any

CAPABILITY_TYPES = (
    "title",
    "rich_text",
    "date",
    "files",
    "url",
    "number",
    "checkbox",
    "people",
    "email",
    "phone_number",
    "multi_select",
)


def _property_capabilities(schema: dict[str, Any]) -> dict[str, Any]:
    capabilities: dict[str, Any] = {property_type: [] for property_type in CAPABILITY_TYPES}
    capabilities["select"] = []
    capabilities["status"] = []
    capabilities["relations"] = []

    for field_name, property_schema in schema.items():
        if not isinstance(property_schema, dict):
            continue
        property_type = property_schema.get("type")
        if property_type in CAPABILITY_TYPES:
            capabilities[property_type].append(field_name)
            continue
        if property_type in {"select", "status"}:
            options = [
                option.get("name")
                for option in property_schema.get("options", [])
                if isinstance(option, dict) and option.get("name")
            ]
            capabilities[property_type].append({"field": field_name, "options": options})
            continue
        if property_type == "relation":
            capabilities["relations"].append(
                {"field": field_name, "target_database_id": property_schema.get("target_database_id")}
            )
    return capabilities


def _data_source_candidate(data_source: dict[str, Any]) -> dict[str, Any]:
    schema = data_source.get("schema", {})
    if not isinstance(schema, dict):
        schema = {}
    return {
        "data_source_id": data_source.get("data_source_id"),
        "title": data_source.get("title"),
        "writable": bool(schema),
        "role_candidate": "primary_record_table" if schema else "unknown",
        "confidence": "medium" if schema else "low",
        "risk_flags": [],
        "property_capabilities": _property_capabilities(schema),
    }


def analyze_target_structure(
    structure: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data_sources = structure.get("data_sources", {})
    if not isinstance(data_sources, dict) or not data_sources:
        return {
            "source": "cache",
            "status": "missing_data_source",
            "data_source_candidates": [],
            "risk_flags": [{"code": "no_writable_data_source", "level": "error"}],
            "structure_complexity": "empty",
        }

    candidates = [
        _data_source_candidate(data_source)
        for data_source in data_sources.values()
        if isinstance(data_source, dict)
    ]
    return {
        "source": "cache",
        "status": "usable" if any(candidate["writable"] for candidate in candidates) else "schema_incomplete",
        "data_source_candidates": candidates,
        "risk_flags": [],
        "structure_complexity": "single_data_source" if len(candidates) == 1 else "multiple_data_sources",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_structure_analyzer.py::test_analyze_target_structure_groups_properties_by_notion_type -q
```

Expected: PASS.

---

### Task 3: Add generic risk policy for check-in/navigation-like targets

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/structure_analyzer.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_structure_analyzer.py`

- [ ] **Step 1: Add failing tests for policy-driven risk flags**

Append to `tests/test_structure_analyzer.py`:

```python

def test_analyze_target_structure_marks_name_pattern_risk_from_policy():
    structure = {
        "target": {"page_id": "page-1", "title": "习惯"},
        "data_sources": {
            "ds-1": {
                "data_source_id": "ds-1",
                "title": "每日签到",
                "schema": {
                    "名称": {"name": "名称", "type": "title"},
                    "日期": {"name": "日期", "type": "date"},
                    "完成": {"name": "完成", "type": "checkbox"},
                },
            }
        },
    }
    policy = {"risk_patterns": {"checkin_like_target": ["签到"]}}

    result = analyze_target_structure(structure, policy=policy)

    candidate = result["data_source_candidates"][0]
    assert candidate["risk_flags"] == [
        {
            "code": "checkin_like_target",
            "level": "warning",
            "message": "Target name matches configured risk pattern: checkin_like_target",
        }
    ]
    assert result["risk_flags"] == candidate["risk_flags"]


def test_analyze_target_structure_marks_tracking_shape_without_business_field_mapping():
    structure = {
        "target": {"page_id": "page-1", "title": "记录"},
        "data_sources": {
            "ds-1": {
                "data_source_id": "ds-1",
                "title": "Daily Records",
                "schema": {
                    "名称": {"name": "名称", "type": "title"},
                    "日期": {"name": "日期", "type": "date"},
                    "完成": {"name": "完成", "type": "checkbox"},
                    "状态": {"name": "状态", "type": "status", "options": []},
                    "成员": {"name": "成员", "type": "people"},
                },
            }
        },
    }

    result = analyze_target_structure(structure, policy={"tracking_shape_threshold": 0.6})

    assert result["data_source_candidates"][0]["risk_flags"] == [
        {
            "code": "tracking_like_target",
            "level": "warning",
            "message": "Target schema is dominated by tracking-style property types.",
        }
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_structure_analyzer.py::test_analyze_target_structure_marks_name_pattern_risk_from_policy tests/test_structure_analyzer.py::test_analyze_target_structure_marks_tracking_shape_without_business_field_mapping -q
```

Expected: FAIL because risk flags are still empty.

- [ ] **Step 3: Implement centralized risk policy**

In `structure_analyzer.py`, add:

```python
DEFAULT_STRUCTURE_POLICY: dict[str, Any] = {
    "risk_patterns": {
        "checkin_like_target": ["签到", "打卡", "checkin", "check-in", "habit"],
        "navigation_like_target": ["导航", "入口", "index", "home"],
        "archive_like_target": ["归档", "archive"],
    },
    "tracking_shape_types": ["date", "checkbox", "status", "select", "people"],
    "tracking_shape_threshold": 0.75,
}
```

Add helpers:

```python
def _merged_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_STRUCTURE_POLICY)
    if not policy:
        return merged
    for key, value in policy.items():
        if key == "risk_patterns" and isinstance(value, dict):
            patterns = dict(merged.get("risk_patterns", {}))
            patterns.update(value)
            merged[key] = patterns
        else:
            merged[key] = value
    return merged


def _name_risk_flags(names: list[str], policy: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    risk_patterns = policy.get("risk_patterns", {})
    if not isinstance(risk_patterns, dict):
        return flags
    joined_names = " ".join(name.lower() for name in names if isinstance(name, str))
    for code, patterns in risk_patterns.items():
        if not isinstance(code, str) or not isinstance(patterns, list):
            continue
        if any(isinstance(pattern, str) and pattern.lower() in joined_names for pattern in patterns):
            flags.append(
                {
                    "code": code,
                    "level": "warning",
                    "message": f"Target name matches configured risk pattern: {code}",
                }
            )
    return flags


def _tracking_shape_risk_flags(schema: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, str]]:
    property_types = [
        property_schema.get("type")
        for property_schema in schema.values()
        if isinstance(property_schema, dict) and property_schema.get("type") != "title"
    ]
    if not property_types:
        return []
    tracking_types = set(policy.get("tracking_shape_types", []))
    tracking_count = sum(1 for property_type in property_types if property_type in tracking_types)
    threshold = policy.get("tracking_shape_threshold", 0.75)
    if tracking_count / len(property_types) >= threshold:
        return [
            {
                "code": "tracking_like_target",
                "level": "warning",
                "message": "Target schema is dominated by tracking-style property types.",
            }
        ]
    return []
```

Update `_data_source_candidate(data_source, target_title, policy)` to collect:

```python
risk_flags = _name_risk_flags([target_title, data_source.get("title")], policy)
risk_flags.extend(_tracking_shape_risk_flags(schema, policy))
```

Update `analyze_target_structure()` to call `_merged_policy(policy)` and aggregate candidate risk flags into top-level `risk_flags` without duplicates.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_structure_analyzer.py -q
```

Expected: PASS.

---

### Task 4: Add capture preflight from cache and analyzer

**Files:**
- Create/Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/preflight.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py`

- [ ] **Step 1: Add failing preflight behavior tests**

Append to `tests/test_preflight.py`:

```python
from capture_to_notion.cache import CacheStore
from capture_to_notion.config import AppConfig
from capture_to_notion.preflight import build_capture_preflight


def make_cache(tmp_path):
    root = tmp_path / "config"
    return CacheStore(
        AppConfig(
            root=root,
            config_file=root / "config.json",
            aliases_file=root / "aliases.json",
            routes_file=root / "routes.json",
            targets_dir=root / "targets",
            plans_dir=root / "plans",
            covers_dir=root / "cache" / "covers",
            enrichment_dir=root / "cache" / "enrichment",
        )
    )


def test_preflight_without_target_suggests_target(tmp_path):
    cache = make_cache(tmp_path)
    capture = CaptureInput.from_dict({"raw_input": "把《可能性的艺术》存一下"})

    result = build_capture_preflight(capture, cache)

    assert result["target"] == {"status": "missing"}
    assert "suggest_target" in result["safe_actions"]
    assert {"action": "plan_directly", "reason": "target_missing"} in result["blocked_actions"]


def test_preflight_with_missing_alias_requests_target_resolution(tmp_path):
    cache = make_cache(tmp_path)
    capture = CaptureInput.from_dict({"raw_input": "把《可能性的艺术》存到书单", "target_hint": "书单"})

    result = build_capture_preflight(capture, cache)

    assert result["target"] == {"status": "alias_missing", "target_hint": "书单"}
    assert "suggest_target" in result["safe_actions"]
    assert {"action": "plan_directly", "reason": "target_not_resolved"} in result["blocked_actions"]


def test_preflight_with_cache_hit_uses_cached_structure(tmp_path):
    cache = make_cache(tmp_path)
    cache.write_json(
        cache.config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-1", "target_id": "target-1"}}},
    )
    cache.write_json(
        cache.config.targets_dir / "target-1.json",
        {
            "target": {"page_id": "page-1", "title": "书单", "target_id": "target-1"},
            "data_sources": {
                "ds-1": {
                    "data_source_id": "ds-1",
                    "title": "Books",
                    "schema": {"名称": {"name": "名称", "type": "title"}},
                }
            },
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》存到书单",
            "target_hint": "书单",
            "intent_hint": "capture_write",
            "input_shape_hint": "short_key",
        }
    )

    result = build_capture_preflight(capture, cache)

    assert result["intent_hint"] == "capture_write"
    assert result["input_shape_hint"] == "short_key"
    assert result["target"]["status"] == "cache_hit"
    assert result["target"]["target_id"] == "target-1"
    assert result["structure"]["source"] == "cache"
    assert result["structure"]["data_source_candidates"][0]["data_source_id"] == "ds-1"
    assert "ask_before_plan" in result["safe_actions"]
    assert {"action": "apply_directly", "reason": "plan_required"} in result["blocked_actions"]


def test_preflight_with_risky_target_blocks_direct_plan(tmp_path):
    cache = make_cache(tmp_path)
    cache.write_json(
        cache.config.aliases_file,
        {"aliases": {"习惯": {"type": "page", "page_id": "page-1", "target_id": "target-1"}}},
    )
    cache.write_json(
        cache.config.targets_dir / "target-1.json",
        {
            "target": {"page_id": "page-1", "title": "习惯", "target_id": "target-1"},
            "data_sources": {
                "ds-1": {
                    "data_source_id": "ds-1",
                    "title": "每日签到",
                    "schema": {
                        "名称": {"name": "名称", "type": "title"},
                        "日期": {"name": "日期", "type": "date"},
                        "完成": {"name": "完成", "type": "checkbox"},
                    },
                }
            },
        },
    )
    capture = CaptureInput.from_dict({"raw_input": "存一下这本书", "target_hint": "习惯"})

    result = build_capture_preflight(capture, cache)

    assert "confirm_risky_target" in result["safe_actions"]
    assert {"action": "plan_directly", "reason": "risky_target_requires_confirmation"} in result["blocked_actions"]
    assert result["confirmation_needed"][0]["code"] in {"checkin_like_target", "tracking_like_target"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_preflight.py -q
```

Expected: FAIL because `build_capture_preflight` is missing or incomplete.

- [ ] **Step 3: Implement preflight**

Create `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/preflight.py`:

```python
from __future__ import annotations

from typing import Any

from capture_to_notion.cache import CacheStore
from capture_to_notion.classifier import classify_content_type
from capture_to_notion.models import CaptureInput
from capture_to_notion.structure_analyzer import analyze_target_structure


def _blocked(action: str, reason: str) -> dict[str, str]:
    return {"action": action, "reason": reason}


def _confirmation_for_risk(risk_flag: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": risk_flag.get("code"),
        "level": risk_flag.get("level", "warning"),
        "message": risk_flag.get("message"),
    }


def build_capture_preflight(capture: CaptureInput, cache: CacheStore) -> dict[str, Any]:
    content_type = classify_content_type(capture)
    result: dict[str, Any] = {
        "intent_hint": capture.intent_hint,
        "input_shape_hint": capture.input_shape_hint,
        "content_type": content_type,
        "target": {},
        "structure": None,
        "safe_actions": [],
        "blocked_actions": [],
        "confirmation_needed": [],
    }

    if not capture.target_hint:
        result["target"] = {"status": "missing"}
        result["safe_actions"] = ["suggest_target"]
        result["blocked_actions"] = [_blocked("plan_directly", "target_missing")]
        return result

    alias = cache.find_alias(capture.target_hint)
    if not alias:
        result["target"] = {"status": "alias_missing", "target_hint": capture.target_hint}
        result["safe_actions"] = ["suggest_target"]
        result["blocked_actions"] = [_blocked("plan_directly", "target_not_resolved")]
        return result

    target_id = alias.get("target_id")
    structure = cache.target_structure(target_id if isinstance(target_id, str) else None)
    if not structure:
        result["target"] = {"status": "cache_missing", "target_hint": capture.target_hint, "target_id": target_id}
        result["safe_actions"] = ["scan_target"]
        result["blocked_actions"] = [_blocked("plan_directly", "target_structure_missing")]
        return result

    target = structure.get("target", {}) if isinstance(structure.get("target"), dict) else {}
    result["target"] = {
        "status": "cache_hit",
        "target_hint": capture.target_hint,
        "target_context_hint": capture.target_context_hint,
        "target_scope_hint": capture.target_scope_hint,
        "target_id": target_id,
        "page_id": alias.get("page_id") or target.get("page_id"),
        "title": target.get("title"),
    }
    analyzed_structure = analyze_target_structure(structure)
    result["structure"] = analyzed_structure
    risk_flags = analyzed_structure.get("risk_flags", [])
    if risk_flags:
        result["safe_actions"] = ["confirm_risky_target"]
        result["blocked_actions"] = [_blocked("plan_directly", "risky_target_requires_confirmation")]
        result["confirmation_needed"] = [_confirmation_for_risk(flag) for flag in risk_flags]
        return result

    result["safe_actions"] = ["ask_before_plan"]
    result["blocked_actions"] = [_blocked("apply_directly", "plan_required")]
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_preflight.py tests/test_structure_analyzer.py -q
```

Expected: PASS.

---

### Task 5: Add CLI `capture preflight`

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`
- Test: existing CLI tests; if `tests/test_cli.py` exists use it, otherwise create focused tests in `/Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_preflight.py`

- [ ] **Step 1: Locate CLI test style**

Run:

```bash
find "/Users/aaron/.claude/skills/capture-to-notion/tests" -maxdepth 1 -type f -name "test_cli*.py" -print
```

Use the existing CLI test conventions for invoking `main()` or command handlers.

- [ ] **Step 2: Write failing CLI test**

If using direct handler tests, add:

```python
import argparse
import json

from capture_to_notion.cli import cmd_capture_preflight


def test_cmd_capture_preflight_outputs_target_missing(tmp_path, monkeypatch, capsys):
    root = tmp_path / "config"
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({"raw_input": "把《可能性的艺术》存一下"}), encoding="utf-8")

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_ROOT", str(root))

    result = cmd_capture_preflight(argparse.Namespace(input=str(input_path)))

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["target"] == {"status": "missing"}
    assert "suggest_target" in output["safe_actions"]
```

If `ensure_config()` does not support `CAPTURE_TO_NOTION_CONFIG_ROOT`, follow existing test patterns for monkeypatching config.

- [ ] **Step 3: Run CLI test to verify it fails**

Run the focused CLI test with:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_cli*.py -q
```

Expected: FAIL because `cmd_capture_preflight` or parser command is missing.

- [ ] **Step 4: Implement CLI command**

In `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`, add import:

```python
from capture_to_notion.preflight import build_capture_preflight
```

Add command handler:

```python
def cmd_capture_preflight(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    capture = load_capture_input(args.input)
    print_json(build_capture_preflight(capture, cache))
    return 0
```

Add argparse subcommand under the existing `capture` command group:

```python
preflight_parser = capture_subparsers.add_parser("preflight")
preflight_parser.add_argument("--input", required=True)
preflight_parser.set_defaults(func=cmd_capture_preflight)
```

Use the exact parser variable names already present in `cli.py`.

- [ ] **Step 5: Run CLI tests to verify they pass**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_preflight.py tests/test_structure_analyzer.py tests/test_cli*.py -q
```

Expected: PASS.

---

### Task 6: Update Skill flow to use preflight first

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/SKILL.md`

- [ ] **Step 1: Replace Required Flow wording**

Update the Required Flow section so it starts with:

```markdown
## Required Flow

1. Apply Summary Preprocessing when the request requires it.
2. First infer the user's intent and input shape before planning:
   - `capture_write`: user wants to save/write content.
   - `capture_plan_only`: user wants to see how it would be stored.
   - `target_suggest_only`: user asks where it should go.
   - `enrich_only`: user asks to search/complete information without writing yet.
   - `scan_target` or `inspect_cache`: user asks to inspect local/Notion structure.
3. Parse the user's request into `input.json`. Include the existing fields plus optional hints when known:
   - `intent_hint`
   - `input_shape_hint` (`short_key`, `external_url`, `structured_text`, `long_text`, or `ambiguous_text`)
   - `target_context_hint` for parent/container context
   - `target_scope_hint` such as `inside_child_page`
   - `user_requested_action`
4. Run preflight before plan:

```bash
capture-to-notion capture preflight --input /path/to/input.json
```

5. Use preflight output to choose the next user-facing action:
   - `suggest_target`: show target suggestions/search results before enrichment or plan.
   - `scan_target`: ask for or run target scan when the user has identified the exact target.
   - `confirm_risky_target`: explain the risk flag and ask whether to continue with this target.
   - `ask_before_plan`: explain what the cached structure supports and ask before generating plan when the next step has cost or ambiguity.
   - `ask_before_parse_url`: if the Skill AI recommends URL parsing based on structure facts, ask before parsing/fetching the URL.
   - `ask_before_search`: if the Skill AI recommends web enrichment based on structure facts, ask before searching.
6. Do not parse/fetch external URLs automatically. URLs are input-shape signals; parsing is a recommended action that needs the current context to justify it.
7. Only run `capture-to-notion capture plan --input ... --output ...` after preflight indicates a usable target structure or after the user has resolved the recommended next step.
8. Present the returned plan in concise Chinese.
9. If `requires_confirmation` is true, ask the user to confirm before writing.
10. If the user explicitly confirms the plan and target, run `capture-to-notion capture apply --plan /path/to/plan.json --confirmed`.
11. Present the apply result, including warnings, asset results, and verification warnings when present.
```

Keep Safety Rules and Target Suggestion sections consistent with this flow.

- [ ] **Step 2: Verify wording does not say plan is first backend action**

Run:

```bash
grep -n "capture plan\|capture preflight\|Do not parse/fetch external URLs automatically" "/Users/aaron/.claude/skills/capture-to-notion/SKILL.md"
```

Expected: `capture preflight` appears before `capture plan`; URL auto-fetch prohibition appears.

---

### Task 7: Planner defensive regression tests

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py` only if no existing tests already cover these cases.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py` only if the tests fail.

- [ ] **Step 1: Add or confirm planner regression tests**

Ensure tests cover:

```python
def test_capture_plan_with_missing_target_structure_has_no_write_operations(...):
    ...
    assert plan.requires_confirmation is True
    assert plan.operations == []
```

```python
def test_capture_plan_with_primary_data_source_missing_has_no_write_operations(...):
    ...
    assert plan.requires_confirmation is True
    assert plan.operations == []
```

Use existing fixtures in `tests/test_planner.py` if present.

- [ ] **Step 2: Run planner regressions**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py -q
```

Expected: PASS. If FAIL, make the minimal planner fix so missing/unresolved structure never creates operations.

---

### Task 8: Hardcoding audit and final verification

**Files:**
- Review changed Python and Skill files.

- [ ] **Step 1: Search for suspicious hardcoded business branches**

Run:

```bash
grep -R "page_title ==\|field_name ==\|target_name ==\|后互联网时代\|作者\|ISBN\|签到" -n "/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion" "/Users/aaron/.claude/skills/capture-to-notion/tests" "/Users/aaron/.claude/skills/capture-to-notion/SKILL.md"
```

Expected: No hardcoded business/page branches in `capture_to_notion/*.py`. Tests and Skill docs may contain example strings; analyzer code may contain centralized default risk-policy terms only.

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_preflight.py tests/test_structure_analyzer.py tests/test_cli*.py tests/test_planner.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full tests**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest
```

Expected: PASS.

- [ ] **Step 4: Check formatting whitespace**

Run:

```bash
git -C "/Users/aaron/.claude/skills/capture-to-notion" diff --check
```

Expected: no output and exit 0.

- [ ] **Step 5: Inspect changed files**

Run:

```bash
git -C "/Users/aaron/.claude/skills/capture-to-notion" status --short
```

Expected: only intentional code, tests, Skill, and plan changes appear.

---

## Parallel/Subagent Execution Plan

### Can run first in parallel

- Agent A: Task 1 + Task 4 skeleton without analyzer integration beyond import contract.
- Agent B: Task 2 + Task 3 structure analyzer and risk policy.
- Agent E: Task 6 Skill flow update.

### Must wait for dependencies

- Agent D: Task 4 full integration after Agent A and Agent B finish.
- Planner defensive Task 7 after preflight behavior is stable.
- Hardcoding audit Task 8 after all code and docs are changed.

### Subagent prompts

#### Agent A prompt

Implement Task 1 and the initial parts of Task 4 from `docs/superpowers/plans/2026-05-14-capture-preflight-structure-analyzer.md`. Work only on `capture_to_notion/models.py`, `capture_to_notion/preflight.py`, and `tests/test_preflight.py`. Use TDD: add tests first, run them red, implement minimal code, run green. Do not modify `structure_analyzer.py`, `planner.py`, or `SKILL.md`. Do not commit.

#### Agent B prompt

Implement Task 2 and Task 3 from `docs/superpowers/plans/2026-05-14-capture-preflight-structure-analyzer.md`. Work only on `capture_to_notion/structure_analyzer.py` and `tests/test_structure_analyzer.py`. The implementation must be generic: no page-specific branches, no business-field mapping, and risk keywords must be centralized in an overridable policy. Use TDD and do not commit.

#### Agent E prompt

Implement Task 6 from `docs/superpowers/plans/2026-05-14-capture-preflight-structure-analyzer.md`. Work only on `SKILL.md`. Update the flow so Skill AI parses intent/input shape first, then calls `capture preflight`, then recommends target scan/search/URL parsing/search enrichment/user confirmation/plan. Make clear URLs are not parsed automatically. Do not commit.

#### Agent D prompt

After Agent A and Agent B finish, integrate Task 4 fully. Work only on `capture_to_notion/preflight.py` and `tests/test_preflight.py`. Ensure preflight uses `analyze_target_structure()`, outputs `safe_actions`, `blocked_actions`, and `confirmation_needed`, and never calls Notion API or parses URLs. Use TDD and do not commit.

#### Agent F prompt

After all implementation tasks finish, review the diff for hardcoded business logic, cache-first violations, and boundary violations. Check that Python preflight outputs structure facts rather than final natural-language recommendations, and that Skill owns user-facing recommendation. Report Critical/Important/Minor findings without editing files unless asked.

---

## Self-Review

- Spec coverage: Covers preflight command, generic structure analyzer, risk policy including check-in-like pages, Skill preflight-first flow, planner defensive checks, parallel/subagent execution, and hardcoding audit.
- Placeholder scan: No placeholder steps remain; every task has concrete files, commands, and expected results.
- Type consistency: The plan uses `CaptureInput` hint fields, `build_capture_preflight()`, and `analyze_target_structure()` consistently across tests, implementation, and CLI.
