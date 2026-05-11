from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capture_to_notion.assets import plan_cover_asset
from capture_to_notion.cache import CacheStore
from capture_to_notion.config import AppConfig
from capture_to_notion.classifier import classify_content_type, normalize_state
from capture_to_notion.models import AssetOperation, CaptureInput, Target, WritePlan
from capture_to_notion.schema import semantic_field_mapping


KNOWN_METADATA_LABELS = [
    "作者",
    "author",
    "播客",
    "podcast",
    "节目",
    "show",
    "ISBN",
    "isbn",
    "出版社",
    "publisher",
    "标题",
    "title",
    "链接",
    "url",
    "日期",
    "published_at",
]
METADATA_DELIMITER_PATTERN = r"[\s,，;；|｜]"
METADATA_COLON_PATTERN = r"[:：]"


def extract_labeled_value(raw_input: str, labels: list[str]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    known_label_pattern = "|".join(re.escape(label) for label in KNOWN_METADATA_LABELS)
    match = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:{label_pattern})\s*{METADATA_COLON_PATTERN}\s*(.+?)(?=(?:{METADATA_DELIMITER_PATTERN}+(?:{known_label_pattern})\s*{METADATA_COLON_PATTERN})|[\r\n;；|｜]|$)",
        raw_input,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group(1).strip(" \t,，。")
    return value or None


def extract_book_title(raw_input: str) -> str:
    match = re.search(r"《([^》]+)》", raw_input)
    if match:
        return match.group(1)
    label_suffix = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:作者|author|播客|podcast|节目)\s*{METADATA_COLON_PATTERN}",
        raw_input,
        flags=re.IGNORECASE,
    )
    if label_suffix:
        return raw_input[: label_suffix.start()].strip()
    return raw_input.strip()


def default_cover_url(content_type: str, title: str) -> str | None:
    if content_type == "unknown":
        return None
    digest = hashlib.sha256(f"{content_type}:{title}".encode("utf-8")).hexdigest()[:12]
    return f"https://example.com/capture-to-notion/covers/{digest}.jpg"


def plan_id_for(capture: CaptureInput) -> str:
    digest = hashlib.sha256(capture.raw_input.encode("utf-8")).hexdigest()[:8]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{today}-{digest}"


def primary_data_source(structure: dict[str, Any], content_type: str) -> tuple[str | None, dict[str, Any] | None]:
    for key, value in structure.get("data_sources", {}).items():
        if value.get("role") == "primary" and content_type in value.get("content_types", []):
            return key, value
    for key, value in structure.get("data_sources", {}).items():
        if value.get("role") == "primary":
            return key, value
    return None, None


def _is_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _asset_cache_path(config: AppConfig, content_type: str, record_key: str, source_url: str) -> str:
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
    record_digest = hashlib.sha256(record_key.encode("utf-8")).hexdigest()[:16]
    suffix = Path(source_url.split("?", 1)[0]).suffix.lower() or ".bin"
    return str(config.covers_dir.parent / "assets" / record_digest / content_type / f"{digest}{suffix}")


def build_plan_field_mapping(
    normalized_record: dict[str, Any],
    fields: dict[str, str],
    schema: dict[str, Any],
    asset_mapping: dict[str, Any],
) -> dict[str, str]:
    field_mapping = {k: v for k, v in fields.items() if k in normalized_record}
    for record_key, mapping in asset_mapping.items():
        if not isinstance(mapping, dict):
            continue
        target_field = mapping.get("field")
        if not isinstance(target_field, str):
            continue
        if schema.get(target_field, {}).get("type") != "files":
            continue
        field_mapping.setdefault(record_key, target_field)
    return field_mapping


def build_asset_operations(
    config: AppConfig,
    content_type: str,
    normalized_record: dict[str, Any],
    asset_mapping: dict[str, Any],
    allow_download: bool = True,
) -> list[AssetOperation]:
    operations: list[AssetOperation] = []
    for record_key, mapping in asset_mapping.items():
        if not isinstance(mapping, dict):
            continue
        if mapping.get("type") != "files" or mapping.get("strategy") != "download_and_attach":
            continue
        target_field = mapping.get("field")
        source_url = normalized_record.get(record_key)
        if record_key == "cover":
            operations.append(
                plan_cover_asset(
                    config,
                    content_type,
                    source_url if isinstance(source_url, str) else None,
                    target_field,
                    allow_download,
                )
            )
            continue
        if not _is_url(source_url):
            continue
        operations.append(
            AssetOperation(
                type="file",
                source_url=source_url,
                local_cache_path=_asset_cache_path(config, content_type, record_key, source_url) if allow_download else None,
                target_field=target_field,
                action="download_and_attach" if allow_download else "attach_external_url",
                record_key=record_key,
            )
        )
    return operations


