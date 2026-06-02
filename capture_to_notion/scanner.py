from __future__ import annotations

from typing import Any

from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.notion_adapter import NotionNotFoundError, NotionPermissionError
from capture_to_notion.notion_graph import plain_text, normalize_block, normalize_database, normalize_data_source, normalize_data_source_template, normalize_page, normalize_view
from capture_to_notion.schema import confirmation_blocking_warnings, normalize_database_schema, plain_title, resolve_field_mapping, schema_hash

PROFILE_FIELD_SOURCES = {"explicit", "profile"}
PRIMARY_FIELD_SOURCES = PROFILE_FIELD_SOURCES


def _target_title(page: dict[str, Any]) -> str | None:
    return plain_title(page.get("title")) or plain_title(page.get("properties", {}).get("title")) or page.get("name")


def _empty_v2_graph(root_kind: str, root_id: str, graph_id: str) -> dict[str, Any]:
    return {
        "cache_version": 2,
        "graph_id": graph_id,
        "root": {"kind": root_kind, "id": root_id},
        "pages": {},
        "blocks": {},
        "databases": {},
        "data_sources": {},
        "views": {},
    }


def _child_database_view_location(
    page_id: str,
    *,
    block_id: str | None = None,
    display_title: str | None = None,
    section_path: list[str] | None = None,
) -> dict[str, Any]:
    location: dict[str, Any] = {"type": "page_id", "id": page_id, "discovered_from": "page_scan"}
    if block_id:
        location["source_block_id"] = block_id
        location["source_block_type"] = "child_database"
    if display_title:
        location["display_title"] = display_title
    if section_path:
        location["section_path"] = section_path
    return location


def _heading_level(block_type: str) -> int | None:
    if block_type not in {"heading_1", "heading_2", "heading_3"}:
        return None
    return int(block_type.rsplit("_", 1)[1])


def _block_title(block: dict[str, Any]) -> str | None:
    block_type = block.get("type")
    payload = block.get(block_type) if isinstance(block_type, str) else None
    return plain_text(payload) if isinstance(payload, dict) else None


def _section_path(headings: dict[int, str]) -> list[str]:
    return [headings[level] for level in sorted(headings) if headings[level]]


def _add_parent_pages(adapter: Any, graph: dict[str, Any], node: dict[str, Any]) -> None:
    parent = node.get("parent") if isinstance(node, dict) else None
    seen: set[str] = set()
    while isinstance(parent, dict) and parent.get("type") == "page_id":
        page_id = parent.get("id")
        if not isinstance(page_id, str) or not page_id or page_id in seen:
            return
        seen.add(page_id)
        if page_id in graph["pages"]:
            parent = graph["pages"][page_id].get("parent")
            continue
        try:
            page = adapter.retrieve_page(page_id)
        except (KeyError, AttributeError, NotionNotFoundError, NotionPermissionError):
            return
        normalized_page = normalize_page(page)
        graph["pages"][page_id] = normalized_page
        parent = normalized_page.get("parent")


def _add_parent_graph_objects(adapter: Any, graph: dict[str, Any], node: dict[str, Any]) -> None:
    parent = node.get("parent") if isinstance(node, dict) else None
    if not isinstance(parent, dict):
        return
    parent_type = parent.get("type")
    parent_id = parent.get("id")
    if not isinstance(parent_id, str) or not parent_id:
        return
    if parent_type == "page_id":
        _add_parent_pages(adapter, graph, node)
    elif parent_type == "data_source_id":
        _add_data_source_parent_chain(adapter, graph, parent_id)
    elif parent_type == "database_id":
        _add_database_parent_chain(adapter, graph, parent_id)


def _safe_retrieve_data_source(adapter: Any, data_source_id: str | None) -> dict[str, Any]:
    if not data_source_id:
        return {}
    try:
        data_source = adapter.retrieve_data_source(data_source_id)
    except (KeyError, AttributeError, NotionNotFoundError, NotionPermissionError):
        return {}
    return data_source if isinstance(data_source, dict) else {}


