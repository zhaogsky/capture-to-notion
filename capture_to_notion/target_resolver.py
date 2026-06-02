from __future__ import annotations

import copy
import re
from typing import Any

from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.models import CaptureInput
from capture_to_notion.path_utils import graph_object_path, graph_visual_path
from capture_to_notion.profile_binder import resolve_write_profile


FACT_KEYS = (
    "page_id",
    "parent_page_id",
    "data_source_id",
    "parent_data_source_id",
    "database_id",
    "parent_database_id",
    "target_id",
    "view_id",
    "view_name",
    "alias",
    "title",
)
LOCATION_FACT_KEYS = (
    "page_id",
    "parent_page_id",
    "data_source_id",
    "parent_data_source_id",
    "database_id",
    "parent_database_id",
)
TEXT_FACT_KEYS = ("alias", "title", "view_name")
CONTEXT_SCOPE_TOKENS = ("page", "parent", "child", "under", "context")


def _target_from_structure(structure: dict[str, Any] | None) -> dict[str, Any]:
    target = structure.get("target") if isinstance(structure, dict) else None
    return target if isinstance(target, dict) else {}


def _data_sources_from_structure(structure: dict[str, Any] | None) -> list[dict[str, Any]]:
    data_sources = structure.get("data_sources") if isinstance(structure, dict) else None
    if not isinstance(data_sources, dict):
        return []
    return [data_source for data_source in data_sources.values() if isinstance(data_source, dict)]


def _add_fact(facts: dict[str, set[str]], key: str, value: Any) -> None:
    if isinstance(value, str) and value:
        facts.setdefault(key, set()).add(value)


def _empty_facts() -> dict[str, set[str]]:
    return {key: set() for key in FACT_KEYS}


def _fact_fields(facts: dict[str, set[str]], keys: tuple[str, ...] = FACT_KEYS) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in keys:
        values = facts.get(key) or set()
        if values:
            fields[key] = sorted(values)[0]
    return fields


def _existing_page_id(
    capture: CaptureInput,
    structure: dict[str, Any] | None,
    alias: dict[str, Any] | None = None,
) -> str | None:
    if capture.existing_page_id:
        return capture.existing_page_id
    if isinstance(alias, dict) and alias.get("type") == "data_source":
        return None
    target = _target_from_structure(structure)
    page_id = target.get("page_id")
    data_source_id = target.get("data_source_id")
    if isinstance(page_id, str) and page_id and isinstance(data_source_id, str) and data_source_id:
        return page_id
    return None


def _structure_for_alias(alias: dict[str, Any], cache: CacheStore) -> tuple[str | None, dict[str, Any] | None]:
    target_id = alias.get("target_id")
    data_source_id = alias.get("data_source_id")
    if isinstance(target_id, str) and target_id:
        structure = cache.target_structure(target_id)
        if structure or not isinstance(data_source_id, str) or not data_source_id:
            return target_id, structure
    if isinstance(data_source_id, str) and data_source_id:
        structure = cache.target_structure_for_data_source(data_source_id)
        target = _target_from_structure(structure)
        resolved_target_id = target.get("target_id")
        if isinstance(resolved_target_id, str) and resolved_target_id:
            return resolved_target_id, structure
        return target_id if isinstance(target_id, str) else None, structure
    return None, None


def _facts_from_alias_structure(alias_name: str | None, alias: dict[str, Any] | None, structure: dict[str, Any] | None) -> dict[str, set[str]]:
    facts = _empty_facts()
    if alias_name:
        _add_fact(facts, "alias", alias_name)
    sources: list[dict[str, Any]] = []
    if isinstance(alias, dict):
        sources.append(alias)
    target = _target_from_structure(structure)
    if target:
        sources.append(target)
    sources.extend(_data_sources_from_structure(structure))
    for source in sources:
        for key in FACT_KEYS:
            _add_fact(facts, key, source.get(key))
        parent = source.get("parent")
        if isinstance(parent, dict):
            _add_fact(facts, "parent_page_id", parent.get("page_id"))
            _add_fact(facts, "parent_data_source_id", parent.get("data_source_id"))
            _add_fact(facts, "parent_database_id", parent.get("database_id"))
    return facts


