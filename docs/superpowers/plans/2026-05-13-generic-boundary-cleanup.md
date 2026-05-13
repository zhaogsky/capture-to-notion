# Capture to Notion Generic Boundary Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining Capture to Notion cleanup so planner, scanner, schema, cache diagnostics, and verifier boundaries are driven by target cache/profile/explicit mapping plus Notion official property types, not hardcoded business-field assumptions.

**Architecture:** Keep parser-profile semantics in `capture_to_notion/planner.py`, generic Notion property handling in `capture_to_notion/schema.py`, target scan/cache metadata in `capture_to_notion/scanner.py` and `capture_to_notion/cache.py`, and user-facing stale-cache guidance in `capture_to_notion/diagnostics.py` / CLI output. Each task is independently testable and should preserve current user safety rules: no Notion MCP fallback, no silent writes, and no property-name business inference.

**Tech Stack:** Python 3.12, pytest, uv, local JSON cache fixtures under `CAPTURE_TO_NOTION_CONFIG_DIR`.

---

## File Structure

- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py`
  - Remove remaining planner business branches for asset trust and legacy title alias.
  - Add parser-profile helpers for asset trust and warning policy consumption.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/schema.py`
  - Make confirmation warning blocking policy accept explicit non-blocking prefixes instead of content type.
  - Keep Notion property type normalization as the only schema-level inference boundary.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/scanner.py`
  - Ensure scan output only derives generic mappings from Notion official property types and explicit parser profile fields.
  - Pass warning-policy inputs explicitly instead of content type branching.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py`
  - Surface stale target cache warnings when cached data sources have `fields` but no `field_sources`.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cache.py`
  - Add a read-only target-cache inspection helper if diagnostics needs structured stale-cache details.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`
  - Display doctor stale-cache warnings without exposing secrets or touching Notion.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`
  - Planner profile-driven asset trust, warning policy, alias removal.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py`
  - Warning policy and official property type registry checks.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py`
  - Scanner property-type boundary and warning policy tests.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`
  - Doctor stale-cache warning regression.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/README.md`
  - Document parser profile asset trust, warning policy, and stale-cache guidance.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/README.zh-CN.md`
  - Chinese version of the same behavior.
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/SKILL.md`
  - Update safety workflow language for profile-driven asset trust and stale-cache warnings.

---

## Task 1: Planner asset trust should be profile/cache driven

**Purpose:** Remove the remaining `_filtered_asset_mapping()` `content_type == "book"` / `cover` branch and replace it with parser-profile driven asset trust fields.

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py:138-146`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py:302-310`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py:574-578`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`
- Docs: `/Users/aaron/.claude/skills/capture-to-notion/README.md`
- Docs: `/Users/aaron/.claude/skills/capture-to-notion/README.zh-CN.md`

- [ ] **Step 1: Write failing default profile test**

Add to `tests/test_planner.py` inside `test_default_book_parser_profile_supplies_required_fields_without_business_labels()`:

```python
assert profile["asset_trust_required_fields"] == ["cover"]
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py::test_default_book_parser_profile_supplies_required_fields_without_business_labels -q
```

Expected: FAIL with `KeyError: 'asset_trust_required_fields'`.

- [ ] **Step 2: Write failing non-book asset trust test**

Add this test near the existing podcast planner tests in `tests/test_planner.py`:

```python
def test_podcast_capture_plan_does_not_attach_untrusted_profile_required_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["asset_trust_required_fields"] = ["cover"]
    target["parser_profile"]["podcast_episode"]["trusted_field_sources"] = ["explicit", "profile"]
    target["data_sources"]["episodes"]["schema"] = {"封面": {"type": "files"}}
    target["data_sources"]["episodes"]["field_sources"] = {"cover": "type_fallback"}
    cache.write_json(config.targets_dir / "podcastshelf.json", target)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:cover:type_fallback" in plan.warnings
    assert "cover" not in plan.field_mapping
    assert plan.asset_operations == []
    assert plan.summary["asset_actions"] == []
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py::test_podcast_capture_plan_does_not_attach_untrusted_profile_required_asset -q
```

Expected: FAIL because podcast cover remains attachable or no untrusted warning is emitted.

- [ ] **Step 3: Implement parser-profile asset trust helper**

In `planner.py`, extend `_default_parser_profile("book")`:

```python
"asset_trust_required_fields": ["cover"],
```

Add helper below `_trusted_field_sources()`:

```python
def _asset_trust_required_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("asset_trust_required_fields"))
```

Change `_trusted_mapping_fields()` and `_untrusted_mapping_warnings()` call sites so the checked keys include schema-required fields plus asset-trust-required fields:

```python
asset_trust_required_fields = _asset_trust_required_fields(parser_profile)
trusted_mapping_required_fields = list(dict.fromkeys(required_schema_fields + asset_trust_required_fields))
trusted_fields = _trusted_mapping_fields(fields, field_sources, trusted_mapping_required_fields, trusted_field_sources)
untrusted_mapping_warnings = _untrusted_mapping_warnings(
    fields,
    field_sources,
    trusted_mapping_required_fields,
    trusted_field_sources,
)
```

Replace `_filtered_asset_mapping()` with a generic version:

```python
def _filtered_asset_mapping(
    asset_mapping: dict[str, Any],
    trusted_fields: dict[str, str],
    asset_trust_required_fields: list[str],
) -> dict[str, Any]:
    filtered_asset_mapping = dict(asset_mapping)
    for record_key in asset_trust_required_fields:
        if record_key not in trusted_fields:
            filtered_asset_mapping.pop(record_key, None)
    return filtered_asset_mapping
