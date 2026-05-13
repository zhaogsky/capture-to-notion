from __future__ import annotations

from typing import Any

from capture_to_notion.cache import CacheStore
from capture_to_notion.schema import confirmation_blocking_warnings, normalize_database_schema, plain_title, schema_hash

PROFILE_FIELD_SOURCES = {"explicit", "profile"}
PRIMARY_FIELD_SOURCES = {"semantic", *PROFILE_FIELD_SOURCES}


def _target_title(page: dict[str, Any]) -> str | None:
    return plain_title(page.get("title")) or plain_title(page.get("properties", {}).get("title")) or page.get("name")


def _database_title(database: dict[str, Any], block: dict[str, Any]) -> str | None:
    child_database = block.get("child_database", {})
    return plain_title(database.get("title")) or child_database.get("title") or database.get("name")


def _data_source_title(data_source: dict[str, Any], metadata: dict[str, Any], database: dict[str, Any], block: dict[str, Any]) -> str | None:
    return plain_title(data_source.get("title")) or metadata.get("name") or _database_title(database, block)


def _child_database_id(block: dict[str, Any]) -> str | None:
    child_database = block.get("child_database", {})
    return child_database.get("database_id") or block.get("id")


def _default_target_id(page_id: str) -> str:
    return page_id.replace("-", "")


def _build_state_mapping(data_sources: dict[str, Any]) -> dict[str, Any]:
    for data_source in data_sources.values():
        field_name = data_source.get("fields", {}).get("state")
        if not field_name:
            continue
        property_schema = data_source.get("schema", {}).get(field_name, {})
        if property_schema.get("type") in {"status", "select"}:
            return {"field": field_name, "values": {}}
    return {}


def _asset_record_key(field_name: str, fields: dict[str, str], asset_mapping: dict[str, Any]) -> str:
    for semantic_key, mapped_field_name in fields.items():
        if mapped_field_name == field_name:
            return semantic_key

    existing_semantic_keys = set(fields)
    record_key = field_name if field_name not in existing_semantic_keys else f"{field_name}_files"
    candidate = record_key
    index = 2
    while candidate in asset_mapping:
        candidate = f"{record_key}_{index}"
        index += 1
    return candidate


def _build_asset_mapping(data_sources: dict[str, Any]) -> dict[str, Any]:
    asset_mapping: dict[str, Any] = {}
    for data_source in data_sources.values():
        fields = data_source.get("fields", {})
        schema = data_source.get("schema", {})
        for field_name in sorted(schema):
            property_schema = schema.get(field_name, {})
            if property_schema.get("type") != "files":
                continue
            record_key = _asset_record_key(field_name, fields, asset_mapping)
            asset_mapping[record_key] = {
                "field": field_name,
                "type": "files",
                "strategy": "download_and_attach",
            }
    return asset_mapping


def _build_relations(data_sources: dict[str, Any]) -> list[dict[str, Any]]:
    relations = []
    for data_source_id, data_source in data_sources.items():
        for field_name, property_schema in data_source.get("schema", {}).items():
            if property_schema.get("type") == "relation":
                relations.append(
                    {
                        "data_source_id": data_source_id,
                        "field": field_name,
                        "target_database_id": property_schema.get("target_database_id"),
                    }
                )
    return relations


def _cached_data_source(cache: CacheStore, target_id: str, data_source_id: str) -> dict[str, Any]:
    structure = cache.target_structure(target_id) or {}
    data_sources = structure.get("data_sources", {})
    if not isinstance(data_sources, dict):
        return {}
    for key, data_source in data_sources.items():
        if isinstance(data_source, dict) and (key == data_source_id or data_source.get("data_source_id") == data_source_id):
            return data_source
    return {}


def _profile_field_mapping(
    cache: CacheStore,
    target_id: str,
    data_source_id: str,
    schema: dict[str, Any],
) -> dict[str, dict[str, str]]:
    data_source = _cached_data_source(cache, target_id, data_source_id)
    fields = data_source.get("fields", {})
    field_sources = data_source.get("field_sources", {})
    if not isinstance(fields, dict) or not isinstance(field_sources, dict):
        return {"fields": {}, "field_sources": {}}

    resolved_fields: dict[str, str] = {}
    resolved_sources: dict[str, str] = {}
    for record_key, field_name in fields.items():
        source = field_sources.get(record_key)
        if source not in PROFILE_FIELD_SOURCES:
            continue
        if not isinstance(record_key, str) or not isinstance(field_name, str):
            continue
        if field_name not in schema:
            continue
        resolved_fields[record_key] = field_name
        resolved_sources[record_key] = source
    return {"fields": resolved_fields, "field_sources": resolved_sources}


def _mapping_warning_key(warning: str) -> str | None:
    parts = warning.split(":", 2)
    if len(parts) >= 2 and parts[0] == "ambiguous_field_mapping":
        return parts[1]
    return None


