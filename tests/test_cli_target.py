import json

import pytest

from capture_to_notion import cli
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
from capture_to_notion.models import CaptureInput, Target, WritePlan
from capture_to_notion.notion_adapter import NotionAuthError
from capture_to_notion.planner import build_capture_plan
from capture_to_notion.preflight import build_capture_preflight


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class FakeGraphCache:
    def __init__(self, graphs):
        self.graphs = graphs

    def read_graph(self, graph_id):
        return self.graphs.get(graph_id)


def make_page_parent_write_plan():
    return WritePlan(
        plan_id="plan-page-parent",
        content_type="article",
        target=Target(
            page_title="Knowledge",
            page_id="page-knowledge",
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_id="knowledge-root",
            target_kind="page_parent",
            parent_page_id="page-knowledge",
        ),
        summary={"title": "CLI validation note"},
        normalized_record={"title": "CLI validation note", "body": "Hello"},
        field_mapping={},
        operations=[
            {
                "type": "create_child_page",
                "parent_page_id": "page-knowledge",
                "title": "CLI validation note",
                "body_blocks": [{"type": "paragraph", "text": "Hello"}],
            }
        ],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )


def test_validate_plan_integrity_accepts_page_parent_without_data_source():
    graph = {
        "cache_version": 2,
        "graph_id": "knowledge-root",
        "root": {"kind": "page", "id": "page-knowledge"},
        "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "Knowledge"}},
        "blocks": {},
        "databases": {},
        "data_sources": {},
        "views": {},
    }

    target_structure = cli._validate_plan_integrity(
        make_page_parent_write_plan(),
        FakeGraphCache({"knowledge-root": graph}),
    )

    assert target_structure["target"]["target_id"] == "knowledge-root"
    assert target_structure["target"]["page_id"] == "page-knowledge"
    assert target_structure["data_sources"] == {}


def test_validate_plan_integrity_accepts_real_page_parent_preflight_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "knowledge-root",
        {
            "cache_version": 2,
            "graph_id": "knowledge-root",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "Knowledge", "kind": "page"}},
            "blocks": {},
            "databases": {},
            "data_sources": {},
            "views": {},
        },
    )
    store.bind_alias("Knowledge", graph_id="knowledge-root", profile_id=None, kind="graph")
    capture = CaptureInput.from_dict(
        {
            "raw_input": "标题：CLI validation note\n\nHello",
            "target_hint": "Knowledge",
            "content_type_hint": "article",
            "intent_hint": "direct_write",
            "input_shape_hint": "plain_text",
            "target_scope_hint": "page_parent",
            "user_requested_action": "write",
        }
    )
    preflight = build_capture_preflight(capture, store)
    plan = build_capture_plan(capture, store)
    plan.preflight_workflow = preflight["workflow"]

    target_structure = cli._validate_plan_integrity(plan, store)

    assert target_structure["target"]["target_id"] == "knowledge-root"
    assert target_structure["target"]["page_id"] == "page-knowledge"
    assert target_structure["data_sources"] == {}


def seed_cached_books_target(root):
    write_json(
        root / "cache-v2" / "graphs" / "bookshelf.json",
        {
            "cache_version": 2,
            "graph_id": "bookshelf",
            "root": {"kind": "page", "id": "page-books"},
            "pages": {"page-books": {"page_id": "page-books", "title": "Bookshelf"}},
            "blocks": {},
            "databases": {},
            "data_sources": {
                "ds-books": {
                    "object": "data_source",
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema_hash": "abc123",
                    "schema": {
                        "Name": {"type": "title"},
                        "Author": {"type": "rich_text"},
                        "Status": {"type": "status"},
                        "Cover": {"type": "files"},
                    },
                }
            },
            "views": {},
        },
    )
    write_json(
        root / "cache-v2" / "profiles" / "profile-books.json",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "bookshelf",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": None,
                    "field_mapping": {
                        "title": "Name",
                        "author": "Author",
                        "state": "Status",
                        "cover": "Cover",
                    },
                    "field_sources": {
                        "title": "user_binding",
                        "author": "user_binding",
                        "state": "user_binding",
                        "cover": "user_binding",
                    },
                    "state_mapping": {"field": "Status", "values": {"initialized": "Want to read", "completed": "Read"}},
                    "asset_mapping": {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}},
                    "relation_mapping": {},
                    "parser_profile": {},
                }
            },
        },
    )
    write_json(
        root / "cache-v2" / "aliases.json",
        {"cache_version": 2, "aliases": {"books": {"graph_id": "bookshelf", "profile_id": "profile-books", "kind": "write_profile"}}},
    )


class SearchAdapter:
    def __init__(self):
        self.search_calls = []

    def search(self, query, limit=None, include_parent_path=True):
        self.search_calls.append(
            {"query": query, "limit": limit, "include_parent_path": include_parent_path}
        )
        return [
            {
                "id": "page-books",
                "object": "page",
                "title": "书单",
                "url": "https://example.com/page-books",
                "last_edited_time": "2026-05-10T00:00:00Z",
            }
        ]


class DuplicateTitleSearchAdapter:
    def __init__(self):
        self.search_calls = []

    def search(self, query, limit=None, include_parent_path=True):
        self.search_calls.append(
            {"query": query, "limit": limit, "include_parent_path": include_parent_path}
        )
        return [
            {
                "id": "page-books-current",
                "object": "page",
                "title": "书单",
                "parent_path": "工作区顶层",
                "url": "https://example.com/current-books",
                "last_edited_time": "2026-05-10T00:00:00Z",
            },
            {
                "id": "page-books-template",
                "object": "page",
                "title": "书单",
                "parent_path": "工具 / 模板",
                "url": "https://example.com/template-books",
                "last_edited_time": "2023-08-25T00:00:00Z",
            },
        ]


class ManySearchAdapter:
    def __init__(self):
        self.search_calls = []

    def search(self, query, limit=None, include_parent_path=True):
        self.search_calls.append(
            {"query": query, "limit": limit, "include_parent_path": include_parent_path}
        )
        return [
            {
                "id": f"page-{index}",
                "object": "page",
                "title": f"候选 {index}",
                "parent_path": "工具 / 模板",
                "url": f"https://example.com/page-{index}",
                "last_edited_time": "2026-05-10T00:00:00Z",
            }
            for index in range(1, 5)
        ]


class NoViewsMixin:
    def list_views(self, database_id=None, data_source_id=None):
        return []

    def retrieve_view(self, view_id):
        raise AssertionError("retrieve_view should not be called when list_views is empty")


