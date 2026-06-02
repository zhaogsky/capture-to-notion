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
        self.create_database_calls = []
        self.update_database_calls = []
        self.create_data_source_calls = []
        self.list_data_source_templates_calls = []
        self.update_data_source_calls = []
        self.update_page_calls = []
        self.view_list_calls = []
        self.view_retrieve_calls = []
        self.view_create_calls = []
        self.view_update_calls = []
        self.view_delete_calls = []
        self.user_list_calls = []
        self.file_upload_create_calls = []
        self.file_upload_send_calls = []
        self.file_upload_send_payloads = []
        self.file_upload_retrieve_calls = []
        self.file_upload_list_calls = []
        self.file_upload_complete_calls = []
        self.database = {"id": "db-1", "object": "database"}
        self.pages = types.SimpleNamespace(retrieve=self.retrieve_page, update=self.update_page, create=self.create_page)
        self.databases = types.SimpleNamespace(
            retrieve=self.retrieve_database,
            query=self.query_database,
            create=self.create_database,
            update=self.update_database,
        )
        self.data_sources = types.SimpleNamespace(
            retrieve=self.retrieve_data_source,
            query=self.query_data_source,
            create=self.create_data_source,
            update=self.update_data_source,
            list_templates=self.list_data_source_templates,
        )
        self.request_calls = []
        self.blocks = types.SimpleNamespace(children=types.SimpleNamespace(list=self.list_block_children))
        self.file_uploads = types.SimpleNamespace(
            create=self.create_file_upload,
            send=self.send_file_upload,
            retrieve=self.retrieve_file_upload,
            list=self.list_file_uploads,
            complete=self.complete_file_upload,
        )
        self.users = types.SimpleNamespace(list=self.list_users)

    def list_users(self, **kwargs):
        self.user_list_calls.append(kwargs)
        if kwargs.get("start_cursor") == "cursor-2":
            return {
                "results": [
                    {"id": "user-2", "name": "Alex Kim", "type": "person", "person": {"email": "alex.kim@example.com"}},
                ],
                "has_more": False,
            }
        return {
            "results": [
                {"id": "user-1", "name": "Ada Lovelace", "type": "person", "person": {"email": "ada@example.com"}},
            ],
            "has_more": True,
            "next_cursor": "cursor-2",
        }

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

    def create_database(self, **kwargs):
        self.create_database_calls.append(kwargs)
        return {"id": "created-database"}

    def update_database(self, **kwargs):
        self.update_database_calls.append(kwargs)
        return {"id": kwargs["database_id"], **{key: value for key, value in kwargs.items() if key != "database_id"}}

    def create_data_source(self, **kwargs):
        self.create_data_source_calls.append(kwargs)
        return {"id": "created-data-source", **kwargs}

    def list_data_source_templates(self, **kwargs):
        self.list_data_source_templates_calls.append(kwargs)
        return {"results": [{"id": "template-1"}, {"id": "template-2"}]}

    def update_data_source(self, **kwargs):
        self.update_data_source_calls.append(kwargs)
        return {"id": kwargs["data_source_id"], "properties": kwargs.get("properties", {})}

    def request(self, path, method, query=None, body=None):
        self.request_calls.append({"path": path, "method": method, "query": query, "body": body})
        if path == "/views" and method == "GET":
            if query and query.get("start_cursor") == "cursor-2":
                return {"results": [{"object": "view", "id": "view-2"}], "has_more": False}
            return {"results": [{"object": "view", "id": "view-1"}], "has_more": True, "next_cursor": "cursor-2"}
        if path == "/views/view-1" and method == "GET":
            return {"object": "view", "id": "view-1", "type": "gallery"}
        if path == "/views" and method == "POST":
            return {"object": "view", "id": "created-view", **(body or {})}
        if path == "/views/view-1" and method == "PATCH":
            return {"object": "view", "id": "view-1", **(body or {})}
        if path == "/views/view-1" and method == "DELETE":
            return {"object": "view", "id": "view-1", "in_trash": True}
        raise AssertionError(f"unexpected request: {method} {path}")

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

    def retrieve_file_upload(self, **kwargs):
        self.file_upload_retrieve_calls.append(kwargs)
        return {"id": kwargs["file_upload_id"], "status": "uploaded"}

    def list_file_uploads(self, **kwargs):
        self.file_upload_list_calls.append(kwargs)
        return {"results": [{"id": "upload-1"}, {"id": "upload-2"}]}

    def complete_file_upload(self, **kwargs):
        self.file_upload_complete_calls.append(kwargs)
        return {"id": kwargs["file_upload_id"], "status": "complete"}


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


