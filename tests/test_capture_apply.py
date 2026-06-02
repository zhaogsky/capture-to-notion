from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from capture_to_notion import assets, cli, verifier
from capture_to_notion.blocks import build_body_blocks
from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
from capture_to_notion.models import WritePlan
from capture_to_notion.verifier import verify_capture_page, verify_plain_page


def test_write_plan_from_dict_serializes_legacy_plan_with_default_summary():
    data = {
        "plan_id": "plan-1",
        "content_type": "book",
        "target": {
            "page_title": "书单",
            "page_id": "page-books",
            "data_source_id": "ds-books",
            "confidence": "high",
            "source": "cache",
        },
        "normalized_record": {"title": "可能性的艺术", "state": "initialized"},
        "field_mapping": {"title": "书名", "state": "阅读状态"},
        "operations": [{"type": "create_page", "data_source_id": "ds-books"}],
        "asset_operations": [
            {
                "type": "cover",
                "source_url": "https://example.com/cover.jpg",
                "local_cache_path": "/tmp/cover.jpg",
                "target_field": "封面",
                "action": "download_and_attach",
                "status": "planned",
                "warning": None,
            }
        ],
        "sources": [{"type": "user_input", "value": "把《可能性的艺术》初始化到书单"}],
        "warnings": ["field_mapping_ambiguous"],
        "requires_confirmation": True,
        "confirmation_reason": "field_mapping_ambiguous",
    }

    plan = WritePlan.from_dict(data)

    assert plan.to_dict() == {**data, "summary": {}}


def test_completion_operation_for_result_prefers_operation_id_over_source_key():
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-1",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"author": "author-page"},
            "field_mapping": {"title": "书名"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "completion_operations": [
                {
                    "operation_id": "completion:0",
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "target_data_source_id": "ds-authors",
                    "field_mapping": {"bio": "Bio"},
                    "record": {"bio": "first"},
                    "asset_operations": [],
                },
                {
                    "operation_id": "completion:1",
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "target_data_source_id": "ds-authors",
                    "field_mapping": {"country": "Country"},
                    "record": {"country": "second"},
                    "asset_operations": [],
                },
            ],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    operation = cli._completion_operation_for_result(
        plan,
        {"operation_id": "completion:1", "type": "complete_relation_page", "source_record_key": "author"},
    )

    assert operation == plan.completion_operations[1]



def test_write_plan_from_dict_exposes_normalized_plan_operations_without_changing_legacy_json():
    data = {
        "plan_id": "plan-1",
        "content_type": "book",
        "target": {
            "page_title": "书单",
            "page_id": "page-books",
            "data_source_id": "ds-books",
            "confidence": "high",
            "source": "cache",
        },
        "normalized_record": {"title": "可能性的艺术"},
        "field_mapping": {"title": "书名"},
        "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
        "asset_operations": [
            {
                "type": "file",
                "source_url": "https://example.com/cover.jpg",
                "local_cache_path": None,
                "target_field": "封面",
                "action": "attach_external_url",
                "record_key": "cover",
                "status": "planned",
                "warning": None,
            }
        ],
        "completion_operations": [
            {"type": "complete_relation_page", "source_record_key": "speaker"}
        ],
        "sources": [],
        "warnings": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
    }

    plan = WritePlan.from_dict(data)

    assert plan.plan_operations == [
        {"operation_id": "operation:0", "type": "create_or_update_page", "data_source_id": "ds-books"},
        {
            "operation_id": "asset:0",
            "type": "asset_operation",
            "asset_operation": {
                "type": "file",
                "source_url": "https://example.com/cover.jpg",
                "local_cache_path": None,
                "target_field": "封面",
                "action": "attach_external_url",
                "record_key": "cover",
                "status": "planned",
                "warning": None,
            },
        },
        {"operation_id": "completion:0", "type": "complete_relation_page", "source_record_key": "speaker"},
    ]
    assert "plan_operations" not in plan.to_dict()


def test_target_structure_for_data_source_finds_matching_cached_target(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "z-other.json",
        {
            "target": {"page_id": "page-other", "title": "其他"},
            "data_sources": {
                "other": {"data_source_id": "ds-other", "title": "Other"},
            },
        },
    )
    expected = {
        "target": {"page_id": "page-books", "title": "书单"},
        "data_sources": {
            "books": {"data_source_id": "ds-books", "title": "Books"},
        },
    }
    cache.write_json(config.targets_dir / "a-books.json", expected)

    assert cache.target_structure_for_data_source("ds-books") == expected


def test_v2_target_structure_from_profile_preserves_relation_mapping():
    structure = cli._target_structure_from_v2_graph(
        {
            "root": {"kind": "data_source", "id": "ds-books"},
            "data_sources": {"ds-books": {"data_source_id": "ds-books", "schema": {}}},
        },
        "graph-books",
        {
            "data_source_id": "ds-books",
            "relation_mapping": {"author": {"create_missing": True}},
        },
    )

    assert structure["relation_mapping"] == {"author": {"create_missing": True}}



def test_v2_target_structure_from_profile_preserves_graph_for_relation_target_schemas():
    graph = {
        "root": {"kind": "data_source", "id": "ds-books"},
        "data_sources": {
            "ds-books": {"data_source_id": "ds-books", "schema": {"书名": {"type": "title"}}},
            "ds-authors": {"data_source_id": "ds-authors", "schema": {"国籍": {"type": "select"}}},
        },
    }

    structure = cli._target_structure_from_v2_graph(
        graph,
        "graph-books",
        {
            "data_source_id": "ds-books",
            "relation_mapping": {"author": {"create_missing": True}},
        },
    )

    assert structure["graph"] is graph
    assert structure["graph"]["data_sources"]["ds-authors"]["schema"] == {"国籍": {"type": "select"}}



def test_target_structure_for_data_source_returns_none_when_missing_or_falsy(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {"data_source_id": "ds-books", "title": "Books"},
            },
        },
    )

    assert cache.target_structure_for_data_source(None) is None
    assert cache.target_structure_for_data_source("") is None
    assert cache.target_structure_for_data_source("ds-missing") is None


ALLOWED_PREFLIGHT_WORKFLOW = {
    "planning": {
        "status": "allowed",
        "next_action": "capture_plan",
        "reason": "direct_plan_allowed",
    }
}


def write_plan_file(path: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "plan_id": "plan-apply-1",
        "content_type": "book",
        "target": {
            "page_title": "书单",
            "page_id": "page-books",
            "data_source_id": "ds-books",
            "confidence": "high",
            "source": "v2_profile",
            "target_id": "graph-books",
        },
        "normalized_record": {"title": "可能性的艺术", "state": "initialized"},
        "field_mapping": {"title": "书名", "state": "阅读状态"},
        "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
        "asset_operations": [],
        "sources": [{"type": "user_input", "value": "把《可能性的艺术》初始化到书单"}],
        "warnings": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
        "preflight_workflow": ALLOWED_PREFLIGHT_WORKFLOW,
    }
    if overrides:
        target_override = overrides.get("target")
        if isinstance(target_override, dict):
            overrides = dict(overrides)
            overrides["target"] = {**data["target"], **target_override}
        data.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def seed_target_cache(config) -> None:
    CacheStore(config).write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                },
            },
        },
    )
    seed_v2_graph(config)


def seed_v2_graph(
    config,
    *,
    graph_id: str = "graph-books",
    page_id: str = "page-books",
    data_source_id: str = "ds-books",
    title: str = "书单",
    data_source_title: str = "Books",
    schema: dict[str, dict[str, Any]] | None = None,
    view_id: str = "view-books",
    view_data_source_id: str | None = None,
) -> None:
    CacheV2Store(config).write_graph(
        graph_id,
        {
            "cache_version": 2,
            "graph_id": graph_id,
            "root": {"kind": "page", "id": page_id},
            "pages": {page_id: {"page_id": page_id, "title": title}},
            "data_sources": {
                data_source_id: {
                    "data_source_id": data_source_id,
                    "title": data_source_title,
                    "schema": schema
                    or {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "views": {
                view_id: {
                    "view_id": view_id,
                    "name": "Books Gallery",
                    "type": "gallery",
                    "data_source_id": view_data_source_id or data_source_id,
                }
            },
        },
    )


class AdapterFactoryProbe:
    def __init__(self, adapter: Any | None = None) -> None:
        self.called = False
        self.adapter = adapter

    def __call__(self, config):
        self.called = True
        return self.adapter


class FakeAdapter:
    def __init__(self, page: dict[str, Any] | None = None, pages: dict[str, dict[str, Any]] | None = None) -> None:
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.retrieved_pages: list[str] = []
        self.page = page
        self.pages = pages or {}

    def create_page(
        self,
        data_source_id: str,
        properties: dict[str, Any],
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        self.created.append((data_source_id, properties))
        return {"id": "page-created", "url": "https://notion.example/page-created"}

    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("unexpected update_page call")

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        self.retrieved_pages.append(page_id)
        if page_id in self.pages:
            return self.pages[page_id]
        if self.page is None:
            raise AssertionError("unexpected retrieve_page call")
        if self.page.get("id") in (None, page_id):
            return self.page
        raise cli.NotionNotFoundError(f"page not found: {page_id}")


def allow_verify_url_checks(monkeypatch) -> None:
    monkeypatch.setattr(cli, "url_is_accessible", lambda url: True)


BOOK_FIELD_MAPPING = {
    "title": "书名",
    "state": "阅读状态",
    "isbn": "ISBN",
    "page_count": "页数",
    "author": "作者",
    "cover": "封面",
}
BOOK_SCHEMA = {
    "书名": {"name": "书名", "type": "title"},
    "阅读状态": {"name": "阅读状态", "type": "status"},
    "ISBN": {"name": "ISBN", "type": "rich_text"},
    "页数": {"name": "页数", "type": "number"},
    "作者": {"name": "作者", "type": "relation"},
    "封面": {"name": "封面", "type": "files"},
}
BOOK_CHECKS = {
    "title": {"property_type": "title"},
    "state": {"property_type": "status"},
    "isbn": {"property_type": "rich_text"},
    "page_count": {"property_type": "number"},
    "author": {"property_type": "relation"},
    "cover": {"property_type": "files", "check_urls": True},
}


def verify_book_page(fake_adapter: FakeAdapter, url_checker=lambda url: True) -> dict[str, Any]:
    return verify_capture_page(
        "page-book-1",
        fake_adapter,
        url_checker=url_checker,
        field_mapping=BOOK_FIELD_MAPPING,
        schema=BOOK_SCHEMA,
        checks=BOOK_CHECKS,
    )


def test_verify_plain_page_checks_title_and_body_blocks():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Why it matters"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Long-context Agent work gets cheaper."}]}},
            ]

    result = verify_plain_page(
        "page-created",
        Adapter(),
        expected_title="DeepSeek V4",
        expected_block_count=2,
        expected_text_samples=["Why it matters", "Long-context Agent work gets cheaper."],
    )

    assert result == {
        "page_id": "page-created",
        "verified": True,
        "checks": {
            "page": {"status": "present"},
            "title": {"status": "present"},
            "body_blocks": {"status": "present", "count": 2},
            "body_text_samples": {"status": "present"},
        },
        "warnings": [],
    }

def test_verify_plain_page_exact_block_count_rejects_extra_blocks():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "First"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Second"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Duplicate"}]}},
            ]

    result = verify_plain_page(
        "page-created",
        Adapter(),
        expected_title="DeepSeek V4",
        expected_block_count=2,
        block_count_mode="exact",
    )

    assert result["verified"] is False
    assert result["checks"]["body_blocks"] == {
        "status": "mismatch",
        "count": 3,
        "expected_count": 2,
        "mode": "exact",
    }
    assert "body_blocks_count_mismatch" in result["warnings"]


def test_verify_plain_page_at_least_block_count_allows_extra_blocks():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "First"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Second"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Existing content"}]}},
            ]

    result = verify_plain_page(
        "page-created",
        Adapter(),
        expected_title="DeepSeek V4",
        expected_block_count=2,
        block_count_mode="at_least",
    )

    assert result["verified"] is True
    assert result["checks"]["body_blocks"] == {"status": "present", "count": 3}
    assert result["warnings"] == []


def test_verify_plain_page_rejects_non_page_objects():
    class Adapter:
        def retrieve_page(self, page_id):
            return {"id": page_id, "object": "database", "properties": {}}

        def list_block_children(self, page_id):
            return [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Not a page."}]}}
            ]

    result = verify_plain_page("not-a-page", Adapter(), expected_title="DeepSeek V4")

    assert result["verified"] is False
    assert result["checks"]["page"]["status"] != "present"
    assert "page_object_mismatch" in result["warnings"]