class ScanAdapter(NoViewsMixin):
    def retrieve_page(self, page_id):
        return {"id": page_id, "title": "书单"}

    def list_block_children(self, page_id):
        return [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]

    def retrieve_database(self, database_id):
        return {
            "id": database_id,
            "title": [{"plain_text": "Books"}],
            "properties": {
                "名称": {"id": "title", "type": "title", "title": {}},
                "阅读状态": {"id": "status", "type": "status", "status": {"options": []}},
                "封面": {"id": "cover", "type": "files", "files": {}},
            },
        }


class PageOnlyScanAdapter(NoViewsMixin):
    def retrieve_page(self, page_id):
        return {"id": page_id, "title": "AI/知识"}

    def list_block_children(self, page_id):
        return [{"type": "paragraph", "id": "block-1", "paragraph": {}}]


class NestedPageOnlyScanAdapter(NoViewsMixin):
    def retrieve_page(self, page_id):
        pages = {
            "page-tools": {
                "id": "page-tools",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "工具"}]}},
                "parent": {"type": "page_id", "page_id": "page-ai"},
            },
            "page-ai": {
                "id": "page-ai",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "AI"}]}},
                "parent": {"type": "workspace", "workspace": True},
            },
        }
        return pages[page_id]

    def list_block_children(self, page_id):
        return [{"type": "paragraph", "id": "block-1", "parent": {"type": "page_id", "page_id": page_id}, "paragraph": {}}]


class RecordPageAncestorScanAdapter(NoViewsMixin):
    def retrieve_page(self, page_id):
        pages = {
            "page-show": {
                "id": "page-show",
                "parent": {"type": "data_source_id", "data_source_id": "ds-podcasts"},
                "properties": {"播客名称": {"type": "title", "title": [{"plain_text": "独树不成林"}]}},
            },
            "page-podcasts": {
                "id": "page-podcasts",
                "parent": {"type": "workspace", "workspace": True},
                "properties": {"名称": {"type": "title", "title": [{"plain_text": "播客"}]}},
            },
        }
        return pages[page_id]

    def list_block_children(self, page_id):
        return []

    def retrieve_data_source(self, data_source_id):
        assert data_source_id == "ds-podcasts"
        return {
            "id": "ds-podcasts",
            "title": [{"plain_text": "播客索引"}],
            "parent": {"type": "database_id", "database_id": "db-podcasts"},
            "properties": {"播客名称": {"id": "title", "name": "播客名称", "type": "title"}},
        }

    def retrieve_database(self, database_id):
        assert database_id == "db-podcasts"
        return {
            "id": "db-podcasts",
            "title": [{"plain_text": "播客库"}],
            "parent": {"type": "page_id", "page_id": "page-podcasts"},
            "data_sources": [{"id": "ds-podcasts"}],
        }


class PodcastCompletionDateScanAdapter(NoViewsMixin):
    def retrieve_page(self, page_id):
        return {"id": page_id, "title": "独树不成林"}

    def list_block_children(self, page_id):
        return [{"type": "child_database", "id": "db-podcasts", "child_database": {"title": "Episodes"}}]

    def retrieve_database(self, database_id):
        return {
            "id": database_id,
            "title": [{"plain_text": "Episodes"}],
            "properties": {
                "主题": {"id": "title", "type": "title", "title": {}},
                "状态": {"id": "state", "type": "select", "select": {"options": []}},
                "内容描述": {"id": "description", "type": "rich_text", "rich_text": {}},
                "完成时间": {"id": "completed", "type": "date", "date": {}},
            },
        }


class CreateDatabaseAdapter:
    def __init__(self):
        self.created = []

    def create_database(self, page_id, title, properties, views=None):
        self.created.append({"page_id": page_id, "title": title, "properties": properties, "views": views})
        result = {"id": "db-episodes", "title": [{"plain_text": title}]}
        if views:
            result["created_views"] = [{"id": "view-gallery", "name": "画廊", "type": "gallery"}]
        return result

    def retrieve_page(self, page_id):
        return {"id": page_id, "parent": {"type": "data_source_id", "data_source_id": "ds-programs"}, "properties": {}}

    def list_block_children(self, page_id):
        return [{"type": "child_database", "id": "db-episodes", "child_database": {"title": "数据库"}}]

    def list_views(self, database_id=None, data_source_id=None):
        return []

    def retrieve_database(self, database_id):
        return {
            "id": database_id,
            "title": [{"plain_text": "数据库"}],
            "parent": {"type": "page_id", "page_id": "page-show"},
            "data_sources": [{"id": "ds-episodes", "name": "数据库"}],
        }

    def retrieve_data_source(self, data_source_id):
        if data_source_id == "ds-programs":
            return {
                "id": data_source_id,
                "title": [{"plain_text": "节目索引"}],
                "parent": {"type": "database_id", "database_id": "db-programs"},
                "properties": {
                    "播客名称": {"id": "title", "type": "title", "title": {}},
                    "主播": {"id": "podcast", "type": "rich_text", "rich_text": {}},
                },
            }
        return {
            "id": data_source_id,
            "title": [{"plain_text": "数据库"}],
            "properties": {
                "主题": {"id": "title", "type": "title", "title": {}},
                "状态": {"id": "state", "type": "status", "status": {"options": []}},
                "内容描述": {"id": "description", "type": "rich_text", "rich_text": {}},
                "完成时间": {"id": "completed", "type": "date", "date": {}},
            },
        }


class DataSourceScanAdapter(NoViewsMixin):
    def retrieve_data_source(self, data_source_id):
        return {
            "id": data_source_id,
            "title": [{"plain_text": "Books"}],
            "properties": {
                "Name": {"id": "title", "type": "title", "title": {}},
                "Status": {"id": "status", "type": "status", "status": {"options": []}},
                "Cover": {"id": "cover", "type": "files", "files": {}},
            },
        }


class DataSourceScanWithViewsAdapter:
    def retrieve_data_source(self, data_source_id):
        return {
            "id": data_source_id,
            "title": [{"plain_text": "Books"}],
            "parent": {"type": "database_id", "database_id": "db-books"},
            "properties": {
                "Name": {"id": "title", "type": "title", "title": {}},
                "Status": {"id": "status", "type": "status", "status": {"options": []}},
            },
        }

    def retrieve_database(self, database_id):
        return {
            "id": database_id,
            "title": [{"plain_text": "Book Database"}],
            "data_sources": [{"id": "ds-books", "name": "Books"}],
        }

    def list_views(self, database_id=None, data_source_id=None):
        if database_id == "db-books" or data_source_id == "ds-books":
            return [{"id": "view-reading"}]
        return []

    def retrieve_view(self, view_id):
        return {
            "id": view_id,
            "name": "在读列表",
            "type": "table",
            "data_source_id": "ds-books",
            "database_id": "db-books",
            "filter": {"property": "Status", "select": {"equals": "在读"}},
            "sorts": [{"property": "Name", "direction": "ascending"}],
        }


