import json
from pathlib import Path

import pytest

from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config
from capture_to_notion.models import CaptureInput, CaptureOptions, Target, WritePlan
from capture_to_notion.planner import (
    build_asset_operations,
    build_capture_plan,
    build_plan_field_mapping,
    build_plan_summary,
    extract_labeled_value,
)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


BOOK_PARSER_PROFILE = {
    "labels": {
        "author": ["作者", "author"],
        "isbn": ["ISBN", "isbn"],
        "publisher": ["出版社", "publisher"],
        "page_count": ["页数", "pages", "page_count"],
    }
}


PODCAST_PARSER_PROFILE = {
    "labels": {
        "podcast": ["播客", "podcast", "节目"],
    }
}


def seed_book_target(config, parser_profile=True):
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
            "parser_profile": {"book": BOOK_PARSER_PROFILE} if parser_profile else {},
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
                        "page_count": "页数",
                        "state": "阅读状态",
                        "cover": "封面",
                    },
                    "schema": {
                        "名称": {"type": "title"},
                        "作者": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                        "出版社": {"type": "rich_text"},
                        "页数": {"type": "number"},
                        "阅读状态": {"type": "status"},
                        "封面": {"type": "files"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {"initialized": "想读", "completed": "已读"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )


def seed_podcast_target(config, parser_profile=True):
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
            "parser_profile": {"podcast_episode": PODCAST_PARSER_PROFILE} if parser_profile else {},
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
    assert WritePlan.from_dict(data).to_dict() == data


def test_builds_book_capture_plan_from_cached_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
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



def test_build_plan_summary_snapshots_mapped_fields_and_warnings():
    field_mapping = {"title": "名称"}
    warnings = ["needs_review"]

    summary = build_plan_summary(
        content_type="book",
        target_page="书单",
        target_data_source="Books",
        normalized_record={"title": "可能性的艺术", "state": "initialized"},
        field_mapping=field_mapping,
        schema_fields={"cover": "封面", "author": "作者", "isbn": "ISBN", "page_count": "页数"},
        asset_operations=[],
        requires_confirmation=False,
        confirmation_reason=None,
        warnings=warnings,
    )

    field_mapping["author"] = "作者"
    warnings.append("another_warning")

    assert summary["mapped_fields"] == {"title": "名称"}
    assert summary["warnings"] == ["needs_review"]



def test_book_capture_plan_requires_confirmation_when_key_values_are_missing(tmp_path, monkeypatch):
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

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "book_key_values_missing"
    assert "book_key_values_missing:author,isbn,page_count" in plan.warnings
    assert plan.operations == []
    assert plan.asset_operations == []
    assert plan.summary["key_fields"]["author"] == {"target_field": "作者", "value_status": "missing_value"}
    assert plan.summary["key_fields"]["isbn"] == {"target_field": "ISBN", "value_status": "missing_value"}
    assert plan.summary["key_fields"]["page_count"] == {"target_field": "页数", "value_status": "missing_value"}


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


def test_book_capture_plan_uses_parser_profile_labels_from_target_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["parser_profile"] = {
        "labels": {
            "author": ["writer"],
            "isbn": ["code"],
            "page_count": ["length"],
        },
        "title_patterns": [r"Book:\s*(.+?)(?=\s+writer\s*:)"]
    }
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="Book: The Art of Possibility writer: 刘瑜 code: 9787559847357 length: 400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.normalized_record["title"] == "The Art of Possibility"
    assert plan.normalized_record["author"] == "刘瑜"
    assert plan.normalized_record["isbn"] == "9787559847357"
    assert plan.normalized_record["page_count"] == 400
    assert plan.field_mapping["author"] == "作者"
    assert plan.field_mapping["isbn"] == "ISBN"
    assert plan.field_mapping["page_count"] == "页数"


def test_book_capture_plan_uses_target_level_parser_profile_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["parser_profile"] = {
        "book": {
            "labels": {
                "author": ["writer"],
                "isbn": ["code"],
                "page_count": ["length"],
            },
            "title_patterns": [r"Book:\s*(.+?)(?=\s+writer\s*:)"]
        }
    }
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="Book: The Art of Possibility writer: 刘瑜 code: 9787559847357 length: 400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.normalized_record["title"] == "The Art of Possibility"
    assert plan.normalized_record["author"] == "刘瑜"
    assert plan.normalized_record["isbn"] == "9787559847357"
    assert plan.normalized_record["page_count"] == 400


def test_book_capture_plan_does_not_parse_business_labels_without_parser_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config, parser_profile=False)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "book_key_values_missing"
    assert plan.normalized_record["title"] == "可能性的艺术"
    assert plan.normalized_record["author"] is None
    assert plan.normalized_record["isbn"] is None
    assert plan.normalized_record["page_count"] is None


def test_book_capture_plan_summary_shows_reviewable_target_fields_and_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.summary == {
        "target_page": "书单",
        "target_data_source": "Books",
        "title": "可能性的艺术",
        "content_type": "book",
        "state": "initialized",
        "mapped_fields": {
            "title": "名称",
            "state": "阅读状态",
            "cover": "封面",
            "author": "作者",
            "isbn": "ISBN",
            "page_count": "页数",
        },
        "key_fields": {
            "cover": {"target_field": "封面", "value_status": "present"},
            "author": {"target_field": "作者", "value_status": "present"},
            "isbn": {"target_field": "ISBN", "value_status": "present"},
            "page_count": {"target_field": "页数", "value_status": "present"},
        },
        "asset_actions": [
            {"record_key": "cover", "target_field": "封面", "action": "download_and_attach"}
        ],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "warnings": [],
    }



def test_book_capture_plan_requires_confirmation_for_page_count_mapping_ambiguity(tmp_path, monkeypatch):
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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
            "data_sources": {
                "db-books": {
                    "data_source_id": "db-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "名称",
                        "state": "阅读状态",
                        "cover": "封面",
                        "author": "作者",
                        "isbn": "ISBN",
                        "page_count": "Page Count",
                    },
                    "mapping_warnings": ["ambiguous_field_mapping:page_count:Page Count,Pages"],
                    "schema": {
                        "名称": {"type": "title"},
                        "阅读状态": {"type": "status"},
                        "封面": {"type": "files"},
                        "作者": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                        "Page Count": {"type": "number"},
                        "Pages": {"type": "rich_text"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "阅读状态", "values": {}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "field_mapping_ambiguous"
    assert plan.warnings == ["ambiguous_field_mapping:page_count:Page Count,Pages"]
    assert plan.operations == []
    assert plan.asset_operations == []



def test_book_capture_plan_requires_confirmation_for_untrusted_page_count_fallback_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["page_count"] = "Pages"
    target["data_sources"]["books"]["field_sources"] = {
        "title": "profile",
        "author": "profile",
        "isbn": "profile",
        "page_count": "type_fallback",
        "state": "profile",
        "cover": "profile",
    }
    target["data_sources"]["books"]["schema"]["Pages"] = {"type": "number"}
    target["data_sources"]["books"]["schema"].pop("页数")
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:page_count:type_fallback" in plan.warnings
    assert "page_count" not in plan.field_mapping
    assert "page_count" not in plan.summary["mapped_fields"]



def test_book_capture_plan_requires_confirmation_for_untrusted_author_relation_fallback_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["author"] = "Relation"
    target["data_sources"]["books"]["field_sources"] = {
        "title": "profile",
        "author": "relation_fallback",
        "isbn": "profile",
        "page_count": "profile",
        "state": "profile",
        "cover": "profile",
    }
    target["data_sources"]["books"]["schema"]["Relation"] = {"type": "relation", "target_database_id": "db-authors"}
    target["data_sources"]["books"]["schema"].pop("作者")
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:author:relation_fallback" in plan.warnings
    assert "author" not in plan.field_mapping
    assert "author" not in plan.summary["mapped_fields"]


def test_book_capture_plan_requires_confirmation_for_unknown_key_field_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["field_sources"] = {
        "title": "profile",
        "author": "profile",
        "isbn": "profile",
        "page_count": "generated",
        "state": "profile",
        "cover": "profile",
    }
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:page_count:generated" in plan.warnings
    assert "page_count" not in plan.field_mapping
    assert "page_count" not in plan.summary["mapped_fields"]


def test_book_capture_plan_requires_confirmation_for_legacy_semantic_field_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["field_sources"] = {
        "title": "semantic",
        "author": "semantic",
        "isbn": "semantic",
        "page_count": "semantic",
        "state": "semantic",
        "cover": "semantic",
    }
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:cover:semantic" in plan.warnings
    assert "cover" not in plan.field_mapping
    assert "author" not in plan.field_mapping
    assert "isbn" not in plan.field_mapping
    assert "page_count" not in plan.field_mapping



def test_book_capture_plan_does_not_attach_untrusted_cover_fallback_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["cover"] = "Attachment"
    target["data_sources"]["books"]["field_sources"] = {
        "title": "profile",
        "author": "profile",
        "isbn": "profile",
        "page_count": "profile",
        "state": "profile",
        "cover": "type_fallback",
    }
    target["data_sources"]["books"]["schema"]["Attachment"] = {"type": "files"}
    target["data_sources"]["books"]["schema"].pop("封面")
    target["asset_mapping"] = {"cover": {"field": "Attachment", "type": "files", "strategy": "download_and_attach"}}
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:cover:type_fallback" in plan.warnings
    assert "cover" not in plan.field_mapping
    assert plan.asset_operations == []
    assert plan.summary["asset_actions"] == []


def test_book_capture_plan_accepts_profile_cover_mapping_for_custom_files_field(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["cover"] = "Attachment"
    target["data_sources"]["books"]["field_sources"] = {
        "title": "profile",
        "author": "profile",
        "isbn": "profile",
        "page_count": "profile",
        "state": "profile",
        "cover": "profile",
    }
    target["data_sources"]["books"]["schema"]["Attachment"] = {"type": "files"}
    target["data_sources"]["books"]["schema"].pop("封面")
    target["asset_mapping"] = {"cover": {"field": "Attachment", "type": "files", "strategy": "download_and_attach"}}
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.field_mapping["cover"] == "Attachment"
    assert plan.asset_operations[0].target_field == "Attachment"


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
    assert extract_labeled_value(
        "作者：刘瑜，出版社：广西师范大学出版社",
        ["作者", "author"],
        ["作者", "author", "出版社", "publisher"],
    ) == "刘瑜"


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
    assert extract_labeled_value(
        raw_input,
        ["作者", "author"],
        ["作者", "author", "出版社", "publisher"],
    ) == expected


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


def test_extract_labeled_podcast_stops_before_chinese_date_labels():
    known_labels = ["播客", "podcast", "节目", "发布日期", "发布于", "published_at"]
    assert extract_labeled_value("播客：忽左忽右 发布日期：2026-05-10", ["播客", "podcast", "节目"], known_labels) == "忽左忽右"
    assert extract_labeled_value("节目：忽左忽右 发布于：2026-05-10", ["播客", "podcast", "节目"], known_labels) == "忽左忽右"


@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("podcast: Acquired url: https://example.com", "Acquired"),
        ("节目：忽左忽右｜标题：某一期", "忽左忽右"),
        ("播客：忽左忽右 published_at: 2026-05-10", "忽左忽右"),
    ],
)
def test_extract_labeled_podcast_stops_before_following_labels(raw_input, expected):
    assert extract_labeled_value(
        raw_input,
        ["播客", "podcast", "节目"],
        ["播客", "podcast", "节目", "标题", "title", "链接", "url", "published_at"],
    ) == expected


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


def test_podcast_capture_plan_uses_parser_profile_labels_for_podcast_and_title_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_podcast_target(config)
    target = json.loads((config.targets_dir / "podcastshelf.json").read_text())
    target["parser_profile"] = {"podcast_episode": {"labels": {"podcast": ["节目名"]}}}
    write_json(config.targets_dir / "podcastshelf.json", target)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 节目名：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["podcast"] == "忽左忽右"
    assert plan.normalized_record["title"] == "收藏这期播客到播客库"


def test_podcast_capture_plan_does_not_parse_business_labels_without_parser_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_podcast_target(config, parser_profile=False)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["podcast"] is None
    assert plan.normalized_record["title"] == "收藏这期播客到播客库 播客：忽左忽右"


def test_podcast_capture_plan_ignores_page_count_only_mapping_ambiguity(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "播客库": {
                    "type": "page",
                    "page_id": "page-podcasts",
                    "target_id": "podcastshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "播客库", "target_id": "podcastshelf"},
            "data_sources": {
                "db-episodes": {
                    "data_source_id": "db-episodes",
                    "title": "Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {
                        "title": "标题",
                        "podcast": "播客",
                        "state": "状态",
                        "page_count": "Page Count",
                    },
                    "mapping_warnings": ["ambiguous_field_mapping:page_count:Page Count,Pages"],
                    "schema": {
                        "标题": {"type": "title"},
                        "播客": {"type": "relation", "target_database_id": "db-podcasts"},
                        "状态": {"type": "select"},
                        "Page Count": {"type": "number"},
                        "Pages": {"type": "rich_text"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": True,
            "confirmation_reason": "field_mapping_ambiguous",
        },
    )
    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.confirmation_reason is None
    assert plan.warnings == ["ambiguous_field_mapping:page_count:Page Count,Pages"]
    assert plan.operations == [
        {
            "type": "create_or_update_page",
            "target_data_source": "Episodes",
            "data_source_id": "db-episodes",
        }
    ]


def test_podcast_capture_plan_keeps_confirmation_for_other_data_source_mapping_ambiguity(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "播客库": {
                    "type": "page",
                    "page_id": "page-podcasts",
                    "target_id": "podcastshelf",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "播客库", "target_id": "podcastshelf"},
            "data_sources": {
                "db-episodes": {
                    "data_source_id": "db-episodes",
                    "title": "Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {
                        "title": "标题",
                        "podcast": "播客",
                        "state": "状态",
                        "page_count": "Page Count",
                    },
                    "mapping_warnings": ["ambiguous_field_mapping:page_count:Page Count,Pages"],
                    "schema": {
                        "标题": {"type": "title"},
                        "播客": {"type": "relation", "target_database_id": "db-podcasts"},
                        "状态": {"type": "select"},
                        "Page Count": {"type": "number"},
                        "Pages": {"type": "rich_text"},
                    },
                },
                "db-tags": {
                    "data_source_id": "db-tags",
                    "title": "Tags",
                    "role": "secondary",
                    "content_types": [],
                    "fields": {"tag": "标签"},
                    "mapping_warnings": ["ambiguous_field_mapping:tag:分类,标签"],
                    "schema": {
                        "分类": {"type": "select"},
                        "标签": {"type": "select"},
                    },
                },
            },
            "relations": [],
            "state_mapping": {"field": "状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "field_mapping_ambiguous"
    assert plan.warnings == [
        "ambiguous_field_mapping:page_count:Page Count,Pages",
        "ambiguous_field_mapping:tag:分类,标签",
    ]
    assert plan.summary["warnings"] == plan.warnings
    assert plan.operations == []



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
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
                        "page_count": "页数",
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
                        "页数": {"type": "number"},
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
            "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
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


def test_capture_plan_uses_cached_fields_from_scanned_target(tmp_path, monkeypatch):
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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
                        "page_count": "页数",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面图": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "出版社": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                        "页数": {"type": "number"},
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
            "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
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
        "author": "作者",
        "isbn": "ISBN",
        "page_count": "页数",
    }



def test_book_capture_plan_maps_page_count_from_cached_field_mapping(tmp_path, monkeypatch):
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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
                        "isbn": "ISBN",
                        "page_count": "页数",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面图": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "ISBN": {"type": "rich_text"},
                        "页数": {"type": "number"},
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
            "raw_input": "把《县乡中国》存到书单 作者：杨华 ISBN：9787301320939 页数：320",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.normalized_record["page_count"] == 320
    assert plan.field_mapping["page_count"] == "页数"
    assert plan.summary["key_fields"]["page_count"] == {"target_field": "页数", "value_status": "present"}


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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
    assert "book_schema_incomplete:cover,author,isbn,page_count,state" in plan.warnings


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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
                        "page_count": "页数",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "ISBN": {"type": "rich_text"},
                        "页数": {"type": "number"},
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
            "raw_input": "把《县乡中国》存到书单在读列表 作者：杨华 ISBN：9787301320939 页数：320",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.field_mapping["cover"] == "封面"
    assert plan.asset_operations[0].target_field == "封面"



def test_book_capture_plan_requires_confirmation_when_cover_field_source_is_untrusted(tmp_path, monkeypatch):
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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
                        "page_count": "页数",
                    },
                    "field_sources": {
                        "title": "profile",
                        "state": "profile",
                        "cover": "type_fallback",
                        "author": "profile",
                        "isbn": "profile",
                        "page_count": "profile",
                    },
                    "mapping_warnings": [],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "附件": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "ISBN": {"type": "rich_text"},
                        "页数": {"type": "number"},
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
            "raw_input": "把《县乡中国》存到书单在读列表 作者：杨华 ISBN：9787301320939 页数：320",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        }
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert plan.operations == []
    assert plan.asset_operations == []
    assert "untrusted_field_mapping:cover:type_fallback" in plan.warnings



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
            "parser_profile": {"book": BOOK_PARSER_PROFILE},
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
                        "page_count": "页数",
                    },
                    "mapping_warnings": ["ambiguous_field_mapping:tag:标签,分类"],
                    "schema": {
                        "书名": {"type": "title"},
                        "阅读进度": {"type": "status"},
                        "封面图": {"type": "files"},
                        "作者": {"type": "relation", "target_database_id": "db-authors"},
                        "出版社": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                        "页数": {"type": "number"},
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
            "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
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
        "author": "刘瑜",
        "isbn": "9787559847357",
        "publisher": None,
        "page_count": 400,
    }
    assert plan.normalized_record["cover"].startswith("https://example.com/capture-to-notion/covers/")
    assert plan.normalized_record["cover"].endswith(".jpg")
    assert plan.field_mapping == {
        "title": "书名",
        "state": "阅读进度",
        "cover": "封面图",
        "author": "作者",
        "isbn": "ISBN",
        "page_count": "页数",
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