def _safe_list_data_source_templates(adapter: Any, data_source_id: str) -> list[dict[str, Any]]:
    try:
        templates = adapter.list_data_source_templates(data_source_id)
    except (KeyError, AttributeError, TypeError, NotionNotFoundError, NotionPermissionError):
        return []
    return [template for template in templates if isinstance(template, dict)] if isinstance(templates, list) else []


def _add_template_facts(adapter: Any, data_source: dict[str, Any]) -> None:
    data_source_id = data_source.get("data_source_id")
    if not isinstance(data_source_id, str) or not data_source_id:
        return
    templates = [
        normalize_data_source_template(
            template,
            fallback_data_source_id=data_source_id,
            fallback_database_id=data_source.get("database_id") if isinstance(data_source.get("database_id"), str) else None,
        )
        for template in _safe_list_data_source_templates(adapter, data_source_id)
    ]
    if templates:
        data_source["templates"] = templates


def _add_data_source_parent_chain(adapter: Any, graph: dict[str, Any], data_source_id: str) -> None:
    if data_source_id not in graph["data_sources"]:
        data_source = _safe_retrieve_data_source(adapter, data_source_id)
        if not data_source:
            return
        normalized_data_source = normalize_data_source(data_source)
        location_facts = _data_source_location_facts(adapter, data_source)
        for key, value in location_facts.items():
            normalized_data_source.setdefault(key, value)
        _add_template_facts(adapter, normalized_data_source)
        graph["data_sources"][data_source_id] = normalized_data_source

    database_id = graph["data_sources"][data_source_id].get("database_id")
    if isinstance(database_id, str) and database_id:
        _add_database_parent_chain(adapter, graph, database_id)


def _add_database_parent_chain(adapter: Any, graph: dict[str, Any], database_id: str) -> None:
    if database_id not in graph["databases"]:
        database = _safe_retrieve_database(adapter, database_id)
        if not database:
            return
        graph["databases"][database_id] = normalize_database(database)
    _add_parent_pages(adapter, graph, graph["databases"][database_id])


def _list_view_refs(adapter: Any, *, database_id: str | None = None, data_source_id: str | None = None) -> list[dict[str, Any]]:
    try:
        return adapter.list_views(database_id=database_id, data_source_id=data_source_id)
    except TypeError:
        if database_id is not None:
            return adapter.list_views(database_id=database_id)
        if data_source_id is not None:
            return adapter.list_views(data_source_id=data_source_id)
        return []


def _retrieve_views(adapter: Any, *, database_id: str | None = None, data_source_id: str | None = None) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for view_ref in _list_view_refs(adapter, database_id=database_id, data_source_id=data_source_id):
        view_id = view_ref.get("id") if isinstance(view_ref, dict) else None
        if not view_id:
            continue
        view = adapter.retrieve_view(str(view_id))
        if isinstance(view, dict):
            views.append(view)
    return views


def _add_view_data_source(adapter: Any, graph: dict[str, Any], view: dict[str, Any]) -> None:
    data_source_id = view.get("data_source_id")
    if isinstance(data_source_id, str) and data_source_id and data_source_id not in graph["data_sources"]:
        _add_data_source_parent_chain(adapter, graph, data_source_id)



def _normalize_view_with_source_schema(
    view: dict[str, Any],
    graph: dict[str, Any],
    *,
    location: dict[str, Any] | None = None,
    fallback_data_source_id: str | None = None,
) -> dict[str, Any]:
    normalized_view = normalize_view(view, location=location)
    data_source_id = normalized_view.get("data_source_id") or fallback_data_source_id
    data_source = graph.get("data_sources", {}).get(data_source_id) if isinstance(data_source_id, str) else None
    schema = data_source.get("schema") if isinstance(data_source, dict) else None
    if isinstance(schema, dict) and schema:
        normalized_view["_source_schema"] = schema
    return normalized_view


