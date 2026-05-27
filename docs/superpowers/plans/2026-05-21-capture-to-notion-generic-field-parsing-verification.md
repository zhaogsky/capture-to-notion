# Capture to Notion Generic Field Parsing and Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Capture to Notion so structured labeled inputs preserve intended field values, plans expose actual value previews, and post-apply verification compares actual Notion values against planned values.

**Architecture:** Keep the fix generic and schema-driven. Parsing should stop labeled values only at the next known label boundary, plan summaries should expose safe value previews for any mapped field, and verification should extract comparable values from Notion page property values using official property types rather than business-specific field names.

**Tech Stack:** Python, pytest, Notion API property objects, existing `capture_to_notion` modules (`planner.py`, `schema.py`, `verifier.py`, `cli.py`).

---

## File Structure

- Modify `capture_to_notion/planner.py`
  - Owns raw input parsing, normalized records, field mapping, and plan summary construction.
  - Add generic labeled-value boundary logic.
  - Add mapped title selection for plan summary title.
  - Add value previews to `writable_fields`.

- Modify `capture_to_notion/schema.py`
  - Owns Notion property type building and generic property helpers.
  - Add generic extraction of comparable page property values from Notion page property objects.
  - Add generic comparison for expected planned values.

- Modify `capture_to_notion/verifier.py`
  - Owns post-write verification against retrieved Notion pages.
  - Extend check specs to accept expected values and report mismatch warnings.

- Modify `capture_to_notion/cli.py`
  - Owns CLI apply/verify orchestration.
  - Include expected planned values in apply verification checks.

- Modify `tests/test_planner.py`
  - Add parser regression tests for delimiter preservation and title/value preview behavior.

- Modify `tests/test_schema.py`
  - Add property value extraction and comparison tests.

- Modify `tests/test_capture_apply.py`
  - Add verifier and apply-level regression tests proving value mismatches fail verification.

---

### Task 1: Add failing parser regression tests for labeled values

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`
- Test target: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py:60-74`

- [ ] **Step 1: Add tests for preserving punctuation inside labeled values**

Append near existing `extract_labeled_value` tests around `tests/test_planner.py:2036`:

```python
def test_extract_labeled_value_preserves_semicolon_until_next_known_label():
    raw_input = "参与人员: 张小珺；姚顺宇\n状态: 进行中"

    assert extract_labeled_value(
        raw_input,
        ["参与人员"],
        ["参与人员", "状态"],
    ) == "张小珺；姚顺宇"


def test_extract_labeled_value_preserves_rich_text_summary_until_next_known_label():
    raw_input = (
        "内容描述: 来源：https://example.com；摘要依据：页面简介。"
        "这是一段总结，包含；分号和｜竖线。\n"
        "状态: 进行中"
    )

    assert extract_labeled_value(
        raw_input,
        ["内容描述"],
        ["内容描述", "状态"],
    ) == "来源：https://example.com；摘要依据：页面简介。这是一段总结，包含；分号和｜竖线。"
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py::test_extract_labeled_value_preserves_semicolon_until_next_known_label tests/test_planner.py::test_extract_labeled_value_preserves_rich_text_summary_until_next_known_label -q
```

Expected: both tests fail because current parsing returns `张小珺` and `来源：https://example.com`.

- [ ] **Step 3: Implement generic next-label boundary parsing**

In `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py`, replace `extract_labeled_value()` with this implementation and add the helper immediately above it:

```python
def _labeled_value_boundary_pattern(known_labels: list[str]) -> str:
    known_label_pattern = "|".join(re.escape(label) for label in known_labels)
    if not known_label_pattern:
        return r"$"
    return rf"(?=(?:{METADATA_DELIMITER_PATTERN})+(?:{known_label_pattern})\s*{METADATA_COLON_PATTERN}|$)"


def extract_labeled_value(raw_input: str, labels: list[str], known_labels: list[str] | None = None) -> str | None:
    if not labels:
        return None
    label_pattern = "|".join(re.escape(label) for label in labels)
    known_label_values = known_labels if known_labels is not None else labels
    boundary_pattern = _labeled_value_boundary_pattern(known_label_values)
    match = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:{label_pattern})\s*{METADATA_COLON_PATTERN}\s*(.+?){boundary_pattern}",
        raw_input,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    value = match.group(1).strip(" \t\r\n,，。")
    return value or None
```