def test_notion_adapter_lists_users_across_pages():
    client = FakeClient()
    adapter = NotionAdapter(client)

    users = adapter.list_users()

    assert users == [
        {"id": "user-1", "name": "Ada Lovelace", "type": "person", "person": {"email": "ada@example.com"}},
        {"id": "user-2", "name": "Alex Kim", "type": "person", "person": {"email": "alex.kim@example.com"}},
    ]
    assert client.user_list_calls == [{}, {"start_cursor": "cursor-2"}]



def test_notion_adapter_search_users_filters_by_email_or_name_case_insensitive():
    adapter = NotionAdapter(FakeClient())

    assert adapter.search_users("ADA@EXAMPLE.COM") == [
        {"id": "user-1", "name": "Ada Lovelace", "type": "person", "person": {"email": "ada@example.com"}}
    ]
    assert adapter.search_users("alex") == [
        {"id": "user-2", "name": "Alex Kim", "type": "person", "person": {"email": "alex.kim@example.com"}}
    ]



def test_from_config_constructs_official_sdk_client_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NOTION_TOKEN", "secret_sdk")
    config = ensure_config()
    created = {}

    class FakeSdkClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    fake_module = types.SimpleNamespace(Client=FakeSdkClient)
    monkeypatch.setitem(sys.modules, "notion_client", fake_module)

    adapter = NotionAdapter.from_config(config)

    assert isinstance(adapter, NotionAdapter)
    assert created["auth"] == "secret_sdk"


def test_adapter_from_config_passes_configured_notion_version(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    data = json.loads(config.config_file.read_text(encoding="utf-8"))
    data["notion"]["auth"]["token"] = "secret-token"
    data["notion"]["api_version"] = "2026-03-11"
    config.config_file.write_text(json.dumps(data), encoding="utf-8")

    created = {}

    class FakeSdkClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    fake_module = types.SimpleNamespace(Client=FakeSdkClient)
    monkeypatch.setitem(sys.modules, "notion_client", fake_module)

    adapter = NotionAdapter.from_config(config)

    assert isinstance(adapter, NotionAdapter)
    assert created == {"auth": "secret-token", "notion_version": "2026-03-11"}


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

    result = adapter.search("书单")[0]
    assert result["parent_path"] == "工具 / 模板"
    assert result["path"] == "工具 / 模板 / 书单"


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


def test_query_data_source_paginates_all_results_and_sends_start_cursor():
    class PaginatedDataSourceClient(FakeClient):
        def query_data_source(self, **kwargs):
            self.data_source_query_calls.append(kwargs)
            if kwargs.get("start_cursor") == "cursor-2":
                return {"results": [{"id": "row-2"}], "has_more": False}
            return {"results": [{"id": "row-1"}], "has_more": True, "next_cursor": "cursor-2"}

    client = PaginatedDataSourceClient()
    adapter = NotionAdapter(client)
    filters = {"property": "Status", "status": {"equals": "Active"}}

    assert adapter.query_data_source("ds-1", filters=filters) == [{"id": "row-1"}, {"id": "row-2"}]
    assert client.data_source_query_calls == [
        {"data_source_id": "ds-1", "filter": filters},
        {"data_source_id": "ds-1", "filter": filters, "start_cursor": "cursor-2"},
    ]



def test_query_database_paginates_all_results_and_sends_start_cursor():
    class PaginatedDatabaseClient(FakeClient):
        def query_database(self, **kwargs):
            self.query_calls.append(kwargs)
            if kwargs.get("start_cursor") == "cursor-2":
                return {"results": [{"id": "row-2"}], "has_more": False}
            return {"results": [{"id": "row-1"}], "has_more": True, "next_cursor": "cursor-2"}

    client = PaginatedDatabaseClient()
    adapter = NotionAdapter(client)
    filters = {"property": "Status", "status": {"equals": "Active"}}

    assert adapter.query_database("db-1", filters=filters) == [{"id": "row-1"}, {"id": "row-2"}]
    assert client.query_calls == [
        {"database_id": "db-1", "filter": filters},
        {"database_id": "db-1", "filter": filters, "start_cursor": "cursor-2"},
    ]



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


def test_create_child_page_uses_page_parent_and_title_property():
    calls = []

    class Pages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return {"id": "page-created", "url": "https://notion.so/page-created"}

    class Client:
        pages = Pages()

    adapter = NotionAdapter(Client())

    result = adapter.create_child_page(
        "parent-page",
        "DeepSeek V4",
        children=[{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}],
    )

    assert result["id"] == "page-created"
    assert calls == [
        {
            "parent": {"page_id": "parent-page"},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": "DeepSeek V4"}}]}},
            "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}],
        }
    ]


def test_append_block_children_calls_blocks_children_append():
    calls = []

    class Children:
        def append(self, **kwargs):
            calls.append(kwargs)
            return {"results": kwargs["children"]}

    class Blocks:
        children = Children()

    class Client:
        blocks = Blocks()

    adapter = NotionAdapter(Client())
    children = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}]

    result = adapter.append_block_children("page-created", children)

    assert result == {"results": children}
    assert calls == [{"block_id": "page-created", "children": children}]