def scan_page_graph(adapter: Any, page_id: str, store: CacheV2Store, *, graph_id: str) -> dict[str, Any]:
    graph = _empty_v2_graph("page", page_id, graph_id)
    page = adapter.retrieve_page(page_id)
    children = adapter.list_block_children(page_id)
    graph["pages"][page_id] = normalize_page(page, block_ids=[str(block.get("id")) for block in children if block.get("id")])
    _add_parent_graph_objects(adapter, graph, graph["pages"][page_id])

    headings: dict[int, str] = {}
    for block in children:
        block_id = block.get("id")
        block_id_text = str(block_id) if block_id else None
        if block_id:
            graph["blocks"][str(block_id)] = normalize_block(block)
        block_type = str(block.get("type"))
        heading_level = _heading_level(block_type)
        if heading_level is not None:
            title = _block_title(block)
            for level in list(headings):
                if level >= heading_level:
                    headings.pop(level, None)
            if title:
                headings[heading_level] = title
            continue
        if block.get("type") != "child_database":
            continue
        database_id = _child_database_id(block)
        if not database_id:
            continue
        database = adapter.retrieve_database(database_id)
        display_title = _database_title(database, block)
        visual_location = _child_database_view_location(
            page_id,
            block_id=block_id_text,
            display_title=display_title,
            section_path=_section_path(headings),
        )
        graph["databases"][database_id] = normalize_database(database)
        _add_parent_pages(adapter, graph, graph["databases"][database_id])
        source_metadata = database.get("data_sources")
        sources = source_metadata if isinstance(source_metadata, list) and source_metadata else [{"id": database_id}]
        for source in sources:
            if not isinstance(source, dict) or not source.get("id"):
                continue
            data_source_id = str(source["id"])
            data_source = adapter.retrieve_data_source(data_source_id) if data_source_id != database_id else database
            normalized_data_source = normalize_data_source(data_source)
            location_facts = _data_source_location_facts(adapter, data_source)
            if not normalized_data_source.get("database_id"):
                normalized_data_source["database_id"] = database_id
            for key, value in location_facts.items():
                normalized_data_source.setdefault(key, value)
            if block_id:
                normalized_data_source["source_block_id"] = str(block_id)
            normalized_data_source["source_block_type"] = "child_database"
            _add_template_facts(adapter, normalized_data_source)
            graph["data_sources"][data_source_id] = normalized_data_source
        for view in _retrieve_views(adapter, database_id=database_id):
            view_id = view.get("id")
            if view_id:
                _add_view_data_source(adapter, graph, view)
                graph["views"][str(view_id)] = _normalize_view_with_source_schema(
                    view,
                    graph,
                    location=visual_location,
                )

    store.write_graph(graph_id, graph)
    return graph


def scan_data_source_graph(adapter: Any, data_source_id: str, store: CacheV2Store, *, graph_id: str) -> dict[str, Any]:
    graph = _empty_v2_graph("data_source", data_source_id, graph_id)
    data_source = adapter.retrieve_data_source(data_source_id)
    normalized_data_source = normalize_data_source(data_source)
    database_id = normalized_data_source.get("database_id")
    database = _safe_retrieve_database(adapter, database_id) if isinstance(database_id, str) and database_id else {}
    location_facts = _data_source_location_facts(adapter, data_source, database)
    for key, value in location_facts.items():
        normalized_data_source.setdefault(key, value)
    _add_template_facts(adapter, normalized_data_source)
    graph["data_sources"][data_source_id] = normalized_data_source

    if isinstance(database_id, str) and database_id:
        if database:
            graph["databases"][database_id] = normalize_database(database)
            _add_parent_pages(adapter, graph, graph["databases"][database_id])
        for view in _retrieve_views(adapter, database_id=database_id):
            view_id = view.get("id")
            if view_id:
                _add_view_data_source(adapter, graph, view)
                graph["views"][str(view_id)] = _normalize_view_with_source_schema(view, graph)

    for view in _retrieve_views(adapter, data_source_id=data_source_id):
        view_id = view.get("id")
        if view_id:
            _add_view_data_source(adapter, graph, view)
            graph["views"][str(view_id)] = _normalize_view_with_source_schema(
                view,
                graph,
                fallback_data_source_id=data_source_id,
            )

    store.write_graph(graph_id, graph)
    return graph


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


