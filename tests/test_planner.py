import json
from pathlib import Path

import pytest

from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config
from capture_to_notion.models import CaptureInput, CaptureOptions, Target, WritePlan
from capture_to_notion.planner import build_asset_operations, build_capture_plan, build_plan_field_mapping, extract_labeled_value


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def seed_book_target(config):
    write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "description": "书籍、作者、阅读状态、封面",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "verified_at": "2026-05-05T10:00:00Z"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "schema_hash": "abc123",
                    "fields": {
                        "title": "名称",
                        "author": "作者",
                        "isbn": "ISBN",
                        "publisher": "出版社",
                        "state": "阅读状态",
                        "cover": "封面"
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {"initialized": "想读", "completed": "已读"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )


def seed_podcast_target(config):
    write_json(
        config.aliases_file,
        {
            "aliases": {
                "播客库": {
                    "type": "page",
                    "page_id": "page-podcasts",
                    "description": "播客节目、单集、状态、封面",
                    "target_id": "podcastshelf",
                }
            }
        },
    )
    write_json(
        config.targets_dir / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "播客库", "verified_at": "2026-05-05T10:00:00Z"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "schema_hash": "abc123",
                    "fields": {
                        "title": "标题",
                        "podcast": "播客",
                        "episode_url": "链接",
                        "published_at": "发布日期",
                        "state": "收听状态",
                        "cover": "封面",
                    },
                }
            },
            "state_mapping": {"field": "收听状态", "values": {"initialized": "想听", "completed": "已听"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )


def test_build_asset_operations_includes_non_cover_files_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()

    operations = build_asset_operations(
        config,
        "book",
        {"cover": "https://example.com/cover.jpg", "attachment": "https://example.com/file.pdf"},
        {
            "cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"},
            "attachment": {"field": "附件", "type": "files", "strategy": "download_and_attach"},
        },
        allow_download=True,
    )

    attachment_operation = next(operation for operation in operations if operation.record_key == "attachment")
    assert attachment_operation.source_url == "https://example.com/file.pdf"
    assert attachment_operation.target_field == "附件"
    assert attachment_operation.action == "download_and_attach"


def test_build_plan_field_mapping_skips_empty_asset_record_fields():
    field_mapping = build_plan_field_mapping(
        normalized_record={
            "title": "县乡中国",
            "cover": "https://example.com/cover.jpg",
            "empty_attachment": None,
        },
        fields={"title": "书名"},
        schema={
            "封面": {"type": "files"},
            "附件": {"type": "files"},
        },
        asset_mapping={
            "cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"},
            "empty_attachment": {"field": "附件", "type": "files", "strategy": "download_and_attach"},
            "missing_attachment": {"field": "附件", "type": "files", "strategy": "download_and_attach"},
        },
    )

    assert field_mapping == {
        "title": "书名",
        "cover": "封面",
    }



def test_write_plan_serializes_summary_near_review_inputs():
    plan = WritePlan(
        plan_id="20260512-demo",
        content_type="book",
        target=Target(
            page_title="书单",
            page_id="page-books",
            data_source_id="ds-books",
            confidence="high",
            source="alias_cache",
        ),
        summary={
            "target_page": "书单",
            "target_data_source": "Books",
            "title": "可能性的艺术",
            "state": "initialized",
            "requires_confirmation": False,
        },
        normalized_record={"title": "可能性的艺术", "state": "initialized"},
        field_mapping={"title": "名称", "state": "阅读状态"},
        operations=[{"type": "create_or_update_page"}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )

    data = plan.to_dict()

    assert data["summary"] == {
        "target_page": "书单",
        "target_data_source": "Books",
        "title": "可能性的艺术",
        "state": "initialized",
        "requires_confirmation": False,
    }
    assert list(data).index("summary") < list(data).index("normalized_record")
    assert WritePlan.from_dict(data).summary == data["summary"]


def test_builds_book_capture_plan_from_cached_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        user_intent="capture_to_notion",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.content_type == "book"
    assert plan.target.page_title == "书单"
    assert plan.target.data_source_id == "ds-books"
    assert plan.normalized_record["state"] == "initialized"
    assert plan.field_mapping["cover"] == "封面"
    assert plan.asset_operations[0].action == "download_and_attach"
    assert plan.asset_operations[0].target_field == "封面"
    assert plan.requires_confirmation is False


def test_book_labeled_author_populates_normalized_record_and_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["title"] == "可能性的艺术"
    assert plan.normalized_record["author"] == "刘瑜"
    assert plan.field_mapping["author"] == "作者"


def test_book_without_labeled_author_keeps_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["author"] is None


def test_extract_labeled_author_stops_before_following_label():
    assert extract_labeled_value("作者：刘瑜，出版社：广西师范大学出版社", ["作者", "author"]) == "刘瑜"


@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("author: Liu Yu, publisher: Guangxi Normal University Press", "Liu Yu"),
        ("作者：刘瑜，出版社：广西师范大学出版社", "刘瑜"),
        ("author: Liu Yu; publisher: Guangxi Normal University Press", "Liu Yu"),
        ("作者：刘瑜；出版社：广西师范大学出版社", "刘瑜"),
        ("author: Liu Yu | publisher: Guangxi Normal University Press", "Liu Yu"),
        ("作者：刘瑜｜出版社：广西师范大学出版社", "刘瑜"),
        ("author: Liu Yu publisher: Guangxi Normal University Press", "Liu Yu"),
    ],
)
def test_extract_labeled_author_stops_before_following_label_delimiters(raw_input, expected):
    assert extract_labeled_value(raw_input, ["作者", "author"]) == expected