```

Update the call in `build_capture_plan()`:

```python
asset_mapping = _filtered_asset_mapping(
    structure.get("asset_mapping") or {},
    trusted_fields,
    asset_trust_required_fields,
)
```

- [ ] **Step 4: Verify Task 1 tests pass**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py::test_default_book_parser_profile_supplies_required_fields_without_business_labels tests/test_planner.py::test_podcast_capture_plan_does_not_attach_untrusted_profile_required_asset -q
```

Expected: 2 passed.

- [ ] **Step 5: Run planner regressions**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py -q
```

Expected: all planner tests pass.

- [ ] **Step 6: Update docs**

In `README.md` parser profile section, add `asset_trust_required_fields` to the profile field explanation and JSON example:

```json
"asset_trust_required_fields": ["cover"]
```

In `README.zh-CN.md`, add the matching Chinese sentence and JSON key.

---

## Task 2: Schema warning blocking policy should be explicit, not content-type special-cased

**Purpose:** Remove `content_type == "book"` from `confirmation_blocking_warnings()` and make non-blocking warning prefixes profile/cache driven.

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/schema.py:96-104`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py:542-548`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/scanner.py:236-244`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py`

- [ ] **Step 1: Write failing schema policy tests**

Add to `tests/test_schema.py`:

```python
def test_confirmation_blocking_warnings_uses_explicit_non_blocking_prefixes():
    warnings = [
        "ambiguous_field_mapping:page_count:Page Count,Pages",
        "ambiguous_field_mapping:author:Author,Creator",
    ]

    assert confirmation_blocking_warnings(
        warnings,
        non_blocking_prefixes=["ambiguous_field_mapping:page_count:"],
    ) == ["ambiguous_field_mapping:author:Author,Creator"]