class NullTitleDataSourceScanAdapter(NoViewsMixin):
    def retrieve_page(self, page_id):
        return {"id": page_id, "title": "播客"}

    def list_block_children(self, page_id):
        return [{"type": "child_database", "id": "db-episodes", "child_database": {"title": "Episodes"}}]

    def retrieve_database(self, database_id):
        return {
            "id": database_id,
            "title": [{"plain_text": "Episodes"}],
            "data_sources": [{"id": "ds-episodes"}],
            "properties": {},
        }

    def retrieve_data_source(self, data_source_id):
        return {
            "id": data_source_id,
            "title": [],
            "parent": {"type": "database_id", "database_id": "db-episodes"},
            "properties": {
                "主题": {"id": "title", "type": "title", "title": {}},
                "状态": {"id": "state", "type": "select", "select": {"options": []}},
            },
        }


def test_cache_reset_v2_requires_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(tmp_path / "aliases.json", {"aliases": {"old": {"target_id": "old"}}})

    result = cli.main(["cache", "reset-v2", "--delete-legacy"])

    assert result == 2
    assert "--confirmed" in capsys.readouterr().err
    assert (tmp_path / "aliases.json").exists()



def test_cache_reset_v2_deletes_legacy_paths_and_recreates_v2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(tmp_path / "aliases.json", {"aliases": {"old": {"target_id": "old"}}})
    write_json(tmp_path / "routes.json", {"routes": {"book": {}}})
    write_json(tmp_path / "targets" / "old.json", {"target": {}})
    write_json(tmp_path / "plans" / "old.json", {"plan_id": "old"})

    result = cli.main(["cache", "reset-v2", "--delete-legacy", "--confirmed"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cache_version"] == 2
    assert data["deleted_legacy"] is True
    assert not (tmp_path / "aliases.json").exists()
    assert not (tmp_path / "routes.json").exists()
    assert not (tmp_path / "targets").exists()
    assert not (tmp_path / "plans").exists()
    assert json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8")) == {
        "cache_version": 2,
        "aliases": {},
    }



def test_target_bind_profile_writes_v2_profile_and_alias(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "schema": {"Name": {"type": "title"}, "Status": {"type": "status"}},
                }
            },
            "views": {"view-books": {"view_id": "view-books", "data_source_id": "ds-books", "type": "gallery"}},
        },
    )

    result = cli.main([
        "target",
        "bind-profile",
        "--alias",
        "books",
        "--graph-id",
        "graph-books",
        "--profile-id",
        "profile-books",
        "--content-type",
        "book",
        "--data-source-id",
        "ds-books",
        "--view-id",
        "view-books",
        "--field",
        "title=Name",
        "--field",
        "state=Status",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "alias": "books",
        "graph_id": "graph-books",
        "profile_id": "profile-books",
        "content_type": "book",
        "data_source_id": "ds-books",
        "view_id": "view-books",
    }
    profile = json.loads((tmp_path / "cache-v2" / "profiles" / "profile-books.json").read_text(encoding="utf-8"))
    write_profile = profile["write_profiles"]["book"]
    assert write_profile["field_mapping"] == {"title": "Name", "state": "Status"}
    assert write_profile["field_sources"] == {"title": "user_binding", "state": "user_binding"}
    aliases = json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["books"] == {"graph_id": "graph-books", "profile_id": "profile-books", "kind": "write_profile"}



def test_target_bind_profile_writes_asset_mapping_for_files_field(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "schema": {"Name": {"type": "title"}, "Cover": {"type": "files"}},
                }
            },
            "views": {},
        },
    )

    result = cli.main([
        "target",
        "bind-profile",
        "--alias",
        "books",
        "--graph-id",
        "graph-books",
        "--profile-id",
        "profile-books",
        "--content-type",
        "book",
        "--data-source-id",
        "ds-books",
        "--field",
        "title=Name",
        "--asset-field",
        "cover=Cover",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["asset_mapping"] == {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}}
    profile = json.loads((tmp_path / "cache-v2" / "profiles" / "profile-books.json").read_text(encoding="utf-8"))
    write_profile = profile["write_profiles"]["book"]
    assert write_profile["field_mapping"] == {"title": "Name", "cover": "Cover"}
    assert write_profile["field_sources"] == {"title": "user_binding", "cover": "profile"}
    assert write_profile["asset_mapping"] == {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}}
    assert write_profile["parser_profile"] == {"asset_trust_required_fields": ["cover"]}



def test_target_bind_profile_writes_relation_create_missing_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "schema": {"Name": {"type": "title"}, "Author": {"type": "relation"}},
                }
            },
            "views": {},
        },
    )

    result = cli.main([
        "target",
        "bind-profile",
        "--alias",
        "books",
        "--graph-id",
        "graph-books",
        "--profile-id",
        "profile-books",
        "--content-type",
        "book",
        "--data-source-id",
        "ds-books",
        "--field",
        "title=Name",
        "--field",
        "author=Author",
        "--relation-create-missing",
        "author",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["relation_mapping"] == {"author": {"create_missing": True}}
    profile = json.loads((tmp_path / "cache-v2" / "profiles" / "profile-books.json").read_text(encoding="utf-8"))
    assert profile["write_profiles"]["book"]["relation_mapping"] == {"author": {"create_missing": True}}



def test_target_bind_profile_preserves_existing_profile_settings_when_adding_relation_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "schema": {"Name": {"type": "title"}, "Author": {"type": "relation"}},
                }
            },
            "views": {},
        },
    )
    write_json(
        tmp_path / "cache-v2" / "profiles" / "profile-books.json",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "content_type": "book",
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "Name"},
                    "field_sources": {"title": "user_binding"},
                    "state_mapping": {"initialized": "Next"},
                    "asset_mapping": {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}},
                    "relation_mapping": {"category": {"create_missing": False}},
                    "parser_profile": {"labels": {"title": ["书名"]}},
                }
            },
        },
    )

    result = cli.main([
        "target",
        "bind-profile",
        "--alias",
        "books",
        "--graph-id",
        "graph-books",
        "--profile-id",
        "profile-books",
        "--content-type",
        "book",
        "--data-source-id",
        "ds-books",
        "--field",
        "title=Name",
        "--field",
        "author=Author",
        "--relation-create-missing",
        "author",
    ])

    assert result == 0
    capsys.readouterr()
    profile = json.loads((tmp_path / "cache-v2" / "profiles" / "profile-books.json").read_text(encoding="utf-8"))
    write_profile = profile["write_profiles"]["book"]
    assert write_profile["state_mapping"] == {"initialized": "Next"}
    assert write_profile["asset_mapping"] == {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}}
    assert write_profile["parser_profile"] == {"labels": {"title": ["书名"]}}
    assert write_profile["relation_mapping"] == {
        "category": {"create_missing": False},
        "author": {"create_missing": True},
    }