def test_apply_verification_uses_plain_page_checks_for_child_page_results():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Why it matters"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Long-context Agent work gets cheaper."}]}},
            ]

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "DeepSeek V4", "body_block_count": 2},
            "normalized_record": {"title": "DeepSeek V4"},
            "field_mapping": {},
            "operations": [
                {
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "DeepSeek V4",
                }
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_child_page", "page_id": "page-created"}]},
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification == {
        "verified": True,
        "pages": [
            {
                "page_id": "page-created",
                "verified": True,
                "checks": {
                    "page": {"status": "present"},
                    "title": {"status": "present"},
                    "body_blocks": {"status": "present", "count": 2},
                    "body_text_samples": {"status": "present"},
                },
                "warnings": [],
            }
        ],
        "warnings": [],
    }


def test_apply_verification_checks_page_cover_when_cover_image_is_planned(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-cover-verify",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"title": "可能性的艺术", "cover": "https://example.com/cover.jpg"},
            "field_mapping": {"title": "书名", "cover": "封面"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [
                {
                    "type": "cover_image",
                    "source_url": "https://example.com/cover.jpg",
                    "local_cache_path": None,
                    "target_field": "封面",
                    "action": "attach_external_url",
                    "record_key": "cover",
                    "status": "planned",
                    "warning": None,
                }
            ],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )
    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_or_update_page", "action": "create_page", "page_id": "page-created"}]},
        FakeAdapter(
            {
                "id": "page-created",
                "object": "page",
                "cover": {"type": "external", "external": {"url": "https://example.com/cover.jpg"}},
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                    "封面": {"type": "files", "files": [{"type": "external", "external": {"url": "https://example.com/cover.jpg"}}]},
                },
            }
        ),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"书名": {"type": "title"}, "封面": {"type": "files"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["page_cover"] == {"status": "present"}



def test_apply_verification_reports_mismatched_page_cover_url(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-cover-verify",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"title": "可能性的艺术", "cover": "https://example.com/expected-cover.jpg"},
            "field_mapping": {"title": "书名", "cover": "封面"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [
                {
                    "type": "cover_image",
                    "source_url": "https://example.com/expected-cover.jpg",
                    "local_cache_path": None,
                    "target_field": "封面",
                    "action": "attach_external_url",
                    "record_key": "cover",
                    "status": "planned",
                    "warning": None,
                }
            ],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_or_update_page", "action": "create_page", "page_id": "page-created"}]},
        FakeAdapter(
            {
                "id": "page-created",
                "object": "page",
                "cover": {"type": "external", "external": {"url": "https://example.com/actual-cover.jpg"}},
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                    "封面": {"type": "files", "files": [{"type": "external", "external": {"url": "https://example.com/expected-cover.jpg"}}]},
                },
            }
        ),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"书名": {"type": "title"}, "封面": {"type": "files"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["page_cover"] == {
        "status": "mismatch",
        "expected_url": "https://example.com/expected-cover.jpg",
        "actual_url": "https://example.com/actual-cover.jpg",
    }
    assert "mismatch:page_cover" in verification["warnings"]



def test_apply_verification_matches_numeric_string_expected_value():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"页数": {"type": "number", "number": 266}},
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {"title": "反乌合之众"},
            "normalized_record": {"page_count": "266"},
            "field_mapping": {"page_count": "页数"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"页数": {"type": "number"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["page_count"] == {"status": "present", "property": "页数"}



def test_verify_capture_page_checks_formula_present():
    result = verify_capture_page(
        "page-book",
        FakeAdapter(
            {
                "id": "page-book",
                "object": "page",
                "properties": {
                    "Computed Status": {"type": "formula", "formula": {"type": "string", "string": "Ready"}},
                },
            }
        ),
        field_mapping={"computed_status": "Computed Status"},
        schema={"Computed Status": {"type": "formula"}},
        checks={"computed_status": {"property_type": "formula"}},
        include_page_cover=False,
    )

    assert result["verified"] is True
    assert result["checks"]["computed_status"] == {"status": "present", "property": "Computed Status"}



def test_verify_capture_page_reports_formula_expected_value_mismatch():
    result = verify_capture_page(
        "page-book",
        FakeAdapter(
            {
                "id": "page-book",
                "object": "page",
                "properties": {
                    "Computed Status": {"type": "formula", "formula": {"type": "string", "string": "Actual"}},
                },
            }
        ),
        field_mapping={"computed_status": "Computed Status"},
        schema={"Computed Status": {"type": "formula"}},
        checks={"computed_status": {"property_type": "formula", "expected_value": "Expected"}},
        include_page_cover=False,
    )

    assert result["verified"] is False
    assert result["checks"]["computed_status"] == {
        "status": "mismatch",
        "property": "Computed Status",
        "expected_value": "Expected",
        "actual_value": "Actual",
    }
    assert "mismatch:computed_status" in result["warnings"]



def test_verify_capture_page_matches_rollup_number_expected_value():
    result = verify_capture_page(
        "page-book",
        FakeAdapter(
            {
                "id": "page-book",
                "object": "page",
                "properties": {
                    "Related Count": {"type": "rollup", "rollup": {"type": "number", "number": 3}},
                },
            }
        ),
        field_mapping={"related_count": "Related Count"},
        schema={"Related Count": {"type": "rollup"}},
        checks={"related_count": {"property_type": "rollup", "expected_value": 3}},
        include_page_cover=False,
    )

    assert result["verified"] is True
    assert result["checks"]["related_count"] == {"status": "present", "property": "Related Count"}



def test_verify_capture_page_treats_empty_rollup_number_as_missing():
    result = verify_capture_page(
        "page-book",
        FakeAdapter(
            {
                "id": "page-book",
                "object": "page",
                "properties": {
                    "Related Count": {"type": "rollup", "rollup": {"type": "number", "number": None}},
                },
            }
        ),
        field_mapping={"related_count": "Related Count"},
        schema={"Related Count": {"type": "rollup"}},
        checks={"related_count": {"property_type": "rollup"}},
        include_page_cover=False,
    )

    assert result["verified"] is False
    assert result["checks"]["related_count"] == {"status": "missing", "property": "Related Count"}
    assert "missing:related_count" in result["warnings"]



def test_apply_verification_checks_computed_fields_from_write_targets():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "自动状态": {"type": "formula", "formula": {"type": "string", "string": "Ready"}},
                },
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {
                "title": "反乌合之众",
                "write_targets": [
                    {"type": "primary_page", "computed_fields": ["自动状态"], "page_id_status": "pending_after_apply"}
                ],
            },
            "normalized_record": {"title": "反乌合之众"},
            "field_mapping": {"title": "名称"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "自动状态": {"type": "formula"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["自动状态"] == {"status": "present", "property": "自动状态"}



def test_apply_verification_uses_expected_value_for_computed_field_expectation():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "自动状态": {"type": "formula", "formula": {"type": "string", "string": "Ready"}},
                },
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {
                "title": "反乌合之众",
                "verification_expectations": {
                    "computed_fields": [{"field": "自动状态", "expected_value": "Ready"}]
                },
            },
            "normalized_record": {"title": "反乌合之众"},
            "field_mapping": {"title": "名称"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "自动状态": {"type": "formula"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["自动状态"] == {"status": "present", "property": "自动状态"}



def test_apply_verification_checks_required_value_field_when_record_value_is_missing():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]}},
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {"title": "反乌合之众", "required_value_fields": ["isbn"]},
            "normalized_record": {"title": "反乌合之众"},
            "field_mapping": {"title": "名称", "isbn": "ISBN"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"名称": {"type": "title"}, "ISBN": {"type": "rich_text"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["isbn"] == {"status": "missing", "property": "ISBN"}
    assert "missing:isbn" in verification["warnings"]



def test_apply_verification_checks_expected_relation_when_resolution_failed():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "关系测试"}]},
                    "Author Page": {"type": "relation", "relation": []},
                },
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {
                "title": "关系测试",
                "verification_expectations": {
                    "relations": ["author_relation"],
                    "targets": [{"target_type": "primary_page", "relations": ["author_relation"]}],
                },
            },
            "normalized_record": {"title": "关系测试", "author_relation": None},
            "field_mapping": {"title": "名称", "author_relation": "Author Page"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": ["relation_resolution_pending:author_relation:CTN Test Author"],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [{"type": "create_or_update_page", "page_id": "page-book"}],
            "resolved_record": {"title": "关系测试", "author_relation": None},
            "warnings": ["relation_unresolved:author_relation:CTN Test Author"],
        },
        Adapter(),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"名称": {"type": "title"}, "Author Page": {"type": "relation"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["author_relation"] == {"status": "missing", "property": "Author Page"}
    assert "missing:author_relation" in verification["warnings"]



def test_apply_verification_checks_relation_actions_when_full_plan_has_no_review_expectations():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "关系测试"}]},
                    "Author Page": {"type": "relation", "relation": []},
                },
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {
                "title": "关系测试",
                "relation_actions": [
                    {"record_key": "author_relation", "target_field": "Author Page", "action": "resolve_relation_page"}
                ],
            },
            "normalized_record": {"title": "关系测试", "author_relation": "CTN Test Author"},
            "field_mapping": {"title": "名称", "author_relation": "Author Page"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": ["relation_resolution_pending:author_relation:CTN Test Author"],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [{"type": "create_or_update_page", "page_id": "page-book"}],
            "resolved_record": {"title": "关系测试", "author_relation": None},
            "warnings": ["relation_unresolved:author_relation:CTN Test Author"],
        },
        Adapter(),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"名称": {"type": "title"}, "Author Page": {"type": "relation"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["author_relation"] == {"status": "missing", "property": "Author Page"}
    assert "missing:author_relation" in verification["warnings"]



def test_apply_verification_accepts_multi_select_expected_scalar_value():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "CTN E2E Test Book"}]},
                    "State": {"type": "multi_select", "multi_select": [{"name": "initialized"}]},
                },
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {"page_title": "书单", "page_id": "page-books", "data_source_id": "ds-books", "confidence": "high", "source": "v2_profile"},
            "summary": {"title": "CTN E2E Test Book", "state": "initialized"},
            "normalized_record": {"title": "CTN E2E Test Book", "state": "initialized"},
            "field_mapping": {"title": "名称", "state": "State"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_or_update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "State": {"type": "multi_select"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["state"] == {"status": "present", "property": "State"}



def test_apply_verification_reports_status_value_mismatch():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "状态": {"type": "status", "status": {"name": "Reading"}},
                },
            }

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {"title": "反乌合之众", "state": "Next"},
            "normalized_record": {"title": "反乌合之众", "state": "Next"},
            "field_mapping": {"title": "名称", "state": "状态"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"名称": {"type": "title"}, "状态": {"type": "status"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["state"] == {
        "status": "mismatch",
        "property": "状态",
        "expected_value": "Next",
        "actual_value": "Reading",
    }
    assert "mismatch:state" in verification["warnings"]



def test_apply_verification_reports_view_visibility_satisfied_for_primary_page():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "状态": {"type": "status", "status": {"name": "Next"}},
                },
            }

    constraints = {"values": {"状态": "Next"}, "warnings": [], "unsupported": [], "conflicts": []}
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "view_id": "view-next",
                "view_name": "Next",
                "view_type": "list",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {"title": "反乌合之众", "view_context": {"view_id": "view-next", "view_name": "Next", "view_type": "list", "constraints": constraints}},
            "normalized_record": {"title": "反乌合之众", "state": "Next"},
            "field_mapping": {"title": "名称", "state": "状态"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "状态": {"type": "status"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["view_visibility"] == {
        "status": "satisfied",
        "view_id": "view-next",
        "view_name": "Next",
        "view_type": "list",
        "constraints": [{"field": "状态", "expected_value": "Next", "actual_value": "Next", "status": "satisfied"}],
        "warnings": [],
    }



def test_apply_verification_reports_view_visibility_satisfied_for_multi_select_contains():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "标签": {"type": "multi_select", "multi_select": [{"name": "社会"}, {"name": "心理"}]},
                },
            }

    constraints = {"values": {"标签": "社会"}, "warnings": [], "unsupported": [], "conflicts": []}
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "view_id": "view-social",
                "view_name": "社会",
                "view_type": "list",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {"title": "反乌合之众", "view_context": {"view_id": "view-social", "view_name": "社会", "view_type": "list", "constraints": constraints}},
            "normalized_record": {"title": "反乌合之众", "tags": ["社会", "心理"]},
            "field_mapping": {"title": "名称", "tags": "标签"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "标签": {"type": "multi_select"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["view_visibility"] == {
        "status": "satisfied",
        "view_id": "view-social",
        "view_name": "社会",
        "view_type": "list",
        "constraints": [{"field": "标签", "expected_value": "社会", "actual_value": ["社会", "心理"], "status": "satisfied"}],
        "warnings": [],
    }



def test_apply_verification_reports_view_visibility_failed_for_primary_page():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "状态": {"type": "status", "status": {"name": "Reading"}},
                },
            }

    constraints = {"values": {"状态": "Next"}, "warnings": [], "unsupported": [], "conflicts": []}
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {"page_title": "书单", "page_id": "page-books", "data_source_id": "ds-books", "view_id": "view-next", "view_name": "Next", "view_type": "list", "confidence": "high", "source": "v2_profile"},
            "summary": {"title": "反乌合之众", "view_context": {"view_id": "view-next", "view_name": "Next", "view_type": "list", "constraints": constraints}},
            "normalized_record": {"title": "反乌合之众", "state": "Next"},
            "field_mapping": {"title": "名称", "state": "状态"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "状态": {"type": "status"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["view_visibility"] == {
        "status": "failed",
        "view_id": "view-next",
        "view_name": "Next",
        "view_type": "list",
        "constraints": [{"field": "状态", "expected_value": "Next", "actual_value": "Reading", "status": "failed"}],
        "warnings": [],
    }
    assert "failed:view_visibility" in verification["warnings"]



def test_apply_verification_reports_view_visibility_not_guaranteed_for_unsupported_rules():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "状态": {"type": "status", "status": {"name": "Next"}},
                },
            }

    constraints = {
        "values": {"状态": "Next"},
        "warnings": ["view_constraint_unsupported:分类"],
        "unsupported": ["view_constraint_unsupported:分类"],
        "conflicts": [],
    }
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {"page_title": "书单", "page_id": "page-books", "data_source_id": "ds-books", "view_id": "view-next", "view_name": "Next", "view_type": "list", "confidence": "high", "source": "v2_profile"},
            "summary": {"title": "反乌合之众", "view_context": {"view_id": "view-next", "view_name": "Next", "view_type": "list", "constraints": constraints}},
            "normalized_record": {"title": "反乌合之众", "state": "Next"},
            "field_mapping": {"title": "名称", "state": "状态"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "update_page", "page_id": "page-book"}]},
        Adapter(),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "状态": {"type": "status"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["view_visibility"] == {
        "status": "not_guaranteed",
        "view_id": "view-next",
        "view_name": "Next",
        "view_type": "list",
        "constraints": [{"field": "状态", "expected_value": "Next", "actual_value": "Next", "status": "satisfied"}],
        "warnings": ["view_constraint_unsupported:分类"],
    }
    assert "not_guaranteed:view_visibility" in verification["warnings"]



def test_apply_verification_uses_exact_block_count_for_child_page_results():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "First"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Second"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Duplicated append"}]}},
            ]

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "DeepSeek V4", "body_block_count": 2},
            "normalized_record": {"title": "DeepSeek V4"},
            "field_mapping": {},
            "operations": [
                {
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "DeepSeek V4",
                    "body_blocks": [
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "First"}]}},
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Second"}]}},
                    ],
                }
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_child_page", "page_id": "page-created"}]},
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["body_blocks"] == {
        "status": "mismatch",
        "count": 3,
        "expected_count": 2,
        "mode": "exact",
    }
    assert "body_blocks_count_mismatch" in verification["pages"][0]["warnings"]
    assert "body_blocks_count_mismatch" in verification["warnings"]