def _build_state_mapping(data_sources: dict[str, Any], cached_structure: dict[str, Any] | None = None) -> dict[str, Any]:
    cached_mapping = cached_structure.get("state_mapping") if isinstance(cached_structure, dict) else None
    for data_source in data_sources.values():
        field_name = data_source.get("fields", {}).get("state")
        if not field_name:
            continue
        property_schema = data_source.get("schema", {}).get(field_name, {})
        if property_schema.get("type") in {"status", "select"}:
            if isinstance(cached_mapping, dict) and cached_mapping.get("field") == field_name and isinstance(cached_mapping.get("values"), dict):
                return {"field": field_name, "values": cached_mapping["values"]}
            return {"field": field_name, "values": {}}
    return {}


def _asset_record_key(field_name: str, fields: dict[str, str], asset_mapping: dict[str, Any]) -> str:
    for record_key, mapped_field_name in fields.items():
        if mapped_field_name == field_name:
            return record_key

    existing_record_keys = set(fields)
    record_key = field_name if field_name not in existing_record_keys else f"{field_name}_files"
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
                relation = {
                    "data_source_id": data_source_id,
                    "field": field_name,
                    "target_database_id": property_schema.get("target_database_id"),
                }
                target_data_source_id = property_schema.get("target_data_source_id")
                if target_data_source_id:
                    relation["target_data_source_id"] = target_data_source_id
                relations.append(relation)
    return relations


def _cached_data_source(structure: dict[str, Any], data_source_id: str) -> dict[str, Any]:
    data_sources = structure.get("data_sources", {})
    if not isinstance(data_sources, dict):
        return {}
    for key, data_source in data_sources.items():
        if isinstance(data_source, dict) and (key == data_source_id or data_source.get("data_source_id") == data_source_id):
            return data_source
    return {}


def _profile_mapping_fields(profile: Any) -> dict[str, str]:
    if not isinstance(profile, dict):
        return {}
    mappings: list[dict[str, Any]] = []
    direct_mapping = profile.get("field_mapping")
    if isinstance(direct_mapping, dict):
        mappings.append(direct_mapping)
    else:
        mappings.extend(
            section.get("field_mapping")
            for section in profile.values()
            if isinstance(section, dict) and isinstance(section.get("field_mapping"), dict)
        )

    resolved: dict[str, str] = {}
    for mapping in mappings:
        for record_key, field_name in mapping.items():
            if isinstance(record_key, str) and isinstance(field_name, str):
                resolved[record_key] = field_name
    return resolved


def _profile_field_mapping(
    structure: dict[str, Any],
    data_source_id: str,
    schema: dict[str, Any],
) -> dict[str, dict[str, str]]:
    data_source = _cached_data_source(structure, data_source_id)
    fields = data_source.get("fields", {})
    field_sources = data_source.get("field_sources", {})

    trusted_cached_fields = {
        record_key: field_name
        for record_key, field_name in (fields.items() if isinstance(fields, dict) else [])
        if isinstance(field_sources, dict) and field_sources.get(record_key) in PROFILE_FIELD_SOURCES
    }
    resolved_fields = resolve_field_mapping(schema, cached_fields=trusted_cached_fields)
    resolved_sources = {
        record_key: field_sources[record_key]
        for record_key in resolved_fields
        if isinstance(field_sources, dict) and record_key in field_sources
    }

    profile_fields: dict[str, str] = {}
    for profile in (structure.get("parser_profile"), data_source.get("parser_profile")):
        profile_fields.update(_profile_mapping_fields(profile))
    for record_key, field_name in resolve_field_mapping(schema, explicit_mapping=profile_fields).items():
        if record_key in resolved_fields:
            continue
        resolved_fields[record_key] = field_name
        resolved_sources[record_key] = "profile"
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


