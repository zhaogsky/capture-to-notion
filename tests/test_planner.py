import json
from pathlib import Path

import pytest

from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
from capture_to_notion.models import AssetOperation, CaptureInput, CaptureOptions, Target, WritePlan
import capture_to_notion.planner as planner_module
from capture_to_notion.planner import (
    build_asset_operations,
    build_capture_plan,
    build_plan_cli_summary,
    build_plan_field_mapping,
    build_plan_summary,
    extract_labeled_value,
    extract_title,
    missing_required_fields,
    missing_required_values,
    parser_profile_for,
)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_planner_exposes_generic_title_helper_only():
    assert hasattr(planner_module, "extract_title")
    assert not hasattr(planner_module, "extract_book_title")


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


def seed_template_v2_target(config):
    write_json(
        config.graphs_v2_dir / "noteshelf.json",
        {
            "cache_version": 2,
            "graph_id": "noteshelf",
            "root": {"kind": "data_source", "id": "ds-notes"},
            "data_sources": {
                "ds-notes": {
                    "data_source_id": "ds-notes",
                    "database_id": "db-notes",
                    "title": "Notes",
                    "schema": {"Name": {"type": "title"}, "Status": {"type": "select"}},
                    "templates": [
                        {
                            "template_id": "template-default",
                            "page_id": "page-template-default",
                            "name": "Default note",
                            "title": "Default note",
                            "data_source_id": "ds-notes",
                            "database_id": "db-notes",
                            "is_default": True,
                        }
                    ],
                }
            },
            "views": {},
        },
    )
    write_json(
        config.profiles_v2_dir / "notes-profile.json",
        {
            "cache_version": 2,
            "profile_id": "notes-profile",
            "graph_id": "noteshelf",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-notes",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "Name", "state": "Status"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "parser_profile": {"trusted_field_sources": ["user_binding"]},
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                }
            },
        },
    )
    write_json(
        config.aliases_v2_file,
        {"cache_version": 2, "aliases": {"notes": {"graph_id": "noteshelf", "profile_id": "notes-profile", "kind": "write_profile"}}},
    )


def test_v2_plan_exposes_template_options_as_facts_only_without_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    seed_template_v2_target(config)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "标题：Template note",
                "target_hint": "notes",
                "content_type_hint": "book",
            }
        ),
        cache,
    )
    cli_summary = build_plan_cli_summary(plan)

    assert plan.summary["template_options"] == {
        "data_source_id": "ds-notes",
        "templates": [
            {
                "template_id": "template-default",
                "page_id": "page-template-default",
                "name": "Default note",
                "title": "Default note",
                "data_source_id": "ds-notes",
                "database_id": "db-notes",
                "is_default": True,
            }
        ],
        "decision": "undecided",
        "selected_template_id": None,
        "facts_only": True,
        "apply_status": "facts_only",
    }
    assert cli_summary["review"]["template_options"] == plan.summary["template_options"]
    assert cli_summary["review"]["verification_expectations"]["targets"][0]["template"] == {
        "decision": "undecided",
        "selected_template_id": None,
        "apply_status": "facts_only",
    }


def test_v2_plan_marks_use_template_as_unsupported_until_writer_supports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    seed_template_v2_target(config)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "标题：Template note",
                "target_hint": "notes",
                "content_type_hint": "book",
                "options": {"template_choice": {"decision": "use_template", "template_id": "template-default"}},
            }
        ),
        cache,
    )

    assert plan.summary["template_options"]["decision"] == "use_template"
    assert plan.summary["template_options"]["selected_template_id"] == "template-default"
    assert plan.summary["template_options"]["apply_status"] == "unsupported"
    assert "template_apply_unsupported:template-default" in plan.warnings
    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "template_apply_unsupported"
    assert plan.summary["write_targets"][0]["template"] == {
        "decision": "use_template",
        "selected_template_id": "template-default",
        "apply_status": "unsupported",
    }


def test_v2_plan_reflects_skip_template_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    seed_template_v2_target(config)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "标题：Template note",
                "target_hint": "notes",
                "content_type_hint": "book",
                "enrichment": {"template_decisions": {"ds-notes": {"decision": "skip_template"}}},
            }
        ),
        cache,
    )

    assert plan.summary["template_options"]["decision"] == "skip_template"
    assert plan.summary["template_options"]["selected_template_id"] is None
    assert plan.summary["template_options"]["apply_status"] == "skipped"
    assert plan.summary["write_targets"][0]["template"] == {
        "decision": "skip_template",
        "selected_template_id": None,
        "apply_status": "skipped",
    }


def test_v2_capture_plan_does_not_write_state_when_input_state_is_null(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    write_json(
        config.graphs_v2_dir / "bookshelf.json",
        {
            "cache_version": 2,
            "graph_id": "bookshelf",
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "名称": {"type": "title"},
                        "状态": {"type": "select", "select": {"options": [{"name": "In progress"}, {"name": "未开始"}]}},
                    },
                }
            },
            "views": {
                "view-reading": {
                    "view_id": "view-reading",
                    "name": "正在阅读",
                    "type": "gallery",
                    "data_source_id": "ds-books",
                }
            },
        },
    )
    write_json(
        config.profiles_v2_dir / "books-profile.json",
        {
            "cache_version": 2,
            "profile_id": "books-profile",
            "graph_id": "bookshelf",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": "view-reading",
                    "field_mapping": {"title": "名称", "state": "状态"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "parser_profile": {"trusted_field_sources": ["user_binding"]},
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                }
            },
        },
    )
    write_json(
        config.aliases_v2_file,
        {"cache_version": 2, "aliases": {"书单": {"graph_id": "bookshelf", "profile_id": "books-profile", "kind": "write_profile"}}},
    )

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "书名：反乌合之众",
                "target_hint": "书单",
                "state": None,
                "content_type_hint": "book",
                "target_context_hint": "Notion view 正在阅读",
                "target_scope_hint": "database_like_area_or_view",
            }
        ),
        cache,
    )

    assert plan.normalized_record.get("state") is None
    assert "state" not in plan.field_mapping
    assert plan.summary["writable_fields"]["state"]["value_status"] == "missing_value"
    assert plan.summary["writable_fields"]["state"]["write_status"] == "omitted_missing_value"


def test_v2_capture_plan_applies_view_status_constraint(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    write_json(
        config.graphs_v2_dir / "bookshelf.json",
        {
            "cache_version": 2,
            "graph_id": "bookshelf",
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "名称": {"id": "title", "type": "title", "name": "名称"},
                        "状态": {"id": "state_id", "type": "status", "name": "状态", "status": {"options": [{"name": "Next"}]}},
                    },
                }
            },
            "views": {
                "view-pending": {
                    "view_id": "view-pending",
                    "name": "待读列表",
                    "type": "gallery",
                    "data_source_id": "ds-books",
                    "quick_filters": {"state_id": {"status": {"equals": "Next"}}},
                }
            },
        },
    )
    write_json(
        config.profiles_v2_dir / "books-profile.json",
        {
            "cache_version": 2,
            "profile_id": "books-profile",
            "graph_id": "bookshelf",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": "view-pending",
                    "field_mapping": {"title": "名称", "state": "状态"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "parser_profile": {"labels": {"title": ["名称"]}, "trusted_field_sources": ["user_binding"]},
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                }
            },
        },
    )
    write_json(
        config.aliases_v2_file,
        {"cache_version": 2, "aliases": {"书单待读列表": {"graph_id": "bookshelf", "profile_id": "books-profile", "kind": "write_profile"}}},
    )

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "名称：反乌合之众",
                "target_hint": "书单待读列表",
                "state": None,
                "content_type_hint": "book",
                "target_context_hint": "Notion view 待读列表",
                "target_scope_hint": "database_like_area_or_view",
            }
        ),
        cache,
    )

    assert plan.normalized_record["state"] == "Next"
    assert plan.field_mapping["state"] == "状态"
    assert plan.summary["writable_fields"]["state"] == {
        "target_field": "状态",
        "value_status": "present",
        "write_status": "planned",
    }
    expected_constraints = {
        "values": {"状态": "Next"},
        "warnings": [],
        "unsupported": [],
        "conflicts": [],
    }
    assert plan.summary["view_context"]["constraints"] == expected_constraints
    review = build_plan_cli_summary(plan)["review"]
    assert review["view_constraints"] == [
        {"status": "enforced", "field": "状态", "value": "Next"}
    ]
    assert review["verification_expectations"]["targets"][0]["view_visibility"] == {
        "view_id": "view-pending",
        "view_name": "待读列表",
        "view_type": "gallery",
        "constraints": expected_constraints,
    }



def test_v2_capture_plan_blocks_conflicting_view_status_constraint(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    write_json(
        config.graphs_v2_dir / "bookshelf.json",
        {
            "cache_version": 2,
            "graph_id": "bookshelf",
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "名称": {"id": "title", "type": "title", "name": "名称"},
                        "状态": {"id": "state_id", "type": "status", "name": "状态", "status": {"options": [{"name": "Next"}, {"name": "Reading"}]}},
                    },
                }
            },
            "views": {
                "view-pending": {
                    "view_id": "view-pending",
                    "name": "待读列表",
                    "type": "gallery",
                    "data_source_id": "ds-books",
                    "quick_filters": {"state_id": {"status": {"equals": "Next"}}},
                }
            },
        },
    )
    write_json(
        config.profiles_v2_dir / "books-profile.json",
        {
            "cache_version": 2,
            "profile_id": "books-profile",
            "graph_id": "bookshelf",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": "view-pending",
                    "field_mapping": {"title": "名称", "state": "状态"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "parser_profile": {"labels": {"title": ["名称"]}, "trusted_field_sources": ["user_binding"]},
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                }
            },
        },
    )
    write_json(
        config.aliases_v2_file,
        {"cache_version": 2, "aliases": {"书单待读列表": {"graph_id": "bookshelf", "profile_id": "books-profile", "kind": "write_profile"}}},
    )

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "名称：反乌合之众",
                "target_hint": "书单待读列表",
                "state": "Reading",
                "content_type_hint": "book",
                "target_context_hint": "Notion view 待读列表",
                "target_scope_hint": "database_like_area_or_view",
            }
        ),
        cache,
    )

    assert plan.requires_confirmation is True
    assert plan.operations == []
    assert "view_constraint_conflict:状态:Next:Reading" in plan.warnings



def test_v2_capture_plan_creates_cover_asset_operation_from_trusted_files_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheV2Store(config)
    write_json(
        config.graphs_v2_dir / "bookshelf.json",
        {
            "cache_version": 2,
            "graph_id": "bookshelf",
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "名称": {"type": "title"},
                        "封面": {"type": "files"},
                    },
                }
            },
            "views": {},
        },
    )
    write_json(
        config.profiles_v2_dir / "books-profile.json",
        {
            "cache_version": 2,
            "profile_id": "books-profile",
            "graph_id": "bookshelf",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "field_mapping": {"title": "名称", "cover": "封面"},
                    "field_sources": {"title": "user_binding", "cover": "user_binding"},
                    "parser_profile": {
                        "required_schema_fields": [],
                        "required_value_fields": [],
                        "summary_key_fields": [],
                        "trusted_field_sources": ["user_binding"],
                    },
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                }
            },
        },
    )
    write_json(
        config.aliases_v2_file,
        {"cache_version": 2, "aliases": {"书单": {"graph_id": "bookshelf", "profile_id": "books-profile", "kind": "write_profile"}}},
    )

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "名称：反乌合之众\n封面：https://example.com/cover.jpg",
                "target_hint": "书单",
                "state": None,
                "content_type_hint": "book",
            }
        ),
        cache,
    )

    assert plan.field_mapping["cover"] == "封面"
    assert plan.asset_operations[0].record_key == "cover"
    assert plan.asset_operations[0].target_field == "封面"
    assert plan.asset_operations[0].action == "download_and_attach"
    assert plan.summary["asset_actions"] == [
        {"record_key": "cover", "target_field": "封面", "action": "download_and_attach"}
    ]



def test_capture_plan_updates_database_item_resolved_from_page_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
        {"aliases": {"可能性的艺术": {"type": "page", "page_id": "page-book-1", "target_id": "book-item"}}},
    )
    write_json(
        config.targets_dir / "book-item.json",
        {
            "target": {
                "page_id": "page-book-1",
                "title": "可能性的艺术",
                "target_id": "book-item",
                "data_source_id": "ds-books",
            },
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "名称", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {"名称": {"type": "title"}, "阅读状态": {"type": "status"}},
                }
            },
        },
    )

    plan = build_capture_plan(
        CaptureInput(raw_input="《可能性的艺术》", target_hint="可能性的艺术", content_type_hint="book"),
        cache,
    )

    assert plan.requires_confirmation is False
    assert plan.operations[0]["page_id"] == "page-book-1"
    assert plan.target.page_id == "page-book-1"
    assert plan.target.data_source_id == "ds-books"
    assert plan.target.source == "target_hint_alias"
    assert plan.summary["write_targets"][0]["action"] == "update_page"



def test_capture_plan_data_source_alias_creates_without_implicit_update(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
        {"aliases": {"书库": {"type": "data_source", "data_source_id": "ds-books", "target_id": "books-ds"}}},
    )
    write_json(
        config.targets_dir / "books-ds.json",
        {
            "target": {"page_id": "page-books", "title": "Books", "target_id": "books-ds", "data_source_id": "ds-books"},
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "名称", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {"名称": {"type": "title"}, "阅读状态": {"type": "status"}},
                }
            },
        },
    )

    plan = build_capture_plan(CaptureInput(raw_input="《新书》", target_hint="书库", content_type_hint="book"), cache)

    assert plan.requires_confirmation is False
    assert "page_id" not in plan.operations[0]
    assert plan.target.page_id == "page-books"
    assert plan.target.target_id == "books-ds"
    assert plan.target.source == "data_source_alias"
    assert plan.summary["write_targets"][0]["action"] == "create_page"
    assert plan.summary["write_targets"][0]["page_id"] is None
    assert plan.summary["write_targets"][0]["page_id_status"] == "pending_after_apply"



def test_capture_plan_write_targets_include_location_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
        {"aliases": {"书库": {"type": "data_source", "data_source_id": "ds-books", "target_id": "books-ds"}}},
    )
    write_json(
        config.targets_dir / "books-ds.json",
        {
            "target": {"page_id": "page-books", "title": "Books", "target_id": "books-ds", "data_source_id": "ds-books"},
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "database_id": "db-books",
                    "parent_page_id": "page-books",
                    "fields": {"title": "名称", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {"名称": {"type": "title"}, "阅读状态": {"type": "status"}},
                }
            },
        },
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="《新书》",
            target_hint="书库",
            target_context_hint="parent page id page-books",
            target_scope_hint="data_source",
            content_type_hint="book",
        ),
        cache,
    )

    write_target = plan.summary["write_targets"][0]
    assert write_target["database_id"] == "db-books"
    assert write_target["parent_page_id"] == "page-books"
    assert write_target["context_verification_source"] == "parent_page_id_match"



def test_capture_plan_explicit_existing_page_id_overrides_database_item_page(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
        {"aliases": {"可能性的艺术": {"type": "page", "page_id": "page-book-1", "target_id": "book-item"}}},
    )
    write_json(
        config.targets_dir / "book-item.json",
        {
            "target": {
                "page_id": "page-book-1",
                "title": "可能性的艺术",
                "target_id": "book-item",
                "data_source_id": "ds-books",
            },
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "名称", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {"名称": {"type": "title"}, "阅读状态": {"type": "status"}},
                }
            },
        },
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="《可能性的艺术》",
            target_hint="可能性的艺术",
            content_type_hint="book",
            existing_page_id="page-explicit",
        ),
        cache,
    )

    assert plan.operations[0]["page_id"] == "page-explicit"
    assert plan.summary["write_targets"][0]["page_id"] == "page-explicit"



def test_capture_plan_infers_and_caches_unmapped_date_field_from_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
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
        config.targets_dir / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "独树不成林", "target_id": "podcastshelf"},
            "parser_profile": {
                "podcast_episode": {
                    "labels": {"description": ["简介"]},
                    "trusted_field_sources": ["explicit", "profile"],
                }
            },
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
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
                        "完成时间": {"type": "date"},
                    },
                }
            },
            "state_mapping": {"field": "状态", "values": {"completed": "已完成"}},
        },
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="《这就是卢梭》 时间改成 2026-05-18 简介：卢梭专题导论",
            target_hint="独树不成林",
            state="已完成",
            content_type_hint="podcast_episode",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.normalized_record["完成时间"] == "2026-05-18"
    assert plan.field_mapping["完成时间"] == "完成时间"
    assert plan.summary["writable_fields"]["完成时间"] == {
        "target_field": "完成时间",
        "value_status": "present",
        "write_status": "planned",
    }
    cached_target = json.loads((config.targets_dir / "podcastshelf.json").read_text(encoding="utf-8"))
    cached_data_source = cached_target["data_sources"]["episodes"]
    assert cached_data_source["fields"]["完成时间"] == "完成时间"
    assert cached_data_source["field_sources"]["完成时间"] == "profile"



