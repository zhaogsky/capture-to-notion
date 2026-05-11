from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class CliInputError(ValueError):
    """Raised when CLI input cannot be loaded or parsed."""

from capture_to_notion.cache import CacheStore
from capture_to_notion.classifier import classify_content_type
from capture_to_notion.config import config_root, ensure_config
from capture_to_notion.diagnostics import doctor_report, version_info
from capture_to_notion.models import CaptureInput, WritePlan
from capture_to_notion.notion_adapter import (
    NotionAdapter,
    NotionApiError,
    NotionAuthError,
    NotionNotFoundError,
    NotionPermissionError,
    NotionRateLimitError,
)
from capture_to_notion.planner import build_capture_plan
from capture_to_notion.scanner import scan_page_target
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


def cmd_version(args: argparse.Namespace) -> int:
    print_json(version_info(config_root()))
    return 0



def cmd_doctor(args: argparse.Namespace) -> int:
    print_json(doctor_report(config_root()))
    return 0


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


def cmd_target_search(args: argparse.Namespace) -> int:
    config = ensure_config()
    adapter = NotionAdapter.from_config(config)
    results = adapter.search(args.query)
    output: dict[str, Any] = {"query": args.query, "results": results, "requires_confirmation": True}
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


def cmd_capture_plan(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    capture = load_capture_input(args.input)
    plan = build_capture_plan(capture, cache)
    if args.output:
        Path(args.output).write_text(plan.to_json(), encoding="utf-8")
    sys.stdout.write(plan.to_json())
    return 0


def cmd_capture_apply(args: argparse.Namespace) -> int:
    config = ensure_config()
    cache = CacheStore(config)
    plan = load_write_plan(args.plan)

    if plan.requires_confirmation and not args.confirmed:
        reason = plan.confirmation_reason or "未说明原因"
        raise CliInputError(f"计划需要确认后才能执行: {reason}. 如已确认，请传入 --confirmed")

    if not plan.operations:
        raise CliInputError("计划没有可执行操作，请重新运行 capture plan 生成可执行计划")

    target_structure = cache.target_structure_for_data_source(plan.target.data_source_id)
    if target_structure is None:
        raise CliInputError(
            f"未找到 data_source_id={plan.target.data_source_id} 的目标结构缓存，请先运行 target scan"
        )

    adapter = NotionAdapter.from_config(config)
    result = apply_write_plan(plan, target_structure, adapter)
    print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capture-to-notion")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version")
    version_parser.set_defaults(func=cmd_version)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.set_defaults(func=cmd_doctor)

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
    target_search = target_subparsers.add_parser("search")
    target_search.add_argument("--query", required=True)
    target_search.set_defaults(func=cmd_target_search)
    target_scan = target_subparsers.add_parser("scan")
    target_scan.add_argument("--page-id", required=True)
    target_scan.add_argument("--alias")
    target_scan.add_argument("--target-id")
    target_scan.set_defaults(func=cmd_target_scan)

    capture_parser = subparsers.add_parser("capture")
    capture_subparsers = capture_parser.add_subparsers(dest="capture_command", required=True)
    capture_plan = capture_subparsers.add_parser("plan")
    capture_plan.add_argument("--input", required=True)
    capture_plan.add_argument("--output")
    capture_plan.set_defaults(func=cmd_capture_plan)
    capture_apply = capture_subparsers.add_parser("apply")
    capture_apply.add_argument("--plan", required=True)
    capture_apply.add_argument("--confirmed", action="store_true")
    capture_apply.set_defaults(func=cmd_capture_apply)

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
