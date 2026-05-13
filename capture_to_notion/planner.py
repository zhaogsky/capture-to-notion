from __future__ import annotations

import hashlib
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capture_to_notion.assets import plan_cover_asset
from capture_to_notion.cache import CacheStore
from capture_to_notion.config import AppConfig
from capture_to_notion.classifier import classify_content_type, normalize_state
from capture_to_notion.models import AssetOperation, CaptureInput, Target, WritePlan
from capture_to_notion.schema import confirmation_blocking_warnings


METADATA_DELIMITER_PATTERN = r"[\s,，;；|｜]"
METADATA_COLON_PATTERN = r"[:：]"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _parser_labels(parser_profile: dict[str, Any] | None, record_key: str) -> list[str]:
    labels = parser_profile.get("labels", {}) if isinstance(parser_profile, dict) else {}
    if isinstance(labels, dict):
        return _string_list(labels.get(record_key))
    return []


def _known_parser_labels(parser_profile: dict[str, Any] | None) -> list[str]:
    known_labels: list[str] = []
    labels = parser_profile.get("labels", {}) if isinstance(parser_profile, dict) else {}
    if isinstance(labels, dict):
        for value in labels.values():
            known_labels.extend(label for label in _string_list(value) if label not in known_labels)
    return known_labels


def extract_labeled_value(raw_input: str, labels: list[str], known_labels: list[str] | None = None) -> str | None:
    if not labels:
        return None
    label_pattern = "|".join(re.escape(label) for label in labels)
    known_label_values = known_labels if known_labels is not None else labels
    known_label_pattern = "|".join(re.escape(label) for label in known_label_values)
    match = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:{label_pattern})\s*{METADATA_COLON_PATTERN}\s*(.+?)(?=(?:{METADATA_DELIMITER_PATTERN}+(?:{known_label_pattern})\s*{METADATA_COLON_PATTERN})|[\r\n;；|｜]|$)",
        raw_input,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group(1).strip(" \t,，。")
    return value or None


def _clean_title_suffix(title: str, parser_profile: dict[str, Any] | None = None) -> str:
    cleaned = title.strip()
    cleanup_terms = _string_list(parser_profile.get("title_cleanup_terms")) if isinstance(parser_profile, dict) else []
    for term in sorted(cleanup_terms, key=len, reverse=True):
        candidate = re.sub(rf"(?:{METADATA_DELIMITER_PATTERN})+{re.escape(term)}$", "", cleaned).strip()
        if candidate:
            cleaned = candidate
    return cleaned


def extract_title(raw_input: str, parser_profile: dict[str, Any] | None = None) -> str:
    title_patterns = parser_profile.get("title_patterns", []) if isinstance(parser_profile, dict) else []
    for pattern in _string_list(title_patterns):
        try:
            match = re.search(pattern, raw_input, flags=re.IGNORECASE)
        except re.error:
            continue
        if match:
            title = _clean_title_suffix(match.group(1 if match.groups() else 0), parser_profile)
            if title:
                return title
    match = re.search(r"《([^》]+)》", raw_input)
    if match:
        return _clean_title_suffix(match.group(1), parser_profile)
    known_labels = _known_parser_labels(parser_profile)
    known_label_pattern = "|".join(re.escape(label) for label in known_labels)
    label_suffix = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:{known_label_pattern})\s*{METADATA_COLON_PATTERN}",
        raw_input,
        flags=re.IGNORECASE,
    ) if known_label_pattern else None
    if label_suffix:
        return _clean_title_suffix(raw_input[: label_suffix.start()], parser_profile)
    return _clean_title_suffix(raw_input, parser_profile)


def extract_page_count(raw_input: str, parser_profile: dict[str, Any] | None = None) -> int | None:
    known_labels = _known_parser_labels(parser_profile)
    value = extract_labeled_value(
        raw_input,
        _parser_labels(parser_profile, "page_count"),
        known_labels,
    )
    if not value:
        return None
    match = re.search(r"\d+", value)
    if not match:
        return None
    return int(match.group(0))


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


def _parser_profile_section(profile: Any, content_type: str) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    section = profile.get(content_type)
    if isinstance(section, dict):
        return section
    return profile


