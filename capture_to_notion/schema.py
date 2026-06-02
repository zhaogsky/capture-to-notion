from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

SCHEMA_PROPERTY_TYPES = {
    "button",
    "checkbox",
    "created_by",
    "created_time",
    "date",
    "email",
    "files",
    "formula",
    "last_edited_by",
    "last_edited_time",
    "multi_select",
    "number",
    "people",
    "phone_number",
    "place",
    "relation",
    "rich_text",
    "rollup",
    "select",
    "status",
    "title",
    "unique_id",
    "url",
}

PAGE_PROPERTY_VALUE_TYPES = SCHEMA_PROPERTY_TYPES | {"verification"}
SUPPORTED_TYPES = SCHEMA_PROPERTY_TYPES

def plain_title(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("plain_text") or item.get("text", {}).get("content") or ""))
        title = "".join(parts).strip()
        return title or None
    return None


def normalize_property(name: str, property_data: dict[str, Any]) -> dict[str, Any] | None:
    property_type = property_data.get("type")
    if property_type not in SUPPORTED_TYPES:
        return None
    type_data = property_data.get(property_type)
    if not isinstance(type_data, dict):
        return None

    normalized: dict[str, Any] = {
        "name": name,
        "id": property_data.get("id"),
        "type": property_type,
    }

    if property_type in {"status", "select", "multi_select"}:
        normalized["options"] = [
            {"name": option.get("name"), "color": option.get("color")}
            for option in type_data.get("options", [])
            if isinstance(option, dict)
        ]

    if property_type == "relation":
        normalized["target_database_id"] = type_data.get("database_id")
        if type_data.get("data_source_id"):
            normalized["target_data_source_id"] = type_data.get("data_source_id")

    return normalized


def normalize_database_schema(database: dict[str, Any]) -> dict[str, dict[str, Any]]:
    properties = database
    if not all(isinstance(value, dict) and isinstance(value.get("type"), str) for value in database.values()):
        wrapped_properties = database.get("properties")
        properties = wrapped_properties if isinstance(wrapped_properties, dict) else {}
    normalized = {}
    for name in sorted(properties):
        property_data = properties[name]
        if not isinstance(property_data, dict):
            continue
        normalized_property = normalize_property(name, property_data)
        if normalized_property is not None:
            normalized[name] = normalized_property
    return normalized


def schema_hash(schema: dict[str, Any]) -> str:
    payload = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]