def _preserve_cached_parser_profile(scanned_data_source: dict[str, Any], cached_data_source: dict[str, Any]) -> None:
    if "parser_profile" in cached_data_source:
        scanned_data_source["parser_profile"] = cached_data_source["parser_profile"]


def _notion_parent_location_facts(parent: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parent, dict):
        return {}

    facts: dict[str, Any] = {}
    parent_type = parent.get("type")
    if isinstance(parent_type, str):
        facts["parent_type"] = parent_type
    if isinstance(parent.get("page_id"), str):
        facts["parent_page_id"] = parent["page_id"]
    if isinstance(parent.get("database_id"), str):
        facts["parent_database_id"] = parent["database_id"]
    if isinstance(parent.get("data_source_id"), str):
        facts["parent_data_source_id"] = parent["data_source_id"]
    return facts


def _safe_retrieve_database(adapter: Any, database_id: str | None) -> dict[str, Any]:
    if not database_id:
        return {}
    try:
        database = adapter.retrieve_database(database_id)
    except (KeyError, AttributeError, NotionNotFoundError, NotionPermissionError):
        return {}
    return database if isinstance(database, dict) else {}


def _database_parent_location_facts(database: dict[str, Any]) -> dict[str, Any]:
    parent = database.get("parent", {}) if isinstance(database, dict) else {}
    return _notion_parent_location_facts(parent)


def _data_source_parent_database_id(data_source: dict[str, Any]) -> str | None:
    if isinstance(data_source.get("database_id"), str):
        return data_source["database_id"]
    parent = data_source.get("parent", {}) if isinstance(data_source.get("parent"), dict) else {}
    if isinstance(parent.get("database_id"), str):
        return parent["database_id"]
    return None


def _data_source_location_facts(adapter: Any, data_source: dict[str, Any], database: dict[str, Any] | None = None) -> dict[str, Any]:
    facts = _notion_parent_location_facts(data_source.get("parent", {}))
    database_id = _data_source_parent_database_id(data_source)
    if database_id:
        facts["database_id"] = database_id
        resolved_database = database if isinstance(database, dict) and database else _safe_retrieve_database(adapter, database_id)
        if resolved_database:
            for key, value in _database_parent_location_facts(resolved_database).items():
                if key != "parent_type" and key not in facts:
                    facts[key] = value
    return facts


def _integer_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: int(weight)
        for key, weight in value.items()
        if isinstance(key, str) and isinstance(weight, int | float) and not isinstance(weight, bool) and weight > 0
    }


def _primary_score_fields_from_profile(profile: Any) -> dict[str, int]:
    if not isinstance(profile, dict):
        return {}
    score_fields = _integer_mapping(profile.get("primary_score_fields"))
    for section in profile.values():
        if isinstance(section, dict):
            score_fields.update(_integer_mapping(section.get("primary_score_fields")))
    return score_fields


def _default_primary_score_fields(config_data: dict[str, Any]) -> dict[str, int]:
    parser_profiles = config_data.get("parser_profiles", {})
    if not isinstance(parser_profiles, dict):
        return {}
    defaults = parser_profiles.get("defaults", {})
    return _primary_score_fields_from_profile(defaults)


def _primary_score(data_source: dict[str, Any], score_fields: dict[str, int]) -> int:
    schema = data_source.get("schema", {})
    if not schema or not score_fields:
        return 0
    fields = data_source.get("fields", {})
    field_sources = data_source.get("field_sources", {})
    score = 0
    for field_key, weight in score_fields.items():
        if field_key in fields and field_sources.get(field_key) in PRIMARY_FIELD_SOURCES:
            score += weight
    return score