- [ ] **Step 4: Run focused parser tests**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py::test_extract_labeled_author_stops_before_following_label tests/test_planner.py::test_extract_labeled_author_stops_before_following_label_delimiters tests/test_planner.py::test_extract_labeled_value_preserves_semicolon_until_next_known_label tests/test_planner.py::test_extract_labeled_value_preserves_rich_text_summary_until_next_known_label -q
```

Expected: all listed tests pass. Existing tests ensure parsing still stops before actual next labels such as `出版社:`.

- [ ] **Step 5: Commit parser fix**

Only if the user has explicitly requested commits for this session, run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git add capture_to_notion/planner.py tests/test_planner.py && git commit -m "fix: preserve labeled field values until next label"
```

---

### Task 2: Add plan summary value previews and mapped title selection

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py`

- [ ] **Step 1: Add a generic target fixture test for labeled title and previews**

Append to `tests/test_planner.py` after the existing podcast tests:

```python
def seed_generic_labeled_target(config):
    write_json(
        config.aliases_file,
        {
            "aliases": {
                "访谈库": {
                    "type": "page",
                    "page_id": "page-interviews",
                    "description": "访谈记录",
                    "target_id": "interviewshelf",
                }
            }
        },
    )
    write_json(
        config.targets_dir / "interviewshelf.json",
        {
            "target": {"page_id": "page-interviews", "title": "访谈库"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-interviews",
                    "title": "Interviews",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {
                        "主题": "主题",
                        "内容描述": "内容描述",
                        "参与人员": "参与人员",
                        "状态": "状态",
                    },
                    "field_sources": {
                        "主题": "profile",
                        "内容描述": "profile",
                        "参与人员": "profile",
                        "状态": "profile",
                    },
                    "parser_profile": {
                        "labels": {
                            "主题": ["主题"],
                            "内容描述": ["内容描述"],
                            "参与人员": ["参与人员"],
                            "状态": ["状态"],
                        }
                    },
                    "schema": {
                        "主题": {"type": "title"},
                        "内容描述": {"type": "rich_text"},
                        "参与人员": {"type": "rich_text"},
                        "状态": {"type": "select", "options": [{"name": "进行中", "color": "red"}]},
                    },
                }
            },
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )


