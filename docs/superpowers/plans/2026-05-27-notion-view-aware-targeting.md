# Notion View-Aware Targeting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `capture-to-notion` from page/data-source-only targeting to a generic Notion model that understands Database → Data Source → View → Page Row relationships and safely resolves write targets when pages contain multiple data sources or views.

**Architecture:** Keep Notion access centralized in `NotionAdapter`; scanner builds a cache-first structure graph containing databases, data sources, blocks, and views; target resolution selects a concrete write data source via alias, canonical role, or user choice. View support is generic: no podcast/book/page-name logic is hardcoded; content-specific behavior comes only from cache profiles, roles, aliases, and explicit input.

**Tech Stack:** Python 3.11+, `notion-client`, `uv`, pytest, local JSON cache under `~/.config/capture-to-notion`.

---

## Non-Negotiable Constraints

- Do not use Notion MCP.
- Do not hardcode `枫言枫语`, `硅谷 101`, podcast-specific page names, or business field names in generic code.
- Do not silently write to Notion from capture flow. Keep preflight → plan → confirmation → apply.
- Do not rely on schema similarity alone when a page has multiple possible write targets.
- Preserve old target cache compatibility. Add new fields incrementally.
- Do not commit unless the user explicitly requests it during execution.

## File Structure

- Modify: `pyproject.toml` — pin `notion-client` to the current latest resolved version.
- Modify: `uv.lock` — lock dependency update.
- Modify: `capture_to_notion/config.py` — add default Notion API version config.
- Modify: `capture_to_notion/notion_adapter.py` — configure Notion version and add generic Views API methods.
- Modify: `capture_to_notion/models.py` — add optional target/display fields for view-backed write targets.
- Create: `capture_to_notion/notion_graph.py` — focused helpers for normalizing databases, data sources, views, blocks, and write candidates.
- Modify: `capture_to_notion/cache.py` — expose views and richer target details without breaking existing cache readers.
- Modify: `capture_to_notion/scanner.py` — scan and preserve views, view/data-source relationships, and canonical metadata.
- Modify: `capture_to_notion/target_resolver.py` — resolve view aliases, data-source aliases, canonical view/data-source roles, and multi-target conflicts.
- Modify: `capture_to_notion/preflight.py` — add view-aware workflow statuses and next actions.
- Modify: `capture_to_notion/planner.py` — include display view/page context in plans and summaries.
- Modify: `capture_to_notion/writer.py` — keep writes data-source-based; no view-specific write behavior.
- Modify: `capture_to_notion/verifier.py` — verify planned view still points to planned data source when view context exists.
- Modify: `capture_to_notion/cli.py` — add view inspect/list and generic role-binding commands.
- Modify: `SKILL.md` — update workflow description after behavior exists.
- Tests: add or modify focused tests in `tests/test_notion_adapter.py`, `tests/test_scanner.py`, `tests/test_preflight.py`, `tests/test_planner.py`, `tests/test_capture_apply.py`, `tests/test_cli_target.py`, `tests/test_workflow_gate.py`, and create `tests/test_notion_graph.py`.

---

## Task 1: Pin SDK Version and Configure Notion API Version

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `capture_to_notion/config.py`
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_config.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing config test for default API version**

Add to `tests/test_config.py`:

```python
def test_default_config_contains_notion_api_version(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    from capture_to_notion.config import ensure_config
    import json

    config = ensure_config()
    data = json.loads(config.config_file.read_text(encoding="utf-8"))

    assert data["notion"]["api_version"] == "2026-03-11"
```

- [ ] **Step 2: Write failing adapter test for configured Notion version**

Add to `tests/test_notion_adapter.py`:

```python
def test_adapter_from_config_passes_configured_notion_version(tmp_path, monkeypatch):
    import json
    import sys
    import types

    from capture_to_notion.config import ensure_config
    from capture_to_notion.notion_adapter import NotionAdapter

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    data = json.loads(config.config_file.read_text(encoding="utf-8"))
    data["notion"]["auth"]["token"] = "secret-token"
    data["notion"]["api_version"] = "2026-03-11"
    config.config_file.write_text(json.dumps(data), encoding="utf-8")

    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    fake_module = types.SimpleNamespace(Client=FakeClient)
    monkeypatch.setitem(sys.modules, "notion_client", fake_module)

    adapter = NotionAdapter.from_config(config)

    assert isinstance(adapter, NotionAdapter)
    assert created["auth"] == "secret-token"
    assert created["notion_version"] == "2026-03-11"
```

