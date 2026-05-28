# Generic Notion Write Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Capture to Notion from data-source-only writes into a generic Notion write planner that supports Page, Data Source, Database, Block, and View with the correct official API roles.

**Architecture:** Keep the existing preflight → plan → confirm → apply → verify safety model. Add a target-kind model where `page_parent` and `data_source` are first-class write targets, `existing_page` is an append/update target, `database` resolves to data sources, `view` provides data-source context, and `block` is body content. Introduce focused block conversion and page-parent write operations without weakening existing data source flows.

**Tech Stack:** Python 3, Notion Python client via `NotionAdapter`, pytest, existing `capture-to-notion` CLI, v2 graph cache.

---

## Official Notion object model to implement

Capture to Notion must support Notion objects with these roles:

| Notion object | Supported role | Direct write target? | Capture to Notion behavior |
|---|---|---:|---|
| Page | parent page, created child page, existing page | yes | create child pages under page parents; append body blocks to existing pages |
| Data Source | structured row/page parent | yes | create/update structured records with schema-conformant properties |
| Database | container for one or more data sources | no | scan/resolve databases to data sources before structured writes |
| Block | page body content unit | no | render raw article/notes into paragraphs, headings, lists, code, quote, divider |
| View | display/query context for a data source | no | validate the backing data source; never use as parent |

---

## File structure

**Modify existing files**

- `SKILL.md` — Replace data-source-only guidance with official object role matrix and routing rules.
- `capture_to_notion/models.py` — Extend `Target` and `WritePlan` serialization to carry `target_kind`, `parent_page_id`, and block-operation metadata.
- `capture_to_notion/notion_adapter.py` — Add page-parent creation and block append wrappers.
- `capture_to_notion/planner.py` — Add page-parent planning path while preserving existing data-source planning.
- `capture_to_notion/preflight.py` — Route page-parent targets to `capture_plan` when scanned target has no data source but can receive child pages.
- `capture_to_notion/writer.py` — Execute `create_child_page` and `append_page_content` operations alongside existing `create_or_update_page`.
- `capture_to_notion/verifier.py` — Verify plain page creation and body blocks.
- `capture_to_notion/cli.py` — Keep `capture plan/apply/verify` as the public flow; update compact summaries for page-parent operations.
- `tests/test_planner.py` — Add page-parent planning tests and regression tests for data-source planning.
- `tests/test_preflight.py` — Add routing tests for page-parent targets.
- `tests/test_writer.py` — Add writer unit tests for child page creation and block append batching.
- `tests/test_capture_apply.py` — Add CLI apply/verify tests for plain page writes.
- `tests/test_cli_target.py` — Add scan output assertions for page-only graphs.

**Create new focused files**

- `capture_to_notion/blocks.py` — Convert raw Markdown-like/plain text into Notion block payloads and split append batches.
- `tests/test_blocks.py` — Unit tests for block conversion, truncation-safe rich text chunks, and 100-child batching.

---

## Operation vocabulary

Add these operation types:

```python
CREATE_OR_UPDATE_PAGE = "create_or_update_page"      # existing data_source path
CREATE_CHILD_PAGE = "create_child_page"              # new page_parent path
APPEND_PAGE_CONTENT = "append_page_content"          # existing_page path
COMPLETE_RELATION_PAGE = "complete_relation_page"    # existing relation completion path
```

Target kind values:

```python
TARGET_KIND_DATA_SOURCE = "data_source"
TARGET_KIND_PAGE_PARENT = "page_parent"
TARGET_KIND_EXISTING_PAGE = "existing_page"
TARGET_KIND_VIEW_BACKED_DATA_SOURCE = "view_backed_data_source"
```

---

### Task 1: Add Notion block conversion utilities

**Files:**
- Create: `capture_to_notion/blocks.py`
- Test: `tests/test_blocks.py`

- [ ] **Step 1: Write failing tests for block conversion**

Create `tests/test_blocks.py`:

```python
from capture_to_notion.blocks import build_body_blocks, split_block_batches


def block_text(block):
    block_type = block["type"]
    return block[block_type]["rich_text"][0]["text"]["content"]


def test_build_body_blocks_converts_headings_lists_quotes_code_and_paragraphs():
    raw = """# Title ignored by caller

## Why V4 matters

DeepSeek V4 changes the cost model.

- 1M context
- cache pricing

> Models become workflow infrastructure.

```text
Flash for default work
Pro for hard calls
```

---

Final paragraph."""

    blocks = build_body_blocks(raw, title="Title ignored by caller")

    assert [block["type"] for block in blocks] == [
        "heading_2",
        "paragraph",
        "bulleted_list_item",
        "bulleted_list_item",
        "quote",
        "code",
        "divider",
        "paragraph",
    ]
    assert block_text(blocks[0]) == "Why V4 matters"
    assert block_text(blocks[1]) == "DeepSeek V4 changes the cost model."
    assert blocks[5]["code"]["language"] == "plain text"
    assert block_text(blocks[7]) == "Final paragraph."


def test_build_body_blocks_omits_matching_title_line():
    blocks = build_body_blocks("标题：DeepSeek V4\n\nBody", title="DeepSeek V4")

    assert [block["type"] for block in blocks] == ["paragraph"]
    assert block_text(blocks[0]) == "Body"


def test_build_body_blocks_splits_long_rich_text_chunks():
    blocks = build_body_blocks("x" * 4500, title="Long")

    assert len(blocks) == 3
    assert [len(block_text(block)) for block in blocks] == [1900, 1900, 700]


def test_split_block_batches_uses_notion_100_child_limit():
    blocks = build_body_blocks("\n\n".join(f"p{i}" for i in range(205)), title="Many")

    batches = split_block_batches(blocks)

    assert [len(batch) for batch in batches] == [100, 100, 5]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_blocks.py -q
```

