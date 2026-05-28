from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


class CliInputError(ValueError):
    """Raised when CLI input cannot be loaded or parsed."""

from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.classifier import classify_content_type
from capture_to_notion.config import config_root, ensure_config
from capture_to_notion.diagnostics import doctor_report, migrate_legacy_config, version_info
from capture_to_notion.profile_binder import bind_write_profile
from capture_to_notion.models import CaptureInput, WritePlan
from capture_to_notion.notion_adapter import (
    NotionAdapter,
    NotionApiError,
    NotionAuthError,
    NotionNotFoundError,
    NotionPermissionError,
    NotionRateLimitError,
)
from capture_to_notion.planner import build_capture_plan, build_plan_cli_summary
from capture_to_notion.preflight import build_capture_preflight, build_capture_preflight_summary
from capture_to_notion.scanner import scan_data_source_graph, scan_page_graph
from capture_to_notion.verifier import url_is_accessible, verify_capture_page, verify_plain_page
from capture_to_notion.workflow_guard import assert_plan_workflow_allows_apply, assert_preflight_allows_plan, scoped_sync_request
from capture_to_notion.writer import NotionWriterError, apply_write_plan


def load_capture_input(path: str) -> CaptureInput:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliInputError(f"输入文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliInputError(f"输入文件 JSON 无效: {path} ({exc.msg})") from exc

    try:
        return CaptureInput.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise CliInputError(f"输入内容无效: {path} ({exc})") from exc


def load_write_plan(path: str) -> WritePlan:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliInputError(f"计划文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliInputError(f"计划文件 JSON 无效: {path} ({exc.msg})") from exc

    try:
        return WritePlan.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise CliInputError(f"计划内容无效: {path} ({exc})") from exc


def print_json(data: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 100


def cmd_version(args: argparse.Namespace) -> int:
    print_json(version_info(config_root()))
    return 0



def cmd_doctor(args: argparse.Namespace) -> int:
    print_json(doctor_report(config_root()))
    return 0



def cmd_config_migrate(args: argparse.Namespace) -> int:
    result = migrate_legacy_config(config_root(), confirmed=args.confirmed)
    print_json(result)
    return 1 if args.confirmed and result.get("errors") else 0


def cmd_cache_inspect(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    graphs = sorted(path.stem for path in config.graphs_v2_dir.glob("*.json"))
    profiles = sorted(path.stem for path in config.profiles_v2_dir.glob("*.json"))
    print_json({"config_root": str(config.root), "cache_version": 2, "aliases": cache.aliases(), "graphs": graphs, "profiles": profiles})
    return 0



def _remove_path(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True



def cmd_cache_reset_v2(args: argparse.Namespace) -> int:
    config = ensure_config()
    if not args.confirmed:
        raise CliInputError("cache reset-v2 需要显式确认，请传入 --confirmed")
    deleted: list[str] = []
    if args.delete_legacy:
        for path in (config.aliases_file, config.routes_file, config.targets_dir, config.plans_dir):
            if path == config.plans_v2_dir:
                continue
            if _remove_path(path):
                deleted.append(str(path))
    for path in (config.cache_v2_dir, config.graphs_v2_dir, config.profiles_v2_dir, config.plans_v2_dir, config.assets_v2_dir):
        path.mkdir(parents=True, exist_ok=True)
    config.aliases_v2_file.write_text(
        json.dumps({"cache_version": 2, "aliases": {}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print_json(
        {
            "cache_version": 2,
            "cache_v2_dir": str(config.cache_v2_dir),
            "deleted_legacy": bool(args.delete_legacy),
            "deleted_paths": deleted,
        }
    )
    return 0


def _v2_root_target(graph: dict[str, Any]) -> dict[str, Any]:
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    target: dict[str, Any] = {}
    if root.get("kind") == "page" and isinstance(root.get("id"), str):
        page_id = root["id"]
        target["page_id"] = page_id
        pages = graph.get("pages") if isinstance(graph.get("pages"), dict) else {}
        page = pages.get(page_id) if isinstance(pages, dict) else None
        if isinstance(page, dict) and isinstance(page.get("title"), str):
            target["title"] = page["title"]
    elif root.get("kind") == "data_source" and isinstance(root.get("id"), str):
        target["data_source_id"] = root["id"]
        data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
        data_source = data_sources.get(root["id"]) if isinstance(data_sources, dict) else None
        if isinstance(data_source, dict) and isinstance(data_source.get("title"), str):
            target["title"] = data_source["title"]
    return target


def _v2_content_types(profile: dict[str, Any] | None) -> list[str]:
    write_profiles = profile.get("write_profiles") if isinstance(profile, dict) else None
    if not isinstance(write_profiles, dict):
        return []
    return sorted(key for key in write_profiles if isinstance(key, str))


def _v2_profile_summary(profile: dict[str, Any] | None) -> dict[str, Any]:
    write_profiles = profile.get("write_profiles") if isinstance(profile, dict) else None
    if not isinstance(write_profiles, dict):
        return {}
    summary: dict[str, Any] = {}
    for content_type, write_profile in write_profiles.items():
        if not isinstance(content_type, str) or not isinstance(write_profile, dict):
            continue
        summary[content_type] = {
            key: write_profile.get(key)
            for key in ("canonical_data_source_id", "canonical_view_id", "field_mapping", "field_sources")
            if key in write_profile
        }
    return summary


def _v2_data_source_summaries(graph: dict[str, Any], *, compact: bool) -> list[dict[str, Any]]:
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    summaries: list[dict[str, Any]] = []
    for key, data_source in sorted(data_sources.items()):
        if not isinstance(data_source, dict):
            continue
        schema = data_source.get("schema") if isinstance(data_source.get("schema"), dict) else {}
        summary = {
            "key": key,
            "data_source_id": data_source.get("data_source_id"),
            "database_id": data_source.get("database_id"),
            "title": data_source.get("title"),
            "schema_hash": data_source.get("schema_hash"),
        }
        if compact:
            summary["field_count"] = len(schema)
        else:
            summary["schema_fields"] = sorted(name for name in schema if isinstance(name, str))
        summaries.append({k: v for k, v in summary.items() if v is not None})
    return summaries


def _v2_graph_detail(cache: CacheV2Store, *, alias_name: str | None, graph_id: str | None, compact: bool) -> dict[str, Any] | None:
    alias = cache.find_alias(alias_name) if alias_name else None
    resolved_graph_id = graph_id or (alias.get("graph_id") if isinstance(alias, dict) else None)
    if not isinstance(resolved_graph_id, str) or not resolved_graph_id:
        return None
    graph = cache.read_graph(resolved_graph_id)
    if not isinstance(graph, dict):
        return None
    aliases = cache.aliases()
    resolved_alias_name = alias_name
    if resolved_alias_name is None:
        for candidate_alias, candidate in aliases.items():
            if isinstance(candidate, dict) and candidate.get("graph_id") == resolved_graph_id:
                resolved_alias_name = candidate_alias
                alias = candidate
                break
    profile_id = alias.get("profile_id") if isinstance(alias, dict) else None
    profile = cache.read_profile(profile_id) if isinstance(profile_id, str) else None
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    detail = {
        "alias": resolved_alias_name,
        "graph_id": resolved_graph_id,
        "profile_id": profile_id,
        "kind": alias.get("kind") if isinstance(alias, dict) else None,
        "root": root,
        "target": _v2_root_target(graph),
        "data_sources": _v2_data_source_summaries(graph, compact=compact),
        "status": "cached",
    }
    if compact:
        detail["content_types"] = _v2_content_types(profile)
    else:
        detail["graph_file"] = str(cache.graph_path(resolved_graph_id))
        if isinstance(profile_id, str):
            detail["profile_file"] = str(cache.profile_path(profile_id))
        detail["write_profiles"] = _v2_profile_summary(profile)
    return {key: value for key, value in detail.items() if value is not None}


def _v2_target_summary(cache: CacheV2Store, alias_name: str, alias: dict[str, Any]) -> dict[str, Any]:
    graph_id = alias.get("graph_id")
    profile_id = alias.get("profile_id")
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    profile = cache.read_profile(profile_id) if isinstance(profile_id, str) else None
    if not isinstance(graph, dict):
        return {
            "alias": alias_name,
            "graph_id": graph_id,
            "profile_id": profile_id,
            "kind": alias.get("kind"),
            "title": None,
            "data_sources": [],
            "views": [],
            "content_types": [],
            "status": "missing_cache",
        }
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    target = _v2_root_target(graph)
    return {
        "alias": alias_name,
        "graph_id": graph_id,
        "profile_id": profile_id,
        "kind": alias.get("kind"),
        "root_kind": root.get("kind"),
        "root_id": root.get("id"),
        "title": target.get("title"),
        "data_sources": _graph_data_source_titles(graph),
        "views": _graph_view_names(graph),
        "content_types": _v2_content_types(profile),
        "status": "cached",
    }


def cmd_target_suggest(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    capture = load_capture_input(args.input)
    content_type = classify_content_type(capture)
    suggestions = []
    for alias_name, alias in sorted(cache.aliases().items()):
        if not isinstance(alias, dict):
            continue
        profile_id = alias.get("profile_id")
        profile = cache.read_profile(profile_id) if isinstance(profile_id, str) else None
        if content_type in _v2_content_types(profile):
            suggestions.append({"alias": alias_name, "graph_id": alias.get("graph_id"), "profile_id": profile_id, "confidence": "high"})
    print_json({"content_type": content_type, "suggestions": suggestions, "requires_confirmation": len(suggestions) == 0})
    return 0


def cmd_target_list(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    targets = [
        _v2_target_summary(cache, alias_name, alias)
        for alias_name, alias in sorted(cache.aliases().items())
        if isinstance(alias, dict)
    ]
    print_json({"count": len(targets), "targets": targets})
    return 0


def cmd_target_inspect(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    detail = _v2_graph_detail(cache, alias_name=args.alias, graph_id=args.target_id, compact=args.compact)
    if detail is None:
        if args.alias:
            alias = cache.find_alias(args.alias)
            if alias is None:
                raise CliInputError(f"未找到 v2 target alias: {args.alias}")
            raise CliInputError(f"未找到 v2 graph cache: {alias.get('graph_id')}")
        if args.target_id:
            raise CliInputError(f"未找到 v2 graph cache: {args.target_id}")
        raise CliInputError("未找到 graph_id")
    print_json(detail)
    return 0


def duplicate_title_groups(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        title = result.get("title")
        if title:
            grouped.setdefault(title, []).append(result)
    return [
        {"title": title, "results": title_results}
        for title, title_results in grouped.items()
        if len(title_results) > 1
    ]


def _compact_target_search_result(result: dict[str, Any], include_parent_path: bool = False) -> dict[str, Any]:
    keys = ["id", "object", "title", "last_edited_time"]
    if include_parent_path:
        keys.append("parent_path")
    return {key: result.get(key) for key in keys if result.get(key) is not None}


def _target_search_limit(value: int) -> int:
    if value < 1 or value > MAX_SEARCH_LIMIT:
        raise CliInputError(f"--limit 必须在 1 到 {MAX_SEARCH_LIMIT} 之间")
    return value


def cmd_target_search(args: argparse.Namespace) -> int:
    limit = _target_search_limit(args.limit)
    config = ensure_config()
    adapter = NotionAdapter.from_config(config)
    include_parent_path = args.include_parent_path or not args.compact
    raw_results = adapter.search(
        args.query,
        limit=min(limit + 1, MAX_SEARCH_LIMIT),
        include_parent_path=include_parent_path,
    )
    truncated = len(raw_results) > limit
    results = raw_results[:limit]
    if args.compact:
        results = [
            _compact_target_search_result(result, include_parent_path=args.include_parent_path)
            for result in results
        ]
    output: dict[str, Any] = {
        "query": args.query,
        "result_count": len(results),
        "truncated": truncated,
        "results": results,
        "requires_confirmation": True,
        "next_action": "choose_exact_target_or_scan",
    }
    duplicates = duplicate_title_groups(results)
    if duplicates:
        output["confirmation_reason"] = "duplicate_target_names"
        output["duplicate_titles"] = duplicates
    print_json(output)
    return 0


def _load_database_schema(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliInputError(f"schema 文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliInputError(f"schema 文件 JSON 无效: {path} ({exc.msg})") from exc
    properties = data.get("properties") if isinstance(data, dict) else None
    if not isinstance(properties, dict):
        raise CliInputError("schema 文件必须包含 properties 对象")
    if not any(isinstance(value, dict) and value.get("type") == "title" for value in properties.values()):
        raise CliInputError("schema properties 必须包含一个 title 字段")
    return properties


def _parse_profile_fields(values: list[str] | None) -> tuple[dict[str, str], dict[str, str]]:
    field_mapping: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise CliInputError("--field 必须使用 semantic=NotionProperty 格式")
        semantic, property_name = value.split("=", 1)
        semantic = semantic.strip()
        property_name = property_name.strip()
        if not semantic or not property_name:
            raise CliInputError("--field 必须使用 semantic=NotionProperty 格式")
        field_mapping[semantic] = property_name
    return field_mapping, {key: "user_binding" for key in field_mapping}



def cmd_target_bind_profile(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    graph = cache.read_graph(args.graph_id)
    if graph is None:
        raise CliInputError(f"未找到 v2 graph cache: {args.graph_id}")
    field_mapping, field_sources = _parse_profile_fields(args.field)
    try:
        profile = bind_write_profile(
            graph,
            profile_id=args.profile_id,
            content_type=args.content_type,
            data_source_id=args.data_source_id,
            view_id=args.view_id,
            field_mapping=field_mapping,
            field_sources=field_sources,
            aliases=[args.alias],
        )
    except ValueError as exc:
        raise CliInputError(str(exc)) from exc
    cache.write_profile(args.profile_id, profile)
    cache.bind_alias(args.alias, graph_id=args.graph_id, profile_id=args.profile_id, kind="write_profile")
    print_json(
        {
            "alias": args.alias,
            "graph_id": args.graph_id,
            "profile_id": args.profile_id,
            "content_type": args.content_type,
            "data_source_id": args.data_source_id,
            "view_id": args.view_id,
        }
    )
    return 0



def _default_graph_id(source_id: str) -> str:
    return source_id.replace("-", "")


def _graph_data_source_titles(graph: dict[str, Any]) -> list[str]:
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    return [
        data_source.get("title")
        for data_source in data_sources.values()
        if isinstance(data_source, dict) and data_source.get("title")
    ]


def _graph_view_names(graph: dict[str, Any]) -> list[str]:
    views = graph.get("views") if isinstance(graph.get("views"), dict) else {}
    return [view.get("name") for view in views.values() if isinstance(view, dict) and view.get("name")]


def _target_scan_output(config: Any, graph: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    graph_id = graph["graph_id"]
    output = {
        "cache_version": 2,
        "graph_id": graph_id,
        "data_sources": _v2_data_source_summaries(graph, compact=compact),
        "views": _graph_view_names(graph),
        "requires_profile_binding": True,
        "next_action": "target bind-profile",
    }
    if not compact:
        output["graph_file"] = str(config.graphs_v2_dir / f"{graph_id}.json")
    return output


def cmd_target_scan(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    adapter = NotionAdapter.from_config(config)
    if args.data_source_id:
        graph_id = args.target_id or _default_graph_id(args.data_source_id)
        graph = scan_data_source_graph(adapter, args.data_source_id, cache, graph_id=graph_id)
    else:
        graph_id = args.target_id or _default_graph_id(args.page_id)
        graph = scan_page_graph(adapter, args.page_id, cache, graph_id=graph_id)
    if args.alias:
        cache.bind_alias(args.alias, graph_id=graph_id, profile_id=None, kind="graph")
    print_json(_target_scan_output(config, graph, compact=args.compact))
    return 0


def cmd_target_create_database(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    adapter = NotionAdapter.from_config(config)
    properties = _load_database_schema(args.schema)
    database = adapter.create_database(args.page_id, args.title, properties)
    graph_id = args.target_id or _default_graph_id(args.page_id)
    graph = scan_page_graph(adapter, args.page_id, cache, graph_id=graph_id)
    if args.alias:
        cache.bind_alias(args.alias, graph_id=graph_id, profile_id=None, kind="graph")
    output = _target_scan_output(config, graph)
    output["created_database_id"] = database.get("id")
    output["created_database_title"] = args.title
    print_json(output)
    return 0


def cmd_capture_preflight(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    capture = load_capture_input(args.input)
    preflight = build_capture_preflight(capture, cache)
    print_json(build_capture_preflight_summary(preflight) if args.compact else preflight)
    return 0



def _run_v2_scoped_sync_for_plan(sync: dict[str, Any], cache: CacheV2Store, adapter: Any) -> None:
    target_id = sync.get("target_id") if isinstance(sync.get("target_id"), str) else None
    data_source_id = sync.get("data_source_id")
    if isinstance(data_source_id, str) and data_source_id:
        scan_data_source_graph(adapter, data_source_id, cache, graph_id=target_id or _default_graph_id(data_source_id))
        return
    page_id = sync.get("page_id")
    if isinstance(page_id, str) and page_id:
        scan_page_graph(adapter, page_id, cache, graph_id=target_id or _default_graph_id(page_id))
        return
    raise CliInputError("next_action=sync_target_cache requires explicit data_source_id or page_id")


def cmd_capture_plan(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    capture = load_capture_input(args.input)
    preflight = build_capture_preflight(capture, cache)
    try:
        sync = scoped_sync_request(preflight)
    except ValueError as exc:
        raise CliInputError(str(exc)) from exc
    if sync is not None:
        adapter = NotionAdapter.from_config(config)
        _run_v2_scoped_sync_for_plan(sync, cache, adapter)
        preflight = build_capture_preflight(capture, cache)
    try:
        assert_preflight_allows_plan(preflight)
    except ValueError as exc:
        raise CliInputError(str(exc)) from exc
    plan = build_capture_plan(capture, cache)
    plan.preflight_workflow = preflight.get("workflow") if isinstance(preflight.get("workflow"), dict) else None
    if args.output:
        Path(args.output).write_text(plan.to_json(), encoding="utf-8")
    if args.compact:
        print_json(build_plan_cli_summary(plan))
    else:
        sys.stdout.write(plan.to_json())
    return 0



def cmd_capture_verify(args: argparse.Namespace) -> int:
    config = ensure_config()
    adapter = NotionAdapter.from_config(config)
    print_json(verify_capture_page(args.page_id, adapter, url_checker=url_is_accessible))
    return 0


def _inaccessible_verification_page(page_id: str) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "verified": False,
        "checks": {"page": {"status": "inaccessible"}},
        "warnings": ["inaccessible:page"],
    }


def _is_empty_verification_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _schema_for_data_source(
    data_source_id: str | None,
    target_structure: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    for data_source in target_structure.get("data_sources", {}).values():
        if data_source.get("data_source_id") == data_source_id and isinstance(data_source.get("schema"), dict):
            return data_source["schema"]
    return {}


def _schema_for_plan(plan: WritePlan, target_structure: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _schema_for_data_source(plan.target.data_source_id, target_structure)


def _verification_checks_for_record(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for record_key, property_name in field_mapping.items():
        if _is_empty_verification_value(record.get(record_key)):
            continue
        property_schema = schema.get(property_name)
        if not isinstance(property_schema, dict):
            continue
        property_type = property_schema.get("type")
        if not isinstance(property_type, str):
            continue
        checks[record_key] = {"property_type": property_type}
        if property_type == "files":
            checks[record_key]["check_urls"] = True
    return checks


def _verification_checks_for_plan(plan: WritePlan, schema: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return _verification_checks_for_record(plan.normalized_record, plan.field_mapping, schema)


def _append_verification_page(
    pages: list[dict[str, Any]],
    *,
    page_id: str,
    adapter: Any,
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
    checks: dict[str, dict[str, Any]],
) -> None:
    try:
        pages.append(
            verify_capture_page(
                page_id,
                adapter,
                url_checker=url_is_accessible,
                field_mapping=field_mapping,
                schema=schema,
                checks=checks,
                include_page_cover=False,
            )
        )
    except (NotionApiError, NotionAuthError, NotionNotFoundError, NotionPermissionError, NotionRateLimitError):
        pages.append(_inaccessible_verification_page(page_id))


def _plain_operation_for_result(plan: WritePlan, operation_result: dict[str, Any]) -> dict[str, Any] | None:
    page_id = operation_result.get("page_id")
    for operation in plan.operations:
        if operation.get("type") != "create_child_page":
            continue
        operation_page_id = operation.get("page_id")
        if isinstance(operation_page_id, str) and isinstance(page_id, str) and operation_page_id != page_id:
            continue
        return operation
    return None


def _expected_plain_page_title(plan: WritePlan, operation_result: dict[str, Any], operation: dict[str, Any] | None) -> str | None:
    for candidate in (operation_result.get("title"), plan.summary.get("title"), operation.get("title") if operation else None):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _expected_plain_page_block_count(plan: WritePlan, operation: dict[str, Any] | None) -> int | None:
    summary_count = plan.summary.get("body_block_count")
    if isinstance(summary_count, int):
        return summary_count
    body_blocks = operation.get("body_blocks") if operation else None
    if isinstance(body_blocks, list):
        return len(body_blocks)
    return None


def _collect_plain_text_samples(value: Any, samples: list[str], *, limit: int = 3) -> None:
    if len(samples) >= limit:
        return
    if isinstance(value, dict):
        plain_text = value.get("plain_text")
        if isinstance(plain_text, str) and plain_text.strip():
            samples.append(plain_text)
            return
        for child in value.values():
            _collect_plain_text_samples(child, samples, limit=limit)
            if len(samples) >= limit:
                return
    elif isinstance(value, list):
        for item in value:
            _collect_plain_text_samples(item, samples, limit=limit)
            if len(samples) >= limit:
                return


def _expected_plain_page_text_samples(operation: dict[str, Any] | None) -> list[str] | None:
    body_blocks = operation.get("body_blocks") if operation else None
    if not isinstance(body_blocks, list):
        return None
    samples: list[str] = []
    _collect_plain_text_samples(body_blocks, samples)
    return samples or None


def _append_plain_verification_page(
    pages: list[dict[str, Any]],
    *,
    page_id: str,
    adapter: Any,
    plan: WritePlan,
    operation_result: dict[str, Any],
) -> None:
    operation = _plain_operation_for_result(plan, operation_result)
    try:
        pages.append(
            verify_plain_page(
                page_id,
                adapter,
                expected_title=_expected_plain_page_title(plan, operation_result, operation),
                expected_block_count=_expected_plain_page_block_count(plan, operation),
                expected_text_samples=_expected_plain_page_text_samples(operation),
            )
        )
    except (NotionApiError, NotionAuthError, NotionNotFoundError, NotionPermissionError, NotionRateLimitError):
        pages.append(_inaccessible_verification_page(page_id))


def _completion_operation_for_result(
    plan: WritePlan,
    operation_result: dict[str, Any],
) -> dict[str, Any] | None:
    operation_type = operation_result.get("type")
    source_record_key = operation_result.get("source_record_key")
    for operation in plan.completion_operations:
        if (
            operation.get("type") == operation_type
            and operation.get("source_record_key") == source_record_key
        ):
            return operation
    return None


def _completion_verification_schema(
    operation: dict[str, Any],
    target_structure: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    schema = operation.get("schema")
    if isinstance(schema, dict):
        return schema
    target_data_source_id = operation.get("target_data_source_id")
    return _schema_for_data_source(
        target_data_source_id if isinstance(target_data_source_id, str) else None,
        target_structure,
    )


def _apply_verification_summary(
    result: dict[str, Any],
    adapter: Any,
    plan: WritePlan,
    target_structure: dict[str, Any],
) -> dict[str, Any] | None:
    pages: list[dict[str, Any]] = []
    schema = _schema_for_plan(plan, target_structure)
    checks = _verification_checks_for_plan(plan, schema)
    for operation_result in result.get("results", []):
        if not isinstance(operation_result, dict):
            continue
        page_id = operation_result.get("page_id")
        if isinstance(page_id, str) and page_id:
            if operation_result.get("type") == "create_child_page":
                _append_plain_verification_page(
                    pages,
                    page_id=page_id,
                    adapter=adapter,
                    plan=plan,
                    operation_result=operation_result,
                )
                continue
            _append_verification_page(
                pages,
                page_id=page_id,
                adapter=adapter,
                field_mapping=plan.field_mapping,
                schema=schema,
                checks=checks,
            )

    for operation_result in result.get("completion_results", []):
        if not isinstance(operation_result, dict):
            continue
        page_id = operation_result.get("page_id")
        operation = _completion_operation_for_result(plan, operation_result)
        if not isinstance(page_id, str) or not page_id or operation is None:
            continue
        field_mapping = operation.get("field_mapping", {})
        record = operation.get("record", {})
        if not isinstance(field_mapping, dict) or not isinstance(record, dict):
            continue
        completion_schema = _completion_verification_schema(operation, target_structure)
        _append_verification_page(
            pages,
            page_id=page_id,
            adapter=adapter,
            field_mapping=field_mapping,
            schema=completion_schema,
            checks=_verification_checks_for_record(record, field_mapping, completion_schema),
        )

    if not pages:
        return None
    warnings = [warning for page in pages for warning in page.get("warnings", []) if isinstance(warning, str)]
    return {
        "verified": all(bool(page.get("verified")) for page in pages),
        "pages": pages,
        "warnings": warnings,
    }


def _notion_error_body_text(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return json.dumps(body, ensure_ascii=False).lower()
    if isinstance(body, str):
        return body.lower()
    return ""


def _is_stale_cache_error(exc: Exception) -> bool:
    if not isinstance(exc, (NotionApiError, NotionNotFoundError)):
        return False
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    if code == "object_not_found" or status == 404:
        return True
    if code != "validation_error":
        return False
    body_text = _notion_error_body_text(exc)
    return "data_source" in body_text or "schema" in body_text


def _can_recover_stale_cache(plan: WritePlan) -> bool:
    return all(not operation.get("page_id") for operation in plan.operations)


def _is_uncertain_create_error(plan: WritePlan, exc: Exception) -> bool:
    if not isinstance(exc, NotionApiError):
        return False
    if not all(not operation.get("page_id") for operation in plan.operations):
        return False
    status = getattr(exc, "status", None)
    return status is None or (isinstance(status, int) and status >= 500)



def _apply_plan_with_verification(
    plan: WritePlan,
    target_structure: dict[str, Any],
    adapter: Any,
) -> dict[str, Any]:
    result = apply_write_plan(plan, target_structure, adapter)
    verification = _apply_verification_summary(result, adapter, plan, target_structure)
    if verification is not None:
        result["verification"] = verification
    return result




def _target_page_id(target_structure: dict[str, Any]) -> str | None:
    target = target_structure.get("target")
    if not isinstance(target, dict):
        return None
    page_id = target.get("page_id")
    return page_id if isinstance(page_id, str) and page_id else None



def _target_id(target_structure: dict[str, Any]) -> str | None:
    target = target_structure.get("target")
    if not isinstance(target, dict):
        return None
    target_id = target.get("target_id")
    return target_id if isinstance(target_id, str) and target_id else None



def _effective_operations(plan: WritePlan) -> list[dict[str, Any]]:
    return plan.operations or plan.planned_operations



def _capture_dict_needs_context_verification(capture_data: dict[str, Any] | None) -> bool:
    if not isinstance(capture_data, dict):
        return False
    if capture_data.get("target_context_hint"):
        return True
    scope_hint = capture_data.get("target_scope_hint")
    if not isinstance(scope_hint, str):
        return False
    lowered = scope_hint.casefold()
    return any(token in lowered for token in ("page", "parent", "child", "under", "context"))



def _workflow_needs_context_verification(workflow: dict[str, Any] | None) -> bool:
    if not isinstance(workflow, dict):
        return False
    target_resolution = workflow.get("target_resolution")
    if not isinstance(target_resolution, dict):
        return False
    if target_resolution.get("target_context_hint"):
        return True
    scope_hint = target_resolution.get("target_scope_hint")
    if not isinstance(scope_hint, str):
        return False
    lowered = scope_hint.casefold()
    return any(token in lowered for token in ("page", "parent", "child", "under", "context"))



def _plan_needs_context_verification(plan: WritePlan) -> bool:
    return _capture_dict_needs_context_verification(plan.capture_input) or _workflow_needs_context_verification(plan.preflight_workflow)



def _assert_matching_value(label: str, plan_value: str | None, refreshed_value: Any) -> None:
    if not plan_value:
        return
    if not isinstance(refreshed_value, str) or not refreshed_value:
        raise CliInputError(f"plan_integrity_failed:{label}_missing_after_refresh")
    if refreshed_value != plan_value:
        raise CliInputError(f"plan_integrity_failed:{label}_mismatch")



def _validate_context_integrity(plan: WritePlan, cache: CacheV2Store, target_structure: dict[str, Any]) -> None:
    if not _plan_needs_context_verification(plan):
        return
    if not plan.capture_input:
        raise CliInputError("plan_integrity_failed:context_capture_input_missing")
    write_targets = plan.summary.get("write_targets") if isinstance(plan.summary, dict) else None
    if not isinstance(write_targets, list) or not write_targets:
        raise CliInputError("plan_integrity_failed:context_write_targets_missing")
    capture = CaptureInput.from_dict(plan.capture_input)
    refreshed_preflight = build_capture_preflight(capture, cache)
    try:
        assert_preflight_allows_plan(refreshed_preflight)
    except ValueError as exc:
        raise CliInputError(str(exc)) from exc
    workflow = refreshed_preflight.get("workflow")
    target_resolution = workflow.get("target_resolution") if isinstance(workflow, dict) else None
    if not isinstance(target_resolution, dict) or target_resolution.get("target_context_verified") is not True:
        raise CliInputError("plan_integrity_failed:target_context_not_verified")
    _assert_matching_value("data_source_id", plan.target.data_source_id, target_resolution.get("data_source_id"))
    _assert_matching_value("page_id", plan.target.page_id, target_resolution.get("page_id"))
    _assert_matching_value("target_id", _target_id(target_structure), target_resolution.get("target_id"))



def _v2_target_page_id(graph: dict[str, Any]) -> str | None:
    root = graph.get("root")
    if isinstance(root, dict) and root.get("kind") == "page" and isinstance(root.get("id"), str):
        return root["id"]
    return None



def _v2_target_title(graph: dict[str, Any], page_id: str | None) -> str | None:
    pages = graph.get("pages")
    page = pages.get(page_id) if isinstance(pages, dict) and page_id else None
    if not isinstance(page, dict):
        return None
    title = page.get("title")
    return title if isinstance(title, str) and title else None



def _target_structure_from_v2_graph(graph: dict[str, Any], graph_id: str) -> dict[str, Any]:
    page_id = _v2_target_page_id(graph)
    target = {"target_id": graph_id}
    if page_id:
        target["page_id"] = page_id
    title = _v2_target_title(graph, page_id)
    if title:
        target["title"] = title
    data_sources = graph.get("data_sources")
    return {
        "target": target,
        "data_sources": data_sources if isinstance(data_sources, dict) else {},
    }



def _validate_v2_view_integrity(plan: WritePlan, graph: dict[str, Any]) -> None:
    view_id = plan.target.view_id
    if not view_id:
        return
    views = graph.get("views")
    view = views.get(view_id) if isinstance(views, dict) else None
    if not isinstance(view, dict):
        raise CliInputError("plan_integrity_failed:view_missing_in_cache")
    if view.get("data_source_id") != plan.target.data_source_id:
        raise CliInputError("plan_integrity_failed:view_data_source_id_mismatch")



def _validate_v2_page_parent_plan_integrity(plan: WritePlan, cache: CacheV2Store) -> dict[str, Any]:
    graph_id = plan.target.target_id
    if not graph_id:
        raise CliInputError("plan_integrity_failed:target_graph_id_missing")
    graph = cache.read_graph(graph_id)
    if graph is None:
        raise CliInputError(f"未找到 graph_id={graph_id} 的 v2 graph cache，请先运行 target scan")
    cached_page_id = _v2_target_page_id(graph)
    if not cached_page_id:
        raise CliInputError("plan_integrity_failed:target_page_not_in_cache")
    target_page_id = plan.target.parent_page_id or plan.target.page_id
    if not target_page_id:
        raise CliInputError("plan_integrity_failed:target_page_id_missing")
    if target_page_id != cached_page_id:
        raise CliInputError("plan_integrity_failed:target_page_id_mismatch")
    data_sources = graph.get("data_sources")
    if isinstance(data_sources, dict) and data_sources:
        raise CliInputError("plan_integrity_failed:page_parent_graph_has_data_sources")
    target_structure = _target_structure_from_v2_graph(graph, graph_id)
    operations = _effective_operations(plan)
    if not operations:
        raise CliInputError("计划没有可执行操作，请重新运行 capture plan 生成可执行计划")
    for operation in operations:
        if operation.get("type") != "create_child_page":
            continue
        if operation.get("parent_page_id") != target_page_id:
            raise CliInputError("plan_integrity_failed:operation_parent_page_id_mismatch")
    _validate_context_integrity(plan, cache, target_structure)
    return target_structure



def _validate_v2_plan_integrity(plan: WritePlan, cache: CacheV2Store) -> dict[str, Any]:
    if plan.target.target_kind == "page_parent":
        return _validate_v2_page_parent_plan_integrity(plan, cache)
    data_source_id = plan.target.data_source_id
    if not data_source_id:
        raise CliInputError("plan_integrity_failed:target_data_source_id_missing")
    graph_id = plan.target.target_id
    if not graph_id:
        raise CliInputError("plan_integrity_failed:target_graph_id_missing")
    graph = cache.read_graph(graph_id)
    if graph is None:
        raise CliInputError(f"未找到 graph_id={graph_id} / data_source_id={data_source_id} 的 v2 graph cache，请先运行 target scan")
    data_sources = graph.get("data_sources")
    if not isinstance(data_sources, dict) or data_source_id not in data_sources:
        raise CliInputError("plan_integrity_failed:target_data_source_not_in_cache")
    target_structure = _target_structure_from_v2_graph(graph, graph_id)
    cached_page_id = _target_page_id(target_structure)
    if plan.target.page_id and cached_page_id and plan.target.page_id != cached_page_id:
        raise CliInputError("plan_integrity_failed:target_page_id_mismatch")
    _validate_v2_view_integrity(plan, graph)
    operations = _effective_operations(plan)
    if not operations:
        raise CliInputError("计划没有可执行操作，请重新运行 capture plan 生成可执行计划")
    for operation in operations:
        if operation.get("type") != "create_or_update_page":
            continue
        if operation.get("data_source_id") != data_source_id:
            raise CliInputError("plan_integrity_failed:operation_data_source_id_mismatch")
    _validate_context_integrity(plan, cache, target_structure)
    return target_structure



def _validate_plan_integrity(plan: WritePlan, cache: CacheV2Store) -> dict[str, Any]:
    return _validate_v2_plan_integrity(plan, cache)



def cmd_capture_apply(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheV2Store(config)
    plan = load_write_plan(args.plan)

    if not args.confirmed:
        if plan.requires_confirmation:
            reason = plan.confirmation_reason or "未说明原因"
            raise CliInputError(f"计划需要确认后才能执行: {reason}. 如已确认，请传入 --confirmed")
        raise CliInputError("执行写入必须显式确认，请传入 --confirmed")

    try:
        assert_plan_workflow_allows_apply(plan.preflight_workflow)
    except ValueError as exc:
        raise CliInputError(str(exc)) from exc

    target_structure = _validate_plan_integrity(plan, cache)

    if args.confirmed:
        if not plan.operations and plan.planned_operations:
            plan.operations = plan.planned_operations
        if not plan.asset_operations and plan.planned_asset_operations:
            plan.asset_operations = plan.planned_asset_operations
        if not plan.completion_operations and plan.planned_completion_operations:
            plan.completion_operations = plan.planned_completion_operations

    adapter = NotionAdapter.from_config(config)
    try:
        result = _apply_plan_with_verification(plan, target_structure, adapter)
    except (NotionApiError, NotionNotFoundError) as exc:
        if _is_uncertain_create_error(plan, exc):
            raise CliInputError(
                f"possible_partial_write: create 请求返回不确定错误，已停止自动重试；请检查 Notion 后重新生成 update 计划再继续 ({exc})"
            ) from exc
        if not _is_stale_cache_error(exc) or not _can_recover_stale_cache(plan):
            raise
        raise CliInputError("v2_stale_cache_recovery_requires_fresh_plan") from exc
    print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capture-to-notion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version")
    version_parser.set_defaults(func=cmd_version)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.set_defaults(func=cmd_doctor)

    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_migrate = config_subparsers.add_parser("migrate")
    config_migrate.add_argument("--confirmed", action="store_true")
    config_migrate.set_defaults(func=cmd_config_migrate)

    cache_parser = subparsers.add_parser("cache")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
    cache_inspect = cache_subparsers.add_parser("inspect")
    cache_inspect.set_defaults(func=cmd_cache_inspect)
    cache_reset_v2 = cache_subparsers.add_parser("reset-v2")
    cache_reset_v2.add_argument("--delete-legacy", action="store_true")
    cache_reset_v2.add_argument("--confirmed", action="store_true")
    cache_reset_v2.set_defaults(func=cmd_cache_reset_v2)

    target_parser = subparsers.add_parser("target")
    target_subparsers = target_parser.add_subparsers(dest="target_command", required=True)
    target_suggest = target_subparsers.add_parser("suggest")
    target_suggest.add_argument("--input", required=True)
    target_suggest.set_defaults(func=cmd_target_suggest)
    target_list = target_subparsers.add_parser("list")
    target_list.set_defaults(func=cmd_target_list)
    target_inspect = target_subparsers.add_parser("inspect")
    target_inspect_group = target_inspect.add_mutually_exclusive_group(required=True)
    target_inspect_group.add_argument("--alias")
    target_inspect_group.add_argument("--target-id")
    target_inspect.add_argument("--compact", action="store_true")
    target_inspect.set_defaults(func=cmd_target_inspect)
    target_search = target_subparsers.add_parser("search")
    target_search.add_argument("--query", required=True)
    target_search.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    target_search.add_argument("--compact", action="store_true")
    target_search.add_argument("--include-parent-path", action="store_true")
    target_search.set_defaults(func=cmd_target_search)
    target_scan = target_subparsers.add_parser("scan")
    target_scan_group = target_scan.add_mutually_exclusive_group(required=True)
    target_scan_group.add_argument("--page-id")
    target_scan_group.add_argument("--data-source-id")
    target_scan.add_argument("--alias")
    target_scan.add_argument("--target-id")
    target_scan.add_argument("--compact", action="store_true")
    target_scan.set_defaults(func=cmd_target_scan)
    target_create_database = target_subparsers.add_parser("create-database")
    target_create_database.add_argument("--page-id", required=True)
    target_create_database.add_argument("--title", required=True)
    target_create_database.add_argument("--schema", required=True)
    target_create_database.add_argument("--alias")
    target_create_database.add_argument("--target-id")
    target_create_database.set_defaults(func=cmd_target_create_database)
    target_bind_profile = target_subparsers.add_parser("bind-profile")
    target_bind_profile.add_argument("--alias", required=True)
    target_bind_profile.add_argument("--graph-id", required=True)
    target_bind_profile.add_argument("--profile-id", required=True)
    target_bind_profile.add_argument("--content-type", required=True)
    target_bind_profile.add_argument("--data-source-id", required=True)
    target_bind_profile.add_argument("--view-id")
    target_bind_profile.add_argument("--field", action="append")
    target_bind_profile.set_defaults(func=cmd_target_bind_profile)

    capture_parser = subparsers.add_parser("capture")
    capture_subparsers = capture_parser.add_subparsers(dest="capture_command", required=True)
    capture_preflight = capture_subparsers.add_parser("preflight")
    capture_preflight.add_argument("--input", required=True)
    capture_preflight.add_argument("--compact", action="store_true")
    capture_preflight.set_defaults(func=cmd_capture_preflight)
    capture_plan = capture_subparsers.add_parser("plan")
    capture_plan.add_argument("--input", required=True)
    capture_plan.add_argument("--output")
    capture_plan.add_argument("--compact", action="store_true")
    capture_plan.set_defaults(func=cmd_capture_plan)
    capture_apply = capture_subparsers.add_parser("apply")
    capture_apply.add_argument("--plan", required=True)
    capture_apply.add_argument("--confirmed", action="store_true")
    capture_apply.set_defaults(func=cmd_capture_apply)
    capture_verify = capture_subparsers.add_parser("verify")
    capture_verify.add_argument("--page-id", required=True)
    capture_verify.set_defaults(func=cmd_capture_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (
        CliInputError,
        NotionAuthError,
        NotionPermissionError,
        NotionNotFoundError,
        NotionRateLimitError,
        NotionApiError,
        NotionWriterError,
    ) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
