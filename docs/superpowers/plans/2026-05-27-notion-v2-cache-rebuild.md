# Notion V2 Cache Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy Capture to Notion target cache with a new v2 Notion graph cache that models pages, blocks, databases, data sources, views, write profiles, and aliases according to the current Notion API model.

**Architecture:** This is a breaking cache rebuild. Runtime code must stop reading legacy `targets/*.json` as authoritative cache and must use v2 graph/profile stores only. Legacy cache deletion is an explicit implementation task; after deletion, all targets must be rebuilt by scanning Notion and binding generic write profiles.

**Tech Stack:** Python 3.11+, `notion-client` pinned to the latest resolved version, Notion API version `2026-03-11`, `uv`, pytest, JSON cache files under `~/.config/capture-to-notion`.

---

## Non-Negotiable Requirements

- This is a generic Notion model refactor, not a fix for a specific page, podcast, book, or database.
- Do not hardcode page names such as `枫言枫语` or `硅谷 101` in implementation code.
- Do not hardcode business field names such as `主题`, `状态`, or `内容描述` in generic code.
- Do not use Notion MCP.
- Do not preserve legacy cache compatibility in runtime code.
- Do not read legacy `targets/*.json` as fallback after v2 is introduced.
- Delete old cache during the implementation flow after the v2 scanner and v2 cache writer are ready.
- All future capture preflight/plan/apply paths must use v2 cache only.
- User-visible Notion writes still require preflight → plan → explicit confirmation → apply.
- Schema similarity may rank candidates, but must never silently choose among multiple write targets.
- Views are display context; writes create/update page rows under data sources.
- Database is a container; data source owns schema and rows.

---

## Final v2 Cache Layout

Use a new cache namespace:

```text
~/.config/capture-to-notion/
├─ config.json
├─ states.json
├─ cache-v2/
│  ├─ aliases.json
│  ├─ graphs/
│  │  └─ <graph_id>.json
│  ├─ profiles/
│  │  └─ <profile_id>.json
│  ├─ plans/
│  │  └─ <plan_id>.json
│  └─ assets/
└─ legacy cache paths removed during reset
```

Remove runtime dependence on:

```text
~/.config/capture-to-notion/aliases.json
~/.config/capture-to-notion/routes.json
~/.config/capture-to-notion/targets/*.json
~/.config/capture-to-notion/plans/*.json
```

The old files may exist before the reset task, but v2 runtime must not read them.

---

## v2 Graph File Shape

`cache-v2/graphs/<graph_id>.json`:

```json
{
  "cache_version": 2,
  "graph_id": "graph-id",
  "root": {
    "kind": "page",
    "id": "page-id"
  },
  "notion": {
    "api_version": "2026-03-11",
    "scanned_at": "2026-05-27T00:00:00Z"
  },
  "pages": {},
  "blocks": {},
  "databases": {},
  "data_sources": {},
  "views": {}
}
```

### Page Node

```json
{
  "object": "page",
  "page_id": "page-id",
  "kind": "container_page",
  "title": "Page title",
  "parent": {"type": "data_source_id", "id": "data-source-id"},
  "property_values": {},
  "block_ids": []
}
```

`kind` is either:

```text
container_page
record_page
unknown_page
```

### Block Node

```json
{
  "object": "block",
  "block_id": "block-id",
  "type": "child_database",
  "parent_page_id": "page-id",
  "has_children": false,
  "child_database": {"title": "Episodes"}
}
```

### Database Node

```json
{
  "object": "database",
  "database_id": "database-id",
  "title": "Database title",
  "parent": {"type": "page_id", "id": "page-id"},
  "is_inline": true,
  "data_source_ids": ["data-source-id"],
  "view_ids": ["view-id"]
}
```

### Data Source Node

```json
{
  "object": "data_source",
  "data_source_id": "data-source-id",
  "database_id": "database-id",
  "title": "Data source title",
  "parent": {"type": "database_id", "id": "database-id"},
  "database_parent": {"type": "page_id", "id": "page-id"},
  "schema": {},
  "schema_hash": "hash",
  "property_capabilities": {
    "Name": "writable"
  },
  "queryable": true,
  "writable": true
}
```

### View Node

```json
{
  "object": "view",
  "view_id": "view-id",
  "name": "Episodes",
  "type": "gallery",
  "database_id": "database-id",
  "data_source_id": "data-source-id",
  "location": {
    "type": "page_id",
    "id": "page-id",
    "discovered_from": "page_scan"
  },
  "filter": {},
  "sorts": [],
  "quick_filters": {},
  "configuration": {}
}
```

---

## v2 Profile File Shape

`cache-v2/profiles/<profile_id>.json`:

```json
{
  "cache_version": 2,
  "profile_id": "profile-id",
  "graph_id": "graph-id",
  "aliases": ["Human readable alias"],
  "write_profiles": {
    "podcast_episode": {
      "content_type": "podcast_episode",
      "canonical_view_id": "view-id",
      "canonical_data_source_id": "data-source-id",
      "field_mapping": {
        "title": "Name"
      },
      "field_sources": {
        "title": "user_binding"
      },
      "state_mapping": {},
      "asset_mapping": {},
      "relation_mapping": {},
      "parser_profile": {}
    }
  }
}
```

Implementation code must treat `content_type` as a string key and not hardcode its values.

---

## File Structure

