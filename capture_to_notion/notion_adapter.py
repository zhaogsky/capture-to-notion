from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

from capture_to_notion.config import AppConfig
from capture_to_notion.view_utils import remap_view_property_references


class NotionApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.body = body


class NotionAuthError(NotionApiError):
    pass


class NotionPermissionError(NotionApiError):
    pass


class NotionNotFoundError(NotionApiError):
    pass


class NotionRateLimitError(NotionApiError):
    pass


T = TypeVar("T")


def _config_data(config: AppConfig) -> dict[str, Any]:
    try:
        data = json.loads(config.config_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    return data if isinstance(data, dict) else {}


def notion_token(config: AppConfig) -> str:
    data = _config_data(config)
    auth_config = data.get("notion", {}).get("auth", {})
    config_token = auth_config.get("token")
    if config_token:
        return config_token

    env_token_name = auth_config.get("env_token_name", "NOTION_TOKEN")
    token = os.environ.get(env_token_name)
    if not token:
        raise NotionAuthError(f"Notion token environment variable is not set: {env_token_name}")
    return token


def notion_api_version(config: AppConfig) -> str:
    data = _config_data(config)
    version = data.get("notion", {}).get("api_version")
    return str(version) if version else "2026-03-11"


def _view_source_schema(view: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("source_schema", "_source_schema"):
        source_schema = view.get(key)
        if isinstance(source_schema, dict):
            return source_schema
    return None


def _plain_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text = "".join(
            str(item.get("plain_text") or item.get("text", {}).get("content") or "")
            for item in value
            if isinstance(item, dict)
        ).strip()
        return text or None
    if isinstance(value, dict):
        prop_type = value.get("type")
        if prop_type and prop_type in value:
            return _plain_text(value.get(prop_type))
        for key in ("title", "rich_text"):
            if key in value:
                return _plain_text(value[key])
    return None


def _find_title_property_name(properties: dict[str, Any]) -> str | None:
    for property_name, property_data in properties.items():
        if isinstance(property_data, dict) and property_data.get("type") == "title":
            return property_name
    return None


def _first_data_source_id(database: dict[str, Any]) -> str | None:
    data_sources = database.get("data_sources")
    if not isinstance(data_sources, list) or not data_sources:
        return None
    first = data_sources[0]
    if not isinstance(first, dict):
        return None
    data_source_id = first.get("id")
    return str(data_source_id) if data_source_id else None


def _result_title(result: dict[str, Any]) -> str | None:
    title = _plain_text(result.get("title"))
    if title:
        return title
    properties = result.get("properties", {})
    if isinstance(properties, dict):
        title_property_name = _find_title_property_name(properties)
        if title_property_name:
            title = _plain_text(properties.get(title_property_name))
            if title:
                return title
    return result.get("name")


def _convert_api_error(exc: Exception) -> Exception:
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    body = getattr(exc, "body", None)
    message = str(exc)
    if code == "unauthorized" or status == 401:
        return NotionAuthError(message, status=status, code=code, body=body)
    if code == "restricted_resource" or status == 403:
        return NotionPermissionError(message, status=status, code=code, body=body)
    if code == "object_not_found" or status == 404:
        return NotionNotFoundError(message, status=status, code=code, body=body)
    if code == "rate_limited" or status == 429:
        return NotionRateLimitError(message, status=status, code=code, body=body)
    return NotionApiError(message, status=status, code=code, body=body)


class NotionAdapter:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_config(cls, config: AppConfig) -> "NotionAdapter":
        try:
            from notion_client import Client
        except ImportError as exc:
            raise NotionApiError("notion-client package is not installed") from exc
        return cls(Client(auth=notion_token(config), notion_version=notion_api_version(config)))

    def _call(self, func: Callable[..., T], **kwargs: Any) -> T:
        try:
            return func(**kwargs)
        except (NotionAuthError, NotionPermissionError, NotionNotFoundError, NotionRateLimitError, NotionApiError):
            raise
        except Exception as exc:
            raise _convert_api_error(exc) from exc

    def _query_all(self, func: Callable[..., dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            page_kwargs = dict(kwargs)
            if start_cursor:
                page_kwargs["start_cursor"] = start_cursor
            response = self._call(func, **page_kwargs)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            start_cursor = response.get("next_cursor")

    def _request(
        self,
        path: str,
        method: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.client.request(path, method, query=query, body=body)
        except (NotionAuthError, NotionPermissionError, NotionNotFoundError, NotionRateLimitError, NotionApiError):
            raise
        except Exception as exc:
            raise _convert_api_error(exc) from exc

    def search(
        self,
        query: str,
        limit: int | None = None,
        include_parent_path: bool = True,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"query": query}
        if limit is not None:
            kwargs["page_size"] = limit
        response = self._call(self.client.search, **kwargs)
        return [
            self._simplify_search_result(result, include_parent_path=include_parent_path)
            for result in response.get("results", [])
        ]

    def get_current_user(self) -> dict[str, Any]:
        return self._call(self.client.users.me)

    def list_users(self) -> list[dict[str, Any]]:
        return self._query_all(self.client.users.list)

    def search_users(self, query: str) -> list[dict[str, Any]]:
        normalized_query = query.casefold()
        return [
            user
            for user in self.list_users()
            if normalized_query in str(user.get("name", "")).casefold()
            or normalized_query in str(user.get("person", {}).get("email", "")).casefold()
        ]

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._call(self.client.pages.retrieve, page_id=page_id)

    def archive_page(self, page_id: str) -> dict[str, Any]:
        return self._call(self.client.pages.update, page_id=page_id, in_trash=True)

    def move_page(self, page_id: str, parent: dict[str, Any]) -> dict[str, Any]:
        return self._call(self.client.pages.move, page_id=page_id, parent=parent)

    def retrieve_page_property(self, page_id: str, property_id: str) -> dict[str, Any]:
        return self._call(self.client.pages.properties.retrieve, page_id=page_id, property_id=property_id)

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._call(self.client.databases.retrieve, database_id=database_id)

    def update_database(self, database_id: str, **payload: Any) -> dict[str, Any]:
        return self._call(self.client.databases.update, database_id=database_id, **payload)

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._call(self.client.data_sources.retrieve, data_source_id=data_source_id)

    def create_data_source(self, parent: dict[str, Any], title: str, properties: dict[str, Any]) -> dict[str, Any]:
        rich_title = [{"type": "text", "text": {"content": title}}]
        return self._call(self.client.data_sources.create, parent=parent, title=rich_title, properties=properties)

    def list_data_source_templates(self, data_source_id: str) -> list[dict[str, Any]]:
        response = self._call(self.client.data_sources.list_templates, data_source_id=data_source_id)
        return response.get("results", [])

    def query_database(self, database_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"database_id": database_id}
        if filters is not None:
            kwargs["filter"] = filters
        return self._query_all(self.client.databases.query, **kwargs)

    def query_data_source(self, data_source_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"data_source_id": data_source_id}
        if filters is not None:
            kwargs["filter"] = filters
        return self._query_all(self.client.data_sources.query, **kwargs)

    def query_database_title_exact(
        self,
        database_id: str,
        title: str,
        data_source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if data_source_id:
            data_source = self.retrieve_data_source(data_source_id)
            properties = data_source.get("properties", {})
            if not isinstance(properties, dict):
                properties = {}
            title_property_name = _find_title_property_name(properties)
            if title_property_name is None:
                raise NotionApiError(f"Data source has no title property: {data_source_id}")
            return self.query_data_source(
                data_source_id,
                filters={"property": title_property_name, "title": {"equals": title}},
            )

        database = self.retrieve_database(database_id)
        target_data_source_id = _first_data_source_id(database)
        if target_data_source_id:
            data_source = self.retrieve_data_source(target_data_source_id)
            properties = data_source.get("properties", {})
            if not isinstance(properties, dict):
                properties = {}
            title_property_name = _find_title_property_name(properties)
            if title_property_name is None:
                raise NotionApiError(f"Data source has no title property: {target_data_source_id}")
            return self.query_data_source(
                target_data_source_id,
                filters={"property": title_property_name, "title": {"equals": title}},
            )

        properties = database.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        title_property_name = _find_title_property_name(properties)
        if title_property_name is None:
            raise NotionApiError(f"Database has no title property: {database_id}")
        return self.query_database(
            database_id,
            filters={"property": title_property_name, "title": {"equals": title}},
        )

    def create_relation_target_page(
        self,
        database_id: str,
        title: str,
        data_source_id: str | None = None,
        extra_properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_data_source_id = data_source_id
        if target_data_source_id is None:
            database = self.retrieve_database(database_id)
            target_data_source_id = _first_data_source_id(database)
        if target_data_source_id is None:
            raise NotionApiError(f"Database has no data source: {database_id}")

        data_source = self.retrieve_data_source(target_data_source_id)
        properties = data_source.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        title_property_name = _find_title_property_name(properties)
        if title_property_name is None:
            raise NotionApiError(f"Data source has no title property: {target_data_source_id}")

        page_properties = dict(extra_properties or {})
        page_properties[title_property_name] = {"title": [{"text": {"content": title}}]}
        return self.create_page(target_data_source_id, page_properties)

    def upload_file(self, path: Path, name: str, mime_type: str) -> dict[str, Any]:
        upload = self._call(
            self.client.file_uploads.create,
            mode="single_part",
            filename=name,
            content_type=mime_type,
        )
        upload_id = upload["id"]
        with path.open("rb") as file_obj:
            self._call(self.client.file_uploads.send, file_upload_id=upload_id, file=file_obj)
        return {
            "type": "file_upload",
            "name": name,
            "mime_type": mime_type,
            "file_upload": {"id": upload_id},
        }

    def upload_file_for_property(self, path: Path, name: str, mime_type: str) -> dict[str, Any] | None:
        return self.upload_file(path, name, mime_type)

    def retrieve_file_upload(self, file_upload_id: str) -> dict[str, Any]:
        return self._call(self.client.file_uploads.retrieve, file_upload_id=file_upload_id)

    def list_file_uploads(self) -> list[dict[str, Any]]:
        response = self._call(self.client.file_uploads.list)
        return response.get("results", [])

    def complete_file_upload(self, file_upload_id: str) -> dict[str, Any]:
        return self._call(self.client.file_uploads.complete, file_upload_id=file_upload_id)

    def create_database(
        self,
        page_id: str,
        title: str,
        properties: dict[str, Any],
        views: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rich_title = [{"type": "text", "text": {"content": title}}]
        database = self._call(
            self.client.databases.create,
            parent={"type": "page_id", "page_id": page_id},
            title=rich_title,
            initial_data_source={"title": rich_title, "properties": properties},
        )
        if views:
            data_sources = database.get("data_sources")
            data_source_id = None
            if isinstance(data_sources, list) and data_sources and isinstance(data_sources[0], dict):
                data_source_id = data_sources[0].get("id")
            if not isinstance(data_source_id, str) or not data_source_id:
                raise NotionApiError("create_database response missing data_sources[0].id for view creation")
            database_id = database.get("id")
            if not isinstance(database_id, str) or not database_id:
                raise NotionApiError("create_database response missing id for view creation")
            target_properties = properties
            views_need_remap = any(
                _view_source_schema(view) is not None
                for view in views
            )
            if views_need_remap:
                data_source = self.retrieve_data_source(data_source_id)
                retrieved_properties = data_source.get("properties")
                target_properties = retrieved_properties if isinstance(retrieved_properties, dict) else {}
            created_views = []
            for view in views:
                source_schema = _view_source_schema(view)
                view_for_create = (
                    remap_view_property_references(view, source_schema, target_properties)
                    if source_schema is not None
                    else view
                )
                created_views.append(
                    self.create_view(
                        data_source_id=data_source_id,
                        database_id=database_id,
                        name=view_for_create["name"],
                        view_type=view_for_create["type"],
                        filter=view_for_create.get("filter"),
                        sorts=view_for_create.get("sorts"),
                        quick_filters=view_for_create.get("quick_filters"),
                        configuration=view_for_create.get("configuration"),
                    )
                )
            database["created_views"] = created_views
        return database

    def update_data_source(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._call(self.client.data_sources.update, data_source_id=data_source_id, properties=properties)

    def list_views(self, data_source_id: str | None = None, database_id: str | None = None) -> list[dict[str, Any]]:
        if data_source_id == "" or database_id == "":
            raise NotionApiError("list_views requires non-empty database_id or data_source_id")
        scopes = [scope for scope in (database_id, data_source_id) if scope]
        if len(scopes) != 1:
            raise NotionApiError("list_views requires exactly one of database_id or data_source_id")
        query: dict[str, Any] = {}
        if data_source_id:
            query["data_source_id"] = data_source_id
        if database_id:
            query["database_id"] = database_id

        results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            page_query = dict(query)
            if start_cursor:
                page_query["start_cursor"] = start_cursor
            response = self._request("/views", "GET", query=page_query)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            start_cursor = response.get("next_cursor")

    def retrieve_view(self, view_id: str) -> dict[str, Any]:
        return self._request(f"/views/{view_id}", "GET")

    def create_view(
        self,
        *,
        data_source_id: str,
        name: str,
        view_type: str,
        database_id: str | None = None,
        view_id: str | None = None,
        create_database: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        quick_filters: dict[str, Any] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not data_source_id:
            raise NotionApiError("create_view requires non-empty data_source_id")
        if not name:
            raise NotionApiError("create_view requires non-empty name")
        if not view_type:
            raise NotionApiError("create_view requires non-empty view_type")
        parent_scopes = [
            scope
            for scope in (
                database_id if database_id else None,
                view_id if view_id else None,
                create_database if create_database is not None else None,
            )
            if scope is not None
        ]
        if len(parent_scopes) != 1:
            raise NotionApiError("create_view requires exactly one of database_id, view_id, or create_database")
        body: dict[str, Any] = {"data_source_id": data_source_id, "name": name, "type": view_type}
        if database_id:
            body["database_id"] = database_id
        if view_id:
            body["view_id"] = view_id
        if create_database is not None:
            body["create_database"] = create_database
        if filter is not None:
            body["filter"] = filter
        if sorts is not None:
            body["sorts"] = sorts
        if quick_filters is not None:
            body["quick_filters"] = quick_filters
        if configuration is not None:
            body["configuration"] = configuration
        return self._request("/views", "POST", body=body)

    def update_view(self, view_id: str, **options: Any) -> dict[str, Any]:
        return self._request(f"/views/{view_id}", "PATCH", body=options)

    def delete_view(self, view_id: str) -> dict[str, Any]:
        return self._request(f"/views/{view_id}", "DELETE")

    def retrieve_block(self, block_id: str) -> dict[str, Any]:
        return self._call(self.client.blocks.retrieve, block_id=block_id)

    def update_block(self, block_id: str, **payload: Any) -> dict[str, Any]:
        return self._call(self.client.blocks.update, block_id=block_id, **payload)

    def delete_block(self, block_id: str) -> dict[str, Any]:
        return self._call(self.client.blocks.delete, block_id=block_id)

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {"block_id": block_id, "page_size": 100}
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = self._call(self.client.blocks.children.list, **kwargs)
            results.extend(response.get("results", []))
            if not response.get("has_more"):
                return results
            start_cursor = response.get("next_cursor")

    def append_block_children(self, block_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
        return self._call(self.client.blocks.children.append, block_id=block_id, children=children)

    def _normalize_cover(self, cover: Any, cover_source_url: str | None = None) -> Any:
        if isinstance(cover, dict) and cover.get("type") == "file_upload":
            if not cover_source_url:
                raise NotionApiError("Page cover file_upload requires cover_source_url")
            return {"type": "external", "external": {"url": cover_source_url}}
        return cover

    def create_page(
        self,
        data_source_id: str,
        properties: dict[str, Any],
        icon: Any = None,
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "parent": {"data_source_id": data_source_id},
            "properties": properties,
        }
        if icon is not None:
            kwargs["icon"] = icon
        if cover is not None:
            kwargs["cover"] = self._normalize_cover(cover, cover_source_url)
        return self._call(self.client.pages.create, **kwargs)

    def create_child_page(
        self,
        parent_page_id: str,
        title: str,
        children: list[dict[str, Any]] | None = None,
        icon: Any = None,
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
        }
        if children:
            kwargs["children"] = children
        if icon is not None:
            kwargs["icon"] = icon
        if cover is not None:
            kwargs["cover"] = self._normalize_cover(cover, cover_source_url)
        return self._call(self.client.pages.create, **kwargs)

    def update_page(
        self,
        page_id: str,
        properties: dict[str, Any],
        cover: Any = None,
        cover_source_url: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"page_id": page_id, "properties": properties}
        if cover is not None:
            kwargs["cover"] = self._normalize_cover(cover, cover_source_url)
        return self._call(self.client.pages.update, **kwargs)

    def _parent_path_info(self, parent: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(parent, dict):
            return {"path": None, "path_complete": False}

        labels: list[str] = []
        seen: set[str] = set()
        current = parent
        while isinstance(current, dict):
            parent_type = current.get("type")
            if parent_type == "workspace":
                return {"path": " / ".join(reversed(labels)) if labels else "工作区顶层", "path_complete": True}
            if parent_type == "page_id":
                parent_id = current.get("page_id")
                if not parent_id or parent_id in seen:
                    break
                seen.add(parent_id)
                page = self.retrieve_page(parent_id)
                labels.append(_result_title(page) or parent_id)
                current = page.get("parent")
                continue
            if parent_type == "database_id":
                database_id = current.get("database_id")
                if not database_id or database_id in seen:
                    break
                seen.add(database_id)
                database = self.retrieve_database(database_id)
                labels.append(_result_title(database) or database_id)
                current = database.get("parent")
                continue
            break

        return {"path": " / ".join(reversed(labels)) if labels else None, "path_complete": False}

    def _parent_path(self, parent: dict[str, Any] | None) -> str | None:
        path = self._parent_path_info(parent).get("path")
        return path if isinstance(path, str) else None

    def _simplify_search_result(self, result: dict[str, Any], include_parent_path: bool = True) -> dict[str, Any]:
        simplified = {
            "id": result.get("id"),
            "object": result.get("object"),
            "title": _result_title(result),
            "url": result.get("url"),
            "last_edited_time": result.get("last_edited_time"),
        }
        if include_parent_path:
            parent_path_info = self._parent_path_info(result.get("parent"))
            parent_path = parent_path_info.get("path")
            if isinstance(parent_path, str) and parent_path:
                simplified["parent_path"] = parent_path
                title = simplified.get("title")
                simplified["path"] = f"{parent_path} / {title}" if title else parent_path
                simplified["path_complete"] = bool(parent_path_info.get("path_complete"))
        return simplified
