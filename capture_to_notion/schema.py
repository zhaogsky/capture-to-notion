from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlparse

SUPPORTED_TYPES = {
    "title",
    "rich_text",
    "status",
    "select",
    "multi_select",
    "url",
    "date",
    "files",
    "relation",
    "number",
    "checkbox",
    "email",
    "phone_number",
    "people",
    "formula",
    "rollup",
    "created_time",
    "created_by",
    "last_edited_time",
    "last_edited_by",
    "unique_id",
}

FIELD_KEYS = {
    "title": "title",
    "rich_text": "notes",
    "status": "state",
    "select": "tag",
    "multi_select": "tag",
    "url": "url",
    "date": "date",
}

NON_BLOCKING_CONFIRMATION_WARNING_PREFIXES = (
    "ambiguous_field_mapping:page_count:",
)

SEMANTIC_FIELD_RULES = {
    "title": {
        "types": {"title"},
        "aliases": ["名称", "标题", "书名", "片名", "name", "title"],
    },
    "state": {
        "types": {"status", "select"},
        "aliases": ["状态", "阅读状态", "阅读进度", "进度", "收听状态", "status", "state"],
    },
    "cover": {
        "types": {"files"},
        "aliases": ["封面", "封面图", "海报", "cover", "cover image", "book cover"],
    },
    "url": {
        "types": {"url"},
        "aliases": ["链接", "网址", "url", "website", "豆瓣链接", "原文链接"],
    },
    "episode_url": {
        "types": {"url"},
        "aliases": ["单集链接", "节目链接", "播客链接", "episode url", "episode_url"],
    },
    "date": {
        "types": {"date"},
        "aliases": ["日期", "时间", "date", "time"],
    },
    "published_at": {
        "types": {"date"},
        "aliases": ["发布日期", "出版日期", "发布时间", "发布于", "published", "published_at", "published date"],
    },
    "notes": {
        "types": {"rich_text"},
        "aliases": ["备注", "笔记", "摘要", "简介", "描述", "notes", "summary", "description"],
    },
    "author": {
        "types": {"relation", "rich_text"},
        "aliases": ["作者", "作者页面", "作者名", "author", "authors"],
    },
    "publisher": {
        "types": {"rich_text", "select", "relation"},
        "aliases": ["出版社", "出版方", "publisher"],
    },
    "isbn": {
        "types": {"rich_text", "url"},
        "aliases": ["isbn", "ISBN"],
    },
    "page_count": {
        "types": {"number", "rich_text"},
        "aliases": ["页数", "页码", "pages", "page count", "page_count"],
    },
    "podcast": {
        "types": {"relation", "rich_text", "select"},
        "aliases": ["播客", "节目", "podcast", "show"],
    },
    "tag": {
        "types": {"select", "multi_select"},
        "aliases": ["标签", "分类", "类型", "tag", "category"],
    },
}


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

    normalized: dict[str, Any] = {
        "name": name,
        "id": property_data.get("id"),
        "type": property_type,
    }

    if property_type in {"status", "select", "multi_select"}:
        type_data = property_data.get(property_type, {})
        normalized["options"] = [
            {"name": option.get("name"), "color": option.get("color")}
            for option in type_data.get("options", [])
            if isinstance(option, dict)
        ]

    if property_type == "relation":
        relation = property_data.get("relation", {})
        normalized["target_database_id"] = relation.get("database_id")

    return normalized


def normalize_database_schema(database: dict[str, Any]) -> dict[str, dict[str, Any]]:
    properties = database.get("properties", {})
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


def _normalized_field_name(name: str) -> str:
    return "".join(str(name).strip().lower().replace("_", " ").replace("-", " ").split())


def _semantic_candidates(schema: dict[str, dict[str, Any]], semantic_key: str) -> list[str]:
    rule = SEMANTIC_FIELD_RULES[semantic_key]
    aliases = {_normalized_field_name(alias) for alias in rule["aliases"]}
    candidates = []
    for name, property_schema in schema.items():
        if property_schema.get("type") not in rule["types"]:
            continue
        if _normalized_field_name(name) in aliases:
            candidates.append(name)
    return sorted(candidates)


def confirmation_blocking_warnings(warnings: list[str] | None, content_type: str | None = None) -> list[str]:
    non_blocking_prefixes = () if content_type == "book" else NON_BLOCKING_CONFIRMATION_WARNING_PREFIXES
    return [
        warning
        for warning in (warnings or [])
        if not warning.startswith(non_blocking_prefixes)
    ]


def semantic_field_mapping(schema: dict[str, dict[str, Any]], include_sources: bool = False) -> dict[str, Any]:
    fields: dict[str, str] = {}
    field_sources: dict[str, str] = {}
    warnings: list[str] = []

    for semantic_key in SEMANTIC_FIELD_RULES:
        candidates = _semantic_candidates(schema, semantic_key)
        if not candidates:
            continue
        fields[semantic_key] = candidates[0]
        field_sources[semantic_key] = "semantic"
        if len(candidates) > 1:
            warnings.append(f"ambiguous_field_mapping:{semantic_key}:{','.join(candidates)}")

    for name in sorted(schema):
        if name in fields.values():
            continue
        property_schema = schema[name]
        property_type = property_schema.get("type")
        fallback_key = FIELD_KEYS.get(property_type)
        if fallback_key and fallback_key not in fields:
            fields[fallback_key] = name
            field_sources[fallback_key] = "type_fallback"
        elif not fallback_key and property_type == "relation":
            is_semantic_relation_candidate = any(
                name in _semantic_candidates(schema, semantic_key)
                for semantic_key, rule in SEMANTIC_FIELD_RULES.items()
                if "relation" in rule["types"]
            )
            if (
                not is_semantic_relation_candidate
                and name not in fields
                and name not in fields.values()
            ):
                fields[name] = name
                field_sources[name] = "relation_fallback"

    result: dict[str, Any] = {
        "fields": fields,
        "warnings": warnings,
        "requires_confirmation": bool(warnings),
    }
    if include_sources:
        result["field_sources"] = field_sources
    return result


def field_mapping(schema: dict[str, dict[str, Any]]) -> dict[str, str]:
    return semantic_field_mapping(schema)["fields"]


def _is_empty_property_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


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
    return {"number": value}


def _build_checkbox_property(value: Any) -> dict[str, Any]:
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
        if not isinstance(file_upload, dict) or not file_upload.get("id"):
            return None
        return value

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


def _build_property_value(property_type: str, value: Any) -> dict[str, Any] | None:
    builders = {
        "title": _build_title_property,
        "rich_text": _build_rich_text_property,
        "select": _build_select_property,
        "multi_select": _build_multi_select_property,
        "status": _build_status_property,
        "url": _build_url_property,
        "number": _build_number_property,
        "checkbox": _build_checkbox_property,
        "email": _build_email_property,
        "phone_number": _build_phone_number_property,
        "people": _build_people_property,
        "date": _build_date_property,
        "files": _build_files_property,
        "relation": _build_relation_property,
    }
    builder = builders.get(property_type)
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