def test_plan_summary_uses_mapped_title_field_and_value_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_generic_labeled_target(config)
    summary = "来源：https://example.com；摘要依据：页面简介。这是一段完整总结。"
    capture = CaptureInput(
        raw_input=(
            "主题: 140. 对姚顺宇的4小时访谈\n"
            "参与人员: 张小珺；姚顺宇\n"
            "状态: 进行中\n"
            f"内容描述: {summary}"
        ),
        target_hint="访谈库",
        state="进行中",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.summary["title"] == "140. 对姚顺宇的4小时访谈"
    assert plan.normalized_record["内容描述"] == summary
    assert plan.normalized_record["参与人员"] == "张小珺；姚顺宇"
    assert plan.summary["writable_fields"]["内容描述"]["value_preview"] == summary
    assert plan.summary["writable_fields"]["参与人员"]["value_preview"] == "张小珺；姚顺宇"
```

- [ ] **Step 2: Run the new plan summary test and verify it fails**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py::test_plan_summary_uses_mapped_title_field_and_value_previews -q
```

Expected: fails because summary title is the raw input and `value_preview` does not exist.

- [ ] **Step 3: Add generic value preview helper**

In `capture_to_notion/planner.py`, add near `_value_status()`:

```python
def _value_preview(value: Any, max_length: int = 500) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"
```

Also add `import json` at the top of `planner.py` if it is not already imported.

- [ ] **Step 4: Add mapped title helper**

In `capture_to_notion/planner.py`, add near `build_plan_summary()`:

```python
def _summary_title(normalized_record: dict[str, Any], field_mapping: dict[str, str], schema_fields: dict[str, str], schema: dict[str, Any]) -> Any:
    for record_key, property_name in field_mapping.items():
        property_schema = schema.get(property_name)
        if isinstance(property_schema, dict) and property_schema.get("type") == "title":
            value = normalized_record.get(record_key)
            if value not in (None, "", [], {}):
                return value
    title = normalized_record.get("title")
    if title not in (None, "", [], {}):
        return title
    for record_key in schema_fields:
        value = normalized_record.get(record_key)
        if value not in (None, "", [], {}):
            return value
    return None
```

- [ ] **Step 5: Update `build_plan_summary()` signature and body**

Change `build_plan_summary()` in `planner.py` so it accepts `schema: dict[str, Any] | None = None`, uses `_summary_title()`, and adds `value_preview`.

Replace the function header with:

```python
def build_plan_summary(
    *,
    content_type: str,
    target_page: str | None,
    target_data_source: str | None,
    normalized_record: dict[str, Any],
    field_mapping: dict[str, str],
    schema_fields: dict[str, str],
    asset_operations: list[AssetOperation],
    requires_confirmation: bool,
    confirmation_reason: str | None,
    warnings: list[str],
    summary_key_fields: list[str] | None = None,
    relation_completion_summaries: list[dict[str, Any]] | None = None,
    write_targets: list[dict[str, Any]] | None = None,
    enrichment_requirements: list[dict[str, Any]] | None = None,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Inside the `writable_fields` loop, replace the assignment with:

```python
        writable_fields[key] = {
            "target_field": field_mapping.get(key) or target_field,
            "value_status": value_status,
            "write_status": write_status,
            "value_preview": _value_preview(normalized_record.get(key)),
        }
```

Replace summary title assignment:

```python
        "title": normalized_record.get("title"),
```

with:

```python
        "title": _summary_title(normalized_record, field_mapping, schema_fields, schema or {}),
```

Update the main call at `build_capture_plan()` to pass schema:

```python
            schema=data_source.get("schema", {}),
```

Leave unresolved plans without schema; the default handles it.

- [ ] **Step 6: Run the focused plan summary test**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py::test_plan_summary_uses_mapped_title_field_and_value_previews -q
```

Expected: PASS.

- [ ] **Step 7: Run planner tests covering summaries and parser labels**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit plan summary fix**

Only if commits are explicitly authorized:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git add capture_to_notion/planner.py tests/test_planner.py && git commit -m "fix: show mapped field values in capture plans"
```

---

### Task 3: Add generic Notion property value extraction and comparison

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/schema.py`

- [ ] **Step 1: Add failing schema tests**

Append to `tests/test_schema.py`:

```python
def test_page_property_plain_value_extracts_common_writable_types():
    from capture_to_notion.schema import page_property_plain_value

    assert page_property_plain_value({"type": "title", "title": [{"plain_text": "标题"}]}) == "标题"
    assert page_property_plain_value({"type": "rich_text", "rich_text": [{"plain_text": "摘要"}]}) == "摘要"
    assert page_property_plain_value({"type": "select", "select": {"name": "进行中"}}) == "进行中"
    assert page_property_plain_value({"type": "status", "status": {"name": "已完成"}}) == "已完成"
    assert page_property_plain_value({"type": "number", "number": 231}) == 231
    assert page_property_plain_value({"type": "url", "url": "https://example.com"}) == "https://example.com"
    assert page_property_plain_value({"type": "date", "date": {"start": "2026-05-21"}}) == "2026-05-21"
    assert page_property_plain_value({"type": "checkbox", "checkbox": True}) is True
    assert page_property_plain_value({"type": "multi_select", "multi_select": [{"name": "AI"}, {"name": "访谈"}]}) == ["AI", "访谈"]
    assert page_property_plain_value({"type": "relation", "relation": [{"id": "page-1"}]}) == ["page-1"]
    assert page_property_plain_value({"type": "people", "people": [{"id": "user-1"}]}) == ["user-1"]


def test_property_value_matches_expected_text_and_lists():
    from capture_to_notion.schema import property_value_matches_expected

    assert property_value_matches_expected({"type": "rich_text", "rich_text": [{"plain_text": "张小珺、姚顺宇"}]}, "张小珺、姚顺宇")
    assert not property_value_matches_expected({"type": "rich_text", "rich_text": [{"plain_text": "张小珺"}]}, "张小珺、姚顺宇")
    assert property_value_matches_expected({"type": "multi_select", "multi_select": [{"name": "AI"}, {"name": "访谈"}]}, ["AI", "访谈"])
    assert property_value_matches_expected({"type": "number", "number": 231}, "231")
```

- [ ] **Step 2: Run new schema tests and verify they fail**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_schema.py::test_page_property_plain_value_extracts_common_writable_types tests/test_schema.py::test_property_value_matches_expected_text_and_lists -q
```

Expected: FAIL because helpers do not exist.

- [ ] **Step 3: Implement property extraction helpers**

In `capture_to_notion/schema.py`, add after `property_has_value()`:

```python
def _plain_text_fragments(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(
        str(item.get("plain_text") or item.get("text", {}).get("content") or "")
        for item in value
        if isinstance(item, dict)
    )


def page_property_plain_value(property_data: Any) -> Any:
    if not isinstance(property_data, dict):
        return None
    property_type = property_data.get("type")
    if property_type == "title":
        return _plain_text_fragments(property_data.get("title"))
    if property_type == "rich_text":
        return _plain_text_fragments(property_data.get("rich_text"))
    if property_type in {"select", "status"}:
        value = property_data.get(property_type)
        return value.get("name") if isinstance(value, dict) else None
    if property_type == "multi_select":
        values = property_data.get("multi_select")
        if not isinstance(values, list):
            return []
        return [item.get("name") for item in values if isinstance(item, dict) and item.get("name")]
    if property_type in {"relation", "people"}:
        values = property_data.get(property_type)
        if not isinstance(values, list):
            return []
        return [item.get("id") for item in values if isinstance(item, dict) and item.get("id")]
    if property_type == "files":
        return file_urls_from_property(property_data)
    if property_type == "date":
        value = property_data.get("date")
        return value.get("start") if isinstance(value, dict) else None
    if property_type in {"number", "url", "email", "phone_number", "checkbox"}:
        return property_data.get(property_type)
    return property_data.get(property_type)


def _expected_plain_value(expected: Any) -> Any:
    if isinstance(expected, tuple):
        return [_expected_plain_value(item) for item in expected]
    if isinstance(expected, list):
        return [_expected_plain_value(item) for item in expected]
    if isinstance(expected, dict):
        return expected
    return expected


def property_value_matches_expected(property_data: Any, expected: Any) -> bool:
    actual = page_property_plain_value(property_data)
    expected_value = _expected_plain_value(expected)
    if isinstance(actual, list) or isinstance(expected_value, list):
        return [str(item) for item in (actual or [])] == [str(item) for item in (expected_value or [])]
    return str(actual) == str(expected_value)
```

- [ ] **Step 4: Run schema tests**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_schema.py::test_page_property_plain_value_extracts_common_writable_types tests/test_schema.py::test_property_value_matches_expected_text_and_lists -q
```

Expected: PASS.

- [ ] **Step 5: Commit schema helper fix**

Only if commits are explicitly authorized:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git add capture_to_notion/schema.py tests/test_schema.py && git commit -m "feat: compare Notion page property values"
```

---

### Task 4: Make verifier fail on mismatched actual values

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/verifier.py`

- [ ] **Step 1: Add verifier mismatch test**

Append near existing verifier tests around `tests/test_capture_apply.py:1494`:

```python
def test_verify_capture_page_reports_mismatched_expected_value() -> None:
    fake_adapter = FakeAdapter(
        {
            "id": "page-episode-1",
            "object": "page",
            "properties": {
                "内容描述": {"type": "rich_text", "rich_text": [{"plain_text": "来源 https://example.com"}]},
                "参与人员": {"type": "rich_text", "rich_text": [{"plain_text": "张小珺"}]},
            },
        }
    )

    result = verify_capture_page(
        "page-episode-1",
        fake_adapter,
        field_mapping={"内容描述": "内容描述", "参与人员": "参与人员"},
        schema={
            "内容描述": {"name": "内容描述", "type": "rich_text"},
            "参与人员": {"name": "参与人员", "type": "rich_text"},
        },
        checks={
            "内容描述": {"property_type": "rich_text", "expected_value": "完整摘要"},
            "参与人员": {"property_type": "rich_text", "expected_value": "张小珺、姚顺宇"},
        },
        include_page_cover=False,
    )

    assert result["verified"] is False
    assert result["checks"]["内容描述"] == {
        "status": "mismatch",
        "property": "内容描述",
        "actual_value": "来源 https://example.com",
        "expected_value": "完整摘要",
    }
    assert result["checks"]["参与人员"] == {
        "status": "mismatch",
        "property": "参与人员",
        "actual_value": "张小珺",
        "expected_value": "张小珺、姚顺宇",
    }
    assert result["warnings"] == ["mismatch:内容描述", "mismatch:参与人员"]
```

- [ ] **Step 2: Run mismatch test and verify it fails**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_capture_apply.py::test_verify_capture_page_reports_mismatched_expected_value -q
```

Expected: FAIL because verifier treats non-empty properties as present.

- [ ] **Step 3: Update verifier imports**

In `capture_to_notion/verifier.py`, replace:

```python
from capture_to_notion.schema import cover_url_from_page, file_urls_from_property, property_has_value
```

with:

```python
from capture_to_notion.schema import (
    cover_url_from_page,
    file_urls_from_property,
    page_property_plain_value,
    property_has_value,
    property_value_matches_expected,
)
```

- [ ] **Step 4: Add mismatch warning support**

In `_warning_for_check()` in `verifier.py`, add mismatch handling before the inaccessible branch:

```python
    if status == "mismatch":
        return f"mismatch:{name}"
```

- [ ] **Step 5: Compare expected values in `_property_check()`**

In `_property_check()` after the files URL block and before `if property_has_value(property_data):`, add:

```python
    if "expected_value" in check_spec:
        expected_value = check_spec.get("expected_value")
        if property_value_matches_expected(property_data, expected_value):
            return {"status": "present", "property": property_name}
        return {
            "status": "mismatch",
            "property": property_name,
            "actual_value": page_property_plain_value(property_data),
            "expected_value": expected_value,
        }
```

- [ ] **Step 6: Run verifier mismatch test**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_capture_apply.py::test_verify_capture_page_reports_mismatched_expected_value -q
```

Expected: PASS.

- [ ] **Step 7: Run existing verifier tests**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_capture_apply.py::test_verify_capture_page_accepts_mapping_driven_arbitrary_property_names tests/test_capture_apply.py::test_verify_capture_page_without_mapping_only_checks_page_and_cover tests/test_capture_apply.py::test_verify_capture_page_requires_mapped_isbn_and_page_count_values tests/test_capture_apply.py::test_verify_capture_page_requires_mapped_author_relation_value -q
```

Expected: PASS.

- [ ] **Step 8: Commit verifier mismatch fix**

Only if commits are explicitly authorized:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git add capture_to_notion/verifier.py tests/test_capture_apply.py && git commit -m "fix: verify Notion field values after capture"
```

---

### Task 5: Wire planned values into apply verification

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`

- [ ] **Step 1: Add apply-level regression test for mismatched values**

Append near `test_capture_apply_includes_verification_summary_for_created_page` in `tests/test_capture_apply.py`:

```python
def test_capture_apply_verification_fails_when_actual_field_value_differs_from_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheStore(config).write_json(
        config.targets_dir / "episodes.json",
        {
            "target": {"page_id": "page-podcasts", "title": "访谈库"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "title": "Episodes",
                    "schema": {
                        "内容描述": {"name": "内容描述", "type": "rich_text"},
                        "参与人员": {"name": "参与人员", "type": "rich_text"},
                    },
                },
            },
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "content_type": "podcast_episode",
            "target": {
                "page_title": "访谈库",
                "page_id": "page-podcasts",
                "data_source_id": "ds-episodes",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"内容描述": "完整摘要", "参与人员": "张小珺、姚顺宇"},
            "field_mapping": {"内容描述": "内容描述", "参与人员": "参与人员"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-episodes"}],
        },
    )
    fake_adapter = FakeAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "properties": {
                    "内容描述": {"type": "rich_text", "rich_text": [{"plain_text": "来源 https://example.com"}]},
                    "参与人员": {"type": "rich_text", "rich_text": [{"plain_text": "张小珺"}]},
                },
            },
        }
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verification"]["verified"] is False
    assert result["verification"]["pages"][0]["checks"]["内容描述"]["status"] == "mismatch"
    assert result["verification"]["pages"][0]["checks"]["参与人员"]["status"] == "mismatch"
    assert "mismatch:内容描述" in result["verification"]["warnings"]
    assert "mismatch:参与人员" in result["verification"]["warnings"]
