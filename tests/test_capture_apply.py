from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

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
    def __init__(self, page: dict[str, Any] | None = None) -> None:
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.retrieved_pages: list[str] = []
        self.page = page

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        self.created.append((data_source_id, properties))
        return {"id": "page-created", "url": "https://notion.example/page-created"}

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("unexpected update_page call")

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        self.retrieved_pages.append(page_id)
        if self.page is None:
            raise AssertionError("unexpected retrieve_page call")
        return self.page


def allow_verify_url_checks(monkeypatch) -> None:
    monkeypatch.setattr(cli, "url_is_accessible", lambda url: True)


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
    fake_adapter = FakeAdapter()
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


def test_capture_verify_successful_page_uses_fake_adapter(tmp_path, monkeypatch, capsys):
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
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "page_id": "page-book-1",
        "verified": True,
        "checks": {
            "page": {"status": "present"},
            "title_property": {"status": "present", "property": "书名"},
            "status_property": {"status": "present", "property": "阅读状态"},
            "isbn_property": {"status": "present", "property": "ISBN"},
            "page_count_property": {"status": "present", "property": "页数"},
            "author_relation_property": {"status": "present", "property": "作者"},
            "cover_files_property": {"status": "present", "property": "封面"},
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
    assert result["checks"]["page"] == {"status": "missing"}
    assert result["checks"]["author_relation_property"] == {"status": "missing"}
    assert "missing:page" in result["warnings"]
    assert "missing:author_relation_property" in result["warnings"]
    assert adapter_factory.called is True
    assert fake_adapter.retrieved_pages == ["page-missing"]
    assert fake_adapter.created == []


def test_capture_verify_requires_isbn_and_page_count_values(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
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
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
    assert result["checks"]["isbn_property"] == {"status": "missing", "property": "ISBN"}
    assert result["checks"]["page_count_property"] == {"status": "missing", "property": "页数"}
    assert "missing:isbn_property" in result["warnings"]
    assert "missing:page_count_property" in result["warnings"]
    assert fake_adapter.created == []


def test_capture_verify_requires_author_relation_value(tmp_path, monkeypatch, capsys):
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
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
    assert result["checks"]["author_relation_property"] == {"status": "missing", "property": "作者"}
    assert "missing:author_relation_property" in result["warnings"]
    assert fake_adapter.created == []


def test_capture_verify_rejects_non_relation_author_property(tmp_path, monkeypatch, capsys):
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
    adapter_factory = AdapterFactoryProbe(fake_adapter)
    monkeypatch.setattr(cli.NotionAdapter, "from_config", adapter_factory)
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
    assert result["checks"]["author_relation_property"] == {"status": "missing", "property": "作者"}
    assert "missing:author_relation_property" in result["warnings"]
    assert fake_adapter.created == []


def test_capture_verify_reports_missing_author_relation_when_unmapped(tmp_path, monkeypatch, capsys):
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
    allow_verify_url_checks(monkeypatch)

    exit_code = cli.main(["capture", "verify", "--page-id", "page-book-1"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
    assert result["checks"]["author_relation_property"] == {"status": "missing"}
    assert "missing:author_relation_property" in result["warnings"]
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
    assert result["checks"]["cover_files_property"] == {"status": "inaccessible", "property": "封面"}
    assert result["checks"]["page_cover"] == {"status": "inaccessible"}
    assert "inaccessible:cover_files_property" in result["warnings"]
    assert "inaccessible:page_cover" in result["warnings"]
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

    result = verify_capture_page("page-book-1", fake_adapter, url_checker=url_checker)

    assert result["verified"] is False
    assert result["checks"]["cover_files_property"] == {"status": "inaccessible", "property": "封面"}
    assert result["checks"]["page_cover"] == {"status": "present"}
    assert "inaccessible:cover_files_property" in result["warnings"]
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

    result = verify_capture_page(
        "page-book-1",
        fake_adapter,
        url_checker=lambda url: url in {"https://example.com/ok.jpg", "https://example.com/page-cover.jpg"},
    )

    assert result["verified"] is False
    assert result["checks"]["cover_files_property"] == {"status": "inaccessible", "property": "封面"}
    assert "inaccessible:cover_files_property" in result["warnings"]


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

    result = verify_capture_page("page-book-1", fake_adapter, url_checker=lambda url: url == "https://example.com/cover.jpg")

    assert result["verified"] is False
    assert result["checks"]["cover_files_property"] == {"status": "present", "property": "封面"}
    assert result["checks"]["page_cover"] == {"status": "inaccessible"}
    assert "inaccessible:page_cover" in result["warnings"]


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