def _default_parser_profile(content_type: str) -> dict[str, Any]:
    if content_type == "book":
        return {
            "required_schema_fields": ["cover", "author", "isbn", "page_count", "state"],
            "required_value_fields": ["author", "isbn", "page_count"],
            "summary_key_fields": ["cover", "author", "isbn", "page_count"],
            "trusted_field_sources": ["explicit", "profile"],
            "asset_trust_required_fields": ["cover"],
        }
    return {}


def parser_profile_for(
    structure: dict[str, Any],
    data_source: dict[str, Any],
    content_type: str,
) -> dict[str, Any]:
    profile = _default_parser_profile(content_type)
    target_profile = _parser_profile_section(structure.get("parser_profile"), content_type)
    data_source_profile = _parser_profile_section(data_source.get("parser_profile"), content_type)
    for source in (target_profile, data_source_profile):
        if not source:
            continue
        for key, value in source.items():
            if key in {"asset_trust_required_fields", "non_blocking_warning_prefixes"}:
                profile[key] = list(dict.fromkeys(_string_list(profile.get(key)) + _string_list(value)))
            else:
                profile[key] = value
    return profile


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
    field_mapping = {k: v for k, v in fields.items() if normalized_record.get(k) is not None}
    for record_key, mapping in asset_mapping.items():
        if not isinstance(mapping, dict):
            continue
        target_field = mapping.get("field")
        if not isinstance(target_field, str):
            continue
        if schema.get(target_field, {}).get("type") != "files":
            continue
        if normalized_record.get(record_key) is None:
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


def _value_status(value: Any) -> str:
    if value in (None, "", [], {}):
        return "missing_value"
    return "present"



def _required_schema_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("required_schema_fields"))



def _required_value_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("required_value_fields"))



def _summary_key_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("summary_key_fields"))



def _trusted_field_sources(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("trusted_field_sources"))



def _asset_trust_required_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("asset_trust_required_fields"))



def _non_blocking_warning_prefixes(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("non_blocking_warning_prefixes"))



def _mapping_warnings_for_structure(structure: dict[str, Any]) -> list[str]:
    return [
        warning
        for source in structure.get("data_sources", {}).values()
        for warning in (source.get("mapping_warnings") or [])
    ]



def _blocking_mapping_warnings_for_structure(structure: dict[str, Any], content_type: str) -> list[str]:
    blocking_warnings: list[str] = []
    for source in structure.get("data_sources", {}).values():
        source_profile = parser_profile_for(structure, source, content_type)
        source_blocking_warnings = confirmation_blocking_warnings(
            source.get("mapping_warnings") or [],
            _non_blocking_warning_prefixes(source_profile),
        )
        blocking_warnings.extend(source_blocking_warnings)
    return blocking_warnings



def _trusted_mapping_fields(
    fields: dict[str, str],
    field_sources: dict[str, str] | None,
    required_schema_fields: list[str],
    trusted_field_sources: list[str],
) -> dict[str, str]:
    if not trusted_field_sources or not field_sources:
        return dict(fields)

    trusted_fields = dict(fields)
    for key in required_schema_fields:
        source = field_sources.get(key)
        if source not in trusted_field_sources:
            trusted_fields.pop(key, None)
    return trusted_fields



def _untrusted_mapping_warnings(
    fields: dict[str, str],
    field_sources: dict[str, str] | None,
    required_schema_fields: list[str],
    trusted_field_sources: list[str],
) -> list[str]:
    if not trusted_field_sources or not field_sources:
        return []

    warnings: list[str] = []
    for key in required_schema_fields:
        if key not in fields:
            continue
        source = field_sources.get(key)
        if source not in trusted_field_sources:
            warnings.append(f"untrusted_field_mapping:{key}:{source or 'missing'}")
    return warnings



def _filtered_asset_mapping(
    asset_mapping: dict[str, Any],
    trusted_fields: dict[str, str],
    asset_trust_required_fields: list[str],
) -> dict[str, Any]:
    filtered_asset_mapping = dict(asset_mapping)
    for record_key in asset_trust_required_fields:
        if record_key not in trusted_fields:
            filtered_asset_mapping.pop(record_key, None)
    return filtered_asset_mapping



