# Notion API Capability Gap Implementation Plan

> Historical implementation plan: this document records the 2026-05-29 development plan and should not be used as the current task tracker. Check `docs/notion-api-capability-inventory.md` and `docs/capture-capability-matrix.md` for current capability status before planning new work.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 Capture to Notion 当前与 Notion 官方 API / Python SDK 之间的关键能力缺口，优先修复 Views 读写与样式复制能力，再逐步补齐与采集、写入、验证相关的基础 API 封装。

**Architecture:** 以 `NotionAdapter` 作为唯一 Notion API 边界，不回退 Notion MCP。官方 Python SDK 已暴露的能力通过 SDK endpoint 封装；SDK 未暴露但官方 API 已支持的能力通过 `client.request()` 在 Adapter 内部封装。Scanner、CLI、planner 只消费 Adapter 返回的通用对象，不直接依赖 SDK 私有结构。

**Tech Stack:** Python 3.12, `notion-client==3.0.0`, pytest, Capture to Notion v2 graph cache, Notion API version `2026-03-11`.

---

## Scope and Capability Inventory

本计划覆盖“与 Capture to Notion 采集、复制结构、写入、验证相关”的能力缺口。OAuth、多用户授权、webhooks 等不直接服务当前写入闭环的能力列入后置 backlog，不进入第一轮实现。

### P0: 必须先修

| 能力 | 官方 API | Python SDK 3.0.0 | 当前 Adapter | 当前问题 | 任务 |
|---|---|---|---|---|---|
| List/Retrieve/Create/Update/Delete Views | 支持 `/v1/views` | 无 `client.views` | 有方法但坏掉 | 调用不存在的 `self.client.views.*`，scanner 静默吞掉失败 | Task 1 |
| 扫描完整 view 配置 | 支持 list + retrieve | 需 raw request | scanner 只 list，不 retrieve | graph `views` 缺 `type/configuration/filter/sorts/quick_filters` | Task 2 |
| 复制 view 配置 | 支持 create view | 需 raw request | 无 remap | 源字段 id 不能直接复制到目标 data source | Task 3 |
| 创建 database 时复制 views | 支持 create database + create view | database create 已支持，view 需 raw | 只支持 schema | 默认创建 table，无法复制 Gallery | Task 4 |

### P1: 与写入/验证强相关

| 能力 | SDK 状态 | 当前 Adapter 状态 | 为什么相关 | 任务 |
|---|---|---|---|---|
| `blocks.retrieve/update/delete` | SDK 已有 | 未封装 | 复制/修复页面结构、清理错误 block | Task 5 |
| `pages.move` | SDK 已有 | 未封装 | 修复写错父级、页面迁移 | Task 5 |
| `pages.properties.retrieve` | SDK 已有 | 未封装 | 验证分页属性值，避免 relation/rollup/rich_text 长值漏读 | Task 5 |
| `data_sources.create/list_templates` | SDK 已有 | 未封装 | 支持 data source 模板、结构化创建 | Task 6 |
| `databases.update` | SDK 已有 | 未封装 | 更新 database container 标题/描述等 | Task 6 |
| `file_uploads.retrieve/list/complete` | SDK 已有 | 未独立封装 | 诊断封面/图片上传状态 | Task 6 |
| query pagination | SDK query 有分页响应 | 当前 `query_*` 只返回第一页 | 大库查重/验证可能漏掉后续页面 | Task 7 |

### P2: 先记录，后续单独验证官方路径后实现

| 能力 | 当前状态 | 处理方式 |
|---|---|---|
| Retrieve page as markdown | 官方文档出现，SDK 未见便捷 endpoint | Task 8 记录为 raw API candidate，不在未核实路径前写实现 |
| Update page content as markdown | 官方文档出现，SDK 未见便捷 endpoint | Task 8 记录为 raw API candidate，不在未核实路径前写实现 |
| View query endpoints | 官方文档出现，SDK 未见便捷 endpoint | Task 8 记录为 raw API candidate，不阻塞 P0 |
| comments update/delete | 官方文档出现；SDK 当前可见 create/list/retrieve | Task 8 记录差异，不进入第一轮写入闭环 |
| custom emojis | 官方文档出现 | Task 8 记录，低优先级 |

### P3: 后置 backlog

| 能力 | 原因 |
|---|---|
| comments create/list/retrieve | 可用于审核说明，但不影响采集写入 |
| users list/me/retrieve | people 字段或 bot 权限诊断时再补 |
| oauth token/revoke/introspect | 当前使用本地 integration token，不做多工作区 OAuth flow |
| webhooks | Capture to Notion 当前是主动写入工具，不是事件驱动同步系统 |

---

## File Structure

- Modify: `capture_to_notion/notion_adapter.py`
  - 添加 `_request()` raw API helper。
  - 修复 Views 方法。
  - 补齐 P1 SDK wrapper。
  - 为 query 方法补分页。
- Modify: `capture_to_notion/scanner.py`
  - 从 list views 改为 list + retrieve full view。
  - 不再把 Views 适配层缺失静默当成“无 views”。