def test_target_bind_profile_preserves_existing_fields_when_no_field_args(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "schema": {"Name": {"type": "title"}, "Author": {"type": "relation"}},
                }
            },
            "views": {},
        },
    )
    write_json(
        tmp_path / "cache-v2" / "profiles" / "profile-books.json",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "content_type": "book",
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "Name", "author": "Author"},
                    "field_sources": {"title": "user_binding", "author": "user_binding"},
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                    "parser_profile": {},
                }
            },
        },
    )

    result = cli.main([
        "target",
        "bind-profile",
        "--alias",
        "books",
        "--graph-id",
        "graph-books",
        "--profile-id",
        "profile-books",
        "--content-type",
        "book",
        "--data-source-id",
        "ds-books",
        "--relation-create-missing",
        "author",
    ])

    assert result == 0
    capsys.readouterr()
    profile = json.loads((tmp_path / "cache-v2" / "profiles" / "profile-books.json").read_text(encoding="utf-8"))
    write_profile = profile["write_profiles"]["book"]
    assert write_profile["field_mapping"] == {"title": "Name", "author": "Author"}
    assert write_profile["field_sources"] == {"title": "user_binding", "author": "user_binding"}
    assert write_profile["relation_mapping"] == {"author": {"create_missing": True}}



def test_capture_preflight_cli_uses_v2_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {"Name": {"type": "title"}, "Status": {"type": "status"}},
                }
            },
            "views": {"view-books": {"view_id": "view-books", "data_source_id": "ds-books", "name": "Gallery", "type": "gallery"}},
        },
    )
    write_json(
        tmp_path / "cache-v2" / "profiles" / "profile-books.json",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": "view-books",
                    "field_mapping": {"title": "Name", "state": "Status"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "parser_profile": {},
                }
            },
        },
    )
    write_json(
        tmp_path / "cache-v2" / "aliases.json",
        {"cache_version": 2, "aliases": {"books": {"graph_id": "graph-books", "profile_id": "profile-books", "kind": "write_profile"}}},
    )
    input_file = tmp_path / "capture.json"
    write_json(input_file, {"raw_input": "可能性的艺术", "target_hint": "books", "state": "initialized", "content_type_hint": "book"})

    result = cli.main(["capture", "preflight", "--input", str(input_file), "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["next_action"] == "capture_plan"
    assert data["target"]["data_source_id"] == "ds-books"
    assert data["target"]["view_id"] == "view-books"



def test_target_search_outputs_candidates_without_writing_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = SearchAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))

    result = cli.main(["target", "search", "--query", "书单"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "query": "书单",
        "result_count": 1,
        "truncated": False,
        "results": [
            {
                "id": "page-books",
                "object": "page",
                "title": "书单",
                "url": "https://example.com/page-books",
                "last_edited_time": "2026-05-10T00:00:00Z",
            }
        ],
        "requires_confirmation": True,
        "next_action": "choose_exact_target_or_scan",
    }
    assert adapter.search_calls == [{"query": "书单", "limit": 6, "include_parent_path": True}]
    assert not (tmp_path / "aliases.json").exists()
    assert json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8")) == {"cache_version": 2, "aliases": {}}
    assert list((tmp_path / "cache-v2" / "graphs").glob("*.json")) == []


def test_target_search_marks_duplicate_titles_as_requiring_disambiguation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: DuplicateTitleSearchAdapter()))

    result = cli.main(["target", "search", "--query", "书单"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["requires_confirmation"] is True
    assert data["confirmation_reason"] == "duplicate_target_names"
    assert data["duplicate_titles"] == [
        {
            "title": "书单",
            "results": [
                {
                    "id": "page-books-current",
                    "object": "page",
                    "title": "书单",
                    "parent_path": "工作区顶层",
                    "url": "https://example.com/current-books",
                    "last_edited_time": "2026-05-10T00:00:00Z",
                },
                {
                    "id": "page-books-template",
                    "object": "page",
                    "title": "书单",
                    "parent_path": "工具 / 模板",
                    "url": "https://example.com/template-books",
                    "last_edited_time": "2023-08-25T00:00:00Z",
                },
            ],
        }
    ]
    assert not (tmp_path / "aliases.json").exists()
    assert json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8")) == {"cache_version": 2, "aliases": {}}
    assert list((tmp_path / "cache-v2" / "graphs").glob("*.json")) == []


def test_target_search_limit_caps_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = ManySearchAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))

    result = cli.main(["target", "search", "--query", "书单", "--limit", "2"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["result_count"] == 2
    assert data["truncated"] is True
    assert [result["id"] for result in data["results"]] == ["page-1", "page-2"]
    assert adapter.search_calls == [{"query": "书单", "limit": 3, "include_parent_path": True}]


def test_target_search_compact_includes_parent_path_by_default_for_disambiguation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = DuplicateTitleSearchAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))

    result = cli.main(["target", "search", "--query", "书单", "--limit", "2", "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    expected_results = [
        {
            "id": "page-books-current",
            "object": "page",
            "title": "书单",
            "last_edited_time": "2026-05-10T00:00:00Z",
            "parent_path": "工作区顶层",
            "path": "工作区顶层 / 书单",
            "path_complete": True,
        },
        {
            "id": "page-books-template",
            "object": "page",
            "title": "书单",
            "last_edited_time": "2023-08-25T00:00:00Z",
            "parent_path": "工具 / 模板",
            "path": "工具 / 模板 / 书单",
            "path_complete": True,
        },
    ]
    assert data["results"] == expected_results
    assert data["duplicate_titles"] == [{"title": "书单", "results": expected_results}]
    assert adapter.search_calls == [{"query": "书单", "limit": 3, "include_parent_path": True}]


def test_target_search_compact_can_include_parent_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = ManySearchAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))

    result = cli.main([
        "target",
        "search",
        "--query",
        "书单",
        "--limit",
        "1",
        "--compact",
        "--include-parent-path",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["results"] == [
        {
            "id": "page-1",
            "object": "page",
            "title": "候选 1",
            "last_edited_time": "2026-05-10T00:00:00Z",
            "parent_path": "工具 / 模板",
            "path": "工具 / 模板 / 候选 1",
            "path_complete": True,
        }
    ]
    assert adapter.search_calls == [{"query": "书单", "limit": 2, "include_parent_path": True}]


def test_target_scan_saves_v2_graph_and_alias(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: ScanAdapter()))

    result = cli.main(["target", "scan", "--page-id", "page-books", "--alias", "书单", "--target-id", "bookshelf"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cache_version"] == 2
    assert data["graph_id"] == "bookshelf"
    assert data["graph_file"] == str(tmp_path / "cache-v2" / "graphs" / "bookshelf.json")
    assert data["data_sources"] == [
        {
            "key": "db-books",
            "data_source_id": "db-books",
            "database_id": "db-books",
            "title": "Books",
            "schema_hash": "75da98c54ab81242",
            "schema_fields": ["名称", "封面", "阅读状态"],
        }
    ]
    assert data["views"] == []
    assert data["requires_profile_binding"] is True
    assert data["next_action"] == "target bind-profile"
    graph = json.loads((tmp_path / "cache-v2" / "graphs" / "bookshelf.json").read_text(encoding="utf-8"))
    assert graph["root"] == {"kind": "page", "id": "page-books"}
    assert set(graph["data_sources"]) == {"db-books"}
    assert set(graph["data_sources"]["db-books"]["schema"]) == {"名称", "阅读状态", "封面"}
    aliases = json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单"] == {"graph_id": "bookshelf", "profile_id": None, "kind": "graph"}


def test_target_scan_compact_outputs_data_source_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: ScanAdapter()))

    result = cli.main(["target", "scan", "--page-id", "page-books", "--alias", "书单", "--target-id", "bookshelf", "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["data_sources"] == [
        {
            "key": "db-books",
            "data_source_id": "db-books",
            "database_id": "db-books",
            "title": "Books",
            "schema_hash": "75da98c54ab81242",
            "field_count": 3,
        }
    ]
    assert "schema_fields" not in data["data_sources"][0]


def test_target_scan_page_only_reports_page_parent_capability(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: PageOnlyScanAdapter()))

    result = cli.main([
        "target",
        "scan",
        "--page-id",
        "page-knowledge",
        "--alias",
        "AI/知识",
        "--target-id",
        "ai-knowledge",
        "--compact",
    ])

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


def test_target_scan_page_only_stores_ancestor_page_for_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: NestedPageOnlyScanAdapter()))

    result = cli.main([
        "target",
        "scan",
        "--page-id",
        "page-tools",
        "--alias",
        "AI 工具",
        "--target-id",
        "ai-tools",
        "--compact",
    ])

    assert result == 0
    graph = json.loads((tmp_path / "cache-v2" / "graphs" / "ai-tools.json").read_text(encoding="utf-8"))
    assert graph["pages"]["page-tools"]["parent"] == {"type": "page_id", "id": "page-ai"}
    assert graph["pages"]["page-ai"]["title"] == "AI"



