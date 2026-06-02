from __future__ import annotations

import json
import os
from typing import Any

from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
from capture_to_notion.notion_adapter import NotionAdapter
from capture_to_notion.profile_binder import bind_write_profile, resolve_write_profile
from capture_to_notion.scanner import scan_data_source_graph, scan_page_graph

SANDBOX_ALIAS = "ctn-e2e-sandbox"
DEFAULT_PARENT_ALIAS = "AI"

PAGE_TARGETS = {
    "ctn-e2e-plain-pages": "Plain Pages",
    "ctn-e2e-knowledge-notes": "Knowledge Notes",
    "ctn-e2e-url": "URL Captures",
    "ctn-e2e-view-source": "View Clone Source",
    "ctn-e2e-view-target": "View Clone Target",
}

AUTHOR_SCHEMA = {
    "Name": {"title": {}},
    "State": {"multi_select": {"options": [{"name": "initialized", "color": "gray"}, {"name": "completed", "color": "green"}]}},
    "Author Picture": {"files": {}},
    "Source": {"rich_text": {}},
}

GUEST_SCHEMA = {
    "Name": {"title": {}},
    "State": {"multi_select": {"options": [{"name": "initialized", "color": "gray"}, {"name": "completed", "color": "green"}]}},
    "Podcast": {"rich_text": {}},
    "Source": {"rich_text": {}},
}

BOOK_SCHEMA_BASE = {
    "Name": {"title": {}},
    "State": {"multi_select": {"options": [{"name": "initialized", "color": "gray"}, {"name": "completed", "color": "green"}]}},
    "Author": {"rich_text": {}},
    "ISBN": {"rich_text": {}},
    "Page Count": {"number": {"format": "number"}},
    "Cover": {"files": {}},
}

PODCAST_SCHEMA_BASE = {
    "Episode": {"title": {}},
    "State": {"multi_select": {"options": [{"name": "initialized", "color": "gray"}, {"name": "completed", "color": "green"}]}},
    "Podcast": {"rich_text": {}},
    "Platform": {"rich_text": {}},
    "Host": {"rich_text": {}},
    "URL": {"url": {}},
}

VIEW_SCHEMA = {
    "Name": {"title": {}},
    "State": {"select": {"options": [{"name": "Inbox", "color": "gray"}, {"name": "Done", "color": "green"}]}},
    "Priority": {"number": {"format": "number"}},
    "Notes": {"rich_text": {}},
}

VIEW_SOURCE_VIEWS = [
    {
        "name": "CTN E2E Inbox",
        "type": "table",
        "filter": {"property": "State", "select": {"equals": "Inbox"}},
        "sorts": [{"property": "Priority", "direction": "descending"}],
        "configuration": {"type": "table"},
    }
]

BOOK_PROFILE = {
    "content_type": "book",
    "alias": "ctn-e2e-books",
    "profile_id": "profile-ctn-e2e-books",
    "field_mapping": {
        "title": "Name",
        "state": "State",
        "author": "Author",
        "author_relation": "Author Page",
        "isbn": "ISBN",
        "page_count": "Page Count",
        "cover": "Cover",
    },
    "parser_profile": {
        "labels": {
            "title": ["书名", "标题"],
            "author": ["作者"],
            "author_relation": ["关联作者", "作者页面"],
            "isbn": ["ISBN", "isbn"],
            "page_count": ["页数"],
            "cover": ["封面"],
        },
        "value_types": {"page_count": "integer"},
        "required_schema_fields": ["cover", "author", "author_relation", "isbn", "page_count", "state"],
        "required_value_fields": ["author", "isbn", "page_count"],
        "summary_key_fields": ["cover", "author", "author_relation", "isbn", "page_count"],
        "trusted_field_sources": ["explicit", "profile", "user_binding"],
        "asset_trust_required_fields": ["cover"],
        "relation_completions": [
            {
                "source_record_key": "author_relation",
                "field_mapping": {"author_relation": "Name", "state": "State"},
                "labels": {"author_relation": ["关联作者", "作者页面"], "state": ["作者状态"]},
            }
        ],
    },
}