- Modify: `pyproject.toml` — pin latest resolved `notion-client`.
- Modify: `uv.lock` — lock dependency version.
- Modify: `capture_to_notion/config.py` — add v2 cache paths and Notion API version default.
- Modify: `capture_to_notion/notion_adapter.py` — explicit Notion API version and Views API.
- Create: `capture_to_notion/notion_graph.py` — v2 graph node normalization and property capability helpers.
- Create: `capture_to_notion/cache_v2.py` — v2 graph/profile/alias/plan store. Does not read v1 files.
- Create: `capture_to_notion/profile_binder.py` — generic write-profile binding and validation.
- Modify: `capture_to_notion/scanner.py` — output v2 graph instead of target cache for new scan paths.
- Modify: `capture_to_notion/target_resolver.py` — resolve using v2 alias + graph + profile only.
- Modify: `capture_to_notion/preflight.py` — v2-only workflow gates.
- Modify: `capture_to_notion/planner.py` — build plans from v2 write target resolution.
- Modify: `capture_to_notion/models.py` — add v2 target/display/write target fields.
- Modify: `capture_to_notion/writer.py` — keep writes data-source-based.
- Modify: `capture_to_notion/verifier.py` — verify page row and optional view→data_source integrity.
- Modify: `capture_to_notion/cli.py` — add v2 scan/bind/reset/inspect commands and switch capture commands to v2 store.
- Modify: `SKILL.md` — update workflow to say v2 cache only.
- Tests: add `tests/test_cache_v2.py`, `tests/test_notion_graph.py`, `tests/test_profile_binder.py`; update existing adapter/scanner/preflight/planner/apply/CLI tests.

---

## Task 1: Pin SDK and Configure API Version

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `capture_to_notion/config.py`
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_config.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Write failing config test**

Add to `tests/test_config.py`:

```python
def test_default_config_contains_v2_cache_paths_and_notion_api_version(tmp_path, monkeypatch):
    import json
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    config = ensure_config()
    data = json.loads(config.config_file.read_text(encoding="utf-8"))

    assert data["notion"]["api_version"] == "2026-03-11"
    assert config.cache_v2_dir == tmp_path / "cache-v2"
    assert config.graphs_v2_dir == tmp_path / "cache-v2" / "graphs"
    assert config.profiles_v2_dir == tmp_path / "cache-v2" / "profiles"
    assert config.aliases_v2_file == tmp_path / "cache-v2" / "aliases.json"
    assert config.plans_v2_dir == tmp_path / "cache-v2" / "plans"
```

- [ ] **Step 2: Write failing adapter version test**

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

    monkeypatch.setitem(sys.modules, "notion_client", types.SimpleNamespace(Client=FakeClient))

    adapter = NotionAdapter.from_config(config)

    assert isinstance(adapter, NotionAdapter)
    assert created == {"auth": "secret-token", "notion_version": "2026-03-11"}
```

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_config.py::test_default_config_contains_v2_cache_paths_and_notion_api_version \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_adapter_from_config_passes_configured_notion_version -q
```

Expected: fail.

- [ ] **Step 4: Implement config changes**

In `capture_to_notion/config.py`, change `DEFAULT_CONFIG` notion block:

```python
"notion": {
    "auth": {"env_token_name": "NOTION_TOKEN"},
    "default_workspace": "default",
    "api_version": "2026-03-11",
},
```

Extend `AppConfig`:

```python
cache_v2_dir: Path
graphs_v2_dir: Path
profiles_v2_dir: Path
plans_v2_dir: Path
aliases_v2_file: Path
```

In `ensure_config()` add:

```python
cache_v2_dir = root / "cache-v2"
graphs_v2_dir = cache_v2_dir / "graphs"
profiles_v2_dir = cache_v2_dir / "profiles"
plans_v2_dir = cache_v2_dir / "plans"
aliases_v2_file = cache_v2_dir / "aliases.json"
```

Include these paths in the mkdir loop:

```python
cache_v2_dir,
graphs_v2_dir,
profiles_v2_dir,
plans_v2_dir,
cache_v2_dir / "assets",
```

Call:

```python
write_json_if_missing(aliases_v2_file, {"cache_version": 2, "aliases": {}})
```

Return them in `AppConfig(...)`.

- [ ] **Step 5: Implement adapter version config**

In `capture_to_notion/notion_adapter.py`, add:

```python
def notion_api_version(config: AppConfig) -> str:
    data = _config_data(config)
    version = data.get("notion", {}).get("api_version")
    return str(version) if version else "2026-03-11"
```

Update:

```python
return cls(Client(auth=notion_token(config), notion_version=notion_api_version(config)))
```

- [ ] **Step 6: Pin latest SDK**

Run:

```bash
uv add --project /Users/aaron/.claude/skills/capture-to-notion 'notion-client@latest'
```

Then pin exact version in `pyproject.toml`. If `uv.lock` resolves `notion-client 3.0.0`, use:

```toml
dependencies = ["notion-client==3.0.0"]
```

Run:

```bash
uv lock --project /Users/aaron/.claude/skills/capture-to-notion
```

