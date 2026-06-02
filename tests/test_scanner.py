import json

from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
from capture_to_notion.scanner import _build_asset_mapping, _primary_score, scan_data_source_graph, scan_data_source_target, scan_page_target
from capture_to_notion.schema import SCHEMA_PROPERTY_TYPES, normalize_database_schema


class FakeAdapter:
    def __init__(self, pages=None, children=None, databases=None, data_sources=None):
        self.pages = pages or {}
        self.children = children or {}
        self.databases = databases or {}
        self.data_sources = data_sources or {}

    def retrieve_page(self, page_id):
        return self.pages[page_id]

    def list_block_children(self, page_id):
        return self.children.get(page_id, [])

    def retrieve_database(self, database_id):
        return self.databases[database_id]

    def retrieve_data_source(self, data_source_id):
        return self.data_sources[data_source_id]

    def list_views(self, database_id=None, data_source_id=None):
        return []

    def list_data_source_templates(self, data_source_id):
        return []


def seed_profile_mapping(config, target_id, data_source_id, fields):
    config.targets_dir.mkdir(parents=True, exist_ok=True)
    (config.targets_dir / f"{target_id}.json").write_text(
        json.dumps(
            {
                "target": {"page_id": "page", "target_id": target_id},
                "data_sources": {
                    data_source_id: {
                        "data_source_id": data_source_id,
                        "fields": fields,
                        "field_sources": {key: "profile" for key in fields},
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def test_primary_score_ignores_legacy_semantic_field_sources():
    assert _primary_score(
        {
            "schema": {"名称": {"type": "title"}},
            "fields": {"title": "名称"},
            "field_sources": {"title": "semantic"},
        },
        {"title": 20},
    ) == 0


def test_primary_score_uses_profile_weights():
    assert _primary_score(
        {
            "schema": {"Headline": {"type": "title"}, "Topic": {"type": "rich_text"}},
            "fields": {"headline": "Headline", "topic": "Topic"},
            "field_sources": {"headline": "profile", "topic": "explicit"},
        },
        {"headline": 100, "topic": 15},
    ) == 115


def test_scan_data_source_graph_preserves_template_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)

    class TemplateAdapter(FakeAdapter):
        def list_data_source_templates(self, data_source_id):
            assert data_source_id == "ds-notes"
            return [
                {
                    "id": "template-default",
                    "page_id": "page-template-default",
                    "title": [{"plain_text": "Default note"}],
                    "data_source_id": "ds-notes",
                    "database_id": "db-notes",
                    "is_default": True,
                }
            ]

    adapter = TemplateAdapter(
        data_sources={
            "ds-notes": {
                "id": "ds-notes",
                "title": [{"plain_text": "Notes"}],
                "parent": {"type": "database_id", "database_id": "db-notes"},
                "properties": {"Name": {"type": "title"}},
            }
        },
        databases={"db-notes": {"id": "db-notes", "title": [{"plain_text": "Notes DB"}], "data_sources": [{"id": "ds-notes"}]}},
    )

    graph = scan_data_source_graph(adapter, "ds-notes", cache, graph_id="notes")

    assert graph["data_sources"]["ds-notes"]["templates"] == [
        {
            "template_id": "template-default",
            "page_id": "page-template-default",
            "name": "Default note",
            "title": "Default note",
            "data_source_id": "ds-notes",
            "database_id": "db-notes",
            "is_default": True,
        }
    ]


def test_scan_page_uses_target_parser_profile_primary_score_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.config_file,
        {"parser_profiles": {"defaults": {}}},
    )
    cache.write_json(
        config.targets_dir / "publishing.json",
        {
            "target": {"page_id": "page-content", "target_id": "publishing"},
            "parser_profile": {
                "article": {
                    "field_mapping": {"headline": "Headline"},
                    "primary_score_fields": {"headline": 100},
                }
            },
            "data_sources": {},
        },
    )
    adapter = FakeAdapter(
        pages={"page-content": {"id": "page-content", "title": "Publishing"}},
        children={
            "page-content": [
                {"type": "child_database", "id": "db-notes", "child_database": {"title": "Notes"}},
                {"type": "child_database", "id": "db-articles", "child_database": {"title": "Articles"}},
            ]
        },
        databases={
            "db-notes": {
                "id": "db-notes",
                "title": "Notes",
                "properties": {"Title": {"id": "title", "type": "title", "title": {}}},
            },
            "db-articles": {
                "id": "db-articles",
                "title": "Articles",
                "properties": {"Headline": {"id": "headline", "type": "title", "title": {}}},
            },
        },
    )

    result = scan_page_target(adapter, "page-content", cache, target_id="publishing")

    assert result["data_sources"]["db-notes"]["role"] == "secondary"
    assert result["data_sources"]["db-articles"]["role"] == "primary"


def test_scan_page_without_primary_score_fields_does_not_use_legacy_book_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.config_file,
        {"parser_profiles": {"defaults": {}}},
    )
    seed_profile_mapping(config, "bookshelf", "db-books", {"title": "书名", "author": "作者", "isbn": "ISBN"})
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "书名": {"id": "title", "type": "title", "title": {}},
                    "作者": {"id": "author", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "ISBN": {"id": "isbn", "type": "rich_text", "rich_text": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")

    assert result["data_sources"]["db-books"]["role"] == "secondary"
    assert result["confirmation_reason"] == "field_mapping_missing"


def test_scan_page_discovers_child_databases_normalizes_schema_and_saves_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(
        config,
        "bookshelf",
        "db-books",
        {
            "title": "名称",
            "notes": "笔记",
            "state": "状态",
            "tag": "标签",
            "url": "链接",
            "date": "日期",
            "cover": "封面",
            "author": "作者",
        },
    )
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={
            "page-books": [
                {"type": "paragraph", "id": "block-1"},
                {
                    "type": "child_database",
                    "id": "db-books",
                    "child_database": {"title": "Books"},
                },
            ]
        },
        databases={
            "db-books": {
                "id": "db-books",
                "title": [{"plain_text": "Books"}],
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "笔记": {"id": "notes", "type": "rich_text", "rich_text": {}},
                    "状态": {
                        "id": "status",
                        "type": "status",
                        "status": {"options": [{"name": "想读", "color": "blue"}]},
                    },
                    "标签": {
                        "id": "tag",
                        "type": "select",
                        "select": {"options": [{"name": "技术", "color": "green"}]},
                    },
                    "链接": {"id": "url", "type": "url", "url": {}},
                    "日期": {"id": "date", "type": "date", "date": {}},
                    "封面": {"id": "files", "type": "files", "files": {}},
                    "作者": {
                        "id": "rel",
                        "type": "relation",
                        "relation": {"database_id": "db-authors", "data_source_id": "ds-authors", "type": "single_property"},
                    },
                    "忽略": {"id": "num", "type": "number", "number": {}},
                },
            }
        },
    )

    result = scan_page_target(
        adapter,
        "page-books",
        cache,
        target_id="bookshelf",
        alias="书单",
    )

    assert result["requires_confirmation"] is False
    assert result["target"] == {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"}
    data_source = result["data_sources"]["db-books"]
    assert data_source["data_source_id"] == "db-books"
    assert data_source["title"] == "Books"
    assert data_source["role"] == "primary"
    assert data_source["fields"] == {
        "title": "名称",
        "notes": "笔记",
        "state": "状态",
        "tag": "标签",
        "url": "链接",
        "date": "日期",
        "cover": "封面",
        "author": "作者",
    }
    assert data_source["schema"]["状态"]["options"] == [{"name": "想读", "color": "blue"}]
    assert data_source["schema"]["作者"]["target_database_id"] == "db-authors"
    assert data_source["schema"]["作者"]["target_data_source_id"] == "ds-authors"
    assert data_source["schema_hash"] == scan_page_target(
        adapter,
        "page-books",
        CacheStore(config),
        target_id="bookshelf-second",
    )["data_sources"]["db-books"]["schema_hash"]
    assert result["relations"] == [
        {
            "data_source_id": "db-books",
            "field": "作者",
            "target_database_id": "db-authors",
            "target_data_source_id": "ds-authors",
        }
    ]
    assert result["state_mapping"] == {"field": "状态", "values": {}}
    assert result["asset_mapping"] == {
        "cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}
    }

    saved = json.loads((config.targets_dir / "bookshelf.json").read_text(encoding="utf-8"))
    assert saved == result
    aliases = json.loads(config.aliases_file.read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单"]["target_id"] == "bookshelf"
    assert aliases["书单"]["page_id"] == "page-books"


def test_scan_page_target_caches_child_database_location_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={
            "page-books": [
                {
                    "type": "child_database",
                    "id": "block-books",
                    "child_database": {"database_id": "db-books", "title": "Books"},
                }
            ]
        },
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "parent": {"type": "page_id", "page_id": "page-books"},
                "properties": {"Name": {"id": "title", "type": "title", "title": {}}},
            }
        },
    )

    result = scan_page_target(adapter, "page-books", CacheStore(config), target_id="bookshelf")

    data_source = result["data_sources"]["db-books"]
    assert data_source["database_id"] == "db-books"
    assert data_source["parent_page_id"] == "page-books"
    assert data_source["parent_type"] == "page_id"
    assert data_source["source_block_id"] == "block-books"
    assert data_source["source_block_type"] == "child_database"
    saved = json.loads((config.targets_dir / "bookshelf.json").read_text(encoding="utf-8"))
    assert saved["data_sources"]["db-books"]["source_block_id"] == "block-books"


def test_scan_page_target_detects_database_item_parent_and_saves_page_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "book-item", "ds-books", {"title": "名称", "state": "状态"})
    adapter = FakeAdapter(
        pages={
            "page-book-1": {
                "id": "page-book-1",
                "title": "可能性的艺术",
                "parent": {"type": "data_source_id", "data_source_id": "ds-books"},
            }
        },
        data_sources={
            "ds-books": {
                "id": "ds-books",
                "title": [{"plain_text": "Books"}],
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "状态": {"id": "status", "type": "status", "status": {"options": []}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-book-1", cache, target_id="book-item", alias="可能性的艺术")

    assert result["target"] == {
        "page_id": "page-book-1",
        "title": "可能性的艺术",
        "target_id": "book-item",
        "data_source_id": "ds-books",
    }
    assert result["data_sources"]["ds-books"]["role"] == "primary"
    assert result["data_sources"]["ds-books"]["fields"] == {"title": "名称", "state": "状态"}
    aliases = json.loads(config.aliases_file.read_text(encoding="utf-8"))["aliases"]
    assert aliases["可能性的艺术"] == {
        "type": "page",
        "page_id": "page-book-1",
        "title": "可能性的艺术",
        "target_id": "book-item",
    }



def test_scan_page_target_marks_database_item_single_data_source_primary_without_profile_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    adapter = FakeAdapter(
        pages={
            "page-episode-1": {
                "id": "page-episode-1",
                "title": "某一期节目",
                "parent": {"type": "data_source_id", "data_source_id": "ds-episodes"},
            }
        },
        data_sources={
            "ds-episodes": {
                "id": "ds-episodes",
                "properties": {
                    "主题": {"id": "title", "type": "title", "title": {}},
                    "状态": {"id": "status", "type": "select", "select": {"options": []}},
                    "完成时间": {"id": "date", "type": "date", "date": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-episode-1", cache, target_id="episode-item", alias="某一期节目")

    assert result["data_sources"]["ds-episodes"]["role"] == "primary"
    assert result["requires_confirmation"] is False
    assert result["confirmation_reason"] is None



def test_scan_data_source_target_caches_selected_source_as_primary(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(
        config,
        "books-ds",
        "ds-books",
        {
            "title": "名称",
            "state": "状态",
            "cover": "封面",
            "author": "作者",
            "isbn": "ISBN",
            "page_count": "页数",
        },
    )
    adapter = FakeAdapter(
        data_sources={
            "ds-books": {
                "id": "ds-books",
                "title": [{"plain_text": "Books"}],
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "状态": {"id": "status", "type": "status", "status": {"options": []}},
                    "封面": {"id": "cover", "type": "files", "files": {}},
                    "作者": {"id": "author", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "ISBN": {"id": "isbn", "type": "rich_text", "rich_text": {}},
                    "页数": {"id": "pages", "type": "number", "number": {}},
                },
            }
        }
    )

    result = scan_data_source_target(adapter, "ds-books", cache, target_id="books-ds", alias="书单Books")

    assert result["requires_confirmation"] is False
    assert result["target"] == {
        "page_id": None,
        "title": "Books",
        "target_id": "books-ds",
        "data_source_id": "ds-books",
    }
    data_source = result["data_sources"]["ds-books"]
    assert data_source["role"] == "primary"
    assert data_source["fields"] == {
        "title": "名称",
        "state": "状态",
        "cover": "封面",
        "author": "作者",
        "isbn": "ISBN",
        "page_count": "页数",
    }
    assert result["state_mapping"] == {"field": "状态", "values": {}}
    assert result["asset_mapping"] == {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}}
    aliases = json.loads(config.aliases_file.read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单Books"] == {
        "type": "data_source",
        "data_source_id": "ds-books",
        "title": "Books",
        "target_id": "books-ds",
    }


def test_scan_data_source_target_caches_parent_page_location_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        databases={
            "db-books": {
                "id": "db-books",
                "parent": {"type": "page_id", "page_id": "page-books"},
            }
        },
        data_sources={
            "ds-books": {
                "id": "ds-books",
                "title": "Books",
                "parent": {"type": "database_id", "database_id": "db-books"},
                "properties": {"Name": {"id": "title", "type": "title", "title": {}}},
            }
        },
    )

    result = scan_data_source_target(adapter, "ds-books", CacheStore(config), target_id="books-ds")

    assert result["target"]["data_source_id"] == "ds-books"
    assert result["target"]["database_id"] == "db-books"
    assert result["target"]["parent_database_id"] == "db-books"
    assert result["target"]["parent_page_id"] == "page-books"
    assert result["target"]["parent_type"] == "database_id"
    data_source = result["data_sources"]["ds-books"]
    assert data_source["database_id"] == "db-books"
    assert data_source["parent_database_id"] == "db-books"
    assert data_source["parent_page_id"] == "page-books"


def test_scan_page_uses_real_data_source_schema_from_database_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "bookshelf", "ds-reading", {"title": "书名", "cover": "封面"})
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-container", "child_database": {"title": "阅读记录"}}]},
        databases={
            "db-container": {
                "id": "db-container",
                "title": [{"plain_text": "阅读记录"}],
                "data_sources": [{"id": "ds-reading", "name": "阅读记录"}],
                "properties": {},
            }
        },
        data_sources={
            "ds-reading": {
                "id": "ds-reading",
                "title": [{"plain_text": "阅读记录"}],
                "properties": {
                    "书名": {"id": "title", "type": "title", "title": {}},
                    "封面": {"id": "cover", "type": "files", "files": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")

    data_source = result["data_sources"]["ds-reading"]
    assert "db-container" not in result["data_sources"]
    assert data_source["data_source_id"] == "ds-reading"
    assert data_source["title"] == "阅读记录"
    assert data_source["fields"] == {"title": "书名", "cover": "封面"}
    assert data_source["schema"]["书名"]["type"] == "title"


def test_scan_page_marks_profile_mapped_book_schema_as_primary_over_empty_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(
        config,
        "bookshelf",
        "db-books",
        {"title": "书名", "state": "阅读进度", "cover": "封面图", "author": "作者", "isbn": "ISBN"},
    )
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={
            "page-books": [
                {"type": "child_database", "id": "db-empty", "child_database": {"title": "Untitled"}},
                {"type": "child_database", "id": "db-books", "child_database": {"title": "正在阅读"}},
            ]
        },
        databases={
            "db-empty": {"id": "db-empty", "title": "Untitled", "properties": {}},
            "db-books": {
                "id": "db-books",
                "title": "正在阅读",
                "properties": {
                    "书名": {"id": "title", "type": "title", "title": {}},
                    "阅读进度": {
                        "id": "status",
                        "type": "status",
                        "status": {"options": [{"name": "正在阅读", "color": "blue"}]},
                    },
                    "封面图": {"id": "files", "type": "files", "files": {}},
                    "作者": {"id": "author", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "ISBN": {"id": "isbn", "type": "rich_text", "rich_text": {}},
                },
            },
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")

    assert result["data_sources"]["db-empty"]["role"] == "secondary"
    assert result["data_sources"]["db-books"]["role"] == "primary"
    assert result["data_sources"]["db-books"]["fields"] == {
        "title": "书名",
        "state": "阅读进度",
        "cover": "封面图",
        "author": "作者",
        "isbn": "ISBN",
    }
    assert result["state_mapping"] == {"field": "阅读进度", "values": {}}


def test_scan_page_prefers_profile_mapped_book_schema_over_unmapped_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "bookshelf", "db-books", {"title": "书名", "author": "作者", "isbn": "ISBN"})
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={
            "page-books": [
                {"type": "child_database", "id": "db-generic", "child_database": {"title": "项目"}},
                {"type": "child_database", "id": "db-books", "child_database": {"title": "阅读记录"}},
            ]
        },
        databases={
            "db-generic": {
                "id": "db-generic",
                "title": "项目",
                "properties": {
                    "Project": {"id": "title", "type": "title", "title": {}},
                    "Status": {"id": "status", "type": "status", "status": {"options": []}},
                    "附件": {"id": "files", "type": "files", "files": {}},
                    "Website": {"id": "url", "type": "url", "url": {}},
                },
            },
            "db-books": {
                "id": "db-books",
                "title": "阅读记录",
                "properties": {
                    "书名": {"id": "title", "type": "title", "title": {}},
                    "作者": {"id": "author", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "ISBN": {"id": "isbn", "type": "rich_text", "rich_text": {}},
                },
            },
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")

    assert result["data_sources"]["db-generic"]["role"] == "secondary"
    assert result["data_sources"]["db-generic"]["fields"] == {}
    assert result["data_sources"]["db-books"]["role"] == "primary"


def test_alias_write_preserves_unrelated_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    config.aliases_file.write_text(
        json.dumps({"aliases": {"旧别名": {"target_id": "old"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    adapter = FakeAdapter(
        pages={"page": {"id": "page", "title": "Page"}},
        children={"page": [{"type": "child_database", "id": "db"}]},
        databases={"db": {"id": "db", "title": "DB", "properties": {"Name": {"id": "title", "type": "title", "title": {}}}}},
    )

    scan_page_target(adapter, "page", CacheStore(config), target_id="new", alias="新别名")

    aliases = json.loads(config.aliases_file.read_text(encoding="utf-8"))["aliases"]
    assert aliases["旧别名"] == {"target_id": "old"}
    assert aliases["新别名"]["target_id"] == "new"


def test_scan_page_without_child_database_requires_confirmation_and_saves_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-empty": {"id": "page-empty", "title": "空页面"}},
        children={"page-empty": [{"type": "paragraph", "id": "p"}]},
    )

    result = scan_page_target(adapter, "page-empty", CacheStore(config), target_id="empty", alias="空")

    assert result["requires_confirmation"] is True
    assert result["confirmation_reason"] == "child_database_not_found"
    assert result["data_sources"] == {}
    assert (config.targets_dir / "empty.json").exists()
    aliases = json.loads(config.aliases_file.read_text(encoding="utf-8"))["aliases"]
    assert aliases["空"]["target_id"] == "empty"


def test_scan_page_with_only_empty_schema_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-empty-schema": {"id": "page-empty-schema", "title": "空库页面"}},
        children={"page-empty-schema": [{"type": "child_database", "id": "db-empty", "child_database": {"title": "Untitled"}}]},
        databases={"db-empty": {"id": "db-empty", "title": "Untitled", "properties": {}}},
    )

    result = scan_page_target(adapter, "page-empty-schema", CacheStore(config), target_id="empty-schema")

    assert result["requires_confirmation"] is True
    assert result["confirmation_reason"] == "data_source_schema_empty"
    assert result["data_sources"]["db-empty"]["role"] == "secondary"


def test_scan_page_with_only_type_fallback_fields_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-generic": {"id": "page-generic", "title": "通用页面"}},
        children={"page-generic": [{"type": "child_database", "id": "db-generic", "child_database": {"title": "项目"}}]},
        databases={
            "db-generic": {
                "id": "db-generic",
                "title": "项目",
                "properties": {
                    "Project": {"id": "title", "type": "title", "title": {}},
                    "Details": {"id": "text", "type": "rich_text", "rich_text": {}},
                    "Phase": {"id": "select", "type": "select", "select": {"options": []}},
                    "Attachment": {"id": "files", "type": "files", "files": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-generic", CacheStore(config), target_id="generic")

    assert result["requires_confirmation"] is True
    assert result["confirmation_reason"] == "field_mapping_missing"
    assert result["data_sources"]["db-generic"]["role"] == "secondary"
    assert result["data_sources"]["db-generic"]["fields"] == {}


def test_scan_page_primary_score_does_not_use_schema_size_as_tiebreaker(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "bookshelf", "db-first", {"title": "书名"})
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={
            "page-books": [
                {"type": "child_database", "id": "db-first", "child_database": {"title": "候选一"}},
                {"type": "child_database", "id": "db-wide", "child_database": {"title": "候选二"}},
            ]
        },
        databases={
            "db-first": {
                "id": "db-first",
                "title": "候选一",
                "properties": {
                    "书名": {"id": "title", "type": "title", "title": {}},
                },
            },
            "db-wide": {
                "id": "db-wide",
                "title": "候选二",
                "properties": {
                    "标题": {"id": "title", "type": "title", "title": {}},
                    "Details": {"id": "text", "type": "rich_text", "rich_text": {}},
                    "Phase": {"id": "select", "type": "select", "select": {"options": []}},
                    "Website": {"id": "url", "type": "url", "url": {}},
                    "Attachment": {"id": "files", "type": "files", "files": {}},
                },
            },
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")

    assert result["data_sources"]["db-first"]["role"] == "primary"
    assert result["data_sources"]["db-wide"]["role"] == "secondary"


def test_scanner_does_not_map_business_fields_from_property_names_without_profile():
    schema = {
        "作者": {"type": "rich_text", "rich_text": {}},
        "ISBN": {"type": "rich_text", "rich_text": {}},
        "页数": {"type": "number", "number": {}},
        "封面": {"type": "files", "files": {}},
    }
    normalized = normalize_database_schema(schema)

    assert normalized["作者"]["type"] == "rich_text"
    assert normalized["ISBN"]["type"] == "rich_text"
    assert normalized["页数"]["type"] == "number"
    assert normalized["封面"]["type"] == "files"



def test_scan_page_ignores_malformed_property_objects_without_type_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-broken": {"id": "page-broken", "title": "Broken"}},
        children={"page-broken": [{"type": "child_database", "id": "db-broken", "child_database": {"title": "Broken"}}]},
        databases={
            "db-broken": {
                "id": "db-broken",
                "title": "Broken",
                "properties": {
                    "Name": {"id": "title", "type": "title", "title": {}},
                    "Broken Files": {"id": "files", "type": "files"},
                    "Broken State": {"id": "state", "type": "status"},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-broken", CacheStore(config), target_id="broken")
    data_source = result["data_sources"]["db-broken"]

    assert "Broken Files" not in data_source["schema"]
    assert "Broken State" not in data_source["schema"]
    assert result["asset_mapping"] == {}
    assert result["state_mapping"] == {}



def test_scan_page_caches_official_property_types_and_ignores_nonofficial_types(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    properties = {
        f"Official {property_type}": {"id": property_type, "type": property_type, property_type: {}}
        for property_type in SCHEMA_PROPERTY_TYPES
    }
    properties["Unsupported"] = {"id": "unsupported", "type": "unsupported_widget", "unsupported_widget": {}}
    adapter = FakeAdapter(
        pages={"page-types": {"id": "page-types", "title": "Types"}},
        children={"page-types": [{"type": "child_database", "id": "db-types", "child_database": {"title": "Types"}}]},
        databases={
            "db-types": {
                "id": "db-types",
                "title": "Types",
                "properties": properties,
            }
        },
    )

    result = scan_page_target(adapter, "page-types", CacheStore(config), target_id="types")
    schema = result["data_sources"]["db-types"]["schema"]

    assert {value["type"] for value in schema.values()} == SCHEMA_PROPERTY_TYPES
    assert "Unsupported" not in schema



def test_scan_page_preserves_official_button_property_type_without_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-dashboard": {"id": "page-dashboard", "title": "Dashboard"}},
        children={"page-dashboard": [{"type": "child_database", "id": "db-actions", "child_database": {"title": "Actions"}}]},
        databases={
            "db-actions": {
                "id": "db-actions",
                "title": "Actions",
                "properties": {
                    "Name": {"id": "title", "type": "title", "title": {}},
                    "Run": {"id": "button", "type": "button", "button": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-dashboard", CacheStore(config), target_id="dashboard")
    data_source = result["data_sources"]["db-actions"]

    assert data_source["schema"]["Run"] == {"name": "Run", "id": "button", "type": "button"}
    assert data_source["fields"] == {}
    assert result["asset_mapping"] == {}
    assert result["requires_confirmation"] is True



def test_scan_page_does_not_infer_business_fields_from_schema_names(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "书名": {"id": "title", "type": "title", "title": {}},
                    "阅读进度": {"id": "status", "type": "status", "status": {"options": []}},
                    "封面": {"id": "files", "type": "files", "files": {}},
                    "豆瓣链接": {"id": "url", "type": "url", "url": {}},
                    "作者": {"id": "author", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "出版社": {"id": "publisher", "type": "rich_text", "rich_text": {}},
                    "ISBN": {"id": "isbn", "type": "rich_text", "rich_text": {}},
                    "页数": {"id": "pages", "type": "number", "number": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", CacheStore(config), target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert result["requires_confirmation"] is True
    assert result["confirmation_reason"] == "field_mapping_missing"
    assert data_source["role"] == "secondary"
    assert data_source["fields"] == {}
    assert data_source["field_sources"] == {}
    assert data_source["mapping_warnings"] == []
    assert data_source["schema"]["作者"]["type"] == "relation"
    assert data_source["schema"]["ISBN"]["type"] == "rich_text"
    assert data_source["schema"]["页数"]["type"] == "number"
    assert data_source["schema"]["封面"]["type"] == "files"


def test_scan_page_uses_cached_profile_field_mapping_over_ambiguous_schema_names(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "target_id": "bookshelf"},
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "fields": {"title": "标题", "author": "作者页面", "cover": "附件"},
                    "field_sources": {"title": "profile", "author": "profile", "cover": "profile"},
                }
            },
        },
    )
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title-1", "type": "title", "title": {}},
                    "标题": {"id": "title-2", "type": "title", "title": {}},
                    "作者": {"id": "author-1", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "作者页面": {"id": "author-2", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "附件": {"id": "files", "type": "files", "files": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert result["requires_confirmation"] is False
    assert result["confirmation_reason"] is None
    assert data_source["role"] == "primary"
    assert data_source["fields"] == {"title": "标题", "author": "作者页面", "cover": "附件"}
    assert data_source["field_sources"] == {"title": "profile", "author": "profile", "cover": "profile"}
    assert data_source["mapping_warnings"] == []
    assert result["asset_mapping"] == {
        "cover": {"field": "附件", "type": "files", "strategy": "download_and_attach"}
    }


def test_scan_page_uses_target_parser_profile_extra_field_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "target_id": "bookshelf"},
            "parser_profile": {
                "book": {
                    "field_mapping": {
                        "title": "名称",
                        "state": "状态",
                        "url": "URL",
                        "current_page": "当前在读页数",
                        "language": "语言",
                        "country": "国家",
                        "format": "版式",
                        "edition": "版次",
                        "sample_file": "试读文件",
                    }
                }
            },
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "fields": {"cover": "封面", "url": "旧链接"},
                    "field_sources": {"cover": "profile", "url": "explicit"},
                    "parser_profile": {
                        "book": {
                            "field_mapping": {
                                "url": "URL",
                                "publisher": "出版社",
                            }
                        }
                    },
                }
            },
        },
    )
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "状态": {"id": "state", "type": "status", "status": {"options": []}},
                    "URL": {"id": "url", "type": "url", "url": {}},
                    "旧链接": {"id": "old-url", "type": "url", "url": {}},
                    "当前在读页数": {"id": "current", "type": "number", "number": {}},
                    "语言": {"id": "language", "type": "rich_text", "rich_text": {}},
                    "国家": {"id": "country", "type": "rich_text", "rich_text": {}},
                    "版式": {"id": "format", "type": "select", "select": {"options": []}},
                    "版次": {"id": "edition", "type": "rich_text", "rich_text": {}},
                    "试读文件": {"id": "sample", "type": "files", "files": {}},
                    "封面": {"id": "cover", "type": "files", "files": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert data_source["fields"] == {
        "cover": "封面",
        "url": "旧链接",
        "title": "名称",
        "state": "状态",
        "current_page": "当前在读页数",
        "language": "语言",
        "country": "国家",
        "format": "版式",
        "edition": "版次",
        "sample_file": "试读文件",
    }
    assert data_source["field_sources"] == {
        "cover": "profile",
        "url": "explicit",
        "title": "profile",
        "state": "profile",
        "current_page": "profile",
        "language": "profile",
        "country": "profile",
        "format": "profile",
        "edition": "profile",
        "sample_file": "profile",
    }
    assert "publisher" not in data_source["fields"]
    assert result["asset_mapping"]["sample_file"] == {
        "field": "试读文件",
        "type": "files",
        "strategy": "download_and_attach",
    }


def test_scan_page_preserves_existing_target_parser_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    parser_profile = {
        "article": {
            "field_mapping": {
                "headline": "Headline",
                "status": "Status",
            },
            "labels": {"status": {"draft": "Draft"}},
        }
    }
    cache.write_json(
        config.targets_dir / "publishing.json",
        {
            "target": {"page_id": "page-content", "target_id": "publishing"},
            "parser_profile": parser_profile,
            "data_sources": {},
        },
    )
    adapter = FakeAdapter(
        pages={"page-content": {"id": "page-content", "title": "Publishing"}},
        children={"page-content": [{"type": "child_database", "id": "db-articles", "child_database": {"title": "Articles"}}]},
        databases={
            "db-articles": {
                "id": "db-articles",
                "title": "Articles",
                "properties": {
                    "Headline": {"id": "title", "type": "title", "title": {}},
                    "Status": {"id": "status", "type": "select", "select": {"options": []}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-content", cache, target_id="publishing")

    assert result["parser_profile"] == parser_profile
    assert result["data_sources"]["db-articles"]["fields"] == {"headline": "Headline", "status": "Status"}
    saved = json.loads((config.targets_dir / "publishing.json").read_text(encoding="utf-8"))
    assert saved["parser_profile"] == parser_profile



def test_scan_page_preserves_matching_data_source_parser_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    parser_profile = {
        "episode": {
            "field_mapping": {
                "headline": "Title",
                "host": "Host",
            },
            "labels": {"host": {"primary": "Main Host"}},
        }
    }
    cache.write_json(
        config.targets_dir / "media.json",
        {
            "target": {"page_id": "page-media", "target_id": "media"},
            "data_sources": {
                "cached-key": {
                    "data_source_id": "ds-episodes",
                    "parser_profile": parser_profile,
                    "fields": {},
                    "field_sources": {},
                }
            },
        },
    )
    adapter = FakeAdapter(
        pages={"page-media": {"id": "page-media", "title": "Media"}},
        children={"page-media": [{"type": "child_database", "id": "db-media", "child_database": {"title": "Media"}}]},
        databases={
            "db-media": {
                "id": "db-media",
                "title": "Media",
                "data_sources": [{"id": "ds-episodes", "name": "Episodes"}],
                "properties": {},
            }
        },
        data_sources={
            "ds-episodes": {
                "id": "ds-episodes",
                "title": "Episodes",
                "properties": {
                    "Title": {"id": "title", "type": "title", "title": {}},
                    "Host": {"id": "host", "type": "rich_text", "rich_text": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-media", cache, target_id="media")

    data_source = result["data_sources"]["ds-episodes"]
    assert data_source["parser_profile"] == parser_profile
    assert data_source["fields"] == {"headline": "Title", "host": "Host"}
    saved = json.loads((config.targets_dir / "media.json").read_text(encoding="utf-8"))
    assert saved["data_sources"]["ds-episodes"]["parser_profile"] == parser_profile


def test_scan_data_source_target_preserves_trusted_mapping_and_parser_profile_while_adding_location_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    parser_profile = {"book": {"field_mapping": {"title": "Name", "state": "Status"}}}
    cache.write_json(
        config.targets_dir / "books-ds.json",
        {
            "target": {"target_id": "books-ds", "data_source_id": "ds-books"},
            "parser_profile": parser_profile,
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "parser_profile": parser_profile,
                    "fields": {"title": "Name", "state": "Status"},
                    "field_sources": {"title": "explicit", "state": "profile"},
                }
            },
        },
    )
    adapter = FakeAdapter(
        databases={"db-books": {"id": "db-books", "parent": {"type": "page_id", "page_id": "page-books"}}},
        data_sources={
            "ds-books": {
                "id": "ds-books",
                "title": "Books",
                "parent": {"type": "database_id", "database_id": "db-books"},
                "properties": {
                    "Name": {"id": "title", "type": "title", "title": {}},
                    "Status": {"id": "status", "type": "status", "status": {"options": []}},
                },
            }
        },
    )

    result = scan_data_source_target(adapter, "ds-books", cache, target_id="books-ds")

    data_source = result["data_sources"]["ds-books"]
    assert result["parser_profile"] == parser_profile
    assert data_source["parser_profile"] == parser_profile
    assert data_source["fields"] == {"title": "Name", "state": "Status"}
    assert data_source["field_sources"] == {"title": "explicit", "state": "profile"}
    assert data_source["database_id"] == "db-books"
    assert data_source["parent_page_id"] == "page-books"
    saved = json.loads((config.targets_dir / "books-ds.json").read_text(encoding="utf-8"))
    assert saved["data_sources"]["ds-books"]["parser_profile"] == parser_profile
    assert saved["data_sources"]["ds-books"]["parent_page_id"] == "page-books"



def test_scan_page_does_not_infer_extra_business_fields_from_schema_names(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "URL": {"id": "url", "type": "url", "url": {}},
                    "当前在读页数": {"id": "current", "type": "number", "number": {}},
                    "语言": {"id": "language", "type": "rich_text", "rich_text": {}},
                    "国家": {"id": "country", "type": "rich_text", "rich_text": {}},
                    "版式": {"id": "format", "type": "select", "select": {"options": []}},
                    "版次": {"id": "edition", "type": "rich_text", "rich_text": {}},
                    "试读文件": {"id": "sample", "type": "files", "files": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", CacheStore(config), target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert result["requires_confirmation"] is True
    assert result["confirmation_reason"] == "field_mapping_missing"
    assert data_source["fields"] == {}
    assert data_source["field_sources"] == {}
    assert data_source["schema"]["语言"]["type"] == "rich_text"
    assert data_source["schema"]["试读文件"]["type"] == "files"


def test_scan_page_requires_confirmation_when_field_mapping_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title-1", "type": "title", "title": {}},
                    "标题": {"id": "title-2", "type": "title", "title": {}},
                    "作者": {"id": "author-1", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "作者页面": {"id": "author-2", "type": "relation", "relation": {"database_id": "db-authors"}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", CacheStore(config), target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert result["requires_confirmation"] is True
    assert result["confirmation_reason"] == "field_mapping_missing"
    assert data_source["fields"] == {}
    assert data_source["field_sources"] == {}
    assert data_source["mapping_warnings"] == []


def test_scan_page_does_not_require_confirmation_for_profile_mapped_podcast_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "podcastshelf", "db-episodes", {"title": "标题", "state": "状态", "podcast": "播客"})
    adapter = FakeAdapter(
        pages={"page-podcasts": {"id": "page-podcasts", "title": "播客库"}},
        children={"page-podcasts": [{"type": "child_database", "id": "db-episodes", "child_database": {"title": "Episodes"}}]},
        databases={
            "db-episodes": {
                "id": "db-episodes",
                "title": "Episodes",
                "properties": {
                    "标题": {"id": "title", "type": "title", "title": {}},
                    "播客": {"id": "podcast", "type": "relation", "relation": {"database_id": "db-podcasts"}},
                    "状态": {"id": "state", "type": "select", "select": {"options": []}},
                    "Pages": {"id": "pages-text", "type": "rich_text", "rich_text": {}},
                    "Page Count": {"id": "pages-number", "type": "number", "number": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-podcasts", cache, target_id="podcastshelf")
    data_source = result["data_sources"]["db-episodes"]

    assert result["requires_confirmation"] is False
    assert result["confirmation_reason"] is None
    assert data_source["role"] == "primary"
    assert data_source["fields"] == {
        "title": "标题",
        "state": "状态",
        "podcast": "播客",
    }
    assert data_source["mapping_warnings"] == []


def test_scan_page_builds_state_mapping_from_profile_mapped_select_state_field(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "bookshelf", "db-books", {"title": "名称", "state": "状态"})
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "状态": {
                        "id": "state",
                        "type": "select",
                        "select": {"options": [{"name": "想读", "color": "blue"}]},
                    },
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert data_source["fields"]["state"] == "状态"
    assert result["state_mapping"] == {"field": "状态", "values": {}}


def test_scan_page_preserves_cached_state_mapping_values(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "bookshelf", "db-books", {"title": "名称", "state": "状态"})
    cached = json.loads((config.targets_dir / "bookshelf.json").read_text(encoding="utf-8"))
    cached["state_mapping"] = {"field": "状态", "values": {"completed": "完成"}}
    cache.write_json(config.targets_dir / "bookshelf.json", cached)
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "状态": {
                        "id": "state",
                        "type": "status",
                        "status": {"options": [{"name": "完成", "color": "green"}]},
                    },
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")

    assert result["state_mapping"] == {"field": "状态", "values": {"completed": "完成"}}



def test_scan_page_builds_cover_asset_mapping_from_profile_mapped_cover_field(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_profile_mapping(config, "bookshelf", "db-books", {"title": "名称", "cover": "封面图"})
    adapter = FakeAdapter(
        pages={"page-books": {"id": "page-books", "title": "书单"}},
        children={"page-books": [{"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}]},
        databases={
            "db-books": {
                "id": "db-books",
                "title": "Books",
                "properties": {
                    "名称": {"id": "title", "type": "title", "title": {}},
                    "附件": {"id": "files-1", "type": "files", "files": {}},
                    "封面图": {"id": "files-2", "type": "files", "files": {}},
                },
            }
        },
    )

    result = scan_page_target(adapter, "page-books", cache, target_id="bookshelf")
    data_source = result["data_sources"]["db-books"]

    assert data_source["fields"]["cover"] == "封面图"
    assert result["asset_mapping"] == {
        "cover": {"field": "封面图", "type": "files", "strategy": "download_and_attach"},
        "附件": {"field": "附件", "type": "files", "strategy": "download_and_attach"},
    }


def test_build_asset_mapping_includes_non_cover_mapped_files_field():
    result = _build_asset_mapping(
        {
            "db-books": {
                "fields": {"cover": "封面图", "attachment": "附件", "notes": "备注"},
                "schema": {
                    "封面图": {"type": "files"},
                    "附件": {"type": "files"},
                    "备注": {"type": "rich_text"},
                },
            }
        }
    )

    assert result == {
        "cover": {"field": "封面图", "type": "files", "strategy": "download_and_attach"},
        "attachment": {"field": "附件", "type": "files", "strategy": "download_and_attach"},
    }


def test_scan_page_graph_uses_title_property_for_record_page_title(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "parent": {"type": "data_source_id", "data_source_id": "ds-podcasts"},
                "properties": {
                    "状态": {"id": "status", "type": "status", "status": {"name": "完成"}},
                    "播客名称": {
                        "id": "title",
                        "type": "title",
                        "title": [{"plain_text": "独树不成林"}],
                    },
                },
            }

        def list_block_children(self, block_id):
            return []

    graph = scan_page_graph(Adapter(), "page-podcast", store, graph_id="graph-podcast")

    assert graph["pages"]["page-podcast"]["title"] == "独树不成林"
    assert store.read_graph("graph-podcast")["pages"]["page-podcast"]["title"] == "独树不成林"



def test_scan_page_graph_includes_record_page_data_source_ancestor_chain(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.path_utils import graph_object_path
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            pages = {
                "page-episode": {
                    "id": "page-episode",
                    "parent": {"type": "data_source_id", "data_source_id": "ds-episodes"},
                    "properties": {
                        "播客名称": {
                            "id": "title",
                            "type": "title",
                            "title": [{"plain_text": "独树不成林"}],
                        }
                    },
                },
                "page-podcasts": {
                    "id": "page-podcasts",
                    "parent": {"type": "workspace", "workspace": True},
                    "properties": {
                        "名称": {
                            "id": "title",
                            "type": "title",
                            "title": [{"plain_text": "播客"}],
                        }
                    },
                },
            }
            return pages[page_id]

        def list_block_children(self, block_id):
            return []

        def retrieve_data_source(self, data_source_id):
            assert data_source_id == "ds-episodes"
            return {
                "id": "ds-episodes",
                "title": [{"plain_text": "Rows"}],
                "parent": {"type": "database_id", "database_id": "db-episodes"},
                "properties": {
                    "主题": {"id": "title", "name": "主题", "type": "title"},
                    "状态": {"id": "status", "name": "状态", "type": "status"},
                },
            }

        def retrieve_database(self, database_id):
            assert database_id == "db-episodes"
            return {
                "id": "db-episodes",
                "title": [{"plain_text": "Episodes"}],
                "parent": {"type": "page_id", "page_id": "page-podcasts"},
                "data_sources": [{"id": "ds-episodes"}],
            }

    graph = scan_page_graph(Adapter(), "page-episode", store, graph_id="graph-episode")

    assert graph["pages"]["page-episode"]["parent"] == {"type": "data_source_id", "id": "ds-episodes"}
    assert graph["data_sources"]["ds-episodes"]["database_id"] == "db-episodes"
    assert graph["databases"]["db-episodes"]["parent"] == {"type": "page_id", "id": "page-podcasts"}
    assert graph["pages"]["page-podcasts"]["title"] == "播客"
    assert graph_object_path(graph, "page-episode", "page") == {
        "path": "工作区顶层 / 播客 / Episodes / Rows / 独树不成林",
        "path_complete": True,
    }
    assert store.read_graph("graph-episode")["data_sources"]["ds-episodes"]["database_id"] == "db-episodes"



def test_scan_page_graph_retrieves_full_child_database_views(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}, "properties": {}}

        def list_block_children(self, block_id):
            return [
                {
                    "id": "db-episodes",
                    "type": "child_database",
                    "parent": {"type": "page_id", "page_id": "page-podcasts"},
                    "child_database": {"title": "Episodes"},
                    "has_children": False,
                }
            ]

        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"plain_text": "Episodes"}],
                "parent": {"type": "page_id", "page_id": "page-podcasts"},
                "properties": {"Title": {"id": "title", "name": "Title", "type": "title"}},
            }

        def list_views(self, data_source_id=None, database_id=None):
            assert database_id == "db-episodes"
            assert data_source_id is None
            return [{"object": "view", "id": "view-gallery"}]

        def retrieve_view(self, view_id):
            assert view_id == "view-gallery"
            return {
                "object": "view",
                "id": "view-gallery",
                "name": "Episode Gallery",
                "type": "gallery",
                "database_id": "db-episodes",
                "data_source_id": "ds-episodes",
                "configuration": {"gallery": {"card_size": "medium"}},
                "sorts": [{"property": "Publish Date", "direction": "descending"}],
                "filter": {"property": "Status", "select": {"equals": "Published"}},
                "quick_filters": {"Status": ["Published"]},
            }

    graph = scan_page_graph(Adapter(), "page-podcasts", store, graph_id="graph-views")

    assert graph["views"]["view-gallery"] == {
        "object": "view",
        "view_id": "view-gallery",
        "name": "Episode Gallery",
        "type": "gallery",
        "database_id": "db-episodes",
        "data_source_id": "ds-episodes",
        "location": {
            "type": "page_id",
            "id": "page-podcasts",
            "discovered_from": "page_scan",
            "source_block_id": "db-episodes",
            "source_block_type": "child_database",
            "display_title": "Episodes",
        },
        "filter": {"property": "Status", "select": {"equals": "Published"}},
        "sorts": [{"property": "Publish Date", "direction": "descending"}],
        "quick_filters": {"Status": ["Published"]},
        "configuration": {"gallery": {"card_size": "medium"}},
    }
    assert store.read_graph("graph-views")["views"]["view-gallery"]["configuration"] == {
        "gallery": {"card_size": "medium"}
    }


def test_scan_page_graph_caches_child_database_view_source_schema(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}, "properties": {}}

        def list_block_children(self, block_id):
            return [
                {
                    "id": "db-episodes",
                    "type": "child_database",
                    "parent": {"type": "page_id", "page_id": "page-podcasts"},
                    "child_database": {"title": "Episodes"},
                    "has_children": False,
                }
            ]

        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"plain_text": "Episodes"}],
                "parent": {"type": "page_id", "page_id": "page-podcasts"},
                "data_sources": [{"id": "ds-episodes"}],
            }

        def retrieve_data_source(self, data_source_id):
            return {
                "id": data_source_id,
                "parent": {"type": "database_id", "database_id": "db-episodes"},
                "properties": {
                    "Title": {"id": "title", "name": "Title", "type": "title"},
                    "Status": {"id": "status", "name": "Status", "type": "status"},
                },
            }

        def list_views(self, data_source_id=None, database_id=None):
            assert database_id == "db-episodes"
            assert data_source_id is None
            return [{"object": "view", "id": "view-gallery"}]

        def retrieve_view(self, view_id):
            return {
                "object": "view",
                "id": view_id,
                "name": "Episode Gallery",
                "type": "gallery",
                "database_id": "db-episodes",
                "data_source_id": "ds-episodes",
                "sorts": [{"property": "Status", "direction": "ascending"}],
            }

    graph = scan_page_graph(Adapter(), "page-podcasts", store, graph_id="graph-view-schema")

    assert graph["views"]["view-gallery"]["_source_schema"] == {
        "Title": {"id": "title", "name": "Title", "type": "title"},
        "Status": {"id": "status", "name": "Status", "type": "status"},
    }
    assert store.read_graph("graph-view-schema")["views"]["view-gallery"]["_source_schema"] == graph["views"]["view-gallery"]["_source_schema"]



def test_scan_page_graph_exposes_child_database_visual_section_path(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.path_utils import graph_visual_path
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "parent": {"type": "workspace"},
                "properties": {
                    "title": {
                        "type": "title",
                        "title": [{"plain_text": "书单"}],
                    }
                },
            }

        def list_block_children(self, block_id):
            return [
                {
                    "id": "heading-reading",
                    "type": "heading_2",
                    "parent": {"type": "page_id", "page_id": "page-books"},
                    "heading_2": {"rich_text": [{"plain_text": "在读列表"}]},
                    "has_children": False,
                },
                {
                    "id": "db-current-reading",
                    "type": "child_database",
                    "parent": {"type": "page_id", "page_id": "page-books"},
                    "child_database": {"title": "正在阅读"},
                    "has_children": False,
                },
            ]

        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"plain_text": "正在阅读"}],
                "parent": {"type": "page_id", "page_id": "page-books"},
                "data_sources": [{"id": "ds-books"}],
            }

        def retrieve_data_source(self, data_source_id):
            return {
                "id": data_source_id,
                "title": [{"plain_text": "Books"}],
                "parent": {"type": "database_id", "database_id": "db-current-reading"},
                "properties": {"名称": {"id": "title", "name": "名称", "type": "title"}},
            }

        def list_views(self, data_source_id=None, database_id=None):
            assert database_id == "db-current-reading"
            return [{"object": "view", "id": "view-current-reading"}]

        def retrieve_view(self, view_id):
            return {
                "object": "view",
                "id": view_id,
                "name": "正在阅读",
                "type": "gallery",
                "database_id": "db-current-reading",
                "data_source_id": "ds-books",
            }

    graph = scan_page_graph(Adapter(), "page-books", store, graph_id="graph-books")

    assert graph["views"]["view-current-reading"]["location"] == {
        "type": "page_id",
        "id": "page-books",
        "discovered_from": "page_scan",
        "source_block_id": "db-current-reading",
        "source_block_type": "child_database",
        "display_title": "正在阅读",
        "section_path": ["在读列表"],
    }
    assert graph_visual_path(graph, "view-current-reading", "view") == {
        "path": "工作区顶层 / 书单 / 在读列表 / 正在阅读",
        "path_complete": True,
    }


def test_scan_page_graph_adds_view_data_source_when_database_metadata_omits_it(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}, "properties": {}}

        def list_block_children(self, block_id):
            return [
                {
                    "id": "db-books",
                    "type": "child_database",
                    "parent": {"type": "page_id", "page_id": "page-books"},
                    "child_database": {"title": "正在阅读"},
                    "has_children": False,
                }
            ]

        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"plain_text": "正在阅读"}],
                "parent": {"type": "page_id", "page_id": "page-books"},
                "properties": {},
            }

        def retrieve_data_source(self, data_source_id):
            assert data_source_id == "ds-books"
            return {
                "id": "ds-books",
                "title": [{"plain_text": "Books"}],
                "parent": {"type": "database_id", "database_id": "db-books"},
                "properties": {"名称": {"id": "title", "name": "名称", "type": "title"}},
            }

        def list_views(self, data_source_id=None, database_id=None):
            assert database_id == "db-books"
            return [{"object": "view", "id": "view-reading"}]

        def retrieve_view(self, view_id):
            return {
                "object": "view",
                "id": "view-reading",
                "name": "正在阅读",
                "type": "gallery",
                "database_id": "db-books",
                "data_source_id": "ds-books",
            }

    graph = scan_page_graph(Adapter(), "page-books", store, graph_id="graph-books")

    assert graph["data_sources"]["ds-books"]["title"] == "Books"
    assert graph["data_sources"]["ds-books"]["database_id"] == "db-books"
    assert graph["data_sources"]["ds-books"]["schema"] == {"名称": {"id": "title", "name": "名称", "type": "title"}}
    assert graph["views"]["view-reading"]["_source_schema"] == {"名称": {"id": "title", "name": "名称", "type": "title"}}



def test_scan_page_target_writes_v2_graph(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_page_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "parent": {"type": "workspace"}, "properties": {}}

        def list_block_children(self, block_id):
            return [
                {
                    "id": "db-1",
                    "type": "child_database",
                    "parent": {"type": "page_id", "page_id": "page-1"},
                    "child_database": {"title": "Episodes"},
                    "has_children": False,
                }
            ]

        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"plain_text": "Episodes"}],
                "parent": {"type": "page_id", "page_id": "page-1"},
                "is_inline": True,
                "data_sources": [{"id": "ds-1"}],
            }

        def retrieve_data_source(self, data_source_id):
            return {
                "id": data_source_id,
                "title": [{"plain_text": "Rows"}],
                "parent": {"type": "database_id", "database_id": "db-1"},
                "database_parent": {"type": "page_id", "page_id": "page-1"},
                "properties": {"Name": {"id": "title", "name": "Name", "type": "title"}},
            }

        def list_views(self, data_source_id=None, database_id=None):
            assert database_id == "db-1" or data_source_id == "ds-1"
            return [{"id": "view-1"}]

        def retrieve_view(self, view_id):
            assert view_id == "view-1"
            return {"id": "view-1", "name": "Episodes", "type": "gallery", "database_id": "db-1", "data_source_id": "ds-1"}

    graph = scan_page_graph(Adapter(), "page-1", store, graph_id="graph-1")

    assert graph["cache_version"] == 2
    assert graph["root"] == {"kind": "page", "id": "page-1"}
    assert "page-1" in graph["pages"]
    assert "db-1" in graph["databases"]
    assert "ds-1" in graph["data_sources"]
    assert graph["data_sources"]["ds-1"]["database_id"] == "db-1"
    assert graph["data_sources"]["ds-1"]["parent_page_id"] == "page-1"
    assert graph["data_sources"]["ds-1"]["parent_type"] == "database_id"
    assert graph["data_sources"]["ds-1"]["source_block_id"] == "db-1"
    assert graph["data_sources"]["ds-1"]["source_block_type"] == "child_database"
    assert graph["views"]["view-1"]["type"] == "gallery"
    assert graph["views"]["view-1"]["location"] == {
        "type": "page_id",
        "id": "page-1",
        "discovered_from": "page_scan",
        "source_block_id": "db-1",
        "source_block_type": "child_database",
        "display_title": "Episodes",
    }
    assert store.read_graph("graph-1")["views"]["view-1"]["data_source_id"] == "ds-1"


def test_scan_data_source_graph_keeps_target_when_parent_page_is_inaccessible(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.notion_adapter import NotionPermissionError
    from capture_to_notion.scanner import scan_data_source_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        def retrieve_data_source(self, data_source_id):
            return {
                "id": data_source_id,
                "title": [{"plain_text": "Rows"}],
                "parent": {"type": "database_id", "database_id": "db-1"},
                "properties": {"Name": {"id": "title", "name": "Name", "type": "title"}},
            }

        def retrieve_database(self, database_id):
            return {
                "id": database_id,
                "title": [{"plain_text": "Episodes"}],
                "parent": {"type": "page_id", "page_id": "page-private"},
                "data_sources": [{"id": "ds-1"}],
            }

        def retrieve_page(self, page_id):
            raise NotionPermissionError("parent page is not shared")

        def list_views(self, data_source_id=None, database_id=None):
            return []

    graph = scan_data_source_graph(Adapter(), "ds-1", store, graph_id="graph-private-parent")

    assert graph["data_sources"]["ds-1"]["database_id"] == "db-1"
    assert graph["databases"]["db-1"]["parent"] == {"type": "page_id", "id": "page-private"}
    assert graph["pages"] == {}
    assert store.read_graph("graph-private-parent")["data_sources"]["ds-1"]["data_source_id"] == "ds-1"



def test_scan_data_source_graph_writes_parent_location_facts(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.scanner import scan_data_source_graph

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    class Adapter:
        database_calls = 0

        def retrieve_data_source(self, data_source_id):
            return {
                "id": data_source_id,
                "title": [{"plain_text": "Rows"}],
                "parent": {"type": "database_id", "database_id": "db-1"},
                "properties": {"Name": {"id": "title", "name": "Name", "type": "title"}},
            }

        def retrieve_database(self, database_id):
            self.database_calls += 1
            return {
                "id": database_id,
                "title": [{"plain_text": "Episodes"}],
                "parent": {"type": "page_id", "page_id": "page-1"},
                "data_sources": [{"id": "ds-1"}],
            }

        def list_views(self, data_source_id=None, database_id=None):
            return []

    adapter = Adapter()
    graph = scan_data_source_graph(adapter, "ds-1", store, graph_id="graph-1")

    assert adapter.database_calls == 1
    assert graph["root"] == {"kind": "data_source", "id": "ds-1"}
    assert graph["data_sources"]["ds-1"]["database_id"] == "db-1"
    assert graph["data_sources"]["ds-1"]["parent_page_id"] == "page-1"
    assert graph["data_sources"]["ds-1"]["parent_type"] == "database_id"
    assert store.read_graph("graph-1")["data_sources"]["ds-1"]["parent_page_id"] == "page-1"