def test_apply_verification_passes_body_block_text_samples_for_child_pages():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Different heading"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Different body."}]}},
            ]

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "DeepSeek V4", "body_block_count": 2},
            "normalized_record": {"title": "DeepSeek V4"},
            "field_mapping": {},
            "operations": [
                {
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "DeepSeek V4",
                    "body_blocks": [
                        {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Why it matters"}]}},
                        {
                            "type": "paragraph",
                            "paragraph": {"rich_text": [{"plain_text": "Long-context Agent work gets cheaper."}]},
                        },
                    ],
                }
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_child_page", "page_id": "page-created"}]},
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["body_text_samples"]["status"] == "missing"
    assert "body_text_samples_missing" in verification["pages"][0]["warnings"]
    assert "body_text_samples_missing" in verification["warnings"]


def test_apply_verification_extracts_write_payload_text_samples_for_child_pages():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Different heading"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Different body."}]}},
            ]

    body_blocks = build_body_blocks(
        "# DeepSeek V4\n\n## Why it matters\n\nLong-context Agent work gets cheaper.",
        title="DeepSeek V4",
    )
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "DeepSeek V4", "body_block_count": len(body_blocks)},
            "normalized_record": {"title": "DeepSeek V4"},
            "field_mapping": {},
            "operations": [
                {
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "DeepSeek V4",
                    "body_blocks": body_blocks,
                }
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_child_page", "page_id": "page-created"}]},
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["body_text_samples"]["status"] == "missing"
    assert "body_text_samples_missing" in verification["pages"][0]["warnings"]
    assert "body_text_samples_missing" in verification["warnings"]



def test_apply_verification_matches_child_page_result_to_operation_id():
    class Adapter:
        def retrieve_page(self, page_id):
            title = "First child" if page_id == "page-a" else "Second child"
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
            }

        def list_block_children(self, page_id):
            text = "Alpha body sample" if page_id == "page-a" else "Beta body sample"
            return [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}
            ]

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "Fallback title"},
            "normalized_record": {"title": "Fallback title"},
            "field_mapping": {},
            "operations": [
                {
                    "operation_id": "create_child_page:0",
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "First child",
                    "body_blocks": [
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Alpha body sample"}]}}
                    ],
                },
                {
                    "operation_id": "create_child_page:1",
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "Second child",
                    "body_blocks": [
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Beta body sample"}]}}
                    ],
                },
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [
                {"type": "create_child_page", "operation_id": "create_child_page:0", "page_id": "page-a"},
                {"type": "create_child_page", "operation_id": "create_child_page:1", "page_id": "page-b"},
            ]
        },
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification is not None
    assert verification["pages"][1]["page_id"] == "page-b"
    assert verification["pages"][1]["checks"]["body_text_samples"]["status"] == "present"



def test_apply_verification_prefers_matched_child_page_operation_expectations_over_summary():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Second child"}]}},
            }

        def list_block_children(self, page_id):
            return [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Second heading"}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Second body"}]}},
            ]

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "First child", "body_block_count": 1},
            "normalized_record": {"title": "First child"},
            "field_mapping": {},
            "operations": [
                {
                    "operation_id": "create_child_page:0",
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "First child",
                    "body_blocks": [
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "First body"}]}}
                    ],
                },
                {
                    "operation_id": "create_child_page:1",
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "Second child",
                    "body_blocks": [
                        {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Second heading"}]}},
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Second body"}]}},
                    ],
                },
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_child_page", "operation_id": "create_child_page:1", "page_id": "page-b"}]},
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["title"] == {"status": "present"}
    assert verification["pages"][0]["checks"]["body_blocks"] == {"status": "present", "count": 2}
    assert verification["warnings"] == []



def test_apply_verification_honors_empty_matched_child_page_body_blocks_over_summary():
    class Adapter:
        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Title only child"}]}},
            }

        def list_block_children(self, page_id):
            return []

    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-page-parent",
            "content_type": "article",
            "target": {
                "page_title": "知识",
                "page_id": "parent-page",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-parent",
                "target_kind": "page_parent",
                "parent_page_id": "parent-page",
            },
            "summary": {"title": "Fallback title", "body_block_count": 3},
            "normalized_record": {"title": "Fallback title"},
            "field_mapping": {},
            "operations": [
                {
                    "operation_id": "create_child_page:0",
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "Body child",
                    "body_blocks": [
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body sample"}]}}
                    ],
                },
                {
                    "operation_id": "create_child_page:1",
                    "type": "create_child_page",
                    "parent_page_id": "parent-page",
                    "title": "Title only child",
                    "body_blocks": [],
                },
            ],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_child_page", "operation_id": "create_child_page:1", "page_id": "page-b"}]},
        Adapter(),
        plan,
        {"target": {"page_id": "parent-page"}, "data_sources": {}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["title"] == {"status": "present"}
    assert verification["pages"][0]["checks"]["body_blocks"] == {"status": "present", "count": 0}
    assert verification["warnings"] == []



def test_capture_apply_requires_confirmation_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {"requires_confirmation": True, "confirmation_reason": "field_mapping_ambiguous"},
    )
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "--confirmed" in stderr
    assert "field_mapping_ambiguous" in stderr
    assert adapter_factory.called is False


def test_capture_apply_requires_confirmed_for_all_writes_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "--confirmed" in stderr
    assert adapter_factory.called is False


def test_capture_apply_rejects_plan_without_allowed_workflow_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path, {"preflight_workflow": {"planning": {"next_action": "scan_target", "reason": "schema_stale"}}})
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "next_action=scan_target" in stderr
    assert adapter_factory.called is False

def test_capture_apply_rejects_empty_operations_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path, {"operations": []})
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "没有可执行操作" in stderr
    assert "capture plan" in stderr
    assert adapter_factory.called is False


def page_with_title(
    page_id: str,
    title: str,
    *,
    parent_data_source_id: str = "ds-books",
    archived: bool = False,
    in_trash: bool = False,
) -> dict[str, Any]:
    return {
        "id": page_id,
        "object": "page",
        "archived": archived,
        "in_trash": in_trash,
        "parent": {"type": "data_source_id", "data_source_id": parent_data_source_id},
        "properties": {
            "书名": {"type": "title", "title": [{"plain_text": title}]},
            "阅读状态": {"type": "status", "status": {"name": "initialized"}},
        },
    }


class UpdateRecordingAdapter(FakeAdapter):
    def __init__(self, page: dict[str, Any]) -> None:
        super().__init__(page=page)
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[tuple[str, str]] = []

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        self.calls.append(("retrieve_page", page_id))
        return super().retrieve_page(page_id)

    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("update_page", page_id))
        self.updated.append((page_id, properties))
        return {"id": page_id, "url": f"https://notion.example/{page_id}"}


def write_update_plan_file(path: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books", "page_id": "page-book-1"}],
    }
    if overrides:
        data.update(overrides)
    return write_plan_file(path, data)


def test_capture_apply_rejects_archived_update_page_before_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_update_plan_file(plan_path)
    fake_adapter = UpdateRecordingAdapter(page_with_title("page-book-1", "可能性的艺术", archived=True))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "update_page_safety_failed:already_archived" in capsys.readouterr().err
    assert fake_adapter.calls == [("retrieve_page", "page-book-1")]
    assert fake_adapter.updated == []



def test_capture_apply_rejects_in_trash_update_page_before_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_update_plan_file(plan_path)
    fake_adapter = UpdateRecordingAdapter(page_with_title("page-book-1", "可能性的艺术", in_trash=True))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "update_page_safety_failed:already_archived" in capsys.readouterr().err
    assert fake_adapter.calls == [("retrieve_page", "page-book-1")]
    assert fake_adapter.updated == []



def test_capture_apply_rejects_update_page_parent_data_source_mismatch_before_mutation(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_update_plan_file(plan_path)
    fake_adapter = UpdateRecordingAdapter(
        page_with_title("page-book-1", "可能性的艺术", parent_data_source_id="ds-other")
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "update_page_safety_failed:parent_data_source_mismatch" in stderr
    assert "expected='ds-books'" in stderr
    assert "actual='ds-other'" in stderr
    assert fake_adapter.calls == [("retrieve_page", "page-book-1")]
    assert fake_adapter.updated == []


def test_capture_apply_retrieves_update_page_before_allowed_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_update_plan_file(plan_path)
    fake_adapter = UpdateRecordingAdapter(page_with_title("page-book-1", "可能性的艺术"))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    assert capsys.readouterr().err == ""
    assert fake_adapter.calls[0] == ("retrieve_page", "page-book-1")
    assert ("update_page", "page-book-1") in fake_adapter.calls
    assert fake_adapter.calls.index(("retrieve_page", "page-book-1")) < fake_adapter.calls.index(
        ("update_page", "page-book-1")
    )
    assert fake_adapter.updated[0][0] == "page-book-1"


def test_capture_apply_rejects_update_page_when_explicit_existing_title_mismatches(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_update_plan_file(
        plan_path,
        {
            "summary": {
                "write_targets": [
                    {"type": "primary_page", "page_id": "page-book-1", "existing_title": "可能性的艺术"}
                ]
            }
        },
    )
    fake_adapter = UpdateRecordingAdapter(page_with_title("page-book-1", "另一本书"))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "update_page_safety_failed:title_mismatch" in stderr
    assert "expected='可能性的艺术'" in stderr
    assert "actual='另一本书'" in stderr
    assert fake_adapter.calls == [("retrieve_page", "page-book-1")]
    assert fake_adapter.updated == []



def test_capture_apply_rejects_update_page_when_explicit_current_title_mismatches(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_update_plan_file(
        plan_path,
        {
            "summary": {
                "write_targets": [
                    {"type": "primary_page", "page_id": "page-book-1", "current_title": "可能性的艺术"}
                ]
            }
        },
    )
    fake_adapter = UpdateRecordingAdapter(page_with_title("page-book-1", "另一本书"))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "update_page_safety_failed:title_mismatch" in stderr
    assert "expected='可能性的艺术'" in stderr
    assert "actual='另一本书'" in stderr
    assert fake_adapter.calls == [("retrieve_page", "page-book-1")]
    assert fake_adapter.updated == []


CONTEXT_PREFLIGHT_WORKFLOW = {
    "planning": {
        "status": "allowed",
        "next_action": "capture_plan",
        "reason": "direct_plan_allowed",
    },
    "target_resolution": {
        "status": "cache_hit",
        "target_context_hint": "上下文页",
        "target_context_verified": True,
        "page_id": "page-books",
        "target_id": "books",
        "data_source_id": "ds-books",
    },
}


def test_capture_apply_rejects_context_plan_missing_capture_input_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "preflight_workflow": CONTEXT_PREFLIGHT_WORKFLOW,
            "capture_input": None,
            "summary": {"write_targets": [{"type": "primary_page"}]},
        },
    )
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "context_capture_input_missing" in capsys.readouterr().err
    assert adapter_factory.called is False



def test_capture_apply_rejects_context_plan_missing_write_targets_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "preflight_workflow": CONTEXT_PREFLIGHT_WORKFLOW,
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "target_context_hint": "上下文页",
                "state": "initialized",
                "content_type_hint": "book",
            },
            "summary": {"write_targets": []},
        },
    )
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "context_write_targets_missing" in capsys.readouterr().err
    assert adapter_factory.called is False