- [ ] **Step 7: Run tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_config.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q
```

Expected: pass.

---

## Task 2: Implement Views API in Adapter

**Files:**
- Modify: `capture_to_notion/notion_adapter.py`
- Test: `tests/test_notion_adapter.py`

- [ ] **Step 1: Add failing Views API tests**

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


def test_views_api_methods_delegate_to_sdk():
    from capture_to_notion.notion_adapter import NotionAdapter

    client = FakeClientWithViews()
    adapter = NotionAdapter(client)

    assert adapter.list_views(data_source_id="ds-1") == [{"object": "view", "id": "view-1"}]
    assert adapter.retrieve_view("view-1")["type"] == "gallery"
    assert adapter.create_view(data_source_id="ds-1", database_id="db-1", name="Episodes", view_type="gallery")["id"] == "created-view"
    assert adapter.update_view("view-1", name="New name")["id"] == "view-1"
    assert adapter.delete_view("view-1")["in_trash"] is True

    assert client.views.list_calls == [{"data_source_id": "ds-1"}]
    assert client.views.retrieve_calls == [{"view_id": "view-1"}]
    assert client.views.create_calls == [{"data_source_id": "ds-1", "database_id": "db-1", "name": "Episodes", "type": "gallery"}]
    assert client.views.update_calls == [{"view_id": "view-1", "name": "New name"}]
    assert client.views.delete_calls == [{"view_id": "view-1"}]
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py::test_views_api_methods_delegate_to_sdk -q
```

Expected: fail.

- [ ] **Step 3: Implement Views API methods**

Add inside `NotionAdapter`:

```python
    def list_views(
        self,
        *,
        database_id: str | None = None,
        data_source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scopes = [scope for scope in (database_id, data_source_id) if scope]
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
        parent_scopes = [scope for scope in (database_id, view_id, create_database) if scope]
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

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py -q
```

Expected: pass.

---

## Task 3: Add v2 Graph Normalization

**Files:**
- Create: `capture_to_notion/notion_graph.py`
- Test: `tests/test_notion_graph.py`

- [ ] **Step 1: Create failing graph tests**

Create `tests/test_notion_graph.py`:

```python
from capture_to_notion.notion_graph import (
    normalize_database,
    normalize_data_source,
    normalize_page,
    normalize_view,
    property_capability,
    schema_hash,
)


def test_property_capability_classifies_official_types():
    assert property_capability({"type": "title"}) == "writable"
    assert property_capability({"type": "rich_text"}) == "writable"
    assert property_capability({"type": "relation"}) == "writable"
    assert property_capability({"type": "created_time"}) == "read_only"
    assert property_capability({"type": "formula"}) == "computed"
    assert property_capability({"type": "rollup"}) == "computed"
    assert property_capability({"type": "place"}) == "limited"
    assert property_capability({"type": "unknown"}) == "unsupported"


def test_normalize_database_records_data_sources():
    database = normalize_database(
        {
            "id": "db-1",
            "title": [{"plain_text": "Episodes"}],
            "parent": {"type": "page_id", "page_id": "page-1"},
            "is_inline": True,
            "data_sources": [{"id": "ds-1"}],
        }
    )

    assert database["database_id"] == "db-1"
    assert database["title"] == "Episodes"
    assert database["parent"] == {"type": "page_id", "id": "page-1"}
    assert database["is_inline"] is True
    assert database["data_source_ids"] == ["ds-1"]


def test_normalize_data_source_records_schema_and_capabilities():
    data_source = normalize_data_source(
        {
            "id": "ds-1",
            "title": [{"plain_text": "Rows"}],
            "parent": {"type": "database_id", "database_id": "db-1"},
            "database_parent": {"type": "page_id", "page_id": "page-1"},
            "properties": {
                "Name": {"id": "title", "type": "title", "name": "Name"},
                "Created": {"id": "c", "type": "created_time", "name": "Created"},
            },
        }
    )

    assert data_source["data_source_id"] == "ds-1"
    assert data_source["database_id"] == "db-1"
    assert data_source["database_parent"] == {"type": "page_id", "id": "page-1"}
    assert data_source["property_capabilities"] == {"Name": "writable", "Created": "read_only"}
    assert data_source["schema_hash"] == schema_hash(data_source["schema"])


def test_normalize_view_records_display_context():
    view = normalize_view(
        {
            "id": "view-1",
            "name": "Episodes",
            "type": "gallery",
            "database_id": "db-1",
            "data_source_id": "ds-1",
            "filter": {"and": []},
            "sorts": [],
            "configuration": {"gallery": {}},
        },
        location={"type": "page_id", "id": "page-1", "discovered_from": "page_scan"},
    )

    assert view["view_id"] == "view-1"
    assert view["type"] == "gallery"
    assert view["data_source_id"] == "ds-1"
    assert view["location"] == {"type": "page_id", "id": "page-1", "discovered_from": "page_scan"}


def test_normalize_page_distinguishes_record_page():
    page = normalize_page(
        {
            "id": "page-row-1",
            "parent": {"type": "data_source_id", "data_source_id": "ds-1"},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Row"}]}}
        }
    )

    assert page["kind"] == "record_page"
    assert page["title"] == "Row"
    assert page["parent"] == {"type": "data_source_id", "id": "ds-1"}
```

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement graph helpers**

Create `capture_to_notion/notion_graph.py` with functions matching the tests. Include these property sets:

```python
WRITABLE_PROPERTY_TYPES = {"title", "rich_text", "number", "select", "multi_select", "status", "date", "checkbox", "url", "email", "phone_number", "people", "files", "relation"}
READ_ONLY_PROPERTY_TYPES = {"created_by", "created_time", "last_edited_by", "last_edited_time", "unique_id", "verification"}
COMPUTED_PROPERTY_TYPES = {"formula", "rollup"}
LIMITED_PROPERTY_TYPES = {"place"}
```

Implement stable `schema_hash()` by JSON-dumping schema with `sort_keys=True` and hashing with SHA-256 truncated to 16 hex chars.