def test_retrieve_block_delegates_exact_kwargs_and_returns_response():
    calls = []

    class Blocks:
        def retrieve(self, **kwargs):
            calls.append(kwargs)
            return {"object": "block", "id": kwargs["block_id"]}

    class Client:
        blocks = Blocks()

    result = NotionAdapter(Client()).retrieve_block("block-1")

    assert result == {"object": "block", "id": "block-1"}
    assert calls == [{"block_id": "block-1"}]


def test_update_block_delegates_exact_kwargs_and_returns_response():
    calls = []

    class Blocks:
        def update(self, **kwargs):
            calls.append(kwargs)
            return {"object": "block", "id": kwargs["block_id"], "archived": kwargs.get("archived")}

    class Client:
        blocks = Blocks()

    payload = {"archived": True, "paragraph": {"rich_text": []}}
    result = NotionAdapter(Client()).update_block("block-1", **payload)

    assert result == {"object": "block", "id": "block-1", "archived": True}
    assert calls == [{"block_id": "block-1", **payload}]


def test_delete_block_delegates_exact_kwargs_and_returns_response():
    calls = []

    class Blocks:
        def delete(self, **kwargs):
            calls.append(kwargs)
            return {"object": "block", "id": kwargs["block_id"], "archived": True}

    class Client:
        blocks = Blocks()

    result = NotionAdapter(Client()).delete_block("block-1")

    assert result == {"object": "block", "id": "block-1", "archived": True}
    assert calls == [{"block_id": "block-1"}]


def test_move_page_delegates_exact_kwargs_and_returns_response():
    calls = []

    class Pages:
        def move(self, **kwargs):
            calls.append(kwargs)
            return {"object": "page", "id": kwargs["page_id"], "parent": kwargs["parent"]}

    class Client:
        pages = Pages()

    parent = {"page_id": "parent-page"}
    result = NotionAdapter(Client()).move_page("page-1", parent)

    assert result == {"object": "page", "id": "page-1", "parent": parent}
    assert calls == [{"page_id": "page-1", "parent": parent}]


def test_archive_page_moves_page_to_trash():
    calls = []

    class Pages:
        def update(self, **kwargs):
            calls.append(kwargs)
            return {"object": "page", "id": kwargs["page_id"], "in_trash": kwargs["in_trash"]}

    class Client:
        pages = Pages()

    result = NotionAdapter(Client()).archive_page("page-1")

    assert result == {"object": "page", "id": "page-1", "in_trash": True}
    assert calls == [{"page_id": "page-1", "in_trash": True}]


def test_retrieve_page_property_delegates_exact_kwargs_and_returns_response():
    calls = []

    class Properties:
        def retrieve(self, **kwargs):
            calls.append(kwargs)
            return {"object": "property_item", "id": kwargs["property_id"]}

    class Pages:
        properties = Properties()

    class Client:
        pages = Pages()

    result = NotionAdapter(Client()).retrieve_page_property("page-1", "prop-1")

    assert result == {"object": "property_item", "id": "prop-1"}
    assert calls == [{"page_id": "page-1", "property_id": "prop-1"}]


