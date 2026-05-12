from __future__ import annotations

from typing import Any

from capture_to_notion.notion_adapter import NotionNotFoundError
from capture_to_notion.schema import semantic_field_mapping


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


def _has_file_or_external(files: Any) -> bool:
    if not isinstance(files, list):
        return False
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        file_type = file_item.get("type")
        if file_type == "external" and isinstance(file_item.get("external"), dict):
            if file_item["external"].get("url"):
                return True
        if file_type == "file" and isinstance(file_item.get("file"), dict):
            if file_item["file"].get("url"):
                return True
    return False


def _check_cover_files(properties: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    property_name = mapping.get("cover")
    if not property_name:
        return {"status": "missing"}
    property_data = properties.get(property_name, {})
    if isinstance(property_data, dict) and _has_file_or_external(property_data.get("files")):
        return {"status": "present", "property": property_name}
    return {"status": "missing", "property": property_name}


def _status(check: dict[str, Any]) -> str:
    return str(check.get("status", "missing"))


def verify_capture_page(page_id: str, adapter: Any) -> dict[str, Any]:
    try:
        page = adapter.retrieve_page(page_id)
    except NotionNotFoundError:
        checks = {
            "page": {"status": "missing"},
            "title_property": {"status": "missing"},
            "status_property": {"status": "missing"},
            "isbn_property": {"status": "missing"},
            "page_count_property": {"status": "missing"},
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
        "cover_files_property": _check_cover_files(properties, mapping),
        "page_cover": {"status": "present" if page.get("cover") else "missing"},
    }
    warnings = [f"missing:{name}" for name, check in checks.items() if _status(check) != "present"]
    return {
        "page_id": page_id,
        "verified": not warnings,
        "checks": checks,
        "warnings": warnings,
    }