def test_target_scan_compact_includes_root_path_for_page_parent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: NestedPageOnlyScanAdapter()))

    result = cli.main([
        "target",
        "scan",
        "--page-id",
        "page-tools",
        "--alias",
        "AI 工具",
        "--target-id",
        "ai-tools",
        "--compact",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == "工作区顶层 / AI / 工具"
    assert data["path_complete"] is True



def test_target_inspect_compact_includes_root_path_for_page_parent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: NestedPageOnlyScanAdapter()))
    assert cli.main([
        "target",
        "scan",
        "--page-id",
        "page-tools",
        "--alias",
        "AI 工具",
        "--target-id",
        "ai-tools",
        "--compact",
    ]) == 0
    capsys.readouterr()

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "inspect", "--target-id", "ai-tools", "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == "工作区顶层 / AI / 工具"
    assert data["path_complete"] is True



def test_target_inspect_path_uses_title_property_when_cached_page_title_is_stale(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    store = CacheV2Store(ensure_config())
    store.write_graph(
        "banlatie",
        {
            "cache_version": 2,
            "graph_id": "banlatie",
            "root": {"kind": "page", "id": "page-show"},
            "pages": {
                "page-show": {
                    "page_id": "page-show",
                    "kind": "record_page",
                    "title": "进行中",
                    "parent": {"type": "workspace", "id": "workspace"},
                    "property_values": {
                        "状态": {"type": "status", "status": {"name": "进行中"}},
                        "播客名称": {"type": "title", "title": [{"plain_text": "半拿铁｜商业浮沉录"}]},
                    },
                }
            },
            "blocks": {},
            "databases": {},
            "data_sources": {},
            "views": {},
        },
    )
    store.bind_alias("半拿铁", graph_id="banlatie", profile_id=None, kind="graph")

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "inspect", "--alias", "半拿铁", "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["target"]["title"] == "半拿铁｜商业浮沉录"
    assert data["path"] == "工作区顶层 / 半拿铁｜商业浮沉录"
    assert data["path_complete"] is True



def test_target_scan_record_page_stores_data_source_ancestor_chain(tmp_path, monkeypatch, capsys):
    from capture_to_notion.path_utils import graph_object_path

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: RecordPageAncestorScanAdapter()))

    result = cli.main([
        "target",
        "scan",
        "--page-id",
        "page-show",
        "--alias",
        "独树不成林",
        "--target-id",
        "dushubuchenglin",
        "--compact",
    ])

    assert result == 0
    capsys.readouterr()
    graph = json.loads((tmp_path / "cache-v2" / "graphs" / "dushubuchenglin.json").read_text(encoding="utf-8"))
    assert graph["pages"]["page-show"]["parent"] == {"type": "data_source_id", "id": "ds-podcasts"}
    assert graph["data_sources"]["ds-podcasts"]["database_id"] == "db-podcasts"
    assert graph["databases"]["db-podcasts"]["parent"] == {"type": "page_id", "id": "page-podcasts"}
    assert graph["pages"]["page-podcasts"]["title"] == "播客"
    assert graph_object_path(graph, "page-show", "page") == {
        "path": "工作区顶层 / 播客 / 播客库 / 播客索引 / 独树不成林",
        "path_complete": True,
    }