def test_capture_plan_maps_date_and_status_schema_input_fields_in_one_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
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
        config.targets_dir / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "独树不成林", "target_id": "podcastshelf"},
            "parser_profile": {"podcast_episode": {"trusted_field_sources": ["explicit", "profile"]}},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {"title": "主题", "state": "状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "主题": {"type": "title"},
                        "状态": {"type": "select"},
                        "完成时间": {"type": "date"},
                        "收听进度": {
                            "type": "status",
                            "options": [{"name": "已完成", "color": "green"}],
                        },
                    },
                }
            },
            "state_mapping": {"field": "状态", "values": {"completed": "已完成"}},
        },
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="某一期节目 完成时间改成 2026-05-25 收听进度设为 已完成",
            target_hint="独树不成林",
            state="completed",
            content_type_hint="podcast_episode",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.normalized_record["完成时间"] == "2026-05-25"
    assert plan.normalized_record["收听进度"] == "已完成"
    assert plan.field_mapping["完成时间"] == "完成时间"
    assert plan.field_mapping["收听进度"] == "收听进度"



def test_capture_plan_uses_cached_schema_field_labels_for_assignment_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
        {
            "aliases": {
                "某一期节目": {
                    "type": "page",
                    "page_id": "page-episode",
                    "target_id": "episode-item",
                }
            }
        },
    )
    write_json(
        config.targets_dir / "episode-item.json",
        {
            "target": {
                "page_id": "page-episode",
                "title": "某一期节目",
                "target_id": "episode-item",
                "data_source_id": "ds-episodes",
            },
            "parser_profile": {"podcast_episode": {"trusted_field_sources": ["profile"]}},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {"状态": "状态", "完成时间": "完成时间"},
                    "field_sources": {"状态": "profile", "完成时间": "profile"},
                    "schema": {
                        "主题": {"type": "title"},
                        "状态": {"type": "select"},
                        "完成时间": {"type": "date"},
                    },
                }
            },
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="这期播客已经听完了，然后状态改成已完成，完成时间改成 2026-05-25。页面信息：节目名=罗永浩的十字路口",
            target_hint="某一期节目",
            state="completed",
            content_type_hint="podcast_episode",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.requires_confirmation is False
    assert plan.normalized_record["状态"] == "已完成"
    assert plan.normalized_record["完成时间"] == "2026-05-25"
    assert plan.field_mapping == {"状态": "状态", "完成时间": "完成时间"}
    assert plan.operations[0]["page_id"] == "page-episode"



def test_capture_plan_preserves_state_value_matching_target_schema_option(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.aliases_file,
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
        config.targets_dir / "podcastshelf.json",
        {
            "target": {"page_id": "page-podcasts", "title": "独树不成林", "target_id": "podcastshelf"},
            "parser_profile": {"podcast_episode": {"trusted_field_sources": ["explicit", "profile"]}},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {"title": "主题", "state": "状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "主题": {"type": "title"},
                        "状态": {
                            "type": "select",
                            "options": [
                                {"name": "已完成", "color": "purple"},
                                {"name": "未开始", "color": "brown"},
                                {"name": "进行中", "color": "green"},
                            ],
                        },
                    },
                }
            },
            "state_mapping": {"field": "状态", "values": {}},
        },
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="《340-思考是一种政治能力吗？》",
            target_hint="独树不成林",
            state="进行中",
            content_type_hint="podcast_episode",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.normalized_record["state"] == "进行中"
    assert plan.summary["state"] == "进行中"



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
    assert plan.operations[0]["operation_id"] == "create_child_page:0"
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
            "target_path": "知识",
            "target_path_complete": False,
        }
    ]
    assert plan.summary["body_block_count"] == 2



def test_v2_page_parent_plan_accepts_ai_generated_body_blocks(tmp_path, monkeypatch):
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
    body_blocks = [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": "AI 生成的重点"}}],
                "icon": {"emoji": "💡"},
            },
        }
    ]

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "标题：DeepSeek V4\n\nFallback body should not be used.",
                "target_hint": "AI/知识",
                "content_type_hint": "article",
                "intent_hint": "direct_write",
                "input_shape_hint": "plain_text",
                "target_scope_hint": "page_parent",
                "user_requested_action": "write",
                "body_blocks": body_blocks,
            }
        ),
        store,
    )

    assert plan.operations[0]["body_blocks"] == body_blocks
    assert plan.summary["body_block_count"] == 1


def test_write_plan_serializes_page_parent_target_kind():
    plan = WritePlan(
        plan_id="plan-page-1",
        content_type="book",
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



def test_build_plan_cli_summary_omits_execution_payload():
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
        warnings=["review_this"],
        requires_confirmation=False,
        confirmation_reason=None,
        completion_operations=[{"type": "complete_relation_page"}],
        capture_input={"raw_input": "把《可能性的艺术》初始化到书单"},
    )

    assert hasattr(planner_module, "build_plan_cli_summary")

    summary = planner_module.build_plan_cli_summary(plan)

    assert summary == {
        "plan_id": "20260512-demo",
        "content_type": "book",
        "target": {
            "page_title": "书单",
            "page_id": "page-books",
            "data_source_id": "ds-books",
            "confidence": "high",
            "source": "alias_cache",
        },
        "summary": {
            "target_page": "书单",
            "target_data_source": "Books",
            "title": "可能性的艺术",
            "state": "initialized",
            "requires_confirmation": False,
        },
        "review": {
            "target_semantics": {
                "target_kind": "data_source",
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
            },
            "view_constraints": [],
            "expected_fields": [],
            "relation_actions": [],
            "relation_target_plans": [],
            "asset_actions": [],
            "blocking_warnings": [
                {
                    "code": "review_this",
                    "severity": "blocking",
                    "category": "confirmation",
                    "message": "review_this",
                }
            ],
            "verification_expectations": {"fields": [], "relations": [], "assets": [], "page_cover": None, "targets": []},
        },
        "warnings": ["review_this"],
        "warning_sections": {
            "blocking": [
                {
                    "code": "review_this",
                    "severity": "blocking",
                    "category": "confirmation",
                    "message": "review_this",
                }
            ],
            "unwritten_fields": [],
            "review": [],
            "notices": [],
        },
        "requires_confirmation": False,
        "confirmation_reason": None,
    }
    assert "normalized_record" not in summary
    assert "field_mapping" not in summary
    assert "operations" not in summary
    assert "asset_operations" not in summary
    assert "completion_operations" not in summary
    assert "capture_input" not in summary



def test_build_plan_cli_summary_exposes_compact_review_sections():
    plan = WritePlan(
        plan_id="20260512-review",
        content_type="book",
        target=Target(
            page_title="书单",
            page_id="page-books",
            data_source_id="ds-books",
            view_id="view-next",
            view_name="待读列表",
            view_type="list",
            target_kind="data_source",
            confidence="high",
            source="v2_alias",
        ),
        summary={
            "target_page": "书单",
            "target_data_source": "Books",
            "title": "反乌合之众",
            "state": "Next",
            "key_fields": {"isbn": {"target_field": "ISBN", "value_status": "present"}},
            "writable_fields": {
                "title": {"target_field": "名称", "value_status": "present", "write_status": "planned"},
                "state": {"target_field": "状态", "value_status": "present", "write_status": "planned"},
                "isbn": {"target_field": "ISBN", "value_status": "present", "write_status": "planned"},
            },
            "write_targets": [{"type": "primary_page", "action": "update_page", "page_id": "page-book"}],
            "relation_actions": [{"field": "author", "target_field": "作者", "action": "create_missing"}],
            "asset_actions": [
                {"field": "cover", "target_field": "封面", "type": "cover_image", "action": "download_and_attach", "status": "planned"}
            ],
            "required_value_fields": ["isbn", "page_count"],
            "warning_details": [
                {"code": "relation_ambiguous:author", "severity": "blocking", "category": "relation", "message": "relation_ambiguous:author"}
            ],
            "requires_confirmation": True,
        },
        normalized_record={"title": "反乌合之众", "state": "Next", "isbn": "978-1"},
        field_mapping={"title": "名称", "state": "状态", "isbn": "ISBN", "author": "作者", "cover": "封面"},
        operations=[{"type": "create_or_update_page", "page_id": "page-book"}],
        asset_operations=[
            AssetOperation(
                type="cover_image",
                source_url="https://example.com/cover.jpg",
                local_cache_path=None,
                target_field="封面",
                action="download_and_attach",
                record_key="cover",
            )
        ],
        sources=[],
        warnings=["view_constraint_conflict:状态:Next:Reading", "relation_ambiguous:author"],
        requires_confirmation=True,
        confirmation_reason="view_constraint_unresolved",
    )

    review = build_plan_cli_summary(plan)["review"]

    assert review["target_semantics"] == {
        "target_kind": "data_source",
        "page_title": "书单",
        "page_id": "page-books",
        "data_source_id": "ds-books",
        "view_id": "view-next",
        "view_name": "待读列表",
        "view_type": "list",
    }
    assert review["view_constraints"] == [{"status": "conflict", "field": "状态", "expected": "Next", "actual": "Reading"}]
    assert review["expected_fields"] == [
        {"record_key": "title", "target_field": "名称", "value_status": "present", "write_status": "planned"},
        {"record_key": "state", "target_field": "状态", "value_status": "present", "write_status": "planned"},
        {"record_key": "isbn", "target_field": "ISBN", "value_status": "present", "write_status": "planned"},
    ]
    assert review["relation_actions"] == [{"field": "author", "target_field": "作者", "action": "create_missing"}]
    assert review["asset_actions"] == [
        {"field": "cover", "target_field": "封面", "type": "cover_image", "action": "download_and_attach", "status": "planned"}
    ]
    assert review["blocking_warnings"] == [
        {"code": "relation_ambiguous:author", "severity": "blocking", "category": "relation", "message": "relation_ambiguous:author"}
    ]
    assert review["verification_expectations"] == {
        "fields": ["isbn", "page_count"],
        "relations": ["author"],
        "assets": ["cover"],
        "page_cover": "https://example.com/cover.jpg",
        "targets": [
            {
                "target_type": "primary_page",
                "action": "update_page",
                "page_id": "page-book",
                "page_id_status": "known",
                "data_source_id": "ds-books",
                "fields": ["isbn", "page_count"],
                "relations": ["author"],
                "assets": ["cover"],
                "page_cover": "https://example.com/cover.jpg",
            }
        ],
    }



def test_write_plan_from_dict_defaults_completion_operations_for_old_plans():
    data = {
        "plan_id": "20260512-demo",
        "content_type": "book",
        "target": {
            "page_title": "书单",
            "page_id": "page-books",
            "data_source_id": "ds-books",
            "confidence": "high",
            "source": "alias_cache",
        },
        "normalized_record": {"title": "可能性的艺术"},
        "field_mapping": {"title": "名称"},
        "operations": [{"type": "create_or_update_page"}],
        "asset_operations": [],
        "sources": [],
        "warnings": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
    }

    plan = WritePlan.from_dict(data)

    assert plan.completion_operations == []
    assert "completion_operations" not in plan.to_dict()


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
    assert plan.normalized_record["state"] == "想读"
    assert plan.normalized_record.get("cover") is None
    assert "cover" not in plan.field_mapping
    assert plan.asset_operations == []
    assert plan.requires_confirmation is False



def test_book_capture_plan_uses_states_file_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)
    write_json(
        config.states_file,
        {
            "states": {
                "queued": {"aliases": ["Queue Me", "待办"]},
                "done": {"aliases": ["Finished"]},
            }
        },
    )

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="Queue Me",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["state"] == "queued"



def test_book_capture_plan_summary_lists_primary_write_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.summary["write_targets"] == [
        {
            "type": "primary_page",
            "action": "create_page",
            "title": "可能性的艺术",
            "target_page": "书单",
            "target_data_source": "Books",
            "data_source_id": "ds-books",
            "page_id": None,
            "page_id_status": "pending_after_apply",
        }
    ]



def test_capture_plan_uses_existing_page_id_for_primary_update(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)
    existing_page_id = "page-existing-book"
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            "target_hint": "书单",
            "state": "初始化",
            "content_type_hint": "book",
            "existing_page_id": existing_page_id,
        }
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.operations == [
        {
            "type": "create_or_update_page",
            "target_data_source": "Books",
            "data_source_id": "ds-books",
            "page_id": existing_page_id,
        }
    ]
    assert plan.summary["write_targets"] == [
        {
            "type": "primary_page",
            "action": "update_page",
            "title": "可能性的艺术",
            "target_page": "书单",
            "target_data_source": "Books",
            "data_source_id": "ds-books",
            "page_id": existing_page_id,
            "page_id_status": "known",
        }
    ]
    assert plan.capture_input["existing_page_id"] == existing_page_id



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



