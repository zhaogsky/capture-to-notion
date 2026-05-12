from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from capture_to_notion.notion_adapter import NotionNotFoundError
from capture_to_notion.schema import semantic_field_mapping

UrlChecker = Callable[[str], bool]


def _request_url_is_accessible(url: str, method: str) -> bool | None:
    headers = {"Range": "bytes=0-0"} if method == "GET" else {}
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        if method == "HEAD" and exc.code in {403, 405}:
            return None
        return False
    except (OSError, ValueError):
        return False


def url_is_accessible(url: str) -> bool:
    head_result = _request_url_is_accessible(url, "HEAD")
    if head_result is not None:
        return head_result
    return bool(_request_url_is_accessible(url, "GET"))


def _page_property_schema(properties: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = {}
    for name, property_data in properties.items():
        if isinstance(property_data, dict) and property_data.get("type"):
            schema[name] = {"name": name, "type": property_data["type"]}
    return schema


def _check_property(mapping: dict[str, str], semantic_key: str) -> dict[str, Any]:
    property_name = mapping.get(semantic_key)
    if property_name:
        return {"status": "present", "property": property_name}
    return {"status": "missing"}


def _property_has_value(property_data: Any) -> bool:
    if not isinstance(property_data, dict):
        return False
    property_type = property_data.get("type")
    if not isinstance(property_type, str):
        return False
    value = property_data.get(property_type)
    return value not in (None, "", [], {})


def _check_property_value(properties: dict[str, Any], mapping: dict[str, str], semantic_key: str) -> dict[str, Any]:
    property_name = mapping.get(semantic_key)
    if not property_name:
        return {"status": "missing"}
    if _property_has_value(properties.get(property_name)):
        return {"status": "present", "property": property_name}
    return {"status": "missing", "property": property_name}


def _check_author_relation(properties: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    property_name = mapping.get("author")
    if not property_name:
        return {"status": "missing"}
    property_data = properties.get(property_name)
    if not isinstance(property_data, dict) or property_data.get("type") != "relation":
        return {"status": "missing", "property": property_name}
    relation = property_data.get("relation")
    if isinstance(relation, list) and relation:
        return {"status": "present", "property": property_name}
    return {"status": "missing", "property": property_name}


def _file_item_url(file_item: dict[str, Any]) -> str | None:
    file_type = file_item.get("type")
    if file_type == "external" and isinstance(file_item.get("external"), dict):
        url = file_item["external"].get("url")
    elif file_type == "file" and isinstance(file_item.get("file"), dict):
        url = file_item["file"].get("url")
    else:
        url = None
    if isinstance(url, str) and url:
        return url
    return None


def _file_urls(files: Any) -> list[str]:
    if not isinstance(files, list):
        return []
    return [url for file_item in files if isinstance(file_item, dict) if (url := _file_item_url(file_item))]


def _all_urls_accessible(urls: list[str], url_checker: UrlChecker | None) -> bool:
    if url_checker is None:
        return bool(urls)
    return all(url_checker(url) for url in urls)


def _check_cover_files(
    properties: dict[str, Any], mapping: dict[str, str], url_checker: UrlChecker | None
) -> dict[str, Any]:
    property_name = mapping.get("cover")
    if not property_name:
        return {"status": "missing"}
    property_data = properties.get(property_name, {})
    urls = _file_urls(property_data.get("files") if isinstance(property_data, dict) else None)
    if not urls:
        return {"status": "missing", "property": property_name}
    if _all_urls_accessible(urls, url_checker):
        return {"status": "present", "property": property_name}
    return {"status": "inaccessible", "property": property_name}


def _cover_url(cover: Any) -> str | None:
    if not isinstance(cover, dict):
        return None
    cover_type = cover.get("type")
    if cover_type == "external" and isinstance(cover.get("external"), dict):
        url = cover["external"].get("url")
    elif cover_type == "file" and isinstance(cover.get("file"), dict):
        url = cover["file"].get("url")
    else:
        url = None
    if isinstance(url, str) and url:
        return url
    return None


def _check_page_cover(page: dict[str, Any], url_checker: UrlChecker | None) -> dict[str, Any]:
    url = _cover_url(page.get("cover"))
    if not url:
        return {"status": "missing"}
    if url_checker is None or url_checker(url):
        return {"status": "present"}
    return {"status": "inaccessible"}


def _status(check: dict[str, Any]) -> str:
    return str(check.get("status", "missing"))


def _warning_for_check(name: str, check: dict[str, Any]) -> str | None:
    status = _status(check)
    if status == "present":
        return None
    if status == "inaccessible":
        return f"inaccessible:{name}"
    return f"missing:{name}"


def verify_capture_page(page_id: str, adapter: Any, url_checker: UrlChecker | None = None) -> dict[str, Any]:
    try:
        page = adapter.retrieve_page(page_id)
    except NotionNotFoundError:
        checks = {
            "page": {"status": "missing"},
            "title_property": {"status": "missing"},
            "status_property": {"status": "missing"},
            "isbn_property": {"status": "missing"},
            "page_count_property": {"status": "missing"},
            "author_relation_property": {"status": "missing"},
            "cover_files_property": {"status": "missing"},
            "page_cover": {"status": "missing"},
        }
        return {
            "page_id": page_id,
            "verified": False,
            "checks": checks,
            "warnings": [f"missing:{name}" for name in checks],
        }

    properties = page.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    mapping = semantic_field_mapping(_page_property_schema(properties)).get("fields", {})
    checks = {
        "page": {"status": "present" if page.get("object") == "page" else "missing"},
        "title_property": _check_property(mapping, "title"),
        "status_property": _check_property(mapping, "state"),
        "isbn_property": _check_property_value(properties, mapping, "isbn"),
        "page_count_property": _check_property_value(properties, mapping, "page_count"),
        "author_relation_property": _check_author_relation(properties, mapping),
        "cover_files_property": _check_cover_files(properties, mapping, url_checker),
        "page_cover": _check_page_cover(page, url_checker),
    }
    warnings = [warning for name, check in checks.items() if (warning := _warning_for_check(name, check))]
    return {
        "page_id": page_id,
        "verified": not warnings,
        "checks": checks,
        "warnings": warnings,
    }