def test_target_scan_output_includes_data_source_without_title(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: NullTitleDataSourceScanAdapter()))

    result = cli.main(["target", "scan", "--page-id", "page-podcasts", "--alias", "播客", "--target-id", "podcastshelf"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["data_sources"] == [
        {
            "key": "ds-episodes",
            "data_source_id": "ds-episodes",
            "database_id": "db-episodes",
            "schema_hash": "da39e43228d0cb14",
            "schema_fields": ["主题", "状态"],
        }
    ]


def test_target_create_database_loads_views_file_and_outputs_created_views(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = CreateDatabaseAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    schema_file = tmp_path / "episode-schema.json"
    views_file = tmp_path / "views.json"
    write_json(schema_file, {"properties": {"主题": {"type": "title", "title": {}}}})
    write_json(
        views_file,
        [
            {
                "name": "画廊",
                "type": "gallery",
                "configuration": {"gallery": {"card_preview": "cover"}},
            }
        ],
    )

    result = cli.main([
        "target",
        "create-database",
        "--page-id",
        "page-show",
        "--title",
        "数据库",
        "--schema",
        str(schema_file),
        "--views",
        str(views_file),
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["created_views"] == [{"id": "view-gallery", "name": "画廊", "type": "gallery"}]
    assert adapter.created[0]["views"] == [
        {
            "name": "画廊",
            "type": "gallery",
            "configuration": {"gallery": {"card_preview": "cover"}},
        }
    ]


def test_target_create_database_accepts_wrapped_views_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = CreateDatabaseAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    schema_file = tmp_path / "episode-schema.json"
    views_file = tmp_path / "views.json"
    write_json(schema_file, {"properties": {"主题": {"type": "title", "title": {}}}})
    write_json(
        views_file,
        {
            "views": [
                {
                    "name": "画廊",
                    "type": "gallery",
                    "configuration": {"gallery": {"card_preview": "cover"}},
                }
            ]
        },
    )

    result = cli.main([
        "target",
        "create-database",
        "--page-id",
        "page-show",
        "--title",
        "数据库",
        "--schema",
        str(schema_file),
        "--views",
        str(views_file),
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["created_views"] == [{"id": "view-gallery", "name": "画廊", "type": "gallery"}]
    assert adapter.created[0]["views"] == [
        {
            "name": "画廊",
            "type": "gallery",
            "configuration": {"gallery": {"card_preview": "cover"}},
        }
    ]


def test_load_database_views_rejects_invalid_wrapper_shape(tmp_path):
    views_file = tmp_path / "views.json"
    write_json(views_file, {"views": {"name": "画廊", "type": "gallery"}})

    with pytest.raises(cli.CliInputError, match="views 文件必须是 view 对象数组"):
        cli._load_database_views(str(views_file))


def test_target_create_database_creates_then_scans_v2_graph(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    adapter = CreateDatabaseAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    schema_file = tmp_path / "episode-schema.json"
    write_json(
        schema_file,
        {
            "properties": {
                "主题": {"type": "title", "title": {}},
                "状态": {"type": "status", "status": {}},
                "内容描述": {"type": "rich_text", "rich_text": {}},
                "完成时间": {"type": "date", "date": {}},
            }
        },
    )

    result = cli.main([
        "target",
        "create-database",
        "--page-id",
        "page-show",
        "--title",
        "数据库",
        "--schema",
        str(schema_file),
        "--alias",
        "枫言枫语",
        "--target-id",
        "fyfy",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cache_version"] == 2
    assert data["graph_id"] == "fyfy"
    assert data["graph_file"] == str(tmp_path / "cache-v2" / "graphs" / "fyfy.json")
    assert data["data_sources"][0]["key"] == "ds-episodes"
    assert data["data_sources"][0]["data_source_id"] == "ds-episodes"
    assert data["data_sources"][0]["database_id"] == "db-episodes"
    assert data["data_sources"][0]["title"] == "数据库"
    assert data["data_sources"][0]["schema_fields"] == ["主题", "内容描述", "完成时间", "状态"]
    assert data["views"] == []
    assert data["requires_profile_binding"] is True
    assert data["next_action"] == "target bind-profile"
    assert data["created_database_id"] == "db-episodes"
    assert data["created_database_title"] == "数据库"
    assert adapter.created == [
        {
            "page_id": "page-show",
            "title": "数据库",
            "properties": {
                "主题": {"type": "title", "title": {}},
                "状态": {"type": "status", "status": {}},
                "内容描述": {"type": "rich_text", "rich_text": {}},
                "完成时间": {"type": "date", "date": {}},
            },
            "views": None,
        }
    ]
    graph = json.loads((tmp_path / "cache-v2" / "graphs" / "fyfy.json").read_text(encoding="utf-8"))
    assert graph["root"] == {"kind": "page", "id": "page-show"}
    assert graph["data_sources"]["ds-episodes"]["database_id"] == "db-episodes"
    assert set(graph["data_sources"]["ds-episodes"]["schema"]) == {"主题", "状态", "内容描述", "完成时间"}
    aliases = json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["枫言枫语"] == {"graph_id": "fyfy", "profile_id": None, "kind": "graph"}



def test_capture_plan_refreshes_cache_when_input_field_needs_actual_schema(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "独树不成林": {
                    "type": "page",
                    "page_id": "page-podcasts",
                    "target_id": "podcastshelf",
                }
            }
        },
    )
    write_json(
        tmp_path / "targets" / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "独树不成林", "target_id": "podcastshelf"},
            "parser_profile": {
                "podcast_episode": {
                    "labels": {"description": ["简介"]},
                    "primary_score_fields": {"title": 20, "state": 10},
                    "trusted_field_sources": ["explicit", "profile"],
                }
            },
            "data_sources": {
                "db-podcasts": {
                    "data_source_id": "db-podcasts",
                    "title": "Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {
                        "title": "主题",
                        "state": "状态",
                        "description": "内容描述",
                    },
                    "field_sources": {
                        "title": "profile",
                        "state": "profile",
                        "description": "profile",
                    },
                    "schema": {
                        "主题": {"type": "title"},
                        "状态": {"type": "select"},
                        "内容描述": {"type": "rich_text"},
                    },
                }
            },
            "state_mapping": {"field": "状态", "values": {"completed": "已完成"}},
        },
    )
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "《这就是卢梭》 时间：2026-05-18 简介：卢梭专题导论",
            "target_hint": "独树不成林",
            "state": "已完成",
            "content_type_hint": "podcast_episode",
            "workflow_confirmations": ["risky_target"],
        },
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: PodcastCompletionDateScanAdapter()))

    result = cli.main(["capture", "plan", "--input", str(input_file), "--compact"])

    assert result == 2
    assert "v2_target_missing" in capsys.readouterr().err



def test_target_scan_accepts_data_source_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: DataSourceScanAdapter()))

    result = cli.main([
        "target",
        "scan",
        "--data-source-id",
        "ds-books",
        "--alias",
        "书单Books",
        "--target-id",
        "books-ds",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cache_version"] == 2
    assert data["graph_id"] == "books-ds"
    assert data["graph_file"] == str(tmp_path / "cache-v2" / "graphs" / "books-ds.json")
    assert data["data_sources"] == [
        {
            "key": "ds-books",
            "data_source_id": "ds-books",
            "title": "Books",
            "schema_hash": "941a679e5f8f3ae5",
            "schema_fields": ["Cover", "Name", "Status"],
        }
    ]
    assert data["views"] == []
    assert data["requires_profile_binding"] is True
    assert data["next_action"] == "target bind-profile"
    graph = json.loads((tmp_path / "cache-v2" / "graphs" / "books-ds.json").read_text(encoding="utf-8"))
    assert graph["root"] == {"kind": "data_source", "id": "ds-books"}
    assert graph["data_sources"]["ds-books"]["title"] == "Books"
    aliases = json.loads((tmp_path / "cache-v2" / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单Books"] == {"graph_id": "books-ds", "profile_id": None, "kind": "graph"}


def test_target_scan_outputs_structured_view_details(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: DataSourceScanWithViewsAdapter()))

    result = cli.main([
        "target",
        "scan",
        "--data-source-id",
        "ds-books",
        "--alias",
        "书单",
        "--target-id",
        "books-ds",
        "--compact",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["views"] == [
        {
            "view_id": "view-reading",
            "name": "在读列表",
            "type": "table",
            "data_source_id": "ds-books",
            "database_id": "db-books",
        }
    ]


def test_target_inspect_outputs_structured_view_details(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "bookshelf.json",
        {
            "cache_version": 2,
            "graph_id": "bookshelf",
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema_hash": "abc123",
                    "schema": {"Name": {"type": "title"}},
                }
            },
            "views": {
                "view-reading": {
                    "view_id": "view-reading",
                    "name": "在读列表",
                    "type": "table",
                    "data_source_id": "ds-books",
                    "database_id": "db-books",
                    "filter": {"property": "Status", "select": {"equals": "在读"}},
                }
            },
        },
    )
    write_json(
        tmp_path / "cache-v2" / "aliases.json",
        {"cache_version": 2, "aliases": {"书单": {"graph_id": "bookshelf", "profile_id": None, "kind": "graph"}}},
    )

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "inspect", "--alias", "书单", "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["views"] == [
        {
            "view_id": "view-reading",
            "name": "在读列表",
            "type": "table",
            "data_source_id": "ds-books",
            "database_id": "db-books",
        }
    ]


def test_target_bind_profile_accepts_view_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "graphs" / "graph-books.json",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {
                "ds-books": {"data_source_id": "ds-books", "schema": {"Name": {"type": "title"}}}
            },
            "views": {
                "view-reading": {
                    "view_id": "view-reading",
                    "name": "在读列表",
                    "data_source_id": "ds-books",
                    "type": "table",
                }
            },
        },
    )

    result = cli.main([
        "target",
        "bind-profile",
        "--alias",
        "书单",
        "--graph-id",
        "graph-books",
        "--profile-id",
        "profile-books",
        "--content-type",
        "book",
        "--data-source-id",
        "ds-books",
        "--view-name",
        "在读列表",
        "--field",
        "title=Name",
    ])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["view_id"] == "view-reading"
    profile = json.loads((tmp_path / "cache-v2" / "profiles" / "profile-books.json").read_text(encoding="utf-8"))
    assert profile["write_profiles"]["book"]["canonical_view_id"] == "view-reading"