def test_create_database_uses_initial_data_source_properties():
    client = FakeClient()
    adapter = NotionAdapter(client)

    result = adapter.create_database(
        "page-show",
        "数据库",
        {
            "主题": {"type": "title", "title": {}},
            "状态": {"type": "status", "status": {}},
        },
    )

    assert result == {"id": "created-database"}
    assert client.create_database_calls == [
        {
            "parent": {"type": "page_id", "page_id": "page-show"},
            "title": [{"type": "text", "text": {"content": "数据库"}}],
            "initial_data_source": {
                "title": [{"type": "text", "text": {"content": "数据库"}}],
                "properties": {
                    "主题": {"type": "title", "title": {}},
                    "状态": {"type": "status", "status": {}},
                },
            },
        }
    ]


def test_create_database_creates_views_with_created_data_source_id():
    class DatabaseWithDataSourceClient(FakeClient):
        def create_database(self, **kwargs):
            self.create_database_calls.append(kwargs)
            return {
                "id": "created-database",
                "data_sources": [{"id": "created-data-source"}],
            }

        def retrieve_data_source(self, data_source_id):
            raise AssertionError("retrieve_data_source should not be called without source_schema metadata")

    client = DatabaseWithDataSourceClient()
    adapter = NotionAdapter(client)

    result = adapter.create_database(
        "page-show",
        "数据库",
        {"主题": {"type": "title", "title": {}}},
        views=[
            {
                "name": "画廊",
                "type": "gallery",
                "filter": {"property": "状态", "status": {"equals": "在读"}},
                "sorts": [{"property": "主题", "direction": "ascending"}],
            },
            {
                "name": "表格",
                "type": "table",
            },
        ],
    )

    assert result["id"] == "created-database"
    assert result["created_views"] == [
        {
            "object": "view",
            "id": "created-view",
            "data_source_id": "created-data-source",
            "database_id": "created-database",
            "name": "画廊",
            "type": "gallery",
            "filter": {"property": "状态", "status": {"equals": "在读"}},
            "sorts": [{"property": "主题", "direction": "ascending"}],
        },
        {
            "object": "view",
            "id": "created-view",
            "data_source_id": "created-data-source",
            "database_id": "created-database",
            "name": "表格",
            "type": "table",
        },
    ]
    assert client.request_calls == [
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {
                "data_source_id": "created-data-source",
                "database_id": "created-database",
                "name": "画廊",
                "type": "gallery",
                "filter": {"property": "状态", "status": {"equals": "在读"}},
                "sorts": [{"property": "主题", "direction": "ascending"}],
            },
        },
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {
                "data_source_id": "created-data-source",
                "database_id": "created-database",
                "name": "表格",
                "type": "table",
            },
        },
    ]


def test_create_database_ignores_cloned_view_parent_scopes_when_creating_views():
    class DatabaseWithDataSourceClient(FakeClient):
        def create_database(self, **kwargs):
            self.create_database_calls.append(kwargs)
            return {
                "id": "new-database",
                "data_sources": [{"id": "new-data-source"}],
            }

    client = DatabaseWithDataSourceClient()
    adapter = NotionAdapter(client)

    result = adapter.create_database(
        "page-show",
        "数据库",
        {"主题": {"type": "title", "title": {}}},
        views=[
            {
                "name": "克隆视图",
                "type": "gallery",
                "view_id": "old-view",
                "create_database": {"parent": {"page_id": "old-page"}},
                "configuration": {"gallery": {"card_preview": "cover"}},
            }
        ],
    )

    assert result["created_views"] == [
        {
            "object": "view",
            "id": "created-view",
            "data_source_id": "new-data-source",
            "database_id": "new-database",
            "name": "克隆视图",
            "type": "gallery",
            "configuration": {"gallery": {"card_preview": "cover"}},
        }
    ]
    assert client.request_calls == [
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {
                "data_source_id": "new-data-source",
                "database_id": "new-database",
                "name": "克隆视图",
                "type": "gallery",
                "configuration": {"gallery": {"card_preview": "cover"}},
            },
        }
    ]