Expected: fails with `ModuleNotFoundError: No module named 'capture_to_notion.blocks'`.

- [ ] **Step 3: Implement minimal block conversion**

Create `capture_to_notion/blocks.py`:

```python
from __future__ import annotations

from typing import Any

RICH_TEXT_CHUNK_SIZE = 1900
NOTION_CHILDREN_LIMIT = 100


def _rich_text(content: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": content}}]


def _text_block(block_type: str, content: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(content)},
    }


def _code_block(content: str, language: str = "plain text") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "code",
        "code": {"rich_text": _rich_text(content), "language": language},
    }


def _divider_block() -> dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def _chunks(text: str, size: int = RICH_TEXT_CHUNK_SIZE) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _append_paragraph_blocks(blocks: list[dict[str, Any]], text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return
    for chunk in _chunks(stripped):
        blocks.append(_text_block("paragraph", chunk))


def _is_matching_title(line: str, title: str) -> bool:
    stripped = line.strip()
    candidates = {title.strip(), f"# {title.strip()}", f"标题：{title.strip()}", f"Title: {title.strip()}"}
    return stripped in candidates


def build_body_blocks(raw_input: str, *, title: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    paragraph_lines: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        _append_paragraph_blocks(blocks, "\n".join(paragraph_lines))
        paragraph_lines.clear()

    def flush_code() -> None:
        if not code_lines:
            return
        for chunk in _chunks("\n".join(code_lines)):
            blocks.append(_code_block(chunk))
        code_lines.clear()

    for line in raw_input.splitlines():
        stripped = line.strip()
        if _is_matching_title(stripped, title):
            continue
        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_paragraph()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            continue
        if stripped == "---":
            flush_paragraph()
            blocks.append(_divider_block())
        elif stripped.startswith("### "):
            flush_paragraph()
            blocks.append(_text_block("heading_3", stripped[4:]))
        elif stripped.startswith("## "):
            flush_paragraph()
            blocks.append(_text_block("heading_2", stripped[3:]))
        elif stripped.startswith("# "):
            flush_paragraph()
            blocks.append(_text_block("heading_1", stripped[2:]))
        elif stripped.startswith("- "):
            flush_paragraph()
            blocks.append(_text_block("bulleted_list_item", stripped[2:]))
        elif len(stripped) > 3 and stripped[0].isdigit() and stripped[1:3] == ". ":
            flush_paragraph()
            blocks.append(_text_block("numbered_list_item", stripped[3:]))
        elif stripped.startswith("> "):
            flush_paragraph()
            blocks.append(_text_block("quote", stripped[2:]))
        else:
            paragraph_lines.append(stripped)
    flush_paragraph()
    if in_code:
        flush_code()
    return blocks


def split_block_batches(blocks: list[dict[str, Any]], *, limit: int = NOTION_CHILDREN_LIMIT) -> list[list[dict[str, Any]]]:
    return [blocks[index : index + limit] for index in range(0, len(blocks), limit)]
```

- [ ] **Step 4: Run tests to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_blocks.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/blocks.py tests/test_blocks.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: add Notion block rendering utilities"
```

---

### Task 2: Add adapter support for page-parent writes and block append

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing adapter tests**

Append to `tests/test_notion_adapter.py`:

```python
def test_create_child_page_uses_page_parent_and_title_property():
    calls = []

    class Pages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return {"id": "page-created", "url": "https://notion.so/page-created"}

    class Client:
        pages = Pages()

    adapter = NotionAdapter(Client())

    result = adapter.create_child_page(
        "parent-page",
        "DeepSeek V4",
        children=[{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}],
    )

    assert result["id"] == "page-created"
    assert calls == [
        {
            "parent": {"page_id": "parent-page"},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": "DeepSeek V4"}}]}},
            "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}],
        }
    ]


def test_append_block_children_calls_blocks_children_append():
    calls = []

    class Children:
        def append(self, **kwargs):
            calls.append(kwargs)
            return {"results": kwargs["children"]}

    class Blocks:
        children = Children()

    class Client:
        blocks = Blocks()

    adapter = NotionAdapter(Client())
    children = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}]

    result = adapter.append_block_children("page-created", children)

    assert result == {"results": children}
    assert calls == [{"block_id": "page-created", "children": children}]
```

- [ ] **Step 2: Run adapter tests to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q -k "child_page or append_block_children"
```

Expected: fails with missing `create_child_page` and `append_block_children`.

- [ ] **Step 3: Implement adapter methods**

In `capture_to_notion/notion_adapter.py`, after existing `create_page`:

```python
    def create_child_page(
        self,
        parent_page_id: str,
        title: str,
        children: list[dict[str, Any]] | None = None,
        icon: Any = None,
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        }
        if children:
            kwargs["children"] = children
        if icon is not None:
            kwargs["icon"] = icon
        if cover is not None:
            kwargs["cover"] = self._normalize_cover(cover, cover_source_url)
        return self._call(self.client.pages.create, **kwargs)

    def append_block_children(self, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call(self.client.blocks.children.append, block_id=block_id, children=children)
```

- [ ] **Step 4: Run adapter tests to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q -k "child_page or append_block_children"
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/notion_adapter.py tests/test_notion_adapter.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: support Notion page-parent writes"
```

---

### Task 3: Extend plan models with target kinds

**Files:**
- Modify: `capture_to_notion/models.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write failing serialization test**