def test_target_list_outputs_cached_targets_without_notion_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    seed_cached_books_target(tmp_path)

    def fail_from_config(cls, config):
        raise AssertionError("target list must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "list"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "count": 1,
        "targets": [
            {
                "alias": "books",
                "graph_id": "bookshelf",
                "profile_id": "profile-books",
                "kind": "write_profile",
                "root_kind": "page",
                "root_id": "page-books",
                "title": "Bookshelf",
                "data_sources": ["Books"],
                "views": [],
                "content_types": ["book"],
                "status": "cached",
            }
        ],
    }


def test_target_inspect_outputs_cached_target_details_without_notion_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    seed_cached_books_target(tmp_path)

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "inspect", "--alias", "books"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "alias": "books",
        "graph_id": "bookshelf",
        "profile_id": "profile-books",
        "kind": "write_profile",
        "graph_file": str(tmp_path / "cache-v2" / "graphs" / "bookshelf.json"),
        "profile_file": str(tmp_path / "cache-v2" / "profiles" / "profile-books.json"),
        "root": {"kind": "page", "id": "page-books"},
        "target": {"page_id": "page-books", "title": "Bookshelf"},
        "data_sources": [
            {
                "key": "ds-books",
                "data_source_id": "ds-books",
                "title": "Books",
                "schema_hash": "abc123",
                "schema_fields": ["Author", "Cover", "Name", "Status"],
            }
        ],
        "write_profiles": {
            "book": {
                "canonical_data_source_id": "ds-books",
                "canonical_view_id": None,
                "field_mapping": {
                    "title": "Name",
                    "author": "Author",
                    "state": "Status",
                    "cover": "Cover",
                },
                "field_sources": {
                    "title": "user_binding",
                    "author": "user_binding",
                    "state": "user_binding",
                    "cover": "user_binding",
                },
            }
        },
        "status": "cached",
    }