def test_book_capture_plan_supports_profile_labeled_extra_fields(tmp_path, monkeypatch):
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
            "parser_profile": {
                "book": {
                    "labels": {
                        "author": ["作者", "author"],
                        "isbn": ["ISBN", "isbn"],
                        "publisher": ["出版社", "publisher"],
                        "page_count": ["页数", "pages", "page_count"],
                        "url": ["url", "链接"],
                        "current_page": ["current_page", "当前页"],
                        "language": ["language", "语言"],
                        "country": ["country", "国家"],
                        "format": ["format", "装帧"],
                        "edition": ["edition", "版本"],
                        "start_date": ["start_date", "开始日期"],
                    }
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {
                        "title": "名称",
                        "state": "阅读状态",
                        "cover": "封面",
                        "author": "作者",
                        "isbn": "ISBN",
                        "publisher": "出版社",
                        "page_count": "页数",
                        "url": "豆瓣链接",
                        "current_page": "当前页",
                        "language": "语言",
                        "country": "国家",
                        "format": "装帧",
                        "edition": "版本",
                        "start_date": "开始日期",
                    },
                    "field_sources": {
                        "title": "profile",
                        "state": "profile",
                        "cover": "profile",
                        "author": "profile",
                        "isbn": "profile",
                        "publisher": "profile",
                        "page_count": "profile",
                        "url": "profile",
                        "current_page": "profile",
                        "language": "profile",
                        "country": "profile",
                        "format": "profile",
                        "edition": "profile",
                        "start_date": "profile",
                    },
                    "schema": {
                        "名称": {"type": "title"},
                        "阅读状态": {"type": "status"},
                        "封面": {"type": "files"},
                        "作者": {"type": "rich_text"},
                        "ISBN": {"type": "rich_text"},
                        "出版社": {"type": "rich_text"},
                        "页数": {"type": "number"},
                        "豆瓣链接": {"type": "url"},
                        "当前页": {"type": "number"},
                        "语言": {"type": "select"},
                        "国家": {"type": "rich_text"},
                        "装帧": {"type": "select"},
                        "版本": {"type": "rich_text"},
                        "开始日期": {"type": "date"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {"initialized": "想读", "completed": "已读"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )

    capture = CaptureInput(
        raw_input=(
            "把《失落的大陆》初始化到书单 作者：比尔·布莱森 ISBN：9787532760138 出版社：上海译文出版社 "
            "页数：337 url: https://book.douban.com/subject/20375524/ current_page: 0 language: 简体中文 "
            "country: 美国 format: 平装本 edition: 2013年版 start_date: 2026-05-14"
        ),
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.normalized_record["author"] == "比尔·布莱森"
    assert plan.normalized_record["isbn"] == "9787532760138"
    assert plan.normalized_record["publisher"] == "上海译文出版社"
    assert plan.normalized_record["page_count"] == 337
    assert isinstance(plan.normalized_record["page_count"], int)
    assert plan.normalized_record["url"] == "https://book.douban.com/subject/20375524/"
    assert plan.normalized_record["current_page"] == 0
    assert isinstance(plan.normalized_record["current_page"], int)
    assert plan.normalized_record["language"] == "简体中文"
    assert plan.normalized_record["country"] == "美国"
    assert plan.normalized_record["format"] == "平装本"
    assert plan.normalized_record["edition"] == "2013年版"
    assert plan.normalized_record["start_date"] == "2026-05-14"
    assert plan.field_mapping == {
        "title": "名称",
        "state": "阅读状态",
        "author": "作者",
        "isbn": "ISBN",
        "publisher": "出版社",
        "page_count": "页数",
        "url": "豆瓣链接",
        "current_page": "当前页",
        "language": "语言",
        "country": "国家",
        "format": "装帧",
        "edition": "版本",
        "start_date": "开始日期",
    }



def test_book_capture_plan_warns_when_relation_value_needs_apply_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
    }
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput(
            raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            target_hint="书单",
            state="初始化",
            content_type_hint="book",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.requires_confirmation is False
    assert plan.field_mapping["author"] == "作者"
    assert "relation_resolution_pending:author:刘瑜" in plan.warnings
    assert {
        "code": "relation_resolution_pending:author:刘瑜",
        "severity": "review",
        "category": "relation_resolution",
        "message": "刘瑜",
    } in plan.summary["warning_details"]



def test_book_capture_plan_summarizes_relation_create_missing_action(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
    }
    target["relation_mapping"] = {"author": {"create_missing": True}}
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput(
            raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            target_hint="书单",
            state="初始化",
            content_type_hint="book",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.summary["relation_actions"] == [
        {
            "record_key": "author",
            "target_field": "作者",
            "value": "刘瑜",
            "action": "create_missing_relation_page",
            "target_database_id": "db-authors",
            "target_data_source_id": None,
            "page_id": None,
            "page_id_status": "pending_after_apply",
        }
    ]



def test_book_capture_plan_exposes_relation_target_plan_for_missing_page(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
        "target_data_source_id": "ds-authors",
    }
    target["data_sources"]["authors"] = {
        "data_source_id": "ds-authors",
        "title": "Authors",
        "role": "secondary",
        "content_types": ["author"],
        "schema": {
            "Name": {"type": "title"},
            "Author Picture": {"type": "files"},
            "Bio": {"type": "rich_text"},
            "Country": {"type": "select", "select": {"options": [{"name": "Chile"}]}},
            "Book Count": {"type": "rollup"},
        },
    }
    target["relation_mapping"] = {"author": {"create_missing": True}}
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput(
            raw_input="把《当我们不再理解世界》初始化到书单 作者：[智利] 本哈明·拉巴图特 ISBN：9787020173228 页数：176",
            target_hint="书单",
            state="初始化",
            content_type_hint="book",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.summary["relation_target_plans"] == [
        {
            "source_record_key": "author",
            "source_value": "[智利] 本哈明·拉巴图特",
            "action": "create_page",
            "target_data_source": "Authors",
            "target_data_source_id": "ds-authors",
            "page_id": None,
            "page_id_status": "pending_after_apply",
            "writable_fields": {
                "title": {"target_field": "Name", "value_status": "present", "write_status": "planned", "type": "title"},
                "Author Picture": {"target_field": "Author Picture", "value_status": "missing_value", "write_status": "needs_enrichment", "type": "files"},
                "Bio": {"target_field": "Bio", "value_status": "missing_value", "write_status": "needs_enrichment", "type": "rich_text"},
                "Country": {"target_field": "Country", "value_status": "missing_value", "write_status": "needs_user_choice", "type": "select"},
            },
            "omitted_fields": {"Book Count": {"type": "rollup", "write_status": "computed"}},
            "shell_page_risk": True,
        }
    ]
    assert "relation_target_shell_page:author:[智利] 本哈明·拉巴图特" in plan.warnings



class FakePlannerPeopleAdapter:
    def __init__(self, users):
        self.users = users
        self.search_calls = []

    @classmethod
    def from_config(cls, _config):
        return cls(cls.users)

    def search_users(self, query):
        self.search_calls.append(query)
        lowered = query.casefold()
        return [
            user
            for user in self.users
            if lowered in str(user.get("name", "")).casefold()
            or lowered in str(user.get("person", {}).get("email", "")).casefold()
        ]


def test_capture_plan_auto_resolves_single_people_email_to_user_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["isbn", "page_count"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)
    FakePlannerPeopleAdapter.users = [
        {"id": "user-ada", "name": "Ada Lovelace", "type": "person", "person": {"email": "ada@example.com"}},
    ]
    monkeypatch.setattr(planner_module, "NotionAdapter", FakePlannerPeopleAdapter, raising=False)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "ada@example.com"},
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.normalized_record["reviewers"] == "user-ada"
    assert plan.field_mapping["reviewers"] == "Reviewers"
    assert "people_resolution_requirements" not in compact["summary"]
    assert not any(warning.startswith("people_") for warning in plan.warnings)


def test_capture_plan_auto_blocks_required_ambiguous_people_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["reviewers"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)
    FakePlannerPeopleAdapter.users = [
        {"id": "user-1", "name": "Alex Chen", "type": "person", "person": {"email": "alex.chen@example.com"}},
        {"id": "user-2", "name": "Alex Kim", "type": "person", "person": {"email": "alex.kim@example.com"}},
    ]
    monkeypatch.setattr(planner_module, "NotionAdapter", FakePlannerPeopleAdapter, raising=False)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "Alex"},
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "people_resolution_required"
    assert plan.operations == []
    assert "people_resolution_required:reviewers:Alex" in plan.warnings
    assert compact["summary"]["people_resolution_requirements"] == [
        {
            "target_type": "people_resolution",
            "source_record_key": "reviewers",
            "source_value": "Alex",
            "target_field": "Reviewers",
            "required": True,
            "blocking": True,
            "candidates": [
                {"user_id": "user-1", "id": "user-1", "name": "Alex Chen", "email": "alex.chen@example.com", "type": "person"},
                {"user_id": "user-2", "id": "user-2", "name": "Alex Kim", "email": "alex.kim@example.com", "type": "person"},
            ],
        }
    ]
    assert compact["review"]["people_resolution_requirements"] == compact["summary"]["people_resolution_requirements"]


def test_capture_plan_blocks_required_unresolved_people_value(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["reviewers"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)
    FakePlannerPeopleAdapter.users = []
    monkeypatch.setattr(planner_module, "NotionAdapter", FakePlannerPeopleAdapter, raising=False)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "Alex"},
            }
        ),
        cache,
    )

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "people_resolution_required"
    assert plan.operations == []
    assert "people_unresolved:reviewers:Alex" in plan.warnings



def test_capture_plan_blocks_required_ambiguous_people_candidates_from_enrichment(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["reviewers"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "Alex"},
                "enrichment": {
                    "people_resolution_requirements": [
                        {
                            "record_key": "reviewers",
                            "source_value": "Alex",
                            "target_field": "Reviewers",
                            "candidates": [
                                {"user_id": "user-1", "id": "user-1", "name": "Alex Chen", "email": "alex@example.com"},
                                {"user_id": "user-2", "id": "user-2", "name": "Alex Kim"},
                            ],
                        }
                    ]
                },
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "people_resolution_required"
    assert plan.operations == []
    assert "people_resolution_required:reviewers:Alex" in plan.warnings
    assert compact["summary"]["people_resolution_requirements"] == [
        {
            "target_type": "people_resolution",
            "source_record_key": "reviewers",
            "source_value": "Alex",
            "target_field": "Reviewers",
            "required": True,
            "blocking": True,
            "candidates": [
                {"user_id": "user-1", "id": "user-1", "name": "Alex Chen", "email": "alex@example.com"},
                {"user_id": "user-2", "id": "user-2", "name": "Alex Kim"},
            ],
        }
    ]
    assert compact["review"]["people_resolution_requirements"] == compact["summary"]["people_resolution_requirements"]
    assert any(warning["code"] == "people_resolution_required:reviewers:Alex" for warning in compact["review"]["blocking_warnings"])



def test_capture_plan_people_decision_choose_existing_sets_user_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["reviewers"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "Alex"},
                "enrichment": {
                    "people_resolution_requirements": [
                        {
                            "record_key": "reviewers",
                            "source_value": "Alex",
                            "target_field": "Reviewers",
                            "candidates": [{"user_id": "user-2", "id": "user-2", "name": "Alex Kim"}],
                        }
                    ],
                    "requirement_decisions": [
                        {
                            "target_type": "people_resolution",
                            "source_record_key": "reviewers",
                            "source_value": "Alex",
                            "target_field": "Reviewers",
                            "action": "use_existing",
                            "user_id": "user-2",
                        }
                    ],
                },
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.requires_confirmation is False
    assert plan.normalized_record["reviewers"] == "user-2"
    assert plan.field_mapping["reviewers"] == "Reviewers"
    assert not any(warning.startswith("people_resolution_required:reviewers:") for warning in plan.warnings)
    assert "people_resolution_requirements" not in compact["summary"]
    assert "people_resolution_requirements" not in compact["review"]



def test_capture_plan_people_decision_skip_clears_optional_people_field(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["isbn", "page_count"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "Alex"},
                "enrichment": {
                    "people_resolution_requirements": [
                        {
                            "record_key": "reviewers",
                            "source_value": "Alex",
                            "target_field": "Reviewers",
                            "candidates": [{"user_id": "user-1", "id": "user-1", "name": "Alex Chen"}],
                        }
                    ],
                    "requirement_decisions": [
                        {
                            "target_type": "people_resolution",
                            "source_record_key": "reviewers",
                            "source_value": "Alex",
                            "target_field": "Reviewers",
                            "action": "skip",
                        }
                    ],
                },
            }
        ),
        cache,
    )

    assert plan.requires_confirmation is False
    assert plan.normalized_record["reviewers"] is None
    assert "reviewers" not in plan.field_mapping
    assert "people_skipped:reviewers:Alex" in plan.warnings



def test_capture_plan_optional_ambiguous_people_requirement_can_be_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["fields"]["reviewers"] = "Reviewers"
    target["data_sources"]["books"]["schema"]["Reviewers"] = {"type": "people"}
    target["parser_profile"]["book"]["required_value_fields"] = ["isbn", "page_count"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《People Sprint》初始化到书单 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "structured_record": {"reviewers": "Alex"},
                "enrichment": {
                    "people_resolution_requirements": [
                        {
                            "record_key": "reviewers",
                            "source_value": "Alex",
                            "target_field": "Reviewers",
                            "candidates": [{"user_id": "user-1", "id": "user-1", "name": "Alex Chen"}],
                        }
                    ]
                },
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.requires_confirmation is False
    assert "people_resolution_required:reviewers:Alex" not in plan.warnings
    assert compact["summary"]["people_resolution_requirements"] == [
        {
            "target_type": "people_resolution",
            "source_record_key": "reviewers",
            "source_value": "Alex",
            "target_field": "Reviewers",
            "required": False,
            "blocking": False,
            "candidates": [{"user_id": "user-1", "id": "user-1", "name": "Alex Chen"}],
        }
    ]



def test_capture_plan_blocks_required_ambiguous_relation_candidates_from_enrichment(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
        "target_data_source_id": "ds-authors",
    }
    target["parser_profile"]["book"]["required_value_fields"] = ["author"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "enrichment": {
                    "relation_resolution_requirements": [
                        {
                            "record_key": "author",
                            "source_value": "刘瑜",
                            "target_field": "作者",
                            "target_database_id": "db-authors",
                            "target_data_source_id": "ds-authors",
                            "candidates": [
                                {"page_id": "author-1", "title": "刘瑜 A", "url": "https://notion.so/author-1"},
                                {"page_id": "author-2", "title": "刘瑜 B"},
                            ],
                        }
                    ]
                },
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "relation_resolution_required"
    assert plan.operations == []
    assert "relation_resolution_required:author:刘瑜" in plan.warnings
    assert compact["summary"]["relation_resolution_requirements"] == [
        {
            "target_type": "relation_resolution",
            "source_record_key": "author",
            "source_value": "刘瑜",
            "target_field": "作者",
            "target_database_id": "db-authors",
            "target_data_source_id": "ds-authors",
            "required": True,
            "blocking": True,
            "candidates": [
                {"page_id": "author-1", "title": "刘瑜 A", "url": "https://notion.so/author-1"},
                {"page_id": "author-2", "title": "刘瑜 B"},
            ],
        }
    ]
    assert compact["review"]["relation_resolution_requirements"] == compact["summary"]["relation_resolution_requirements"]
    assert any(warning["code"] == "relation_resolution_required:author:刘瑜" for warning in compact["review"]["blocking_warnings"])



def test_capture_plan_relation_decision_choose_existing_sets_relation_page_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
        "target_data_source_id": "ds-authors",
    }
    target["parser_profile"]["book"]["required_value_fields"] = ["author"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "enrichment": {
                    "relation_resolution_requirements": [
                        {
                            "record_key": "author",
                            "source_value": "刘瑜",
                            "target_field": "作者",
                            "target_database_id": "db-authors",
                            "target_data_source_id": "ds-authors",
                            "candidates": [{"page_id": "author-2", "title": "刘瑜 B"}],
                        }
                    ],
                    "requirement_decisions": [
                        {
                            "target_type": "relation_resolution",
                            "source_record_key": "author",
                            "source_value": "刘瑜",
                            "target_field": "作者",
                            "action": "choose_existing",
                            "page_id": "author-2",
                        }
                    ],
                },
            }
        ),
        cache,
    )
    compact = build_plan_cli_summary(plan)

    assert plan.requires_confirmation is False
    assert plan.normalized_record["author"] == "author-2"
    assert plan.field_mapping["author"] == "作者"
    assert not any(warning.startswith("relation_resolution_required:author:") for warning in plan.warnings)
    assert not any(warning.startswith("relation_resolution_pending:author:") for warning in plan.warnings)
    assert "relation_resolution_requirements" not in compact["summary"]
    assert "relation_resolution_requirements" not in compact["review"]



def test_capture_plan_relation_decision_skip_clears_optional_relation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
        "target_data_source_id": "ds-authors",
    }
    target["parser_profile"]["book"]["required_value_fields"] = ["isbn", "page_count"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
                "target_hint": "书单",
                "state": "初始化",
                "content_type_hint": "book",
                "enrichment": {
                    "relation_resolution_requirements": [
                        {
                            "record_key": "author",
                            "source_value": "刘瑜",
                            "target_field": "作者",
                            "target_database_id": "db-authors",
                            "target_data_source_id": "ds-authors",
                            "candidates": [{"page_id": "author-1", "title": "刘瑜 A"}],
                        }
                    ],
                    "requirement_decisions": [
                        {
                            "target_type": "relation_resolution",
                            "source_record_key": "author",
                            "source_value": "刘瑜",
                            "target_field": "作者",
                            "action": "skip",
                            "reason": "user_confirmed_skip",
                        }
                    ],
                },
            }
        ),
        cache,
    )

    assert plan.requires_confirmation is False
    assert plan.normalized_record["author"] is None
    assert "author" not in plan.field_mapping
    assert "relation_skipped:author:刘瑜" in plan.warnings



