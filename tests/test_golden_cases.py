from __future__ import annotations

import json

from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config
from capture_to_notion.models import CaptureInput, CaptureOptions
from capture_to_notion.planner import build_capture_plan


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


BOOK_PARSER_PROFILE = {
    "book": {
        "labels": {
            "author": ["作者", "author"],
            "isbn": ["ISBN", "isbn"],
            "publisher": ["出版社", "publisher"],
            "page_count": ["页数", "pages", "page_count"],
        }
    }
}


PODCAST_PARSER_PROFILE = {
    "podcast_episode": {
        "labels": {
            "podcast": ["播客", "podcast", "节目"],
        }
    }
}


def seed_books_target(config, *, include_isbn: bool = True, include_cover: bool = True, include_page_count: bool = True) -> None:
    fields = {
        "title": "名称",
        "author": "作者",
        "state": "阅读状态",
    }
    schema = {
        "名称": {"type": "title"},
        "作者": {"type": "rich_text"},
        "阅读状态": {"type": "status"},
    }
    if include_isbn:
        fields["isbn"] = "ISBN"
        schema["ISBN"] = {"type": "rich_text"}
    if include_page_count:
        fields["page_count"] = "页数"
        schema["页数"] = {"type": "number"}
    if include_cover:
        fields["cover"] = "封面"
        schema["封面"] = {"type": "files"}
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
            "target": {"page_id": "page-books", "title": "书单", "verified_at": "2026-05-11T00:00:00Z"},
            "parser_profile": BOOK_PARSER_PROFILE,
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "schema_hash": "abc123",
                    "fields": fields,
                    "schema": schema,
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {"initialized": "想读", "completed": "已读"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )


def seed_podcast_target(config) -> None:
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
            "target": {"page_id": "page-podcasts", "title": "播客库", "verified_at": "2026-05-11T00:00:00Z"},
            "parser_profile": PODCAST_PARSER_PROFILE,
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "schema_hash": "pod123",
                    "fields": {
                        "title": "标题",
                        "podcast": "播客",
                        "state": "收听状态",
                        "cover": "封面",
                    },
                    "schema": {
                        "标题": {"type": "title"},
                        "播客": {"type": "rich_text"},
                        "收听状态": {"type": "status"},
                        "封面": {"type": "files"},
                    },
                }
            },
            "state_mapping": {"field": "收听状态", "values": {"initialized": "想听", "completed": "已听"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )


def test_golden_initialized_book_plan_has_core_fields_and_cover_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_books_target(config)
    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.content_type == "book"
    assert plan.target.page_title == "书单"
    assert plan.target.data_source_id == "ds-books"
    assert plan.normalized_record["title"] == "可能性的艺术"
    assert plan.normalized_record["author"] == "刘瑜"
    assert plan.normalized_record["state"] == "initialized"
    assert plan.field_mapping == {
        "title": "名称",
        "state": "阅读状态",
        "cover": "封面",
        "author": "作者",
        "isbn": "ISBN",
        "page_count": "页数",
    }
    assert plan.requires_confirmation is False
    assert plan.asset_operations[0].record_key == "cover"
    assert plan.asset_operations[0].target_field == "封面"


def test_golden_completed_book_plan_maps_completed_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_books_target(config)
    capture = CaptureInput(
        raw_input="我读完了《可能性的艺术》 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="读完",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["title"] == "可能性的艺术"
    assert plan.normalized_record["state"] == "completed"
    assert plan.requires_confirmation is False
    assert plan.operations == [
        {"type": "create_or_update_page", "target_data_source": "Books", "data_source_id": "ds-books"}
    ]


def test_golden_missing_target_returns_unresolved_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.target.source == "unresolved"
    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "target_not_resolved"
    assert plan.warnings == ["目标页面未解析，需要先选择或确认存储页面。"]


def test_golden_incomplete_book_schema_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_books_target(config, include_isbn=False, include_cover=False)
    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "book_schema_incomplete"
    assert "book_schema_incomplete:cover,isbn" in plan.warnings
    assert plan.operations == []
    assert plan.asset_operations == []


def test_golden_podcast_episode_plan_maps_podcast_field(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_podcast_target(config)
    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="待听",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.content_type == "podcast_episode"
    assert plan.target.data_source_id == "ds-podcasts"
    assert plan.normalized_record["podcast"] == "忽左忽右"
    assert plan.normalized_record["state"] == "initialized"
    assert plan.field_mapping["podcast"] == "播客"
    assert plan.field_mapping["cover"] == "封面"
    assert plan.requires_confirmation is False
