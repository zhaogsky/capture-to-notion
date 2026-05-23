import json
import sys
import types
from pathlib import Path

import pytest

from capture_to_notion.config import ensure_config
from capture_to_notion.notion_adapter import (
    NotionAdapter,
    NotionApiError,
    NotionAuthError,
    NotionNotFoundError,
    NotionPermissionError,
    NotionRateLimitError,
    notion_token,
)


class FakeClient:
    def __init__(self):
        self.search_calls = []
        self.query_calls = []
        self.data_source_query_calls = []
        self.create_page_calls = []
        self.update_page_calls = []
        self.file_upload_create_calls = []
        self.file_upload_send_calls = []
        self.file_upload_send_payloads = []
        self.database = {"id": "db-1", "object": "database"}
        self.pages = types.SimpleNamespace(retrieve=self.retrieve_page, update=self.update_page, create=self.create_page)
        self.databases = types.SimpleNamespace(retrieve=self.retrieve_database, query=self.query_database)
        self.data_sources = types.SimpleNamespace(retrieve=self.retrieve_data_source, query=self.query_data_source)
        self.blocks = types.SimpleNamespace(children=types.SimpleNamespace(list=self.list_block_children))
        self.file_uploads = types.SimpleNamespace(create=self.create_file_upload, send=self.send_file_upload)

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return {
            "results": [
                {
                    "id": "page-books",
                    "object": "page",
                    "url": "https://example.com/page-books",
                    "last_edited_time": "2026-05-10T00:00:00Z",
                    "properties": {"title": {"type": "title", "title": [{"plain_text": "书单"}]}}
                }
            ]
        }

    def retrieve_page(self, page_id):
        return {"id": page_id, "object": "page"}

    def retrieve_database(self, database_id):
        return self.database | {"id": database_id}

    def retrieve_data_source(self, data_source_id):
        return {"id": data_source_id, "object": "data_source", "properties": {"名称": {"type": "title"}}}

    def query_database(self, database_id, filter=None):
        self.query_calls.append({"database_id": database_id, "filter": filter})
        return {"results": [{"id": "row-1"}]}

    def query_data_source(self, data_source_id, filter=None):
        self.data_source_query_calls.append({"data_source_id": data_source_id, "filter": filter})
        return {"results": [{"id": "row-1"}]}

    def list_block_children(self, block_id, start_cursor=None, page_size=100):
        return {"results": [{"id": "db-1", "type": "child_database"}], "has_more": False}

    def update_page(self, **kwargs):
        self.update_page_calls.append(kwargs)
        return {"id": kwargs["page_id"], "properties": kwargs.get("properties", {}), "cover": kwargs.get("cover")}

    def create_page(self, **kwargs):
        self.create_page_calls.append(kwargs)
        return {"id": "created-page", "url": "https://example.com/created-page"}

    def create_file_upload(self, **kwargs):
        self.file_upload_create_calls.append(kwargs)
        return {"id": "upload-1"}

    def send_file_upload(self, **kwargs):
        self.file_upload_send_calls.append(kwargs)
        file_arg = kwargs.get("file")
        if isinstance(file_arg, (str, Path)):
            self.file_upload_send_payloads.append({"invalid_path_argument": file_arg})
        elif isinstance(file_arg, tuple):
            file_obj = file_arg[-1]
            self.file_upload_send_payloads.append(file_obj.read())
        else:
            self.file_upload_send_payloads.append(file_arg.read())
        return {"ok": True}


