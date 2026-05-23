from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class CliInputError(ValueError):
    """Raised when CLI input cannot be loaded or parsed."""

from capture_to_notion.cache import CacheStore
from capture_to_notion.classifier import classify_content_type
from capture_to_notion.config import config_root, ensure_config
from capture_to_notion.diagnostics import doctor_report, migrate_legacy_config, version_info
from capture_to_notion.models import CaptureInput, WritePlan
from capture_to_notion.notion_adapter import (
    NotionAdapter,
    NotionApiError,
    NotionAuthError,
    NotionNotFoundError,
    NotionPermissionError,
    NotionRateLimitError,
)
from capture_to_notion.planner import build_capture_plan, build_plan_cli_summary, primary_data_source
from capture_to_notion.preflight import build_capture_preflight, build_capture_preflight_summary
from capture_to_notion.scanner import scan_data_source_target, scan_page_target
from capture_to_notion.verifier import url_is_accessible, verify_capture_page
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


LABELED_INPUT_PATTERN = re.compile(r"(?:^|[\s,，;；|｜])([^\s,，;；|｜:：]{1,32})\s*[:：]")
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 100


def _labeled_input_keys(raw_input: str) -> set[str]:
    return {match.group(1) for match in LABELED_INPUT_PATTERN.finditer(raw_input)}


def _profile_labels(profile: Any) -> set[str]:
    labels: set[str] = set()
    if not isinstance(profile, dict):
        return labels
    raw_labels = profile.get("labels")
    if isinstance(raw_labels, dict):
        for value in raw_labels.values():
            if isinstance(value, str):
                labels.add(value)
            elif isinstance(value, list):
                labels.update(item for item in value if isinstance(item, str))
    for section in profile.values():
        if isinstance(section, dict):
            labels.update(_profile_labels(section))
    return labels


def _known_input_labels(structure: dict[str, Any], data_source: dict[str, Any]) -> set[str]:
    labels = _profile_labels(structure.get("parser_profile")) | _profile_labels(data_source.get("parser_profile"))
    fields = data_source.get("fields", {})
    if isinstance(fields, dict):
        labels.update(key for key in fields if isinstance(key, str))
        labels.update(value for value in fields.values() if isinstance(value, str))
    return labels


def _plan_needs_schema_refresh(capture: CaptureInput, cache: CacheStore) -> bool:
    alias = cache.find_alias(capture.target_hint)
    if not isinstance(alias, dict) or not isinstance(alias.get("page_id"), str):
        return False
    structure = cache.target_structure(alias.get("target_id"))
    if not structure:
        return False
    content_type = classify_content_type(capture)
    _, data_source = primary_data_source(structure, content_type)
    if not isinstance(data_source, dict):
        return False
    input_keys = _labeled_input_keys(capture.raw_input)
    if not input_keys:
        return False
    return bool(input_keys - _known_input_labels(structure, data_source))


def _refresh_target_cache_for_plan(capture: CaptureInput, cache: CacheStore, adapter: Any) -> None:
    alias = cache.find_alias(capture.target_hint)
    if not isinstance(alias, dict):
        return
    page_id = alias.get("page_id")
    if not isinstance(page_id, str) or not page_id:
        return
    scan_page_target(
        adapter,
        page_id,
        cache,
        target_id=alias.get("target_id") if isinstance(alias.get("target_id"), str) else None,
        alias=capture.target_hint,
    )


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
    cache = CacheStore(config)
    print_json({"config_root": str(config.root), "aliases": cache.aliases(), "routes": cache.routes()})
    return 0


