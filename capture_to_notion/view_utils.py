from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import unquote


PROPERTY_KEYS = {"property", "property_id"}
SPECIAL_PROPERTY_REFERENCE_KEYS = {"map_by", "toggle_column_id"}
_DROP = object()


def _property_id_to_name(schema: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, value in schema.items():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            property_id = value["id"]
            mapping[property_id] = name
            mapping[unquote(property_id)] = name
    return mapping


def _property_name_to_id(schema: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, value in schema.items():
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            mapping[name] = value["id"]
    return mapping


def _warning(source_property_id: str, source_name: str | None) -> dict[str, Any]:
    warning: dict[str, Any] = {"code": "view_property_not_mapped", "source_property_id": source_property_id}
    if source_name:
        warning["source_property_name"] = source_name
    return warning


def _dedupe_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, Any]] = []
    for warning in warnings:
        key = (str(warning.get("source_property_id")), warning.get("source_property_name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(warning)
    return deduped


def _is_property_reference_key(key: str) -> bool:
    return key in PROPERTY_KEYS or key in SPECIAL_PROPERTY_REFERENCE_KEYS or key.endswith("_property_id")


def remap_view_property_references(
    view: dict[str, Any],
    source_schema: dict[str, Any],
    target_schema: dict[str, Any],
) -> dict[str, Any]:
    source_id_to_name = _property_id_to_name(source_schema)
    source_name_to_id = _property_name_to_id(source_schema)
    target_name_to_id = _property_name_to_id(target_schema)
    warnings: list[dict[str, Any]] = []

    def remap_property_reference(reference: str) -> tuple[str | None, str | None]:
        source_name = source_id_to_name.get(reference)
        if source_name is None and reference in source_name_to_id:
            source_name = reference
        target_id = target_name_to_id.get(source_name) if source_name else None
        return target_id, source_name

    def source_property_id_for_reference(reference: str, source_name: str | None) -> str:
        if reference in source_id_to_name:
            return reference
        if source_name and source_name in source_name_to_id:
            return source_name_to_id[source_name]
        return reference

    def remap_value(value: Any, *, remap_property_keys: bool = False) -> Any:
        if isinstance(value, list):
            remapped_items = []
            for item in value:
                remapped = remap_value(item, remap_property_keys=remap_property_keys)
                if remapped is not _DROP:
                    remapped_items.append(remapped)
            return remapped_items
        if not isinstance(value, dict):
            return value

        output: dict[str, Any] = {}
        for key, item in value.items():
            output_key = key
            if remap_property_keys and (key in source_name_to_id or key in source_id_to_name):
                target_id, source_name = remap_property_reference(key)
                if not target_id:
                    warnings.append(_warning(source_property_id_for_reference(key, source_name), source_name))
                    continue
                output_key = target_id

            if _is_property_reference_key(key) and isinstance(item, str):
                target_id, source_name = remap_property_reference(item)
                if not target_id:
                    warnings.append(_warning(source_property_id_for_reference(item, source_name), source_name))
                    return _DROP
                output[output_key] = target_id
                continue

            remapped_item = remap_value(item, remap_property_keys=remap_property_keys)
            if remapped_item is not _DROP:
                output[output_key] = remapped_item
        if len(output) == 1:
            compound_key = next(iter(output))
            if compound_key in {"and", "or"} and output[compound_key] == []:
                return _DROP
        return output

    remapped = deepcopy(view)
    for key in ("configuration", "sorts", "filter", "quick_filters"):
        if key in remapped:
            value = remap_value(remapped[key], remap_property_keys=key == "quick_filters")
            if value is _DROP:
                value = [] if key == "sorts" else {}
            remapped[key] = value
    remapped["warnings"] = _dedupe_warnings(warnings)
    return remapped
