from __future__ import annotations

import re
from typing import Any

_RELATION_KEYS = ("author", "podcast")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PAGE_ID_32_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _field_schema(target_structure: dict[str, Any], field_name: str) -> dict[str, Any] | None:
    data_sources = target_structure.get("data_sources", {})
    if not isinstance(data_sources, dict):
        return None
    for data_source in data_sources.values():
        if not isinstance(data_source, dict):
            continue
        schema = data_source.get("schema", {})
        if isinstance(schema, dict) and isinstance(schema.get(field_name), dict):
            return schema[field_name]
    return None


def _relation_target(target_structure: dict[str, Any], field_name: str, field_schema: dict[str, Any]) -> str | None:
    target_database_id = field_schema.get("target_database_id")
    if target_database_id:
        return str(target_database_id)

    relations = target_structure.get("relations", [])
    if not isinstance(relations, list):
        return None
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if relation.get("field") == field_name and relation.get("target_database_id"):
            return str(relation["target_database_id"])
    return None


def _looks_like_page_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(_UUID_RE.match(stripped) or _PAGE_ID_32_HEX_RE.match(stripped)) or stripped.startswith(("page_", "page-"))


def _resolve_single_value(adapter: Any, target_database_id: str, key: str, value: Any) -> tuple[str | None, str | None]:
    if _looks_like_page_id(value):
        return str(value), None
    if not isinstance(value, str):
        return None, f"relation_unresolved:{key}:{value}"

    try:
        matches = adapter.query_database_title_exact(target_database_id, value)
    except Exception:
        return None, f"relation_query_failed:{key}:{value}"

    if len(matches) == 1:
        page_id = matches[0].get("id") if isinstance(matches[0], dict) else None
        if page_id:
            return str(page_id), None
        return None, f"relation_unresolved:{key}:{value}"
    if not matches:
        return None, f"relation_unresolved:{key}:{value}"
    return None, f"relation_ambiguous:{key}:{value}"


def resolve_record_relations(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
    adapter: Any,
) -> tuple[dict[str, Any], list[str]]:
    resolved_record = dict(record)
    warnings: list[str] = []

    for key in _RELATION_KEYS:
        if key not in resolved_record:
            continue
        value = resolved_record[key]
        if _is_empty(value):
            continue

        field_name = field_mapping.get(key)
        if not field_name:
            continue

        schema = _field_schema(target_structure, field_name)
        if not schema or schema.get("type") != "relation":
            continue

        target_database_id = _relation_target(target_structure, field_name, schema)
        if not target_database_id:
            resolved_record[key] = None
            warnings.append(f"relation_target_missing:{key}:{field_name}")
            continue

        values = value if isinstance(value, list) else [value]
        resolved_values: list[str] = []
        for item in values:
            if _is_empty(item):
                continue
            resolved_id, warning = _resolve_single_value(adapter, target_database_id, key, item)
            if resolved_id:
                resolved_values.append(resolved_id)
            if warning:
                warnings.append(warning)

        if isinstance(value, list):
            resolved_record[key] = resolved_values or None
        else:
            resolved_record[key] = resolved_values[0] if resolved_values else None

    return resolved_record, warnings