- Create: `capture_to_notion/view_utils.py`
  - 放置 view property reference remap 逻辑。
  - 保持纯函数，便于测试。
- Modify: `capture_to_notion/cli.py`
  - `target create-database` 增加 `--views` 参数，支持 schema + views 复制。
  - 输出 created view ids 和 view warnings。
- Modify: `capture_to_notion/notion_graph.py`
  - 确保 `normalize_view()` 保留完整 view 配置。
- Test: `tests/test_notion_adapter.py`
  - Adapter raw Views、P1 wrappers、pagination 测试。
- Test: `tests/test_scanner.py`
  - scanner full view retrieval 测试。
- Test: `tests/test_notion_graph.py`
  - view normalization 回归测试。
- Test: `tests/test_cli_target.py`
  - `target create-database --views` CLI 测试。
- Test: `tests/test_view_utils.py`
  - view property remap 纯函数测试。

预计修改 10 个文件，低于 15 个文件限制。

---

### Task 1: Fix Views adapter with raw Notion requests

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write the failing test**

Replace the existing fake `client.views` expectation with a raw request fake. Add this test to `tests/test_notion_adapter.py` near the existing view tests:

```python
def test_views_api_methods_use_raw_request_when_sdk_has_no_views_namespace():
    class RawViewClient:
        def __init__(self):
            self.requests = []

        def request(self, path, method, query=None, body=None):
            self.requests.append({"path": path, "method": method, "query": query, "body": body})
            if path == "/views" and method == "GET":
                return {"results": [{"object": "view", "id": "view-1"}], "has_more": False}
            if path == "/views/view-1" and method == "GET":
                return {"object": "view", "id": "view-1", "type": "gallery"}
            if path == "/views" and method == "POST":
                return {"object": "view", "id": "created-view", **body}
            if path == "/views/view-1" and method == "PATCH":
                return {"object": "view", "id": "view-1", **body}
            if path == "/views/view-1" and method == "DELETE":
                return {"object": "view", "id": "view-1", "in_trash": True}
            raise AssertionError(f"unexpected request: {method} {path}")

    client = RawViewClient()
    adapter = NotionAdapter(client)

    assert adapter.list_views(data_source_id="ds-1") == [{"object": "view", "id": "view-1"}]
    assert adapter.retrieve_view("view-1") == {"object": "view", "id": "view-1", "type": "gallery"}
    assert adapter.create_view(data_source_id="ds-1", database_id="db-1", name="Episodes", view_type="gallery")["id"] == "created-view"
    assert adapter.update_view("view-1", name="Renamed")["id"] == "view-1"
    assert adapter.delete_view("view-1")["in_trash"] is True

    assert client.requests == [
        {"path": "/views", "method": "GET", "query": {"data_source_id": "ds-1"}, "body": None},
        {"path": "/views/view-1", "method": "GET", "query": None, "body": None},
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {"data_source_id": "ds-1", "database_id": "db-1", "name": "Episodes", "type": "gallery"},
        },
        {"path": "/views/view-1", "method": "PATCH", "query": None, "body": {"name": "Renamed"}},
        {"path": "/views/view-1", "method": "DELETE", "query": None, "body": None},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_views_api_methods_use_raw_request_when_sdk_has_no_views_namespace -q
```

Expected: FAIL because `NotionAdapter.list_views()` tries to access `self.client.views.list` instead of `client.request()`.

- [ ] **Step 3: Implement raw request helper and Views methods**

In `capture_to_notion/notion_adapter.py`, add this helper after `_call()`:

```python
    def _request(
        self,
        path: str,
        method: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.client.request(path, method, query=query, body=body)
        except Exception as exc:
            raise _convert_api_error(exc) from exc
```

Replace the existing Views methods with:

```python
    def list_views(self, data_source_id: str | None = None, database_id: str | None = None) -> list[dict[str, Any]]:
        scopes = [scope for scope in (database_id, data_source_id) if scope]
        if len(scopes) != 1:
            raise NotionApiError("list_views requires exactly one of database_id or data_source_id")
        query: dict[str, Any] = {}
        if data_source_id is not None:
            query["data_source_id"] = data_source_id
        if database_id is not None:
            query["database_id"] = database_id
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            page_query = dict(query)
            if start_cursor:
                page_query["start_cursor"] = start_cursor
            response = self._request("/views", "GET", query=page_query)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            start_cursor = response.get("next_cursor")

    def retrieve_view(self, view_id: str) -> dict[str, Any]:
        return self._request(f"/views/{view_id}", "GET")

    def create_view(
        self,
        *,
        data_source_id: str,
        name: str,
        view_type: str,
        database_id: str | None = None,
        view_id: str | None = None,
        create_database: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        quick_filters: dict[str, Any] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parent_scopes = [scope for scope in (database_id, view_id, create_database) if scope]
        if len(parent_scopes) != 1:
            raise NotionApiError("create_view requires exactly one of database_id, view_id, or create_database")
        body: dict[str, Any] = {"data_source_id": data_source_id, "name": name, "type": view_type}
        if database_id is not None:
            body["database_id"] = database_id
        if view_id is not None:
            body["view_id"] = view_id
        if create_database is not None:
            body["create_database"] = create_database
        if filter is not None:
            body["filter"] = filter
        if sorts is not None:
            body["sorts"] = sorts
        if quick_filters is not None:
            body["quick_filters"] = quick_filters
        if configuration is not None:
            body["configuration"] = configuration
        return self._request("/views", "POST", body=body)

    def update_view(self, view_id: str, **options: Any) -> dict[str, Any]:
        return self._request(f"/views/{view_id}", "PATCH", body=options)

    def delete_view(self, view_id: str) -> dict[str, Any]:
        return self._request(f"/views/{view_id}", "DELETE")
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_views_api_methods_use_raw_request_when_sdk_has_no_views_namespace -q
```