def test_create_database_remaps_view_property_references_using_retrieved_data_source_schema():
    class DatabaseWithDataSourceClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.retrieve_data_source_calls = []

        def create_database(self, **kwargs):
            self.create_database_calls.append(kwargs)
            return {
                "id": "new-database",
                "data_sources": [{"id": "new-data-source"}],
            }

        def retrieve_data_source(self, data_source_id):
            self.retrieve_data_source_calls.append(data_source_id)
            return {
                "id": data_source_id,
                "object": "data_source",
                "properties": {
                    "日期": {"id": "dst-date", "type": "date", "date": {}},
                    "状态": {"id": "dst-status", "type": "status", "status": {}},
                },
            }

    client = DatabaseWithDataSourceClient()
    adapter = NotionAdapter(client)

    adapter.create_database(
        "page-show",
        "数据库",
        {
            "日期": {"type": "date", "date": {}},
            "状态": {"type": "status", "status": {}},
        },
        views=[
            {
                "name": "克隆视图",
                "type": "timeline",
                "_source_schema": {
                    "日期": {"id": "src-date", "type": "date"},
                    "状态": {"id": "src-status", "type": "status"},
                },
                "warnings": [{"code": "old_warning"}],
                "view_id": "old-view",
                "create_database": {"parent": {"page_id": "old-page"}},
                "configuration": {
                    "timeline": {"date_property_id": "src-date", "end_date_property_id": "src-date"},
                    "group_by": {"property_id": "src-status"},
                },
                "sorts": [{"property": "src-status", "direction": "ascending"}],
                "filter": {"property": "src-date", "date": {"is_not_empty": True}},
            }
        ],
    )

    assert client.retrieve_data_source_calls == ["new-data-source"]
    assert client.request_calls == [
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {
                "data_source_id": "new-data-source",
                "database_id": "new-database",
                "name": "克隆视图",
                "type": "timeline",
                "filter": {"property": "dst-date", "date": {"is_not_empty": True}},
                "sorts": [{"property": "dst-status", "direction": "ascending"}],
                "configuration": {
                    "timeline": {"date_property_id": "dst-date", "end_date_property_id": "dst-date"},
                    "group_by": {"property_id": "dst-status"},
                },
            },
        }
    ]


def test_create_database_falls_back_to_valid_private_source_schema_for_view_remap():
    class DatabaseWithDataSourceClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.retrieve_data_source_calls = []

        def create_database(self, **kwargs):
            self.create_database_calls.append(kwargs)
            return {
                "id": "new-database",
                "data_sources": [{"id": "new-data-source"}],
            }

        def retrieve_data_source(self, data_source_id):
            self.retrieve_data_source_calls.append(data_source_id)
            return {
                "id": data_source_id,
                "object": "data_source",
                "properties": {
                    "状态": {"id": "dst-status", "type": "status", "status": {}},
                },
            }

    client = DatabaseWithDataSourceClient()
    adapter = NotionAdapter(client)

    adapter.create_database(
        "page-show",
        "数据库",
        {"状态": {"type": "status", "status": {}}},
        views=[
            {
                "name": "克隆视图",
                "type": "board",
                "source_schema": None,
                "_source_schema": {
                    "状态": {"id": "src-status", "type": "status"},
                },
                "configuration": {
                    "board": {},
                    "group_by": {"property_id": "src-status"},
                },
            }
        ],
    )

    assert client.retrieve_data_source_calls == ["new-data-source"]
    assert client.request_calls == [
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {
                "data_source_id": "new-data-source",
                "database_id": "new-database",
                "name": "克隆视图",
                "type": "board",
                "configuration": {
                    "board": {},
                    "group_by": {"property_id": "dst-status"},
                },
            },
        }
    ]


def test_create_data_source_converts_title_and_delegates_to_sdk_client():
    client = FakeClient()
    adapter = NotionAdapter(client)
    parent = {"type": "page_id", "page_id": "page-show"}
    properties = {"主题": {"type": "title", "title": {}}}

    result = adapter.create_data_source(parent, "资料库", properties)

    rich_title = [{"type": "text", "text": {"content": "资料库"}}]
    assert result == {"id": "created-data-source", "parent": parent, "title": rich_title, "properties": properties}
    assert client.create_data_source_calls == [
        {"parent": parent, "title": rich_title, "properties": properties}
    ]


def test_list_data_source_templates_returns_results_from_sdk_client():
    client = FakeClient()
    adapter = NotionAdapter(client)

    result = adapter.list_data_source_templates("ds-episodes")

    assert result == [{"id": "template-1"}, {"id": "template-2"}]
    assert client.list_data_source_templates_calls == [{"data_source_id": "ds-episodes"}]