def test_extract_labeled_author_supports_english_and_colon_variants():
    assert extract_labeled_value("author: Liu Yu", ["作者", "author"]) == "Liu Yu"
    assert extract_labeled_value("作者: 刘瑜", ["作者", "author"]) == "刘瑜"


def test_podcast_labeled_podcast_populates_normalized_record_and_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_podcast_target(config)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.content_type == "podcast_episode"
    assert plan.normalized_record["podcast"] == "忽左忽右"
    assert plan.field_mapping["podcast"] == "播客"


def test_extract_labeled_podcast_supports_english_and_program_labels():
    assert extract_labeled_value("podcast: Acquired", ["播客", "podcast", "节目"]) == "Acquired"
    assert extract_labeled_value("节目：忽左忽右", ["播客", "podcast", "节目"]) == "忽左忽右"


@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("podcast: Acquired url: https://example.com", "Acquired"),
        ("节目：忽左忽右｜标题：某一期", "忽左忽右"),
        ("播客：忽左忽右 published_at: 2026-05-10", "忽左忽右"),
    ],
)
def test_extract_labeled_podcast_stops_before_following_labels(raw_input, expected):
    assert extract_labeled_value(raw_input, ["播客", "podcast", "节目"]) == expected


def test_podcast_title_cleanup_strips_explicit_podcast_label(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_podcast_target(config)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 podcast: Acquired",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["podcast"] == "Acquired"
    assert plan.normalized_record["title"] == "收藏这期播客到播客库"


def test_podcast_without_labeled_podcast_keeps_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_podcast_target(config)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["podcast"] is None


def test_unknown_target_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()

    capture = CaptureInput(raw_input="存一下这期播客 https://example.com/episode/1", options=CaptureOptions())

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.content_type == "podcast_episode"
    assert plan.target.confidence == "none"
    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "target_not_resolved"


def test_alias_target_structure_missing_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )

    capture = CaptureInput(raw_input="把《可能性的艺术》初始化到书单", target_hint="书单", options=CaptureOptions())

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "target_structure_missing"


def test_primary_data_source_missing_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "secondary",
                    "content_types": ["book"],
                    "fields": {"title": "名称", "cover": "封面"},
                }
            },
        },
    )

    capture = CaptureInput(raw_input="把《可能性的艺术》初始化到书单", target_hint="书单", options=CaptureOptions())

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "primary_data_source_missing"


def test_allow_asset_download_false_uses_external_url_action(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        content_type_hint="book",
        options=CaptureOptions(allow_asset_download=False),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.asset_operations[0].action == "attach_external_url"
    assert plan.asset_operations[0].local_cache_path is None


def test_corrupt_aliases_json_falls_back_without_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    config.aliases_file.parent.mkdir(parents=True, exist_ok=True)
    config.aliases_file.write_text("{not-valid-json", encoding="utf-8")

    capture = CaptureInput(raw_input="把《可能性的艺术》初始化到书单", target_hint="书单", options=CaptureOptions())

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "target_not_resolved"


def test_capture_plan_merges_scanned_files_asset_mapping_into_field_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "书名",
                        "state": "阅读进度",
                        "cover": "封面图",
                        "author": "作者",
                        "publisher": "出版社",
                        "isbn": "ISBN",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面图": {"type": "files"},
                        "附件": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "出版社": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "阅读进度", "values": {}},
            "asset_mapping": {
                "cover": {"field": "封面图", "type": "files", "strategy": "download_and_attach"},
                "附件": {"field": "附件", "type": "files", "strategy": "download_and_attach"},
            },
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert "附件" not in plan.field_mapping
    assert plan.field_mapping["cover"] == "封面图"
    assert plan.field_mapping["title"] == "书名"
    assert plan.field_mapping["state"] == "阅读进度"