```

- [ ] **Step 2: Run apply-level regression test and verify it fails**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_capture_apply.py::test_capture_apply_verification_fails_when_actual_field_value_differs_from_plan -q
```

Expected: FAIL because `_verification_checks_for_record()` does not include expected values.

- [ ] **Step 3: Add expected values to CLI verification checks**

In `capture_to_notion/cli.py`, update `_verification_checks_for_record()` so each check includes the planned value.

Replace:

```python
        checks[record_key] = {"property_type": property_type}
```

with:

```python
        checks[record_key] = {"property_type": property_type, "expected_value": record.get(record_key)}
```

Keep the existing `files` URL handling unchanged:

```python
        if property_type == "files":
            checks[record_key]["check_urls"] = True
```

- [ ] **Step 4: Run apply-level regression test**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_capture_apply.py::test_capture_apply_verification_fails_when_actual_field_value_differs_from_plan -q
```

Expected: PASS.

- [ ] **Step 5: Run apply verification tests**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_capture_apply.py::test_capture_apply_includes_verification_summary_for_created_page tests/test_capture_apply.py::test_capture_apply_verification_fails_when_actual_field_value_differs_from_plan tests/test_capture_apply.py::test_capture_apply_verification_reports_inaccessible_mapped_file_url -q
```

Expected: PASS.