def test_update_database_delegates_payload_to_sdk_client():
    client = FakeClient()
    adapter = NotionAdapter(client)
    payload = {"title": [{"type": "text", "text": {"content": "新标题"}}], "is_inline": True}

    result = adapter.update_database("db-episodes", **payload)

    assert result == {"id": "db-episodes", **payload}
    assert client.update_database_calls == [{"database_id": "db-episodes", **payload}]


def test_update_data_source_delegates_properties_to_sdk_client():
    client = FakeClient()
    adapter = NotionAdapter(client)

    result = adapter.update_data_source("ds-episodes", {"状态": {"type": "status", "status": {}}})

    assert result == {"id": "ds-episodes", "properties": {"状态": {"type": "status", "status": {}}}}
    assert client.update_data_source_calls == [
        {"data_source_id": "ds-episodes", "properties": {"状态": {"type": "status", "status": {}}}}
    ]


def test_file_upload_management_methods_delegate_to_sdk_client():
    client = FakeClient()
    adapter = NotionAdapter(client)

    assert adapter.retrieve_file_upload("upload-1") == {"id": "upload-1", "status": "uploaded"}
    assert adapter.list_file_uploads() == [{"id": "upload-1"}, {"id": "upload-2"}]
    assert adapter.complete_file_upload("upload-1") == {"id": "upload-1", "status": "complete"}
    assert client.file_upload_retrieve_calls == [{"file_upload_id": "upload-1"}]
    assert client.file_upload_list_calls == [{}]
    assert client.file_upload_complete_calls == [{"file_upload_id": "upload-1"}]


def test_views_api_methods_use_client_request_without_sdk_views_namespace():
    client = FakeClient()
    assert not hasattr(client, "views")
    adapter = NotionAdapter(client)

    assert adapter.list_views(data_source_id="ds-1") == [
        {"object": "view", "id": "view-1"},
        {"object": "view", "id": "view-2"},
    ]
    assert adapter.retrieve_view("view-1")["type"] == "gallery"
    created = adapter.create_view(
        data_source_id="ds-1",
        database_id="db-1",
        name="Episodes",
        view_type="gallery",
        filter={"property": "Status", "status": {"equals": "Active"}},
        sorts=[{"property": "Name", "direction": "ascending"}],
        quick_filters={"status": ["Active"]},
        configuration={"gallery": {"card_preview": "cover"}},
    )
    assert created["id"] == "created-view"
    assert adapter.update_view("view-1", name="New name") == {"object": "view", "id": "view-1", "name": "New name"}
    assert adapter.delete_view("view-1")["in_trash"] is True

    assert client.request_calls == [
        {"path": "/views", "method": "GET", "query": {"data_source_id": "ds-1"}, "body": None},
        {
            "path": "/views",
            "method": "GET",
            "query": {"data_source_id": "ds-1", "start_cursor": "cursor-2"},
            "body": None,
        },
        {"path": "/views/view-1", "method": "GET", "query": None, "body": None},
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {
                "data_source_id": "ds-1",
                "name": "Episodes",
                "type": "gallery",
                "database_id": "db-1",
                "filter": {"property": "Status", "status": {"equals": "Active"}},
                "sorts": [{"property": "Name", "direction": "ascending"}],
                "quick_filters": {"status": ["Active"]},
                "configuration": {"gallery": {"card_preview": "cover"}},
            },
        },
        {"path": "/views/view-1", "method": "PATCH", "query": None, "body": {"name": "New name"}},
        {"path": "/views/view-1", "method": "DELETE", "query": None, "body": None},
    ]


def test_list_views_rejects_empty_data_source_id_when_database_id_is_provided():
    client = FakeClient()
    adapter = NotionAdapter(client)

    with pytest.raises(NotionApiError):
        adapter.list_views(data_source_id="", database_id="db-1")

    assert client.request_calls == []


def test_list_views_rejects_empty_data_source_id_without_sending_request():
    client = FakeClient()
    adapter = NotionAdapter(client)

    with pytest.raises(NotionApiError):
        adapter.list_views(data_source_id="")

    assert client.request_calls == []


def test_create_view_ignores_empty_database_id_when_view_id_parent_is_provided():
    client = FakeClient()
    adapter = NotionAdapter(client)

    adapter.create_view(
        data_source_id="ds-1",
        database_id="",
        view_id="view-1",
        name="View",
        view_type="table",
    )

    assert client.request_calls == [
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {"data_source_id": "ds-1", "name": "View", "type": "table", "view_id": "view-1"},
        }
    ]


