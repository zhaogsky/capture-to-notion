import json

from capture_to_notion import cli
from capture_to_notion.notion_adapter import NotionAuthError


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
        "requires_confirmation": False,
    }
    target = json.loads((tmp_path / "targets" / "bookshelf.json").read_text(encoding="utf-8"))
    assert target["data_sources"]["db-books"]["fields"] == {
        "title": "名称",
        "state": "阅读状态",
        "cover": "封面",
    }
    aliases = json.loads((tmp_path / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    assert aliases["书单"]["target_id"] == "bookshelf"


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