def test_v2_apply_integrity_blocks_view_data_source_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_v2_graph(config, view_data_source_id="ds-other")
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
                "target_id": "graph-books",
                "view_id": "view-books",
                "view_name": "Books Gallery",
                "view_type": "gallery",
            },
        },
    )
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "view_data_source_id_mismatch" in capsys.readouterr().err
    assert adapter_factory.called is False



def test_capture_apply_rejects_cached_page_drift_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-drifted", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                },
            },
        },
    )
    seed_v2_graph(config, page_id="page-drifted")
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "target_page_id_mismatch" in capsys.readouterr().err
    assert adapter_factory.called is False



def test_capture_apply_rejects_refreshed_context_failure_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "书单": {"type": "page", "page_id": "page-books", "target_id": "books"},
                "上下文页": {"type": "page", "page_id": "page-other", "target_id": "other"},
            }
        },
    )
    seed_target_cache(config)
    cache.write_json(config.targets_dir / "other.json", {"target": {"page_id": "page-other", "title": "其它页", "target_id": "other"}, "data_sources": {}})
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "preflight_workflow": CONTEXT_PREFLIGHT_WORKFLOW,
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "target_context_hint": "上下文页",
                "state": "initialized",
                "content_type_hint": "book",
            },
            "summary": {"write_targets": [{"type": "primary_page"}]},
        },
    )
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "next_action=scan_target" in stderr
    assert adapter_factory.called is False


def test_capture_apply_uses_plan_target_id_when_data_source_exists_in_multiple_cached_targets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    shared_data_source = {
        "data_source_id": "ds-books",
        "title": "Books",
        "schema": {
            "书名": {"name": "书名", "type": "title"},
            "阅读状态": {"name": "阅读状态", "type": "status"},
        },
    }
    cache.write_json(
        config.targets_dir / "a-container.json",
        {
            "target": {"page_id": "page-container", "title": "父页面", "target_id": "container"},
            "data_sources": {"books": shared_data_source},
        },
    )
    cache.write_json(
        config.targets_dir / "books-ds.json",
        {
            "target": {"page_id": "page-books", "title": "Books", "target_id": "books-ds"},
            "data_sources": {"books": shared_data_source},
        },
    )
    seed_v2_graph(config, graph_id="books-ds", title="Books")
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "target": {
                "page_title": "Books",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "data_source_alias",
                "target_id": "books-ds",
            },
        },
    )
    fake_adapter = FakeAdapter(
        {
            "id": "page-created",
            "object": "page",
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "initialized"}},
            },
        }
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert fake_adapter.created[0][0] == "ds-books"



def test_capture_apply_uses_planned_operations_after_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "operations": [],
            "planned_operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "requires_confirmation": True,
            "confirmation_reason": "field_mapping_missing",
        },
    )
    fake_adapter = FakeAdapter(
        {
            "id": "page-created",
            "object": "page",
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "initialized"}},
            },
        }
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert fake_adapter.created[0][0] == "ds-books"

def test_capture_apply_uses_planned_asset_operations_after_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheStore(config).write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                        "封面": {"name": "封面", "type": "files"},
                    },
                },
            },
        },
    )
    seed_v2_graph(
        config,
        schema={
            "书名": {"name": "书名", "type": "title"},
            "阅读状态": {"name": "阅读状态", "type": "status"},
            "封面": {"name": "封面", "type": "files"},
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {"title": "可能性的艺术", "state": "initialized", "cover": None},
            "field_mapping": {"title": "书名", "state": "阅读状态", "cover": "封面"},
            "operations": [],
            "planned_operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "planned_asset_operations": [
                {
                    "type": "cover_image",
                    "source_url": "https://example.com/cover.jpg",
                    "local_cache_path": None,
                    "target_field": "封面",
                    "action": "attach_external_url",
                    "record_key": "cover",
                    "status": "planned",
                    "warning": None,
                }
            ],
            "requires_confirmation": True,
            "confirmation_reason": "field_mapping_missing",
        },
    )
    fake_adapter = FakeAdapter(
        {
            "id": "page-created",
            "object": "page",
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                "封面": {"type": "files", "files": [{"type": "external", "external": {"url": "https://example.com/cover.jpg"}}]},
            },
        }
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    assert fake_adapter.created[0][1]["封面"]["files"][0]["external"]["url"] == "https://example.com/cover.jpg"



def test_capture_apply_uses_planned_completion_operations_after_confirmation(tmp_path, monkeypatch, capsys):
    class CompletionAdapter(FakeAdapter):
        def __init__(self):
            super().__init__(
                pages={
                    "page-created": {
                        "id": "page-created",
                        "object": "page",
                        "properties": {
                            "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                            "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                        },
                    },
                    "page-related": {
                        "id": "page-related",
                        "object": "page",
                        "properties": {
                            "备注": {"type": "rich_text", "rich_text": [{"plain_text": "已补全"}]},
                        },
                    },
                }
            )
            self.updated: list[tuple[str, dict[str, Any]]] = []

        def update_page(self, page_id: str, properties: dict[str, Any], cover: Any = None, cover_source_url: str | None = None) -> dict[str, Any]:
            self.updated.append((page_id, properties))
            return {"id": page_id, "url": f"https://notion.example/{page_id}"}

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {"title": "可能性的艺术", "state": "initialized", "related": "page-related"},
            "operations": [],
            "planned_operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "completion_operations": [],
            "planned_completion_operations": [
                {
                    "type": "complete_relation_page",
                    "source_record_key": "related",
                    "field_mapping": {"note": "备注"},
                    "record": {"note": "已补全"},
                    "schema": {"备注": {"name": "备注", "type": "rich_text"}},
                    "asset_operations": [],
                }
            ],
            "requires_confirmation": True,
            "confirmation_reason": "field_mapping_missing",
        },
    )
    fake_adapter = CompletionAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["completion_results"][0]["page_id"] == "page-related"
    assert fake_adapter.updated == [("page-related", {"备注": {"rich_text": [{"text": {"content": "已补全"}}]}})]



def test_capture_apply_simulates_people_field_write_with_person_user(tmp_path, monkeypatch, capsys):
    class PeopleAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.pages = {}

        def get_current_user(self) -> dict[str, Any]:
            return {"id": "person-user", "type": "person", "name": "Ada"}

        def create_page(
            self,
            data_source_id: str,
            properties: dict[str, Any],
            cover: Any = None,
            cover_source_url: str | None = None,
        ) -> dict[str, Any]:
            self.created.append((data_source_id, properties))
            self.pages["page-created"] = {
                "id": "page-created",
                "object": "page",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "People simulation"}]},
                    "Reviewers": {"type": "people", "people": properties["Reviewers"]["people"]},
                    "Source": {"type": "rich_text", "rich_text": [{"plain_text": "simulated"}]},
                },
            }
            return {"id": "page-created", "url": "https://notion.example/page-created"}

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_v2_graph(
        config,
        graph_id="graph-people",
        page_id="page-people",
        data_source_id="ds-people",
        title="People Sandbox",
        data_source_title="People",
        schema={
            "Name": {"name": "Name", "type": "title"},
            "Reviewers": {"name": "Reviewers", "type": "people"},
            "Source": {"name": "Source", "type": "rich_text"},
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "content_type": "note",
            "target": {
                "page_title": "People Sandbox",
                "page_id": "page-people",
                "data_source_id": "ds-people",
                "target_id": "graph-people",
            },
            "summary": {
                "verification_expectations": {
                    "fields": ["reviewers"],
                    "targets": [{"target_type": "primary_page", "fields": ["reviewers"]}],
                }
            },
            "normalized_record": {"title": "People simulation", "reviewers": "me", "source": "simulated"},
            "field_mapping": {"title": "Name", "reviewers": "Reviewers", "source": "Source"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-people"}],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    fake_adapter = PeopleAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert fake_adapter.created == [
        (
            "ds-people",
            {
                "Name": {"title": [{"text": {"content": "People simulation"}}]},
                "Reviewers": {"people": [{"id": "person-user"}]},
                "Source": {"rich_text": [{"text": {"content": "simulated"}}]},
            },
        )
    ]
    assert result["verification"]["verified"] is True
    assert result["verification"]["pages"][0]["checks"]["reviewers"] == {"status": "present", "property": "Reviewers"}



def test_capture_apply_creates_plain_child_page_from_plan(tmp_path, monkeypatch, capsys):
    class PlainPageAdapter:
        def __init__(self) -> None:
            self.created_child_pages: list[tuple[str, str, list[dict[str, Any]]]] = []
            self.appended: list[tuple[str, list[dict[str, Any]]]] = []
            self.child_blocks: dict[str, list[dict[str, Any]]] = {}

        def create_child_page(self, parent_page_id: str, title: str, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            blocks = list(children or [])
            self.created_child_pages.append((parent_page_id, title, blocks))
            self.child_blocks["page-created"] = blocks
            return {"id": "page-created", "url": "https://notion.example/page-created"}

        def append_block_children(self, page_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
            blocks = list(children)
            self.appended.append((page_id, blocks))
            self.child_blocks.setdefault(page_id, []).extend(blocks)
            return {"results": blocks}

        def retrieve_page(self, page_id: str) -> dict[str, Any]:
            return {
                "id": page_id,
                "object": "page",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "DeepSeek V4"}]}},
            }

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            notion_blocks = []
            for block in self.child_blocks.get(page_id, []):
                block_type = block.get("type")
                payload = block.get(block_type) if isinstance(block_type, str) else None
                if not isinstance(payload, dict):
                    notion_blocks.append(block)
                    continue
                rich_text = []
                for item in payload.get("rich_text", []):
                    text = item.get("text") if isinstance(item, dict) else None
                    content = text.get("content") if isinstance(text, dict) else None
                    rich_text.append({**item, "plain_text": content} if isinstance(content, str) else item)
                notion_blocks.append({**block, block_type: {**payload, "rich_text": rich_text}})
            return notion_blocks

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheV2Store(config).write_graph(
        "graph-knowledge",
        {
            "cache_version": 2,
            "graph_id": "graph-knowledge",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "知识库"}},
            "data_sources": {},
            "views": {},
        },
    )
    body_blocks = build_body_blocks(
        "# DeepSeek V4\n\n## Why it matters\n\nLong-context Agent work gets cheaper.",
        title="DeepSeek V4",
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "content_type": "article",
            "target": {
                "page_title": "知识库",
                "page_id": "page-knowledge",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_page_graph",
                "target_id": "graph-knowledge",
                "target_kind": "page_parent",
                "parent_page_id": "page-knowledge",
            },
            "summary": {"title": "DeepSeek V4", "body_block_count": len(body_blocks)},
            "normalized_record": {"title": "DeepSeek V4", "state": "initialized"},
            "field_mapping": {},
            "operations": [
                {
                    "operation_id": "create_child_page:0",
                    "type": "create_child_page",
                    "parent_page_id": "page-knowledge",
                    "title": "DeepSeek V4",
                    "body_blocks": body_blocks,
                }
            ],
            "asset_operations": [],
            "preflight_workflow": ALLOWED_PREFLIGHT_WORKFLOW,
        },
    )
    fake_adapter = PlainPageAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["results"][0]["type"] == "create_child_page"
    assert result["results"][0]["page_id"] == "page-created"
    assert result["verification"]["verified"] is True
    assert fake_adapter.created_child_pages == [("page-knowledge", "DeepSeek V4", body_blocks)]