- [ ] **Step 4: Run graph tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py -q
```

Expected: pass.

---

## Task 4: Implement v2 Cache Store Only

**Files:**
- Create: `capture_to_notion/cache_v2.py`
- Test: `tests/test_cache_v2.py`

- [ ] **Step 1: Write failing v2 cache tests**

Create `tests/test_cache_v2.py`:

```python
from capture_to_notion.cache_v2 import CacheV2Store


def test_v2_store_writes_and_reads_graph(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    graph = {"cache_version": 2, "graph_id": "graph-1", "root": {"kind": "page", "id": "page-1"}}
    store.write_graph("graph-1", graph)

    assert store.read_graph("graph-1") == graph
    assert not (tmp_path / "targets" / "graph-1.json").exists()


def test_v2_store_aliases_do_not_read_legacy_aliases(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    config.aliases_file.write_text('{"aliases":{"Old":{"target_id":"legacy"}}}', encoding="utf-8")

    store = CacheV2Store(config)

    assert store.aliases() == {}


def test_v2_store_binds_alias_to_graph_and_profile(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    assert store.find_alias("Program") == {"graph_id": "graph-1", "profile_id": "profile-1", "kind": "page"}
```

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cache_v2.py -q
```

Expected: fail.

- [ ] **Step 3: Implement `CacheV2Store`**

Create `capture_to_notion/cache_v2.py`:

```python
from __future__ import annotations

import json
from typing import Any

from capture_to_notion.config import AppConfig


class CacheV2Store:
    def __init__(self, config: AppConfig):
        self.config = config

    def read_json(self, path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
        return data if isinstance(data, dict) else default

    def write_json(self, path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def graph_path(self, graph_id: str):
        return self.config.graphs_v2_dir / f"{graph_id}.json"

    def profile_path(self, profile_id: str):
        return self.config.profiles_v2_dir / f"{profile_id}.json"

    def plan_path(self, plan_id: str):
        return self.config.plans_v2_dir / f"{plan_id}.json"

    def read_graph(self, graph_id: str) -> dict[str, Any] | None:
        data = self.read_json(self.graph_path(graph_id), {})
        if data.get("cache_version") != 2:
            return None
        return data

    def write_graph(self, graph_id: str, graph: dict[str, Any]) -> None:
        graph = dict(graph)
        graph["cache_version"] = 2
        graph["graph_id"] = graph_id
        self.write_json(self.graph_path(graph_id), graph)

    def read_profile(self, profile_id: str) -> dict[str, Any] | None:
        data = self.read_json(self.profile_path(profile_id), {})
        if data.get("cache_version") != 2:
            return None
        return data

    def write_profile(self, profile_id: str, profile: dict[str, Any]) -> None:
        profile = dict(profile)
        profile["cache_version"] = 2
        profile["profile_id"] = profile_id
        self.write_json(self.profile_path(profile_id), profile)

    def aliases(self) -> dict[str, Any]:
        data = self.read_json(self.config.aliases_v2_file, {"cache_version": 2, "aliases": {}})
        aliases = data.get("aliases")
        return aliases if isinstance(aliases, dict) else {}

    def find_alias(self, alias: str | None) -> dict[str, Any] | None:
        if not alias:
            return None
        value = self.aliases().get(alias)
        return value if isinstance(value, dict) else None

    def bind_alias(self, alias: str, *, graph_id: str, profile_id: str | None, kind: str) -> None:
        data = self.read_json(self.config.aliases_v2_file, {"cache_version": 2, "aliases": {}})
        aliases = data.setdefault("aliases", {})
        aliases[alias] = {"graph_id": graph_id, "profile_id": profile_id, "kind": kind}
        data["cache_version"] = 2
        self.write_json(self.config.aliases_v2_file, data)
```

- [ ] **Step 4: Run cache tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cache_v2.py -q
```

Expected: pass.

---

## Task 5: Implement Profile Binder

**Files:**
- Create: `capture_to_notion/profile_binder.py`
- Test: `tests/test_profile_binder.py`

- [ ] **Step 1: Write failing profile binder tests**

Create `tests/test_profile_binder.py`:

```python
from capture_to_notion.profile_binder import bind_write_profile, resolve_write_profile


def test_bind_write_profile_requires_data_source_in_graph():
    import pytest

    graph = {"data_sources": {}, "views": {}}

    with pytest.raises(ValueError, match="data_source not found"):
        bind_write_profile(
            graph,
            profile_id="program",
            content_type="podcast_episode",
            data_source_id="missing",
            view_id=None,
            field_mapping={"title": "Name"},
            field_sources={"title": "user_binding"},
        )


def test_bind_write_profile_records_view_and_data_source():
    graph = {
        "graph_id": "graph-1",
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-1", "type": "gallery"}},
    }

    profile = bind_write_profile(
        graph,
        profile_id="program",
        content_type="podcast_episode",
        data_source_id="ds-1",
        view_id="view-1",
        field_mapping={"title": "Name"},
        field_sources={"title": "user_binding"},
    )

    write_profile = profile["write_profiles"]["podcast_episode"]
    assert profile["cache_version"] == 2
    assert profile["graph_id"] == "graph-1"
    assert write_profile["canonical_data_source_id"] == "ds-1"
    assert write_profile["canonical_view_id"] == "view-1"
    assert write_profile["field_mapping"] == {"title": "Name"}


def test_resolve_write_profile_returns_view_backed_target():
    graph = {
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-1", "type": "gallery", "name": "Episodes"}},
    }
    profile = {
        "write_profiles": {
            "podcast_episode": {
                "canonical_data_source_id": "ds-1",
                "canonical_view_id": "view-1",
                "field_mapping": {"title": "Name"},
                "field_sources": {"title": "user_binding"},
            }
        }
    }

    resolved = resolve_write_profile(graph, profile, content_type="podcast_episode")

    assert resolved["target_kind"] == "view_backed_data_source"
    assert resolved["data_source_id"] == "ds-1"
    assert resolved["view_id"] == "view-1"
    assert resolved["view_type"] == "gallery"
    assert resolved["selection_source"] == "write_profile"
```

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_profile_binder.py -q
```

Expected: fail.

- [ ] **Step 3: Implement profile binder**

Create `capture_to_notion/profile_binder.py` implementing `bind_write_profile()` and `resolve_write_profile()` exactly for the tested behavior. Keep it generic: content type is a string key, field mappings are passed in, and no page names or business fields are hardcoded.

- [ ] **Step 4: Run profile tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_profile_binder.py -q
```

Expected: pass.

---

## Task 6: Rewrite Scanner to Produce v2 Graphs

**Files:**
- Modify: `capture_to_notion/scanner.py`
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Add failing v2 scanner test**

Add to `tests/test_scanner.py`:

```python
def test_scan_page_target_writes_v2_graph(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.config import ensure_config
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}, "properties": {}}

        def list_block_children(self, block_id):
            return [{"id": "db-1", "type": "child_database", "child_database": {"title": "Episodes"}, "has_children": False}]

        def retrieve_database(self, database_id):
            return {"id": database_id, "title": [{"plain_text": "Episodes"}], "parent": {"type": "page_id", "page_id": "page-1"}, "is_inline": True, "data_sources": [{"id": "ds-1"}]}

        def retrieve_data_source(self, data_source_id):
            return {"id": data_source_id, "title": [{"plain_text": "Rows"}], "parent": {"type": "database_id", "database_id": "db-1"}, "database_parent": {"type": "page_id", "page_id": "page-1"}, "properties": {"Name": {"id": "title", "name": "Name", "type": "title"}}}

        def list_views(self, *, database_id=None, data_source_id=None):
            return [{"id": "view-1", "name": "Episodes", "type": "gallery", "database_id": "db-1", "data_source_id": "ds-1"}]

    graph = scan_page_graph(Adapter(), "page-1", store, graph_id="graph-1")

    assert graph["cache_version"] == 2
    assert graph["root"] == {"kind": "page", "id": "page-1"}
    assert "page-1" in graph["pages"]
    assert "db-1" in graph["databases"]
    assert "ds-1" in graph["data_sources"]
    assert graph["views"]["view-1"]["type"] == "gallery"
    assert store.read_graph("graph-1")["views"]["view-1"]["data_source_id"] == "ds-1"
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py::test_scan_page_target_writes_v2_graph -q
```

Expected: fail because `scan_page_graph` does not exist.

- [ ] **Step 3: Implement `scan_page_graph`**

In `scanner.py`, add v2 scanner functions alongside old functions initially:

```python
def scan_page_graph(adapter, page_id: str, store: CacheV2Store, *, graph_id: str) -> dict[str, Any]:
    ...
```

Implementation must:

1. retrieve page;
2. normalize page;
3. list direct child blocks;
4. store normalized blocks;
5. for each `child_database` block, retrieve database;
6. normalize database;
7. retrieve each database `data_sources[].id`;
8. normalize data source;
9. list views by database or data source;
10. normalize views with location `{type: "page_id", id: page_id, discovered_from: "page_scan"}`;
11. write the graph through `CacheV2Store.write_graph()`.

- [ ] **Step 4: Run scanner tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py -q
```

Expected: pass.

---

## Task 7: Switch Target Resolution to v2 Only

**Files:**
- Modify: `capture_to_notion/target_resolver.py`
- Modify: `capture_to_notion/preflight.py`
- Test: `tests/test_preflight.py`
- Test: `tests/test_workflow_gate.py`

- [ ] **Step 1: Write failing test that v1 aliases are ignored**

Add to `tests/test_preflight.py`:

```python
def test_preflight_ignores_legacy_aliases_and_requires_v2_target(tmp_path, monkeypatch):
    import json
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.cache import CacheStore
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import CaptureInput
    from capture_to_notion.preflight import build_capture_preflight

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    legacy = CacheStore(config)
    legacy.write_json(config.aliases_file, {"aliases": {"Program": {"target_id": "legacy"}}})
    legacy.write_json(config.targets_dir / "legacy.json", {"target": {"page_id": "page-legacy"}, "data_sources": {"ds-legacy": {"data_source_id": "ds-legacy"}}})

    preflight = build_capture_preflight(
        CaptureInput(raw_input="标题：Example", target_hint="Program", content_type_hint="podcast_episode"),
        CacheV2Store(config),
    )

    assert preflight["workflow"]["planning"]["next_action"] == "scan_target"
    assert preflight["workflow"]["target_resolution"]["status"] == "v2_target_missing"
```

- [ ] **Step 2: Write failing test for v2 profile resolution**

Add to `tests/test_preflight.py`:

```python
def test_preflight_resolves_v2_profile_to_view_backed_data_source(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import CaptureInput
    from capture_to_notion.preflight import build_capture_preflight

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    graph = {
        "cache_version": 2,
        "graph_id": "graph-1",
        "root": {"kind": "page", "id": "page-1"},
        "pages": {},
        "blocks": {},
        "databases": {},
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "type": "gallery", "name": "Episodes", "data_source_id": "ds-1"}},
    }
    profile = {
        "cache_version": 2,
        "profile_id": "profile-1",
        "graph_id": "graph-1",
        "write_profiles": {
            "podcast_episode": {
                "canonical_data_source_id": "ds-1",
                "canonical_view_id": "view-1",
                "field_mapping": {"title": "Name"},
                "field_sources": {"title": "user_binding"},
            }
        },
    }
    store.write_graph("graph-1", graph)
    store.write_profile("profile-1", profile)
    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    preflight = build_capture_preflight(
        CaptureInput(raw_input="标题：Example", target_hint="Program", content_type_hint="podcast_episode"),
        store,
    )

    resolution = preflight["workflow"]["target_resolution"]
    assert preflight["workflow"]["planning"]["next_action"] == "capture_plan"
    assert resolution["data_source_id"] == "ds-1"
    assert resolution["view_id"] == "view-1"
    assert resolution["context_verification_source"] == "write_profile"
```

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py::test_preflight_ignores_legacy_aliases_and_requires_v2_target \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py::test_preflight_resolves_v2_profile_to_view_backed_data_source -q
```

Expected: fail.

- [ ] **Step 4: Implement v2 resolver path**

Refactor `target_resolver.py` so capture preflight uses `CacheV2Store` APIs:

1. look up `target_hint` in `cache-v2/aliases.json` only;
2. if alias missing, return `status: v2_target_missing`;
3. load graph by alias `graph_id`;
4. load profile by alias `profile_id`;
5. resolve write profile by content type using `resolve_write_profile()`;
6. if no profile for content type, return `status: write_profile_missing`;
7. if resolved, return data_source/view/display fields.

Refactor `preflight.py` to accept `CacheV2Store`. Remove fallback to `CacheStore` from capture path.

- [ ] **Step 5: Run preflight and workflow tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py -q
```

Expected: pass after updating old tests to v2 fixtures. Delete or rewrite tests that assert v1 runtime fallback.

---

## Task 8: Switch Planner to v2 Write Profiles

**Files:**
- Modify: `capture_to_notion/planner.py`
- Modify: `capture_to_notion/models.py`
- Test: `tests/test_planner.py`

- [ ] **Step 1: Write failing v2 planner test**

Add to `tests/test_planner.py`:

```python
def test_v2_plan_uses_write_profile_and_view_context(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import CaptureInput
    from capture_to_notion.planner import build_capture_plan

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph("graph-1", {
        "cache_version": 2,
        "graph_id": "graph-1",
        "root": {"kind": "page", "id": "page-1"},
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"id": "title", "type": "title", "name": "Name"}, "Summary": {"id": "s", "type": "rich_text", "name": "Summary"}}}},
        "views": {"view-1": {"view_id": "view-1", "name": "Episodes", "type": "gallery", "data_source_id": "ds-1"}},
    })
    store.write_profile("profile-1", {
        "cache_version": 2,
        "profile_id": "profile-1",
        "graph_id": "graph-1",
        "write_profiles": {"podcast_episode": {"canonical_data_source_id": "ds-1", "canonical_view_id": "view-1", "field_mapping": {"title": "Name", "description": "Summary"}, "field_sources": {"title": "user_binding", "description": "user_binding"}}},
    })
    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    plan = build_capture_plan(
        CaptureInput(raw_input="标题：Example\n摘要：Summary text", target_hint="Program", content_type_hint="podcast_episode"),
        store,
    )

    assert plan.target.data_source_id == "ds-1"
    assert plan.target.view_id == "view-1"
    write_target = plan.summary["write_targets"][0]
    assert write_target["target_kind"] == "view_backed_data_source"
    assert write_target["display_view_type"] == "gallery"
    assert write_target["data_source_id"] == "ds-1"
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py::test_v2_plan_uses_write_profile_and_view_context -q
```

Expected: fail.

- [ ] **Step 3: Implement v2 planner path**

Update `build_capture_plan()` to use v2 store methods and write profile mapping. The planner must:

1. call v2 target resolver;
2. retrieve graph/profile;
3. get field mapping from write profile;
4. build Notion properties from the selected data source schema;
5. set `Target.data_source_id`, optional `Target.view_id`, `Target.view_name`, `Target.view_type`, and `Target.display_page_id`;
6. include view context in `summary.write_targets`.

Remove v1 target cache fallback from `build_capture_plan()`.

- [ ] **Step 4: Run planner tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py -q
```

Expected: pass after v2 fixture updates.

---

## Task 9: Switch Apply Integrity to v2 Only

**Files:**
- Modify: `capture_to_notion/cli.py`
- Modify: `capture_to_notion/verifier.py`
- Test: `tests/test_capture_apply.py`

- [ ] **Step 1: Write failing v2 apply integrity test**

Add to `tests/test_capture_apply.py`:

```python
def test_v2_apply_integrity_blocks_view_data_source_mismatch(tmp_path, monkeypatch):
    import pytest
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.cli import CliInputError, _validate_plan_integrity
    from capture_to_notion.config import ensure_config
    from capture_to_notion.models import Target, WritePlan

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph("graph-1", {
        "cache_version": 2,
        "graph_id": "graph-1",
        "data_sources": {"ds-1": {"data_source_id": "ds-1"}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-2"}},
    })
    store.write_profile("profile-1", {"cache_version": 2, "profile_id": "profile-1", "graph_id": "graph-1", "write_profiles": {}})
    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    plan = WritePlan(
        plan_id="plan-1",
        content_type="podcast_episode",
        target=Target(page_title=None, page_id=None, data_source_id="ds-1", confidence="high", source="write_profile", target_id="graph-1", view_id="view-1"),
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
        _validate_plan_integrity(plan, store)
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py::test_v2_apply_integrity_blocks_view_data_source_mismatch -q
```

Expected: fail.

- [ ] **Step 3: Implement v2 integrity check**

Update `_validate_plan_integrity()` to accept/use `CacheV2Store` only. It must:

1. load graph by `plan.target.target_id`;
2. verify `plan.target.data_source_id` exists in graph `data_sources`;
3. if `plan.target.view_id` exists, verify view exists;
4. verify `view.data_source_id == plan.target.data_source_id`;
5. reject any missing graph/data_source/view with `plan_integrity_failed:*`.

Remove v1 `target_structure_for_data_source()` fallback from apply path.

- [ ] **Step 4: Run apply tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py -q
```

Expected: pass after v2 fixture updates.

---

## Task 10: v2 CLI Commands and Legacy Cache Deletion

**Files:**
- Modify: `capture_to_notion/cli.py`
- Test: `tests/test_cli_target.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI reset test**

Add to `tests/test_cli.py`:

```python
def test_cache_reset_v2_deletes_legacy_cache_paths(tmp_path, monkeypatch):
    from capture_to_notion.cli import main
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    config.aliases_file.write_text('{"aliases":{"Old":{}}}', encoding="utf-8")
    config.routes_file.write_text('{"routes":{}}', encoding="utf-8")
    (config.targets_dir / "old.json").write_text("{}", encoding="utf-8")

    exit_code = main(["cache", "reset-v2", "--delete-legacy", "--confirmed"])

    assert exit_code == 0
    assert not config.aliases_file.exists()
    assert not config.routes_file.exists()
    assert not config.targets_dir.exists()
    assert config.cache_v2_dir.exists()
    assert config.aliases_v2_file.exists()
```

- [ ] **Step 2: Write failing v2 scan/bind command test**

Add to `tests/test_cli_target.py`:

```python
def test_target_bind_profile_uses_v2_cache(tmp_path, monkeypatch):
    import json
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.cli import main
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph("graph-1", {
        "cache_version": 2,
        "graph_id": "graph-1",
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-1", "type": "gallery"}},
    })

    exit_code = main([
        "target",
        "bind-profile",
        "--alias",
        "Program",
        "--graph-id",
        "graph-1",
        "--profile-id",
        "profile-1",
        "--content-type",
        "podcast_episode",
        "--data-source-id",
        "ds-1",
        "--view-id",
        "view-1",
        "--field",
        "title=Name",
    ])

    assert exit_code == 0
    profile = json.loads((config.profiles_v2_dir / "profile-1.json").read_text(encoding="utf-8"))
    assert profile["write_profiles"]["podcast_episode"]["canonical_view_id"] == "view-1"
    assert store.find_alias("Program")["graph_id"] == "graph-1"
```

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py::test_cache_reset_v2_deletes_legacy_cache_paths \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py::test_target_bind_profile_uses_v2_cache -q
```

Expected: fail.

- [ ] **Step 4: Implement `cache reset-v2`**

In `cli.py`, implement:

```bash
capture-to-notion cache reset-v2 --delete-legacy --confirmed
```

Behavior:

1. refuse unless `--confirmed` is present;
2. if `--delete-legacy`, delete:
   - `config.aliases_file`
   - `config.routes_file`
   - `config.targets_dir`
   - old `config.plans_dir` only if it is not the same as v2 plans dir;
3. recreate v2 dirs and `cache-v2/aliases.json`.

Use Python `Path.unlink()` and `shutil.rmtree()` carefully on exact configured paths only.

- [ ] **Step 5: Implement `target bind-profile`**

Add CLI command:

```bash
capture-to-notion target bind-profile \
  --alias <alias> \
  --graph-id <graph-id> \
  --profile-id <profile-id> \
  --content-type <content-type> \
  --data-source-id <data-source-id> \
  [--view-id <view-id>] \
  [--field semantic=NotionProperty]
```

It must:

1. read v2 graph;
2. parse repeated `--field` values into `field_mapping`;
3. set all `field_sources` to `user_binding`;
4. call `bind_write_profile()`;
5. write v2 profile;
6. bind alias in v2 aliases.

- [ ] **Step 6: Run CLI tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py -q
```

Expected: pass.

---

## Task 11: Switch Capture CLI Commands to v2 Store

**Files:**
- Modify: `capture_to_notion/cli.py`
- Test: `tests/test_workflow_gate.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing workflow test that capture commands instantiate v2 store**

Add to `tests/test_workflow_gate.py`:

```python
def test_capture_preflight_uses_v2_cache_only(tmp_path, monkeypatch, capsys):
    import json
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.cli import main
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({"raw_input": "标题：Example", "target_hint": "Program", "content_type_hint": "podcast_episode"}), encoding="utf-8")

    store = CacheV2Store(config)
    store.write_graph("graph-1", {"cache_version": 2, "graph_id": "graph-1", "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}}, "views": {}})
    store.write_profile("profile-1", {"cache_version": 2, "profile_id": "profile-1", "graph_id": "graph-1", "write_profiles": {"podcast_episode": {"canonical_data_source_id": "ds-1", "field_mapping": {"title": "Name"}, "field_sources": {"title": "user_binding"}}}})
    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    exit_code = main(["capture", "preflight", "--input", str(input_path), "--compact"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["workflow"]["target_resolution"]["data_source_id"] == "ds-1"
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py::test_capture_preflight_uses_v2_cache_only -q
```

Expected: fail until CLI capture commands use `CacheV2Store`.

- [ ] **Step 3: Update capture command store wiring**

In `cli.py`, replace capture command initialization from `CacheStore(config)` to `CacheV2Store(config)` for:

```text
capture preflight
capture plan
capture apply
capture verify if it needs cache context
```

Do not add fallback to `CacheStore`.

- [ ] **Step 4: Run workflow tests**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py -q
```

Expected: pass.

---

## Task 12: Update Skill Documentation for v2-Only Cache

**Files:**
- Modify: `SKILL.md`
- Modify: `README.zh-CN.md` only if it documents cache flow

- [ ] **Step 1: Update `SKILL.md` cache rules**

Replace cache-first wording with:

```markdown
## V2 Notion Graph Cache

Capture to Notion uses only the v2 graph cache under `cache-v2/`. Legacy target cache files are not read by capture preflight, plan, or apply. If a target has no v2 graph/profile, route to scan/bind instead of falling back to old cache.

A v2 target consists of:
- a graph: pages, blocks, databases, data sources, and views from Notion API;
- a write profile: content type to canonical data source/view plus field mappings;
- an alias: user-facing name to graph/profile.

Writes always go to a data source. Views are display context and are validated when present.
```

- [ ] **Step 2: Run docs-related smoke test**

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py -q
```

Expected: pass.

---

## Task 13: Destructive Cache Reset and Full Rebuild Validation

**Files:**
- Runtime cache only. This task deletes old local cache files by explicit user-approved command.

- [ ] **Step 1: Verify reset command refuses without confirmation**

Run:

```bash
capture-to-notion cache reset-v2 --delete-legacy
```

Expected: fails with a message requiring `--confirmed`.

- [ ] **Step 2: Delete old cache and initialize v2 cache**

Run:

```bash
capture-to-notion cache reset-v2 --delete-legacy --confirmed
```

Expected:

```text
~/.config/capture-to-notion/targets/ removed
~/.config/capture-to-notion/aliases.json removed
~/.config/capture-to-notion/routes.json removed
~/.config/capture-to-notion/cache-v2/ exists
~/.config/capture-to-notion/cache-v2/aliases.json exists
```

- [ ] **Step 3: Re-scan real targets without content writes**

Run scans for known target pages only after confirming exact page IDs from user/context:

```bash
capture-to-notion target scan --page-id 36c6a715-808c-8152-8941-cf2b9afbb0f7 --graph-id fyfy --alias "枫言枫语"
capture-to-notion target scan --page-id 16e6a715-808c-80e3-8a74-c275de127d39 --graph-id silicon-valley-101 --alias "硅谷 101"
```

Expected: v2 graph files are created under `cache-v2/graphs/` and include pages/databases/data_sources/views where API exposes them.

- [ ] **Step 4: Bind generic write profile for the real gallery data source**

Use actual v2 graph IDs and actual data source/view IDs from scan output. Do not invent view IDs. Example command shape:

```bash
capture-to-notion target bind-profile \
  --alias "枫言枫语" \
  --graph-id fyfy \
  --profile-id fyfy \
  --content-type podcast_episode \
  --data-source-id <actual-data-source-id> \
  --view-id <actual-view-id-if-present> \
  --field title=<actual-title-property> \
  --field state=<actual-state-property> \
  --field description=<actual-description-property>
```

Expected: profile is created under `cache-v2/profiles/fyfy.json`.

- [ ] **Step 5: Preflight without writing**

Run:

```bash
capture-to-notion capture preflight --input /tmp/capture-to-notion-fyfy-166-gallery-input.json --compact
```

Expected:

```text
next_action = capture_plan
workflow.target_resolution.data_source_id = bound data source
workflow.target_resolution.view_id = bound view when available
```

- [ ] **Step 6: Plan without writing**

Run:

```bash
capture-to-notion capture plan \
  --input /tmp/capture-to-notion-fyfy-166-gallery-input.json \
  --output /tmp/capture-to-notion-v2-regression-plan.json \
  --compact
```

Expected: plan shows v2 graph/profile-derived write target. Do not apply during this validation unless user separately confirms.

---

## Final Verification

Run targeted tests:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_config.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_adapter.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_notion_graph.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cache_v2.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_profile_binder.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_scanner.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_preflight.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_planner.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_cli_target.py \
  /Users/aaron/.claude/skills/capture-to-notion/tests/test_workflow_gate.py
```

Run full regression:

```bash
uv run --project /Users/aaron/.claude/skills/capture-to-notion pytest \
  /Users/aaron/.claude/skills/capture-to-notion/tests
```

## Self-Review Notes

- This plan intentionally removes runtime compatibility with v1 cache.
- The only cache accepted by capture preflight/plan/apply is v2 graph/profile/alias cache.
- Old cache deletion is explicit and tested with `cache reset-v2 --delete-legacy --confirmed`.
- The design is generic: implementation code receives content type, field mapping, graph IDs, data source IDs, and view IDs from cache/profile/user binding.
- Real page IDs appear only in final validation commands, not in implementation code.
- The plan covers SDK pinning, Notion API versioning, Views API, v2 graph normalization, v2 cache, profile binding, scanner, resolver, planner, apply integrity, CLI, docs, destructive reset, and non-writing validation.