- [ ] **Step 6: Commit CLI verification wiring**

Only if commits are explicitly authorized:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git add capture_to_notion/cli.py tests/test_capture_apply.py && git commit -m "fix: compare planned values during apply verification"
```

---

### Task 6: End-to-end local regression for the original failure shape

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py`

- [ ] **Step 1: Add a planner regression matching the original labeled input shape**

Append to `tests/test_planner.py`:

```python
def test_original_podcast_summary_shape_preserves_description_and_people(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_generic_labeled_target(config)
    description = (
        "来源：https://www.xiaoyuzhoufm.com/episode/6a00aa051b7bd50295dfe41d；"
        "摘要依据：未获取到完整转录稿，基于页面简介。"
        "核心判断包括预训练仍有延展空间，Coding 是高势能场景。"
    )
    capture = CaptureInput(
        raw_input=(
            "主题: 140. 对姚顺宇的4小时访谈：请允许我小疯一下！\n"
            "参与人员: 张小珺；姚顺宇\n"
            "状态: 进行中\n"
            f"内容描述: {description}"
        ),
        target_hint="访谈库",
        state="进行中",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["内容描述"] == description
    assert plan.normalized_record["参与人员"] == "张小珺；姚顺宇"
    assert plan.summary["writable_fields"]["内容描述"]["value_preview"] == description
    assert plan.summary["writable_fields"]["参与人员"]["value_preview"] == "张小珺；姚顺宇"
```

