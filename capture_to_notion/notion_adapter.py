from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

from capture_to_notion.config import AppConfig


class NotionAuthError(Exception):
    pass


class NotionPermissionError(Exception):
    pass


class NotionNotFoundError(Exception):
    pass


class NotionRateLimitError(Exception):
    pass


class NotionApiError(Exception):
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


def _result_title(result: dict[str, Any]) -> str | None:
    title = _plain_text(result.get("title"))
    if title:
        return title
    properties = result.get("properties", {})
    if isinstance(properties, dict):
        for property_data in properties.values():
            if isinstance(property_data, dict) and property_data.get("type") == "title":
                title = _plain_text(property_data)
                if title:
                    return title
    return result.get("name")


def _convert_api_error(exc: Exception) -> Exception:
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    message = str(exc)
    if code == "unauthorized" or status == 401:
        return NotionAuthError(message)
    if code == "restricted_resource" or status == 403:
        return NotionPermissionError(message)
    if code == "object_not_found" or status == 404:
        return NotionNotFoundError(message)
    if code == "rate_limited" or status == 429:
        return NotionRateLimitError(message)
    return NotionApiError(message)


class NotionAdapter:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_config(cls, config: AppConfig) -> "NotionAdapter":
        try:
            from notion_client import Client
        except ImportError as exc:
            raise NotionApiError("notion-client package is not installed") from exc
        return cls(Client(auth=notion_token(config)))

    def _call(self, func: Callable[..., T], **kwargs: Any) -> T:
        try:
            return func(**kwargs)
        except (NotionAuthError, NotionPermissionError, NotionNotFoundError, NotionRateLimitError, NotionApiError):
            raise
        except Exception as exc:
            raise _convert_api_error(exc) from exc

    def search(self, query: str) -> list[dict[str, Any]]:
        response = self._call(self.client.search, query=query)
        return [self._simplify_search_result(result) for result in response.get("results", [])]

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._call(self.client.pages.retrieve, page_id=page_id)

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._call(self.client.databases.retrieve, database_id=database_id)

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self._call(self.client.data_sources.retrieve, data_source_id=data_source_id)

    def query_database(self, database_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"database_id": database_id}
        if filters is not None:
            kwargs["filter"] = filters
        response = self._call(self.client.databases.query, **kwargs)
        return response.get("results", [])

    def query_database_title_exact(self, database_id: str, title: str) -> list[dict[str, Any]]:
        database = self.retrieve_database(database_id)
        properties = database.get("properties", {})
        title_property_name = None
        if isinstance(properties, dict):
            for property_name, property_data in properties.items():
                if isinstance(property_data, dict) and property_data.get("type") == "title":
                    title_property_name = property_name
                    break
        if title_property_name is None:
            raise NotionApiError(f"Database has no title property: {database_id}")
        return self.query_database(
            database_id,
            filters={"property": title_property_name, "title": {"equals": title}},
        )

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
            "file_upload": {"id": upload_id},
        }

    def upload_file_for_property(self, path: Path, name: str, mime_type: str) -> dict[str, Any] | None:
        return self.upload_file(path, name, mime_type)

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

    def _parent_path(self, parent: dict[str, Any] | None) -> str | None:
        if not isinstance(parent, dict):
            return None

        labels: list[str] = []
        seen: set[str] = set()
        current = parent
        while isinstance(current, dict):
            parent_type = current.get("type")
            if parent_type == "workspace":
                return " / ".join(reversed(labels)) if labels else "工作区顶层"
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

        return " / ".join(reversed(labels)) if labels else None

    def _simplify_search_result(self, result: dict[str, Any]) -> dict[str, Any]:
        simplified = {
            "id": result.get("id"),
            "object": result.get("object"),
            "title": _result_title(result),
            "url": result.get("url"),
            "last_edited_time": result.get("last_edited_time"),
        }
        parent_path = self._parent_path(result.get("parent"))
        if parent_path:
            simplified["parent_path"] = parent_path
        return simplified