PODCAST_PROFILE = {
    "content_type": "podcast_episode",
    "alias": "ctn-e2e-podcasts",
    "profile_id": "profile-ctn-e2e-podcasts",
    "field_mapping": {
        "title": "Episode",
        "state": "State",
        "podcast": "Podcast",
        "platform": "Platform",
        "host": "Host",
        "guest_relation": "Guest Page",
        "url": "URL",
    },
    "parser_profile": {
        "labels": {
            "title": ["标题"],
            "podcast": ["播客"],
            "platform": ["平台", "收听平台"],
            "host": ["主播"],
            "guest_relation": ["嘉宾", "嘉宾页面"],
            "url": ["链接", "URL", "url"],
        },
        "trusted_field_sources": ["explicit", "profile", "user_binding"],
        "relation_completions": [
            {
                "source_record_key": "guest_relation",
                "field_mapping": {"guest_relation": "Name", "podcast": "Podcast", "state": "State"},
                "labels": {"guest_relation": ["嘉宾", "嘉宾页面"], "podcast": ["播客"], "state": ["嘉宾状态"]},
            }
        ],
    },
}


def graph_root_page_id(cache: CacheV2Store, alias_name: str) -> str | None:
    alias = cache.find_alias(alias_name)
    graph_id = alias.get("graph_id") if isinstance(alias, dict) else None
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    root = graph.get("root") if isinstance(graph, dict) else None
    if isinstance(root, dict) and root.get("kind") == "page" and isinstance(root.get("id"), str):
        return root["id"]
    return None


def graph_data_source_schema(cache: CacheV2Store, alias_name: str) -> dict[str, Any]:
    graph = graph_for_alias(cache, alias_name)
    data_sources = graph.get("data_sources") if isinstance(graph, dict) else None
    if not isinstance(data_sources, dict):
        return {}
    for data_source in data_sources.values():
        if isinstance(data_source, dict) and isinstance(data_source.get("schema"), dict):
            return data_source["schema"]
    return {}


def graph_for_alias(cache: CacheV2Store, alias_name: str) -> dict[str, Any]:
    alias = cache.find_alias(alias_name)
    graph_id = alias.get("graph_id") if isinstance(alias, dict) else None
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    return graph if isinstance(graph, dict) else {}


def data_source_id_by_title(graph: dict[str, Any], title: str) -> str | None:
    data_sources = graph.get("data_sources") if isinstance(graph, dict) else None
    if not isinstance(data_sources, dict):
        return None
    for data_source_id, data_source in data_sources.items():
        if isinstance(data_source, dict) and data_source.get("title") == title:
            return str(data_source_id)
    return None


def database_id_by_title(graph: dict[str, Any], title: str) -> str | None:
    databases = graph.get("databases") if isinstance(graph, dict) else None
    if not isinstance(databases, dict):
        return None
    for database_id, database in databases.items():
        if isinstance(database, dict) and database.get("title") == title:
            return str(database_id)
    return None


def alias_has_schema_fields(cache: CacheV2Store, alias_name: str, fields: set[str]) -> bool:
    return fields.issubset(set(graph_data_source_schema(cache, alias_name)))


def scan_page_alias(adapter: NotionAdapter, cache: CacheV2Store, page_id: str, alias_name: str) -> dict[str, Any]:
    graph = scan_page_graph(adapter, page_id, cache, graph_id=alias_name)
    cache.bind_alias(alias_name, graph_id=alias_name, profile_id=None, kind="graph")
    return graph


def ensure_page_alias(adapter: NotionAdapter, cache: CacheV2Store, alias_name: str, title: str, parent_page_id: str) -> str:
    existing = graph_root_page_id(cache, alias_name)
    if existing:
        return existing
    page = adapter.create_child_page(parent_page_id, title)
    page_id = str(page["id"])
    scan_page_alias(adapter, cache, page_id, alias_name)
    return page_id


