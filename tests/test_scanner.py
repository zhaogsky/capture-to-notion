import json

from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config
from capture_to_notion.scanner import _build_asset_mapping, _primary_score, scan_page_target


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


def seed_profile_mapping(config, target_id, data_source_id, fields):
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
        }
    ) == 0


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
                        "relation": {"database_id": "db-authors", "type": "single_property"},
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
    assert data_source["schema_hash"] == scan_page_target(
        adapter,
        "page-books",
        CacheStore(config),
        target_id="bookshelf-second",
    )["data_sources"]["db-books"]["schema_hash"]
    assert result["relations"] == [
        {"data_source_id": "db-books", "field": "作者", "target_database_id": "db-authors"}
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
                    "封面图": {"id": "files", "type": "files", "files": {}},
                    "豆瓣链接": {"id": "url", "type": "url", "url": {}},
                    "作者": {"id": "author", "type": "relation", "relation": {"database_id": "db-authors"}},
                    "出版社": {"id": "publisher", "type": "rich_text", "rich_text": {}},
                    "ISBN": {"id": "isbn", "type": "rich_text", "rich_text": {}},
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