- [ ] **Step 3: Run tests and confirm they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_config.py::test_default_config_contains_notion_api_version \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_adapter_from_config_passes_configured_notion_version -q
```

Expected: both tests fail because `api_version` and `notion_version` are not wired yet.

- [ ] **Step 4: Implement config and adapter version wiring**

In `capture_to_notion/config.py`, update `DEFAULT_CONFIG`:

```python
DEFAULT_CONFIG = {
    "notion": {
        "auth": {"env_token_name": "NOTION_TOKEN"},
        "default_workspace": "default",
        "api_version": "2026-03-11",
    },
    "behavior": {
```

In `capture_to_notion/notion_adapter.py`, add:

```python
def notion_api_version(config: AppConfig) -> str:
    data = _config_data(config)
    version = data.get("notion", {}).get("api_version")
    return str(version) if version else "2026-03-11"
```

Update `from_config`:

```python
return cls(Client(auth=notion_token(config), notion_version=notion_api_version(config)))
```

- [ ] **Step 5: Pin latest resolved SDK version**

Run:

```bash
uv add --project /Users/aaron/.claude/skills/capture-to-notion 'notion-client@latest'
```

Then inspect `pyproject.toml`. If it still contains a range such as `notion-client>=...`, replace it with the resolved locked version from `uv.lock`, for example:

```toml
dependencies = ["notion-client==3.0.0"]
```

Run:

```bash
uv lock --project /Users/aaron/.claude/skills/capture-to-notion
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_config.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q
```

Expected: all selected tests pass.

---

## Task 2: Add Generic Views API to NotionAdapter

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing tests for views adapter methods**

Add to `tests/test_notion_adapter.py`:

```python
class FakeViewsClient:
    def __init__(self):
        self.list_calls = []
        self.retrieve_calls = []
        self.create_calls = []
        self.update_calls = []
        self.delete_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return {"results": [{"object": "view", "id": "view-1"}], "has_more": False}

    def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        return {"object": "view", "id": kwargs["view_id"], "type": "gallery"}

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"object": "view", "id": "created-view", **kwargs}

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return {"object": "view", "id": kwargs["view_id"]}

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return {"object": "view", "id": kwargs["view_id"], "in_trash": True}


class FakeClientWithViews:
    def __init__(self):
        self.views = FakeViewsClient()


def test_list_views_by_data_source_id_delegates_to_sdk():
    from capture_to_notion.notion_adapter import NotionAdapter

    client = FakeClientWithViews()
    adapter = NotionAdapter(client)

    result = adapter.list_views(data_source_id="ds-1")

    assert result == [{"object": "view", "id": "view-1"}]
    assert client.views.list_calls == [{"data_source_id": "ds-1"}]


def test_list_views_requires_one_scope():
    import pytest
    from capture_to_notion.notion_adapter import NotionAdapter, NotionApiError

    adapter = NotionAdapter(FakeClientWithViews())

    with pytest.raises(NotionApiError, match="exactly one"):
        adapter.list_views(database_id="db-1", data_source_id="ds-1")


def test_retrieve_view_delegates_to_sdk():
    from capture_to_notion.notion_adapter import NotionAdapter

    client = FakeClientWithViews()
    adapter = NotionAdapter(client)

    result = adapter.retrieve_view("view-1")

    assert result["type"] == "gallery"
    assert client.views.retrieve_calls == [{"view_id": "view-1"}]


def test_create_update_delete_view_delegate_to_sdk():
    from capture_to_notion.notion_adapter import NotionAdapter

    client = FakeClientWithViews()
    adapter = NotionAdapter(client)

    created = adapter.create_view(
        data_source_id="ds-1",
        database_id="db-1",
        name="Episodes",
        view_type="gallery",
        configuration={"gallery": {}},
    )
    updated = adapter.update_view("view-1", name="Episodes 2")
    deleted = adapter.delete_view("view-1")

    assert created["id"] == "created-view"
    assert client.views.create_calls == [
        {
            "data_source_id": "ds-1",
            "database_id": "db-1",
            "name": "Episodes",
            "type": "gallery",
            "configuration": {"gallery": {}},
        }
    ]
    assert updated["id"] == "view-1"
    assert client.views.update_calls == [{"view_id": "view-1", "name": "Episodes 2"}]
    assert deleted["in_trash"] is True
    assert client.views.delete_calls == [{"view_id": "view-1"}]
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_list_views_by_data_source_id_delegates_to_sdk \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_list_views_requires_one_scope \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_retrieve_view_delegates_to_sdk \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_create_update_delete_view_delegate_to_sdk -q
```

Expected: fail because `NotionAdapter` has no view methods.

- [ ] **Step 3: Implement view adapter methods**

Add to `capture_to_notion/notion_adapter.py` inside `NotionAdapter`:

```python
    def list_views(
        self,
        *,
        database_id: str | None = None,
        data_source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scopes = [value for value in (database_id, data_source_id) if value]
        if len(scopes) != 1:
            raise NotionApiError("list_views requires exactly one of database_id or data_source_id")
        kwargs: dict[str, Any] = {}
        if database_id:
            kwargs["database_id"] = database_id
        if data_source_id:
            kwargs["data_source_id"] = data_source_id
        response = self._call(self.client.views.list, **kwargs)
        return response.get("results", [])

    def retrieve_view(self, view_id: str) -> dict[str, Any]:
        return self._call(self.client.views.retrieve, view_id=view_id)

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
        parent_scopes = [value for value in (database_id, view_id, create_database) if value]
        if len(parent_scopes) != 1:
            raise NotionApiError("create_view requires exactly one of database_id, view_id, or create_database")
        kwargs: dict[str, Any] = {"data_source_id": data_source_id, "name": name, "type": view_type}
        if database_id:
            kwargs["database_id"] = database_id
        if view_id:
            kwargs["view_id"] = view_id
        if create_database:
            kwargs["create_database"] = create_database
        if filter is not None:
            kwargs["filter"] = filter
        if sorts is not None:
            kwargs["sorts"] = sorts
        if quick_filters is not None:
            kwargs["quick_filters"] = quick_filters
        if configuration is not None:
            kwargs["configuration"] = configuration
        return self._call(self.client.views.create, **kwargs)

    def update_view(self, view_id: str, **changes: Any) -> dict[str, Any]:
        return self._call(self.client.views.update, view_id=view_id, **changes)

    def delete_view(self, view_id: str) -> dict[str, Any]:
        return self._call(self.client.views.delete, view_id=view_id)
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q
```

Expected: pass.

---

## Task 3: Add Generic Notion Graph Normalization Helpers

**Files:**
- Create: `capture_to_notion/notion_graph.py`
- Test: `tests/test_notion_graph.py`

- [ ] **Step 1: Write failing tests for view and write-candidate normalization**

Create `tests/test_notion_graph.py`:

```python
from capture_to_notion.notion_graph import normalize_view, write_candidates_from_structure


def test_normalize_view_records_display_context_and_data_source():
    view = normalize_view(
        {
            "object": "view",
            "id": "view-1",
            "name": "Episodes",
            "type": "gallery",
            "database_id": "db-1",
            "data_source_id": "ds-1",
            "filter": {"and": []},
            "sorts": [{"property": "Name", "direction": "ascending"}],
            "configuration": {"gallery": {"card_size": "medium"}},
        },
        location_page_id="page-1",
        discovered_from="page_scan",
    )

    assert view == {
        "view_id": "view-1",
        "name": "Episodes",
        "type": "gallery",
        "database_id": "db-1",
        "data_source_id": "ds-1",
        "location_page_id": "page-1",
        "discovered_from": "page_scan",
        "filter": {"and": []},
        "sorts": [{"property": "Name", "direction": "ascending"}],
        "configuration": {"gallery": {"card_size": "medium"}},
    }


def test_write_candidates_include_view_backed_and_data_source_candidates():
    structure = {
        "data_sources": {
            "ds-1": {
                "data_source_id": "ds-1",
                "role": "episode_collection",
                "content_types": ["podcast_episode"],
                "canonical": True,
                "fields": {"title": "主题"},
            }
        },
        "views": {
            "view-1": {
                "view_id": "view-1",
                "name": "Episodes",
                "type": "gallery",
                "data_source_id": "ds-1",
                "location_page_id": "page-1",
                "role": "episode_gallery",
                "content_types": ["podcast_episode"],
                "canonical": True,
            }
        },
    }

    candidates = write_candidates_from_structure(structure, content_type="podcast_episode")

    assert candidates == [
        {
            "target_kind": "view_backed_data_source",
            "data_source_id": "ds-1",
            "view_id": "view-1",
            "view_name": "Episodes",
            "view_type": "gallery",
            "location_page_id": "page-1",
            "selection_source": "canonical_view_match",
            "canonical": True,
        },
        {
            "target_kind": "data_source",
            "data_source_id": "ds-1",
            "view_id": None,
            "view_name": None,
            "view_type": None,
            "location_page_id": None,
            "selection_source": "canonical_data_source_match",
            "canonical": True,
        },
    ]
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py -q
```

Expected: fail because `capture_to_notion.notion_graph` does not exist.

- [ ] **Step 3: Implement `notion_graph.py`**

Create `capture_to_notion/notion_graph.py`:

```python
from __future__ import annotations

from typing import Any


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def normalize_view(
    raw_view: dict[str, Any],
    *,
    location_page_id: str | None = None,
    discovered_from: str | None = None,
) -> dict[str, Any]:
    view_id = _string(raw_view.get("id")) or _string(raw_view.get("view_id"))
    if not view_id:
        raise ValueError("view id is required")
    normalized: dict[str, Any] = {
        "view_id": view_id,
        "name": _string(raw_view.get("name")),
        "type": _string(raw_view.get("type")),
        "database_id": _string(raw_view.get("database_id")),
        "data_source_id": _string(raw_view.get("data_source_id")),
        "location_page_id": location_page_id,
        "discovered_from": discovered_from,
    }
    for key in ("filter", "sorts", "configuration"):
        if key in raw_view:
            normalized[key] = raw_view[key]
    return normalized


def _matches_content_type(item: dict[str, Any], content_type: str | None) -> bool:
    if not content_type:
        return True
    raw_content_types = item.get("content_types")
    if not isinstance(raw_content_types, list):
        return False
    return content_type in raw_content_types


def write_candidates_from_structure(
    structure: dict[str, Any],
    *,
    content_type: str | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    data_sources = structure.get("data_sources", {})
    if not isinstance(data_sources, dict):
        data_sources = {}
    views = structure.get("views", {})
    if isinstance(views, dict):
        for view in views.values():
            if not isinstance(view, dict):
                continue
            if not _matches_content_type(view, content_type):
                continue
            data_source_id = _string(view.get("data_source_id"))
            if not data_source_id:
                continue
            if data_source_id not in data_sources:
                continue
            canonical = view.get("canonical") is True
            candidates.append(
                {
                    "target_kind": "view_backed_data_source",
                    "data_source_id": data_source_id,
                    "view_id": _string(view.get("view_id")),
                    "view_name": _string(view.get("name")),
                    "view_type": _string(view.get("type")),
                    "location_page_id": _string(view.get("location_page_id")),
                    "selection_source": "canonical_view_match" if canonical else "view_content_type_match",
                    "canonical": canonical,
                }
            )
    for data_source in data_sources.values():
        if not isinstance(data_source, dict):
            continue
        if not _matches_content_type(data_source, content_type):
            continue
        data_source_id = _string(data_source.get("data_source_id"))
        if not data_source_id:
            continue
        canonical = data_source.get("canonical") is True
        candidates.append(
            {
                "target_kind": "data_source",
                "data_source_id": data_source_id,
                "view_id": None,
                "view_name": None,
                "view_type": None,
                "location_page_id": None,
                "selection_source": "canonical_data_source_match" if canonical else "data_source_content_type_match",
                "canonical": canonical,
            }
        )
    return candidates
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py -q
```

Expected: pass.

---

## Task 4: Scan and Preserve Views in Target Cache

**Files:**
- Modify: `capture_to_notion/scanner.py`
- Modify: `capture_to_notion/cache.py`
- Test: `tests/test_scanner.py`
- Test: `tests/test_cli_target.py`

- [ ] **Step 1: Write failing scanner test for page views**

Add to `tests/test_scanner.py`:

```python
def test_scan_page_records_views_for_child_database(tmp_path, monkeypatch):
    from capture_to_notion.cache import CacheStore
    from capture_to_notion.config import ensure_config
    from capture_to_notion.scanner import scan_page_target

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}}

        def list_block_children(self, page_id):
            return [{"id": "db-block-1", "type": "child_database", "child_database": {"title": "Episodes"}}]

        def retrieve_database(self, database_id):
            assert database_id == "db-block-1"
            return {
                "id": "db-block-1",
                "parent": {"type": "page_id", "page_id": "page-1"},
                "data_sources": [{"id": "ds-1"}],
            }

        def retrieve_data_source(self, data_source_id):
            return {
                "id": data_source_id,
                "parent": {"type": "database_id", "database_id": "db-block-1"},
                "properties": {"主题": {"id": "title", "type": "title", "name": "主题"}},
            }

        def list_views(self, *, database_id=None, data_source_id=None):
            assert database_id == "db-block-1" or data_source_id == "ds-1"
            return [
                {
                    "object": "view",
                    "id": "view-1",
                    "name": "单集",
                    "type": "gallery",
                    "database_id": "db-block-1",
                    "data_source_id": "ds-1",
                }
            ]

    structure = scan_page_target(Adapter(), "page-1", cache, target_id="page-1")

    assert structure["views"]["view-1"]["type"] == "gallery"
    assert structure["views"]["view-1"]["data_source_id"] == "ds-1"
    assert structure["views"]["view-1"]["location_page_id"] == "page-1"
```

- [ ] **Step 2: Write failing preservation test for view roles**

Add to `tests/test_scanner.py`:

```python
def test_scan_page_preserves_cached_view_roles(tmp_path, monkeypatch):
    from capture_to_notion.cache import CacheStore
    from capture_to_notion.config import ensure_config
    from capture_to_notion.scanner import scan_page_target

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "page-1.json",
        {
            "target": {"page_id": "page-1", "target_id": "page-1"},
            "views": {
                "view-1": {
                    "view_id": "view-1",
                    "role": "episode_gallery",
                    "content_types": ["podcast_episode"],
                    "canonical": True,
                }
            },
        },
    )

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}}

        def list_block_children(self, page_id):
            return [{"id": "db-block-1", "type": "child_database", "child_database": {"title": "Episodes"}}]

        def retrieve_database(self, database_id):
            return {"id": database_id, "parent": {"type": "page_id", "page_id": "page-1"}, "data_sources": [{"id": "ds-1"}]}

        def retrieve_data_source(self, data_source_id):
            return {"id": data_source_id, "properties": {"主题": {"id": "title", "type": "title", "name": "主题"}}}

        def list_views(self, *, database_id=None, data_source_id=None):
            return [{"id": "view-1", "name": "单集", "type": "gallery", "database_id": "db-block-1", "data_source_id": "ds-1"}]

    structure = scan_page_target(Adapter(), "page-1", cache, target_id="page-1")

    assert structure["views"]["view-1"]["role"] == "episode_gallery"
    assert structure["views"]["view-1"]["content_types"] == ["podcast_episode"]
    assert structure["views"]["view-1"]["canonical"] is True