Append to `tests/test_planner.py`:

```python
def test_write_plan_serializes_page_parent_target_kind():
    plan = WritePlan(
        plan_id="plan-page-1",
        content_type="article",
        target=Target(
            page_title="知识",
            page_id="parent-page",
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_kind="page_parent",
            parent_page_id="parent-page",
        ),
        summary={"title": "DeepSeek V4"},
        normalized_record={"title": "DeepSeek V4"},
        field_mapping={},
        operations=[{"type": "create_child_page", "parent_page_id": "parent-page", "title": "DeepSeek V4", "body_blocks": []}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )

    data = plan.to_dict()

    assert data["target"]["target_kind"] == "page_parent"
    assert data["target"]["parent_page_id"] == "parent-page"
    assert WritePlan.from_dict(data).target.target_kind == "page_parent"
    assert WritePlan.from_dict(data).target.parent_page_id == "parent-page"
```

- [ ] **Step 2: Run serialization test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py::test_write_plan_serializes_page_parent_target_kind -q
```

Expected: fails because `Target` does not accept `target_kind` or `parent_page_id`.

- [ ] **Step 3: Implement model fields**

In `capture_to_notion/models.py`, extend `Target`:

```python
@dataclass
class Target:
    page_title: str | None
    page_id: str | None
    data_source_id: str | None
    confidence: str
    source: str
    target_id: str | None = None
    view_id: str | None = None
    view_name: str | None = None
    view_type: str | None = None
    display_page_id: str | None = None
    target_kind: str | None = None
    parent_page_id: str | None = None
```

Update `Target.from_dict`:

```python
            target_kind=data.get("target_kind"),
            parent_page_id=data.get("parent_page_id"),
```

Update `WritePlan.to_dict` target cleanup loop:

```python
        for key in ("target_id", "view_id", "view_name", "view_type", "display_page_id", "target_kind", "parent_page_id"):
            if target.get(key) is None:
                target.pop(key)
```

- [ ] **Step 4: Run serialization test to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py::test_write_plan_serializes_page_parent_target_kind -q
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/models.py tests/test_planner.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: model generic Notion write targets"
```

---

### Task 4: Execute page-parent write operations in writer

**Files:**
- Modify: `capture_to_notion/writer.py`
- Test: `tests/test_writer.py`

- [ ] **Step 1: Write failing writer tests**

Append to `tests/test_writer.py`:

```python
def make_page_parent_plan(blocks):
    return WritePlan(
        plan_id="plan-page-parent",
        content_type="article",
        target=Target(
            page_title="知识",
            page_id="parent-page",
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_kind="page_parent",
            parent_page_id="parent-page",
        ),
        summary={"title": "DeepSeek V4"},
        normalized_record={"title": "DeepSeek V4"},
        field_mapping={},
        operations=[{"type": "create_child_page", "parent_page_id": "parent-page", "title": "DeepSeek V4", "body_blocks": blocks}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )


class PageParentAdapter:
    def __init__(self):
        self.created = []
        self.appended = []

    def create_child_page(self, parent_page_id, title, children=None, cover=None):
        self.created.append({"parent_page_id": parent_page_id, "title": title, "children": children or [], "cover": cover})
        return {"id": "created-page", "url": "https://notion.so/created-page"}

    def append_block_children(self, block_id, children):
        self.appended.append({"block_id": block_id, "children": children})
        return {"results": children}


def paragraph_block(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def test_apply_write_plan_creates_child_page_with_first_100_blocks():
    blocks = [paragraph_block(str(index)) for index in range(3)]
    adapter = PageParentAdapter()

    result = apply_write_plan(make_page_parent_plan(blocks), {"pages": {"parent-page": {"title": "知识"}}}, adapter)

    assert result["results"] == [{"type": "create_child_page", "action": "create_child_page", "page_id": "created-page", "url": "https://notion.so/created-page"}]
    assert adapter.created[0]["parent_page_id"] == "parent-page"
    assert adapter.created[0]["title"] == "DeepSeek V4"
    assert adapter.created[0]["children"] == blocks
    assert adapter.appended == []


def test_apply_write_plan_appends_remaining_blocks_after_create_limit():
    blocks = [paragraph_block(str(index)) for index in range(205)]
    adapter = PageParentAdapter()

    result = apply_write_plan(make_page_parent_plan(blocks), {"pages": {"parent-page": {"title": "知识"}}}, adapter)

    assert result["results"][0]["page_id"] == "created-page"
    assert len(adapter.created[0]["children"]) == 100
    assert [len(call["children"]) for call in adapter.appended] == [100, 5]
    assert all(call["block_id"] == "created-page" for call in adapter.appended)
```

- [ ] **Step 2: Run writer tests to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_writer.py -q -k "child_page or remaining_blocks"
```

Expected: fails because writer rejects non-`create_or_update_page` operations or requires `data_source_id`.

- [ ] **Step 3: Implement page-parent writer branch**

In `capture_to_notion/writer.py`:

1. Import block batching:

```python
from capture_to_notion.blocks import split_block_batches
```

2. Add constants:

```python
CREATE_CHILD_PAGE = "create_child_page"
APPEND_PAGE_CONTENT = "append_page_content"
```

3. Replace `_validate_plan_target` with a target-kind-aware version:

```python
def _validate_plan_target(plan: WritePlan, target_structure: dict[str, Any]) -> None:
    target_kind = plan.target.target_kind or ("data_source" if plan.target.data_source_id else None)
    if target_kind == "data_source" or target_kind == "view_backed_data_source":
        data_source_id = plan.target.data_source_id
        if not data_source_id:
            raise NotionWriterError("Plan target is missing data_source_id")
        _data_source_schema(target_structure, data_source_id)
        return
    if target_kind == "page_parent":
        parent_page_id = plan.target.parent_page_id or plan.target.page_id
        if not parent_page_id:
            raise NotionWriterError("Plan target is missing parent_page_id")
        return
    if target_kind == "existing_page":
        if not plan.target.page_id:
            raise NotionWriterError("Plan target is missing page_id")
        return
    raise NotionWriterError(f"Unsupported target kind: {target_kind}")