def test_book_capture_plan_blocks_relation_pending_when_profile_marks_warning_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
    }
    target["parser_profile"]["book"]["blocking_warning_prefixes"] = ["relation_resolution_pending:author:"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput(
            raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            target_hint="书单",
            state="初始化",
            content_type_hint="book",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "blocking_warning"
    assert "relation_resolution_pending:author:刘瑜" in plan.warnings
    assert plan.operations == []
    assert {
        "code": "relation_resolution_pending:author:刘瑜",
        "severity": "blocking",
        "category": "relation_resolution",
        "message": "刘瑜",
    } in plan.summary["warning_details"]



def test_book_capture_plan_keeps_blocking_warning_when_also_marked_non_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
    }
    target["parser_profile"]["book"]["non_blocking_warning_prefixes"] = ["relation_resolution_pending:author:"]
    target["parser_profile"]["book"]["blocking_warning_prefixes"] = ["relation_resolution_pending:author:"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    plan = build_capture_plan(
        CaptureInput(
            raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            target_hint="书单",
            state="初始化",
            content_type_hint="book",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "blocking_warning"
    assert plan.operations == []
    assert {
        "code": "relation_resolution_pending:author:刘瑜",
        "severity": "blocking",
        "category": "relation_resolution",
        "message": "刘瑜",
    } in plan.summary["warning_details"]



def test_book_capture_plan_blocks_when_required_asset_url_is_inaccessible(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["parser_profile"]["book"]["record_defaults"] = {"cover": "https://assets.example.invalid/cover.jpg"}
    target["parser_profile"]["book"]["asset_url_check_fields"] = ["cover"]
    cache.write_json(config.targets_dir / "bookshelf.json", target)
    monkeypatch.setattr(
        planner_module,
        "verify_image_url",
        lambda url: {"ok": False, "status": 403, "content_type": "text/html", "method": "HEAD"},
    )

    plan = build_capture_plan(
        CaptureInput(
            raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            target_hint="书单",
            state="初始化",
            content_type_hint="book",
            options=CaptureOptions(),
        ),
        cache,
    )

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "asset_url_inaccessible"
    assert "asset_url_inaccessible:cover:https://assets.example.invalid/cover.jpg" in plan.warnings
    assert plan.operations == []
    assert plan.planned_asset_operations[0].record_key == "cover"
    assert plan.summary["asset_actions"] == [
        {"record_key": "cover", "target_field": "封面", "action": "download_and_attach"}
    ]



def test_book_capture_plan_builds_profile_relation_completion_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["parser_profile"]["book"]["required_schema_fields"] = ["author", "state"]
    target["parser_profile"]["book"]["required_value_fields"] = ["author"]
    target["parser_profile"]["book"]["relation_completions"] = [
        {
            "source_record_key": "author",
            "target_data_source_id": "ds-authors",
            "field_mapping": {
                "author_picture": "Author Picture",
                "author_country": "国籍",
                "author_bio": "简介",
            },
            "labels": {
                "author_picture": ["作者图片"],
                "author_country": ["作者国家"],
                "author_bio": ["作者简介"],
            },
        }
    ]
    target["data_sources"]["books"]["schema"]["作者"] = {
        "type": "relation",
        "target_database_id": "db-authors",
    }
    target["data_sources"]["authors"] = {
        "data_source_id": "ds-authors",
        "title": "Authors",
        "role": "secondary",
        "content_types": [],
        "schema": {
            "Author Picture": {"type": "files"},
            "国籍": {"type": "select"},
            "简介": {"type": "rich_text"},
        },
    }
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input=(
            "把《失落的大陆》初始化到书单 作者：比尔·布莱森 "
            "作者图片：https://example.com/bryson.jpg 作者国家：美国"
        ),
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.completion_operations == [
        {
            "type": "complete_relation_page",
            "operation_id": "completion:0",
            "source_record_key": "author",
            "target_data_source_id": "ds-authors",
            "field_mapping": {
                "author_picture": "Author Picture",
                "author_country": "国籍",
            },
            "record": {
                "author_picture": "https://example.com/bryson.jpg",
                "author_country": "美国",
            },
            "asset_operations": [
                {
                    "type": "file",
                    "source_url": "https://example.com/bryson.jpg",
                    "local_cache_path": planner_module._asset_cache_path(
                        config,
                        "book",
                        "author_picture",
                        "https://example.com/bryson.jpg",
                    ),
                    "target_field": "Author Picture",
                    "action": "download_and_attach",
                    "record_key": "author_picture",
                    "status": "planned",
                    "warning": None,
                }
            ],
        }
    ]
    assert plan.summary["relation_completions"] == [
        {
            "source_record_key": "author",
            "target_data_source": "Authors",
            "writable_fields": {
                "author_picture": {
                    "target_field": "Author Picture",
                    "value_status": "present",
                    "write_status": "planned",
                },
                "author_country": {
                    "target_field": "国籍",
                    "value_status": "present",
                    "write_status": "planned",
                },
                "author_bio": {
                    "target_field": "简介",
                    "value_status": "missing_value",
                    "write_status": "omitted_missing_value",
                },
            },
        }
    ]
    assert plan.summary["write_targets"][1] == {
        "type": "relation_page",
        "action": "update_page",
        "source_record_key": "author",
        "source_value": "比尔·布莱森",
        "target_data_source": "Authors",
        "target_data_source_id": "ds-authors",
        "page_id": None,
        "page_id_status": "pending_relation_resolution",
    }


def test_build_plan_summary_reports_writable_fields_for_missing_mapped_values():
    summary = build_plan_summary(
        content_type="book",
        target_page="书单",
        target_data_source="Books",
        normalized_record={
            "title": "失落的大陆",
            "state": "initialized",
            "author": "比尔·布莱森",
        },
        field_mapping={
            "title": "名称",
            "state": "阅读状态",
            "author": "作者",
        },
        schema_fields={
            "title": "名称",
            "state": "阅读状态",
            "author": "作者",
            "language": "语言",
            "category": "分类",
            "rating": "评分",
        },
        asset_operations=[],
        requires_confirmation=False,
        confirmation_reason=None,
        warnings=[],
        summary_key_fields=["author"],
    )

    assert "language" not in summary["mapped_fields"]
    assert "category" not in summary["mapped_fields"]
    assert "rating" not in summary["mapped_fields"]
    assert summary["writable_fields"] == {
        "title": {
            "target_field": "名称",
            "value_status": "present",
            "write_status": "planned",
        },
        "state": {
            "target_field": "阅读状态",
            "value_status": "present",
            "write_status": "planned",
        },
        "author": {
            "target_field": "作者",
            "value_status": "present",
            "write_status": "planned",
        },
        "language": {
            "target_field": "语言",
            "value_status": "missing_value",
            "write_status": "omitted_missing_value",
        },
        "category": {
            "target_field": "分类",
            "value_status": "missing_value",
            "write_status": "omitted_missing_value",
        },
        "rating": {
            "target_field": "评分",
            "value_status": "missing_value",
            "write_status": "omitted_missing_value",
        },
    }
    assert summary["key_fields"] == {
        "author": {"target_field": "作者", "value_status": "present"}
    }



def test_parser_profile_for_uses_supplied_default_profile_without_business_labels():
    default_profile = {
        "required_schema_fields": ["cover", "author", "isbn", "page_count", "state"],
        "required_value_fields": ["author", "isbn", "page_count"],
        "summary_key_fields": ["cover", "author", "isbn", "page_count"],
        "trusted_field_sources": ["explicit", "profile"],
        "asset_trust_required_fields": ["cover"],
    }

    profile = parser_profile_for({}, {}, "book", default_profile)

    assert profile["required_schema_fields"] == ["cover", "author", "isbn", "page_count", "state"]
    assert profile["required_value_fields"] == ["author", "isbn", "page_count"]
    assert profile["summary_key_fields"] == ["cover", "author", "isbn", "page_count"]
    assert profile["trusted_field_sources"] == ["explicit", "profile"]
    assert profile["asset_trust_required_fields"] == ["cover"]
    assert "labels" not in profile



def test_parser_profile_for_has_no_book_defaults_without_supplied_profile():
    assert parser_profile_for({}, {}, "book") == {}



def test_extract_title_uses_generic_parser_profile_patterns():
    assert hasattr(planner_module, "extract_title")
    title = planner_module.extract_title(
        "Episode: AI and Everything host: Acquired",
        {"title_patterns": [r"Episode:\s*(.+?)(?=\s+host\s*:)"]},
    )

    assert title == "AI and Everything"



def test_extract_title_handles_nested_chinese_book_quotes():
    assert planner_module.extract_title("《340-思考是一种政治能力吗？（介绍阿伦特《心智生活》）》") == "340-思考是一种政治能力吗？（介绍阿伦特《心智生活》）"



def test_extract_title_truncates_before_known_label_assignment_suffix():
    assert planner_module.extract_title(
        "某一期节目 完成时间改成 2026-05-25",
        {"labels": {"completed_at": ["完成时间"]}},
    ) == "某一期节目"



def test_book_capture_plan_strips_target_alias_from_title_prefix_before_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="可能性的艺术 书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["title"] == "可能性的艺术"



def test_unresolved_plan_uses_generic_title_helper(monkeypatch):
    monkeypatch.setattr(planner_module, "extract_title", lambda raw_input, parser_profile=None: "patched title", raising=False)
    capture = CaptureInput(
        raw_input="raw title",
        target_hint="missing target",
        state="initialized",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = planner_module.unresolved_plan(capture, "podcast_episode", "target_not_resolved")

    assert plan.normalized_record["title"] == "patched title"
    assert plan.summary["title"] == "patched title"



def test_missing_required_helpers_do_not_apply_book_defaults_without_required_fields():
    assert missing_required_fields("book", {}) == []
    assert missing_required_values("book", {"title": "可能性的艺术"}) == []



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
    assert plan.planned_operations == [
        {"type": "create_or_update_page", "target_data_source": "Books", "data_source_id": "ds-books"}
    ]
    assert plan.asset_operations == []
    assert plan.summary["write_targets"] == [
        {
            "type": "primary_page",
            "action": "create_page",
            "target_page": "书单",
            "target_data_source": "Books",
            "data_source_id": "ds-books",
            "page_id": None,
            "page_id_status": "pending_after_apply",
            "title": "可能性的艺术",
            "write_status": "requires_confirmation",
        }
    ]
    assert plan.summary["key_fields"]["author"] == {"target_field": "作者", "value_status": "missing_value"}
    assert plan.summary["key_fields"]["isbn"] == {"target_field": "ISBN", "value_status": "missing_value"}
    assert plan.summary["key_fields"]["page_count"] == {"target_field": "页数", "value_status": "missing_value"}


def test_book_capture_plan_uses_config_default_required_value_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(
        config.config_file,
        {
            "parser_profiles": {
                "defaults": {
                    "book": {
                        "required_schema_fields": ["author", "state"],
                        "required_value_fields": ["author"],
                        "trusted_field_sources": ["explicit", "profile"],
                    }
                }
            }
        },
    )
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.confirmation_reason is None
    assert not any(warning.startswith("book_key_values_missing") for warning in plan.warnings)
    assert plan.summary["required_value_fields"] == ["author"]



def test_book_capture_plan_does_not_use_code_default_required_fields_when_config_default_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    write_json(config.config_file, {"parser_profiles": {"defaults": {"book": {}}}})
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert not any(warning.startswith("book_key_values_missing") for warning in plan.warnings)



def test_book_capture_plan_uses_parser_profile_required_schema_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)
    target = cache.read_json(config.targets_dir / "bookshelf.json", {})
    target["parser_profile"]["book"]["required_schema_fields"] = ["author", "state"]
    target["parser_profile"]["book"]["required_value_fields"] = ["author"]
    books = target["data_sources"]["books"]
    for record_key in ["cover", "isbn", "page_count"]:
        books["fields"].pop(record_key)
    for property_name in ["封面", "ISBN", "页数"]:
        books["schema"].pop(property_name)
    cache.write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.confirmation_reason is None
    assert not any(warning.startswith("book_schema_incomplete") for warning in plan.warnings)



def test_capture_plan_omits_state_when_input_does_not_request_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config)

    plan = build_capture_plan(
        CaptureInput.from_dict({"raw_input": "《可能性的艺术》", "target_hint": "书单", "content_type_hint": "book"}),
        cache,
    )

    assert plan.normalized_record["state"] is None
    assert "state" not in plan.field_mapping



def test_profile_normalized_record_extracts_custom_content_type_labels():
    capture = CaptureInput(
        raw_input="Title: Agent Notes creator: Aaron rating: 5 stars",
        target_hint=None,
        state="初始化",
        content_type_hint=None,
        options=CaptureOptions(),
    )

    record = planner_module._normalized_record_for_capture(
        capture,
        "book",
        {
            "title_patterns": [r"Title:\s*(.+?)(?=\s+creator\s*:)"] ,
            "labels": {"creator": ["creator"], "rating": ["rating"]},
        },
    )

    assert record["title"] == "Agent Notes"
    assert record["state"] == "initialized"
    assert record["creator"] == "Aaron"
    assert record["rating"] == "5 stars"


def test_profile_normalized_record_uses_record_defaults_only_when_configured():
    capture = CaptureInput(
        raw_input="Episode: Deep Dive podcast: Acquired",
        target_hint=None,
        state="初始化",
        content_type_hint=None,
        options=CaptureOptions(),
    )

    record = planner_module._normalized_record_for_capture(
        capture,
        "podcast_episode",
        {
            "title_patterns": [r"Episode:\s*(.+?)(?=\s+podcast\s*:)"] ,
            "labels": {"podcast": ["podcast"]},
            "record_defaults": {"episode_url": None, "published_at": None},
        },
    )
    record_without_defaults = planner_module._normalized_record_for_capture(
        capture,
        "podcast_episode",
        {"title_patterns": [r"Episode:\s*(.+?)(?=\s+podcast\s*:)"] , "labels": {"podcast": ["podcast"]}},
    )

    assert record["episode_url"] is None
    assert record["published_at"] is None
    assert "episode_url" not in record_without_defaults
    assert "published_at" not in record_without_defaults


def test_profile_normalized_record_uses_value_types_for_numeric_fields():
    capture = CaptureInput(
        raw_input="Article: Long Read length: 400 pages",
        target_hint=None,
        state="初始化",
        content_type_hint=None,
        options=CaptureOptions(),
    )

    record = planner_module._normalized_record_for_capture(
        capture,
        "book",
        {
            "title_patterns": [r"Article:\s*(.+?)(?=\s+length\s*:)"] ,
            "labels": {"length": ["length"]},
            "value_types": {"length": "integer"},
        },
    )

    assert record["length"] == 400
    assert isinstance(record["length"], int)


def test_book_normalized_record_helper_uses_parser_profile_labels_only():
    assert hasattr(planner_module, "_book_normalized_record")
    record = planner_module._book_normalized_record(
        raw_input="Book: The Art of Possibility writer: 刘瑜 code: 9787559847357 length: 400",
        state="想读",
        parser_profile={
            "labels": {
                "author": ["writer"],
                "isbn": ["code"],
                "page_count": ["length"],
            },
            "title_patterns": [r"Book:\s*(.+?)(?=\s+writer\s*:)"] ,
            "value_types": {"page_count": "integer"},
        },
    )

    assert record["title"] == "The Art of Possibility"
    assert record["state"] == "initialized"
    assert record["author"] == "刘瑜"
    assert record["isbn"] == "9787559847357"
    assert record["page_count"] == 400



def test_podcast_normalized_record_helper_uses_parser_profile_labels_only():
    assert hasattr(planner_module, "_podcast_normalized_record")
    record = planner_module._podcast_normalized_record(
        raw_input="收藏这期播客到播客库 节目名：忽左忽右",
        state="初始化",
        parser_profile={"labels": {"podcast": ["节目名"]}, "record_defaults": {"episode_url": None, "published_at": None}},
    )

    assert record["title"] == "收藏这期播客到播客库"
    assert record["state"] == "initialized"
    assert record["podcast"] == "忽左忽右"
    assert record["episode_url"] is None
    assert record["published_at"] is None



def test_book_capture_plan_does_not_generate_default_cover_without_profile_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record.get("cover") is None
    assert "cover" not in plan.field_mapping
    assert plan.asset_operations == []


def test_book_capture_plan_uses_profile_default_cover_url(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)
    target = json.loads((config.targets_dir / "bookshelf.json").read_text(encoding="utf-8"))
    target["parser_profile"]["book"]["record_defaults"] = {"cover": "https://assets.example.invalid/placeholder.jpg"}
    write_json(config.targets_dir / "bookshelf.json", target)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["cover"] == "https://assets.example.invalid/placeholder.jpg"
    assert plan.field_mapping["cover"] == "封面"
    assert len(plan.asset_operations) == 1
    assert plan.asset_operations[0].source_url == "https://assets.example.invalid/placeholder.jpg"


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


@pytest.mark.parametrize(
    ("raw_input", "expected_author"),
    [
        ("把《可能性的艺术》初始化到书单 作者：刘瑜、王小波 ISBN：9787559847357 页数：400", "刘瑜、王小波"),
        (
            "把《The Left Hand of Darkness》初始化到书单 author: Ursula K. Le Guin, Ann Leckie ISBN: 9780441478125 pages: 304",
            "Ursula K. Le Guin, Ann Leckie",
        ),
    ],
)
def test_book_capture_plan_preserves_multi_author_value_from_parser_profile_labels(
    tmp_path,
    monkeypatch,
    raw_input,
    expected_author,
):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)

    capture = CaptureInput(
        raw_input=raw_input,
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["author"] == expected_author
    assert plan.requires_confirmation is False


def test_book_capture_plan_parses_mixed_chinese_english_metadata_from_parser_profile_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)
    capture = CaptureInput(
        raw_input="把《The Left Hand of Darkness》初始化到书单 author: Ursula K. Le Guin ISBN: 9780441478125 页数：304",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, CacheStore(config))

    assert plan.normalized_record["title"] == "The Left Hand of Darkness"
    assert plan.normalized_record["author"] == "Ursula K. Le Guin"
    assert plan.normalized_record["isbn"] == "9780441478125"
    assert plan.normalized_record["page_count"] == 304
    assert plan.requires_confirmation is False


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


def test_capture_plan_merges_ai_structured_record_without_business_label_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config, parser_profile=False)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        structured_record={"author": "刘瑜", "isbn": "9787559847357", "page_count": 400},
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.normalized_record["title"] == "可能性的艺术"
    assert plan.normalized_record["author"] == "刘瑜"
    assert plan.normalized_record["isbn"] == "9787559847357"
    assert plan.normalized_record["page_count"] == 400
    assert "book_key_values_missing:author,isbn,page_count" not in plan.warnings


def test_capture_plan_merges_confirmed_enrichment_record_patch_and_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config, parser_profile=False)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        structured_record={"author": "刘瑜", "isbn": "9787559847357", "page_count": 400},
        enrichment={
            "record_patch": {"publisher": "广西师范大学出版社"},
            "confirmation_status": "confirmed",
        },
        sources=[
            {
                "source_id": "source-1",
                "type": "web",
                "title": "出版信息来源",
                "url": "https://example.com/book",
                "provided_by": "skill_ai",
                "confidence": "medium",
            }
        ],
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.normalized_record["publisher"] == "广西师范大学出版社"
    assert plan.sources == capture.sources


def test_capture_plan_blocks_unconfirmed_enrichment_conflicts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config, parser_profile=False)

    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="想读",
        content_type_hint="book",
        structured_record={
            "author": "刘瑜",
            "isbn": "9787559847357",
            "page_count": 400,
            "publisher": "来源 A 出版社",
        },
        enrichment={
            "record_patch": {"publisher": "来源 B 出版社"},
            "conflicts": [{"field": "publisher", "existing": "来源 A 出版社", "candidate": "来源 B 出版社"}],
            "confirmation_status": "needs_confirmation",
        },
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.normalized_record["publisher"] == "来源 A 出版社"
    assert "enrichment_conflict:publisher" in plan.warnings
    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "enrichment_conflict"