def write_config(tmp_path, env_token_name="CUSTOM_NOTION_TOKEN"):
    config = ensure_config()
    config.config_file.write_text(
        json.dumps({"notion": {"auth": {"env_token_name": env_token_name}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return config


def test_notion_token_reads_env_var_named_by_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CUSTOM_NOTION_TOKEN", "secret_test")
    config = write_config(tmp_path)

    assert notion_token(config) == "secret_test"


def test_notion_token_reads_token_from_config_before_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CUSTOM_NOTION_TOKEN", "secret_env")
    config = ensure_config()
    config.config_file.write_text(
        json.dumps(
            {"notion": {"auth": {"env_token_name": "CUSTOM_NOTION_TOKEN", "token": "secret_config"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert notion_token(config) == "secret_config"


def test_notion_token_missing_raises_clear_auth_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CUSTOM_NOTION_TOKEN", raising=False)
    config = write_config(tmp_path)

    with pytest.raises(NotionAuthError, match="CUSTOM_NOTION_TOKEN"):
        notion_token(config)


def test_from_config_constructs_official_sdk_client_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NOTION_TOKEN", "secret_sdk")
    config = ensure_config()
    created = {}

    class FakeSdkClient:
        def __init__(self, auth):
            created["auth"] = auth

    fake_module = types.SimpleNamespace(Client=FakeSdkClient)
    monkeypatch.setitem(sys.modules, "notion_client", fake_module)

    adapter = NotionAdapter.from_config(config)

    assert isinstance(adapter, NotionAdapter)
    assert created["auth"] == "secret_sdk"


def test_search_calls_sdk_search_and_simplifies_results():
    client = FakeClient()
    adapter = NotionAdapter(client)

    results = adapter.search("书单")

    assert client.search_calls == [{"query": "书单"}]
    assert results == [
        {
            "id": "page-books",
            "object": "page",
            "title": "书单",
            "url": "https://example.com/page-books",
            "last_edited_time": "2026-05-10T00:00:00Z",
        }
    ]


def test_search_passes_page_size_when_limit_is_set():
    client = FakeClient()
    adapter = NotionAdapter(client)

    adapter.search("书单", limit=5)

    assert client.search_calls == [{"query": "书单", "page_size": 5}]


def test_search_includes_parent_path_when_parent_pages_are_available():
    class ParentPathClient(FakeClient):
        def search(self, **kwargs):
            self.search_calls.append(kwargs)
            return {
                "results": [
                    {
                        "id": "page-books",
                        "object": "page",
                        "url": "https://example.com/page-books",
                        "last_edited_time": "2026-05-10T00:00:00Z",
                        "properties": {"title": {"type": "title", "title": [{"plain_text": "书单"}]}},
                        "parent": {"type": "page_id", "page_id": "page-template"},
                    }
                ]
            }

        def retrieve_page(self, page_id):
            pages = {
                "page-template": {
                    "id": "page-template",
                    "object": "page",
                    "properties": {"title": {"type": "title", "title": [{"plain_text": "模板"}]}},
                    "parent": {"type": "page_id", "page_id": "page-tools"},
                },
                "page-tools": {
                    "id": "page-tools",
                    "object": "page",
                    "properties": {"title": {"type": "title", "title": [{"plain_text": "工具"}]}},
                    "parent": {"type": "workspace", "workspace": True},
                },
            }
            return pages[page_id]

    adapter = NotionAdapter(ParentPathClient())

    assert adapter.search("书单")[0]["parent_path"] == "工具 / 模板"


def test_search_can_skip_parent_path_resolution():
    class ParentPathClient(FakeClient):
        def search(self, **kwargs):
            self.search_calls.append(kwargs)
            return {
                "results": [
                    {
                        "id": "page-books",
                        "object": "page",
                        "url": "https://example.com/page-books",
                        "last_edited_time": "2026-05-10T00:00:00Z",
                        "properties": {"title": {"type": "title", "title": [{"plain_text": "书单"}]}},
                        "parent": {"type": "page_id", "page_id": "page-template"},
                    }
                ]
            }

        def retrieve_page(self, page_id):
            raise AssertionError("parent path should not be resolved")

    adapter = NotionAdapter(ParentPathClient())

    assert "parent_path" not in adapter.search("书单", include_parent_path=False)[0]


def test_search_marks_workspace_parent_as_top_level_location():
    class WorkspaceParentClient(FakeClient):
        def search(self, **kwargs):
            self.search_calls.append(kwargs)
            return {
                "results": [
                    {
                        "id": "page-books",
                        "object": "page",
                        "url": "https://example.com/page-books",
                        "last_edited_time": "2026-05-10T00:00:00Z",
                        "properties": {"title": {"type": "title", "title": [{"plain_text": "书单"}]}},
                        "parent": {"type": "workspace", "workspace": True},
                    }
                ]
            }

    adapter = NotionAdapter(WorkspaceParentClient())

    assert adapter.search("书单")[0]["parent_path"] == "工作区顶层"


def test_retrieve_and_query_methods_delegate_to_sdk_client():
    client = FakeClient()
    adapter = NotionAdapter(client)

    assert adapter.retrieve_page("page-1") == {"id": "page-1", "object": "page"}
    assert adapter.retrieve_database("db-1") == {"id": "db-1", "object": "database"}
    assert adapter.query_database("db-1") == [{"id": "row-1"}]
    assert adapter.list_block_children("page-1") == [{"id": "db-1", "type": "child_database"}]


def test_retrieve_data_source_delegates_to_sdk_client():
    adapter = NotionAdapter(FakeClient())

    assert adapter.retrieve_data_source("ds-1") == {
        "id": "ds-1",
        "object": "data_source",
        "properties": {"名称": {"type": "title"}},
    }


def test_create_page_uses_data_source_parent():
    client = FakeClient()
    adapter = NotionAdapter(client)

    result = adapter.create_page("ds-books", {"书名": {"title": []}})

    assert result == {"id": "created-page", "url": "https://example.com/created-page"}
    assert client.create_page_calls == [
        {
            "parent": {"data_source_id": "ds-books"},
            "properties": {"书名": {"title": []}},
        }
    ]


def test_create_page_converts_file_upload_cover_to_external_url():
    client = FakeClient()
    adapter = NotionAdapter(client)

    adapter.create_page(
        "ds-authors",
        {"Name": {"title": []}},
        cover={"type": "file_upload", "name": "author.jpg", "file_upload": {"id": "upload-1"}},
        cover_source_url="https://example.com/author.jpg",
    )

    assert client.create_page_calls == [
        {
            "parent": {"data_source_id": "ds-authors"},
            "properties": {"Name": {"title": []}},
            "cover": {"type": "external", "external": {"url": "https://example.com/author.jpg"}},
        }
    ]


def test_query_database_title_exact_builds_title_equals_filter():
    client = FakeClient()
    client.database = {
        "object": "database",
        "properties": {
            "作者": {"type": "title", "title": {}},
            "备注": {"type": "rich_text", "rich_text": {}},
        },
    }
    adapter = NotionAdapter(client)

    assert adapter.query_database_title_exact("db-authors", "刘慈欣") == [{"id": "row-1"}]
    assert client.query_calls == [
        {
            "database_id": "db-authors",
            "filter": {"property": "作者", "title": {"equals": "刘慈欣"}},
        }
    ]


def test_query_database_title_exact_raises_when_database_has_no_title_property():
    client = FakeClient()
    client.database = {"object": "database", "properties": {"备注": {"type": "rich_text"}}}
    adapter = NotionAdapter(client)

    with pytest.raises(NotionApiError, match="Database has no title property: db-authors"):
        adapter.query_database_title_exact("db-authors", "刘慈欣")


def test_query_database_title_exact_uses_database_data_source_and_queries_by_data_source_title():
    client = FakeClient()
    client.database = {
        "object": "database",
        "data_sources": [{"id": "ds-authors"}],
        "properties": {"备注": {"type": "rich_text"}},
    }

    def retrieve_data_source(data_source_id):
        return {
            "id": data_source_id,
            "object": "data_source",
            "properties": {
                "作者名称": {"type": "title", "title": {}},
                "备注": {"type": "rich_text", "rich_text": {}},
            },
        }

    client.retrieve_data_source = retrieve_data_source
    client.data_sources = types.SimpleNamespace(
        retrieve=client.retrieve_data_source,
        query=client.query_data_source,
    )
    adapter = NotionAdapter(client)

    assert adapter.query_database_title_exact("db-authors", "刘慈欣") == [{"id": "row-1"}]
    assert client.data_source_query_calls == [
        {
            "data_source_id": "ds-authors",
            "filter": {"property": "作者名称", "title": {"equals": "刘慈欣"}},
        }
    ]
    assert client.query_calls == []


def test_update_page_converts_file_upload_cover_to_external_url():
    client = FakeClient()
    adapter = NotionAdapter(client)

    result = adapter.update_page(
        "page-author",
        {"Author Picture": {"files": []}},
        cover={"type": "file_upload", "name": "author.jpg", "file_upload": {"id": "upload-1"}},
        cover_source_url="https://example.com/author.jpg",
    )

    assert result["cover"] == {"type": "external", "external": {"url": "https://example.com/author.jpg"}}
    assert client.update_page_calls == [
        {
            "page_id": "page-author",
            "properties": {"Author Picture": {"files": []}},
            "cover": {"type": "external", "external": {"url": "https://example.com/author.jpg"}},
        }
    ]



def test_upload_file_creates_single_part_upload_sends_file_and_returns_file_upload_object(tmp_path):
    client = FakeClient()
    adapter = NotionAdapter(client)
    file_path = tmp_path / "cover.jpg"
    file_path.write_bytes(b"image-bytes")

    uploaded = adapter.upload_file(file_path, "封面.jpg", "image/jpeg")

    assert client.file_upload_create_calls == [
        {
            "mode": "single_part",
            "filename": "封面.jpg",
            "content_type": "image/jpeg",
        }
    ]
    assert len(client.file_upload_send_calls) == 1
    assert client.file_upload_send_calls[0]["file_upload_id"] == "upload-1"
    assert not isinstance(client.file_upload_send_calls[0]["file"], (str, Path))
    assert client.file_upload_send_payloads == [b"image-bytes"]
    assert uploaded == {
        "type": "file_upload",
        "name": "封面.jpg",
        "file_upload": {"id": "upload-1"},
    }


def test_upload_file_for_property_delegates_to_upload_file(tmp_path, monkeypatch):
    adapter = NotionAdapter(FakeClient())
    file_path = tmp_path / "cover.jpg"
    file_path.write_bytes(b"image-bytes")
    calls = []

    def fake_upload_file(path, name, mime_type):
        calls.append((path, name, mime_type))
        return {"type": "file_upload", "name": name, "file_upload": {"id": "upload-2"}}

    monkeypatch.setattr(adapter, "upload_file", fake_upload_file)

    uploaded = adapter.upload_file_for_property(file_path, "cover.jpg", "image/jpeg")

    assert calls == [(file_path, "cover.jpg", "image/jpeg")]
    assert uploaded == {"type": "file_upload", "name": "cover.jpg", "file_upload": {"id": "upload-2"}}


@pytest.mark.parametrize(
    ("code", "status", "expected"),
    [
        ("unauthorized", 401, NotionAuthError),
        ("restricted_resource", 403, NotionPermissionError),
        ("object_not_found", 404, NotionNotFoundError),
        ("rate_limited", 429, NotionRateLimitError),
        ("internal_server_error", 500, NotionApiError),
    ],
)
def test_sdk_errors_convert_to_internal_errors(code, status, expected):
    class FakeSdkError(Exception):
        pass

    body = {"code": code, "message": "structured sdk failure"}
    error = FakeSdkError("sdk failed")
    error.code = code
    error.status = status
    error.body = body

    class BrokenClient(FakeClient):
        def search(self, **kwargs):
            raise error

    adapter = NotionAdapter(BrokenClient())

    with pytest.raises(expected) as exc_info:
        adapter.search("书单")

    assert exc_info.value.code == code
    assert exc_info.value.status == status
    assert exc_info.value.message == "sdk failed"
    assert exc_info.value.body == body