```

4. Update `_validate_write_operations`:

```python
def _validate_write_operations(plan: WritePlan) -> None:
    for operation in plan.operations:
        operation_type = operation.get("type")
        if operation_type == CREATE_OR_UPDATE_PAGE:
            operation_data_source_id = operation.get("data_source_id")
            if operation_data_source_id != plan.target.data_source_id:
                raise NotionWriterError("Operation data_source_id does not match plan target data_source_id")
        elif operation_type == CREATE_CHILD_PAGE:
            parent_page_id = operation.get("parent_page_id")
            expected_parent_page_id = plan.target.parent_page_id or plan.target.page_id
            if parent_page_id != expected_parent_page_id:
                raise NotionWriterError("Operation parent_page_id does not match plan target parent_page_id")
            if not isinstance(operation.get("title"), str) or not operation.get("title"):
                raise NotionWriterError("Child page operation is missing title")
        elif operation_type == APPEND_PAGE_CONTENT:
            if operation.get("page_id") != plan.target.page_id:
                raise NotionWriterError("Append operation page_id does not match plan target page_id")
        else:
            raise NotionWriterError(f"Unsupported write operation type: {operation_type}")
```

5. Add helper:

```python
def _apply_create_child_page(operation: dict[str, Any], adapter: Any) -> dict[str, Any]:
    blocks = operation.get("body_blocks", [])
    if not isinstance(blocks, list):
        raise NotionWriterError("Child page body_blocks must be a list")
    batches = split_block_batches(blocks)
    first_batch = batches[0] if batches else []
    response = adapter.create_child_page(operation["parent_page_id"], operation["title"], children=first_batch)
    page_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(page_id, str) or not page_id:
        raise NotionWriterError("Notion did not return created child page id")
    for batch in batches[1:]:
        adapter.append_block_children(page_id, batch)
    result = {"type": CREATE_CHILD_PAGE, "action": "create_child_page", "page_id": page_id}
    response_url = response.get("url") if isinstance(response, dict) else None
    if response_url is not None:
        result["url"] = response_url
    return result
```

6. At the start of `apply_write_plan`, after validation, branch page-parent operations before schema/property logic:

```python
    if any(operation.get("type") == CREATE_CHILD_PAGE for operation in plan.operations):
        results = []
        for operation in plan.operations:
            try:
                results.append(_apply_create_child_page(operation, adapter))
            except EXPECTED_NOTION_WRITE_ERRORS as exc:
                if results:
                    raise PartialWriteError("写入已部分完成，后续页面内容写入失败；请检查已创建页面后重试") from exc
                raise
        return {"plan_id": plan.plan_id, "applied": True, "results": results, "asset_results": [], "warnings": list(plan.warnings)}
```

- [ ] **Step 4: Run writer tests to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_writer.py -q -k "child_page or remaining_blocks"
```

Expected: tests pass.

- [ ] **Step 5: Run writer regression tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_writer.py -q
```

Expected: all writer tests pass.

- [ ] **Step 6: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/writer.py tests/test_writer.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: apply page-parent write plans"
```

---

### Task 5: Plan page-parent writes from scanned page-only targets

**Files:**
- Modify: `capture_to_notion/planner.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write failing planner test**

Append to `tests/test_planner.py`:

```python
def test_v2_plan_creates_child_page_when_target_is_plain_page(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "ai-knowledge",
        {
            "cache_version": 2,
            "graph_id": "ai-knowledge",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "知识", "kind": "page"}},
            "blocks": {},
            "databases": {},
            "data_sources": {},
            "views": {},
        },
    )
    store.bind_alias("AI/知识", graph_id="ai-knowledge", profile_id=None, kind="graph")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "标题：DeepSeek V4\n\n## Why it matters\n\nLong-context Agent work gets cheaper.",
                "target_hint": "AI/知识",
                "content_type_hint": "article",
                "intent_hint": "direct_write",
                "input_shape_hint": "plain_text",
                "target_scope_hint": "page_parent",
                "user_requested_action": "write",
            }
        ),
        store,
    )

    assert plan.target.target_kind == "page_parent"
    assert plan.target.parent_page_id == "page-knowledge"
    assert plan.target.data_source_id is None
    assert plan.operations[0]["type"] == "create_child_page"
    assert plan.operations[0]["parent_page_id"] == "page-knowledge"
    assert plan.operations[0]["title"] == "DeepSeek V4"
    assert [block["type"] for block in plan.operations[0]["body_blocks"]] == ["heading_2", "paragraph"]
    assert plan.summary["write_targets"] == [
        {
            "type": "primary_page",
            "action": "create_child_page",
            "title": "DeepSeek V4",
            "target_page": "知识",
            "parent_page_id": "page-knowledge",
            "target_kind": "page_parent",
            "page_id": None,
            "page_id_status": "pending_after_apply",
            "context_verification_source": "v2_page_graph",
        }
    ]