def test_allow_web_search_does_not_change_backend_plan_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_book_target(config, parser_profile=False)

    base_kwargs = {
        "raw_input": "把《可能性的艺术》初始化到书单",
        "target_hint": "书单",
        "state": "想读",
        "content_type_hint": "book",
        "structured_record": {"author": "刘瑜", "isbn": "9787559847357", "page_count": 400},
    }
    without_web = build_capture_plan(
        CaptureInput(**base_kwargs, options=CaptureOptions(allow_web_search=False)),
        cache,
    )
    with_web = build_capture_plan(
        CaptureInput(**base_kwargs, options=CaptureOptions(allow_web_search=True)),
        cache,
    )

    assert with_web.normalized_record == without_web.normalized_record
    assert with_web.field_mapping == without_web.field_mapping
    assert with_web.warnings == without_web.warnings
    assert with_web.requires_confirmation == without_web.requires_confirmation
    assert with_web.sources == without_web.sources


def test_podcast_capture_plan_summary_uses_parser_profile_key_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["summary_key_fields"] = ["podcast", "episode_url"]
    cache.write_json(config.targets_dir / "podcastshelf.json", target)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 节目：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.summary["key_fields"] == {
        "podcast": {"target_field": "播客", "value_status": "present"},
        "episode_url": {"target_field": "链接", "value_status": "missing_value"},
    }



def test_podcast_summary_field_requires_content_source_before_mapping(tmp_path, monkeypatch):
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
            "parser_profile": {
                "podcast_episode": {
                    "labels": {
                        "description": ["简介"],
                        "transcript": ["转录稿"],
                    },
                    "summary_fields": ["description"],
                    "summary_policy": {
                        "description": {
                            "preferred_skill": "summarize",
                            "requires_content_source": True,
                            "content_source_fields": ["transcript"],
                        }
                    },
                }
            },
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {
                        "title": "标题",
                        "state": "收听状态",
                        "description": "内容描述",
                    },
                    "field_sources": {
                        "title": "profile",
                        "state": "profile",
                        "description": "profile",
                    },
                    "schema": {
                        "标题": {"type": "title"},
                        "收听状态": {"type": "select"},
                        "内容描述": {"type": "rich_text"},
                    },
                }
            },
            "state_mapping": {"field": "收听状态", "values": {"initialized": "想听"}},
        },
    )

    capture = CaptureInput(
        raw_input="《339-如何让成年后的孩子依然和你亲近？》 简介：母亲节闲聊播客！",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "summary_content_source_missing"
    assert "summary_content_source_missing:description" in plan.warnings
    assert plan.normalized_record.get("description") is None
    assert "description" not in plan.field_mapping
    assert plan.operations == []
    assert plan.summary["enrichment_requirements"] == [
        {
            "field": "description",
            "kind": "content_summary",
            "preferred_skill": "summarize",
            "requires_content_source": True,
            "status": "blocked",
            "reason": "content_source_missing",
        }
    ]



def test_unmarked_podcast_description_remains_regular_labeled_field(tmp_path, monkeypatch):
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
            "parser_profile": {
                "podcast_episode": {
                    "labels": {"description": ["简介"]},
                }
            },
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-podcasts",
                    "title": "Podcast Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {
                        "title": "标题",
                        "state": "收听状态",
                        "description": "内容描述",
                    },
                    "schema": {
                        "标题": {"type": "title"},
                        "收听状态": {"type": "select"},
                        "内容描述": {"type": "rich_text"},
                    },
                }
            },
            "state_mapping": {"field": "收听状态", "values": {"initialized": "想听"}},
        },
    )

    capture = CaptureInput(
        raw_input="《339-如何让成年后的孩子依然和你亲近？》 简介：母亲节闲聊播客！",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.normalized_record["description"] == "母亲节闲聊播客！"
    assert plan.field_mapping["description"] == "内容描述"
    assert "enrichment_requirements" not in plan.summary



def test_book_capture_plan_summary_shows_reviewable_target_fields_and_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    seed_book_target(config)
    target = json.loads((config.targets_dir / "bookshelf.json").read_text(encoding="utf-8"))
    target["parser_profile"]["book"]["record_defaults"] = {"cover": "https://example.com/cover.jpg"}
    write_json(config.targets_dir / "bookshelf.json", target)

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
        "state": "想读",
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
        "writable_fields": {
            "title": {"target_field": "名称", "value_status": "present", "write_status": "planned"},
            "state": {"target_field": "阅读状态", "value_status": "present", "write_status": "planned"},
            "cover": {"target_field": "封面", "value_status": "present", "write_status": "planned"},
            "author": {"target_field": "作者", "value_status": "present", "write_status": "planned"},
            "isbn": {"target_field": "ISBN", "value_status": "present", "write_status": "planned"},
            "publisher": {"target_field": "出版社", "value_status": "missing_value", "write_status": "omitted_missing_value"},
            "page_count": {"target_field": "页数", "value_status": "present", "write_status": "planned"},
        },
        "write_targets": [
            {
                "type": "primary_page",
                "action": "create_page",
                "title": "可能性的艺术",
                "target_page": "书单",
                "target_data_source": "Books",
                "data_source_id": "ds-books",
                "page_id": None,
                "page_id_status": "pending_after_apply",
            }
        ],
        "asset_actions": [
            {"record_key": "cover", "target_field": "封面", "action": "download_and_attach"}
        ],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "warnings": [],
        "warning_details": [],
        "required_value_fields": ["author", "isbn", "page_count"],
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
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
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
    assert plan.summary["warning_details"] == [
        {
            "code": "ambiguous_field_mapping:page_count:Page Count,Pages",
            "severity": "blocking",
            "category": "confirmation",
            "message": "Page Count,Pages",
        }
    ]
    assert build_plan_cli_summary(plan)["warning_sections"]["blocking"] == plan.summary["warning_details"]
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
    target["parser_profile"]["book"]["record_defaults"] = {"cover": "https://example.com/cover.jpg"}
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


@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        ("author: Liu Yu", "Liu Yu"),
        ("作者：刘瑜", "刘瑜"),
        ("author = Liu Yu", "Liu Yu"),
        ("作者改成 刘瑜", "刘瑜"),
        ("作者设为 刘瑜", "刘瑜"),
        ("作者更新为 刘瑜", "刘瑜"),
        ("作者调整为 刘瑜", "刘瑜"),
        ("作者变更为 刘瑜", "刘瑜"),
    ],
)
def test_extract_labeled_author_supports_assignment_connectors(raw_input, expected):
    assert extract_labeled_value(raw_input, ["作者", "author"]) == expected



def test_extract_labeled_value_supports_transition_prefixes_and_sentence_terminators():
    raw_input = "这期播客已经听完了，然后状态改成已完成，完成时间改成 2026-05-25。页面信息：节目名=罗永浩的十字路口"
    known_labels = ["状态", "完成时间", "页面信息", "节目名"]

    assert extract_labeled_value(raw_input, ["状态"], known_labels) == "已完成"
    assert extract_labeled_value(raw_input, ["完成时间"], known_labels) == "2026-05-25"


def test_extract_labeled_value_preserves_chinese_periods_in_long_structured_fields():
    raw_input = "内容描述：第一句。第二句。\n状态：未开始"
    known_labels = ["内容描述", "状态"]

    assert extract_labeled_value(raw_input, ["内容描述"], known_labels) == "第一句。第二句"


def test_extract_labeled_value_preserves_pipe_inside_long_text_value():
    raw_input = (
        "内容描述：本期《半拿铁 | 商业沉浮录》讲述前湖北首富、"
        "东星航空创始人兰世立跌宕起伏的商业人生。\n"
        "参与人员：潇磊、刘飞"
    )

    value = extract_labeled_value(
        raw_input,
        ["内容描述"],
        known_labels=["主题", "状态", "内容描述", "参与人员"],
    )

    assert value == "本期《半拿铁 | 商业沉浮录》讲述前湖北首富、东星航空创始人兰世立跌宕起伏的商业人生"


def test_extract_labeled_value_treats_pipe_before_known_label_as_boundary():
    raw_input = "主题：No.203 ✈️ “不死鸟”兰世立 | 状态：未开始 | 参与人员：潇磊、刘飞"
    known_labels = ["主题", "状态", "参与人员"]

    assert extract_labeled_value(raw_input, ["主题"], known_labels) == "No.203 ✈️ “不死鸟”兰世立"
    assert extract_labeled_value(raw_input, ["状态"], known_labels) == "未开始"


def test_extract_labeled_value_stops_at_chinese_period_before_known_label():
    raw_input = "完成时间改成 2026-05-25。页面信息：节目名=罗永浩的十字路口"
    known_labels = ["完成时间", "页面信息", "节目名"]

    assert extract_labeled_value(raw_input, ["完成时间"], known_labels) == "2026-05-25"


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



def test_extract_labeled_value_prefers_longer_labels_for_main_and_known_boundaries():
    known_labels = ["节目", "节目名", "完成", "完成时间"]
    assert extract_labeled_value("节目名：忽左忽右 完成时间改成 2026-05-25", ["节目", "节目名"], known_labels) == "忽左忽右"
    assert extract_labeled_value("节目名：忽左忽右 完成时间改成 2026-05-25", ["完成", "完成时间"], known_labels) == "2026-05-25"


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

    assert plan.normalized_record.get("podcast") is None
    assert plan.normalized_record["title"] == "收藏这期播客到播客库 播客：忽左忽右"


def test_podcast_capture_plan_requires_trusted_mapping_sources_from_parser_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["required_schema_fields"] = ["podcast"]
    target["parser_profile"]["podcast_episode"]["trusted_field_sources"] = ["explicit", "profile"]
    target["data_sources"]["episodes"]["field_sources"] = {"podcast": "inferred"}
    cache.write_json(config.targets_dir / "podcastshelf.json", target)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:podcast:inferred" in plan.warnings
    assert "podcast_episode_schema_incomplete:podcast" in plan.warnings
    assert "podcast" not in plan.field_mapping
    assert plan.operations == []