def test_confirmation_blocking_warnings_blocks_everything_without_policy():
    warnings = ["ambiguous_field_mapping:page_count:Page Count,Pages"]

    assert confirmation_blocking_warnings(warnings) == warnings
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_schema.py::test_confirmation_blocking_warnings_uses_explicit_non_blocking_prefixes tests/test_schema.py::test_confirmation_blocking_warnings_blocks_everything_without_policy -q
```

Expected: FAIL because `confirmation_blocking_warnings()` does not accept `non_blocking_prefixes`.

- [ ] **Step 2: Implement explicit warning policy function signature**

In `schema.py`, replace the current function with:

```python
def confirmation_blocking_warnings(
    warnings: list[str] | None,
    non_blocking_prefixes: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    prefixes = tuple(non_blocking_prefixes or ())
    return [
        warning
        for warning in (warnings or [])
        if not warning.startswith(prefixes)
    ]
```

Remove `NON_BLOCKING_CONFIRMATION_WARNING_PREFIXES` if no callers remain.

- [ ] **Step 3: Add planner parser-profile helper**

In `planner.py`, add:

```python
def _non_blocking_warning_prefixes(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("non_blocking_warning_prefixes"))
```

In `build_capture_plan()`, compute:

```python
non_blocking_warning_prefixes = _non_blocking_warning_prefixes(parser_profile)
```

Update calls:

```python
blocking_mapping_warnings = confirmation_blocking_warnings(warnings, non_blocking_warning_prefixes)
blocking_structure_mapping_warnings = confirmation_blocking_warnings(
    all_mapping_warnings,
    non_blocking_warning_prefixes,
)
```

- [ ] **Step 4: Update scanner call site**

In `scanner.py`, when scan logic calls `confirmation_blocking_warnings(...)`, pass an explicit policy from parser profile if that profile is already available in the function scope. If the current scanner function only has `content_type`, pass `[]` so all warnings block until a later scan-profile refactor supplies policy:

```python
blocking_warnings = confirmation_blocking_warnings(mapping_warnings, [])
```

- [ ] **Step 5: Verify schema and existing warning tests**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_schema.py tests/test_planner.py tests/test_scanner.py -q
```

Expected: all selected tests pass after updating affected expectations to profile-driven policy.

---

## Task 3: Scanner should keep Notion official property type boundary explicit

**Purpose:** Verify and tighten scanner behavior so generic scan logic derives only from Notion official property types and explicit parser profile mappings, never business field names.

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/scanner.py:39-88`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/schema.py:55-73`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py`

- [ ] **Step 1: Write scanner no-business-name inference test**

Add to `tests/test_scanner.py`:

```python
def test_scanner_does_not_map_business_fields_from_property_names_without_profile():
    schema = {
        "作者": {"type": "rich_text"},
        "ISBN": {"type": "rich_text"},
        "页数": {"type": "number"},
        "封面": {"type": "files"},
    }

    normalized = normalize_database_schema(schema)

    assert normalized["作者"]["type"] == "rich_text"
    assert normalized["ISBN"]["type"] == "rich_text"
    assert normalized["页数"]["type"] == "number"
    assert normalized["封面"]["type"] == "files"
```

If `normalize_database_schema` is not imported in the file, add:

```python
from capture_to_notion.schema import normalize_database_schema
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_scanner.py::test_scanner_does_not_map_business_fields_from_property_names_without_profile -q
```

Expected: PASS if boundary already holds. If it fails, remove the business-name mapping path that caused the failure.

- [ ] **Step 2: Write official type whitelist regression**

Add to `tests/test_schema.py`:

```python
def test_normalize_database_schema_preserves_supported_notion_property_types():
    raw_schema = {
        "Title": {"type": "title"},
        "Text": {"type": "rich_text"},
        "Number": {"type": "number"},
        "Select": {"type": "select"},
        "Status": {"type": "status"},
        "Date": {"type": "date"},
        "Url": {"type": "url"},
        "Files": {"type": "files"},
        "Relation": {"type": "relation", "relation": {"database_id": "db-related"}},
        "Checkbox": {"type": "checkbox"},
        "Email": {"type": "email"},
        "Phone": {"type": "phone_number"},
    }

    normalized = normalize_database_schema(raw_schema)

    assert {name: value["type"] for name, value in normalized.items()} == {
        "Title": "title",
        "Text": "rich_text",
        "Number": "number",
        "Select": "select",
        "Status": "status",
        "Date": "date",
        "Url": "url",
        "Files": "files",
        "Relation": "relation",
        "Checkbox": "checkbox",
        "Email": "email",
        "Phone": "phone_number",
    }
    assert normalized["Relation"]["target_database_id"] == "db-related"
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_schema.py::test_normalize_database_schema_preserves_supported_notion_property_types -q
```

Expected: PASS if currently supported. If unsupported official types are dropped, add them to the schema type registry in `schema.py` without adding business semantics.

- [ ] **Step 3: Document scanner boundary in docs**

In `README.md`, add one sentence to the parser profile section:

```markdown
Target scanning records Notion property names and official property types; it does not infer business record keys from property names unless a parser profile or explicit mapping supplies that key.
```

Add the matching Chinese sentence in `README.zh-CN.md`.

---

## Task 4: Doctor should warn about stale target caches missing field_sources

**Purpose:** Make the compatibility behavior from the trusted-source migration visible: old target caches without `field_sources` still work, but users should be prompted to rescan.

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py:73-104`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cache.py:112-127`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py:309-310`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`
- Docs: `/Users/aaron/.claude/skills/capture-to-notion/README.md`
- Docs: `/Users/aaron/.claude/skills/capture-to-notion/README.zh-CN.md`

- [ ] **Step 1: Write failing doctor stale-cache test**

Add to `tests/test_p0_foundation.py`:

```python
def test_doctor_warns_about_target_cache_missing_field_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "title": "Books",
                    "fields": {"title": "名称", "author": "作者"},
                    "schema": {"名称": {"type": "title"}, "作者": {"type": "rich_text"}},
                }
            },
        },
    )

    report = doctor_report(config)

    stale_check = next(check for check in report["checks"] if check["name"] == "target_cache_field_sources")
    assert stale_check["status"] == "warning"
    assert stale_check["details"] == {
        "targets_missing_field_sources": ["bookshelf"],
        "message": "Rescan these targets to record mapping field_sources.",
    }