Expected: PASS.

- [ ] **Step 5: Run adapter regression tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/notion_adapter.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py
git commit -m "fix: route Notion views through raw API requests"
```

---

### Task 2: Scan and cache full view objects

**Files:**
- Modify: `capture_to_notion/scanner.py`
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scanner.py`:

```python
def test_scan_page_graph_retrieves_full_child_database_views(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class ViewAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                pages={"page-podcast": {"id": "page-podcast", "parent": {"type": "workspace", "workspace": True}, "properties": {}}},
                children={
                    "page-podcast": [
                        {"id": "db-episodes", "type": "child_database", "child_database": {"title": "Episodes"}},
                    ]
                },
                databases={
                    "db-episodes": {
                        "id": "db-episodes",
                        "parent": {"type": "page_id", "page_id": "page-podcast"},
                        "title": [{"plain_text": "Episodes"}],
                        "data_sources": [{"id": "ds-episodes", "name": "Episodes"}],
                    }
                },
                data_sources={
                    "ds-episodes": {
                        "id": "ds-episodes",
                        "parent": {"type": "database_id", "database_id": "db-episodes"},
                        "database_parent": {"type": "page_id", "page_id": "page-podcast"},
                        "properties": {"主题": {"id": "title", "type": "title", "title": {}}},
                    }
                },
            )
            self.retrieve_view_calls = []

        def list_views(self, database_id=None, data_source_id=None):
            assert database_id == "db-episodes"
            assert data_source_id is None
            return [{"object": "view", "id": "view-gallery"}]

        def retrieve_view(self, view_id):
            self.retrieve_view_calls.append(view_id)
            return {
                "object": "view",
                "id": view_id,
                "name": "Gallery",
                "type": "gallery",
                "database_id": "db-episodes",
                "data_source_id": "ds-episodes",
                "configuration": {"gallery": {"card_size": "medium"}},
                "sorts": [{"property": "title", "direction": "ascending"}],
                "filter": {"property": "title", "title": {"is_not_empty": True}},
                "quick_filters": {"filters": []},
            }

    adapter = ViewAdapter()
    graph = scan_page_graph(adapter, "page-podcast", store, graph_id="podcast")

    assert adapter.retrieve_view_calls == ["view-gallery"]
    assert graph["views"]["view-gallery"] == {
        "object": "view",
        "view_id": "view-gallery",
        "name": "Gallery",
        "type": "gallery",
        "database_id": "db-episodes",
        "data_source_id": "ds-episodes",
        "location": {"type": "page_id", "id": "page-podcast", "discovered_from": "page_scan"},
        "filter": {"property": "title", "title": {"is_not_empty": True}},
        "sorts": [{"property": "title", "direction": "ascending"}],
        "quick_filters": {"filters": []},
        "configuration": {"gallery": {"card_size": "medium"}},
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py::test_scan_page_graph_retrieves_full_child_database_views -q
```

Expected: FAIL because `scan_page_graph()` normalizes the list result directly and never calls `retrieve_view()`.

- [ ] **Step 3: Implement full view retrieval**

In `capture_to_notion/scanner.py`, replace `_list_views()` with:

```python
def _list_views(adapter: Any, *, database_id: str | None = None, data_source_id: str | None = None) -> list[dict[str, Any]]:
    return adapter.list_views(database_id=database_id, data_source_id=data_source_id)


def _retrieve_full_views(
    adapter: Any,
    *,
    database_id: str | None = None,
    data_source_id: str | None = None,
) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for view_ref in _list_views(adapter, database_id=database_id, data_source_id=data_source_id):
        view_id = view_ref.get("id")
        if not view_id:
            continue
        full_view = adapter.retrieve_view(str(view_id))
        views.append(full_view)
    return views
```

Then replace each loop like:

```python
for view in _list_views(adapter, database_id=database_id):
```

with:

```python
for view in _retrieve_full_views(adapter, database_id=database_id):
```

And replace:

```python
for view in _list_views(adapter, data_source_id=data_source_id):
```

with:

```python
for view in _retrieve_full_views(adapter, data_source_id=data_source_id):
```