def _score_fields_for_data_source(data_source: dict[str, Any], base_score_fields: dict[str, int]) -> dict[str, int]:
    score_fields = dict(base_score_fields)
    score_fields.update(_primary_score_fields_from_profile(data_source.get("parser_profile")))
    return score_fields


def _assign_data_source_roles(data_sources: dict[str, Any], score_fields: dict[str, int]) -> None:
    if not data_sources:
        return
    primary_id = max(
        data_sources,
        key=lambda data_source_id: _primary_score(
            data_sources[data_source_id],
            _score_fields_for_data_source(data_sources[data_source_id], score_fields),
        ),
    )
    primary_score = _primary_score(data_sources[primary_id], _score_fields_for_data_source(data_sources[primary_id], score_fields))
    if primary_score == 0:
        return
    for data_source_id, data_source in data_sources.items():
        data_source["role"] = "primary" if data_source_id == primary_id else "secondary"


def _read_aliases(cache: CacheStore) -> dict[str, Any]:
    data = cache.read_json(cache.config.aliases_file, {"aliases": {}})
    aliases = data.get("aliases")
    return aliases if isinstance(aliases, dict) else {}


def _save_alias(cache: CacheStore, alias: str, target_id: str, page_id: str, title: str | None) -> None:
    aliases = _read_aliases(cache)
    aliases[alias] = {"type": "page", "page_id": page_id, "title": title, "target_id": target_id}
    cache.write_json(cache.config.aliases_file, {"aliases": aliases})


def _save_data_source_alias(cache: CacheStore, alias: str, target_id: str, data_source_id: str, title: str | None) -> None:
    aliases = _read_aliases(cache)
    aliases[alias] = {"type": "data_source", "data_source_id": data_source_id, "title": title, "target_id": target_id}
    cache.write_json(cache.config.aliases_file, {"aliases": aliases})