def missing_required_fields(content_type: str, fields: dict[str, str], schema: dict[str, Any] | None = None) -> list[str]:
    if content_type != "book":
        return []

    missing_fields = [field for field in ["cover", "author", "isbn", "state"] if field not in fields]
    if "cover" not in missing_fields and schema:
        semantic_fields = semantic_field_mapping(schema)["fields"]
        if semantic_fields.get("cover") != fields.get("cover"):
            missing_fields.append("cover")
    return missing_fields


def unresolved_plan(capture: CaptureInput, content_type: str, reason: str) -> WritePlan:
    title = extract_book_title(capture.raw_input)
    return WritePlan(
        plan_id=plan_id_for(capture),
        content_type=content_type,
        target=Target(page_title=None, page_id=None, data_source_id=None, confidence="none", source="unresolved"),
        normalized_record={"title": title, "state": normalize_state(capture.state)},
        field_mapping={},
        operations=[],
        asset_operations=[],
        sources=[],
        warnings=["目标页面未解析，需要先选择或确认存储页面。"],
        requires_confirmation=True,
        confirmation_reason=reason,
    )


def build_capture_plan(capture: CaptureInput, cache: CacheStore) -> WritePlan:
    content_type = classify_content_type(capture)
    alias = cache.find_alias(capture.target_hint)
    if not alias:
        return unresolved_plan(capture, content_type, "target_not_resolved")

    structure = cache.target_structure(alias.get("target_id"))
    if not structure:
        return unresolved_plan(capture, content_type, "target_structure_missing")

    _, data_source = primary_data_source(structure, content_type)
    if not data_source:
        return unresolved_plan(capture, content_type, "primary_data_source_missing")

    fields = data_source.get("fields", {})
    title = extract_book_title(capture.raw_input)
    state = normalize_state(capture.state)
    cover_url = default_cover_url(content_type, title)

    normalized_record = {
        "title": title,
        "state": state,
        "cover": cover_url,
    }
    if content_type == "book":
        normalized_record.update(
            {
                "author": extract_labeled_value(capture.raw_input, ["作者", "author"]),
                "isbn": None,
                "publisher": None,
            }
        )
    if content_type == "podcast_episode":
        normalized_record.update(
            {
                "podcast": extract_labeled_value(capture.raw_input, ["播客", "podcast", "节目"]),
                "episode_url": None,
                "published_at": None,
            }
        )

    confirmation_reason = structure.get("confirmation_reason")
    warnings = list(data_source.get("mapping_warnings") or [])
    data_source_schema = data_source.get("schema", {})
    missing_fields = missing_required_fields(content_type, fields, data_source_schema)
    if missing_fields:
        warnings.append(f"{content_type}_schema_incomplete:{','.join(missing_fields)}")
    if not confirmation_reason and data_source.get("mapping_warnings"):
        confirmation_reason = "field_mapping_ambiguous"
    if not confirmation_reason and missing_fields:
        confirmation_reason = f"{content_type}_schema_incomplete"
    requires_confirmation = bool(structure.get("requires_confirmation") or data_source.get("mapping_warnings") or missing_fields)
    asset_mapping = dict(structure.get("asset_mapping") or {})
    cover_field = fields.get("cover")
    if cover_field and data_source_schema.get(cover_field, {}).get("type") == "files":
        asset_mapping["cover"] = {"field": cover_field, "type": "files", "strategy": "download_and_attach"}
    asset_operations = build_asset_operations(
        cache.config,
        content_type,
        normalized_record,
        asset_mapping,
        capture.options.allow_asset_download,
    )

    plan = WritePlan(
        plan_id=plan_id_for(capture),
        content_type=content_type,
        target=Target(
            page_title=structure.get("target", {}).get("title"),
            page_id=structure.get("target", {}).get("page_id"),
            data_source_id=data_source.get("data_source_id"),
            confidence="high",
            source="alias_cache",
        ),
        normalized_record=normalized_record,
        field_mapping=build_plan_field_mapping(
            normalized_record,
            fields,
            data_source.get("schema", {}),
            asset_mapping,
        ),
        operations=(
            []
            if requires_confirmation
            else [
                {
                    "type": "create_or_update_page",
                    "target_data_source": data_source.get("title"),
                    "data_source_id": data_source.get("data_source_id"),
                }
            ]
        ),
        asset_operations=[] if requires_confirmation else asset_operations,
        sources=[{"title": "Phase 1 placeholder enrichment source", "url": cover_url or ""}],
        warnings=warnings,
        requires_confirmation=requires_confirmation,
        confirmation_reason=confirmation_reason,
    )
    if not requires_confirmation:
        cache.save_plan(plan.plan_id, plan.to_dict())
    return plan
