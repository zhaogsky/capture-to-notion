import json

from capture_to_notion import cli
from capture_to_notion.notion_adapter import NotionAuthError


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seed_cached_books_target(root):
    write_json(
        root / "aliases.json",
        {
            "aliases": {
                "books": {
                    "type": "page",
                    "page_id": "page-books",
                    "description": "Books and reading status",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    write_json(
        root / "targets" / "bookshelf.json",
        {
            "target": {
                "page_id": "page-books",
                "title": "Bookshelf",
                "verified_at": "2026-05-11T00:00:00Z",
                "target_id": "raw-target-id",
                "workspace": "private",
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "schema_hash": "abc123",
                    "fields": {
                        "title": "Name",
                        "author": "Author",
                        "state": "Status",
                        "cover": "Cover",
                    },
                    "field_sources": {
                        "title": "profile",
                        "author": "profile",
                        "state": "profile",
                        "cover": "profile",
                    },
                }
            },
            "state_mapping": {"field": "Status", "values": {"initialized": "Want to read", "completed": "Read"}},
            "asset_mapping": {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}},
        },
    )


class SearchAdapter:
    def search(self, query):
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
    def search(self, query):
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


class ScanAdapter:
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


class PodcastCompletionDateScanAdapter:
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


class DataSourceScanAdapter:
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


def test_target_search_outputs_candidates_without_writing_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: SearchAdapter()))

    result = cli.main(["target", "search", "--query", "书单"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "query": "书单",
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
    }
    assert json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8")) == {"aliases": {}}


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
    assert json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8")) == {"aliases": {}}


def test_target_scan_saves_target_cache_and_alias(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: ScanAdapter()))

    result = cli.main(["target", "scan", "--page-id", "page-books", "--alias", "书单", "--target-id", "bookshelf"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "target_id": "bookshelf",
        "target_file": str(tmp_path / "targets" / "bookshelf.json"),
        "data_sources": ["Books"],
        "requires_confirmation": True,
    }
    target = json.loads((tmp_path / "targets" / "bookshelf.json").read_text(encoding="utf-8"))
    assert target["confirmation_reason"] == "field_mapping_missing"
    assert target["data_sources"]["db-books"]["fields"] == {}
    aliases = json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单"]["target_id"] == "bookshelf"


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
        },
    )
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: PodcastCompletionDateScanAdapter()))

    result = cli.main(["capture", "plan", "--input", str(input_file), "--compact"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["mapped_fields"]["完成时间"] == "完成时间"
    assert data["summary"]["writable_fields"]["完成时间"] == {
        "target_field": "完成时间",
        "value_status": "present",
        "write_status": "planned",
    }
    target = json.loads((tmp_path / "targets" / "podcastshelf.json").read_text(encoding="utf-8"))
    assert "完成时间" in target["data_sources"]["db-podcasts"]["schema"]
    assert target["data_sources"]["db-podcasts"]["fields"]["完成时间"] == "完成时间"



def test_target_scan_accepts_data_source_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    seed_cached_books_target(tmp_path)
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
    assert data == {
        "target_id": "books-ds",
        "target_file": str(tmp_path / "targets" / "books-ds.json"),
        "data_sources": ["Books"],
        "requires_confirmation": False,
    }
    target = json.loads((tmp_path / "targets" / "books-ds.json").read_text(encoding="utf-8"))
    assert target["target"] == {
        "page_id": None,
        "title": "Books",
        "target_id": "books-ds",
        "data_source_id": "ds-books",
    }
    assert target["data_sources"]["ds-books"]["role"] == "primary"
    aliases = json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单Books"]["type"] == "data_source"
    assert aliases["书单Books"]["data_source_id"] == "ds-books"


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
                "target_id": "bookshelf",
                "page_id": "page-books",
                "title": "Bookshelf",
                "description": "Books and reading status",
                "data_sources": ["Books"],
                "content_types": ["book"],
                "verified_at": "2026-05-11T00:00:00Z",
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
        "target_id": "bookshelf",
        "target_file": str(tmp_path / "targets" / "bookshelf.json"),
        "target": {
            "page_id": "page-books",
            "title": "Bookshelf",
            "verified_at": "2026-05-11T00:00:00Z",
        },
        "data_sources": [
            {
                "key": "books",
                "data_source_id": "ds-books",
                "title": "Books",
                "role": "primary",
                "content_types": ["book"],
                "schema_hash": "abc123",
                "fields": {
                    "title": "Name",
                    "author": "Author",
                    "state": "Status",
                    "cover": "Cover",
                },
            }
        ],
        "state_mapping": {"field": "Status", "values": {"initialized": "Want to read", "completed": "Read"}},
        "asset_mapping": {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}},
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
        "target_id": "bookshelf",
        "target_file": str(tmp_path / "targets" / "bookshelf.json"),
        "target": {
            "page_id": "page-books",
            "title": "Bookshelf",
            "verified_at": "2026-05-11T00:00:00Z",
        },
        "data_sources": [
            {
                "key": "books",
                "data_source_id": "ds-books",
                "title": "Books",
                "role": "primary",
                "content_types": ["book"],
                "schema_hash": "abc123",
                "field_count": 4,
            }
        ],
        "status": "cached",
    }
    assert "fields" not in data["data_sources"][0]
    assert "state_mapping" not in data
    assert "asset_mapping" not in data