def _asset_summary(operation: AssetOperation) -> dict[str, str | None]:
    return {
        "record_key": operation.record_key,
        "target_field": operation.target_field,
        "action": operation.action,
    }



def build_plan_summary(
    *,
    content_type: str,
    target_page: str | None,
    target_data_source: str | None,
    normalized_record: dict[str, Any],
    field_mapping: dict[str, str],
    schema_fields: dict[str, str],
    asset_operations: list[AssetOperation],
    requires_confirmation: bool,
    confirmation_reason: str | None,
    warnings: list[str],
    summary_key_fields: list[str] | None = None,
) -> dict[str, Any]:
    key_fields: dict[str, dict[str, str | None]] = {}
    for key in summary_key_fields or []:
        key_fields[key] = {
            "target_field": field_mapping.get(key) or schema_fields.get(key),
            "value_status": _value_status(normalized_record.get(key)),
        }

    return {
        "target_page": target_page,
        "target_data_source": target_data_source,
        "title": normalized_record.get("title"),
        "content_type": content_type,
        "state": normalized_record.get("state"),
        "mapped_fields": dict(field_mapping),
        "key_fields": key_fields,
        "asset_actions": [_asset_summary(operation) for operation in asset_operations],
        "requires_confirmation": requires_confirmation,
        "confirmation_reason": confirmation_reason,
        "warnings": list(warnings),
    }



def missing_required_fields(
    content_type: str,
    fields: dict[str, str],
    schema: dict[str, Any] | None = None,
    required_fields: list[str] | None = None,
) -> list[str]:
    fields_to_check = required_fields or []
    missing_fields = [field for field in fields_to_check if field not in fields]
    if "cover" not in missing_fields and "cover" in fields_to_check and schema:
        cover_field = fields.get("cover")
        if schema.get(cover_field, {}).get("type") != "files":
            missing_fields.append("cover")
    return missing_fields



def missing_required_values(
    content_type: str,
    normalized_record: dict[str, Any],
    required_fields: list[str] | None = None,
) -> list[str]:
    fields_to_check = required_fields or []
    return [field for field in fields_to_check if normalized_record.get(field) in (None, "", [], {})]


def _base_normalized_record(
    *,
    raw_input: str,
    state: str | None,
    content_type: str,
    parser_profile: dict[str, Any],
) -> dict[str, Any]:
    title = extract_title(raw_input, parser_profile)
    return {
        "title": title,
        "state": normalize_state(state),
        "cover": default_cover_url(content_type, title),
    }



def _book_normalized_record(
    *,
    raw_input: str,
    state: str | None,
    parser_profile: dict[str, Any],
) -> dict[str, Any]:
    record = _base_normalized_record(
        raw_input=raw_input,
        state=state,
        content_type="book",
        parser_profile=parser_profile,
    )
    known_labels = _known_parser_labels(parser_profile)
    record.update(
        {
            "author": extract_labeled_value(raw_input, _parser_labels(parser_profile, "author"), known_labels),
            "isbn": extract_labeled_value(raw_input, _parser_labels(parser_profile, "isbn"), known_labels),
            "publisher": extract_labeled_value(raw_input, _parser_labels(parser_profile, "publisher"), known_labels),
            "page_count": extract_page_count(raw_input, parser_profile),
        }
    )
    return record



def _podcast_normalized_record(
    *,
    raw_input: str,
    state: str | None,
    parser_profile: dict[str, Any],
) -> dict[str, Any]:
    record = _base_normalized_record(
        raw_input=raw_input,
        state=state,
        content_type="podcast_episode",
        parser_profile=parser_profile,
    )
    known_labels = _known_parser_labels(parser_profile)
    record.update(
        {
            "podcast": extract_labeled_value(raw_input, _parser_labels(parser_profile, "podcast"), known_labels),
            "episode_url": None,
            "published_at": None,
        }
    )
    return record



def _capture_title_cleanup_terms(capture: CaptureInput, structure: dict[str, Any]) -> list[str]:
    terms = _string_list(capture.target_hint)
    target = structure.get("target")
    if isinstance(target, dict):
        terms.extend(_string_list(target.get("title")))
    return list(dict.fromkeys(terms))