def profile_resolves(cache: CacheV2Store, alias_name: str, content_type: str, required_fields: set[str]) -> bool:
    alias = cache.find_alias(alias_name)
    graph_id = alias.get("graph_id") if isinstance(alias, dict) else None
    profile_id = alias.get("profile_id") if isinstance(alias, dict) else None
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    profile = cache.read_profile(profile_id) if isinstance(profile_id, str) else None
    if not isinstance(graph, dict) or not isinstance(profile, dict):
        return False
    resolved = resolve_write_profile(graph, profile, content_type=content_type)
    field_mapping = resolved.get("field_mapping") if isinstance(resolved, dict) else None
    return isinstance(field_mapping, dict) and required_fields.issubset(set(field_mapping))


def bind_profile(cache: CacheV2Store, graph: dict[str, Any], data_source_id: str, profile_spec: dict[str, Any]) -> dict[str, Any]:
    field_mapping = profile_spec["field_mapping"]
    parser_profile = dict(profile_spec["parser_profile"])
    parser_profile["relation_completions"] = relation_completions_with_targets(parser_profile.get("relation_completions", []), graph)
    profile = bind_write_profile(
        graph,
        profile_id=profile_spec["profile_id"],
        content_type=profile_spec["content_type"],
        data_source_id=data_source_id,
        view_id=None,
        field_mapping=field_mapping,
        field_sources={key: "user_binding" for key in field_mapping},
        parser_profile=parser_profile,
        aliases=[profile_spec["alias"]],
    )
    cache.write_profile(profile_spec["profile_id"], profile)
    cache.bind_alias(profile_spec["alias"], graph_id=profile_spec["alias"], profile_id=profile_spec["profile_id"], kind="write_profile")
    return profile


def relation_property(target: dict[str, Any], *, single_property: bool = True) -> dict[str, Any]:
    relation: dict[str, Any] = {}
    if target.get("database_id"):
        relation["database_id"] = target["database_id"]
    if target.get("data_source_id"):
        relation["data_source_id"] = target["data_source_id"]
    if single_property:
        relation.update({"type": "single_property", "single_property": {}})
    return {"relation": relation}


def with_relation(schema: dict[str, Any], field_name: str, target: dict[str, Any]) -> dict[str, Any]:
    extended = dict(schema)
    extended[field_name] = relation_property(target)
    return extended