Do not catch `AttributeError` here. If the Adapter cannot list views, scanner should fail loudly during development instead of silently caching `views: {}`.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py::test_scan_page_graph_retrieves_full_child_database_views -q
```

Expected: PASS.

- [ ] **Step 5: Run scanner tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py -q
```

Expected: PASS. If existing fake adapters lack `list_views()` and now fail, add `list_views()` returning `[]` and `retrieve_view()` raising `AssertionError` only to fake adapters used by v2 graph tests that scan child databases.

- [ ] **Step 6: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/scanner.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py
git commit -m "fix: cache full Notion view objects during scans"
```

---

### Task 3: Add view property reference remapping

**Files:**
- Create: `capture_to_notion/view_utils.py`
- Test: `tests/test_view_utils.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_view_utils.py` with:

```python
from capture_to_notion.view_utils import remap_view_property_references


def test_remap_view_property_references_updates_configuration_sorts_filter_and_quick_filters():
    source_schema = {
        "主题": {"id": "src-title", "type": "title"},
        "状态": {"id": "src-status", "type": "status"},
    }
    target_schema = {
        "主题": {"id": "dst-title", "type": "title"},
        "状态": {"id": "dst-status", "type": "status"},
    }
    view = {
        "type": "gallery",
        "configuration": {
            "properties": [
                {"property_id": "src-title", "visible": True},
                {"property_id": "src-status", "visible": False},
            ]
        },
        "sorts": [{"property": "src-status", "direction": "ascending"}],
        "filter": {
            "and": [
                {"property": "src-title", "title": {"is_not_empty": True}},
                {"property": "src-status", "status": {"equals": "已完成"}},
            ]
        },
        "quick_filters": {"filters": [{"property": "src-status", "status": {"equals": "已完成"}}]},
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"]["properties"] == [
        {"property_id": "dst-title", "visible": True},
        {"property_id": "dst-status", "visible": False},
    ]
    assert remapped["sorts"] == [{"property": "dst-status", "direction": "ascending"}]
    assert remapped["filter"] == {
        "and": [
            {"property": "dst-title", "title": {"is_not_empty": True}},
            {"property": "dst-status", "status": {"equals": "已完成"}},
        ]
    }
    assert remapped["quick_filters"] == {"filters": [{"property": "dst-status", "status": {"equals": "已完成"}}]}