def test_target_inspect_outputs_cached_target_details_by_target_id_without_notion_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    seed_cached_books_target(tmp_path)

    def fail_from_config(cls, config):
        raise AssertionError("target inspect must read local cache only")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))

    result = cli.main(["target", "inspect", "--target-id", "bookshelf"])

    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "alias": "books",
        "target_id": "bookshelf",
        "target_file": str(tmp_path / "targets" / "bookshelf.json"),
        "target": {
            "page_id": "page-books",
            "title": "Bookshelf",
            "verified_at": "2026-05-11T00:00:00Z",
        },
        "data_sources": [
            {
                "key": "books",
                "data_source_id": "ds-books",
                "title": "Books",
                "role": "primary",
                "content_types": ["book"],
                "schema_hash": "abc123",
                "fields": {
                    "title": "Name",
                    "author": "Author",
                    "state": "Status",
                    "cover": "Cover",
                },
            }
        ],
        "state_mapping": {"field": "Status", "values": {"initialized": "Want to read", "completed": "Read"}},
        "asset_mapping": {"cover": {"field": "Cover", "type": "files", "strategy": "download_and_attach"}},
        "status": "cached",
    }


def test_target_inspect_existing_alias_missing_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "books": {
                    "type": "page",
                    "page_id": "page-books",
                    "description": "Books and reading status",
                    "target_id": "bookshelf",
                }
            }
        },
    )

    result = cli.main(["target", "inspect", "--alias", "books"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 target alias: books" not in captured.err
    assert "未找到 target cache: bookshelf" in captured.err


def test_target_inspect_invalid_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "books": {
                    "type": "page",
                    "page_id": "page-books",
                    "description": "Books and reading status",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    (tmp_path / "targets").mkdir(parents=True)
    (tmp_path / "targets" / "bookshelf.json").write_text("{not json", encoding="utf-8")

    result = cli.main(["target", "inspect", "--alias", "books"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 target alias: books" not in captured.err
    assert "target cache 无效: bookshelf" in captured.err


def test_target_inspect_missing_target_id_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    result = cli.main(["target", "inspect", "--target-id", "bookshelf"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 target cache: bookshelf" in captured.err


def test_target_inspect_invalid_target_id_cache_reports_target_cache_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    (tmp_path / "targets").mkdir(parents=True)
    (tmp_path / "targets" / "bookshelf.json").write_text("{not json", encoding="utf-8")

    result = cli.main(["target", "inspect", "--target-id", "bookshelf"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "target cache 无效: bookshelf" in captured.err


def test_target_inspect_missing_alias_exits_with_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    result = cli.main(["target", "inspect", "--alias", "missing"])

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "未找到 target alias: missing" in captured.err


def test_target_list_handles_mixed_invalid_cache_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "bad-json": {
                    "type": "page",
                    "page_id": "page-bad-json",
                    "description": "Invalid JSON cache",
                    "target_id": "bad-json",
                },
                "missing": {
                    "type": "page",
                    "page_id": "page-missing",
                    "description": "Missing target cache",
                    "target_id": "missing-cache",
                },
                "no-target-id": {
                    "type": "page",
                    "page_id": "page-no-target-id",
                    "description": "No target id",
                },
                "non-dict-alias": "skip me",
                "numeric-target-id": {
                    "type": "page",
                    "page_id": "page-numeric-target-id",
                    "description": "Numeric target id",
                    "target_id": 123,
                },
                "valid-weird": {
                    "type": "page",
                    "page_id": "page-valid-weird-alias",
                    "description": "Malformed content types are ignored",
                    "target_id": "valid-weird",
                },
            }
        },
    )
    (tmp_path / "targets").mkdir(parents=True)
    (tmp_path / "targets" / "bad-json.json").write_text("{not json", encoding="utf-8")
    write_json(
        tmp_path / "targets" / "valid-weird.json",
        {
            "target": {
                "page_id": "page-valid-weird-cache",
                "title": "Valid Weird",
                "verified_at": "2026-05-11T00:00:00Z",
            },
            "data_sources": {
                "string": {"title": "String Source", "content_types": "book"},
                "none": {"title": "None Source", "content_types": None},
                "number": {"title": "Number Source", "content_types": 42},
                "dict": {"title": "Dict Source", "content_types": {"type": "book"}},
                "mixed": {"title": "Mixed Source", "content_types": ["article", 7, None, "book"]},
                "tuple": {"title": "Tuple Source", "content_types": ("video", 9)},
            },
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
                "target_id": "bad-json",
                "page_id": "page-bad-json",
                "title": None,
                "description": "Invalid JSON cache",
                "data_sources": [],
                "content_types": [],
                "verified_at": None,
                "status": "invalid_cache",
            },
            {
                "alias": "missing",
                "target_id": "missing-cache",
                "page_id": "page-missing",
                "title": None,
                "description": "Missing target cache",
                "data_sources": [],
                "content_types": [],
                "verified_at": None,
                "status": "missing_cache",
            },
            {
                "alias": "no-target-id",
                "target_id": None,
                "page_id": "page-no-target-id",
                "title": None,
                "description": "No target id",
                "data_sources": [],
                "content_types": [],
                "verified_at": None,
                "status": "missing_cache",
            },
            {
                "alias": "numeric-target-id",
                "target_id": 123,
                "page_id": "page-numeric-target-id",
                "title": None,
                "description": "Numeric target id",
                "data_sources": [],
                "content_types": [],
                "verified_at": None,
                "status": "missing_cache",
            },
            {
                "alias": "valid-weird",
                "target_id": "valid-weird",
                "page_id": "page-valid-weird-alias",
                "title": "Valid Weird",
                "description": "Malformed content types are ignored",
                "data_sources": [
                    "String Source",
                    "None Source",
                    "Number Source",
                    "Dict Source",
                    "Mixed Source",
                    "Tuple Source",
                ],
                "content_types": ["article", "book", "video"],
                "verified_at": "2026-05-11T00:00:00Z",
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