def relation_completions_with_targets(completions: Any, graph: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(completions, list):
        return []
    data_sources = graph.get("data_sources") if isinstance(graph, dict) else None
    if not isinstance(data_sources, dict):
        return [dict(item) for item in completions if isinstance(item, dict)]
    by_title = {value.get("title"): key for key, value in data_sources.items() if isinstance(value, dict)}
    patched = []
    for item in completions:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        if copy.get("source_record_key") == "author_relation" and "Authors" in by_title:
            copy["target_data_source_id"] = by_title["Authors"]
        if copy.get("source_record_key") == "guest_relation" and "Guests" in by_title:
            copy["target_data_source_id"] = by_title["Guests"]
        patched.append(copy)
    return patched


def ensure_database_alias(
    adapter: NotionAdapter,
    cache: CacheV2Store,
    *,
    alias_name: str,
    title: str,
    parent_page_id: str,
    schema: dict[str, Any],
    profile_spec: dict[str, Any] | None = None,
    views: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required_fields = set(schema)
    required_mapping = set(profile_spec["field_mapping"]) if profile_spec else set()
    if alias_has_schema_fields(cache, alias_name, required_fields) and (profile_spec is None or profile_resolves(cache, alias_name, profile_spec["content_type"], required_mapping)):
        alias = cache.find_alias(alias_name)
        graph = cache.read_graph(alias["graph_id"])
        data_source_id = data_source_id_by_title(graph, title)
        if data_source_id is None:
            raise RuntimeError(f"cached graph for {alias_name} does not contain data source titled {title!r}")
        return {"alias": alias_name, "status": "reused", "graph_id": alias["graph_id"], "data_source_id": data_source_id, "database_id": database_id_by_title(graph, title)}

    database = adapter.create_database(parent_page_id, title, schema, views=views)
    data_sources = database.get("data_sources")
    if not isinstance(data_sources, list) or not data_sources or not isinstance(data_sources[0], dict):
        raise RuntimeError(f"create database did not return data source: {title}")
    data_source_id = str(data_sources[0]["id"])
    graph = scan_page_graph(adapter, parent_page_id, cache, graph_id=alias_name)
    cache.bind_alias(alias_name, graph_id=alias_name, profile_id=None, kind="graph")
    if profile_spec is not None:
        bind_profile(cache, graph, data_source_id, profile_spec)
    return {"alias": alias_name, "status": "created", "graph_id": alias_name, "data_source_id": data_source_id, "database_id": database.get("id")}


def ensure_seed_page(adapter: NotionAdapter, data_source_id: str, title_field: str, title: str, extra: dict[str, Any] | None = None) -> str:
    existing = adapter.query_data_source(data_source_id, filters={"property": title_field, "title": {"equals": title}})
    if existing and isinstance(existing[0], dict) and existing[0].get("id"):
        return str(existing[0]["id"])
    properties: dict[str, Any] = {title_field: {"title": [{"text": {"content": title}}]}}
    state = extra.get("state") if isinstance(extra, dict) else None
    if state:
        properties["State"] = {"multi_select": [{"name": str(state)}]}
    page = adapter.create_page(data_source_id, properties)
    return str(page["id"])


def main() -> None:
    config = ensure_config()
    cache = CacheV2Store(config)
    adapter = NotionAdapter.from_config(config)
    parent_page_id = os.environ.get("CTN_E2E_SANDBOX_PARENT_PAGE_ID")
    parent_alias = os.environ.get("CTN_E2E_SANDBOX_PARENT_ALIAS", DEFAULT_PARENT_ALIAS)
    if not parent_page_id:
        parent_page_id = graph_root_page_id(cache, parent_alias)
    if not parent_page_id:
        raise SystemExit(f"No sandbox parent page found. Set CTN_E2E_SANDBOX_PARENT_PAGE_ID or scan parent alias {parent_alias!r}.")

    sandbox_page_id = ensure_page_alias(adapter, cache, SANDBOX_ALIAS, "CTN E2E Sandbox", parent_page_id)
    pages = {
        alias: ensure_page_alias(adapter, cache, alias, title, sandbox_page_id)
        for alias, title in PAGE_TARGETS.items()
    }

    authors = ensure_database_alias(adapter, cache, alias_name="ctn-e2e-authors", title="Authors", parent_page_id=sandbox_page_id, schema=AUTHOR_SCHEMA)
    guests = ensure_database_alias(adapter, cache, alias_name="ctn-e2e-guests", title="Guests", parent_page_id=sandbox_page_id, schema=GUEST_SCHEMA)
    book_schema = with_relation(BOOK_SCHEMA_BASE, "Author Page", authors)
    podcast_schema = with_relation(PODCAST_SCHEMA_BASE, "Guest Page", guests)

    databases = [
        authors,
        guests,
        ensure_database_alias(adapter, cache, alias_name="ctn-e2e-books", title="Books", parent_page_id=sandbox_page_id, schema=book_schema, profile_spec=BOOK_PROFILE),
        ensure_database_alias(adapter, cache, alias_name="ctn-e2e-podcasts", title="Podcasts", parent_page_id=sandbox_page_id, schema=podcast_schema, profile_spec=PODCAST_PROFILE),
        ensure_database_alias(adapter, cache, alias_name="ctn-e2e-view-source-db", title="View Source Items", parent_page_id=pages["ctn-e2e-view-source"], schema=VIEW_SCHEMA, views=VIEW_SOURCE_VIEWS),
        ensure_database_alias(adapter, cache, alias_name="ctn-e2e-view-target-db", title="View Target Items", parent_page_id=pages["ctn-e2e-view-target"], schema=VIEW_SCHEMA),
    ]

    ensure_seed_page(adapter, authors["data_source_id"], "Name", "CTN E2E Author", {"state": "initialized"})
    ensure_seed_page(adapter, guests["data_source_id"], "Name", "CTN E2E Guest", {"state": "initialized"})

    print(json.dumps({"sandbox_page_id": sandbox_page_id, "page_aliases": pages, "databases": databases}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