- [ ] **Step 2: Run original-shape planner regression**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py::test_original_podcast_summary_shape_preserves_description_and_people -q
```

Expected: PASS after Tasks 1-2.

- [ ] **Step 3: Run focused full regression set**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest tests/test_planner.py tests/test_schema.py tests/test_capture_apply.py -q
```

Expected: PASS.

- [ ] **Step 4: Run package-level tests if focused set passes**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && pytest -q
```

Expected: PASS. If unrelated failures appear, record the failing test names and error output before deciding whether they are in scope.

- [ ] **Step 5: Inspect git diff for business hardcoding**

Run:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git diff -- capture_to_notion tests
```

Expected: implementation changes are generic and do not special-case `张小珺`, `内容描述`, `参与人员`, `小宇宙`, or a specific Notion page ID outside test fixtures.

- [ ] **Step 6: Commit final regression test**

Only if commits are explicitly authorized:

```bash
cd /Users/aaron/.claude/skills/capture-to-notion && git add tests/test_planner.py && git commit -m "test: cover podcast summary labeled input regression"
```

---

## Self-Review

- Spec coverage: This plan covers the four identified generic issues: parser truncation, mapped title/preview visibility, generic property value extraction, and apply verification value comparison.
- Placeholder scan: No task uses TBD/TODO/fill-in placeholders. Each code-changing step includes concrete code.
- Type consistency: `expected_value` is introduced in CLI check specs, consumed by verifier, and compared through schema helpers. The same record keys and field mappings flow from plan to verification.
- Scope check: The plan does not modify Notion schema, does not introduce business-specific aliases in runtime code, and does not add special handling for the current podcast target.