def test_create_view_treats_empty_create_database_as_provided_and_sends_it():
    client = FakeClient()
    adapter = NotionAdapter(client)

    adapter.create_view(data_source_id="ds-1", create_database={}, name="View", view_type="table")

    assert client.request_calls == [
        {
            "path": "/views",
            "method": "POST",
            "query": None,
            "body": {"data_source_id": "ds-1", "name": "View", "type": "table", "create_database": {}},
        }
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"data_source_id": "", "name": "View", "view_type": "table"},
        {"data_source_id": "ds-1", "name": "", "view_type": "table"},
        {"data_source_id": "ds-1", "name": "View", "view_type": ""},
    ],
)
def test_create_view_rejects_empty_required_fields_without_sending_request(kwargs):
    client = FakeClient()
    adapter = NotionAdapter(client)

    with pytest.raises(NotionApiError):
        adapter.create_view(database_id="db-1", **kwargs)

    assert client.request_calls == []


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


def test_query_database_title_exact_uses_explicit_data_source_id():
    client = FakeClient()
    client.database = {
        "object": "database",
        "data_sources": [{"id": "ds-first"}],
        "properties": {"Legacy": {"type": "title", "title": {}}},
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

    assert adapter.query_database_title_exact("db-authors", "刘慈欣", data_source_id="ds-authors") == [{"id": "row-1"}]
    assert client.data_source_query_calls == [
        {
            "data_source_id": "ds-authors",
            "filter": {"property": "作者名称", "title": {"equals": "刘慈欣"}},
        }
    ]
    assert client.query_calls == []



def test_create_relation_target_page_uses_database_data_source_title_property():
    client = FakeClient()
    client.database = {
        "object": "database",
        "data_sources": [{"id": "ds-authors"}],
        "properties": {"Legacy": {"type": "rich_text"}},
    }

    def retrieve_data_source(data_source_id):
        return {
            "id": data_source_id,
            "object": "data_source",
            "properties": {
                "Name": {"type": "title", "title": {}},
                "简介": {"type": "rich_text", "rich_text": {}},
            },
        }

    client.retrieve_data_source = retrieve_data_source
    client.data_sources = types.SimpleNamespace(
        retrieve=client.retrieve_data_source,
        query=client.query_data_source,
        create=client.create_data_source,
        update=client.update_data_source,
        list_templates=client.list_data_source_templates,
    )
    adapter = NotionAdapter(client)

    assert adapter.create_relation_target_page("db-authors", "刘慈欣") == {"id": "created-page", "url": "https://example.com/created-page"}
    assert client.create_page_calls == [
        {
            "parent": {"data_source_id": "ds-authors"},
            "properties": {"Name": {"title": [{"text": {"content": "刘慈欣"}}]}},
        }
    ]



def test_create_relation_target_page_uses_explicit_data_source_id():
    client = FakeClient()
    adapter = NotionAdapter(client)

    adapter.create_relation_target_page("db-authors", "刘慈欣", data_source_id="ds-authors")

    assert client.create_page_calls == [
        {
            "parent": {"data_source_id": "ds-authors"},
            "properties": {"名称": {"title": [{"text": {"content": "刘慈欣"}}]}},
        }
    ]



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
        "mime_type": "image/jpeg",
        "file_upload": {"id": "upload-1"},
    }


def test_upload_file_for_property_delegates_to_upload_file(tmp_path, monkeypatch):
    adapter = NotionAdapter(FakeClient())
    file_path = tmp_path / "cover.jpg"
    file_path.write_bytes(b"image-bytes")
    calls = []

    def fake_upload_file(path, name, mime_type):
        calls.append((path, name, mime_type))
        return {"type": "file_upload", "name": name, "mime_type": mime_type, "file_upload": {"id": "upload-2"}}

    monkeypatch.setattr(adapter, "upload_file", fake_upload_file)

    uploaded = adapter.upload_file_for_property(file_path, "cover.jpg", "image/jpeg")

    assert calls == [(file_path, "cover.jpg", "image/jpeg")]
    assert uploaded == {"type": "file_upload", "name": "cover.jpg", "mime_type": "image/jpeg", "file_upload": {"id": "upload-2"}}


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