def confirmation_blocking_warnings(
    warnings: list[str] | None,
    non_blocking_prefixes: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    prefixes = tuple(non_blocking_prefixes or ())
    return [
        warning
        for warning in (warnings or [])
        if not warning.startswith(prefixes)
    ]




def _is_clear_directive(value: Any) -> bool:
    return isinstance(value, dict) and value.get("$clear") is True


def _is_empty_property_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or (value == {} and not _is_clear_directive(value))


def property_has_value(property_data: Any) -> bool:
    if not isinstance(property_data, dict):
        return False
    property_type = property_data.get("type")
    if property_type not in PAGE_PROPERTY_VALUE_TYPES:
        return False
    value = property_data.get(property_type)
    return not _is_empty_property_value(value)


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


def file_urls_from_property(property_data: Any) -> list[str]:
    if not isinstance(property_data, dict) or property_data.get("type") != "files":
        return []
    files = property_data.get("files")
    if not isinstance(files, list):
        return []
    return [url for file_item in files if isinstance(file_item, dict) if (url := _file_item_url(file_item))]


def cover_url_from_page(page: dict[str, Any]) -> str | None:
    cover = page.get("cover")
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


def resolve_field_mapping(
    schema: dict[str, dict[str, Any]],
    *,
    cached_fields: dict[str, str] | None = None,
    explicit_mapping: dict[str, str] | None = None,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for source in (cached_fields or {}, explicit_mapping or {}):
        for record_key, property_name in source.items():
            if isinstance(record_key, str) and isinstance(property_name, str) and property_name in schema:
                resolved[record_key] = property_name
    return resolved


def _text_content(value: Any) -> str:
    return str(value)


def _build_title_property(value: Any) -> dict[str, Any]:
    return {"title": [{"text": {"content": _text_content(value)}}]}


def _build_rich_text_property(value: Any) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": _text_content(value)}}]}


def _build_select_property(value: Any) -> dict[str, Any]:
    return {"select": {"name": _text_content(value)}}


def _build_status_property(value: Any) -> dict[str, Any]:
    return {"status": {"name": _text_content(value)}}


def _build_multi_select_property(value: Any) -> dict[str, Any] | None:
    values = value if isinstance(value, list) else [value]
    options = [{"name": _text_content(item)} for item in values if item is not None and item != ""]
    if not options:
        return None
    return {"multi_select": options}


def _build_url_property(value: Any) -> dict[str, Any]:
    return {"url": _text_content(value)}


def _build_number_property(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        stripped = value.strip()
        if re.fullmatch(r"[-+]?\d+", stripped):
            value = int(stripped)
    return {"number": value}


def _build_checkbox_property(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"是", "对", "真", "已", "已完成", "true", "yes", "y", "1"}:
            return {"checkbox": True}
        if normalized in {"否", "不", "假", "未", "未完成", "false", "no", "n", "0"}:
            return {"checkbox": False}
    return {"checkbox": bool(value)}


def _build_email_property(value: Any) -> dict[str, Any]:
    return {"email": _text_content(value)}


def _build_phone_number_property(value: Any) -> dict[str, Any]:
    return {"phone_number": _text_content(value)}


def _build_people_property(value: Any) -> dict[str, Any] | None:
    people_ids = value if isinstance(value, list) else [value]
    people = [{"id": str(user_id)} for user_id in people_ids if user_id]
    if not people:
        return None
    return {"people": people}


def _build_date_property(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        start = value.get("start")
        if not start:
            return None
        date_value: dict[str, Any] = {"start": _text_content(start)}
        end = value.get("end")
        if end:
            date_value["end"] = _text_content(end)
        return {"date": date_value}
    return {"date": {"start": _text_content(value)}}


def _file_name_from_url(url: str) -> str:
    path_name = urlparse(url).path.rsplit("/", 1)[-1]
    return path_name or "external-file"


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_prebuilt_file_object(value: dict[str, Any]) -> dict[str, Any] | None:
    name = value.get("name")
    if not isinstance(name, str) or not name:
        return None

    file_type = value.get("type")
    if file_type == "external":
        external = value.get("external")
        if not isinstance(external, dict) or not _is_http_url(external.get("url")):
            return None
        return value

    if file_type == "file":
        file_data = value.get("file")
        if not isinstance(file_data, dict) or not _is_http_url(file_data.get("url")):
            return None
        return value

    if file_type == "file_upload":
        file_upload = value.get("file_upload")
        upload_id = file_upload.get("id") if isinstance(file_upload, dict) else None
        if not upload_id:
            return None
        return {"type": "file_upload", "name": name, "file_upload": {"id": upload_id}}

    return None


def _external_file_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        prebuilt_file_object = _safe_prebuilt_file_object(value)
        if prebuilt_file_object is not None:
            return prebuilt_file_object
        url = value.get("url")
        if not _is_http_url(url):
            return None
        name = value.get("name") or _file_name_from_url(url)
        return {"type": "external", "name": str(name), "external": {"url": url}}
    if not _is_http_url(value):
        return None
    return {"type": "external", "name": _file_name_from_url(value), "external": {"url": value}}


def _build_files_property(value: Any) -> dict[str, Any] | None:
    values = value if isinstance(value, list) else [value]
    files = []
    for item in values:
        file_object = _external_file_object(item)
        if file_object is not None:
            files.append(file_object)
    if not files:
        return None
    return {"files": files}


def _build_relation_property(value: Any) -> dict[str, Any] | None:
    relation_ids = value if isinstance(value, list) else [value]
    relations = [{"id": str(page_id)} for page_id in relation_ids if page_id]
    if not relations:
        return None
    return {"relation": relations}


PROPERTY_TYPE_BUILDERS = {
    "checkbox": _build_checkbox_property,
    "date": _build_date_property,
    "email": _build_email_property,
    "files": _build_files_property,
    "multi_select": _build_multi_select_property,
    "number": _build_number_property,
    "people": _build_people_property,
    "phone_number": _build_phone_number_property,
    "relation": _build_relation_property,
    "rich_text": _build_rich_text_property,
    "select": _build_select_property,
    "status": _build_status_property,
    "title": _build_title_property,
    "url": _build_url_property,
}
WRITABLE_PROPERTY_TYPES = set(PROPERTY_TYPE_BUILDERS)
READONLY_PROPERTY_TYPES = SCHEMA_PROPERTY_TYPES - WRITABLE_PROPERTY_TYPES


def _clear_property_value(property_type: str) -> dict[str, Any] | None:
    if property_type in {"files", "multi_select", "people", "relation", "rich_text"}:
        return {property_type: []}
    if property_type in {"date", "email", "number", "phone_number", "select", "status", "url"}:
        return {property_type: None}
    if property_type == "checkbox":
        return {"checkbox": False}
    return None


def _raw_notion_property_value(property_type: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    payload = value.get("$notion")
    if not isinstance(payload, dict):
        return None
    if set(payload) != {property_type}:
        return None
    return payload


def _build_property_value(property_type: str, value: Any) -> dict[str, Any] | None:
    raw_value = _raw_notion_property_value(property_type, value)
    if raw_value is not None:
        return raw_value
    if _is_clear_directive(value):
        return _clear_property_value(property_type)
    builder = PROPERTY_TYPE_BUILDERS.get(property_type)
    if builder is None:
        return None
    return builder(value)


def build_properties(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for key, value in record.items():
        if _is_empty_property_value(value):
            continue
        notion_name = field_mapping.get(key)
        if not notion_name:
            continue
        property_schema = schema.get(notion_name)
        if not property_schema:
            continue
        property_value = _build_property_value(property_schema.get("type", ""), value)
        if property_value is not None:
            properties[notion_name] = property_value
    return properties