```

Ensure imports include:

```python
from capture_to_notion.cache import CacheStore
from capture_to_notion.diagnostics import doctor_report
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_p0_foundation.py::test_doctor_warns_about_target_cache_missing_field_sources -q
```

Expected: FAIL because doctor does not yet include `target_cache_field_sources`.

- [ ] **Step 2: Implement read-only stale cache detection**

In `diagnostics.py`, add a helper:

```python
def _targets_missing_field_sources(config: AppConfig) -> list[str]:
    missing: list[str] = []
    for target_file in sorted(config.targets_dir.glob("*.json")):
        data = json.loads(target_file.read_text(encoding="utf-8"))
        data_sources = data.get("data_sources", {})
        if not isinstance(data_sources, dict):
            continue
        for source in data_sources.values():
            if not isinstance(source, dict):
                continue
            fields = source.get("fields")
            field_sources = source.get("field_sources")
            if isinstance(fields, dict) and fields and not isinstance(field_sources, dict):
                missing.append(target_file.stem)
                break
    return missing
```

If `json` is not imported, add `import json`.

In `doctor_report(config)`, append:

```python
missing_field_sources = _targets_missing_field_sources(config)
checks.append(
    {
        "name": "target_cache_field_sources",
        "status": "warning" if missing_field_sources else "ok",
        "details": {
            "targets_missing_field_sources": missing_field_sources,
            "message": "Rescan these targets to record mapping field_sources." if missing_field_sources else "All cached targets with fields include field_sources.",
        },
    }
)
```

- [ ] **Step 3: Verify doctor tests**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_p0_foundation.py -q
```

Expected: all P0 tests pass.

- [ ] **Step 4: Update docs**

In README doctor section, add:

```markdown
`doctor` also warns when cached targets predate `field_sources`; rescan those targets before relying on trusted mapping gates.
```

Add matching Chinese text in `README.zh-CN.md`.

---

## Task 5: Remove `extract_book_title` compatibility alias

**Purpose:** Finish title helper cleanup now that internal call sites use `extract_title()`.

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/planner.py:88`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py`

- [ ] **Step 1: Confirm no usage remains**

Run:

```bash
grep -R "extract_book_title" "/Users/aaron/.claude/skills/capture-to-notion" --exclude-dir=.git --exclude-dir=.venv
```

Expected: only `planner.py` alias appears. If docs/tests also mention it, update them to `extract_title()` in this task.

- [ ] **Step 2: Write failing alias removal test**

Add to `tests/test_planner.py` near `test_extract_title_uses_generic_parser_profile_patterns()`:

```python
def test_planner_exposes_generic_title_helper_only():
    assert hasattr(planner_module, "extract_title")
    assert not hasattr(planner_module, "extract_book_title")
```

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py::test_planner_exposes_generic_title_helper_only -q
```

Expected: FAIL because the alias still exists.

- [ ] **Step 3: Remove alias**

In `planner.py`, delete:

```python
extract_book_title = extract_title
```

- [ ] **Step 4: Verify title helper tests**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py::test_extract_title_uses_generic_parser_profile_patterns tests/test_planner.py::test_unresolved_plan_uses_generic_title_helper tests/test_planner.py::test_planner_exposes_generic_title_helper_only -q
```

Expected: 3 passed.

---

## Task 6: Official Notion property type alignment review and regression net

**Purpose:** Add a regression net proving writer/verifier/schema use official Notion property value types rather than business semantics.

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/schema.py:339-340`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/verifier.py:79`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py:228-233`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_writer.py`
- Test: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py`

- [ ] **Step 1: Write property value builder registry test**

Add to `tests/test_schema.py`:

```python
def test_property_value_builders_cover_supported_write_types_without_business_keys():
    schema = {
        "Name": {"type": "title"},
        "Notes": {"type": "rich_text"},
        "Pages": {"type": "number"},
        "Status": {"type": "status"},
        "Category": {"type": "select"},
        "Published": {"type": "date"},
        "Link": {"type": "url"},
        "Files": {"type": "files"},
        "Author": {"type": "relation"},
    }
    record = {
        "title": "可能性的艺术",
        "notes": "公共讨论",
        "pages": 400,
        "status": "想读",
        "category": "政治",
        "published": "2022-01-01",
        "link": "https://example.com/book",
        "files": "https://example.com/cover.jpg",
        "author": ["page-author"],
    }
    mapping = {
        "title": "Name",
        "notes": "Notes",
        "pages": "Pages",
        "status": "Status",
        "category": "Category",
        "published": "Published",
        "link": "Link",
        "files": "Files",
        "author": "Author",
    }

    properties = build_page_properties(record, mapping, schema)

    assert set(properties) == set(schema)
    assert properties["Name"]["title"][0]["text"]["content"] == "可能性的艺术"
    assert properties["Notes"]["rich_text"][0]["text"]["content"] == "公共讨论"
    assert properties["Pages"] == {"number": 400}
    assert properties["Status"] == {"status": {"name": "想读"}}
    assert properties["Category"] == {"select": {"name": "政治"}}
    assert properties["Published"] == {"date": {"start": "2022-01-01"}}
    assert properties["Link"] == {"url": "https://example.com/book"}
    assert properties["Files"]["files"][0]["external"]["url"] == "https://example.com/cover.jpg"
    assert properties["Author"] == {"relation": [{"id": "page-author"}]}
