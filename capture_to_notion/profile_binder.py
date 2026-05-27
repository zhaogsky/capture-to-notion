from __future__ import annotations

from typing import Any


def _graph_data_source(graph: dict[str, Any], data_source_id: str) -> dict[str, Any] | None:
    data_sources = graph.get("data_sources")
    if not isinstance(data_sources, dict):
        return None
    data_source = data_sources.get(data_source_id)
    return data_source if isinstance(data_source, dict) else None


def _graph_view(graph: dict[str, Any], view_id: str) -> dict[str, Any] | None:
    views = graph.get("views")
    if not isinstance(views, dict):
        return None
    view = views.get(view_id)
    return view if isinstance(view, dict) else None


def bind_write_profile(
    graph: dict[str, Any],
    *,
    profile_id: str,
    content_type: str,
    data_source_id: str,
    view_id: str | None,
    field_mapping: dict[str, str],
    field_sources: dict[str, str],
    state_mapping: dict[str, Any] | None = None,
    asset_mapping: dict[str, Any] | None = None,
    relation_mapping: dict[str, Any] | None = None,
    parser_profile: dict[str, Any] | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    if _graph_data_source(graph, data_source_id) is None:
        raise ValueError(f"data_source not found: {data_source_id}")
    if view_id is not None:
        view = _graph_view(graph, view_id)
        if view is None:
            raise ValueError(f"view not found: {view_id}")
        if view.get("data_source_id") != data_source_id:
            raise ValueError(f"view does not target data_source: {view_id} -> {data_source_id}")

    return {
        "cache_version": 2,
        "profile_id": profile_id,
        "graph_id": graph.get("graph_id"),
        "aliases": aliases or [],
        "write_profiles": {
            content_type: {
                "content_type": content_type,
                "canonical_view_id": view_id,
                "canonical_data_source_id": data_source_id,
                "field_mapping": dict(field_mapping),
                "field_sources": dict(field_sources),
                "state_mapping": state_mapping or {},
                "asset_mapping": asset_mapping or {},
                "relation_mapping": relation_mapping or {},
                "parser_profile": parser_profile or {},
            }
        },
    }


def resolve_write_profile(graph: dict[str, Any], profile: dict[str, Any], *, content_type: str) -> dict[str, Any] | None:
    write_profiles = profile.get("write_profiles")
    if not isinstance(write_profiles, dict):
        return None
    write_profile = write_profiles.get(content_type)
    if not isinstance(write_profile, dict):
        return None

    data_source_id = write_profile.get("canonical_data_source_id")
    if not isinstance(data_source_id, str) or _graph_data_source(graph, data_source_id) is None:
        return None

    view_id = write_profile.get("canonical_view_id")
    view = _graph_view(graph, view_id) if isinstance(view_id, str) else None
    if isinstance(view_id, str) and view is None:
        return None
    if view and view.get("data_source_id") != data_source_id:
        return None

    return {
        "target_kind": "view_backed_data_source" if view else "data_source",
        "data_source_id": data_source_id,
        "view_id": view_id if view else None,
        "view_name": view.get("name") if view else None,
        "view_type": view.get("type") if view else None,
        "selection_source": "write_profile",
        "field_mapping": write_profile.get("field_mapping", {}),
        "field_sources": write_profile.get("field_sources", {}),
        "state_mapping": write_profile.get("state_mapping", {}),
        "asset_mapping": write_profile.get("asset_mapping", {}),
        "relation_mapping": write_profile.get("relation_mapping", {}),
        "parser_profile": write_profile.get("parser_profile", {}),
    }