```

- [ ] **Step 2: Run planner test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py::test_v2_plan_creates_child_page_when_target_is_plain_page -q
```

Expected: fails because planner requires data source/profile.

- [ ] **Step 3: Implement page-parent planning helper**

In `capture_to_notion/planner.py`, import:

```python
from capture_to_notion.blocks import build_body_blocks
```

Add helpers near existing summary helpers:

```python
def _graph_root_page(graph: dict[str, Any]) -> dict[str, Any] | None:
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    page_id = root.get("id") if root.get("kind") == "page" else None
    pages = graph.get("pages") if isinstance(graph.get("pages"), dict) else {}
    page = pages.get(page_id) if isinstance(page_id, str) else None
    return page if isinstance(page, dict) else None


def _is_page_parent_capture(capture: CaptureInput, graph: dict[str, Any]) -> bool:
    if graph.get("data_sources"):
        return False
    if _graph_root_page(graph) is None:
        return False
    return capture.target_scope_hint in {"page_parent", "specific_page", "target_unspecified"} or capture.content_type_hint in {"article", "note", None}


def _title_from_raw_input(raw_input: str) -> str:
    for line in raw_input.splitlines():
        stripped = line.strip()
        if stripped.startswith("标题："):
            return stripped.split("：", 1)[1].strip()
        if stripped.startswith("Title:"):
            return stripped.split(":", 1)[1].strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    first_line = next((line.strip() for line in raw_input.splitlines() if line.strip()), "Untitled")
    return first_line[:120]
```

Add a planner branch before data-source profile planning in `build_capture_plan` after alias graph is resolved:

```python
    if isinstance(cache, CacheV2Store) and _is_page_parent_capture(capture, graph):
        page = _graph_root_page(graph)
        if page is None:
            raise ValueError("page_parent target is missing root page")
        parent_page_id = page.get("page_id") or graph.get("root", {}).get("id")
        title = _title_from_raw_input(capture.raw_input)
        body_blocks = build_body_blocks(capture.raw_input, title=title)
        operation = {"type": "create_child_page", "parent_page_id": parent_page_id, "title": title, "body_blocks": body_blocks}
        write_target = {
            "type": "primary_page",
            "action": "create_child_page",
            "title": title,
            "target_page": page.get("title"),
            "parent_page_id": parent_page_id,
            "target_kind": "page_parent",
            "page_id": None,
            "page_id_status": "pending_after_apply",
            "context_verification_source": "v2_page_graph",
        }
        return WritePlan(
            plan_id=_plan_id(),
            content_type=capture.content_type_hint or "note",
            target=Target(
                page_title=page.get("title"),
                page_id=parent_page_id,
                data_source_id=None,
                confidence="high",
                source="v2_page_graph",
                target_kind="page_parent",
                parent_page_id=parent_page_id,
            ),
            summary={
                "target_page": page.get("title"),
                "target_data_source": None,
                "title": title,
                "content_type": capture.content_type_hint or "note",
                "mapped_fields": {},
                "writable_fields": {},
                "write_targets": [write_target],
                "asset_actions": [],
                "body_block_count": len(body_blocks),
                "requires_confirmation": False,
                "confirmation_reason": None,
                "warnings": [],
                "warning_details": [],
            },
            normalized_record={"title": title, "body": capture.raw_input},
            field_mapping={},
            operations=[operation],
            asset_operations=[],
            sources=[],
            warnings=[],
            requires_confirmation=False,
            confirmation_reason=None,
            capture_input=capture.to_dict(),
        )
```

- [ ] **Step 4: Run planner test to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py::test_v2_plan_creates_child_page_when_target_is_plain_page -q
```

Expected: test passes.

- [ ] **Step 5: Run planner regressions for existing data-source behavior**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py -q -k "v2_plan or podcast_capture or book_capture"
```

Expected: selected planner tests pass.

- [ ] **Step 6: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/planner.py tests/test_planner.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: plan plain page captures"
```

---

### Task 6: Route page-only targets through preflight

**Files:**
- Modify: `capture_to_notion/preflight.py`
- Test: `tests/test_preflight.py`

- [ ] **Step 1: Write failing preflight test**

Append to `tests/test_preflight.py`:

```python
def test_preflight_routes_scanned_page_only_target_to_capture_plan(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "ai-knowledge",
        {
            "cache_version": 2,
            "graph_id": "ai-knowledge",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "知识", "kind": "page"}},
            "blocks": {},
            "databases": {},
            "data_sources": {},
            "views": {},
        },
    )
    store.bind_alias("AI/知识", graph_id="ai-knowledge", profile_id=None, kind="graph")

    result = run_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "标题：DeepSeek V4\n\nBody",
                "target_hint": "AI/知识",
                "content_type_hint": "article",
                "intent_hint": "direct_write",
                "input_shape_hint": "plain_text",
                "target_scope_hint": "page_parent",
                "user_requested_action": "write",
            }
        ),
        store,
    )

    assert result["target"]["status"] == "v2_page_parent_ready"
    assert result["workflow"]["planning"]["next_action"] == "capture_plan"
    assert result["safe_actions"] == [{"action": "capture_plan", "reason": "v2_page_parent_ready"}]
```

Use the existing preflight entry function name from `tests/test_preflight.py`. If the helper is named differently, use the helper already present in that file rather than creating a new preflight runner.

- [ ] **Step 2: Run preflight test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py::test_preflight_routes_scanned_page_only_target_to_capture_plan -q
```

Expected: fails because scanned page-only graph still routes to profile/data-source missing.

- [ ] **Step 3: Implement page-parent preflight status**