```

- [ ] **Step 3: Run tests and confirm they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py::test_scan_page_records_views_for_child_database \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py::test_scan_page_preserves_cached_view_roles -q
```

Expected: fail because scanner does not record `views`.

- [ ] **Step 4: Implement view scan merge**

In `capture_to_notion/scanner.py`:

- Import `normalize_view` from `capture_to_notion.notion_graph`.
- When scanning a database or data source, call `adapter.list_views(database_id=database_id)` or `adapter.list_views(data_source_id=data_source_id)` when available.
- Store normalized views under top-level `structure["views"]` keyed by `view_id`.
- Preserve cached view metadata keys when view IDs match:

```python
_PRESERVED_VIEW_KEYS = ("role", "content_types", "canonical", "alias")


def _merge_cached_view_profile(view: dict[str, Any], cached_view: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cached_view, dict):
        return view
    merged = dict(view)
    for key in _PRESERVED_VIEW_KEYS:
        if key in cached_view:
            merged[key] = cached_view[key]
    return merged
```

- If `adapter.list_views` raises `AttributeError`, store no views and keep old behavior.
- If Notion returns a recoverable 404/permission error for views while scanning data source schema, append a warning such as `view_scan_unavailable` instead of failing the entire target scan.

- [ ] **Step 5: Expose views in cache detail**