def test_target_inspect_compact_omits_full_fields(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    seed_cached_books_target(tmp_path)

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    try:
        result = cli.main(["target", "inspect", "--alias", "books", "--compact"])
    except SystemExit as exc:
        result = exc.code

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "alias": "books",
        "graph_id": "bookshelf",
        "profile_id": "profile-books",
        "kind": "write_profile",
        "root": {"kind": "page", "id": "page-books"},
        "target": {"page_id": "page-books", "title": "Bookshelf"},
        "path": "Bookshelf",
        "path_complete": False,
        "data_sources": [
            {
                "key": "ds-books",
                "data_source_id": "ds-books",
                "title": "Books",
                "schema_hash": "abc123",
                "field_count": 4,
            }
        ],
        "content_types": ["book"],
        "status": "cached",
    }
    assert "schema_fields" not in data["data_sources"][0]
    assert "write_profiles" not in data
    assert "graph_file" not in data



def test_target_inspect_outputs_cached_target_details_by_target_id_without_notion_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    seed_cached_books_target(tmp_path)

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "inspect", "--target-id", "bookshelf"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["alias"] == "books"
    assert data["graph_id"] == "bookshelf"
    assert data["profile_id"] == "profile-books"
    assert data["target"] == {"page_id": "page-books", "title": "Bookshelf"}
    assert data["data_sources"][0]["data_source_id"] == "ds-books"
    assert data["write_profiles"]["book"]["field_mapping"] == {
        "title": "Name",
        "author": "Author",
        "state": "Status",
        "cover": "Cover",
    }
    assert data["status"] == "cached"


def test_target_inspect_existing_alias_missing_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "aliases.json",
        {"cache_version": 2, "aliases": {"books": {"graph_id": "bookshelf", "profile_id": "profile-books", "kind": "write_profile"}}},
    )

    result = cli.main(["target", "inspect", "--alias", "books"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 v2 target alias: books" not in captured.err
    assert "未找到 v2 graph cache: bookshelf" in captured.err


def test_target_inspect_invalid_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "aliases.json",
        {"cache_version": 2, "aliases": {"books": {"graph_id": "bookshelf", "profile_id": "profile-books", "kind": "write_profile"}}},
    )
    (tmp_path / "cache-v2" / "graphs").mkdir(parents=True)
    (tmp_path / "cache-v2" / "graphs" / "bookshelf.json").write_text("{not json", encoding="utf-8")

    result = cli.main(["target", "inspect", "--alias", "books"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 v2 target alias: books" not in captured.err
    assert "未找到 v2 graph cache: bookshelf" in captured.err


def test_target_inspect_missing_target_id_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    result = cli.main(["target", "inspect", "--target-id", "bookshelf"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 v2 graph cache: bookshelf" in captured.err


def test_target_inspect_invalid_target_id_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    (tmp_path / "cache-v2" / "graphs").mkdir(parents=True)
    (tmp_path / "cache-v2" / "graphs" / "bookshelf.json").write_text("{not json", encoding="utf-8")

    result = cli.main(["target", "inspect", "--target-id", "bookshelf"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 v2 graph cache: bookshelf" in captured.err


def test_target_inspect_missing_alias_exits_with_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    result = cli.main(["target", "inspect", "--alias", "missing"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 v2 target alias: missing" in captured.err


def test_target_list_handles_mixed_invalid_cache_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "cache-v2" / "aliases.json",
        {
            "cache_version": 2,
            "aliases": {
                "bad-json": {"graph_id": "bad-json", "profile_id": None, "kind": "graph"},
                "missing": {"graph_id": "missing-cache", "profile_id": None, "kind": "graph"},
                "no-graph-id": {"profile_id": None, "kind": "graph"},
                "non-dict-alias": "skip me",
                "numeric-graph-id": {"graph_id": 123, "profile_id": None, "kind": "graph"},
                "valid-weird": {"graph_id": "valid-weird", "profile_id": "profile-weird", "kind": "write_profile"},
            },
        },
    )
    (tmp_path / "cache-v2" / "graphs").mkdir(parents=True)
    (tmp_path / "cache-v2" / "graphs" / "bad-json.json").write_text("{not json", encoding="utf-8")
    write_json(
        tmp_path / "cache-v2" / "graphs" / "valid-weird.json",
        {
            "cache_version": 2,
            "graph_id": "valid-weird",
            "root": {"kind": "page", "id": "page-valid-weird-cache"},
            "pages": {"page-valid-weird-cache": {"page_id": "page-valid-weird-cache", "title": "Valid Weird"}},
            "data_sources": {
                "string": {"data_source_id": "string", "title": "String Source"},
                "none": {"data_source_id": "none", "title": "None Source"},
            },
            "views": {},
        },
    )
    write_json(
        tmp_path / "cache-v2" / "profiles" / "profile-weird.json",
        {
            "cache_version": 2,
            "profile_id": "profile-weird",
            "graph_id": "valid-weird",
            "write_profiles": {"article": {}, "book": {}, "video": {}},
        },
    )

    def fail_from_config(cls, config):
        raise AssertionError("target list must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "list"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "count": 5,
        "targets": [
            {
                "alias": "bad-json",
                "graph_id": "bad-json",
                "profile_id": None,
                "kind": "graph",
                "title": None,
                "data_sources": [],
                "views": [],
                "content_types": [],
                "status": "missing_cache",
            },
            {
                "alias": "missing",
                "graph_id": "missing-cache",
                "profile_id": None,
                "kind": "graph",
                "title": None,
                "data_sources": [],
                "views": [],
                "content_types": [],
                "status": "missing_cache",
            },
            {
                "alias": "no-graph-id",
                "graph_id": None,
                "profile_id": None,
                "kind": "graph",
                "title": None,
                "data_sources": [],
                "views": [],
                "content_types": [],
                "status": "missing_cache",
            },
            {
                "alias": "numeric-graph-id",
                "graph_id": 123,
                "profile_id": None,
                "kind": "graph",
                "title": None,
                "data_sources": [],
                "views": [],
                "content_types": [],
                "status": "missing_cache",
            },
            {
                "alias": "valid-weird",
                "graph_id": "valid-weird",
                "profile_id": "profile-weird",
                "kind": "write_profile",
                "root_kind": "page",
                "root_id": "page-valid-weird-cache",
                "title": "Valid Weird",
                "data_sources": ["String Source", "None Source"],
                "views": [],
                "content_types": ["article", "book", "video"],
                "status": "cached",
            },
        ],
    }


def test_target_search_notion_error_exits_nonzero_with_readable_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    def raise_auth_error(cls, config):
        raise NotionAuthError("Notion token environment variable is not set: NOTION_TOKEN")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(raise_auth_error))

    result = cli.main(["target", "search", "--query", "书单"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "错误:" in captured.err
    assert "NOTION_TOKEN" in captured.err