def _parser_profile_with_title_cleanup_terms(
    parser_profile: dict[str, Any],
    capture: CaptureInput,
    structure: dict[str, Any],
) -> dict[str, Any]:
    cleanup_terms = list(dict.fromkeys(_string_list(parser_profile.get("title_cleanup_terms")) + _capture_title_cleanup_terms(capture, structure)))
    if not cleanup_terms:
        return parser_profile
    return {**parser_profile, "title_cleanup_terms": cleanup_terms}



def _normalized_record_for_capture(
    capture: CaptureInput,
    content_type: str,
    parser_profile: dict[str, Any],
) -> dict[str, Any]:
    if content_type == "book":
        return _book_normalized_record(
            raw_input=capture.raw_input,
            state=capture.state,
            parser_profile=parser_profile,
        )
    if content_type == "podcast_episode":
        return _podcast_normalized_record(
            raw_input=capture.raw_input,
            state=capture.state,
            parser_profile=parser_profile,
        )
    return _base_normalized_record(
        raw_input=capture.raw_input,
        state=capture.state,
        content_type=content_type,
        parser_profile=parser_profile,
    )


def _shell_arg(value: str) -> str:
    if value and all(ch.isalnum() or ch in "@%+=:,./-" for ch in value):
        return value
    return shlex.quote(value)