def test_podcast_capture_plan_does_not_attach_untrusted_profile_required_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["asset_trust_required_fields"] = ["cover"]
    target["parser_profile"]["podcast_episode"]["trusted_field_sources"] = ["explicit", "profile"]
    target["data_sources"]["episodes"]["schema"] = {"封面": {"type": "files"}}
    target["data_sources"]["episodes"]["field_sources"] = {"cover": "type_fallback"}
    cache.write_json(config.targets_dir / "podcastshelf.json", target)

    capture = CaptureInput(
        raw_input="收藏这期播客到播客库 播客：忽左忽右",
        target_hint="播客库",
        state="初始化",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "untrusted_field_mapping"
    assert "untrusted_field_mapping:cover:type_fallback" in plan.warnings
    assert "cover" not in plan.field_mapping
    assert plan.asset_operations == []
    assert plan.summary["asset_actions"] == []



def test_podcast_capture_plan_uses_trusted_asset_field_for_any_required_asset_key(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["asset_trust_required_fields"] = ["attachment"]
    target["parser_profile"]["podcast_episode"]["trusted_field_sources"] = ["explicit", "profile"]
    target["data_sources"]["episodes"]["fields"]["attachment"] = "附件"
    target["data_sources"]["episodes"]["schema"] = {
        "附件": {"type": "files"},
        "旧附件": {"type": "files"},
    }
    target["data_sources"]["episodes"]["field_sources"] = {"attachment": "profile"}
    target["asset_mapping"] = {"attachment": {"field": "旧附件", "type": "files", "strategy": "download_and_attach"}}
    cache.write_json(config.targets_dir / "podcastshelf.json", target)
    monkeypatch.setattr(
        planner_module,
        "_normalized_record_for_capture",
        lambda capture, content_type, parser_profile: {
            "title": "忽左忽右",
            "state": "initialized",
            "attachment": "https://example.com/transcript.pdf",
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
    assert plan.field_mapping["attachment"] == "附件"
    assert plan.asset_operations[0].target_field == "附件"
    assert plan.summary["asset_actions"] == [
        {"record_key": "attachment", "target_field": "附件", "action": "download_and_attach"}
    ]



def test_data_source_asset_trust_extends_target_level_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["asset_trust_required_fields"] = ["cover"]
    target["parser_profile"]["podcast_episode"]["trusted_field_sources"] = ["profile"]
    target["data_sources"]["episodes"]["parser_profile"] = {
        "podcast_episode": {"asset_trust_required_fields": ["attachment"]}
    }
    target["data_sources"]["episodes"]["fields"]["attachment"] = "附件"
    target["data_sources"]["episodes"]["schema"] = {
        "封面": {"type": "files"},
        "旧封面": {"type": "files"},
        "附件": {"type": "files"},
        "旧附件": {"type": "files"},
    }
    target["data_sources"]["episodes"]["field_sources"] = {"cover": "profile", "attachment": "profile"}
    target["asset_mapping"] = {
        "cover": {"field": "旧封面", "type": "files", "strategy": "download_and_attach"},
        "attachment": {"field": "旧附件", "type": "files", "strategy": "download_and_attach"},
    }
    cache.write_json(config.targets_dir / "podcastshelf.json", target)
    monkeypatch.setattr(
        planner_module,
        "_normalized_record_for_capture",
        lambda capture, content_type, parser_profile: {
            "title": "忽左忽右",
            "state": "initialized",
            "cover": "https://example.com/cover.jpg",
            "attachment": "https://example.com/transcript.pdf",
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

    asset_targets = {operation.record_key: operation.target_field for operation in plan.asset_operations}
    assert plan.requires_confirmation is False
    assert asset_targets == {"cover": "封面", "attachment": "附件"}
    assert plan.field_mapping["cover"] == "封面"
    assert plan.field_mapping["attachment"] == "附件"



def test_podcast_capture_plan_removes_stale_asset_mapping_when_trusted_field_is_not_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    seed_podcast_target(config)
    target = cache.read_json(config.targets_dir / "podcastshelf.json", {})
    target["parser_profile"]["podcast_episode"]["asset_trust_required_fields"] = ["attachment"]
    target["parser_profile"]["podcast_episode"]["trusted_field_sources"] = ["explicit", "profile"]
    target["data_sources"]["episodes"]["fields"]["attachment"] = "附件"
    target["data_sources"]["episodes"]["schema"] = {
        "附件": {"type": "url"},
        "旧附件": {"type": "files"},
    }
    target["data_sources"]["episodes"]["field_sources"] = {"attachment": "profile"}
    target["asset_mapping"] = {"attachment": {"field": "旧附件", "type": "files", "strategy": "download_and_attach"}}
    cache.write_json(config.targets_dir / "podcastshelf.json", target)
    monkeypatch.setattr(
        planner_module,
        "_normalized_record_for_capture",
        lambda capture, content_type, parser_profile: {
            "title": "忽左忽右",
            "state": "initialized",
            "attachment": "https://example.com/transcript.pdf",
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
    assert plan.field_mapping["attachment"] == "附件"
    assert plan.asset_operations == []
    assert plan.summary["asset_actions"] == []



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
            "parser_profile": {
                "podcast_episode": {
                    "non_blocking_warning_prefixes": ["ambiguous_field_mapping:page_count:"],
                }
            },
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
    assert plan.summary["warning_details"] == [
        {
            "code": "ambiguous_field_mapping:page_count:Page Count,Pages",
            "severity": "notice",
            "category": "field_mapping",
            "message": "Page Count,Pages",
        }
    ]
    assert build_plan_cli_summary(plan)["warning_sections"]["notices"] == plan.summary["warning_details"]
    assert plan.operations == [
        {
            "type": "create_or_update_page",
            "target_data_source": "Episodes",
            "data_source_id": "db-episodes",
        }
    ]


def test_capture_plan_applies_non_blocking_warning_prefixes_per_data_source(tmp_path, monkeypatch):
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
                    "fields": {"title": "标题", "podcast": "播客", "state": "状态"},
                    "schema": {
                        "标题": {"type": "title"},
                        "播客": {"type": "rich_text"},
                        "状态": {"type": "select"},
                    },
                },
                "db-tags": {
                    "data_source_id": "db-tags",
                    "title": "Tags",
                    "role": "secondary",
                    "content_types": [],
                    "parser_profile": {
                        "non_blocking_warning_prefixes": ["ambiguous_field_mapping:tag:"],
                    },
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

    assert plan.requires_confirmation is False
    assert plan.confirmation_reason is None
    assert plan.warnings == ["ambiguous_field_mapping:tag:分类,标签"]
    assert plan.operations == [
        {
            "type": "create_or_update_page",
            "target_data_source": "Episodes",
            "data_source_id": "db-episodes",
        }
    ]


def test_data_source_warning_policy_extends_target_level_policy(tmp_path, monkeypatch):
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
            "parser_profile": {
                "podcast_episode": {
                    "non_blocking_warning_prefixes": ["ambiguous_field_mapping:page_count:"],
                }
            },
            "data_sources": {
                "db-episodes": {
                    "data_source_id": "db-episodes",
                    "title": "Episodes",
                    "role": "primary",
                    "content_types": ["podcast_episode"],
                    "fields": {"title": "标题", "podcast": "播客", "state": "状态"},
                    "schema": {
                        "标题": {"type": "title"},
                        "播客": {"type": "rich_text"},
                        "状态": {"type": "select"},
                    },
                },
                "db-tags": {
                    "data_source_id": "db-tags",
                    "title": "Tags",
                    "role": "secondary",
                    "content_types": [],
                    "parser_profile": {
                        "non_blocking_warning_prefixes": ["ambiguous_field_mapping:tag:"],
                    },
                    "fields": {"tag": "标签", "page_count": "Page Count"},
                    "mapping_warnings": [
                        "ambiguous_field_mapping:page_count:Page Count,Pages",
                        "ambiguous_field_mapping:tag:分类,标签",
                    ],
                    "schema": {
                        "Page Count": {"type": "number"},
                        "Pages": {"type": "rich_text"},
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

    assert plan.requires_confirmation is False
    assert plan.confirmation_reason is None
    assert plan.warnings == [
        "ambiguous_field_mapping:page_count:Page Count,Pages",
        "ambiguous_field_mapping:tag:分类,标签",
    ]
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

    assert plan.normalized_record.get("podcast") is None


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


def test_capture_plan_uses_single_writable_non_primary_data_source_with_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "播客子页": {
                    "type": "page",
                    "page_id": "page-podcast-child",
                    "target_id": "podcast-child",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "podcast-child.json",
        {
            "target": {"page_id": "page-podcast-child", "title": "播客子页", "target_id": "podcast-child"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "title": "Episodes",
                    "role": "secondary",
                    "content_types": [],
                    "fields": {},
                    "field_sources": {},
                    "mapping_warnings": [],
                    "schema": {
                        "主题": {"type": "title"},
                        "内容描述": {"type": "rich_text"},
                        "完成时间": {"type": "date"},
                        "状态": {"type": "select"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {},
            "asset_mapping": {},
            "requires_confirmation": True,
            "confirmation_reason": "field_mapping_missing",
        },
    )

    capture = CaptureInput(
        raw_input="小宇宙单集：https://example.com/episode/1；播客：后互联网时代的乱弹",
        target_hint="播客子页",
        state="initialized",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.target.page_title == "播客子页"
    assert plan.target.data_source_id == "ds-episodes"
    assert plan.summary["target_data_source"] == "Episodes"
    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "field_mapping_missing"
    assert plan.operations == []



def test_podcast_plan_uses_trusted_mapping_on_single_non_primary_data_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "播客子页": {
                    "type": "page",
                    "page_id": "page-podcast-child",
                    "target_id": "podcast-child",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "podcast-child.json",
        {
            "target": {"page_id": "page-podcast-child", "title": "播客子页", "target_id": "podcast-child"},
            "parser_profile": {
                "podcast_episode": {
                    "labels": {"episode_url": ["小宇宙单集", "url"], "podcast": ["播客"]},
                    "field_mapping": {"title": "主题", "episode_url": "内容描述", "state": "状态"},
                    "trusted_field_sources": ["explicit", "profile"],
                }
            },
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "title": "Episodes",
                    "role": "secondary",
                    "content_types": [],
                    "fields": {"title": "主题", "episode_url": "内容描述", "state": "状态"},
                    "field_sources": {"title": "profile", "episode_url": "profile", "state": "profile"},
                    "mapping_warnings": [],
                    "schema": {
                        "主题": {"type": "title"},
                        "内容描述": {"type": "rich_text"},
                        "参与人员": {"type": "rich_text"},
                        "完成时间": {"type": "date"},
                        "状态": {"type": "select"},
                    },
                }
            },
            "relations": [],
            "state_mapping": {"field": "状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )

    capture = CaptureInput(
        raw_input="小宇宙单集：https://example.com/episode/1；播客：后互联网时代的乱弹",
        target_hint="播客子页",
        state="initialized",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is False
    assert plan.confirmation_reason is None
    assert plan.target.data_source_id == "ds-episodes"
    assert plan.normalized_record["episode_url"] == "https://example.com/episode/1"
    assert plan.field_mapping == {"title": "主题", "state": "状态", "episode_url": "内容描述"}
    assert plan.operations == [
        {
            "type": "create_or_update_page",
            "target_data_source": "Episodes",
            "data_source_id": "ds-episodes",
        }
    ]
    assert plan.summary["write_targets"] == [
        {
            "type": "primary_page",
            "action": "create_page",
            "title": "小宇宙单集：https://example.com/episode/1；播客：后互联网时代的乱弹",
            "target_page": "播客子页",
            "target_data_source": "Episodes",
            "data_source_id": "ds-episodes",
            "page_id": None,
            "page_id_status": "pending_after_apply",
        }
    ]



def test_capture_plan_requires_confirmation_for_multiple_writable_non_primary_data_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "播客子页": {
                    "type": "page",
                    "page_id": "page-podcast-child",
                    "target_id": "podcast-child",
                }
            }
        },
    )
    cache.write_json(
        config.targets_dir / "podcast-child.json",
        {
            "target": {"page_id": "page-podcast-child", "title": "播客子页", "target_id": "podcast-child"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "title": "Episodes",
                    "role": "secondary",
                    "schema": {"主题": {"type": "title"}},
                },
                "queue": {
                    "data_source_id": "ds-queue",
                    "title": "Queue",
                    "role": "secondary",
                    "schema": {"名称": {"type": "title"}},
                },
            },
        },
    )

    capture = CaptureInput(
        raw_input="小宇宙单集：https://example.com/episode/1",
        target_hint="播客子页",
        state="initialized",
        content_type_hint="podcast_episode",
        options=CaptureOptions(),
    )

    plan = build_capture_plan(capture, cache)

    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "data_source_ambiguous"
    assert plan.target.data_source_id is None
    assert plan.operations == []



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
    target = json.loads((config.targets_dir / "bookshelf.json").read_text(encoding="utf-8"))
    target["parser_profile"]["book"]["record_defaults"] = {"cover": "https://example.com/cover.jpg"}
    write_json(config.targets_dir / "bookshelf.json", target)

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
            "parser_profile": {"book": {**BOOK_PARSER_PROFILE, "record_defaults": {"cover": "https://example.com/cover.jpg"}}},
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
            "parser_profile": {"book": {**BOOK_PARSER_PROFILE, "record_defaults": {"cover": "https://example.com/cover.jpg"}}},
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
            "parser_profile": {"book": {**BOOK_PARSER_PROFILE, "record_defaults": {"cover": "https://example.com/cover.jpg"}}},
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
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
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
            "parser_profile": {
                "book": {
                    **BOOK_PARSER_PROFILE,
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "summary_key_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
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
        "cover": None,
        "author": "刘瑜",
        "isbn": "9787559847357",
        "publisher": None,
        "page_count": 400,
    }
    assert plan.field_mapping == {
        "title": "书名",
        "state": "阅读进度",
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


def test_v2_plan_maps_profile_state_mapping_to_target_option(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-books",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-library"},
            "pages": {"page-library": {"page_id": "page-library", "title": "书单"}},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "名称": {"id": "title", "type": "title", "name": "名称"},
                        "状态": {
                            "id": "state",
                            "type": "status",
                            "name": "状态",
                            "status": {"options": [{"name": "Next"}, {"name": "Finished"}]},
                        },
                    },
                }
            },
            "views": {},
        },
    )
    store.write_profile(
        "profile-books",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "field_mapping": {"title": "名称", "state": "状态"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "state_mapping": {"initialized": "Next", "待读": "Next", "completed": "Finished"},
                    "parser_profile": {
                        "labels": {"title": ["书名"]},
                        "required_schema_fields": [],
                        "required_value_fields": [],
                        "summary_key_fields": [],
                        "trusted_field_sources": ["user_binding"],
                    },
                }
            },
        },
    )
    store.bind_alias("书单", graph_id="graph-books", profile_id="profile-books", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "书名：羞耻感",
                "target_hint": "书单",
                "state": "待读",
                "content_type_hint": "book",
            }
        ),
        store,
    )

    assert plan.normalized_record["state"] == "Next"
    assert plan.summary["state"] == "Next"
    assert plan.field_mapping["state"] == "状态"



def test_v2_plan_uses_write_profile_and_view_context(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-1",
        {
            "cache_version": 2,
            "graph_id": "graph-1",
            "root": {"kind": "page", "id": "page-1"},
            "pages": {
                "page-1": {"page_id": "page-1", "title": "Program", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "databases": {
                "db-1": {"database_id": "db-1", "title": "Episodes DB", "parent": {"type": "page_id", "id": "page-1"}},
            },
            "data_sources": {
                "ds-1": {
                    "data_source_id": "ds-1",
                    "database_id": "db-1",
                    "parent": {"type": "database_id", "id": "db-1"},
                    "title": "Rows",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Summary": {"id": "s", "type": "rich_text", "name": "Summary"},
                    },
                }
            },
            "views": {"view-1": {"view_id": "view-1", "name": "Episodes", "type": "gallery", "data_source_id": "ds-1"}},
        },
    )
    store.write_profile(
        "profile-1",
        {
            "cache_version": 2,
            "profile_id": "profile-1",
            "graph_id": "graph-1",
            "write_profiles": {
                "podcast_episode": {
                    "canonical_data_source_id": "ds-1",
                    "canonical_view_id": "view-1",
                    "field_mapping": {"title": "Name", "description": "Summary"},
                    "field_sources": {"title": "user_binding", "description": "user_binding"},
                    "parser_profile": {"labels": {"description": ["摘要"]}},
                }
            },
        },
    )
    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    plan = build_capture_plan(
        CaptureInput.from_dict({"raw_input": "标题：Example\n摘要：Summary text", "target_hint": "Program", "content_type_hint": "podcast_episode"}),
        store,
    )

    assert plan.target.data_source_id == "ds-1"
    assert plan.target.target_id == "graph-1"
    assert plan.target.view_id == "view-1"
    assert plan.target.view_type == "gallery"
    assert plan.field_mapping == {"title": "Name", "description": "Summary"}
    write_target = plan.summary["write_targets"][0]
    assert write_target["target_kind"] == "view_backed_data_source"
    assert write_target["display_view_type"] == "gallery"
    assert write_target["display_view_name"] == "Episodes"
    assert write_target["data_source_id"] == "ds-1"
    assert write_target["target_path"] == "工作区顶层 / Program / Episodes DB / Rows"
    assert write_target["target_path_complete"] is True



def test_v2_plan_builds_relation_completion_for_secondary_graph_data_source(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-books",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-library"},
            "pages": {
                "page-library": {"page_id": "page-library", "title": "Library", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "databases": {
                "db-books": {"database_id": "db-books", "title": "Books", "parent": {"type": "page_id", "id": "page-library"}},
                "db-authors": {"database_id": "db-authors", "title": "Authors", "parent": {"type": "page_id", "id": "page-library"}},
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "database_id": "db-books",
                    "parent": {"type": "database_id", "id": "db-books"},
                    "title": "Books",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "State": {"id": "state", "type": "multi_select", "name": "State"},
                        "Author Page": {"id": "rel", "type": "relation", "name": "Author Page", "target_database_id": "db-authors"},
                    },
                },
                "ds-authors": {
                    "data_source_id": "ds-authors",
                    "database_id": "db-authors",
                    "parent": {"type": "database_id", "id": "db-authors"},
                    "title": "Authors",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "State": {"id": "state", "type": "multi_select", "name": "State"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_profile(
        "profile-books",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "field_mapping": {"title": "Name", "state": "State", "author_relation": "Author Page"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "author_relation": "user_binding"},
                    "parser_profile": {
                        "labels": {"title": ["书名"], "author_relation": ["关联作者"]},
                        "required_schema_fields": [],
                        "required_value_fields": [],
                        "summary_key_fields": [],
                        "trusted_field_sources": ["user_binding"],
                        "relation_completions": [
                            {
                                "source_record_key": "author_relation",
                                "target_data_source_id": "ds-authors",
                                "field_mapping": {"author_relation": "Name", "state": "State"},
                                "labels": {"author_relation": ["关联作者"], "state": ["作者状态"]},
                            }
                        ],
                    },
                }
            },
        },
    )
    store.bind_alias("Library", graph_id="graph-books", profile_id="profile-books", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "书名：关系测试\n关联作者：CTN E2E Author\n作者状态：initialized",
                "target_hint": "Library",
                "content_type_hint": "book",
            }
        ),
        store,
    )

    assert plan.completion_operations == [
        {
            "type": "complete_relation_page",
            "operation_id": "completion:0",
            "source_record_key": "author_relation",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"author_relation": "Name", "state": "State"},
            "record": {"author_relation": "CTN E2E Author", "state": "initialized"},
            "asset_operations": [],
        }
    ]
    assert plan.summary["relation_completions"][0]["target_data_source"] == "Authors"
    assert plan.summary["write_targets"][1]["target_data_source"] == "Authors"



def test_v2_cache_database_lookup_requires_unique_data_source(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    for graph_id, data_source_id in (("graph-authors-a", "ds-authors-a"), ("graph-authors-b", "ds-authors-b")):
        store.write_graph(
            graph_id,
            {
                "cache_version": 2,
                "graph_id": graph_id,
                "root": {"kind": "page", "id": "page-library"},
                "data_sources": {
                    data_source_id: {
                        "data_source_id": data_source_id,
                        "database_id": "db-authors",
                        "title": "Authors",
                        "schema": {"Name": {"type": "title"}},
                    }
                },
                "views": {},
            },
        )

    assert store.find_graph_data_source_by_database("db-authors") is None



def test_v2_plan_builds_relation_target_manifest_from_secondary_cached_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-books",
        {
            "cache_version": 2,
            "graph_id": "graph-books",
            "root": {"kind": "page", "id": "page-library"},
            "pages": {
                "page-library": {"page_id": "page-library", "title": "Library", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "databases": {
                "db-books": {"database_id": "db-books", "title": "Books", "parent": {"type": "page_id", "id": "page-library"}},
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "database_id": "db-books",
                    "parent": {"type": "database_id", "id": "db-books"},
                    "title": "Books",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Author Page": {
                            "id": "rel",
                            "type": "relation",
                            "name": "Author Page",
                            "target_database_id": "db-authors",
                        },
                    },
                },
            },
            "views": {},
        },
    )
    store.write_graph(
        "graph-authors",
        {
            "cache_version": 2,
            "graph_id": "graph-authors",
            "root": {"kind": "page", "id": "page-library"},
            "pages": {
                "page-library": {"page_id": "page-library", "title": "Library", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "databases": {
                "db-authors": {"database_id": "db-authors", "title": "Authors", "parent": {"type": "page_id", "id": "page-library"}},
            },
            "data_sources": {
                "ds-authors": {
                    "data_source_id": "ds-authors",
                    "database_id": "db-authors",
                    "parent": {"type": "database_id", "id": "db-authors"},
                    "title": "Authors",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Author Picture": {"id": "files", "type": "files", "name": "Author Picture"},
                        "Bio": {"id": "bio", "type": "rich_text", "name": "Bio"},
                        "Country": {"id": "country", "type": "select", "name": "Country"},
                        "Book Count": {"id": "count", "type": "rollup", "name": "Book Count"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_profile(
        "profile-books",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "field_mapping": {"title": "Name", "author_relation": "Author Page"},
                    "field_sources": {"title": "user_binding", "author_relation": "user_binding"},
                    "relation_mapping": {"author_relation": {"create_missing": True}},
                    "parser_profile": {
                        "labels": {"title": ["书名"], "author_relation": ["关联作者"]},
                        "required_schema_fields": [],
                        "required_value_fields": [],
                        "summary_key_fields": [],
                        "trusted_field_sources": ["user_binding"],
                    },
                }
            },
        },
    )
    store.bind_alias("Library", graph_id="graph-books", profile_id="profile-books", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "书名：关系目标测试\n关联作者：CTN E2E Author",
                "target_hint": "Library",
                "content_type_hint": "book",
            }
        ),
        store,
    )

    expected_target_plan = {
        "source_record_key": "author_relation",
        "source_value": "CTN E2E Author",
        "action": "create_page",
        "target_data_source": "Authors",
        "target_data_source_id": "ds-authors",
        "page_id": None,
        "page_id_status": "pending_after_apply",
        "writable_fields": {
            "title": {"target_field": "Name", "value_status": "present", "write_status": "planned", "type": "title"},
            "Author Picture": {
                "target_field": "Author Picture",
                "value_status": "missing_value",
                "write_status": "needs_enrichment",
                "type": "files",
            },
            "Bio": {"target_field": "Bio", "value_status": "missing_value", "write_status": "needs_enrichment", "type": "rich_text"},
            "Country": {"target_field": "Country", "value_status": "missing_value", "write_status": "needs_user_choice", "type": "select"},
        },
        "omitted_fields": {
            "Book Count": {"type": "rollup", "write_status": "computed"},
        },
        "shell_page_risk": True,
    }
    assert plan.summary["relation_target_plans"] == [expected_target_plan]
    assert len(plan.summary["write_targets"]) == 2
    primary_target, relation_target = plan.summary["write_targets"]
    assert primary_target["type"] == "primary_page"
    assert relation_target == {
        "type": "relation_target_page",
        **expected_target_plan,
        "write_status": "requires_confirmation",
    }
    assert relation_target["writable_fields"] == plan.summary["relation_target_plans"][0]["writable_fields"]
    assert relation_target["omitted_fields"] == plan.summary["relation_target_plans"][0]["omitted_fields"]
    assert "relation_target_shell_page:author_relation:CTN E2E Author" in plan.warnings
    expected_requirements = [
        {
            "target_type": "relation_target_page",
            "target_role": "relation_target_page",
            "source_record_key": "author_relation",
            "source_value": "CTN E2E Author",
            "target_data_source_id": "ds-authors",
            "field": "Author Picture",
            "target_field": "Author Picture",
            "property_type": "files",
            "requirement_type": "enrichment",
            "allowed_sources": ["web_search_or_user_input"],
            "default_action": "web_search_or_user_input",
            "blocking": True,
        },
        {
            "target_type": "relation_target_page",
            "target_role": "relation_target_page",
            "source_record_key": "author_relation",
            "source_value": "CTN E2E Author",
            "target_data_source_id": "ds-authors",
            "field": "Bio",
            "target_field": "Bio",
            "property_type": "rich_text",
            "requirement_type": "enrichment",
            "allowed_sources": ["web_search_or_user_input"],
            "default_action": "web_search_or_user_input",
            "blocking": True,
        },
        {
            "target_type": "relation_target_page",
            "target_role": "relation_target_page",
            "source_record_key": "author_relation",
            "source_value": "CTN E2E Author",
            "target_data_source_id": "ds-authors",
            "field": "Country",
            "target_field": "Country",
            "property_type": "select",
            "requirement_type": "user_choice",
            "allowed_sources": ["ask_user"],
            "default_action": "ask_user",
            "blocking": True,
        },
    ]
    assert plan.summary["enrichment_requirements"] == expected_requirements
    assert all(requirement["field"] != "Book Count" for requirement in plan.summary["enrichment_requirements"])
    review = build_plan_cli_summary(plan)["review"]
    assert review["relation_target_plans"] == [expected_target_plan]
    assert review["enrichment_requirements"] == expected_requirements
    verification_expectations = review["verification_expectations"]
    assert verification_expectations["relations"] == ["author_relation"]
    assert len(verification_expectations["targets"]) == 2
    primary_expectation, relation_expectation = verification_expectations["targets"]
    assert primary_expectation["target_type"] == "primary_page"
    assert primary_expectation["relations"] == ["author_relation"]
    assert relation_expectation == {
        "target_type": "relation_target_page",
        "source_record_key": "author_relation",
        "source_value": "CTN E2E Author",
        "target_data_source_id": "ds-authors",
        "page_id_status": "pending_after_apply",
        "planned_fields": ["title"],
        "enrichment_required_fields": ["Author Picture", "Bio"],
        "user_choice_required_fields": ["Country"],
        "computed_fields": ["Book Count"],
        "shell_page_risk": True,
    }
    assert plan.requires_confirmation is True
    assert plan.confirmation_reason == "relation_target_shell_page"



def test_v2_real_like_book_plan_regression_uses_secondary_author_graph_without_apply(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "book-list-main",
        {
            "cache_version": 2,
            "graph_id": "book-list-main",
            "root": {"kind": "page", "id": "page-book-list"},
            "pages": {
                "page-book-list": {
                    "page_id": "page-book-list",
                    "title": "Book List",
                    "parent": {"type": "workspace", "id": "workspace"},
                },
            },
            "databases": {
                "db-books": {"database_id": "db-books", "title": "Books", "parent": {"type": "page_id", "id": "page-book-list"}},
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "database_id": "db-books",
                    "parent": {"type": "database_id", "id": "db-books"},
                    "title": "Books",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Status": {
                            "id": "status",
                            "type": "status",
                            "name": "Status",
                            "status": {"options": [{"name": "Reading"}, {"name": "Finished"}]},
                        },
                        "Author": {
                            "id": "author_rel",
                            "type": "relation",
                            "name": "Author",
                            "target_database_id": "db-authors",
                        },
                        "ISBN": {"id": "isbn", "type": "rich_text", "name": "ISBN"},
                        "Pages": {"id": "pages", "type": "number", "name": "Pages"},
                    },
                },
            },
            "views": {
                "view-reading": {
                    "view_id": "view-reading",
                    "name": "Reading List",
                    "type": "gallery",
                    "data_source_id": "ds-books",
                    "quick_filters": {"status": {"status": {"equals": "Reading"}}},
                },
            },
        },
    )
    store.write_graph(
        "book-authors",
        {
            "cache_version": 2,
            "graph_id": "book-authors",
            "root": {"kind": "page", "id": "page-authors"},
            "pages": {
                "page-authors": {"page_id": "page-authors", "title": "Authors", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "databases": {
                "db-authors": {"database_id": "db-authors", "title": "Authors", "parent": {"type": "page_id", "id": "page-authors"}},
            },
            "data_sources": {
                "ds-authors": {
                    "data_source_id": "ds-authors",
                    "database_id": "db-authors",
                    "parent": {"type": "database_id", "id": "db-authors"},
                    "title": "Authors",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Author Picture": {"id": "picture", "type": "files", "name": "Author Picture"},
                        "Bio": {"id": "bio", "type": "rich_text", "name": "Bio"},
                        "Country": {"id": "country", "type": "select", "name": "Country"},
                        "Book Count": {"id": "book_count", "type": "rollup", "name": "Book Count"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_profile(
        "book-list-profile",
        {
            "cache_version": 2,
            "profile_id": "book-list-profile",
            "graph_id": "book-list-main",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": "view-reading",
                    "field_mapping": {
                        "title": "Name",
                        "state": "Status",
                        "author_relation": "Author",
                        "isbn": "ISBN",
                        "page_count": "Pages",
                    },
                    "field_sources": {
                        "title": "user_binding",
                        "state": "user_binding",
                        "author_relation": "user_binding",
                        "isbn": "user_binding",
                        "page_count": "user_binding",
                    },
                    "state_mapping": {"initialized": "Reading", "reading": "Reading", "completed": "Finished"},
                    "relation_mapping": {"author_relation": {"create_missing": True}},
                    "parser_profile": {
                        "labels": {
                            "title": ["书名", "title"],
                            "author_relation": ["作者", "author"],
                            "isbn": ["ISBN", "isbn"],
                            "page_count": ["页数", "pages"],
                        },
                        "required_schema_fields": [],
                        "required_value_fields": ["isbn", "page_count"],
                        "summary_key_fields": ["isbn", "page_count"],
                        "trusted_field_sources": ["user_binding"],
                    },
                }
            },
        },
    )
    store.bind_alias("真实书单", graph_id="book-list-main", profile_id="book-list-profile", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "书名：When We Cease to Understand the World\n作者：Benjamín Labatut\nISBN：9781681375663\n页数：192",
                "target_hint": "真实书单",
                "state": "reading",
                "content_type_hint": "book",
                "existing_page_id": "page-book-existing",
                "target_context_hint": "Notion view Reading List",
                "target_scope_hint": "database_like_area_or_view",
            }
        ),
        store,
    )
    compact = build_plan_cli_summary(plan)
    summary = compact["summary"]
    review = compact["review"]

    assert compact["target"]["view_id"] == "view-reading"
    assert review["target_semantics"]["view_name"] == "Reading List"
    assert summary["view_context"] == {
        "view_id": "view-reading",
        "view_name": "Reading List",
        "view_type": "gallery",
        "constraints": {
            "values": {"Status": "Reading"},
            "warnings": [],
            "unsupported": [],
            "conflicts": [],
        },
    }

    primary_target, relation_target = summary["write_targets"]
    assert primary_target["type"] == "primary_page"
    assert primary_target["action"] == "update_page"
    assert primary_target["page_id"] == "page-book-existing"
    assert relation_target["type"] == "relation_target_page"
    assert relation_target["action"] == "create_page"
    assert relation_target["target_data_source_id"] == "ds-authors"
    assert relation_target["source_record_key"] == "author_relation"
    assert relation_target["source_value"] == "Benjamín Labatut"
    assert relation_target["writable_fields"]["title"]["write_status"] == "planned"
    assert relation_target["writable_fields"]["Author Picture"]["write_status"] == "needs_enrichment"
    assert relation_target["writable_fields"]["Bio"]["write_status"] == "needs_enrichment"
    assert relation_target["writable_fields"]["Country"]["write_status"] == "needs_user_choice"
    assert relation_target["omitted_fields"]["Book Count"]["write_status"] == "computed"

    requirement_fields = [requirement["field"] for requirement in summary["enrichment_requirements"]]
    assert requirement_fields == ["Author Picture", "Bio", "Country"]
    assert [requirement["field"] for requirement in review["enrichment_requirements"]] == requirement_fields
    assert "Book Count" not in requirement_fields

    target_types = [target["target_type"] for target in review["verification_expectations"]["targets"]]
    assert target_types == ["primary_page", "relation_target_page"]
    relation_expectation = review["verification_expectations"]["targets"][1]
    assert relation_expectation["planned_fields"] == ["title"]
    assert relation_expectation["enrichment_required_fields"] == ["Author Picture", "Bio"]
    assert relation_expectation["user_choice_required_fields"] == ["Country"]
    assert relation_expectation["computed_fields"] == ["Book Count"]

    assert compact["requires_confirmation"] is True
    assert compact["confirmation_reason"] == "relation_target_shell_page"
    assert "relation_target_shell_page:author_relation:Benjamín Labatut" in compact["warnings"]
    assert any(warning["code"] == "relation_target_shell_page:author_relation:Benjamín Labatut" for warning in review["blocking_warnings"])



def test_v2_relation_target_requirement_decisions_resolve_or_skip_blockers(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "book-list-main",
        {
            "cache_version": 2,
            "graph_id": "book-list-main",
            "root": {"kind": "page", "id": "page-book-list"},
            "pages": {
                "page-book-list": {"page_id": "page-book-list", "title": "Book List", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "databases": {
                "db-books": {"database_id": "db-books", "title": "Books", "parent": {"type": "page_id", "id": "page-book-list"}},
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "database_id": "db-books",
                    "parent": {"type": "database_id", "id": "db-books"},
                    "title": "Books",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Status": {"id": "status", "type": "status", "name": "Status", "status": {"options": [{"name": "Reading"}, {"name": "Finished"}]}},
                        "Author": {"id": "author_rel", "type": "relation", "name": "Author", "target_database_id": "db-authors"},
                        "ISBN": {"id": "isbn", "type": "rich_text", "name": "ISBN"},
                        "Pages": {"id": "pages", "type": "number", "name": "Pages"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_graph(
        "book-authors",
        {
            "cache_version": 2,
            "graph_id": "book-authors",
            "root": {"kind": "page", "id": "page-authors"},
            "pages": {"page-authors": {"page_id": "page-authors", "title": "Authors", "parent": {"type": "workspace", "id": "workspace"}}},
            "databases": {"db-authors": {"database_id": "db-authors", "title": "Authors", "parent": {"type": "page_id", "id": "page-authors"}}},
            "data_sources": {
                "ds-authors": {
                    "data_source_id": "ds-authors",
                    "database_id": "db-authors",
                    "parent": {"type": "database_id", "id": "db-authors"},
                    "title": "Authors",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Author Picture": {"id": "picture", "type": "files", "name": "Author Picture"},
                        "Bio": {"id": "bio", "type": "rich_text", "name": "Bio"},
                        "Country": {"id": "country", "type": "select", "name": "Country"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_profile(
        "book-list-profile",
        {
            "cache_version": 2,
            "profile_id": "book-list-profile",
            "graph_id": "book-list-main",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "field_mapping": {"title": "Name", "state": "Status", "author_relation": "Author", "isbn": "ISBN", "page_count": "Pages"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "author_relation": "user_binding", "isbn": "user_binding", "page_count": "user_binding"},
                    "state_mapping": {"reading": "Reading"},
                    "relation_mapping": {"author_relation": {"create_missing": True}},
                    "parser_profile": {
                        "labels": {"title": ["书名"], "author_relation": ["作者"], "isbn": ["ISBN"], "page_count": ["页数"]},
                        "required_schema_fields": [],
                        "required_value_fields": ["isbn", "page_count"],
                        "summary_key_fields": ["isbn", "page_count"],
                        "trusted_field_sources": ["user_binding"],
                    },
                }
            },
        },
    )
    store.bind_alias("真实书单", graph_id="book-list-main", profile_id="book-list-profile", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "书名：When We Cease to Understand the World\n作者：Benjamín Labatut\nISBN：9781681375663\n页数：192",
                "target_hint": "真实书单",
                "state": "reading",
                "content_type_hint": "book",
                "existing_page_id": "page-book-existing",
                "enrichment": {
                    "requirement_decisions": [
                        {
                            "target_type": "relation_target_page",
                            "source_record_key": "author_relation",
                            "source_value": "Benjamín Labatut",
                            "field": "Bio",
                            "action": "provide_value",
                            "value": "Benjamín Labatut is a Chilean author.",
                        },
                        {
                            "target_type": "relation_target_page",
                            "source_record_key": "author_relation",
                            "source_value": "Benjamín Labatut",
                            "field": "Country",
                            "action": "choose_value",
                            "value": "Chile",
                        },
                        {
                            "target_type": "relation_target_page",
                            "source_record_key": "author_relation",
                            "source_value": "Benjamín Labatut",
                            "field": "Author Picture",
                            "action": "skip",
                            "reason": "user_confirmed_skip",
                        },
                    ]
                },
            }
        ),
        store,
    )
    compact = build_plan_cli_summary(plan)
    relation_target = next(target for target in compact["summary"]["write_targets"] if target["type"] == "relation_target_page")

    assert "enrichment_requirements" not in compact["summary"]
    assert "enrichment_requirements" not in compact["review"]
    assert relation_target["writable_fields"]["Bio"] == {
        "target_field": "Bio",
        "value_status": "present",
        "write_status": "planned",
        "type": "rich_text",
        "value": "Benjamín Labatut is a Chilean author.",
        "value_source": "user_decision",
    }
    assert relation_target["writable_fields"]["Country"] == {
        "target_field": "Country",
        "value_status": "present",
        "write_status": "planned",
        "type": "select",
        "value": "Chile",
        "value_source": "user_decision",
    }
    assert relation_target["writable_fields"]["Author Picture"] == {
        "target_field": "Author Picture",
        "value_status": "skipped",
        "write_status": "skipped",
        "type": "files",
        "skip_reason": "user_confirmed_skip",
    }
    assert relation_target["shell_page_risk"] is False

    relation_expectation = compact["review"]["verification_expectations"]["targets"][1]
    assert relation_expectation["planned_fields"] == ["title", "Bio", "Country"]
    assert relation_expectation["skipped_fields"] == ["Author Picture"]
    assert "enrichment_required_fields" not in relation_expectation
    assert "user_choice_required_fields" not in relation_expectation
    assert plan.completion_operations == [
        {
            "type": "complete_relation_page",
            "operation_id": "completion:0",
            "source_record_key": "author_relation",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"Bio": "Bio", "Country": "Country"},
            "record": {"Bio": "Benjamín Labatut is a Chilean author.", "Country": "Chile"},
            "asset_operations": [],
        }
    ]
    assert compact["confirmation_reason"] != "relation_target_shell_page"
    assert "relation_target_shell_page:author_relation:Benjamín Labatut" not in compact["warnings"]



def _build_real_like_relation_decision_plan(tmp_path, monkeypatch, decisions, relation_completions=None, raw_input_suffix=""):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "book-list-main",
        {
            "cache_version": 2,
            "graph_id": "book-list-main",
            "root": {"kind": "page", "id": "page-book-list"},
            "pages": {"page-book-list": {"page_id": "page-book-list", "title": "Book List", "parent": {"type": "workspace", "id": "workspace"}}},
            "databases": {"db-books": {"database_id": "db-books", "title": "Books", "parent": {"type": "page_id", "id": "page-book-list"}}},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "database_id": "db-books",
                    "parent": {"type": "database_id", "id": "db-books"},
                    "title": "Books",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Status": {"id": "status", "type": "status", "name": "Status", "status": {"options": [{"name": "Reading"}]}},
                        "Author": {"id": "author_rel", "type": "relation", "name": "Author", "target_database_id": "db-authors"},
                        "ISBN": {"id": "isbn", "type": "rich_text", "name": "ISBN"},
                        "Pages": {"id": "pages", "type": "number", "name": "Pages"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_graph(
        "book-authors",
        {
            "cache_version": 2,
            "graph_id": "book-authors",
            "root": {"kind": "page", "id": "page-authors"},
            "pages": {"page-authors": {"page_id": "page-authors", "title": "Authors", "parent": {"type": "workspace", "id": "workspace"}}},
            "databases": {"db-authors": {"database_id": "db-authors", "title": "Authors", "parent": {"type": "page_id", "id": "page-authors"}}},
            "data_sources": {
                "ds-authors": {
                    "data_source_id": "ds-authors",
                    "database_id": "db-authors",
                    "parent": {"type": "database_id", "id": "db-authors"},
                    "title": "Authors",
                    "schema": {
                        "Name": {"id": "title", "type": "title", "name": "Name"},
                        "Author Picture": {"id": "picture", "type": "files", "name": "Author Picture"},
                        "Bio": {"id": "bio", "type": "rich_text", "name": "Bio"},
                        "Country": {"id": "country", "type": "select", "name": "Country"},
                    },
                },
            },
            "views": {},
        },
    )
    store.write_profile(
        "book-list-profile",
        {
            "cache_version": 2,
            "profile_id": "book-list-profile",
            "graph_id": "book-list-main",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "field_mapping": {"title": "Name", "state": "Status", "author_relation": "Author", "isbn": "ISBN", "page_count": "Pages"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "author_relation": "user_binding", "isbn": "user_binding", "page_count": "user_binding"},
                    "state_mapping": {"reading": "Reading"},
                    "relation_mapping": {"author_relation": {"create_missing": True}},
                    "parser_profile": {
                        "labels": {"title": ["书名"], "author_relation": ["作者"], "isbn": ["ISBN"], "page_count": ["页数"]},
                        "required_schema_fields": [],
                        "required_value_fields": ["isbn", "page_count"],
                        "summary_key_fields": ["isbn", "page_count"],
                        "trusted_field_sources": ["user_binding"],
                        "relation_completions": relation_completions or [],
                    },
                }
            },
        },
    )
    store.bind_alias("真实书单", graph_id="book-list-main", profile_id="book-list-profile", kind="write_profile")
    raw_input = "书名：When We Cease to Understand the World\n作者：Benjamín Labatut\nISBN：9781681375663\n页数：192"
    if raw_input_suffix:
        raw_input = f"{raw_input}\n{raw_input_suffix}"
    return build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": raw_input,
                "target_hint": "真实书单",
                "state": "reading",
                "content_type_hint": "book",
                "existing_page_id": "page-book-existing",
                "enrichment": {"requirement_decisions": decisions},
            }
        ),
        store,
    )



def test_v2_partial_relation_target_requirement_decision_keeps_remaining_blockers(tmp_path, monkeypatch):
    plan = _build_real_like_relation_decision_plan(
        tmp_path,
        monkeypatch,
        [
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Bio",
                "action": "provide_value",
                "value": "Benjamín Labatut is a Chilean author.",
            }
        ],
    )
    compact = build_plan_cli_summary(plan)
    relation_target = next(target for target in compact["summary"]["write_targets"] if target["type"] == "relation_target_page")

    assert relation_target["writable_fields"]["Bio"]["write_status"] == "planned"
    assert relation_target["writable_fields"]["Author Picture"]["write_status"] == "needs_enrichment"
    assert relation_target["writable_fields"]["Country"]["write_status"] == "needs_user_choice"
    assert [requirement["field"] for requirement in compact["summary"]["enrichment_requirements"]] == ["Author Picture", "Country"]
    assert [requirement["field"] for requirement in compact["review"]["enrichment_requirements"]] == ["Author Picture", "Country"]
    relation_expectation = compact["review"]["verification_expectations"]["targets"][1]
    assert relation_expectation["planned_fields"] == ["title", "Bio"]
    assert relation_expectation["enrichment_required_fields"] == ["Author Picture"]
    assert relation_expectation["user_choice_required_fields"] == ["Country"]
    assert compact["requires_confirmation"] is True
    assert compact["confirmation_reason"] == "relation_target_shell_page"
    assert "relation_target_shell_page:author_relation:Benjamín Labatut" in compact["warnings"]
    assert plan.completion_operations == []



def test_v2_relation_target_file_url_decision_creates_completion_asset_operation(tmp_path, monkeypatch):
    picture_url = "https://example.com/benjamin-labatut.jpg"
    plan = _build_real_like_relation_decision_plan(
        tmp_path,
        monkeypatch,
        [
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Author Picture",
                "action": "provide_value",
                "value": picture_url,
            },
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Bio",
                "action": "provide_value",
                "value": "Benjamín Labatut is a Chilean author.",
            },
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Country",
                "action": "choose_value",
                "value": "Chile",
            },
        ],
    )
    compact = build_plan_cli_summary(plan)
    relation_target = next(target for target in compact["summary"]["write_targets"] if target["type"] == "relation_target_page")

    assert relation_target["writable_fields"]["Author Picture"] == {
        "target_field": "Author Picture",
        "value_status": "present",
        "write_status": "planned",
        "type": "files",
        "value": picture_url,
        "value_source": "user_decision",
    }
    assert "enrichment_requirements" not in compact["summary"]
    assert plan.completion_operations == [
        {
            "type": "complete_relation_page",
            "operation_id": "completion:0",
            "source_record_key": "author_relation",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"Author Picture": "Author Picture", "Bio": "Bio", "Country": "Country"},
            "record": {"Author Picture": picture_url, "Bio": "Benjamín Labatut is a Chilean author.", "Country": "Chile"},
            "asset_operations": [
                {
                    "type": "file",
                    "source_url": picture_url,
                    "local_cache_path": plan.completion_operations[0]["asset_operations"][0]["local_cache_path"],
                    "target_field": "Author Picture",
                    "action": "download_and_attach",
                    "record_key": "Author Picture",
                    "status": "planned",
                    "warning": None,
                }
            ],
        }
    ]
    asset_operation = plan.completion_operations[0]["asset_operations"][0]
    assert asset_operation["local_cache_path"]
    assert asset_operation["local_cache_path"].endswith(".jpg")
    relation_expectation = compact["review"]["verification_expectations"]["targets"][1]
    assert relation_expectation["planned_fields"] == ["title", "Author Picture", "Bio", "Country"]
    assert compact["requires_confirmation"] is False



def test_v2_relation_completion_operations_include_stable_operation_ids(tmp_path, monkeypatch):
    plan = _build_real_like_relation_decision_plan(
        tmp_path,
        monkeypatch,
        [
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Author Picture",
                "action": "skip",
            },
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Bio",
                "action": "provide_value",
                "value": "Benjamín Labatut is a Chilean author.",
            },
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Country",
                "action": "choose_value",
                "value": "Chile",
            },
        ],
    )

    assert [operation["operation_id"] for operation in plan.completion_operations] == ["completion:0"]