def test_capture_apply_appends_existing_plain_page_from_v2_page_graph(tmp_path, monkeypatch, capsys):
    class PlainPageAdapter:
        def __init__(self) -> None:
            self.appended: list[tuple[str, list[dict[str, Any]]]] = []
            self.child_blocks: dict[str, list[dict[str, Any]]] = {"page-existing": []}

        def append_block_children(self, page_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
            blocks = list(children)
            self.appended.append((page_id, blocks))
            self.child_blocks.setdefault(page_id, []).extend(blocks)
            return {"results": blocks}

        def retrieve_page(self, page_id: str) -> dict[str, Any]:
            return {
                "id": page_id,
                "object": "page",
                "parent": {"type": "page_id", "page_id": "page-knowledge"},
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Existing Note"}]}},
            }

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            return self.child_blocks.get(page_id, [])

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache_v2 = CacheV2Store(config)
    cache_v2.write_graph(
        "graph-knowledge",
        {
            "cache_version": 2,
            "graph_id": "graph-knowledge",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "知识库"}},
            "data_sources": {},
            "views": {},
        },
    )
    cache_v2.bind_alias("knowledge", graph_id="graph-knowledge", profile_id=None, kind="page")
    body_blocks = build_body_blocks("## Update\n\nAdditional context.", title="Existing Note")
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "content_type": "article",
            "target": {
                "page_title": "Existing Note",
                "page_id": "page-existing",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_page_graph",
                "target_id": "graph-knowledge",
                "target_kind": "existing_page",
                "parent_page_id": "page-knowledge",
            },
            "summary": {
                "title": "Existing Note",
                "body_block_count": len(body_blocks),
                "write_targets": [
                    {
                        "type": "primary_page",
                        "action": "append_page_content",
                        "page_id": "page-existing",
                        "parent_page_id": "page-knowledge",
                    }
                ],
            },
            "normalized_record": {"title": "Existing Note", "state": "initialized"},
            "field_mapping": {},
            "operations": [
                {
                    "operation_id": "append_page_content:0",
                    "type": "append_page_content",
                    "page_id": "page-existing",
                    "body_blocks": body_blocks,
                }
            ],
            "asset_operations": [],
            "preflight_workflow": ALLOWED_PREFLIGHT_WORKFLOW,
        },
    )
    fake_adapter = PlainPageAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["results"][0] == {
        "type": "append_page_content",
        "action": "append_page_content",
        "page_id": "page-existing",
    }
    assert fake_adapter.appended == [("page-existing", body_blocks)]



def test_capture_apply_rejects_existing_plain_page_parent_mismatch_before_append(tmp_path, monkeypatch, capsys):
    class PlainPageAdapter:
        def __init__(self) -> None:
            self.appended: list[tuple[str, list[dict[str, Any]]]] = []

        def append_block_children(self, page_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
            blocks = list(children)
            self.appended.append((page_id, blocks))
            return {"results": blocks}

        def retrieve_page(self, page_id: str) -> dict[str, Any]:
            return {
                "id": page_id,
                "object": "page",
                "parent": {"type": "page_id", "page_id": "page-other"},
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Existing Note"}]}},
            }

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache_v2 = CacheV2Store(config)
    cache_v2.write_graph(
        "graph-knowledge",
        {
            "cache_version": 2,
            "graph_id": "graph-knowledge",
            "root": {"kind": "page", "id": "page-knowledge"},
            "pages": {"page-knowledge": {"page_id": "page-knowledge", "title": "知识库"}},
            "data_sources": {},
            "views": {},
        },
    )
    cache_v2.bind_alias("knowledge", graph_id="graph-knowledge", profile_id=None, kind="page")
    body_blocks = build_body_blocks("## Update\n\nAdditional context.", title="Existing Note")
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "content_type": "article",
            "target": {
                "page_title": "Existing Note",
                "page_id": "page-existing",
                "data_source_id": None,
                "confidence": "high",
                "source": "v2_page_graph",
                "target_id": "graph-knowledge",
                "target_kind": "existing_page",
                "parent_page_id": "page-knowledge",
            },
            "summary": {
                "title": "Existing Note",
                "body_block_count": len(body_blocks),
                "write_targets": [
                    {
                        "type": "primary_page",
                        "action": "append_page_content",
                        "page_id": "page-existing",
                        "parent_page_id": "page-knowledge",
                    }
                ],
            },
            "normalized_record": {"title": "Existing Note", "state": "initialized"},
            "field_mapping": {},
            "operations": [
                {
                    "operation_id": "append_page_content:0",
                    "type": "append_page_content",
                    "page_id": "page-existing",
                    "body_blocks": body_blocks,
                }
            ],
            "asset_operations": [],
            "preflight_workflow": ALLOWED_PREFLIGHT_WORKFLOW,
        },
    )
    fake_adapter = PlainPageAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "append_page_safety_failed:parent_page_mismatch" in captured.err
    assert "expected='page-knowledge'" in captured.err
    assert "actual='page-other'" in captured.err
    assert fake_adapter.appended == []



def test_capture_apply_uses_v2_profile_state_mapping(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    cache_v2 = CacheV2Store(config)
    cache_v2.write_profile(
        "profile-books",
        {
            "cache_version": 2,
            "profile_id": "profile-books",
            "graph_id": "graph-books",
            "write_profiles": {
                "book": {
                    "content_type": "book",
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "user", "state": "user"},
                    "state_mapping": {
                        "field": "阅读状态",
                        "values": {"initialized": "未开始", "completed": "完成"},
                    },
                }
            },
        },
    )
    cache_v2.bind_alias("书单", graph_id="graph-books", profile_id="profile-books", kind="write_profile")
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {"title": "可能性的艺术", "state": "completed"},
            "capture_input": {
                "raw_input": "把《可能性的艺术》标记为已读",
                "target_hint": "书单",
                "state": "completed",
                "content_type_hint": "book",
            },
        },
    )
    fake_adapter = FakeAdapter(
        {
            "id": "page-created",
            "object": "page",
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "完成"}},
            },
        }
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    json.loads(capsys.readouterr().out)
    assert fake_adapter.created[0][1]["阅读状态"] == {"status": {"name": "完成"}}


def test_capture_apply_successful_create_uses_fake_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    fake_adapter = FakeAdapter(
        {
            "id": "page-created",
            "object": "page",
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "initialized"}},
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    result = json.loads(stdout)
    assert result["applied"] is True
    assert result["results"][0]["page_id"] == "page-created"
    assert adapter_factory.called is True
    assert fake_adapter.created == [
        (
            "ds-books",
            {
                "书名": {"title": [{"text": {"content": "可能性的艺术"}}]},
                "阅读状态": {"status": {"name": "initialized"}},
            },
        )
    ]
    assert fake_adapter.retrieved_pages == ["page-created"]
    assert result["verification"] == {
        "verified": True,
        "pages": [
            {
                "page_id": "page-created",
                "verified": True,
                "checks": {
                    "page": {"status": "present"},
                    "title": {"status": "present", "property": "书名"},
                    "state": {"status": "present", "property": "阅读状态"},
                },
                "warnings": [],
            }
        ],
        "warnings": [],
    }

def test_capture_apply_reports_verification_not_found_without_recreating(tmp_path, monkeypatch, capsys):
    class VerificationNotFoundAdapter(FakeAdapter):
        def retrieve_page(self, page_id: str) -> dict[str, Any]:
            self.retrieved_pages.append(page_id)
            raise cli.NotionNotFoundError(f"page not found: {page_id}")

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    fake_adapter = VerificationNotFoundAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert len(fake_adapter.created) == 1
    assert fake_adapter.retrieved_pages == ["page-created"]
    assert result["verification"] == {
        "verified": False,
        "pages": [
            {
                "page_id": "page-created",
                "verified": False,
                "checks": {
                    "page": {"status": "missing"},
                    "title": {"status": "missing"},
                    "state": {"status": "missing"},
                },
                "warnings": ["missing:page", "missing:title", "missing:state"],
            }
        ],
        "warnings": ["missing:page", "missing:title", "missing:state"],
    }


def test_stale_cache_detection_uses_structured_notion_error_body() -> None:
    stale = cli.NotionApiError(
        "request failed",
        status=400,
        code="validation_error",
        body={"message": "body failed validation: data_source_id is invalid"},
    )
    non_stale = cli.NotionApiError(
        "body failed validation: data_source_id is mentioned in user content",
        status=400,
        code="validation_error",
        body={"message": "body failed validation: status option is invalid"},
    )

    assert cli._is_stale_cache_error(stale) is True
    assert cli._is_stale_cache_error(non_stale) is False


def test_capture_apply_rescans_replans_and_retries_on_stale_cache_error(tmp_path, monkeypatch, capsys):
    class RecoveringAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(
                pages={
                    "page-books": {"id": "page-books", "title": "书单"},
                    "page-created": {
                        "id": "page-created",
                        "object": "page",
                        "properties": {
                            "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                            "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                        },
                    },
                }
            )
            self.children = {
                "page-books": [
                    {"type": "child_database", "id": "db-new-books", "child_database": {"title": "Books"}}
                ]
            }
            self.databases = {
                "db-new-books": {
                    "id": "db-new-books",
                    "title": "Books",
                    "data_sources": [{"id": "ds-new-books", "name": "Books"}],
                    "properties": {},
                }
            }
            self.data_sources = {
                "ds-new-books": {
                    "id": "ds-new-books",
                    "title": "Books",
                    "properties": {
                        "书名": {"id": "title", "type": "title", "title": {}},
                        "阅读状态": {"id": "state", "type": "status", "status": {"options": []}},
                    },
                }
            }
            self.create_attempts: list[tuple[str, dict[str, Any]]] = []
            self.scanned_pages: list[str] = []

        def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            self.create_attempts.append((data_source_id, properties))
            if data_source_id == "ds-old-books":
                raise cli.NotionApiError(
                    "body failed validation",
                    status=400,
                    code="validation_error",
                    body={"message": "body failed validation: data_source_id is invalid"},
                )
            return {"id": "page-created", "url": "https://notion.example/page-created"}

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            self.scanned_pages.append(page_id)
            return self.children.get(page_id, [])

        def retrieve_database(self, database_id: str) -> dict[str, Any]:
            return self.databases[database_id]

        def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
            return self.data_sources[data_source_id]

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}},
    )
    cache.write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "old": {
                    "data_source_id": "ds-old-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "旧书名", "state": "旧状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "旧书名": {"name": "旧书名", "type": "title"},
                        "旧状态": {"name": "旧状态", "type": "status"},
                    },
                }
            },
            "state_mapping": {"field": "旧状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    seed_v2_graph(
        config,
        data_source_id="ds-old-books",
        schema={
            "旧书名": {"name": "旧书名", "type": "title"},
            "旧状态": {"name": "旧状态", "type": "status"},
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-old-books",
                "confidence": "high",
                "source": "alias_cache",
            },
            "normalized_record": {"title": "可能性的艺术", "state": "initialized"},
            "field_mapping": {"title": "旧书名", "state": "旧状态"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-old-books"}],
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "state": "initialized",
                "content_type_hint": "book",
                "options": {"allow_asset_download": True},
            },
        },
    )
    fake_adapter = RecoveringAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "v2_stale_cache_recovery_requires_fresh_plan" in capsys.readouterr().err
    assert fake_adapter.create_attempts[0][0] == "ds-old-books"
    assert fake_adapter.scanned_pages == []


