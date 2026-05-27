from __future__ import annotations

import hashlib
import json
from typing import Any


WRITABLE_PROPERTY_TYPES = {
    "title",
    "rich_text",
    "number",
    "select",
    "multi_select",
    "status",
    "date",
    "checkbox",
    "url",
    "email",
    "phone_number",
    "people",
    "files",
    "relation",
}
READ_ONLY_PROPERTY_TYPES = {
    "created_by",
    "created_time",
    "last_edited_by",
    "last_edited_time",
    "unique_id",
    "verification",
}
COMPUTED_PROPERTY_TYPES = {"formula", "rollup"}
LIMITED_PROPERTY_TYPES = {"place"}


def plain_text(value: Any) -> str | None:
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
        value_type = value.get("type")
        if isinstance(value_type, str) and value_type in value:
            return plain_text(value.get(value_type))
        for key in ("title", "rich_text", "name"):
            if key in value:
                return plain_text(value.get(key))
        for item in value.values():
            text = plain_text(item)
            if text:
                return text
    return None


def schema_hash(schema: dict[str, Any]) -> str:
    payload = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def property_capability(property_schema: dict[str, Any]) -> str:
    property_type = property_schema.get("type")
    if property_type in WRITABLE_PROPERTY_TYPES:
        return "writable"
    if property_type in READ_ONLY_PROPERTY_TYPES:
        return "read_only"
    if property_type in COMPUTED_PROPERTY_TYPES:
        return "computed"
    if property_type in LIMITED_PROPERTY_TYPES:
        return "limited"
    return "unsupported"


def normalize_parent(parent: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(parent, dict):
        return None
    parent_type = parent.get("type")
    if not isinstance(parent_type, str):
        return None
    if parent_type == "workspace":
        return {"type": "workspace", "id": "workspace"}
    parent_id = parent.get(parent_type)
    if isinstance(parent_id, bool):
        return {"type": parent_type, "id": parent_type}
    if parent_id is None:
        return None
    return {"type": parent_type, "id": str(parent_id)}


def normalize_database(database: dict[str, Any]) -> dict[str, Any]:
    data_source_ids = [
        str(data_source.get("id"))
        for data_source in database.get("data_sources", [])
        if isinstance(data_source, dict) and data_source.get("id")
    ]
    view_ids = [str(view.get("id")) for view in database.get("views", []) if isinstance(view, dict) and view.get("id")]
    return {
        "object": "database",
        "database_id": str(database.get("id")),
        "title": plain_text(database.get("title")),
        "parent": normalize_parent(database.get("parent")),
        "is_inline": bool(database.get("is_inline", False)),
        "data_source_ids": data_source_ids,
        "view_ids": view_ids,
    }


def normalize_data_source(data_source: dict[str, Any]) -> dict[str, Any]:
    schema = data_source.get("properties")
    if not isinstance(schema, dict):
        schema = {}
    parent = normalize_parent(data_source.get("parent"))
    database_parent = normalize_parent(data_source.get("database_parent"))
    database_id = parent["id"] if parent and parent.get("type") == "database_id" else data_source.get("database_id")
    return {
        "object": "data_source",
        "data_source_id": str(data_source.get("id")),
        "database_id": str(database_id) if database_id else None,
        "title": plain_text(data_source.get("title")) or data_source.get("name"),
        "parent": parent,
        "database_parent": database_parent,
        "schema": schema,
        "schema_hash": schema_hash(schema),
        "property_capabilities": {name: property_capability(value) for name, value in schema.items() if isinstance(value, dict)},
        "queryable": data_source.get("queryable", True) is not False,
        "writable": data_source.get("writable", True) is not False,
    }


def normalize_view(view: dict[str, Any], *, location: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "object": "view",
        "view_id": str(view.get("id")),
        "name": view.get("name") or plain_text(view.get("title")),
        "type": view.get("type"),
        "database_id": view.get("database_id"),
        "data_source_id": view.get("data_source_id"),
        "location": location,
        "filter": view.get("filter", {}),
        "sorts": view.get("sorts", []),
        "quick_filters": view.get("quick_filters", {}),
        "configuration": view.get("configuration", {}),
    }


def normalize_page(page: dict[str, Any], *, block_ids: list[str] | None = None) -> dict[str, Any]:
    parent = normalize_parent(page.get("parent"))
    kind = "unknown_page"
    if parent and parent.get("type") in {"data_source_id", "database_id"}:
        kind = "record_page"
    elif parent:
        kind = "container_page"
    properties = page.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    return {
        "object": "page",
        "page_id": str(page.get("id")),
        "kind": kind,
        "title": plain_text(properties),
        "parent": parent,
        "property_values": properties,
        "block_ids": block_ids or [],
    }


def normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    parent = normalize_parent(block.get("parent"))
    block_type = str(block.get("type"))
    node: dict[str, Any] = {
        "object": "block",
        "block_id": str(block.get("id")),
        "type": block_type,
        "parent_page_id": parent["id"] if parent and parent.get("type") == "page_id" else None,
        "has_children": bool(block.get("has_children", False)),
    }
    block_payload = block.get(block_type)
    if isinstance(block_payload, dict):
        node[block_type] = block_payload
    return node
