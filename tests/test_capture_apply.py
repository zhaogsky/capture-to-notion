from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from capture_to_notion import cli, verifier
from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config
from capture_to_notion.models import WritePlan
from capture_to_notion.verifier import verify_capture_page


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


def write_plan_file(path: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {
        "plan_id": "plan-apply-1",
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
        "operations": [{"type": "create_or_update_page", "data_source_id": "ds-books"}],
        "asset_operations": [],
        "sources": [{"type": "user_input", "value": "把《可能性的艺术》初始化到书单"}],
        "warnings": [],
        "requires_confirmation": False,
        "confirmation_reason": None,
    }
    if overrides:
        data.update(overrides)
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

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.created.append((data_source_id, properties))
        return {"id": "page-created", "url": "https://notion.example/page-created"}

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["recovered_from_stale_cache"] is True
    assert fake_adapter.create_attempts[0][0] == "ds-old-books"
    assert fake_adapter.create_attempts[1][0] == "ds-new-books"
    assert fake_adapter.scanned_pages == ["page-books"]
    refreshed_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert refreshed_plan["target"]["data_source_id"] == "ds-new-books"
    assert refreshed_plan["field_mapping"] == {"title": "书名", "state": "阅读状态"}
    refreshed_cache = json.loads((config.targets_dir / "books.json").read_text(encoding="utf-8"))
    assert "ds-new-books" in [source["data_source_id"] for source in refreshed_cache["data_sources"].values()]


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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 2
    assert "possible_partial_write" in capsys.readouterr().err
    assert fake_adapter.create_attempts == 1
    assert fake_adapter.scanned_pages == []


def test_capture_apply_does_not_recover_update_page_not_found_as_create(tmp_path, monkeypatch, capsys):
    class UpdateNotFoundAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__(pages={"page-books": {"id": "page-books", "title": "书单"}})
            self.scanned_pages: list[str] = []

        def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
            raise cli.NotionNotFoundError(f"page not found: {page_id}")

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] is True
    assert result["verification"]["verified"] is True
    assert result["verification"]["warnings"] == []
    assert result["verification"]["pages"][0]["page_id"] == "page-created"
    assert fake_adapter.retrieved_pages == ["page-created"]


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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "计划文件 JSON 无效" in stderr
    assert "plan.json" in stderr

def test_capture_apply_invalid_plan_shape_returns_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"plan_id": "missing-fields"}), encoding="utf-8")

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

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

    exit_code = cli.main(["capture", "apply", "--plan", str(plan_path)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "ds-books" in stderr
    assert "target scan" in stderr
    assert adapter_factory.called is False