def test_capture_apply_recovers_stale_data_source_only_target_without_page_id(tmp_path, monkeypatch, capsys):
    class DataSourceOnlyRecoveringAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(
                pages={
                    "page-created": {
                        "id": "page-created",
                        "object": "page",
                        "properties": {
                            "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                            "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                        },
                    }
                }
            )
            self.data_sources = {
                "ds-books": {
                    "id": "ds-books",
                    "title": "Books",
                    "properties": {
                        "书名": {"id": "title", "type": "title", "title": {}},
                        "阅读状态": {"id": "state", "type": "status", "status": {"options": []}},
                    },
                }
            }
            self.create_attempts: list[tuple[str, dict[str, Any]]] = []
            self.retrieved_data_sources: list[str] = []

        def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            self.create_attempts.append((data_source_id, properties))
            if len(self.create_attempts) == 1:
                raise cli.NotionApiError(
                    "body failed validation",
                    status=400,
                    code="validation_error",
                    body={"message": "body failed validation: schema is stale"},
                )
            return {"id": "page-created", "url": "https://notion.example/page-created"}

        def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
            self.retrieved_data_sources.append(data_source_id)
            return self.data_sources[data_source_id]

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {"aliases": {"书库": {"type": "data_source", "data_source_id": "ds-books", "target_id": "books-ds"}}},
    )
    cache.write_json(
        config.targets_dir / "books-ds.json",
        {
            "target": {"page_id": None, "title": "Books", "target_id": "books-ds", "data_source_id": "ds-books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "旧书名", "state": "旧状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "旧书名": {"name": "旧书名", "type": "title"},
                        "旧状态": {"name": "旧状态", "type": "status"},
                    },
                }
            },
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    seed_v2_graph(
        config,
        schema={
            "旧书名": {"name": "旧书名", "type": "title"},
            "旧状态": {"name": "旧状态", "type": "status"},
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "target": {
                "page_title": "Books",
                "page_id": None,
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "data_source_alias",
            },
            "normalized_record": {"title": "可能性的艺术", "state": "initialized"},
            "field_mapping": {"title": "旧书名", "state": "旧状态"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书库",
                "state": "initialized",
                "content_type_hint": "book",
                "options": {"allow_asset_download": True},
            },
        },
    )
    fake_adapter = DataSourceOnlyRecoveringAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "v2_stale_cache_recovery_requires_fresh_plan" in capsys.readouterr().err
    assert fake_adapter.retrieved_data_sources == []
    assert len(fake_adapter.create_attempts) == 1



def test_capture_apply_reports_possible_partial_write_for_ambiguous_create_error(tmp_path, monkeypatch, capsys):
    class AmbiguousCreateAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(pages={"page-books": {"id": "page-books", "title": "书单"}})
            self.create_attempts = 0
            self.scanned_pages: list[str] = []

        def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            self.create_attempts += 1
            raise cli.NotionApiError(
                "server failed after accepting create request",
                status=500,
                code="internal_server_error",
                body={"message": "body failed validation: data_source_id is invalid"},
            )

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            self.scanned_pages.append(page_id)
            return []

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}},
    )
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "state": "initialized",
                "content_type_hint": "book",
                "options": {"allow_asset_download": True},
            },
        },
    )
    fake_adapter = AmbiguousCreateAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "possible_partial_write" in capsys.readouterr().err
    assert fake_adapter.create_attempts == 1
    assert fake_adapter.scanned_pages == []


def test_capture_apply_does_not_recover_update_page_not_found_as_create(tmp_path, monkeypatch, capsys):
    class UpdateNotFoundAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(pages={"page-books": {"id": "page-books", "title": "书单"}})
            self.scanned_pages: list[str] = []

        def retrieve_page(self, page_id: str) -> dict[str, Any]:
            if page_id == "page-missing":
                raise cli.NotionNotFoundError(f"page not found: {page_id}")
            return super().retrieve_page(page_id)

        def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("unexpected update_page call")

        def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("unexpected create_page call")

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            self.scanned_pages.append(page_id)
            return []

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}},
    )
    cache.write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    seed_v2_graph(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books", "page_id": "page-missing"}],
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "state": "initialized",
                "content_type_hint": "book",
                "options": {"allow_asset_download": True},
            },
        },
    )
    fake_adapter = UpdateNotFoundAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "page not found" in capsys.readouterr().err
    assert fake_adapter.created == []
    assert fake_adapter.scanned_pages == []


def test_capture_apply_does_not_recreate_when_later_main_operation_fails(tmp_path, monkeypatch, capsys):
    class LaterOperationFailingAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(
                pages={
                    "page-books": {"id": "page-books", "title": "书单"},
                    "page-created": {
                        "id": "page-created",
                        "object": "page",
                        "properties": {
                            "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                            "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                        },
                    },
                }
            )
            self.children = {
                "page-books": [
                    {"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}
                ]
            }
            self.databases = {
                "db-books": {
                    "id": "db-books",
                    "title": "Books",
                    "data_sources": [{"id": "ds-books", "name": "Books"}],
                    "properties": {},
                }
            }
            self.data_sources = {
                "ds-books": {
                    "id": "ds-books",
                    "title": "Books",
                    "properties": {
                        "书名": {"id": "title", "type": "title", "title": {}},
                        "阅读状态": {"id": "state", "type": "status", "status": {"options": []}},
                    },
                }
            }
            self.scanned_pages: list[str] = []

        def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            self.created.append((data_source_id, properties))
            if len(self.created) == 2:
                raise cli.NotionApiError("body failed validation: data_source_id is invalid")
            return {"id": "page-created", "url": "https://notion.example/page-created"}

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            self.scanned_pages.append(page_id)
            return self.children.get(page_id, [])

        def retrieve_database(self, database_id: str) -> dict[str, Any]:
            return self.databases[database_id]

        def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
            return self.data_sources[data_source_id]

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}},
    )
    cache.write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    seed_v2_graph(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "operations": [
                {"type": "create_or_update_page", "data_source_id": "ds-books"},
                {"type": "create_or_update_page", "data_source_id": "ds-books"},
            ],
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "state": "initialized",
                "content_type_hint": "book",
                "options": {"allow_asset_download": True},
            },
        },
    )
    fake_adapter = LaterOperationFailingAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "部分" in capsys.readouterr().err
    assert len(fake_adapter.created) == 2
    assert fake_adapter.scanned_pages == []


def test_capture_apply_does_not_recreate_when_stale_error_happens_after_create(tmp_path, monkeypatch, capsys):
    class PartialAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(
                pages={
                    "page-books": {"id": "page-books", "title": "书单"},
                    "page-created": {
                        "id": "page-created",
                        "object": "page",
                        "properties": {
                            "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                            "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                        },
                    },
                }
            )
            self.children = {
                "page-books": [
                    {"type": "child_database", "id": "db-books", "child_database": {"title": "Books"}}
                ]
            }
            self.databases = {
                "db-books": {
                    "id": "db-books",
                    "title": "Books",
                    "data_sources": [{"id": "ds-books", "name": "Books"}],
                    "properties": {},
                }
            }
            self.data_sources = {
                "ds-books": {
                    "id": "ds-books",
                    "title": "Books",
                    "properties": {
                        "书名": {"id": "title", "type": "title", "title": {}},
                        "阅读状态": {"id": "state", "type": "status", "status": {"options": []}},
                    },
                }
            }
            self.scanned_pages: list[str] = []

        def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            raise cli.NotionApiError("body failed validation: property is invalid")

        def list_block_children(self, page_id: str) -> list[dict[str, Any]]:
            self.scanned_pages.append(page_id)
            return self.children.get(page_id, [])

        def retrieve_database(self, database_id: str) -> dict[str, Any]:
            return self.databases[database_id]

        def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
            return self.data_sources[data_source_id]

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}},
    )
    cache.write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {}},
            "asset_mapping": {},
            "requires_confirmation": False,
            "confirmation_reason": None,
        },
    )
    seed_v2_graph(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {"title": "可能性的艺术", "state": "initialized", "author": "author-page-1"},
            "completion_operations": [
                {
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "target_data_source_id": "ds-authors",
                    "field_mapping": {"bio": "简介"},
                    "record": {"bio": "简介"},
                    "schema": {"简介": {"name": "简介", "type": "rich_text"}},
                }
            ],
            "capture_input": {
                "raw_input": "《可能性的艺术》",
                "target_hint": "书单",
                "state": "initialized",
                "content_type_hint": "book",
                "options": {"allow_asset_download": True},
            },
        },
    )
    fake_adapter = PartialAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    assert "部分" in capsys.readouterr().err
    assert len(fake_adapter.created) == 1
    assert fake_adapter.scanned_pages == []


def test_capture_apply_includes_verification_summary_for_created_page(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    fake_adapter = FakeAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                    "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                    "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                    "页数": {"type": "number", "number": 400},
                    "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                    "封面": {
                        "type": "files",
                        "files": [
                            {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                        ],
                    },
                },
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["verification"]["verified"] is True
    assert result["verification"]["warnings"] == []
    assert result["verification"]["pages"][0]["page_id"] == "page-created"
    assert fake_adapter.retrieved_pages == ["page-created"]


def test_capture_apply_cover_download_failure_does_not_verify_as_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheStore(config).write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "封面": {"name": "封面", "type": "files"},
                    },
                },
            },
        },
    )
    seed_v2_graph(
        config,
        schema={
            "书名": {"name": "书名", "type": "title"},
            "封面": {"name": "封面", "type": "files"},
        },
    )
    cover_url = "https://example.com/cover.jpg"
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {"title": "可能性的艺术", "cover": cover_url},
            "field_mapping": {"title": "书名", "cover": "封面"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [
                {
                    "type": "cover_image",
                    "source_url": cover_url,
                    "local_cache_path": str(tmp_path / "covers" / "cover.jpg"),
                    "target_field": "封面",
                    "action": "download_and_attach",
                    "record_key": "cover",
                    "status": "planned",
                    "warning": None,
                }
            ],
        },
    )
    fake_adapter = FakeAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "cover": {"type": "external", "external": {"url": cover_url}},
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                    "封面": {
                        "type": "files",
                        "files": [
                            {"type": "external", "name": "cover.jpg", "external": {"url": cover_url}}
                        ],
                    },
                },
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    monkeypatch.setattr(assets, "default_download", lambda url: (_ for _ in ()).throw(OSError("download failed")))
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert {
        "field": "cover",
        "action": "download_and_attach",
        "status": "download_failed",
        "source_url": cover_url,
    } in result["asset_results"]
    assert f"asset_download_failed:cover:{cover_url}" in result["warnings"]
    assert result["verification"]["verified"] is False
    assert result["verification"]["pages"][0]["page_id"] == "page-created"
    assert f"asset_download_failed:cover:{cover_url}" in result["verification"]["warnings"]
    assert fake_adapter.retrieved_pages == ["page-created"]
    assert "封面" not in fake_adapter.created[0][1]