def test_capture_plan_uses_semantic_fields_from_scanned_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "书名",
                        "state": "阅读进度",
                        "cover": "封面图",
                        "author": "作者",
                        "publisher": "出版社",
                        "isbn": "ISBN",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面图": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "出版社": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "阅读进度", "values": {}},
            "asset_mapping": {"cover": {"field": "封面图", "type": "files", "strategy": "download_and_attach"}},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.field_mapping == {
        "title": "书名",
        "state": "阅读进度",
        "cover": "封面图",
    }


def test_book_capture_plan_requires_confirmation_for_minimal_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "db-minimal": {
                    "data_source_id": "db-minimal",
                    "title": "数据库",
                    "role": "primary",
                    "content_types": [],
                    "fields": {"title": "Name", "tag": "Tags"},
                    "mapping_warnings": [],
                    "schema": {
                        "Name": {"type": "title"},
                        "Tags": {"type": "multi_select"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《县乡中国》存到书单在读列表",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "book_schema_incomplete"
    assert plan.operations == []
    assert plan.asset_operations == []
    assert "book_schema_incomplete:cover,author,isbn,state" in plan.warnings


def test_capture_plan_uses_primary_cover_field_over_stale_global_asset_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "db-posters": {
                    "data_source_id": "db-posters",
                    "title": "Posters",
                    "role": "secondary",
                    "content_types": [],
                    "fields": {"cover": "海报"},
                    "mapping_warnings": [],
                    "schema": {"海报": {"type": "files"}},
                },
                "db-books": {
                    "data_source_id": "db-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "书名",
                        "state": "阅读进度",
                        "cover": "封面",
                        "author": "作者",
                        "isbn": "ISBN",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "ISBN": {"type": "rich_text"},
                    },
                },
            },
            "relations": [],
            "state_mapping": {"field": "阅读进度", "values": {}},
            "asset_mapping": {"cover": {"field": "海报", "type": "files", "strategy": "download_and_attach"}},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《县乡中国》存到书单在读列表",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.field_mapping["cover"] == "封面"
    assert plan.asset_operations[0].target_field == "封面"



def test_book_capture_plan_requires_confirmation_when_cover_field_is_generic_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "书名",
                        "state": "阅读进度",
                        "cover": "附件",
                        "author": "作者",
                        "isbn": "ISBN",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "附件": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "ISBN": {"type": "rich_text"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "阅读进度", "values": {}},
            "asset_mapping": {"cover": {"field": "附件", "type": "files", "strategy": "download_and_attach"}},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《县乡中国》存到书单在读列表",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "book_schema_incomplete"
    assert plan.operations == []
    assert plan.asset_operations == []
    assert "book_schema_incomplete:cover" in plan.warnings



def test_capture_plan_preserves_scanned_confirmation_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "书名",
                        "state": "阅读进度",
                        "cover": "封面图",
                        "author": "作者",
                        "publisher": "出版社",
                        "isbn": "ISBN",
                    },
                    "mapping_warnings": ["ambiguous_field_mapping:tag:标签,分类"],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面图": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "出版社": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "阅读进度", "values": {}},
            "asset_mapping": {"cover": {"field": "封面图", "type": "files", "strategy": "download_and_attach"}},
            "requires_confirmation": True,
            "confirmation_reason": "field_mapping_ambiguous",
        },
    )
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "field_mapping_ambiguous"
    assert plan.operations == []
    assert plan.asset_operations == []
    assert "ambiguous_field_mapping:tag:标签,分类" in plan.warnings
    assert plan.normalized_record == {
        "title": "可能性的艺术",
        "state": "initialized",
        "cover": plan.normalized_record["cover"],
        "author": None,
        "isbn": None,
        "publisher": None,
    }
    assert plan.normalized_record["cover"].startswith("https://example.com/capture-to-notion/covers/")
    assert plan.normalized_record["cover"].endswith(".jpg")
    assert plan.field_mapping == {
        "title": "书名",
        "state": "阅读进度",
        "cover": "封面图",
    }


def test_build_asset_operations_uses_safe_cache_path_for_record_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()

    operations = build_asset_operations(
        config,
        "book",
        {"../附件/危险": "https://example.com/file.jpg"},
        {
            "../附件/危险": {
                "field": "附件",
                "type": "files",
                "strategy": "download_and_attach",
            }
        },
    )

    cache_path = Path(operations[0].local_cache_path)
    relative_parts = cache_path.relative_to(config.covers_dir.parent).parts
    assert ".." not in relative_parts
    assert relative_parts[0] != "../附件/危险"