def _scan_data_source(
    data_source_id: str,
    data_source: dict[str, Any],
    title: str | None,
    cached_structure: dict[str, Any],
    location_facts: dict[str, Any] | None = None,
    templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    schema = normalize_database_schema(data_source)
    mapping = _merge_profile_field_mapping(
        {"fields": {}, "field_sources": {}, "warnings": [], "requires_confirmation": False},
        _profile_field_mapping(cached_structure, data_source_id, schema),
    )
    scanned_data_source = {
        "data_source_id": data_source_id,
        "title": title,
        "role": "secondary",
        "content_types": [],
        "schema_hash": schema_hash(schema),
        "fields": mapping["fields"],
        "field_sources": mapping["field_sources"],
        "mapping_warnings": mapping["warnings"],
        "schema": schema,
    }
    if location_facts:
        scanned_data_source.update(location_facts)
    normalized_templates = [
        normalize_data_source_template(
            template,
            fallback_data_source_id=data_source_id,
            fallback_database_id=scanned_data_source.get("database_id") if isinstance(scanned_data_source.get("database_id"), str) else None,
        )
        for template in templates or []
    ]
    if normalized_templates:
        scanned_data_source["templates"] = normalized_templates
    _preserve_cached_parser_profile(scanned_data_source, _cached_data_source(cached_structure, data_source_id))
    return scanned_data_source


def _target_confirmation(data_sources: dict[str, Any]) -> tuple[bool, str | None]:
    has_mapping_warnings = any(
        confirmation_blocking_warnings(data_source.get("mapping_warnings"), [])
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
    confirmation_reason = (
        "child_database_not_found"
        if not data_sources
        else "data_source_schema_empty"
        if not has_primary_data_source and not has_schema
        else "field_mapping_missing"
        if not has_primary_data_source
        else "field_mapping_ambiguous"
        if has_mapping_warnings
        else None
    )
    return requires_confirmation, confirmation_reason


def scan_page_target(
    adapter: Any,
    page_id: str,
    cache: CacheStore,
    target_id: str | None = None,
    alias: str | None = None,
) -> dict[str, Any]:
    resolved_target_id = target_id or _default_target_id(page_id)
    cached_structure = cache.target_structure(resolved_target_id) or {}
    page = adapter.retrieve_page(page_id)
    title = _target_title(page)
    parent = page.get("parent", {}) if isinstance(page.get("parent"), dict) else {}
    children = adapter.list_block_children(page_id)

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
            location_facts = {
                **_database_parent_location_facts(database),
                "database_id": database_id,
                "parent_page_id": page_id,
                "source_block_id": block.get("id"),
                "source_block_type": block.get("type"),
            }
            data_sources[data_source_id] = _scan_data_source(
                data_source_id,
                data_source,
                _data_source_title(data_source, source, database, block),
                cached_structure,
                location_facts,
                _safe_list_data_source_templates(adapter, data_source_id),
            )

    if data_sources:
        config_data = cache.read_json(cache.config.config_file, {})
        score_fields = _default_primary_score_fields(config_data)
        score_fields.update(_primary_score_fields_from_profile(cached_structure.get("parser_profile")))
        _assign_data_source_roles(data_sources, score_fields)
        requires_confirmation, confirmation_reason = _target_confirmation(data_sources)
        target_structure = {
            "target": {"page_id": page_id, "title": title, "target_id": resolved_target_id, **_notion_parent_location_facts(parent)},
            "data_sources": data_sources,
            "relations": _build_relations(data_sources),
            "state_mapping": _build_state_mapping(data_sources, cached_structure),
            "asset_mapping": _build_asset_mapping(data_sources),
            "requires_confirmation": requires_confirmation,
            "confirmation_reason": confirmation_reason,
        }
        if "parser_profile" in cached_structure:
            target_structure["parser_profile"] = cached_structure["parser_profile"]

        cache.write_json(cache.config.targets_dir / f"{resolved_target_id}.json", target_structure)
        if alias:
            _save_alias(cache, alias, resolved_target_id, page_id, title)
        return target_structure

    if parent.get("type") == "data_source_id" and isinstance(parent.get("data_source_id"), str):
        data_source_id = parent["data_source_id"]
        data_source = adapter.retrieve_data_source(data_source_id)
        data_source_title = plain_title(data_source.get("title")) or data_source.get("name")
        location_facts = _data_source_location_facts(adapter, data_source)
        data_sources = {
            data_source_id: _scan_data_source(
                data_source_id,
                data_source,
                data_source_title,
                cached_structure,
                location_facts,
                _safe_list_data_source_templates(adapter, data_source_id),
            )
        }
        config_data = cache.read_json(cache.config.config_file, {})
        score_fields = _default_primary_score_fields(config_data)
        score_fields.update(_primary_score_fields_from_profile(cached_structure.get("parser_profile")))
        _assign_data_source_roles(data_sources, score_fields)
        if not any(data_source.get("role") == "primary" for data_source in data_sources.values()):
            data_sources[data_source_id]["role"] = "primary"
        requires_confirmation, confirmation_reason = _target_confirmation(data_sources)
        target_structure = {
            "target": {
                "page_id": page_id,
                "title": title,
                "target_id": resolved_target_id,
                "data_source_id": data_source_id,
                **location_facts,
            },
            "data_sources": data_sources,
            "relations": _build_relations(data_sources),
            "state_mapping": _build_state_mapping(data_sources, cached_structure),
            "asset_mapping": _build_asset_mapping(data_sources),
            "requires_confirmation": requires_confirmation,
            "confirmation_reason": confirmation_reason,
        }
        if "parser_profile" in cached_structure:
            target_structure["parser_profile"] = cached_structure["parser_profile"]

        cache.write_json(cache.config.targets_dir / f"{resolved_target_id}.json", target_structure)
        if alias:
            _save_alias(cache, alias, resolved_target_id, page_id, title)
        return target_structure

    children = adapter.list_block_children(page_id)

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
            location_facts = {
                **_database_parent_location_facts(database),
                "database_id": database_id,
                "parent_page_id": page_id,
                "source_block_id": block.get("id"),
                "source_block_type": block.get("type"),
            }
            data_sources[data_source_id] = _scan_data_source(
                data_source_id,
                data_source,
                _data_source_title(data_source, source, database, block),
                cached_structure,
                location_facts,
                _safe_list_data_source_templates(adapter, data_source_id),
            )

    config_data = cache.read_json(cache.config.config_file, {})
    score_fields = _default_primary_score_fields(config_data)
    score_fields.update(_primary_score_fields_from_profile(cached_structure.get("parser_profile")))
    _assign_data_source_roles(data_sources, score_fields)
    requires_confirmation, confirmation_reason = _target_confirmation(data_sources)
    target_structure = {
        "target": {"page_id": page_id, "title": title, "target_id": resolved_target_id, **_notion_parent_location_facts(parent)},
        "data_sources": data_sources,
        "relations": _build_relations(data_sources),
        "state_mapping": _build_state_mapping(data_sources, cached_structure),
        "asset_mapping": _build_asset_mapping(data_sources),
        "requires_confirmation": requires_confirmation,
        "confirmation_reason": confirmation_reason,
    }
    if "parser_profile" in cached_structure:
        target_structure["parser_profile"] = cached_structure["parser_profile"]

    cache.write_json(cache.config.targets_dir / f"{resolved_target_id}.json", target_structure)
    if alias:
        _save_alias(cache, alias, resolved_target_id, page_id, title)

    return target_structure


def scan_data_source_target(
    adapter: Any,
    data_source_id: str,
    cache: CacheStore,
    target_id: str | None = None,
    alias: str | None = None,
) -> dict[str, Any]:
    resolved_target_id = target_id or _default_target_id(data_source_id)
    cached_structure = cache.target_structure(resolved_target_id) or cache.target_structure_for_data_source(data_source_id) or {}
    data_source = adapter.retrieve_data_source(data_source_id)
    title = plain_title(data_source.get("title")) or data_source.get("name")
    location_facts = _data_source_location_facts(adapter, data_source)
    data_sources = {
        data_source_id: _scan_data_source(
            data_source_id,
            data_source,
            title,
            cached_structure,
            location_facts,
            _safe_list_data_source_templates(adapter, data_source_id),
        )
    }

    config_data = cache.read_json(cache.config.config_file, {})
    score_fields = _default_primary_score_fields(config_data)
    score_fields.update(_primary_score_fields_from_profile(cached_structure.get("parser_profile")))
    _assign_data_source_roles(data_sources, score_fields)
    requires_confirmation, confirmation_reason = _target_confirmation(data_sources)
    target_structure = {
        "target": {
            "page_id": location_facts.get("parent_page_id"),
            "title": title,
            "target_id": resolved_target_id,
            "data_source_id": data_source_id,
            **location_facts,
        },
        "data_sources": data_sources,
        "relations": _build_relations(data_sources),
        "state_mapping": _build_state_mapping(data_sources, cached_structure),
        "asset_mapping": _build_asset_mapping(data_sources),
        "requires_confirmation": requires_confirmation,
        "confirmation_reason": confirmation_reason,
    }
    if "parser_profile" in cached_structure:
        target_structure["parser_profile"] = cached_structure["parser_profile"]

    cache.write_json(cache.config.targets_dir / f"{resolved_target_id}.json", target_structure)
    if alias:
        _save_data_source_alias(cache, alias, resolved_target_id, data_source_id, title)

    return target_structure