def test_apply_verification_accepts_uploaded_completion_file_url(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-author-picture",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "normalized_record": {"title": "反乌合之众", "author": "author-page"},
            "field_mapping": {"title": "名称", "author": "作者"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "completion_operations": [
                {
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "target_data_source_id": "ds-authors",
                    "field_mapping": {"author_picture": "Author Picture"},
                    "record": {"author_picture": "https://example.com/source-author.jpg"},
                    "schema": {"Author Picture": {"type": "files"}},
                    "asset_operations": [
                        {
                            "type": "file",
                            "source_url": "https://example.com/source-author.jpg",
                            "local_cache_path": "/tmp/source-author.jpg",
                            "target_field": "Author Picture",
                            "action": "download_and_attach",
                            "record_key": "author_picture",
                            "status": "planned",
                            "warning": None,
                        }
                    ],
                }
            ],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [{"type": "update_page", "page_id": "page-book"}],
            "completion_results": [
                {
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "page_id": "author-page",
                    "asset_results": [
                        {
                            "field": "author_picture",
                            "action": "download_and_attach",
                            "status": "uploaded",
                            "source_url": "https://example.com/source-author.jpg",
                        }
                    ],
                }
            ],
        },
        FakeAdapter(
            pages={
                "page-book": {
                    "id": "page-book",
                    "object": "page",
                    "properties": {
                        "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                        "作者": {"type": "relation", "relation": [{"id": "author-page"}]},
                    },
                },
                "author-page": {
                    "id": "author-page",
                    "object": "page",
                    "properties": {
                        "Author Picture": {"type": "files", "files": [{"type": "file", "file": {"url": "https://notion.example/signed-author.jpg"}}]},
                    },
                },
            }
        ),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "作者": {"type": "relation"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][1]["checks"]["author_picture"] == {"status": "present", "property": "Author Picture"}



def test_apply_verification_marks_failed_completion_asset_upload_unverified(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    source_url = "https://example.com/source-author.jpg"
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-author-picture-failed",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "normalized_record": {"title": "反乌合之众", "author": "author-page"},
            "field_mapping": {"title": "名称", "author": "作者"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "completion_operations": [
                {
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "target_data_source_id": "ds-authors",
                    "field_mapping": {"author_picture": "Author Picture"},
                    "record": {"author_picture": source_url},
                    "schema": {"Author Picture": {"type": "files"}},
                    "asset_operations": [
                        {
                            "type": "file",
                            "source_url": source_url,
                            "local_cache_path": "/tmp/source-author.jpg",
                            "target_field": "Author Picture",
                            "action": "download_and_attach",
                            "record_key": "author_picture",
                            "status": "planned",
                            "warning": None,
                        }
                    ],
                }
            ],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [{"type": "update_page", "page_id": "page-book"}],
            "completion_results": [
                {
                    "type": "complete_relation_page",
                    "source_record_key": "author",
                    "page_id": "author-page",
                    "asset_results": [
                        {
                            "field": "author_picture",
                            "action": "download_and_attach",
                            "status": "upload_failed",
                            "source_url": source_url,
                        }
                    ],
                }
            ],
            "warnings": [f"asset_upload_failed:author_picture:{source_url}"],
        },
        FakeAdapter(
            pages={
                "page-book": {
                    "id": "page-book",
                    "object": "page",
                    "properties": {
                        "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                        "作者": {"type": "relation", "relation": [{"id": "author-page"}]},
                    },
                },
                "author-page": {
                    "id": "author-page",
                    "object": "page",
                    "properties": {
                        "Author Picture": {
                            "type": "files",
                            "files": [{"type": "external", "external": {"url": source_url}}],
                        },
                    },
                },
            }
        ),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "作者": {"type": "relation"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is False
    assert f"asset_upload_failed:author_picture:{source_url}" in verification["warnings"]



def test_apply_verification_uses_resolved_relation_page_id(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-relation-created",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"title": "当我们不再理解世界", "author": "[智利] 本哈明·拉巴图特"},
            "field_mapping": {"title": "书名", "author": "作者"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [{"type": "create_or_update_page", "action": "update_page", "page_id": "page-book"}],
            "resolved_record": {"title": "当我们不再理解世界", "author": "author-page-created"},
        },
        FakeAdapter(
            pages={
                "page-book": {
                    "id": "page-book",
                    "object": "page",
                    "properties": {
                        "书名": {"type": "title", "title": [{"plain_text": "当我们不再理解世界"}]},
                        "作者": {"type": "relation", "relation": [{"id": "author-page-created"}]},
                    },
                },
            }
        ),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"书名": {"type": "title"}, "作者": {"type": "relation"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["author"] == {"status": "present", "property": "作者"}



def test_apply_verification_reports_mismatched_relation_page_id(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-relation-mismatch",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"title": "可能性的艺术", "author": "author-page-expected"},
            "field_mapping": {"title": "书名", "author": "作者"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_or_update_page", "action": "create_page", "page_id": "page-created"}]},
        FakeAdapter(
            pages={
                "page-created": {
                    "id": "page-created",
                    "object": "page",
                    "properties": {
                        "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                        "作者": {"type": "relation", "relation": [{"id": "author-page-actual"}]},
                    },
                },
            }
        ),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"书名": {"type": "title"}, "作者": {"type": "relation"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["author"] == {
        "status": "mismatch",
        "property": "作者",
        "expected_ids": ["author-page-expected"],
        "actual_ids": ["author-page-actual"],
    }
    assert "mismatch:author" in verification["warnings"]



def test_apply_verification_accepts_uploaded_file_url_for_uploaded_cover_asset(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-book",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "v2_profile",
            },
            "summary": {"title": "反乌合之众"},
            "normalized_record": {"title": "反乌合之众", "cover": "https://example.com/source-cover.jpg"},
            "field_mapping": {"title": "名称", "cover": "封面"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [
                {
                    "type": "cover_image",
                    "source_url": "https://example.com/source-cover.jpg",
                    "local_cache_path": "/tmp/source-cover.jpg",
                    "target_field": "封面",
                    "action": "download_and_attach",
                    "record_key": "cover",
                    "status": "planned",
                    "warning": None,
                }
            ],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {
            "results": [{"type": "update_page", "page_id": "page-book"}],
            "asset_results": [
                {
                    "field": "cover",
                    "action": "download_and_attach",
                    "status": "uploaded",
                    "source_url": "https://example.com/source-cover.jpg",
                }
            ],
        },
        FakeAdapter(
            {
                "id": "page-book",
                "object": "page",
                "cover": {"type": "external", "external": {"url": "https://example.com/source-cover.jpg"}},
                "properties": {
                    "名称": {"type": "title", "title": [{"plain_text": "反乌合之众"}]},
                    "封面": {"type": "files", "files": [{"type": "file", "file": {"url": "https://notion.example/signed-cover.jpg"}}]},
                },
            }
        ),
        plan,
        {"data_sources": {"books": {"data_source_id": "ds-books", "schema": {"名称": {"type": "title"}, "封面": {"type": "files"}}}}},
    )

    assert verification is not None
    assert verification["verified"] is True
    assert verification["pages"][0]["checks"]["cover"] == {"status": "present", "property": "封面"}



def test_apply_verification_reports_mismatched_mapped_file_url(monkeypatch):
    allow_verify_url_checks(monkeypatch)
    plan = WritePlan.from_dict(
        {
            "plan_id": "plan-file-url-mismatch",
            "content_type": "book",
            "target": {
                "page_title": "书单",
                "page_id": "page-books",
                "data_source_id": "ds-books",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"title": "可能性的艺术", "cover": "https://example.com/expected.jpg"},
            "field_mapping": {"title": "书名", "cover": "封面"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
            "asset_operations": [],
            "sources": [],
            "warnings": [],
            "requires_confirmation": False,
            "confirmation_reason": None,
        }
    )

    verification = cli._apply_verification_summary(
        {"results": [{"type": "create_or_update_page", "action": "create_page", "page_id": "page-created"}]},
        FakeAdapter(
            pages={
                "page-created": {
                    "id": "page-created",
                    "object": "page",
                    "properties": {
                        "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                        "封面": {
                            "type": "files",
                            "files": [
                                {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/actual.jpg"}}
                            ],
                        },
                    },
                },
            }
        ),
        plan,
        {
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "schema": {"书名": {"type": "title"}, "封面": {"type": "files"}},
                }
            }
        },
    )

    assert verification is not None
    assert verification["verified"] is False
    assert verification["pages"][0]["checks"]["cover"] == {
        "status": "mismatch",
        "property": "封面",
        "expected_urls": ["https://example.com/expected.jpg"],
        "actual_urls": ["https://example.com/actual.jpg"],
    }
    assert "mismatch:cover" in verification["warnings"]



def test_capture_apply_verification_reports_inaccessible_mapped_file_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheStore(config).write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "封面": {"name": "封面", "type": "files"},
                    },
                },
            },
        },
    )
    seed_v2_graph(
        config,
        schema={
            "书名": {"name": "书名", "type": "title"},
            "封面": {"name": "封面", "type": "files"},
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {
                "title": "可能性的艺术",
                "cover": "https://example.com/cover.jpg",
            },
            "field_mapping": {"title": "书名", "cover": "封面"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
        },
    )
    fake_adapter = FakeAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                    "封面": {
                        "type": "files",
                        "files": [
                            {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                        ],
                    },
                },
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    monkeypatch.setattr(cli, "url_is_accessible", lambda url: False)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["verification"]["verified"] is False
    assert result["verification"]["pages"][0]["checks"]["cover"] == {
        "status": "inaccessible",
        "property": "封面",
    }
    assert "inaccessible:cover" in result["verification"]["pages"][0]["warnings"]
    assert "inaccessible:cover" in result["verification"]["warnings"]
    assert fake_adapter.created

def test_capture_apply_verifies_relation_completion_pages(tmp_path, monkeypatch, capsys):
    class CompletionAdapter(FakeAdapter):
        def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
            super().__init__(pages=pages)
            self.updated: list[tuple[str, dict[str, Any]]] = []

        def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            self.updated.append((page_id, properties))
            return {"id": page_id, "url": f"https://notion.example/{page_id}"}

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheStore(config).write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                        "作者": {
                            "name": "作者",
                            "type": "relation",
                            "target_database_id": "db-authors",
                        },
                    },
                },
                "authors": {
                    "data_source_id": "ds-authors",
                    "title": "Authors",
                    "schema": {
                        "Author Picture": {"name": "Author Picture", "type": "files"},
                        "国籍": {"name": "国籍", "type": "select"},
                    },
                },
            },
        },
    )
    seed_v2_graph(
        config,
        schema={
            "书名": {"name": "书名", "type": "title"},
            "阅读状态": {"name": "阅读状态", "type": "status"},
            "作者": {"name": "作者", "type": "relation", "target_database_id": "db-authors"},
        },
    )
    graph = CacheV2Store(config).read_graph("graph-books")
    assert graph is not None
    graph["data_sources"]["ds-authors"] = {
        "data_source_id": "ds-authors",
        "title": "Authors",
        "schema": {
            "Author Picture": {"name": "Author Picture", "type": "files"},
            "国籍": {"name": "国籍", "type": "select"},
        },
    }
    CacheV2Store(config).write_graph("graph-books", graph)
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {
                "title": "失落的大陆",
                "state": "initialized",
                "author": "page-author-1",
            },
            "field_mapping": {"title": "书名", "state": "阅读状态", "author": "作者"},
            "completion_operations": [
                {
                    "type": "complete_relation_page",
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
                    "asset_operations": [],
                }
            ],
        },
    )
    fake_adapter = CompletionAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "失落的大陆"}]},
                    "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                    "作者": {"type": "relation", "relation": [{"id": "page-author-1"}]},
                },
            },
            "page-author-1": {
                "id": "page-author-1",
                "object": "page",
                "properties": {
                    "Author Picture": {"type": "files", "files": []},
                    "国籍": {"type": "select", "select": {"name": "美国"}},
                },
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verification"]["verified"] is False
    assert [page["page_id"] for page in result["verification"]["pages"]] == [
        "page-created",
        "page-author-1",
    ]
    relation_page = result["verification"]["pages"][1]
    assert relation_page["checks"] == {
        "page": {"status": "present"},
        "author_picture": {"status": "missing", "property": "Author Picture"},
        "author_country": {"status": "present", "property": "国籍"},
    }
    assert relation_page["warnings"] == ["missing:author_picture"]
    assert result["verification"]["warnings"] == ["missing:author_picture"]
    assert fake_adapter.retrieved_pages == ["page-created", "page-author-1"]



def test_capture_apply_verifies_relation_completion_pages_using_secondary_graph_schema(tmp_path, monkeypatch, capsys):
    class CompletionAdapter(FakeAdapter):
        def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
            super().__init__(pages=pages)
            self.updated: list[tuple[str, dict[str, Any]]] = []

        def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            self.updated.append((page_id, properties))
            return {"id": page_id, "url": f"https://notion.example/{page_id}"}

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_v2_graph(
        config,
        schema={
            "书名": {"name": "书名", "type": "title"},
            "阅读状态": {"name": "阅读状态", "type": "status"},
            "作者": {
                "name": "作者",
                "type": "relation",
                "target_database_id": "db-authors",
                "target_data_source_id": "ds-authors",
            },
        },
    )
    CacheV2Store(config).write_graph(
        "graph-authors",
        {
            "cache_version": 2,
            "graph_id": "graph-authors",
            "root": {"kind": "data_source", "id": "ds-authors"},
            "data_sources": {
                "ds-authors": {
                    "data_source_id": "ds-authors",
                    "title": "Authors",
                    "schema": {
                        "Author Picture": {"name": "Author Picture", "type": "files"},
                        "国籍": {"name": "国籍", "type": "select"},
                    },
                }
            },
        },
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "normalized_record": {
                "title": "失落的大陆",
                "state": "initialized",
                "author": "page-author-1",
            },
            "field_mapping": {"title": "书名", "state": "阅读状态", "author": "作者"},
            "completion_operations": [
                {
                    "type": "complete_relation_page",
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
                    "asset_operations": [],
                }
            ],
        },
    )
    fake_adapter = CompletionAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "失落的大陆"}]},
                    "阅读状态": {"type": "status", "status": {"name": "initialized"}},
                    "作者": {"type": "relation", "relation": [{"id": "page-author-1"}]},
                },
            },
            "page-author-1": {
                "id": "page-author-1",
                "object": "page",
                "properties": {
                    "Author Picture": {"type": "files", "files": []},
                    "国籍": {"type": "select", "select": {"name": "美国"}},
                },
            },
        }
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", AdapterFactoryProbe(fake_adapter))
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert "completion_schema_missing:author" not in result["warnings"]
    assert result["completion_results"] == [
        {
            "type": "complete_relation_page",
            "action": "update_page",
            "source_record_key": "author",
            "page_id": "page-author-1",
            "url": "https://notion.example/page-author-1",
        }
    ]
    assert fake_adapter.updated == [
        (
            "page-author-1",
            {
                "Author Picture": {
                    "files": [
                        {
                            "type": "external",
                            "name": "bryson.jpg",
                            "external": {"url": "https://example.com/bryson.jpg"},
                        }
                    ]
                },
                "国籍": {"select": {"name": "美国"}},
            },
        )
    ]
    assert [page["page_id"] for page in result["verification"]["pages"]] == [
        "page-created",
        "page-author-1",
    ]
    assert result["verification"]["pages"][1]["checks"] == {
        "page": {"status": "present"},
        "author_picture": {"status": "missing", "property": "Author Picture"},
        "author_country": {"status": "present", "property": "国籍"},
    }