In `capture_to_notion/preflight.py`, after target alias graph resolution and before data-source/profile checks, add logic equivalent to:

```python
def _graph_has_data_sources(graph: dict[str, Any]) -> bool:
    return bool(graph.get("data_sources"))


def _graph_has_root_page(graph: dict[str, Any]) -> bool:
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    return root.get("kind") == "page" and isinstance(root.get("id"), str)


def _wants_page_parent(capture: CaptureInput) -> bool:
    return capture.target_scope_hint == "page_parent" or capture.content_type_hint in {"article", "note"} or capture.input_shape_hint in {"plain_text", "structured_notes"}
```

When `_graph_has_root_page(graph)` is true, `_graph_has_data_sources(graph)` is false, and `_wants_page_parent(capture)` is true, return compact workflow facts:

```python
"target": {"hint": capture.target_hint, "status": "v2_page_parent_ready", "source": "target_hint", "alias": capture.target_hint},
"safe_actions": [{"action": "capture_plan", "reason": "v2_page_parent_ready"}],
"blocked_actions": [],
"workflow": {"planning": {"status": "ready", "next_action": "capture_plan", "reason": "v2_page_parent_ready"}, ...},
"next_action": "capture_plan",
"next_action_reason": "v2_page_parent_ready",
```

Preserve the existing workflow keys already emitted by preflight.

- [ ] **Step 4: Run preflight test to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py::test_preflight_routes_scanned_page_only_target_to_capture_plan -q
```

Expected: test passes.

- [ ] **Step 5: Run preflight regression tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py -q
```

Expected: all preflight tests pass.

- [ ] **Step 6: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/preflight.py tests/test_preflight.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: route plain page captures"
```

---

### Task 7: Verify page-parent writes

**Files:**
- Modify: `capture_to_notion/verifier.py`
- Modify: `capture_to_notion/cli.py`
- Test: `tests/test_capture_apply.py`

- [ ] **Step 1: Write failing verifier test**

Append to `tests/test_capture_apply.py`:

```python
def test_verify_plain_page_checks_title_and_body_blocks():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Why it matters"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Long-context Agent work gets cheaper."}]}},
            ]

    result = verify_plain_page(
        "page-created",
        Adapter(),
        expected_title="DeepSeek V4",
        expected_block_count=2,
        expected_text_samples=["Why it matters", "Long-context Agent work gets cheaper."],
    )

    assert result == {
        "page_id": "page-created",
        "verified": True,
        "checks": {
            "page": {"status": "present"},
            "title": {"status": "present"},
            "body_blocks": {"status": "present", "count": 2},
            "body_text_samples": {"status": "present"},
        },
        "warnings": [],
    }
```

- [ ] **Step 2: Run verifier test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py::test_verify_plain_page_checks_title_and_body_blocks -q
```

Expected: fails because `verify_plain_page` is missing.

- [ ] **Step 3: Implement `verify_plain_page`**

In `capture_to_notion/verifier.py` add:

```python
def _plain_text_from_rich_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return "".join(item.get("plain_text", "") for item in items if isinstance(item, dict))


def _plain_page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties") if isinstance(page.get("properties"), dict) else {}
    title_property = properties.get("title") if isinstance(properties.get("title"), dict) else None
    if title_property and title_property.get("type") == "title":
        return _plain_text_from_rich_text(title_property.get("title"))
    for property_value in properties.values():
        if isinstance(property_value, dict) and property_value.get("type") == "title":
            return _plain_text_from_rich_text(property_value.get("title"))
    return ""


def _block_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    payload = block.get(block_type) if isinstance(block_type, str) else None
    if not isinstance(payload, dict):
        return ""
    return _plain_text_from_rich_text(payload.get("rich_text"))


def verify_plain_page(
    page_id: str,
    adapter: Any,
    *,
    expected_title: str | None = None,
    expected_block_count: int | None = None,
    expected_text_samples: list[str] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    checks: dict[str, Any] = {"page": {"status": "missing"}}
    page = adapter.retrieve_page(page_id)
    checks["page"] = {"status": "present"}

    if expected_title is not None:
        actual_title = _plain_page_title(page)
        if actual_title == expected_title:
            checks["title"] = {"status": "present"}
        else:
            checks["title"] = {"status": "mismatch", "expected": expected_title, "actual": actual_title}
            warnings.append("title_mismatch")

    blocks = adapter.list_block_children(page_id)
    block_count = len(blocks)
    if expected_block_count is not None:
        if block_count >= expected_block_count:
            checks["body_blocks"] = {"status": "present", "count": block_count}
        else:
            checks["body_blocks"] = {"status": "missing", "count": block_count, "expected": expected_block_count}
            warnings.append("body_blocks_missing")

    samples = expected_text_samples or []
    if samples:
        body_text = "\n".join(_block_text(block) for block in blocks)
        missing_samples = [sample for sample in samples if sample not in body_text]
        if missing_samples:
            checks["body_text_samples"] = {"status": "missing", "missing": missing_samples}
            warnings.append("body_text_samples_missing")
        else:
            checks["body_text_samples"] = {"status": "present"}

    return {"page_id": page_id, "verified": not warnings, "checks": checks, "warnings": warnings}
```

- [ ] **Step 4: Wire apply verification for child page results**

In `capture_to_notion/cli.py`, import `verify_plain_page` and update apply verification logic where `results` are processed:

```python
if result.get("type") == "create_child_page":
    verification_pages.append(
        verify_plain_page(
            page_id,
            adapter,
            expected_title=result.get("title") or plan.summary.get("title"),
            expected_block_count=plan.summary.get("body_block_count"),
        )
    )
```