def test_v2_relation_completion_dedupes_overlapping_profile_and_target_plan(tmp_path, monkeypatch):
    plan = _build_real_like_relation_decision_plan(
        tmp_path,
        monkeypatch,
        [
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Author Picture",
                "action": "skip",
            },
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Bio",
                "action": "provide_value",
                "value": "Benjamín Labatut is a Chilean author.",
            },
            {
                "target_type": "relation_target_page",
                "source_record_key": "author_relation",
                "source_value": "Benjamín Labatut",
                "field": "Country",
                "action": "choose_value",
                "value": "Chile",
            },
        ],
        relation_completions=[
            {
                "source_record_key": "author_relation",
                "target_data_source_id": "ds-authors",
                "field_mapping": {"Bio": "Bio", "Country": "Country"},
                "labels": {"Bio": ["作者简介"], "Country": ["作者国家"]},
            }
        ],
        raw_input_suffix="作者简介：Benjamín Labatut is a Chilean author.\n作者国家：Chile",
    )

    assert plan.completion_operations == [
        {
            "type": "complete_relation_page",
            "operation_id": "completion:0",
            "source_record_key": "author_relation",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"Bio": "Bio", "Country": "Country"},
            "record": {"Bio": "Benjamín Labatut is a Chilean author.", "Country": "Chile"},
            "asset_operations": [],
        }
    ]