def test_remap_view_property_references_drops_unmapped_property_configuration_entries():
    source_schema = {"主题": {"id": "src-title", "type": "title"}, "缺失": {"id": "src-missing", "type": "rich_text"}}
    target_schema = {"主题": {"id": "dst-title", "type": "title"}}
    view = {
        "configuration": {
            "properties": [
                {"property_id": "src-title", "visible": True},
                {"property_id": "src-missing", "visible": True},
            ]
        },
        "sorts": [{"property": "src-missing", "direction": "ascending"}],
        "filter": {"property": "src-missing", "rich_text": {"is_not_empty": True}},
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"]["properties"] == [{"property_id": "dst-title", "visible": True}]
    assert remapped["sorts"] == []
    assert remapped["filter"] == {}
    assert remapped["warnings"] == [
        {"code": "view_property_not_mapped", "source_property_id": "src-missing", "source_property_name": "缺失"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_view_utils.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'capture_to_notion.view_utils'`.

- [ ] **Step 3: Implement remap utility**

Create `capture_to_notion/view_utils.py`:

```python
from __future__ import annotations

from copy import deepcopy
from typing import Any


PROPERTY_KEYS = {"property", "property_id"}


def _property_id_to_name(schema: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, value in schema.items():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            mapping[value["id"]] = name
    return mapping


def _property_name_to_id(schema: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, value in schema.items():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            mapping[name] = value["id"]
    return mapping


def _warning(source_property_id: str, source_name: str | None) -> dict[str, Any]:
    warning = {"code": "view_property_not_mapped", "source_property_id": source_property_id}
    if source_name:
        warning["source_property_name"] = source_name
    return warning


def _dedupe_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for warning in warnings:
        key = (str(warning.get("source_property_id")), warning.get("source_property_name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(warning)
    return deduped


def remap_view_property_references(
    view: dict[str, Any],
    source_schema: dict[str, Any],
    target_schema: dict[str, Any],
) -> dict[str, Any]:
    source_id_to_name = _property_id_to_name(source_schema)
    target_name_to_id = _property_name_to_id(target_schema)
    warnings: list[dict[str, Any]] = []

    def remap_value(value: Any) -> Any:
        if isinstance(value, list):
            remapped_items = []
            for item in value:
                remapped = remap_value(item)
                if remapped is not None:
                    remapped_items.append(remapped)
            return remapped_items
        if not isinstance(value, dict):
            return value

        output: dict[str, Any] = {}
        for key, item in value.items():
            if key in PROPERTY_KEYS and isinstance(item, str):
                source_name = source_id_to_name.get(item)
                target_id = target_name_to_id.get(source_name) if source_name else None
                if not target_id:
                    warnings.append(_warning(item, source_name))
                    return None
                output[key] = target_id
                continue
            remapped_item = remap_value(item)
            if remapped_item is not None:
                output[key] = remapped_item
        return output

    remapped = deepcopy(view)
    for key in ("configuration", "sorts", "filter", "quick_filters"):
        if key in remapped:
            value = remap_value(remapped[key])
            if value is None:
                value = [] if key == "sorts" else {}
            remapped[key] = value
    remapped["warnings"] = _dedupe_warnings(warnings)
    return remapped
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_view_utils.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/view_utils.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_view_utils.py
git commit -m "feat: remap Notion view property references"
```

---

### Task 4: Create databases with cloned views

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Modify: `capture_to_notion/cli.py`
- Test: `tests/test_notion_adapter.py`
- Test: `tests/test_cli_target.py`

- [ ] **Step 1: Write failing Adapter test**

Add to `tests/test_notion_adapter.py`:

```python
def test_create_database_can_create_followup_views_with_new_data_source_id():
    class Client(FakeClient):
        def create_database(self, **kwargs):
            self.create_database_calls.append(kwargs)
            return {"id": "db-created", "data_sources": [{"id": "ds-created"}]}

    client = Client()
    adapter = NotionAdapter(client)

    result = adapter.create_database(
        "page-show",
        "单集记录",
        {"主题": {"id": "dst-title", "type": "title", "title": {}}},
        views=[
            {
                "name": "Gallery",
                "type": "gallery",
                "configuration": {"gallery": {"card_size": "medium"}},
                "sorts": [],
                "filter": {},
                "quick_filters": {},
            }
        ],
    )

    assert result["id"] == "db-created"
    assert result["created_views"] == [{"object": "view", "id": "created-view", "data_source_id": "ds-created", "database_id": "db-created", "name": "Gallery", "type": "gallery", "configuration": {"gallery": {"card_size": "medium"}}, "sorts": [], "filter": {}, "quick_filters": {}}]
    assert client.view_create_calls == [
        {
            "data_source_id": "ds-created",
            "database_id": "db-created",
            "name": "Gallery",
            "type": "gallery",
            "configuration": {"gallery": {"card_size": "medium"}},
            "sorts": [],
            "filter": {},
            "quick_filters": {},
        }
    ]
```

- [ ] **Step 2: Run Adapter test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_create_database_can_create_followup_views_with_new_data_source_id -q
```

Expected: FAIL because `create_database()` does not accept `views`.

- [ ] **Step 3: Implement optional view creation in Adapter**

Change the signature in `capture_to_notion/notion_adapter.py`:

```python
    def create_database(
        self,
        page_id: str,
        title: str,
        properties: dict[str, Any],
        views: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
```

Replace the method body with:

```python
        rich_title = [{"type": "text", "text": {"content": title}}]
        database = self._call(
            self.client.databases.create,
            parent={"type": "page_id", "page_id": page_id},
            title=rich_title,
            initial_data_source={"title": rich_title, "properties": properties},
        )
        if not views:
            return database
        data_source_id = _first_data_source_id(database)
        if not data_source_id:
            raise NotionApiError(f"Created database has no data source: {database.get('id')}")
        database_id = str(database.get("id"))
        created_views = []
        for view in views:
            created_views.append(
                self.create_view(
                    data_source_id=data_source_id,
                    database_id=database_id,
                    name=str(view.get("name") or view.get("type") or "View"),
                    view_type=str(view.get("type") or "table"),
                    filter=view.get("filter") if isinstance(view.get("filter"), dict) else None,
                    sorts=view.get("sorts") if isinstance(view.get("sorts"), list) else None,
                    quick_filters=view.get("quick_filters") if isinstance(view.get("quick_filters"), dict) else None,
                    configuration=view.get("configuration") if isinstance(view.get("configuration"), dict) else None,
                )
            )
        database["created_views"] = created_views
        return database
```

- [ ] **Step 4: Run Adapter test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_create_database_can_create_followup_views_with_new_data_source_id -q
```

Expected: PASS.

- [ ] **Step 5: Write failing CLI test for `--views`**

Add to `tests/test_cli_target.py` a test that invokes `target create-database --views`. Use the existing CLI command test style in that file. The test should create two files:

```python
schema_file.write_text(json.dumps({"properties": {"主题": {"id": "dst-title", "type": "title", "title": {}}}}, ensure_ascii=False), encoding="utf-8")
views_file.write_text(json.dumps({"views": [{"name": "Gallery", "type": "gallery", "configuration": {"gallery": {"card_size": "medium"}}}]}, ensure_ascii=False), encoding="utf-8")
```

Assert that the fake adapter receives:

```python
{
    "page_id": "page-show",
    "title": "单集记录",
    "properties": {"主题": {"id": "dst-title", "type": "title", "title": {}}},
    "views": [{"name": "Gallery", "type": "gallery", "configuration": {"gallery": {"card_size": "medium"}}}],
}
```

- [ ] **Step 6: Run CLI test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q -k "create_database and views"
```

Expected: FAIL because the parser has no `--views` argument.

- [ ] **Step 7: Implement `--views` in CLI**

In `capture_to_notion/cli.py`, add a loader near `_load_database_schema()`:

```python
def _load_database_views(path: str | None) -> list[dict[str, Any]] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("views"), list):
        return [view for view in payload["views"] if isinstance(view, dict)]
    if isinstance(payload, list):
        return [view for view in payload if isinstance(view, dict)]
    raise CliInputError("views 文件必须是 view 列表，或包含 views 列表的对象")
```

In the target create-database parser, add:

```python
create_database_parser.add_argument("--views", help="JSON file containing view specs to create after the database is created")
```

In `cmd_target_create_database()`, replace:

```python
database = adapter.create_database(args.page_id, args.title, properties)
```

with:

```python
views = _load_database_views(args.views)
database = adapter.create_database(args.page_id, args.title, properties, views=views)
```

And include created views in output:

```python
if database.get("created_views"):
    output["created_views"] = database["created_views"]
```

- [ ] **Step 8: Run CLI test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q -k "create_database and views"
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/notion_adapter.py /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py
git commit -m "feat: create Notion databases with cloned views"
```

---

### Task 5: Add block and page utility wrappers

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_notion_adapter.py`:

```python
def test_block_and_page_utility_methods_delegate_to_sdk():
    class Blocks:
        def __init__(self):
            self.retrieve_calls = []
            self.update_calls = []
            self.delete_calls = []
            self.children = types.SimpleNamespace(list=lambda **kwargs: {"results": [], "has_more": False})

        def retrieve(self, **kwargs):
            self.retrieve_calls.append(kwargs)
            return {"id": kwargs["block_id"], "type": "paragraph"}

        def update(self, **kwargs):
            self.update_calls.append(kwargs)
            return {"id": kwargs["block_id"], "archived": kwargs.get("archived")}

        def delete(self, **kwargs):
            self.delete_calls.append(kwargs)
            return {"id": kwargs["block_id"], "archived": True}

    class Pages:
        def __init__(self):
            self.move_calls = []
            self.properties = types.SimpleNamespace(retrieve=self.retrieve_property)

        def move(self, **kwargs):
            self.move_calls.append(kwargs)
            return {"id": kwargs["page_id"], "parent": kwargs["parent"]}

        def retrieve_property(self, **kwargs):
            return {"object": "property_item", "id": kwargs["property_id"]}

    class Client:
        def __init__(self):
            self.blocks = Blocks()
            self.pages = Pages()

    client = Client()
    adapter = NotionAdapter(client)

    assert adapter.retrieve_block("block-1") == {"id": "block-1", "type": "paragraph"}
    assert adapter.update_block("block-1", paragraph={"rich_text": []}) == {"id": "block-1", "archived": None}
    assert adapter.delete_block("block-1") == {"id": "block-1", "archived": True}
    assert adapter.move_page("page-1", {"type": "page_id", "page_id": "page-parent"}) == {"id": "page-1", "parent": {"type": "page_id", "page_id": "page-parent"}}
    assert adapter.retrieve_page_property("page-1", "prop-1") == {"object": "property_item", "id": "prop-1"}

    assert client.blocks.retrieve_calls == [{"block_id": "block-1"}]
    assert client.blocks.update_calls == [{"block_id": "block-1", "paragraph": {"rich_text": []}}]
    assert client.blocks.delete_calls == [{"block_id": "block-1"}]
    assert client.pages.move_calls == [{"page_id": "page-1", "parent": {"type": "page_id", "page_id": "page-parent"}}]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_block_and_page_utility_methods_delegate_to_sdk -q
```

Expected: FAIL because these methods do not exist.

- [ ] **Step 3: Implement wrappers**

Add to `capture_to_notion/notion_adapter.py` near block/page methods:

```python
    def retrieve_block(self, block_id: str) -> dict[str, Any]:
        return self._call(self.client.blocks.retrieve, block_id=block_id)

    def update_block(self, block_id: str, **payload: Any) -> dict[str, Any]:
        return self._call(self.client.blocks.update, block_id=block_id, **payload)

    def delete_block(self, block_id: str) -> dict[str, Any]:
        return self._call(self.client.blocks.delete, block_id=block_id)

    def move_page(self, page_id: str, parent: dict[str, Any]) -> dict[str, Any]:
        return self._call(self.client.pages.move, page_id=page_id, parent=parent)

    def retrieve_page_property(self, page_id: str, property_id: str) -> dict[str, Any]:
        return self._call(self.client.pages.properties.retrieve, page_id=page_id, property_id=property_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_block_and_page_utility_methods_delegate_to_sdk -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/notion_adapter.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py
git commit -m "feat: wrap block and page utility APIs"
```

---

### Task 6: Add data source, database, and file upload utility wrappers

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_notion_adapter.py`:

```python
def test_data_source_database_and_file_upload_utility_methods_delegate_to_sdk():
    class DataSources:
        def __init__(self):
            self.create_calls = []
            self.list_template_calls = []

        def create(self, **kwargs):
            self.create_calls.append(kwargs)
            return {"id": "ds-created", **kwargs}

        def list_templates(self, **kwargs):
            self.list_template_calls.append(kwargs)
            return {"results": [{"id": "template-1"}]}

    class Databases:
        def __init__(self):
            self.update_calls = []

        def update(self, **kwargs):
            self.update_calls.append(kwargs)
            return {"id": kwargs["database_id"], **kwargs}

    class FileUploads:
        def __init__(self):
            self.retrieve_calls = []
            self.list_calls = []
            self.complete_calls = []

        def retrieve(self, **kwargs):
            self.retrieve_calls.append(kwargs)
            return {"id": kwargs["file_upload_id"], "status": "uploaded"}

        def list(self, **kwargs):
            self.list_calls.append(kwargs)
            return {"results": [{"id": "upload-1"}]}

        def complete(self, **kwargs):
            self.complete_calls.append(kwargs)
            return {"id": kwargs["file_upload_id"], "status": "complete"}

    class Client:
        def __init__(self):
            self.data_sources = DataSources()
            self.databases = Databases()
            self.file_uploads = FileUploads()

    client = Client()
    adapter = NotionAdapter(client)

    assert adapter.create_data_source({"type": "page_id", "page_id": "page-1"}, "Data", {"Name": {"type": "title", "title": {}}})["id"] == "ds-created"
    assert adapter.list_data_source_templates("ds-1") == [{"id": "template-1"}]
    assert adapter.update_database("db-1", title=[{"plain_text": "New"}])["id"] == "db-1"
    assert adapter.retrieve_file_upload("upload-1") == {"id": "upload-1", "status": "uploaded"}
    assert adapter.list_file_uploads() == [{"id": "upload-1"}]
    assert adapter.complete_file_upload("upload-1") == {"id": "upload-1", "status": "complete"}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_data_source_database_and_file_upload_utility_methods_delegate_to_sdk -q
```

Expected: FAIL because these methods do not exist.

- [ ] **Step 3: Implement wrappers**

Add to `capture_to_notion/notion_adapter.py`:

```python
    def create_data_source(self, parent: dict[str, Any], title: str, properties: dict[str, Any]) -> dict[str, Any]:
        rich_title = [{"type": "text", "text": {"content": title}}]
        return self._call(self.client.data_sources.create, parent=parent, title=rich_title, properties=properties)

    def list_data_source_templates(self, data_source_id: str) -> list[dict[str, Any]]:
        response = self._call(self.client.data_sources.list_templates, data_source_id=data_source_id)
        return response.get("results", [])

    def update_database(self, database_id: str, **payload: Any) -> dict[str, Any]:
        return self._call(self.client.databases.update, database_id=database_id, **payload)

    def retrieve_file_upload(self, file_upload_id: str) -> dict[str, Any]:
        return self._call(self.client.file_uploads.retrieve, file_upload_id=file_upload_id)

    def list_file_uploads(self) -> list[dict[str, Any]]:
        response = self._call(self.client.file_uploads.list)
        return response.get("results", [])

    def complete_file_upload(self, file_upload_id: str) -> dict[str, Any]:
        return self._call(self.client.file_uploads.complete, file_upload_id=file_upload_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_data_source_database_and_file_upload_utility_methods_delegate_to_sdk -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/notion_adapter.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py
git commit -m "feat: wrap data source database and upload utility APIs"
```

---

### Task 7: Paginate query results in Adapter

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_notion_adapter.py`:

```python
def test_query_data_source_paginates_all_results():
    class DataSources:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if "start_cursor" not in kwargs:
                return {"results": [{"id": "row-1"}], "has_more": True, "next_cursor": "cursor-2"}
            return {"results": [{"id": "row-2"}], "has_more": False}

    class Client:
        def __init__(self):
            self.data_sources = DataSources()

    client = Client()
    adapter = NotionAdapter(client)

    assert adapter.query_data_source("ds-1", filters={"property": "Name", "title": {"equals": "A"}}) == [
        {"id": "row-1"},
        {"id": "row-2"},
    ]
    assert client.data_sources.calls == [
        {"data_source_id": "ds-1", "filter": {"property": "Name", "title": {"equals": "A"}}},
        {"data_source_id": "ds-1", "filter": {"property": "Name", "title": {"equals": "A"}}, "start_cursor": "cursor-2"},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_query_data_source_paginates_all_results -q
```

Expected: FAIL because `query_data_source()` returns only the first page.

- [ ] **Step 3: Implement pagination helper**

In `capture_to_notion/notion_adapter.py`, add:

```python
    def _collect_paginated(self, func: Callable[..., dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            page_kwargs = dict(kwargs)
            if start_cursor:
                page_kwargs["start_cursor"] = start_cursor
            response = self._call(func, **page_kwargs)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            start_cursor = response.get("next_cursor")
```

Replace `query_database()` with:

```python
    def query_database(self, database_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"database_id": database_id}
        if filters is not None:
            kwargs["filter"] = filters
        return self._collect_paginated(self.client.databases.query, **kwargs)
```

Replace `query_data_source()` with:

```python
    def query_data_source(self, data_source_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"data_source_id": data_source_id}
        if filters is not None:
            kwargs["filter"] = filters
        return self._collect_paginated(self.client.data_sources.query, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_query_data_source_paginates_all_results -q
```

Expected: PASS.

- [ ] **Step 5: Run adapter tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/notion_adapter.py /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py
git commit -m "fix: paginate Notion query results"
```

---

### Task 8: Add a capability inventory document for deferred raw APIs

**Files:**
- Create: `docs/notion-api-capability-inventory.md`

- [ ] **Step 1: Create inventory document**

Create `docs/notion-api-capability-inventory.md` with:

```markdown
# Notion API Capability Inventory

This inventory records Notion API capabilities that are relevant to Capture to Notion but are not all implemented in the first capability-gap pass.

## Implemented in Adapter

- Views: list, retrieve, create, update, delete via raw `client.request()`.
- Blocks: retrieve, update, delete, list children, append children.
- Pages: retrieve, create, update, move, retrieve property.
- Databases: retrieve, create, update.
- Data sources: retrieve, query, update, create, list templates.
- File uploads: create, send, complete, retrieve, list.

## Raw API candidates requiring endpoint verification before implementation

- Retrieve page as markdown.
- Update page content as markdown.
- Create view query.
- Get view query results.
- Delete view query.
- Update comment.
- Delete comment.
- List custom emojis.

## Deferred SDK-backed capabilities

- Comments create/list/retrieve.
- Users list/me/retrieve.
- OAuth token/revoke/introspect.

## Deferred event-driven capabilities

- Webhooks and webhook event handling.

## Rule

Do not implement raw API candidates from this document until the exact path, HTTP method, request shape, response shape, and Notion API version behavior have been verified against the current official documentation or a controlled API probe.
```

- [ ] **Step 2: Commit**

```bash
git add /Users/aaron/.claude/skills/capture-to-notion/docs/notion-api-capability-inventory.md
git commit -m "docs: record deferred Notion API capability gaps"
```

---

### Task 9: Run focused and full regression

**Files:**
- No source changes.

- [ ] **Step 1: Run focused tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_view_utils.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest /Users/aaron/.claude/skills/capture-to-notion/tests -q
```

Expected: PASS.

- [ ] **Step 3: Manual no-write verification for real source scan**

Run only a scan of the known source page/database. This reads Notion and updates local cache; it does not write Notion content:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion capture-to-notion target scan \
  --page-id 2fd6a715-808c-809d-9e4d-dfdd76a45d86 \
  --alias 半拿铁周刊-view-check
```

Expected: local graph cache contains at least one `views` entry whose `type` is `gallery` or another real source view type. If the source page has multiple views, all returned view ids should be present with non-empty `configuration` when the API provides it.

- [ ] **Step 4: Manual dry create plan for cloned views**

Do not write to Notion. Use a fake or test-only CLI fixture if available. If no test-only Notion target exists, skip real `target create-database` and rely on automated CLI tests.

- [ ] **Step 5: Final status report**

Report:

```text
Implemented:
- Views raw API wrapper.
- Full view scan/cache.
- View property id remapping.
- Database creation with cloned views.
- P1 SDK wrappers.
- Query pagination.

Verified:
- Focused tests passed.
- Full tests passed.
- Real source scan sees view metadata.

Not implemented in this pass:
- Markdown raw endpoints.
- View query endpoints.
- Comments/users/oauth/webhooks/custom emojis.
```

---

## Risks and Required Test Coverage

1. **Views API request shape may differ from documentation.**
   - Coverage: Adapter tests assert exact `client.request()` paths and bodies.
   - Manual check: one real read-only scan against the known 半拿铁周刊 source.

2. **View list may not include full configuration.**
   - Coverage: scanner test requires `retrieve_view()` before caching.

3. **Property id remap may drop filters/sorts when target schema differs.**
   - Coverage: `test_view_utils.py` asserts both remapped and dropped property references with warnings.

4. **Creating views after creating a database is not atomic.**
   - Coverage: Adapter returns `created_views`; CLI reports created views and warnings. If view creation fails after database creation, report the partial result instead of retrying blindly.

5. **Pagination can alter assumptions in tests that expected a single SDK call.**
   - Coverage: update affected fake clients to return `has_more: False`; run full regression.

6. **More wrappers increase Adapter surface area without direct CLI usage.**
   - Coverage: keep wrappers thin and tested only as delegation; do not wire them into write flows until a concrete workflow needs them.

---

## Execution Order

1. Task 1 — fix Views Adapter.
2. Task 2 — cache full views during scan.
3. Task 3 — remap view property references.
4. Task 4 — create cloned views with database creation.
5. Task 5 — block/page wrappers.
6. Task 6 — data source/database/file upload wrappers.
7. Task 7 — query pagination.
8. Task 8 — capability inventory document.
9. Task 9 — focused + full regression.

Do not repair existing real Notion pages or recreate 三五环 records during this implementation. This plan only develops and verifies the backend capability. Real Notion corrections require a separate preflight/plan/apply confirmation flow.
