from __future__ import annotations

from typing import Any


WORKSPACE_LABEL = "工作区顶层"


def _node_title(node: dict[str, Any] | None, fallback_id: str | None = None) -> str | None:
    if not isinstance(node, dict):
        return fallback_id
    for key in ("title", "name"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback_id


def _page_by_id(graph: dict[str, Any], page_id: str | None) -> dict[str, Any] | None:
    pages = graph.get("pages")
    page = pages.get(page_id) if isinstance(pages, dict) and page_id else None
    return page if isinstance(page, dict) else None


def _database_by_id(graph: dict[str, Any], database_id: str | None) -> dict[str, Any] | None:
    databases = graph.get("databases")
    database = databases.get(database_id) if isinstance(databases, dict) and database_id else None
    return database if isinstance(database, dict) else None


def _data_source_by_id(graph: dict[str, Any], data_source_id: str | None) -> dict[str, Any] | None:
    data_sources = graph.get("data_sources")
    if not isinstance(data_sources, dict) or not data_source_id:
        return None
    data_source = data_sources.get(data_source_id)
    if isinstance(data_source, dict):
        return data_source
    for candidate in data_sources.values():
        if isinstance(candidate, dict) and candidate.get("data_source_id") == data_source_id:
            return candidate
    return None


def _parent_ref(node: dict[str, Any], kind: str) -> tuple[str, str | None] | None:
    parent = node.get("parent")
    if isinstance(parent, dict):
        parent_type = parent.get("type")
        parent_id = parent.get("id")
        if isinstance(parent_type, str):
            if parent_type == "workspace":
                return ("workspace", "workspace")
            if isinstance(parent_id, str) and parent_id:
                return (parent_type, parent_id)

    if kind == "database":
        parent_page_id = node.get("parent_page_id")
        if isinstance(parent_page_id, str) and parent_page_id:
            return ("page_id", parent_page_id)

    if kind == "data_source":
        for field_name, parent_type in (
            ("database_id", "database_id"),
            ("parent_database_id", "database_id"),
            ("parent_page_id", "page_id"),
            ("parent_data_source_id", "data_source_id"),
        ):
            parent_id = node.get(field_name)
            if isinstance(parent_id, str) and parent_id:
                return (parent_type, parent_id)
        database_parent = node.get("database_parent")
        if isinstance(database_parent, dict):
            parent_type = database_parent.get("type")
            parent_id = database_parent.get("id")
            if parent_type == "workspace":
                return ("workspace", "workspace")
            if isinstance(parent_type, str) and isinstance(parent_id, str) and parent_id:
                return (parent_type, parent_id)

    return None


def _node_for_ref(graph: dict[str, Any], ref_type: str, ref_id: str | None) -> tuple[str, dict[str, Any] | None]:
    if ref_type == "page_id":
        return ("page", _page_by_id(graph, ref_id))
    if ref_type == "database_id":
        return ("database", _database_by_id(graph, ref_id))
    if ref_type == "data_source_id":
        return ("data_source", _data_source_by_id(graph, ref_id))
    return (ref_type, None)


def graph_visual_path(graph: dict[str, Any], object_id: str | None, kind: str | None = None) -> dict[str, Any]:
    if not isinstance(graph, dict) or not object_id:
        return {"path": None, "path_complete": False}
    node = None
    if kind == "view" or kind is None:
        views = graph.get("views")
        node = views.get(object_id) if isinstance(views, dict) else None
    if not isinstance(node, dict):
        return {"path": None, "path_complete": False}
    location = node.get("location")
    if not isinstance(location, dict) or location.get("type") != "page_id":
        return {"path": None, "path_complete": False}
    page_path = graph_object_path(graph, location.get("id"), "page")
    path = page_path.get("path")
    labels = [path] if isinstance(path, str) and path else []
    section_path = location.get("section_path")
    if isinstance(section_path, list):
        labels.extend(value for value in section_path if isinstance(value, str) and value)
    display_title = location.get("display_title") or node.get("name") or node.get("title")
    if isinstance(display_title, str) and display_title:
        labels.append(display_title)
    return {"path": " / ".join(labels) or None, "path_complete": bool(page_path.get("path_complete")) and bool(labels)}


def graph_object_path(graph: dict[str, Any], object_id: str | None, kind: str | None = None) -> dict[str, Any]:
    if not isinstance(graph, dict) or not object_id:
        return {"path": None, "path_complete": False}

    if kind == "page" or kind is None:
        node = _page_by_id(graph, object_id)
        if node is not None:
            return _graph_path_from_node(graph, node, "page", object_id)
    if kind == "database" or kind is None:
        node = _database_by_id(graph, object_id)
        if node is not None:
            return _graph_path_from_node(graph, node, "database", object_id)
    if kind == "data_source" or kind is None:
        node = _data_source_by_id(graph, object_id)
        if node is not None:
            return _graph_path_from_node(graph, node, "data_source", object_id)

    return {"path": None, "path_complete": False}


def _graph_path_from_node(graph: dict[str, Any], node: dict[str, Any], kind: str, fallback_id: str) -> dict[str, Any]:
    labels: list[str] = []
    seen: set[tuple[str, str]] = set()
    current = node
    current_kind = kind
    current_id = fallback_id

    while isinstance(current, dict):
        key = (current_kind, current_id)
        if key in seen:
            return {"path": " / ".join(reversed(labels)) or None, "path_complete": False}
        seen.add(key)

        title = _node_title(current, current_id)
        if title:
            labels.append(title)

        parent = _parent_ref(current, current_kind)
        if parent is None:
            return {"path": " / ".join(reversed(labels)) or None, "path_complete": False}
        parent_type, parent_id = parent
        if parent_type == "workspace":
            labels.append(WORKSPACE_LABEL)
            return {"path": " / ".join(reversed(labels)), "path_complete": True}

        parent_kind, parent_node = _node_for_ref(graph, parent_type, parent_id)
        if parent_node is None or parent_id is None:
            return {"path": " / ".join(reversed(labels)) or None, "path_complete": False}
        current = parent_node
        current_kind = parent_kind
        current_id = parent_id

    return {"path": " / ".join(reversed(labels)) or None, "path_complete": False}