def test_v2_page_parent_plan_uses_existing_page_id_for_append(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-notes",
        {
            "cache_version": 2,
            "graph_id": "graph-notes",
            "root": {"kind": "page", "id": "page-notes"},
            "pages": {
                "page-notes": {"page_id": "page-notes", "title": "Notes", "parent": {"type": "workspace", "id": "workspace"}},
            },
            "data_sources": {},
            "views": {},
        },
    )
    store.bind_alias("Notes", graph_id="graph-notes", profile_id=None, kind="graph")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "追加到已有页面",
                "target_hint": "Notes",
                "input_shape_hint": "plain_text",
                "target_scope_hint": "existing_page",
                "existing_page_id": "page-existing",
            }
        ),
        store,
    )

    assert plan.operations[0]["type"] == "append_page_content"
    assert plan.operations[0]["page_id"] == "page-existing"
    assert plan.summary["write_targets"][0]["action"] == "append_page_content"
    assert plan.summary["write_targets"][0]["page_id"] == "page-existing"
    assert plan.target.target_kind == "existing_page"



def test_v2_plan_uses_existing_page_id_for_primary_update(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-podcast",
        {
            "cache_version": 2,
            "graph_id": "graph-podcast",
            "root": {"kind": "data_source", "id": "ds-podcast"},
            "data_sources": {
                "ds-podcast": {
                    "data_source_id": "ds-podcast",
                    "database_id": "db-podcast",
                    "parent_page_id": "page-program",
                    "schema": {
                        "主题": {"id": "title", "type": "title", "name": "主题"},
                        "状态": {"id": "state", "type": "select", "name": "状态", "select": {"options": [{"name": "已完成"}]}},
                        "完成时间": {"id": "done", "type": "date", "name": "完成时间"},
                    },
                }
            },
            "views": {},
        },
    )
    store.write_profile(
        "profile-podcast",
        {
            "cache_version": 2,
            "profile_id": "profile-podcast",
            "graph_id": "graph-podcast",
            "write_profiles": {
                "podcast_episode": {
                    "canonical_data_source_id": "ds-podcast",
                    "field_mapping": {"title": "主题", "state": "状态", "完成时间": "完成时间"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "完成时间": "user_binding"},
                    "parser_profile": {"labels": {"完成时间": ["完成时间"]}},
                }
            },
        },
    )
    store.bind_alias("后互联网时代的乱弹", graph_id="graph-podcast", profile_id="profile-podcast", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "主题：第214期 两部影片的故事\n状态：已完成\n完成时间：2026-05-26\n用户说明：它是《后互联网时代的乱弹》里面的播客。",
                "target_hint": "后互联网时代的乱弹",
                "state": "已完成",
                "content_type_hint": "podcast_episode",
                "existing_page_id": "page-existing-episode",
            }
        ),
        store,
    )

    assert plan.operations == [
        {
            "type": "create_or_update_page",
            "target_data_source": None,
            "data_source_id": "ds-podcast",
            "target_kind": "data_source",
            "page_id": "page-existing-episode",
        }
    ]
    assert plan.normalized_record["title"] == "第214期 两部影片的故事"
    assert plan.normalized_record["state"] == "已完成"
    assert plan.normalized_record["完成时间"] == "2026-05-26"
    write_target = plan.summary["write_targets"][0]
    assert write_target["action"] == "update_page"
    assert write_target["page_id"] == "page-existing-episode"
    assert write_target["page_id_status"] == "known"



def test_explicit_title_label_takes_priority_over_chinese_quoted_context():
    assert extract_title(
        "主题：第214期 两部影片的故事\n用户说明：它是《后互联网时代的乱弹》里面的播客。",
        {"labels": {"title": ["主题"]}},
    ) == "第214期 两部影片的故事"



def test_v2_plan_maps_unbound_schema_field_when_input_uses_same_label(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-podcast",
        {
            "cache_version": 2,
            "graph_id": "graph-podcast",
            "root": {"kind": "data_source", "id": "ds-podcast"},
            "data_sources": {
                "ds-podcast": {
                    "data_source_id": "ds-podcast",
                    "schema": {
                        "主题": {"id": "title", "type": "title", "name": "主题"},
                        "状态": {"id": "state", "type": "select", "name": "状态", "select": {"options": [{"name": "进行中"}]}},
                        "内容描述": {"id": "desc", "type": "rich_text", "name": "内容描述"},
                        "参与人员": {"id": "people", "type": "rich_text", "name": "参与人员"},
                    },
                }
            },
            "views": {},
        },
    )
    store.write_profile(
        "profile-podcast",
        {
            "cache_version": 2,
            "profile_id": "profile-podcast",
            "graph_id": "graph-podcast",
            "write_profiles": {
                "podcast_episode": {
                    "canonical_data_source_id": "ds-podcast",
                    "field_mapping": {"title": "主题", "state": "状态", "description": "内容描述"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "description": "user_binding"},
                    "parser_profile": {},
                }
            },
        },
    )
    store.bind_alias("独树不成林", graph_id="graph-podcast", profile_id="profile-podcast", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "主题：算法时代\n状态：进行中\n内容描述：讨论算法与公共生活。\n参与人员：大卫翁、仲树",
                "target_hint": "独树不成林",
                "state": "进行中",
                "content_type_hint": "podcast_episode",
            }
        ),
        store,
    )

    assert plan.normalized_record["参与人员"] == "大卫翁、仲树"
    assert plan.field_mapping["参与人员"] == "参与人员"
    assert plan.summary["writable_fields"]["参与人员"] == {
        "target_field": "参与人员",
        "value_status": "present",
        "write_status": "planned",
    }
    assert "unmapped_writable_schema_field:参与人员" not in plan.warnings
    assert "unmapped_writable_fields" not in plan.summary
    assert plan.summary["warning_details"] == []
    assert build_plan_cli_summary(plan)["warning_sections"] == {
        "blocking": [],
        "unwritten_fields": [],
        "review": [],
        "notices": [],
    }



def test_v2_plan_reports_unmapped_writable_schema_fields_as_unwritten_only(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.write_graph(
        "graph-podcast",
        {
            "cache_version": 2,
            "graph_id": "graph-podcast",
            "root": {"kind": "data_source", "id": "ds-podcast"},
            "data_sources": {
                "ds-podcast": {
                    "data_source_id": "ds-podcast",
                    "schema": {
                        "主题": {"id": "title", "type": "title", "name": "主题"},
                        "状态": {"id": "state", "type": "select", "name": "状态", "select": {"options": [{"name": "进行中"}]}},
                        "内容描述": {"id": "desc", "type": "rich_text", "name": "内容描述"},
                        "参与人员": {"id": "people", "type": "rich_text", "name": "参与人员"},
                    },
                }
            },
            "views": {},
        },
    )
    store.write_profile(
        "profile-podcast",
        {
            "cache_version": 2,
            "profile_id": "profile-podcast",
            "graph_id": "graph-podcast",
            "write_profiles": {
                "podcast_episode": {
                    "canonical_data_source_id": "ds-podcast",
                    "field_mapping": {"title": "主题", "state": "状态", "description": "内容描述"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "description": "user_binding"},
                    "parser_profile": {},
                }
            },
        },
    )
    store.bind_alias("独树不成林", graph_id="graph-podcast", profile_id="profile-podcast", kind="write_profile")

    plan = build_capture_plan(
        CaptureInput.from_dict(
            {
                "raw_input": "主题：算法时代\n状态：进行中\n内容描述：讨论算法与公共生活。",
                "target_hint": "独树不成林",
                "state": "进行中",
                "content_type_hint": "podcast_episode",
            }
        ),
        store,
    )

    assert "unmapped_writable_schema_field:参与人员" not in plan.warnings
    assert plan.summary["warnings"] == []
    assert plan.summary["warning_details"] == []
    assert plan.summary["unmapped_writable_fields"] == {
        "参与人员": {
            "type": "rich_text",
            "value_status": "missing_value",
            "write_status": "omitted_unmapped",
        }
    }
    cli_summary = build_plan_cli_summary(plan)
    assert cli_summary["warnings"] == []
    assert cli_summary["warning_sections"] == {
        "blocking": [],
        "unwritten_fields": [
            {
                "field": "参与人员",
                "type": "rich_text",
                "value_status": "missing_value",
                "write_status": "omitted_unmapped",
            }
        ],
        "review": [],
        "notices": [],
    }