def _resolved_from_alias(
    *,
    capture: CaptureInput,
    cache: CacheStore,
    alias_name: str,
    alias: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    target_id, structure = _structure_for_alias(alias, cache)
    target = _target_from_structure(structure)
    facts = _facts_from_alias_structure(alias_name, alias, structure)
    page_id = alias.get("page_id") or target.get("page_id") or _fact_fields(facts).get("page_id")
    data_source_id = alias.get("data_source_id") or target.get("data_source_id") or _fact_fields(facts).get("data_source_id")
    if not isinstance(data_source_id, str):
        data_source_id = None
    if not isinstance(page_id, str):
        page_id = None
    resolved = {
        "status": "cache_hit" if structure else "cache_missing",
        "source": source,
        "alias": alias_name,
        "target_id": target_id,
        "page_id": page_id,
        "data_source_id": data_source_id,
        "structure": structure,
        "existing_page_id": _existing_page_id(capture, structure, alias),
        "facts": facts,
        **_fact_fields(facts, LOCATION_FACT_KEYS),
    }
    return {key: value for key, value in resolved.items() if value is not None}


def _route_candidates(cache: CacheStore, content_type: str) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    routes = cache.routes().get(content_type, {}).get("preferred_targets", [])
    if not isinstance(routes, list):
        return []
    candidates: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        alias_name = route.get("alias")
        if not isinstance(alias_name, str) or not alias_name:
            continue
        alias = cache.find_alias(alias_name)
        if isinstance(alias, dict):
            candidates.append((alias_name, route, alias))
        else:
            candidates.append((alias_name, route, None))
    return candidates


def _is_reliable_route(route: dict[str, Any], structure: dict[str, Any] | None) -> bool:
    if not structure:
        return False
    confidence = route.get("confidence", "medium")
    return confidence in {"high", "medium", None}


def _has_context_gate(capture: CaptureInput) -> bool:
    if capture.target_context_hint:
        return True
    scope_hint = capture.target_scope_hint.casefold() if isinstance(capture.target_scope_hint, str) else ""
    return any(token in scope_hint for token in CONTEXT_SCOPE_TOKENS)


def _is_location_context(capture: CaptureInput) -> bool:
    scope_hint = capture.target_scope_hint.casefold() if isinstance(capture.target_scope_hint, str) else ""
    context_hint = capture.target_context_hint.casefold() if isinstance(capture.target_context_hint, str) else ""
    return any(token in scope_hint or token in context_hint for token in CONTEXT_SCOPE_TOKENS)


def _with_context_fields(capture: CaptureInput, resolution: dict[str, Any], **fields: Any) -> dict[str, Any]:
    enriched = {
        **resolution,
        "target_context_hint": capture.target_context_hint,
        "target_scope_hint": capture.target_scope_hint,
        **fields,
    }
    return {key: value for key, value in enriched.items() if value is not None}


def _context_facts(alias_name: str, alias: dict[str, Any], structure: dict[str, Any] | None) -> dict[str, set[str]]:
    return _facts_from_alias_structure(alias_name, alias, structure)


def _resolution_facts(resolution: dict[str, Any]) -> dict[str, set[str]]:
    existing = resolution.get("facts")
    facts = _empty_facts()
    if isinstance(existing, dict):
        for key in FACT_KEYS:
            values = existing.get(key)
            if isinstance(values, set):
                facts[key].update(value for value in values if isinstance(value, str) and value)
            elif isinstance(values, list):
                facts[key].update(value for value in values if isinstance(value, str) and value)
            elif isinstance(values, str) and values:
                facts[key].add(values)
    for key in FACT_KEYS:
        _add_fact(facts, key, resolution.get(key))
    structure_facts = _facts_from_alias_structure(
        resolution.get("alias") if isinstance(resolution.get("alias"), str) else None,
        None,
        resolution.get("structure") if isinstance(resolution.get("structure"), dict) else None,
    )
    for key in FACT_KEYS:
        facts[key].update(structure_facts[key])
    return facts


def _text_fragments(value: str) -> list[str]:
    fragments = [value]
    fragments.extend(re.split(r"[\s/\\|｜,_，:：;；()（）\[\]【】<>《》\-]+", value))
    return [fragment.casefold() for fragment in fragments if len(fragment.strip()) >= 3]


def _matching_text_source(resolution: dict[str, Any], context_hint: str | None) -> str | None:
    if not isinstance(context_hint, str) or not context_hint.strip():
        return None
    context = context_hint.casefold()
    resolved = _resolution_facts(resolution)
    for key in ("page_id", "target_id", "data_source_id", "database_id"):
        for value in resolved[key]:
            if value.casefold() in context:
                return f"{key}_text_match"
    for key in TEXT_FACT_KEYS:
        for value in resolved[key]:
            if any(fragment in context for fragment in _text_fragments(value)):
                return f"{key}_text_match"
    return None


def _context_facts_from_text(context_hint: str | None) -> dict[str, set[str]]:
    facts = _empty_facts()
    if not isinstance(context_hint, str) or not context_hint.strip():
        return facts
    patterns = (
        ("parent_page_id", r"parent\s+page\s+id\s+([A-Za-z0-9_-]+)"),
        ("page_id", r"(?<!parent\s)page\s+id\s+([A-Za-z0-9_-]+)"),
        ("parent_data_source_id", r"parent\s+data[-_\s]?source\s+id\s+([A-Za-z0-9_-]+)"),
        ("data_source_id", r"(?<!parent\s)data[-_\s]?source\s+id\s+([A-Za-z0-9_-]+)"),
        ("parent_database_id", r"parent\s+database\s+id\s+([A-Za-z0-9_-]+)"),
        ("database_id", r"(?<!parent\s)database\s+id\s+([A-Za-z0-9_-]+)"),
        ("target_id", r"target\s+id\s+([A-Za-z0-9_-]+)"),
    )
    for key, pattern in patterns:
        for match in re.finditer(pattern, context_hint, flags=re.IGNORECASE):
            _add_fact(facts, key, match.group(1))
    return facts


def _matching_location_source(resolved: dict[str, set[str]], context: dict[str, set[str]]) -> str | None:
    checks = (
        ("parent_page_id", ("parent_page_id", "page_id"), "parent_page_id_match"),
        ("page_id", ("page_id",), "page_id_match"),
        ("parent_data_source_id", ("parent_data_source_id", "data_source_id"), "parent_data_source_id_match"),
        ("data_source_id", ("data_source_id",), "data_source_id_match"),
        ("parent_database_id", ("parent_database_id", "database_id"), "parent_database_id_match"),
        ("database_id", ("database_id",), "database_id_match"),
        ("target_id", ("target_id",), "target_id_match"),
    )
    for resolved_key, context_keys, source in checks:
        resolved_values = resolved.get(resolved_key) or set()
        if not resolved_values:
            continue
        context_values: set[str] = set()
        for context_key in context_keys:
            context_values.update(context.get(context_key) or set())
        if context_values and resolved_values & context_values:
            return source
    return None


def _has_location_facts(facts: dict[str, set[str]]) -> bool:
    return any(facts.get(key) for key in LOCATION_FACT_KEYS)


def _has_parent_location_facts(facts: dict[str, set[str]]) -> bool:
    return any(facts.get(key) for key in ("parent_page_id", "parent_data_source_id", "parent_database_id"))


def _location_mismatch_possible(resolved: dict[str, set[str]], context: dict[str, set[str]]) -> bool:
    pairs = (
        ("parent_page_id", ("parent_page_id", "page_id")),
        ("page_id", ("page_id",)),
        ("parent_data_source_id", ("parent_data_source_id", "data_source_id")),
        ("data_source_id", ("data_source_id",)),
        ("parent_database_id", ("parent_database_id", "database_id")),
        ("database_id", ("database_id",)),
        ("target_id", ("target_id",)),
    )
    for resolved_key, context_keys in pairs:
        resolved_values = resolved.get(resolved_key) or set()
        context_values: set[str] = set()
        for context_key in context_keys:
            context_values.update(context.get(context_key) or set())
        if resolved_values and context_values:
            return True
    return False


def _cache_completeness(capture: CaptureInput, resolution: dict[str, Any]) -> dict[str, Any]:
    facts = _resolution_facts(resolution)
    available = [key for key in LOCATION_FACT_KEYS if facts.get(key)]
    required_any = ["parent_page_id", "parent_data_source_id", "parent_database_id"]
    if capture.target_scope_hint and "page" in capture.target_scope_hint.casefold():
        required_any = ["page_id", "parent_page_id"]
    if capture.target_scope_hint and "data" in capture.target_scope_hint.casefold():
        required_any = ["parent_page_id", "parent_data_source_id", "parent_database_id"]
    return {
        "status": "incomplete",
        "available_location_facts": available,
        "required_any_location_facts": required_any,
        "missing_location_facts": [key for key in required_any if key not in available],
    }


def _sync_request(capture: CaptureInput, resolution: dict[str, Any]) -> dict[str, Any]:
    sync = {
        "scope": capture.target_scope_hint or "target_context",
        "target_id": resolution.get("target_id"),
        "page_id": resolution.get("page_id"),
        "data_source_id": resolution.get("data_source_id"),
        "alias": resolution.get("alias"),
    }
    return {key: value for key, value in sync.items() if value is not None}


def _v2_sync_request(capture: CaptureInput, resolution: dict[str, Any]) -> dict[str, Any]:
    sync = {
        "scope": capture.target_scope_hint or "target_context",
        "target_id": resolution.get("target_id"),
        "alias": resolution.get("alias"),
    }
    if isinstance(resolution.get("page_id"), str):
        sync["page_id"] = resolution["page_id"]
    elif isinstance(resolution.get("data_source_id"), str):
        sync["data_source_id"] = resolution["data_source_id"]
    return {key: value for key, value in sync.items() if value is not None}


def _resolve_alias(capture: CaptureInput, cache: CacheStore, alias_name: str, source: str) -> dict[str, Any] | None:
    alias = cache.find_alias(alias_name)
    if not isinstance(alias, dict):
        return None
    alias_type = alias.get("type")
    resolved_source = "data_source_alias" if source == "target_hint_alias" and alias_type == "data_source" else source
    return _resolved_from_alias(
        capture=capture,
        cache=cache,
        alias_name=alias_name,
        alias=alias,
        source=resolved_source,
    )


def _resolve_context_target(capture: CaptureInput, cache: CacheStore) -> dict[str, Any]:
    target_hint = capture.target_hint
    context_hint = capture.target_context_hint

    if target_hint:
        resolved = _resolve_alias(capture, cache, target_hint, "target_hint_alias")
        if resolved is None:
            return _with_context_fields(
                capture,
                {"status": "target_not_resolved", "source": "target_hint", "alias": target_hint},
                target_context_verified=False,
            )
    elif context_hint:
        resolved = _resolve_alias(capture, cache, context_hint, "target_context_alias")
        if resolved is None:
            return _with_context_fields(
                capture,
                {"status": "target_context_unverified", "source": "target_context_hint", "alias": context_hint},
                target_context_verified=False,
            )
    else:
        return _with_context_fields(
            capture,
            {"status": "target_context_unverified", "source": "target_scope_hint"},
            target_context_verified=False,
        )

    if not context_hint:
        return _with_context_fields(
            capture,
            resolved,
            target_context_verified=True,
            context_verification_source="target_hint",
        )

    resolved_facts = _resolution_facts(resolved)
    context_alias = cache.find_alias(context_hint)
    if isinstance(context_alias, dict):
        _, context_structure = _structure_for_alias(context_alias, cache)
        context = _context_facts(context_hint, context_alias, context_structure)
    else:
        context = _context_facts_from_text(context_hint)

    location_source = _matching_location_source(resolved_facts, context)
    if location_source is not None:
        return _with_context_fields(
            capture,
            resolved,
            target_context_verified=True,
            context_verification_source=location_source,
        )

    text_source = _matching_text_source(resolved, context_hint)
    if _is_location_context(capture):
        if _location_mismatch_possible(resolved_facts, context):
            return _with_context_fields(
                capture,
                {**resolved, "status": "target_context_mismatch"},
                target_context_verified=False,
            )
        if target_hint and (not _has_location_facts(context) or not _has_parent_location_facts(resolved_facts)):
            return _with_context_fields(
                capture,
                {**resolved, "status": "target_context_cache_incomplete"},
                target_context_verified=False,
                cache_completeness=_cache_completeness(capture, resolved),
                sync=_sync_request(capture, resolved),
            )

    if text_source is None:
        return _with_context_fields(
            capture,
            {**resolved, "status": "target_context_mismatch" if isinstance(context_alias, dict) else "target_context_unverified"},
            target_context_verified=False,
        )

    return _with_context_fields(
        capture,
        resolved,
        target_context_verified=True,
        context_verification_source=text_source,
    )


def _v2_page_title(graph: dict[str, Any], page_id: str | None) -> str | None:
    pages = graph.get("pages") if isinstance(graph.get("pages"), dict) else {}
    page = pages.get(page_id) if isinstance(page_id, str) else None
    if not isinstance(page, dict):
        return None
    title = page.get("title")
    return title if isinstance(title, str) and title else None


def _v2_context_structure(graph: dict[str, Any], graph_id: str) -> dict[str, Any]:
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    page_id = root.get("id") if root.get("kind") == "page" and isinstance(root.get("id"), str) else None
    target = {"target_id": graph_id}
    if page_id:
        target["page_id"] = page_id
        title = _v2_page_title(graph, page_id)
        if title:
            target["title"] = title
    elif root.get("kind") == "data_source" and isinstance(root.get("id"), str):
        target["data_source_id"] = root["id"]
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    return {"target": target, "data_sources": data_sources}


def _v2_context_facts(cache: CacheV2Store, alias_name: str | None) -> dict[str, set[str]]:
    if not isinstance(alias_name, str) or not alias_name:
        return _empty_facts()
    alias = cache.find_alias(alias_name)
    if not isinstance(alias, dict):
        return _context_facts_from_text(alias_name)
    graph_id = alias.get("graph_id")
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    if not isinstance(graph, dict):
        return _context_facts_from_text(alias_name)
    alias_facts = {"target_id": graph_id, "alias": alias_name}
    return _facts_from_alias_structure(alias_name, alias_facts, _v2_context_structure(graph, graph_id))


def _v2_target_path_fields(
    graph: dict[str, Any],
    data_source_id: str | None,
    page_id: str | None,
    view_id: str | None = None,
) -> dict[str, Any]:
    path_info = graph_object_path(graph, data_source_id, "data_source") if data_source_id else graph_object_path(graph, page_id, "page")
    fields = {"target_path_complete": bool(path_info.get("path_complete"))}
    path = path_info.get("path")
    if isinstance(path, str) and path:
        fields["target_path"] = path
    visual_path_info = graph_visual_path(graph, view_id, "view") if view_id else {}
    visual_path = visual_path_info.get("path") if isinstance(visual_path_info, dict) else None
    if isinstance(visual_path, str) and visual_path:
        fields["visual_path"] = visual_path
        fields["visual_path_complete"] = bool(visual_path_info.get("path_complete"))
    return fields



def _relation_target_refs_from_schema(schema: dict[str, Any]) -> tuple[set[str], set[str]]:
    target_data_source_ids: set[str] = set()
    target_database_ids: set[str] = set()
    for field_schema in schema.values():
        if not isinstance(field_schema, dict) or field_schema.get("type") != "relation":
            continue
        target_data_source_id = field_schema.get("target_data_source_id")
        if isinstance(target_data_source_id, str) and target_data_source_id:
            target_data_source_ids.add(target_data_source_id)
        target_database_id = field_schema.get("target_database_id")
        if isinstance(target_database_id, str) and target_database_id:
            target_database_ids.add(target_database_id)
        relation_schema = field_schema.get("relation")
        if isinstance(relation_schema, dict):
            relation_data_source_id = relation_schema.get("data_source_id")
            if isinstance(relation_data_source_id, str) and relation_data_source_id:
                target_data_source_ids.add(relation_data_source_id)
            relation_database_id = relation_schema.get("database_id")
            if isinstance(relation_database_id, str) and relation_database_id:
                target_database_ids.add(relation_database_id)
    return target_data_source_ids, target_database_ids



def _with_cached_relation_target_data_sources(graph: dict[str, Any], cache: CacheV2Store) -> dict[str, Any]:
    data_sources = graph.get("data_sources")
    if not isinstance(data_sources, dict):
        return graph
    target_data_source_ids: set[str] = set()
    target_database_ids: set[str] = set()
    existing_database_ids = {
        data_source.get("database_id")
        for data_source in data_sources.values()
        if isinstance(data_source, dict) and isinstance(data_source.get("database_id"), str)
    }
    for data_source in data_sources.values():
        schema = data_source.get("schema") if isinstance(data_source, dict) else None
        if isinstance(schema, dict):
            data_source_ids, database_ids = _relation_target_refs_from_schema(schema)
            target_data_source_ids.update(data_source_ids)
            target_database_ids.update(database_ids)
    missing_target_ids = [target_id for target_id in sorted(target_data_source_ids) if target_id not in data_sources]
    missing_database_ids = [database_id for database_id in sorted(target_database_ids) if database_id not in existing_database_ids]
    if not missing_target_ids and not missing_database_ids:
        return graph

    graph_copy = copy.deepcopy(graph)
    merged_data_sources = graph_copy.setdefault("data_sources", {})
    if not isinstance(merged_data_sources, dict):
        return graph
    merged_databases = graph_copy.setdefault("databases", {})
    if not isinstance(merged_databases, dict):
        merged_databases = {}
        graph_copy["databases"] = merged_databases
    merged_pages = graph_copy.setdefault("pages", {})
    if not isinstance(merged_pages, dict):
        merged_pages = {}
        graph_copy["pages"] = merged_pages

    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for target_id in missing_target_ids:
        match = cache.find_graph_data_source(target_id)
        if match is not None:
            matches.append(match)
    for database_id in missing_database_ids:
        match = cache.find_graph_data_source_by_database(database_id)
        if match is not None:
            matches.append(match)

    for target_graph, target_data_source in matches:
        target_id = target_data_source.get("data_source_id")
        if isinstance(target_id, str) and target_id:
            merged_data_sources.setdefault(target_id, copy.deepcopy(target_data_source))
        database_id = target_data_source.get("database_id")
        target_databases = target_graph.get("databases")
        if isinstance(database_id, str) and isinstance(target_databases, dict) and isinstance(target_databases.get(database_id), dict):
            merged_databases.setdefault(database_id, copy.deepcopy(target_databases[database_id]))
        target_pages = target_graph.get("pages")
        if isinstance(target_pages, dict):
            for page_id, page in target_pages.items():
                if isinstance(page_id, str) and isinstance(page, dict):
                    merged_pages.setdefault(page_id, copy.deepcopy(page))
    return graph_copy



def _v2_structure(
    graph: dict[str, Any],
    profile: dict[str, Any],
    data_source_id: str,
    resolved: dict[str, Any],
    page_id: str | None,
) -> dict[str, Any]:
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    data_source = data_sources.get(data_source_id) if isinstance(data_sources, dict) else None
    normalized_data_source = dict(data_source) if isinstance(data_source, dict) else {}
    normalized_data_source.setdefault("data_source_id", data_source_id)
    normalized_data_source["fields"] = dict(resolved.get("field_mapping") or {})
    normalized_data_source["field_sources"] = dict(resolved.get("field_sources") or {})
    if isinstance(resolved.get("parser_profile"), dict):
        normalized_data_source["parser_profile"] = resolved["parser_profile"]
    target = {
        "page_id": page_id,
        "target_id": graph.get("graph_id"),
        "data_source_id": data_source_id,
    }
    page_title = _v2_page_title(graph, page_id)
    if page_title:
        target["title"] = page_title
    return {
        "cache_version": 2,
        "graph_id": graph.get("graph_id"),
        "profile_id": profile.get("profile_id"),
        "target": target,
        "data_sources": {data_source_id: normalized_data_source},
        "views": graph.get("views", {}),
        "asset_mapping": resolved.get("asset_mapping", {}),
        "relation_mapping": resolved.get("relation_mapping", {}),
        "state_mapping": resolved.get("state_mapping", {}),
        "requires_confirmation": False,
        "confirmation_reason": None,
        "graph": graph,
        "profile": profile,
    }


def _resolve_v2_capture_target(capture: CaptureInput, cache: CacheV2Store, content_type: str) -> dict[str, Any]:
    alias_name = capture.target_hint
    if not alias_name:
        return {"status": "v2_target_missing", "source": "missing"}
    alias = cache.find_alias(alias_name)
    if not isinstance(alias, dict):
        return {"status": "v2_target_missing", "source": "target_hint", "alias": alias_name}

    graph_id = alias.get("graph_id")
    profile_id = alias.get("profile_id")
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    if not isinstance(graph, dict):
        return {"status": "v2_target_missing", "source": "v2_alias", "alias": alias_name, "graph_id": graph_id}
    profile = cache.read_profile(profile_id) if isinstance(profile_id, str) else None
    if not isinstance(profile, dict):
        return {
            "status": "write_profile_missing",
            "source": "v2_alias",
            "alias": alias_name,
            "graph_id": graph_id,
            "profile_id": profile_id,
        }

    resolved = resolve_write_profile(graph, profile, content_type=content_type)
    if resolved is None:
        return {
            "status": "write_profile_missing",
            "source": "v2_profile",
            "alias": alias_name,
            "graph_id": graph_id,
            "profile_id": profile_id,
        }

    data_source_id = resolved["data_source_id"]
    graph = _with_cached_relation_target_data_sources(graph, cache)
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    page_id = root.get("id") if root.get("kind") == "page" else None
    structure = _v2_structure(graph, profile, data_source_id, resolved, page_id)
    facts = _facts_from_alias_structure(alias_name, {"target_id": graph_id, "alias": alias_name}, structure)
    base = {
        "status": "cache_hit",
        "source": "v2_profile",
        "alias": alias_name,
        "target_id": graph_id,
        "graph_id": graph_id,
        "profile_id": profile_id,
        "page_id": page_id,
        "data_source_id": data_source_id,
        "view_id": resolved.get("view_id"),
        "view_name": resolved.get("view_name"),
        "view_type": resolved.get("view_type"),
        "target_kind": resolved.get("target_kind"),
        "existing_page_id": capture.existing_page_id,
        "context_verification_source": "write_profile",
        "structure": structure,
        "write_profile": resolved,
        "facts": facts,
        **_fact_fields(facts, LOCATION_FACT_KEYS),
        **_v2_target_path_fields(graph, data_source_id, page_id, resolved.get("view_id")),
    }

    if not _has_context_gate(capture):
        return {key: value for key, value in base.items() if value is not None}
    if not capture.target_context_hint:
        return _with_context_fields(
            capture,
            base,
            target_context_verified=True,
            context_verification_source="target_hint",
        )

    resolved_facts = _resolution_facts(base)
    context = _v2_context_facts(cache, capture.target_context_hint)
    if not _has_location_facts(context):
        context = _context_facts_from_text(capture.target_context_hint)
    location_source = _matching_location_source(resolved_facts, context)
    if location_source is not None:
        return _with_context_fields(
            capture,
            base,
            target_context_verified=True,
            context_verification_source=location_source,
        )

    text_source = _matching_text_source(base, capture.target_context_hint)
    if _is_location_context(capture):
        if _location_mismatch_possible(resolved_facts, context):
            return _with_context_fields(
                capture,
                {**base, "status": "target_context_mismatch"},
                target_context_verified=False,
            )
        if not _has_location_facts(context) or not _has_parent_location_facts(resolved_facts):
            if root.get("kind") != "page" and _has_location_facts(context) and not _has_parent_location_facts(resolved_facts):
                return _with_context_fields(
                    capture,
                    {**base, "status": "target_context_cache_incomplete"},
                    target_context_verified=False,
                    cache_completeness=_cache_completeness(capture, base),
                    sync=_v2_sync_request(capture, base),
                )
            return _with_context_fields(
                capture,
                base,
                target_context_verified=False,
                cache_completeness=_cache_completeness(capture, base),
            )

    if text_source is None:
        return _with_context_fields(
            capture,
            {**base, "status": "target_context_unverified"},
            target_context_verified=False,
        )

    return _with_context_fields(
        capture,
        base,
        target_context_verified=True,
        context_verification_source=text_source,
    )


def resolve_capture_target(capture: CaptureInput, cache: CacheStore | CacheV2Store, content_type: str) -> dict[str, Any]:
    """Resolve capture target while preserving existing cache shapes."""
    if isinstance(cache, CacheV2Store):
        return _resolve_v2_capture_target(capture, cache, content_type)

    if _has_context_gate(capture):
        return _resolve_context_target(capture, cache)

    if capture.target_hint:
        resolved = _resolve_alias(capture, cache, capture.target_hint, "target_hint_alias")
        if resolved is None:
            return {
                "status": "target_not_resolved",
                "source": "target_hint",
                "alias": capture.target_hint,
            }
        return resolved

    candidates = _route_candidates(cache, content_type)
    reliable: list[dict[str, Any]] = []
    for alias_name, route, alias in candidates:
        if not isinstance(alias, dict):
            continue
        resolved = _resolved_from_alias(
            capture=capture,
            cache=cache,
            alias_name=alias_name,
            alias=alias,
            source="route_preferred_target",
        )
        if _is_reliable_route(route, resolved.get("structure")):
            reliable.append(resolved)

    if len(reliable) == 1:
        return reliable[0]
    if len(reliable) > 1:
        return {
            "status": "ambiguous_target",
            "source": "route_preferred_target",
            "candidates": [
                {key: candidate.get(key) for key in ("alias", "target_id", "page_id", "data_source_id") if candidate.get(key)}
                for candidate in reliable
            ],
        }
    return {"status": "target_missing", "source": "missing"}