If the existing apply verification helper builds page verification in a separate function, add the branch there instead of duplicating logic inside `cmd_capture_apply`.

- [ ] **Step 5: Run verifier test to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py::test_verify_plain_page_checks_title_and_body_blocks -q
```

Expected: test passes.

- [ ] **Step 6: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/verifier.py capture_to_notion/cli.py tests/test_capture_apply.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: verify plain Notion pages"
```

---

### Task 8: Add CLI integration coverage for page-parent plan/apply

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_capture_apply.py`

- [ ] **Step 1: Write failing CLI plan test**

Append to `tests/test_cli.py`:

```python
def test_capture_plan_compact_outputs_page_parent_write_target(tmp_path, monkeypatch, capsys):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "ai-knowledge",
        {
            "cache_version": 2,
            "graph_id": "ai-knowledge",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "知识", "kind": "page"}},
            "blocks": {},
            "databases": {},
            "data_sources": {},
            "views": {},
        },
    )
    store.bind_alias("AI/知识", graph_id="ai-knowledge", profile_id=None, kind="graph")
    input_path = tmp_path / "input.json"
    plan_path = tmp_path / "plan.json"
    input_path.write_text(
        json.dumps(
            {
                "raw_input": "标题：DeepSeek V4\n\nBody",
                "target_hint": "AI/知识",
                "content_type_hint": "article",
                "target_scope_hint": "page_parent",
                "intent_hint": "direct_write",
                "user_requested_action": "write",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(plan_path), "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["target"]["target_kind"] == "page_parent"
    assert data["summary"]["write_targets"][0]["action"] == "create_child_page"
    assert data["summary"]["body_block_count"] == 1
```

- [ ] **Step 2: Run CLI plan test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py::test_capture_plan_compact_outputs_page_parent_write_target -q
```

Expected: fails until planner/CLI summary supports target kind.

- [ ] **Step 3: Write failing CLI apply test**

Append to `tests/test_capture_apply.py`:

```python
def test_capture_apply_creates_plain_child_page_from_plan(tmp_path, monkeypatch, capsys):
    class Adapter:
        def __init__(self):
            self.pages = {}
            self.blocks = {}

        def create_child_page(self, parent_page_id, title, children=None, cover=None):
            self.pages["created-page"] = {"id": "created-page", "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}}}
            self.blocks["created-page"] = list(children or [])
            return {"id": "created-page", "url": "https://notion.so/created-page"}

        def append_block_children(self, block_id, children):
            self.blocks.setdefault(block_id, []).extend(children)
            return {"results": children}

        def retrieve_page(self, page_id):
            return self.pages[page_id]

        def list_block_children(self, page_id):
            return self.blocks.get(page_id, [])

    adapter = Adapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    plan_path = tmp_path / "plan.json"
    plan = WritePlan(
        plan_id="plain-page-plan",
        content_type="article",
        target=Target("知识", "page-knowledge", None, "high", "v2_page_graph", target_kind="page_parent", parent_page_id="page-knowledge"),
        summary={"title": "DeepSeek V4", "body_block_count": 1},
        normalized_record={"title": "DeepSeek V4"},
        field_mapping={},
        operations=[{"type": "create_child_page", "parent_page_id": "page-knowledge", "title": "DeepSeek V4", "body_blocks": [paragraph_block("Body")]}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )
    plan.save(plan_path)

    result = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["applied"] is True
    assert data["results"][0]["type"] == "create_child_page"
    assert data["verification"]["verified"] is True
```

- [ ] **Step 4: Run CLI apply test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py::test_capture_apply_creates_plain_child_page_from_plan -q
```

Expected: fails until apply loads page-parent plans without requiring target structure data source.

- [ ] **Step 5: Implement CLI apply structure handling for page-parent plans**

In `capture_to_notion/cli.py`, update `cmd_capture_apply` so page-parent plans do not require data-source target structure. If the current function loads target structure via graph/profile, add:

```python
if plan.target.target_kind == "page_parent":
    target_structure = {"pages": {plan.target.parent_page_id: {"page_id": plan.target.parent_page_id, "title": plan.target.page_title}}}
else:
    target_structure = _load_target_structure_for_plan(config, plan)
```

Use the existing helper names in `cli.py`; do not duplicate target loading if the helper can be extended cleanly.

- [ ] **Step 6: Run CLI integration tests to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py::test_capture_plan_compact_outputs_page_parent_write_target \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py::test_capture_apply_creates_plain_child_page_from_plan -q
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/cli.py tests/test_cli.py tests/test_capture_apply.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "feat: support plain page capture CLI flow"
```

---

### Task 9: Update scan summaries and Skill instructions for object roles

**Files:**
- Modify: `capture_to_notion/cli.py`
- Modify: `SKILL.md`
- Test: `tests/test_cli_target.py`

- [ ] **Step 1: Write failing scan summary test for page-only targets**

Append to `tests/test_cli_target.py`:

```python
class PlainPageScanAdapter:
    def retrieve_page(self, page_id):
        return {"id": page_id, "title": "知识"}

    def list_block_children(self, page_id):
        return [{"type": "paragraph", "id": "block-1", "paragraph": {"rich_text": []}}]


def test_target_scan_page_only_reports_page_parent_capability(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: PlainPageScanAdapter()))

    result = cli.main(["target", "scan", "--page-id", "page-knowledge", "--alias", "AI/知识", "--target-id", "ai-knowledge", "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["graph_id"] == "ai-knowledge"
    assert data["target_capabilities"] == {
        "page_parent": True,
        "data_source": False,
        "database_container": False,
        "view_context": False,
    }
    assert data["next_action"] == "capture preflight"
```

- [ ] **Step 2: Run scan test to verify failure**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py::test_target_scan_page_only_reports_page_parent_capability -q
```

Expected: fails because scan output always says `requires_profile_binding` and `target bind-profile`.

- [ ] **Step 3: Implement target capabilities in scan output**

In `capture_to_notion/cli.py`, add:

```python
def _target_capabilities(graph: dict[str, Any]) -> dict[str, bool]:
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    databases = graph.get("databases") if isinstance(graph.get("databases"), dict) else {}
    views = graph.get("views") if isinstance(graph.get("views"), dict) else {}
    return {
        "page_parent": root.get("kind") == "page",
        "data_source": bool(data_sources),
        "database_container": bool(databases),
        "view_context": bool(views),
    }
```

Update `_target_scan_output`:

```python
    capabilities = _target_capabilities(graph)
    output = {
        "cache_version": 2,
        "graph_id": graph_id,
        "target_capabilities": capabilities,
        "data_sources": _v2_data_source_summaries(graph, compact=compact),
        "views": _graph_view_names(graph),
        "requires_profile_binding": capabilities["data_source"],
        "next_action": "target bind-profile" if capabilities["data_source"] else "capture preflight",
    }
```

- [ ] **Step 4: Update `SKILL.md` capability boundary**

Replace the current data-source-only text:

```markdown
Writes always go to a data source. Views are display context and are validated when present.
```

with:

```markdown
Writes go to the correct Notion parent type for the confirmed target:
- Page parent writes create ordinary child pages and store body content as blocks.
- Data source writes create or update structured pages whose properties conform to the data source schema.
- Database objects are containers used to discover data sources; writes do not create rows directly under databases.
- Block objects carry page body content; they are created through page creation or append-block operations.
- View objects are display/query context for data sources; they help confirm the target but are not write parents.
```

In Required Flow, add before preflight routing:

```markdown
Before forcing `scan_target` or `bind-profile`, distinguish whether the user asked for a structured database/list entry or an ordinary child page. If a scanned target has a root page and no data sources, and the user asked to save an article/note/plain text under that page, continue to `capture_plan` as a page-parent write instead of requiring profile binding.
```

- [ ] **Step 5: Run scan test to verify green**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py::test_target_scan_page_only_reports_page_parent_capability -q
```

Expected: test passes.

- [ ] **Step 6: Commit**

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion add capture_to_notion/cli.py SKILL.md tests/test_cli_target.py
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "docs: define generic Notion object roles"
```

---

### Task 10: End-to-end regression and release readiness

**Files:**
- No source changes expected unless tests reveal a defect.

- [ ] **Step 1: Run focused new capability tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_blocks.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_writer.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Manual CLI dry run with page-parent target**

Use a temp config directory and a fake/scanned page graph if available from tests. If using the real local config, do not apply. Run:

```bash
capture-to-notion capture preflight --input /tmp/capture-to-notion-deepseek-v4-article-input.json --compact
capture-to-notion capture plan --input /tmp/capture-to-notion-deepseek-v4-article-input.json --output /tmp/capture-to-notion-page-parent-plan.json --compact
```

Expected:

```json
{
  "target": {"target_kind": "page_parent"},
  "summary": {"write_targets": [{"action": "create_child_page"}]}
}
```

- [ ] **Step 4: Manual CLI dry run with existing data-source target**

Run a known existing database target input such as a podcast episode capture with cached profile:

```bash
capture-to-notion capture preflight --input /tmp/capture-to-notion-banlatie-episode-input.json --compact
capture-to-notion capture plan --input /tmp/capture-to-notion-banlatie-episode-input.json --output /tmp/capture-to-notion-data-source-regression-plan.json --compact
```

Expected: output still uses `create_page` / `create_or_update_page` under a `data_source_id`, and existing mapped fields remain planned.

- [ ] **Step 5: Commit any test fixes**

If the verification steps required fixes, commit them:

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion status --short
git -C /Users/aaron/.claude/skills/capture-to-notion add <changed-files>
git -C /Users/aaron/.claude/skills/capture-to-notion commit -m "fix: stabilize generic Notion write model"
```

If there are no changes, do not create an empty commit.

---

## Execution notes

- Use a development branch before implementation:

```bash
git -C /Users/aaron/.claude/skills/capture-to-notion checkout -b generic-notion-write-model
```

- Do not push without explicit user approval.
- Do not use Notion MCP as a fallback.
- Preserve the existing database/data-source path and tests while adding page-parent capability.
- Do not implement schema editing, database creation, tables, callouts, image upload, or full Markdown fidelity in this plan.
- First version block rendering covers enough to make article/note pages useful: headings, paragraphs, lists, quotes, code, dividers.

## Self-review

- Spec coverage: Page writes are covered by Tasks 2, 4, 5, 6, 7, 8, 9. Data Source regressions are covered by Tasks 4, 5, 10. Database resolution remains through existing scan/data-source behavior and is documented in Task 9. Blocks are covered by Task 1. Views remain context-only and are documented in Task 9 with existing v2 scan behavior preserved.
- Placeholder scan: The plan avoids open-ended implementation instructions and gives concrete tests, code snippets, commands, and expected outputs.
- Type consistency: Operation names are consistently `create_child_page`, `append_page_content`, `create_or_update_page`, and `complete_relation_page`; target kinds are consistently `page_parent`, `data_source`, `existing_page`, and `view_backed_data_source`.