def test_apply_verification_uses_plan_mapping_property_types_not_business_names(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    CacheStore(config).write_json(
        config.targets_dir / "custom.json",
        {
            "target": {"page_id": "page-custom", "title": "Custom"},
            "data_sources": {
                "items": {
                    "data_source_id": "ds-custom",
                    "title": "Items",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "Cached Primary", "page_count": "Cached Metric"},
                    "field_sources": {"title": "profile", "page_count": "profile"},
                    "schema": {
                        "Primary": {"type": "title"},
                        "Metric": {"type": "number"},
                    },
                }
            },
        },
    )
    seed_v2_graph(
        config,
        page_id="page-custom",
        data_source_id="ds-custom",
        title="Custom",
        data_source_title="Items",
        schema={"Primary": {"type": "title"}, "Metric": {"type": "number"}},
    )
    plan_path = tmp_path / "plan.json"
    write_plan_file(
        plan_path,
        {
            "target": {
                "page_title": "Custom",
                "page_id": "page-custom",
                "data_source_id": "ds-custom",
                "confidence": "high",
                "source": "cache",
            },
            "normalized_record": {"title": "任意条目", "page_count": 400},
            "field_mapping": {"title": "Primary", "page_count": "Metric"},
            "operations": [{"type": "create_or_update_page", "data_source_id": "ds-custom"}],
        },
    )
    fake_adapter = FakeAdapter(
        pages={
            "page-created": {
                "id": "page-created",
                "object": "page",
                "properties": {
                    "Primary": {"type": "title", "title": [{"plain_text": "任意条目"}]},
                    "Metric": {"type": "rich_text", "rich_text": [{"plain_text": "400"}]},
                },
            }
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert fake_adapter.created == [
        (
            "ds-custom",
            {
                "Primary": {"title": [{"text": {"content": "任意条目"}}]},
                "Metric": {"number": 400},
            },
        )
    ]
    assert result["verification"]["pages"][0]["checks"]["page_count"] == {
        "status": "missing",
        "property": "Metric",
    }
    assert "missing:page_count" in result["verification"]["warnings"]

def test_capture_apply_preserves_result_when_verification_page_retrieval_fails(tmp_path, monkeypatch, capsys):
    class VerificationPermissionAdapter(FakeAdapter):
        def retrieve_page(self, page_id: str) -> dict[str, Any]:
            self.retrieved_pages.append(page_id)
            if page_id == "page-created":
                raise cli.NotionPermissionError("cannot read created page")
            return super().retrieve_page(page_id)

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    seed_target_cache(config)
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    fake_adapter = VerificationPermissionAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["results"][0]["page_id"] == "page-created"
    assert result["verification"] == {
        "verified": False,
        "pages": [
            {
                "page_id": "page-created",
                "verified": False,
                "checks": {"page": {"status": "inaccessible"}},
                "warnings": ["inaccessible:page"],
            }
        ],
        "warnings": ["inaccessible:page"],
    }
    assert fake_adapter.created
    assert fake_adapter.retrieved_pages == ["page-created"]


def test_verify_capture_page_accepts_mapping_driven_arbitrary_property_names() -> None:
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "任意标题字段": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "任意状态字段": {"type": "status", "status": {"name": "想读"}},
                "任意文件字段": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
                "任意关系字段": {"type": "relation", "relation": [{"id": "author-page-1"}]},
            },
        }
    )

    result = verify_capture_page(
        "page-book-1",
        fake_adapter,
        url_checker=lambda url: True,
        field_mapping={
            "title": "任意标题字段",
            "state": "任意状态字段",
            "cover": "任意文件字段",
            "author": "任意关系字段",
        },
        schema={
            "任意标题字段": {"name": "任意标题字段", "type": "title"},
            "任意状态字段": {"name": "任意状态字段", "type": "status"},
            "任意文件字段": {"name": "任意文件字段", "type": "files"},
            "任意关系字段": {"name": "任意关系字段", "type": "relation"},
        },
        checks={
            "title": {"property_type": "title"},
            "state": {"property_type": "status"},
            "cover": {"property_type": "files", "check_urls": True},
            "author": {"property_type": "relation"},
        },
    )

    assert result == {
        "page_id": "page-book-1",
        "verified": True,
        "checks": {
            "page": {"status": "present"},
            "title": {"status": "present", "property": "任意标题字段"},
            "state": {"status": "present", "property": "任意状态字段"},
            "cover": {"status": "present", "property": "任意文件字段"},
            "author": {"status": "present", "property": "任意关系字段"},
            "page_cover": {"status": "present"},
        },
        "warnings": [],
    }


def test_verify_capture_page_without_mapping_only_checks_page_and_cover() -> None:
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
                "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
            },
        }
    )

    result = verify_capture_page("page-book-1", fake_adapter, url_checker=lambda url: True)

    assert result == {
        "page_id": "page-book-1",
        "verified": True,
        "checks": {
            "page": {"status": "present"},
            "page_cover": {"status": "present"},
        },
        "warnings": [],
    }


def test_capture_verify_successful_page_uses_fake_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    fake_adapter = FakeAdapter(
        pages={
            "page-book-1": {
                "id": "page-book-1",
                "object": "page",
                "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
                "properties": {
                    "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                    "阅读状态": {"type": "status", "status": {"name": "想读"}},
                    "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                    "页数": {"type": "number", "number": 400},
                    "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                    "封面": {
                        "type": "files",
                        "files": [
                            {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                        ],
                    },
                },
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "page_id": "page-book-1",
        "verified": True,
        "checks": {
            "page": {"status": "present"},
            "page_cover": {"status": "present"},
        },
        "warnings": [],
    }
    assert adapter_factory.called is True
    assert fake_adapter.retrieved_pages == ["page-book-1"]
    assert fake_adapter.created == []


class NotFoundAdapter(FakeAdapter):
    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        self.retrieved_pages.append(page_id)
        raise cli.NotionNotFoundError("page not found")


def test_capture_verify_missing_page_returns_stable_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    fake_adapter = NotFoundAdapter()
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-missing"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["page_id"] == "page-missing"
    assert result["verified"] is False
    assert result["checks"] == {"page": {"status": "missing"}, "page_cover": {"status": "missing"}}
    assert result["warnings"] == ["missing:page", "missing:page_cover"]
    assert adapter_factory.called is True
    assert fake_adapter.retrieved_pages == ["page-missing"]
    assert fake_adapter.created == []


def test_verify_capture_page_requires_mapped_isbn_and_page_count_values():
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": []},
                "页数": {"type": "number", "number": None},
                "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )

    result = verify_book_page(fake_adapter)

    assert result["verified"] is False
    assert result["checks"]["isbn"] == {"status": "missing", "property": "ISBN"}
    assert result["checks"]["page_count"] == {"status": "missing", "property": "页数"}
    assert "missing:isbn" in result["warnings"]
    assert "missing:page_count" in result["warnings"]
    assert fake_adapter.created == []


def test_verify_capture_page_requires_mapped_author_relation_value():
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "作者": {"type": "relation", "relation": []},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )

    result = verify_book_page(fake_adapter)

    assert result["verified"] is False
    assert result["checks"]["author"] == {"status": "missing", "property": "作者"}
    assert "missing:author" in result["warnings"]
    assert fake_adapter.created == []


def test_verify_capture_page_rejects_mapped_non_relation_author_property():
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "作者": {"type": "rich_text", "rich_text": [{"plain_text": "刘瑜"}]},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )

    result = verify_book_page(fake_adapter)

    assert result["verified"] is False
    assert result["checks"]["author"] == {"status": "missing", "property": "作者"}
    assert "missing:author" in result["warnings"]
    assert fake_adapter.created == []


def test_verify_capture_page_reports_missing_author_relation_when_unmapped():
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )
    field_mapping = {key: value for key, value in BOOK_FIELD_MAPPING.items() if key != "author"}

    result = verify_capture_page(
        "page-book-1",
        fake_adapter,
        url_checker=lambda url: True,
        field_mapping=field_mapping,
        schema=BOOK_SCHEMA,
        checks=BOOK_CHECKS,
    )

    assert result["verified"] is False
    assert result["checks"]["author"] == {"status": "missing"}
    assert "missing:author" in result["warnings"]
    assert fake_adapter.created == []


def test_url_is_accessible_falls_back_to_range_get_when_head_is_rejected(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    def fake_urlopen(request, timeout):
        calls.append((request.get_method(), request.get_header("Range")))
        if request.get_method() == "HEAD":
            raise urllib.error.HTTPError(request.full_url, 405, "Method Not Allowed", hdrs=None, fp=None)
        return FakeResponse()

    monkeypatch.setattr(verifier.urllib.request, "urlopen", fake_urlopen)

    assert verifier.url_is_accessible("https://example.com/cover.jpg") is True
    assert calls == [("HEAD", None), ("GET", "bytes=0-0")]


def test_capture_verify_reports_inaccessible_image_urls_from_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    monkeypatch.setattr(cli, "url_is_accessible", lambda url: False)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
    assert result["checks"] == {
        "page": {"status": "present"},
        "page_cover": {"status": "inaccessible"},
    }
    assert result["warnings"] == ["inaccessible:page_cover"]
    assert fake_adapter.created == []


def test_verify_capture_page_marks_inaccessible_cover_file_url() -> None:
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )
    checked_urls: list[str] = []

    def url_checker(url: str) -> bool:
        checked_urls.append(url)
        return url == "https://example.com/page-cover.jpg"

    result = verify_book_page(fake_adapter, url_checker=url_checker)

    assert result["verified"] is False
    assert result["checks"]["cover"] == {"status": "inaccessible", "property": "封面"}
    assert result["checks"]["page_cover"] == {"status": "present"}
    assert "inaccessible:cover" in result["warnings"]
    assert checked_urls == ["https://example.com/cover.jpg", "https://example.com/page-cover.jpg"]


def test_verify_capture_page_marks_cover_files_inaccessible_when_any_file_url_fails() -> None:
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "ok.jpg", "external": {"url": "https://example.com/ok.jpg"}},
                        {"type": "external", "name": "broken.jpg", "external": {"url": "https://example.com/broken.jpg"}},
                    ],
                },
            },
        }
    )

    result = verify_book_page(
        fake_adapter,
        url_checker=lambda url: url in {"https://example.com/ok.jpg", "https://example.com/page-cover.jpg"},
    )

    assert result["verified"] is False
    assert result["checks"]["cover"] == {"status": "inaccessible", "property": "封面"}
    assert "inaccessible:cover" in result["warnings"]


def test_verify_capture_page_marks_inaccessible_page_cover_url() -> None:
    fake_adapter = FakeAdapter(
        {
            "id": "page-book-1",
            "object": "page",
            "cover": {"type": "external", "external": {"url": "https://example.com/page-cover.jpg"}},
            "properties": {
                "书名": {"type": "title", "title": [{"plain_text": "可能性的艺术"}]},
                "阅读状态": {"type": "status", "status": {"name": "想读"}},
                "ISBN": {"type": "rich_text", "rich_text": [{"plain_text": "9787559847357"}]},
                "页数": {"type": "number", "number": 400},
                "作者": {"type": "relation", "relation": [{"id": "author-page-1"}]},
                "封面": {
                    "type": "files",
                    "files": [
                        {"type": "external", "name": "cover.jpg", "external": {"url": "https://example.com/cover.jpg"}}
                    ],
                },
            },
        }
    )

    result = verify_book_page(fake_adapter, url_checker=lambda url: url == "https://example.com/cover.jpg")

    assert result["verified"] is False
    assert result["checks"]["cover"] == {"status": "present", "property": "封面"}
    assert result["checks"]["page_cover"] == {"status": "inaccessible"}
    assert "inaccessible:page_cover" in result["warnings"]


def test_fake_adapter_rejects_unexpected_single_page_id() -> None:
    fake_adapter = FakeAdapter({"id": "page-book-1", "object": "page", "properties": {}})

    with pytest.raises(cli.NotionNotFoundError, match="author-page-1"):
        fake_adapter.retrieve_page("author-page-1")

def test_capture_apply_missing_plan_file_returns_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))

    exit_code = cli.main(["capture", "apply", "--plan", str(tmp_path / "missing.json")])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "计划文件不存在" in stderr
    assert "missing.json" in stderr

def test_capture_apply_invalid_plan_json_returns_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{invalid json", encoding="utf-8")

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "计划文件 JSON 无效" in stderr
    assert "plan.json" in stderr

def test_capture_apply_invalid_plan_shape_returns_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"plan_id": "missing-fields"}), encoding="utf-8")

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "计划内容无效" in stderr
    assert "plan.json" in stderr

def test_capture_apply_missing_target_structure_before_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    plan_path = tmp_path / "plan.json"
    write_plan_file(plan_path)
    adapter_factory = AdapterFactoryProbe()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path), "--confirmed"])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "ds-books" in stderr
    assert "target scan" in stderr
    assert adapter_factory.called is False
