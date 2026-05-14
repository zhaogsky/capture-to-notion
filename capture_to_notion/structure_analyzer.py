from __future__ import annotations

from copy import deepcopy
from typing import Any

from capture_to_notion.schema import WRITABLE_PROPERTY_TYPES

DEFAULT_STRUCTURE_ANALYZER_POLICY: dict[str, Any] = {
    "name_risk_patterns": [
        {"flag": "navigation_like_name", "keywords": ["navigation", "index", "directory", "catalog", "menu"]},
        {"flag": "archive_like_name", "keywords": ["archive", "history", "log"]},
    ],
    "tracking_shape": {
        "flag": "tracking_shape",
        "workflow_property_types": ["status", "select"],
        "operational_property_types": ["status", "select", "multi_select", "date", "checkbox", "people"],
        "content_property_types": ["rich_text", "files", "url", "number", "email", "phone_number", "relation"],
        "require_title": True,
        "require_supporting_property_types": ["date", "checkbox", "people", "multi_select"],
    },
}


def _merged_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_STRUCTURE_ANALYZER_POLICY)
    if not isinstance(policy, dict):
        return merged
    for key, value in policy.items():
        if key == "tracking_shape" and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
            continue
        merged[key] = value
    return merged



def _data_source_entries(structure: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    data_sources = structure.get("data_sources")
    if not isinstance(data_sources, dict):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for data_source_key, data_source in data_sources.items():
        if isinstance(data_source, dict):
            entries.append((str(data_source_key), data_source))
    return entries



def _property_summary(property_name: str, property_schema: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "name": property_schema.get("name") or property_name,
        "id": property_schema.get("id"),
        "type": property_schema.get("type"),
    }
    if property_schema.get("type") in {"select", "status", "multi_select"}:
        options = property_schema.get("options")
        summary["options"] = options if isinstance(options, list) else []
    if property_schema.get("type") == "relation":
        if property_schema.get("target_database_id") is not None:
            summary["target_database_id"] = property_schema.get("target_database_id")
        if property_schema.get("target_data_source_id") is not None:
            summary["target_data_source_id"] = property_schema.get("target_data_source_id")
    return summary



def _group_properties_by_type(schema: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for property_name in sorted(schema):
        property_schema = schema.get(property_name)
        if not isinstance(property_schema, dict):
            continue
        property_type = property_schema.get("type")
        if not isinstance(property_type, str) or not property_type:
            continue
        grouped.setdefault(property_type, []).append(_property_summary(property_name, property_schema))
    return {
        property_type: {"count": len(properties), "properties": properties}
        for property_type, properties in grouped.items()
    }



def _name_risk_flags(name: str | None, policy: dict[str, Any]) -> list[str]:
    if not isinstance(name, str) or not name.strip():
        return []
    lowered_name = name.casefold()
    risk_flags: list[str] = []
    for pattern in policy.get("name_risk_patterns", []):
        if not isinstance(pattern, dict):
            continue
        flag = pattern.get("flag")
        keywords = pattern.get("keywords")
        if not isinstance(flag, str) or not isinstance(keywords, list):
            continue
        lowered_keywords = [keyword.casefold() for keyword in keywords if isinstance(keyword, str) and keyword.strip()]
        if lowered_keywords and any(keyword in lowered_name for keyword in lowered_keywords):
            risk_flags.append(flag)
    return risk_flags



def _tracking_shape_flag(grouped_property_types: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    tracking_policy = policy.get("tracking_shape")
    if not isinstance(tracking_policy, dict):
        return []

    property_types = set(grouped_property_types)
    require_title = bool(tracking_policy.get("require_title", True))
    has_title = "title" in property_types
    workflow_types = set(tracking_policy.get("workflow_property_types", []))
    supporting_types = set(tracking_policy.get("require_supporting_property_types", []))
    operational_types = set(tracking_policy.get("operational_property_types", []))
    content_types = set(tracking_policy.get("content_property_types", []))
    flag = tracking_policy.get("flag")

    if require_title and not has_title:
        return []
    if not workflow_types.intersection(property_types):
        return []
    if not supporting_types.intersection(property_types):
        return []

    operational_count = sum(grouped_property_types[property_type]["count"] for property_type in operational_types if property_type in grouped_property_types)
    content_count = sum(grouped_property_types[property_type]["count"] for property_type in content_types if property_type in grouped_property_types)
    if operational_count <= content_count:
        return []
    if not isinstance(flag, str) or not flag:
        return []
    return [flag]



def _capabilities(schema: dict[str, Any]) -> dict[str, Any]:
    property_types = _group_properties_by_type(schema)
    writable = any(property_type in WRITABLE_PROPERTY_TYPES for property_type in property_types)
    return {
        "writable": writable,
        "property_types": property_types,
    }



def analyze_target_structure(structure: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_policy = _merged_policy(policy)
    candidates: list[dict[str, Any]] = []

    for data_source_key, data_source in _data_source_entries(structure):
        schema = data_source.get("schema") if isinstance(data_source.get("schema"), dict) else {}
        capabilities = _capabilities(schema)
        name = data_source.get("title") or data_source.get("name")
        risk_flags = sorted(
            set(
                _name_risk_flags(name, resolved_policy)
                + _tracking_shape_flag(capabilities["property_types"], resolved_policy)
            )
        )
        candidates.append(
            {
                "id": data_source.get("data_source_id") or data_source_key,
                "name": name,
                "role": data_source.get("role"),
                "capabilities": capabilities,
                "risk_flags": risk_flags,
            }
        )

    candidates.sort(key=lambda item: str(item.get("id") or ""))
    top_level_risk_flags = sorted({flag for candidate in candidates for flag in candidate.get("risk_flags", [])})
    writable_candidate_count = sum(1 for candidate in candidates if candidate["capabilities"].get("writable"))

    return {
        "data_source_candidates": candidates,
        "risk_flags": top_level_risk_flags,
        "structure_complexity": {
            "data_source_count": len(candidates),
            "writable_candidate_count": writable_candidate_count,
            "risk_count": len(top_level_risk_flags),
        },
    }