```

Use the actual builder function name from `schema.py`; if it is not `build_page_properties`, import the current function already used in nearby schema tests.

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_schema.py::test_property_value_builders_cover_supported_write_types_without_business_keys -q
```

Expected: PASS if registry is already aligned; otherwise implement missing official type builders in `schema.py` without business-field logic.

- [ ] **Step 2: Write verifier type-only behavior test**

Add to `tests/test_capture_apply.py` or `tests/test_writer.py`, whichever already has verification fixtures:

```python
def test_apply_verification_uses_plan_mapping_property_types_not_business_names(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "custom.json",
        {
            "target": {"page_id": "page-custom", "title": "Custom"},
            "data_sources": {
                "items": {
                    "data_source_id": "ds-custom",
                    "title": "Items",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "Primary", "page_count": "Metric"},
                    "field_sources": {"title": "explicit", "page_count": "explicit"},
                    "schema": {"Primary": {"type": "title"}, "Metric": {"type": "number"}},
                }
            },
        },
    )

    # The assertion for this test should inspect the produced verification checks and confirm
    # the number check is based on field mapping `page_count -> Metric` and property type `number`,
    # not on the property name containing pages/page_count.
```

Before adding this exact test, read the existing verification fixture in `tests/test_capture_apply.py` and use its fake Notion adapter pattern. The final assertion must be concrete, for example:

```python
assert verification["checks"]["Metric"]["property_type"] == "number"
```

Run the specific test and confirm it fails or passes for the intended reason. If it already passes, keep it as a regression net.

- [ ] **Step 3: Run schema/writer/apply regressions**

Run:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_schema.py tests/test_writer.py tests/test_capture_apply.py -q
```

Expected: all selected tests pass.

---

## Final Verification

After all six tasks pass individually, run these commands in order:

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest tests/test_planner.py tests/test_schema.py tests/test_scanner.py tests/test_p0_foundation.py tests/test_writer.py tests/test_capture_apply.py -q
```

Expected: selected regression suite passes.

```bash
uv --directory "/Users/aaron/.claude/skills/capture-to-notion" run --with pytest python -m pytest
```

Expected: full suite passes.

```bash
git -C "/Users/aaron/.claude/skills/capture-to-notion" diff --check
```

Expected: no output.

```bash
git -C "/Users/aaron/.claude/skills/capture-to-notion" status --short
```

Expected: only intentional modified files are shown. Do not commit unless the user explicitly asks for a commit.

---

## Recommended Execution Order

1. Task 1: Planner asset trust profile-driven cleanup.
2. Task 2: Schema warning policy profile/config-driven cleanup.
3. Task 5: Remove `extract_book_title` alias.
4. Task 4: Doctor stale-cache warning.
5. Task 3: Scanner official type boundary regression.
6. Task 6: Official Notion property type alignment regression net.

This order removes the clearest remaining hardcoded planner/schema branches first, then adds user-facing migration guidance, then expands the type-boundary regression net.

## Self-Review

- Spec coverage: all six requested directions are covered by Tasks 1-6.
- Placeholder scan: no `TBD`, `TODO`, or open-ended implementation placeholder remains; Task 6 Step 2 requires reading an existing fixture before inserting the concrete assertion because that file's fake adapter shape must be preserved.
- Type consistency: parser profile keys used in tests and implementation snippets are `asset_trust_required_fields`, `trusted_field_sources`, and `non_blocking_warning_prefixes`; all are list-valued and parsed through `_string_list()`.
- Scope check: the plan changes fewer than 15 files and each task is independently testable.