In `capture_to_notion/cache.py`, update `target_detail()` and `target_detail_summary()` to include:

```python
"views": [
    {
        "view_id": view.get("view_id"),
        "name": view.get("name"),
        "type": view.get("type"),
        "data_source_id": view.get("data_source_id"),
        "location_page_id": view.get("location_page_id"),
        "role": view.get("role"),
        "content_types": view.get("content_types"),
        "canonical": view.get("canonical"),
    }
]
```

- [ ] **Step 6: Run scanner/cache tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q
```

Expected: pass.

---

## Task 5: Add Generic Role Binding CLI for Data Sources and Views

**Files:**
- Modify: `capture_to_notion/cli.py`
- Modify: `capture_to_notion/cache.py` if helper methods are useful
- Test: `tests/test_cli_target.py`

- [ ] **Step 1: Write failing CLI tests for role binding**

Add to `tests/test_cli_target.py`:

```python
def test_target_bind_data_source_role_marks_canonical(tmp_path, monkeypatch, capsys):
    import json
    from capture_to_notion.cli import main
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    target_file = config.targets_dir / "target-1.json"
    target_file.write_text(
        json.dumps(
            {
                "target": {"target_id": "target-1", "page_id": "page-1"},
                "data_sources": {"ds-1": {"data_source_id": "ds-1"}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "target",
            "bind-data-source-role",
            "--target-id",
            "target-1",
            "--data-source-id",
            "ds-1",
            "--content-type",
            "podcast_episode",
            "--role",
            "episode_collection",
            "--canonical",
        ]
    )

    assert exit_code == 0
    saved = json.loads(target_file.read_text(encoding="utf-8"))
    assert saved["data_sources"]["ds-1"]["content_types"] == ["podcast_episode"]
    assert saved["data_sources"]["ds-1"]["role"] == "episode_collection"
    assert saved["data_sources"]["ds-1"]["canonical"] is True


def test_target_bind_view_role_marks_canonical(tmp_path, monkeypatch):
    import json
    from capture_to_notion.cli import main
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    target_file = config.targets_dir / "target-1.json"
    target_file.write_text(
        json.dumps(
            {
                "target": {"target_id": "target-1", "page_id": "page-1"},
                "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-1", "type": "gallery"}},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "target",
            "bind-view-role",
            "--target-id",
            "target-1",
            "--view-id",
            "view-1",
            "--content-type",
            "podcast_episode",
            "--role",
            "episode_gallery",
            "--canonical",
        ]
    )

    assert exit_code == 0
    saved = json.loads(target_file.read_text(encoding="utf-8"))
    assert saved["views"]["view-1"]["content_types"] == ["podcast_episode"]
    assert saved["views"]["view-1"]["role"] == "episode_gallery"
    assert saved["views"]["view-1"]["canonical"] is True
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py::test_target_bind_data_source_role_marks_canonical \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py::test_target_bind_view_role_marks_canonical -q
```

Expected: fail because commands do not exist.

- [ ] **Step 3: Implement role binding commands**

In `capture_to_notion/cli.py`, add handlers:

```python
def _append_unique(items: list[str], value: str) -> list[str]:
    return sorted(set(items + [value]))


def cmd_target_bind_data_source_role(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    path = config.targets_dir / f"{args.target_id}.json"
    structure = cache.read_json(path, {})
    data_sources = structure.get("data_sources")
    if not isinstance(data_sources, dict):
        raise CliInputError("target cache has no data_sources")
    data_source = data_sources.get(args.data_source_id)
    if not isinstance(data_source, dict):
        raise CliInputError(f"data_source not found in target cache: {args.data_source_id}")
    raw_content_types = data_source.get("content_types")
    content_types = raw_content_types if isinstance(raw_content_types, list) else []
    data_source["content_types"] = _append_unique([str(item) for item in content_types if isinstance(item, str)], args.content_type)
    data_source["role"] = args.role
    if args.canonical:
        data_source["canonical"] = True
    cache.write_json(path, structure)
    print_json({"target_id": args.target_id, "data_source_id": args.data_source_id, "role": args.role, "canonical": args.canonical})
    return 0


def cmd_target_bind_view_role(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    path = config.targets_dir / f"{args.target_id}.json"
    structure = cache.read_json(path, {})
    views = structure.get("views")
    if not isinstance(views, dict):
        raise CliInputError("target cache has no views")
    view = views.get(args.view_id)
    if not isinstance(view, dict):
        raise CliInputError(f"view not found in target cache: {args.view_id}")
    raw_content_types = view.get("content_types")
    content_types = raw_content_types if isinstance(raw_content_types, list) else []
    view["content_types"] = _append_unique([str(item) for item in content_types if isinstance(item, str)], args.content_type)
    view["role"] = args.role
    if args.canonical:
        view["canonical"] = True
    cache.write_json(path, structure)
    print_json({"target_id": args.target_id, "view_id": args.view_id, "role": args.role, "canonical": args.canonical})
    return 0
```

Register subcommands under `target`:

```python
bind_ds = target_subparsers.add_parser("bind-data-source-role")
bind_ds.add_argument("--target-id", required=True)
bind_ds.add_argument("--data-source-id", required=True)
bind_ds.add_argument("--content-type", required=True)
bind_ds.add_argument("--role", required=True)
bind_ds.add_argument("--canonical", action="store_true")
bind_ds.set_defaults(func=cmd_target_bind_data_source_role)

bind_view = target_subparsers.add_parser("bind-view-role")
bind_view.add_argument("--target-id", required=True)
bind_view.add_argument("--view-id", required=True)
bind_view.add_argument("--content-type", required=True)
bind_view.add_argument("--role", required=True)
bind_view.add_argument("--canonical", action="store_true")
bind_view.set_defaults(func=cmd_target_bind_view_role)
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q
```

Expected: pass.

---

## Task 6: Resolve Canonical View/Data Source Targets Generically

**Files:**
- Modify: `capture_to_notion/target_resolver.py`
- Modify: `capture_to_notion/preflight.py`
- Test: `tests/test_preflight.py`
- Test: `tests/test_workflow_gate.py`

- [ ] **Step 1: Write failing preflight test for canonical gallery selection**

Add to `tests/test_preflight.py`:

```python
def test_preflight_page_with_canonical_view_selects_view_backed_data_source(tmp_path, monkeypatch):
    from capture_to_notion.cache import CacheStore
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import CaptureInput
    from capture_to_notion.preflight import build_capture_preflight

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(config.aliases_file, {"aliases": {"Program": {"target_id": "program", "page_id": "page-1"}}})
    cache.write_json(
        config.targets_dir / "program.json",
        {
            "target": {"target_id": "program", "page_id": "page-1"},
            "data_sources": {
                "ds-old": {"data_source_id": "ds-old", "fields": {"title": "Name"}},
                "ds-episodes": {
                    "data_source_id": "ds-episodes",
                    "fields": {"title": "主题", "description": "内容描述"},
                    "content_types": ["podcast_episode"],
                    "canonical": True,
                },
            },
            "views": {
                "view-episodes": {
                    "view_id": "view-episodes",
                    "name": "Episodes",
                    "type": "gallery",
                    "data_source_id": "ds-episodes",
                    "location_page_id": "page-1",
                    "content_types": ["podcast_episode"],
                    "canonical": True,
                }
            },
        },
    )

    preflight = build_capture_preflight(
        CaptureInput(raw_input="标题：Example", target_hint="Program", content_type_hint="podcast_episode"),
        cache,
    )

    resolution = preflight["workflow"]["target_resolution"]
    assert preflight["workflow"]["planning"]["next_action"] == "capture_plan"
    assert resolution["data_source_id"] == "ds-episodes"
    assert resolution["view_id"] == "view-episodes"
    assert resolution["view_type"] == "gallery"
    assert resolution["context_verification_source"] == "canonical_view_match"
```

- [ ] **Step 2: Write failing preflight test for multiple unbound data sources**

Add to `tests/test_preflight.py`:

```python
def test_preflight_page_with_multiple_unbound_data_sources_requires_choice(tmp_path, monkeypatch):
    from capture_to_notion.cache import CacheStore
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import CaptureInput
    from capture_to_notion.preflight import build_capture_preflight

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(config.aliases_file, {"aliases": {"Program": {"target_id": "program", "page_id": "page-1"}}})
    cache.write_json(
        config.targets_dir / "program.json",
        {
            "target": {"target_id": "program", "page_id": "page-1"},
            "data_sources": {
                "ds-1": {"data_source_id": "ds-1", "fields": {"title": "主题"}},
                "ds-2": {"data_source_id": "ds-2", "fields": {"title": "主题"}},
            },
        },
    )

    preflight = build_capture_preflight(
        CaptureInput(raw_input="标题：Example", target_hint="Program", content_type_hint="podcast_episode"),
        cache,
    )

    assert preflight["workflow"]["planning"]["next_action"] == "choose_target"
    assert preflight["workflow"]["planning"]["reason"] == "multiple_write_targets"
    assert preflight["workflow"]["target_resolution"]["status"] == "multiple_write_targets"
```

- [ ] **Step 3: Run tests and confirm they fail**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py::test_preflight_page_with_canonical_view_selects_view_backed_data_source \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py::test_preflight_page_with_multiple_unbound_data_sources_requires_choice -q
```

Expected: fail because resolver/preflight do not select by canonical view or block multiple unbound candidates this way.

- [ ] **Step 4: Implement generic resolution rules**

In `capture_to_notion/target_resolver.py`:

- After alias/page cache resolution, build write candidates with `write_candidates_from_structure(structure, content_type=capture.content_type_hint)`.
- Prefer a single canonical view candidate.
- Then prefer a single canonical data source candidate.
- If no canonical exists and exactly one data source exists, preserve existing behavior.
- If more than one possible data source exists and none is canonical, return:

```python
{
    "status": "multiple_write_targets",
    "target_type": "page",
    "page_id": page_id,
    "candidates": [...],
}
```

- Include these fields when a view-backed target is selected:

```python
"view_id": candidate["view_id"],
"view_name": candidate["view_name"],
"view_type": candidate["view_type"],
"display_page_id": candidate["location_page_id"],
"context_verification_source": candidate["selection_source"],
```

In `capture_to_notion/preflight.py`:

- Treat `multiple_write_targets` as:

```python
planning = {
    "status": "needs_choice",
    "next_action": "choose_target",
    "reason": "multiple_write_targets",
}
```

- Treat canonical view/data-source matches as allowed unless existing risk gates apply.

- [ ] **Step 5: Run preflight tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py -q
```

Expected: pass.

---

## Task 7: Include View Context in Plans

**Files:**
- Modify: `capture_to_notion/models.py`
- Modify: `capture_to_notion/planner.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write failing planner test for view-backed write target summary**

Add to `tests/test_planner.py`:

```python
def test_capture_plan_includes_view_context_for_view_backed_target(tmp_path, monkeypatch):
    from capture_to_notion.cache import CacheStore
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import CaptureInput
    from capture_to_notion.planner import build_capture_plan

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(config.aliases_file, {"aliases": {"Program Episodes": {"target_id": "program", "type": "view"}}})
    cache.write_json(
        config.targets_dir / "program.json",
        {
            "target": {"target_id": "program", "page_id": "page-1"},
            "data_sources": {
                "ds-1": {
                    "data_source_id": "ds-1",
                    "fields": {"title": "主题", "description": "内容描述"},
                    "field_sources": {"title": "profile", "description": "profile"},
                    "schema": {
                        "主题": {"id": "title", "type": "title", "name": "主题"},
                        "内容描述": {"id": "desc", "type": "rich_text", "name": "内容描述"},
                    },
                    "content_types": ["podcast_episode"],
                    "canonical": True,
                }
            },
            "views": {
                "view-1": {
                    "view_id": "view-1",
                    "name": "Episodes",
                    "type": "gallery",
                    "data_source_id": "ds-1",
                    "location_page_id": "page-1",
                    "content_types": ["podcast_episode"],
                    "canonical": True,
                }
            },
            "parser_profile": {
                "podcast_episode": {
                    "labels": {"title": ["标题"], "description": ["摘要"]},
                    "trusted_field_sources": ["profile"],
                }
            },
        },
    )

    plan = build_capture_plan(
        CaptureInput(raw_input="标题：Example\n摘要：Summary", target_hint="Program Episodes", content_type_hint="podcast_episode"),
        cache,
    )

    write_target = plan.summary["write_targets"][0]
    assert write_target["target_kind"] == "view_backed_data_source"
    assert write_target["display_view_id"] == "view-1"
    assert write_target["display_view_type"] == "gallery"
    assert write_target["display_view_name"] == "Episodes"
    assert write_target["display_page_id"] == "page-1"
```

- [ ] **Step 2: Run test and confirm it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py::test_capture_plan_includes_view_context_for_view_backed_target -q
```

Expected: fail because plan summary has no view context.

- [ ] **Step 3: Extend plan target and summary construction**

In `capture_to_notion/models.py`, add optional fields to `Target`:

```python
view_id: str | None = None
view_name: str | None = None
view_type: str | None = None
display_page_id: str | None = None
```

Ensure `WritePlan.to_dict()` omits these fields when they are `None`, following the existing `target_id` compatibility pattern.

In `capture_to_notion/planner.py`:

- When resolution contains `view_id`, copy view fields into `Target`.
- In write target summary, include:

```python
"target_kind": "view_backed_data_source" if target.view_id else "data_source",
"display_page_id": target.display_page_id,
"display_view_id": target.view_id,
"display_view_name": target.view_name,
"display_view_type": target.view_type,
"selection_source": resolution.get("context_verification_source"),
```

- Keep `data_source_id` as the real write target.

- [ ] **Step 4: Run planner tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py -q
```

Expected: pass.

---

## Task 8: Verify View Integrity During Apply

**Files:**
- Modify: `capture_to_notion/cli.py`
- Modify: `capture_to_notion/verifier.py` if plan verification logic is centralized there
- Test: `tests/test_capture_apply.py`

- [ ] **Step 1: Write failing apply integrity test for view mismatch**

Add to `tests/test_capture_apply.py`:

```python
def test_apply_integrity_blocks_when_planned_view_points_to_different_data_source(tmp_path, monkeypatch):
    import json
    import pytest

    from capture_to_notion.cache import CacheStore
    from capture_to_notion.cli import CliInputError, _validate_plan_integrity
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import Target, WritePlan

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "target-1.json",
        {
            "target": {"target_id": "target-1", "page_id": "page-1"},
            "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema_hash": "hash-1"}},
            "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-2", "type": "gallery"}},
        },
    )
    plan = WritePlan(
        plan_id="plan-1",
        content_type="podcast_episode",
        target=Target(
            page_title=None,
            page_id="page-1",
            data_source_id="ds-1",
            confidence="high",
            source="view_alias",
            target_id="target-1",
            view_id="view-1",
            view_type="gallery",
            view_name="Episodes",
            display_page_id="page-1",
        ),
        normalized_record={"title": "Example"},
        field_mapping={},
        operations=[{"type": "create_or_update_page", "data_source_id": "ds-1"}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
        summary={},
    )

    with pytest.raises(CliInputError, match="view_target_mismatch"):
        _validate_plan_integrity(plan, cache)
```

- [ ] **Step 2: Run test and confirm it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py::test_apply_integrity_blocks_when_planned_view_points_to_different_data_source -q
```

Expected: fail because integrity check does not validate view/data-source relationship.

- [ ] **Step 3: Implement view integrity validation**

In `capture_to_notion/cli.py` inside `_validate_plan_integrity()`:

```python
view_id = getattr(plan.target, "view_id", None)
if view_id:
    views = target_structure.get("views", {}) if isinstance(target_structure, dict) else {}
    view = views.get(view_id) if isinstance(views, dict) else None
    if not isinstance(view, dict):
        raise CliInputError(f"plan_integrity_failed:view_missing:{view_id}")
    if view.get("data_source_id") != data_source_id:
        raise CliInputError("plan_integrity_failed:view_target_mismatch")
```

Place this after `target_structure` and `data_source_id` are resolved, before writes happen.

- [ ] **Step 4: Run apply tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py -q
```

Expected: pass.

---

## Task 9: Add View CLI Inspection Commands

**Files:**
- Modify: `capture_to_notion/cli.py`
- Test: `tests/test_cli_target.py`

- [ ] **Step 1: Write failing CLI tests for view list/inspect from cache**

Add to `tests/test_cli_target.py`:

```python
def test_target_inspect_includes_views(tmp_path, monkeypatch, capsys):
    import json
    from capture_to_notion.cli import main
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    config.aliases_file.write_text(json.dumps({"aliases": {"Program": {"target_id": "target-1"}}}), encoding="utf-8")
    (config.targets_dir / "target-1.json").write_text(
        json.dumps(
            {
                "target": {"target_id": "target-1", "page_id": "page-1"},
                "data_sources": {},
                "views": {
                    "view-1": {
                        "view_id": "view-1",
                        "name": "Episodes",
                        "type": "gallery",
                        "data_source_id": "ds-1",
                        "canonical": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["target", "inspect", "--alias", "Program"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["views"][0]["view_id"] == "view-1"
    assert output["views"][0]["type"] == "gallery"
```

- [ ] **Step 2: Run test and confirm it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py::test_target_inspect_includes_views -q
```

Expected: fail because target inspect does not show views.

- [ ] **Step 3: Implement cached view inspection**

Use the `cache.py` changes from Task 4 to include views in detail and summary output. If `cmd_target_inspect` filters fields, update it to pass through `views`.

- [ ] **Step 4: Run CLI tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q
```

Expected: pass.

---

## Task 10: Generalize Property Type Capability Classification

**Files:**
- Modify: `capture_to_notion/schema.py`
- Modify: `capture_to_notion/planner.py` if planner owns omission reasons
- Test: `tests/test_schema.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write failing schema classification test**

Add to `tests/test_schema.py`:

```python
def test_property_capability_classifies_official_notion_types():
    from capture_to_notion.schema import property_capability

    assert property_capability({"type": "title"}) == "writable"
    assert property_capability({"type": "rich_text"}) == "writable"
    assert property_capability({"type": "status"}) == "writable"
    assert property_capability({"type": "relation"}) == "writable"
    assert property_capability({"type": "created_time"}) == "read_only"
    assert property_capability({"type": "last_edited_by"}) == "read_only"
    assert property_capability({"type": "formula"}) == "computed"
    assert property_capability({"type": "rollup"}) == "computed"
    assert property_capability({"type": "place"}) == "limited"
    assert property_capability({"type": "unknown_future_type"}) == "unsupported"
```

- [ ] **Step 2: Run test and confirm it fails**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py::test_property_capability_classifies_official_notion_types -q
```

Expected: fail because `property_capability` does not exist.

- [ ] **Step 3: Implement property capability helper**

In `capture_to_notion/schema.py`, add:

```python
WRITABLE_PROPERTY_TYPES = {
    "title",
    "rich_text",
    "number",
    "select",
    "multi_select",
    "status",
    "date",
    "checkbox",
    "url",
    "email",
    "phone_number",
    "people",
    "files",
    "relation",
}
READ_ONLY_PROPERTY_TYPES = {"created_by", "created_time", "last_edited_by", "last_edited_time", "unique_id", "verification"}
COMPUTED_PROPERTY_TYPES = {"formula", "rollup"}
LIMITED_PROPERTY_TYPES = {"place"}


def property_capability(property_schema: dict[str, object]) -> str:
    property_type = property_schema.get("type")
    if property_type in WRITABLE_PROPERTY_TYPES:
        return "writable"
    if property_type in READ_ONLY_PROPERTY_TYPES:
        return "read_only"
    if property_type in COMPUTED_PROPERTY_TYPES:
        return "computed"
    if property_type in LIMITED_PROPERTY_TYPES:
        return "limited"
    return "unsupported"
```

Update planner field omission logic to use this helper when deciding whether a mapped field can be written. Read-only/computed/limited/unsupported fields must not be planned for writes.

- [ ] **Step 4: Run schema/planner tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_schema.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py -q
```

Expected: pass.

---

## Task 11: Update Skill Workflow Text After Behavior Exists

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md` or `README.zh-CN.md` only if they already document capture targeting behavior

- [ ] **Step 1: Update `SKILL.md` target rules**

After Tasks 1-10 pass, update `SKILL.md` with these behavior rules:

```markdown
### View-aware target resolution

When a Notion page contains multiple data sources or views, do not choose a target by schema similarity alone. Prefer, in order: exact view alias, exact data-source alias, canonical view role for the requested content type, canonical data-source role for the requested content type, and only then a single unambiguous writable data source. If multiple candidates remain, route to `choose_target`.

Capture writes always create or update page rows under a data source. Views are display context: a gallery/list/table view may be shown to the user and recorded in plans, but the write target remains `view.data_source_id`.
```

- [ ] **Step 2: Run documentation-sensitive tests**

Run:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py -q
```

Expected: pass.

---

## Task 12: Real Cache Migration and Non-Writing Validation

**Files:**
- No implementation files unless tests reveal gaps.
- Runtime cache files under `/Users/aaron/.config/capture-to-notion/targets/` may be updated only by explicit scan/bind commands.

- [ ] **Step 1: Scan reference targets without writing content**

Run:

```bash
capture-to-notion target scan --page-id 36c6a715-808c-8152-8941-cf2b9afbb0f7 --alias "枫言枫语"
capture-to-notion target scan --page-id 16e6a715-808c-80e3-8a74-c275de127d39 --alias "硅谷 101-reference"
```

Expected: both scans complete and target cache files include `views` if API access exposes them.

- [ ] **Step 2: Bind canonical roles using generic commands**

Use the actual view/data-source IDs from scan output. For the current known gallery data source, run:

```bash
capture-to-notion target bind-data-source-role \
  --target-id fyfy-episode-gallery \
  --data-source-id 36c6a715-808c-808c-afab-000b57546c68 \
  --content-type podcast_episode \
  --role podcast_episode_collection \
  --canonical
```

If a view ID is available from the new Views API scan, run:

```bash
capture-to-notion target bind-view-role \
  --target-id fyfy-episode-gallery \
  --view-id <actual-view-id-from-cache> \
  --content-type podcast_episode \
  --role podcast_episode_gallery \
  --canonical
```

Do not invent a view ID. If no view ID appears, stop and report that the API did not expose the view for this target.

- [ ] **Step 3: Preflight a capture without applying**

Run:

```bash
capture-to-notion capture preflight --input /tmp/capture-to-notion-fyfy-166-gallery-input.json --compact
```

Expected:

```text
next_action = capture_plan
context_verification_source = canonical_view_match or canonical_data_source_match
```

- [ ] **Step 4: Generate a plan without applying**

Run:

```bash
capture-to-notion capture plan \
  --input /tmp/capture-to-notion-fyfy-166-gallery-input.json \
  --output /tmp/capture-to-notion-fyfy-166-view-aware-regression-plan.json \
  --compact
```

Expected: plan summary includes page, data source, and view context when view is available. Do not run `capture apply` in this validation task.

---

## Final Verification Suite

Run targeted tests:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py
```

Run broader regression before claiming completion:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests
```

## Self-Review Notes

- Spec coverage: SDK/API version, Views API, scanner/cache, resolver/preflight, planner/apply, CLI binding, property capability, and real non-writing validation are covered.
- Scope: This is one coherent subsystem upgrade: view-aware target resolution for Capture to Notion. It touches many files but remains within the Notion capture backend.
- Genericity: No implementation task hardcodes a specific Notion page, program, podcast, or field name. Real IDs appear only in the final validation task.
- Execution safety: Plan forbids Notion writes during validation except explicit user-confirmed capture apply outside this plan.
- Commit policy: This plan intentionally does not require commits because the current user/project instructions say not to commit unless explicitly requested.