def unresolved_plan(
    capture: CaptureInput,
    content_type: str,
    reason: str,
    warnings: list[str] | None = None,
) -> WritePlan:
    title = extract_title(capture.raw_input)
    normalized_record = {"title": title, "state": normalize_state(capture.state)}
    warnings = warnings or ["目标页面未解析，需要先选择或确认存储页面。"]
    return WritePlan(
        plan_id=plan_id_for(capture),
        content_type=content_type,
        target=Target(page_title=None, page_id=None, data_source_id=None, confidence="none", source="unresolved"),
        summary=build_plan_summary(
            content_type=content_type,
            target_page=None,
            target_data_source=None,
            normalized_record=normalized_record,
            field_mapping={},
            schema_fields={},
            asset_operations=[],
            requires_confirmation=True,
            confirmation_reason=reason,
            warnings=warnings,
        ),
        normalized_record=normalized_record,
        field_mapping={},
        operations=[],
        asset_operations=[],
        sources=[],
        warnings=warnings,
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
        warnings = ["目标页面未解析，需要先选择或确认存储页面。"]
        page_id = alias.get("page_id")
        if isinstance(page_id, str) and page_id and capture.target_hint:
            warnings.append(
                f"capture-to-notion target scan --page-id {_shell_arg(page_id)} --alias {_shell_arg(capture.target_hint)}"
            )
        return unresolved_plan(capture, content_type, "target_structure_missing", warnings)

    _, data_source = primary_data_source(structure, content_type)
    if not data_source:
        return unresolved_plan(capture, content_type, "primary_data_source_missing")

    fields = data_source.get("fields", {})
    field_sources = data_source.get("field_sources", {})
    parser_profile = _parser_profile_with_title_cleanup_terms(
        parser_profile_for(structure, data_source, content_type),
        capture,
        structure,
    )
    required_schema_fields = _required_schema_fields(parser_profile)
    required_value_fields = _required_value_fields(parser_profile)
    summary_key_fields = _summary_key_fields(parser_profile)
    trusted_field_sources = _trusted_field_sources(parser_profile)
    asset_trust_required_fields = _asset_trust_required_fields(parser_profile)
    non_blocking_warning_prefixes = _non_blocking_warning_prefixes(parser_profile)
    trusted_mapping_required_fields = list(dict.fromkeys(required_schema_fields + asset_trust_required_fields))
    trusted_fields = _trusted_mapping_fields(
        fields,
        field_sources,
        trusted_mapping_required_fields,
        trusted_field_sources,
    )
    untrusted_mapping_warnings = _untrusted_mapping_warnings(
        fields,
        field_sources,
        trusted_mapping_required_fields,
        trusted_field_sources,
    )
    normalized_record = _normalized_record_for_capture(capture, content_type, parser_profile)
    cover_url = normalized_record.get("cover")

    confirmation_reason = structure.get("confirmation_reason")
    warnings = list(data_source.get("mapping_warnings") or [])
    for warning in untrusted_mapping_warnings:
        if warning not in warnings:
            warnings.append(warning)
    blocking_mapping_warnings = confirmation_blocking_warnings(warnings, non_blocking_warning_prefixes)
    structure_mapping_warnings = _mapping_warnings_for_structure(structure)
    blocking_structure_mapping_warnings = _blocking_mapping_warnings_for_structure(structure, content_type)
    for warning in structure_mapping_warnings:
        if warning not in warnings:
            warnings.append(warning)
    structure_requires_confirmation = bool(structure.get("requires_confirmation"))
    if confirmation_reason == "field_mapping_ambiguous" and not blocking_structure_mapping_warnings:
        confirmation_reason = None
        structure_requires_confirmation = False
    data_source_schema = data_source.get("schema", {})
    missing_fields = missing_required_fields(content_type, trusted_fields, data_source_schema, required_schema_fields)
    missing_values = missing_required_values(content_type, normalized_record, required_value_fields)
    if missing_fields:
        warnings.append(f"{content_type}_schema_incomplete:{','.join(missing_fields)}")
    if missing_values:
        warnings.append(f"{content_type}_key_values_missing:{','.join(missing_values)}")
    if not confirmation_reason and untrusted_mapping_warnings:
        confirmation_reason = "untrusted_field_mapping"
    if not confirmation_reason and blocking_structure_mapping_warnings:
        confirmation_reason = "field_mapping_ambiguous"
    if not confirmation_reason and missing_fields:
        confirmation_reason = f"{content_type}_schema_incomplete"
    if not confirmation_reason and missing_values:
        confirmation_reason = f"{content_type}_key_values_missing"
    requires_confirmation = bool(
        structure_requires_confirmation
        or untrusted_mapping_warnings
        or blocking_structure_mapping_warnings
        or missing_fields
        or missing_values
    )
    asset_mapping = _filtered_asset_mapping(
        structure.get("asset_mapping") or {},
        trusted_fields,
        asset_trust_required_fields,
    )
    for record_key in asset_trust_required_fields:
        asset_mapping.pop(record_key, None)
        target_field = trusted_fields.get(record_key)
        if target_field and data_source_schema.get(target_field, {}).get("type") == "files":
            asset_mapping[record_key] = {"field": target_field, "type": "files", "strategy": "download_and_attach"}
    field_mapping = build_plan_field_mapping(
        normalized_record,
        trusted_fields,
        data_source.get("schema", {}),
        asset_mapping,
    )
    asset_operations = build_asset_operations(
        cache.config,
        content_type,
        normalized_record,
        asset_mapping,
        capture.options.allow_asset_download,
    )
    operations = (
        []
        if requires_confirmation
        else [
            {
                "type": "create_or_update_page",
                "target_data_source": data_source.get("title"),
                "data_source_id": data_source.get("data_source_id"),
            }
        ]
    )
    planned_asset_operations = [] if requires_confirmation else asset_operations

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
        summary=build_plan_summary(
            content_type=content_type,
            target_page=structure.get("target", {}).get("title"),
            target_data_source=data_source.get("title"),
            normalized_record=normalized_record,
            field_mapping=field_mapping,
            schema_fields=trusted_fields,
            asset_operations=asset_operations,
            requires_confirmation=requires_confirmation,
            confirmation_reason=confirmation_reason,
            warnings=warnings,
            summary_key_fields=summary_key_fields,
        ),
        normalized_record=normalized_record,
        field_mapping=field_mapping,
        operations=operations,
        asset_operations=planned_asset_operations,
        sources=[{"title": "Phase 1 placeholder enrichment source", "url": cover_url or ""}],
        warnings=warnings,
        requires_confirmation=requires_confirmation,
        confirmation_reason=confirmation_reason,
    )
    if not requires_confirmation:
        cache.save_plan(plan.plan_id, plan.to_dict())
    return plan