def cmd_target_suggest(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    capture = load_capture_input(args.input)
    content_type = classify_content_type(capture)
    suggestions = []
    routes = cache.routes().get(content_type, {}).get("preferred_targets", [])
    for route in routes:
        alias_name = route.get("alias")
        alias = cache.find_alias(alias_name)
        if alias:
            suggestions.append({"alias": alias_name, "page_id": alias.get("page_id"), "confidence": route.get("confidence", "medium")})
    print_json({"content_type": content_type, "suggestions": suggestions, "requires_confirmation": len(suggestions) == 0})
    return 0


def cmd_target_list(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    targets = cache.target_summaries()
    print_json({"count": len(targets), "targets": targets})
    return 0


def _target_cache_error(cache: CacheStore, target_id: str) -> CliInputError:
    status = cache.target_cache_status(target_id)
    if status == "invalid_cache":
        return CliInputError(f"target cache 无效: {target_id}")
    return CliInputError(f"未找到 target cache: {target_id}")


def cmd_target_inspect(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    detail = (
        cache.target_detail_summary(alias_name=args.alias, target_id=args.target_id)
        if args.compact
        else cache.target_detail(alias_name=args.alias, target_id=args.target_id)
    )
    if detail is None:
        if args.alias:
            reference = cache.target_reference(alias_name=args.alias)
            if reference is None:
                raise CliInputError(f"未找到 target alias: {args.alias}")
            target_id = reference.get("target_id")
            if target_id:
                raise _target_cache_error(cache, target_id)
            raise CliInputError(f"未找到 target_id: {args.alias}")
        if args.target_id:
            raise _target_cache_error(cache, args.target_id)
        raise CliInputError("未找到 target_id")
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


def cmd_target_scan(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    adapter = NotionAdapter.from_config(config)
    if args.data_source_id:
        target = scan_data_source_target(adapter, args.data_source_id, cache, target_id=args.target_id, alias=args.alias)
    else:
        target = scan_page_target(adapter, args.page_id, cache, target_id=args.target_id, alias=args.alias)
    target_id = target["target"]["target_id"]
    print_json(
        {
            "target_id": target_id,
            "target_file": str(config.targets_dir / f"{target_id}.json"),
            "data_sources": [
                data_source.get("title")
                for data_source in target.get("data_sources", {}).values()
                if data_source.get("title")
            ],
            "requires_confirmation": target.get("requires_confirmation", True),
        }
    )
    return 0


def cmd_capture_preflight(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    capture = load_capture_input(args.input)
    preflight = build_capture_preflight(capture, cache)
    print_json(build_capture_preflight_summary(preflight) if args.compact else preflight)
    return 0



def cmd_capture_plan(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    capture = load_capture_input(args.input)
    if _plan_needs_schema_refresh(capture, cache):
        _refresh_target_cache_for_plan(capture, cache, NotionAdapter.from_config(config))
    plan = build_capture_plan(capture, cache)
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


def _target_id_for_recovery(cache: CacheStore, plan: WritePlan, capture: CaptureInput) -> str | None:
    alias = cache.find_alias(capture.target_hint)
    if isinstance(alias, dict) and isinstance(alias.get("target_id"), str):
        return alias["target_id"]
    structure = cache.target_structure_for_data_source(plan.target.data_source_id)
    target = structure.get("target", {}) if isinstance(structure, dict) else {}
    target_id = target.get("target_id")
    return target_id if isinstance(target_id, str) else None



def _page_id_for_recovery(plan: WritePlan, capture: CaptureInput, cache: CacheStore) -> str | None:
    alias = cache.find_alias(capture.target_hint)
    if isinstance(alias, dict) and isinstance(alias.get("page_id"), str):
        return alias["page_id"]
    return plan.target.page_id



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



def _recover_stale_cache_and_apply(
    *,
    plan_path: Path,
    plan: WritePlan,
    cache: CacheStore,
    adapter: Any,
    error: Exception,
) -> dict[str, Any]:
    if not plan.capture_input:
        raise error
    capture = CaptureInput.from_dict(plan.capture_input)
    page_id = _page_id_for_recovery(plan, capture, cache)
    target_id = _target_id_for_recovery(cache, plan, capture)
    if not page_id:
        raise error

    scan_page_target(
        adapter,
        page_id,
        cache,
        target_id=target_id,
        alias=capture.target_hint,
    )
    refreshed_plan = build_capture_plan(capture, cache)
    refreshed_plan.save(plan_path)
    if refreshed_plan.requires_confirmation:
        raise CliInputError(
            f"目标结构已刷新，但新计划需要确认: {refreshed_plan.confirmation_reason or '未说明原因'}"
        )
    refreshed_structure = cache.target_structure_for_data_source(refreshed_plan.target.data_source_id)
    if refreshed_structure is None:
        raise CliInputError("目标结构已刷新，但仍未找到可写 data_source，请重新选择目标")
    result = _apply_plan_with_verification(refreshed_plan, refreshed_structure, adapter)
    result["recovered_from_stale_cache"] = True
    result["stale_cache_error"] = str(error)
    return result



def cmd_capture_apply(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    plan = load_write_plan(args.plan)

    if plan.requires_confirmation and not args.confirmed:
        reason = plan.confirmation_reason or "未说明原因"
        raise CliInputError(f"计划需要确认后才能执行: {reason}. 如已确认，请传入 --confirmed")

    if args.confirmed:
        if not plan.operations and plan.planned_operations:
            plan.operations = plan.planned_operations
        if not plan.asset_operations and plan.planned_asset_operations:
            plan.asset_operations = plan.planned_asset_operations
        if not plan.completion_operations and plan.planned_completion_operations:
            plan.completion_operations = plan.planned_completion_operations

    if not plan.operations:
        raise CliInputError("计划没有可执行操作，请重新运行 capture plan 生成可执行计划")

    target_structure = cache.target_structure_for_data_source(plan.target.data_source_id)
    if target_structure is None:
        raise CliInputError(
            f"未找到 data_source_id={plan.target.data_source_id} 的目标结构缓存，请先运行 target scan"
        )

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
        result = _recover_stale_cache_and_apply(
            plan_path=Path(args.plan),
            plan=plan,
            cache=cache,
            adapter=adapter,
            error=exc,
        )
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
    target_scan.set_defaults(func=cmd_target_scan)

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