def _merge_profile_field_mapping(mapping: dict[str, Any], profile_mapping: dict[str, dict[str, str]]) -> dict[str, Any]:
    profile_fields = profile_mapping.get("fields", {})
    if not profile_fields:
        return mapping

    fields = dict(mapping.get("fields", {}))
    field_sources = dict(mapping.get("field_sources", {}))
    fields.update(profile_fields)
    field_sources.update(profile_mapping.get("field_sources", {}))
    warnings = [warning for warning in mapping.get("warnings", []) if _mapping_warning_key(warning) not in profile_fields]
    return {
        "fields": fields,
        "field_sources": field_sources,
        "warnings": warnings,
        "requires_confirmation": bool(warnings),
    }


def _primary_score(data_source: dict[str, Any]) -> int:
    schema = data_source.get("schema", {})
    if not schema:
        return 0
    fields = data_source.get("fields", {})
    field_sources = data_source.get("field_sources", {})
    weights = {
        "title": 20,
        "state": 10,
        "cover": 10,
        "author": 35,
        "publisher": 15,
        "isbn": 35,
    }
    score = 0
    for field_key, weight in weights.items():
        if field_key in fields and field_sources.get(field_key) in PRIMARY_FIELD_SOURCES:
            score += weight
    return score


def _assign_data_source_roles(data_sources: dict[str, Any]) -> None:
    if not data_sources:
        return
    primary_id = max(data_sources, key=lambda data_source_id: _primary_score(data_sources[data_source_id]))
    if _primary_score(data_sources[primary_id]) == 0:
        return
    for data_source_id, data_source in data_sources.items():
        data_source["role"] = "primary" if data_source_id == primary_id else "secondary"


def _save_alias(cache: CacheStore, alias: str, target_id: str, page_id: str, title: str | None) -> None:
    data = cache.read_json(cache.config.aliases_file, {"aliases": {}})
    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        aliases = {}
    aliases[alias] = {"type": "page", "page_id": page_id, "title": title, "target_id": target_id}
    cache.write_json(cache.config.aliases_file, {"aliases": aliases})


def scan_page_target(
    adapter: Any,
    page_id: str,
    cache: CacheStore,
    target_id: str | None = None,
    alias: str | None = None,
) -> dict[str, Any]:
    resolved_target_id = target_id or _default_target_id(page_id)
    page = adapter.retrieve_page(page_id)
    children = adapter.list_block_children(page_id)
    title = _target_title(page)

    data_sources: dict[str, Any] = {}
    for block in children:
        if block.get("type") != "child_database":
            continue
        database_id = _child_database_id(block)
        if not database_id:
            continue
        database = adapter.retrieve_database(database_id)
        source_metadata = database.get("data_sources")
        sources = source_metadata if isinstance(source_metadata, list) and source_metadata else [{"id": database_id}]
        for source in sources:
            if not isinstance(source, dict) or not source.get("id"):
                continue
            data_source_id = source["id"]
            data_source = adapter.retrieve_data_source(data_source_id) if data_source_id != database_id else database
            schema = normalize_database_schema(data_source)
            mapping = _merge_profile_field_mapping(
                {"fields": {}, "field_sources": {}, "warnings": [], "requires_confirmation": False},
                _profile_field_mapping(cache, resolved_target_id, data_source_id, schema),
            )
            data_sources[data_source_id] = {
                "data_source_id": data_source_id,
                "title": _data_source_title(data_source, source, database, block),
                "role": "secondary",
                "content_types": [],
                "schema_hash": schema_hash(schema),
                "fields": mapping["fields"],
                "field_sources": mapping["field_sources"],
                "mapping_warnings": mapping["warnings"],
                "schema": schema,
            }

    _assign_data_source_roles(data_sources)
    has_mapping_warnings = any(
        confirmation_blocking_warnings(data_source.get("mapping_warnings"))
        for data_source in data_sources.values()
    )
    has_primary_data_source = any(
        data_source.get("role") == "primary"
        for data_source in data_sources.values()
    )
    has_schema = any(
        bool(data_source.get("schema"))
        for data_source in data_sources.values()
    )
    requires_confirmation = not bool(data_sources) or not has_primary_data_source or has_mapping_warnings
    target_structure = {
        "target": {"page_id": page_id, "title": title, "target_id": resolved_target_id},
        "data_sources": data_sources,
        "relations": _build_relations(data_sources),
        "state_mapping": _build_state_mapping(data_sources),
        "asset_mapping": _build_asset_mapping(data_sources),
        "requires_confirmation": requires_confirmation,
        "confirmation_reason": (
            "child_database_not_found"
            if not data_sources
            else "data_source_schema_empty"
            if not has_primary_data_source and not has_schema
            else "field_mapping_missing"
            if not has_primary_data_source
            else "field_mapping_ambiguous"
            if has_mapping_warnings
            else None
        ),
    }

    cache.write_json(cache.config.targets_dir / f"{resolved_target_id}.json", target_structure)
    if alias:
        _save_alias(cache, alias, resolved_target_id, page_id, title)

    return target_structure
