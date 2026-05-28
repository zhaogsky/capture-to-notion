from __future__ import annotations

import hashlib
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capture_to_notion.assets import plan_cover_asset
from capture_to_notion.blocks import build_body_blocks
from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import AppConfig
from capture_to_notion.classifier import classify_content_type, normalize_state
from capture_to_notion.models import AssetOperation, CaptureInput, Target, WritePlan
from capture_to_notion.schema import WRITABLE_PROPERTY_TYPES, confirmation_blocking_warnings
from capture_to_notion.target_resolver import resolve_capture_target


METADATA_DELIMITER_PATTERN = r"[\s,，;；|｜]"
METADATA_COLON_PATTERN = r"[:：]"
METADATA_ASSIGNMENT_PATTERN = rf"(?:{METADATA_COLON_PATTERN}|=|改成|设为|更新为|调整为|变更为)"
METADATA_LABEL_PREFIX_PATTERN = r"(?:然后|并且|同时|并|把|将)?"
METADATA_VALUE_TERMINATOR_PATTERN = r"[\r\n;；|｜]"


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
    if not isinstance(parser_profile, dict):
        return known_labels

    labels = parser_profile.get("labels", {})
    if isinstance(labels, dict):
        for value in labels.values():
            known_labels.extend(label for label in _string_list(value) if label not in known_labels)

    completions = parser_profile.get("relation_completions", [])
    if isinstance(completions, list):
        for completion in completions:
            if not isinstance(completion, dict):
                continue
            completion_labels = completion.get("labels", {})
            if not isinstance(completion_labels, dict):
                continue
            for value in completion_labels.values():
                known_labels.extend(label for label in _string_list(value) if label not in known_labels)
    return known_labels


def _metadata_label_pattern(labels: list[str]) -> str:
    return "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))


def extract_labeled_value(raw_input: str, labels: list[str], known_labels: list[str] | None = None) -> str | None:
    if not labels:
        return None
    label_pattern = _metadata_label_pattern(labels)
    known_label_values = known_labels if known_labels is not None else labels
    boundary_label_values = list(dict.fromkeys([*known_label_values, "页面信息"]))
    known_label_pattern = _metadata_label_pattern(boundary_label_values)
    match = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:{METADATA_LABEL_PREFIX_PATTERN})(?:{label_pattern})\s*{METADATA_ASSIGNMENT_PATTERN}\s*(.+?)(?=(?:(?:{METADATA_DELIMITER_PATTERN}+|。)(?:{METADATA_LABEL_PREFIX_PATTERN})(?:{known_label_pattern})\s*{METADATA_ASSIGNMENT_PATTERN})|{METADATA_VALUE_TERMINATOR_PATTERN}|$)",
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


def _extract_chinese_quoted_title(raw_input: str) -> str | None:
    start = raw_input.find("《")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(raw_input)):
        char = raw_input[index]
        if char == "《":
            depth += 1
        elif char == "》":
            depth -= 1
            if depth == 0:
                title = raw_input[start + 1:index]
                return title or None
    return None



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
    known_labels = _known_parser_labels(parser_profile)
    labeled_title = extract_labeled_value(raw_input, _parser_labels(parser_profile, "title"), known_labels)
    if labeled_title:
        return _clean_title_suffix(labeled_title, parser_profile)
    quoted_title = _extract_chinese_quoted_title(raw_input)
    if quoted_title:
        return _clean_title_suffix(quoted_title, parser_profile)
    known_label_pattern = _metadata_label_pattern(known_labels)
    label_suffix = re.search(
        rf"(?:^|{METADATA_DELIMITER_PATTERN})(?:{METADATA_LABEL_PREFIX_PATTERN})(?:{known_label_pattern})\s*{METADATA_ASSIGNMENT_PATTERN}",
        raw_input,
        flags=re.IGNORECASE,
    ) if known_label_pattern else None
    if label_suffix:
        title = _clean_title_suffix(raw_input[: label_suffix.start()], parser_profile)
        if title:
            return title
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


def _integer_record_keys(parser_profile: dict[str, Any] | None) -> set[str]:
    if not isinstance(parser_profile, dict):
        return set()
    value_types = parser_profile.get("value_types", {})
    integer_keys = {
        record_key
        for record_key, value_type in (value_types.items() if isinstance(value_types, dict) else [])
        if isinstance(record_key, str) and value_type == "integer"
    }
    integer_keys.update(_string_list(parser_profile.get("numeric_fields")))
    return integer_keys


def _coerce_record_value(record_key: str, value: Any, parser_profile: dict[str, Any] | None) -> Any:
    if record_key not in _integer_record_keys(parser_profile) or not isinstance(value, str):
        return value
    match = re.search(r"\d+", value)
    if not match:
        return value
    return int(match.group(0))


def _profile_labeled_values(
    raw_input: str,
    parser_profile: dict[str, Any] | None,
    exclude_keys: set[str] | None = None,
) -> dict[str, Any]:
    labels = parser_profile.get("labels", {}) if isinstance(parser_profile, dict) else {}
    if not isinstance(labels, dict):
        return {}

    known_labels = _known_parser_labels(parser_profile)
    excluded = exclude_keys or set()
    values: dict[str, Any] = {}
    for record_key in labels:
        if record_key in excluded:
            continue
        value = extract_labeled_value(raw_input, _parser_labels(parser_profile, record_key), known_labels)
        if value is None:
            continue
        values[record_key] = _coerce_record_value(record_key, value, parser_profile)
    return values


def default_cover_url(content_type: str, title: str) -> str | None:
    return None


def plan_id_for(capture: CaptureInput) -> str:
    digest = hashlib.sha256(capture.raw_input.encode("utf-8")).hexdigest()[:8]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{today}-{digest}"


def _has_writable_title_schema(data_source: dict[str, Any]) -> bool:
    schema = data_source.get("schema")
    if not isinstance(schema, dict):
        return False
    return any(
        isinstance(property_schema, dict) and property_schema.get("type") == "title"
        for property_schema in schema.values()
    )



def _writable_data_source_candidates(structure: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (key, value)
        for key, value in structure.get("data_sources", {}).items()
        if isinstance(value, dict) and _has_writable_title_schema(value)
    ]



def primary_data_source(structure: dict[str, Any], content_type: str) -> tuple[str | None, dict[str, Any] | None]:
    for key, value in structure.get("data_sources", {}).items():
        if value.get("role") == "primary" and content_type in value.get("content_types", []):
            return key, value
    for key, value in structure.get("data_sources", {}).items():
        if value.get("role") == "primary":
            return key, value
    writable_candidates = _writable_data_source_candidates(structure)
    if len(writable_candidates) == 1:
        return writable_candidates[0]
    return None, None


def _parser_profile_section(profile: Any, content_type: str) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    section = profile.get(content_type)
    if isinstance(section, dict):
        return section
    return profile


def _parser_profile_default_from_config(config_data: dict[str, Any], content_type: str) -> dict[str, Any]:
    parser_profiles = config_data.get("parser_profiles", {})
    if not isinstance(parser_profiles, dict):
        return {}
    defaults = parser_profiles.get("defaults", {})
    return _parser_profile_section(defaults, content_type)


def parser_profile_for(
    structure: dict[str, Any],
    data_source: dict[str, Any],
    content_type: str,
    default_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = dict(default_profile) if isinstance(default_profile, dict) else {}
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
            if not _is_url(source_url):
                continue
            operations.append(
                plan_cover_asset(
                    config,
                    content_type,
                    source_url,
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



def _ambiguous_mapping_candidate_fields(warnings: list[str]) -> set[str]:
    candidates: set[str] = set()
    prefix = "ambiguous_field_mapping:"
    for warning in warnings:
        if not isinstance(warning, str) or not warning.startswith(prefix):
            continue
        parts = warning.split(":", 2)
        if len(parts) != 3:
            continue
        candidates.update(candidate for candidate in parts[2].split(",") if candidate)
    return candidates



def _unmapped_writable_schema_fields(
    schema: dict[str, Any],
    fields: dict[str, str],
    ignored_fields: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    mapped_targets = {target_field for target_field in fields.values() if isinstance(target_field, str)}
    ignored = ignored_fields or set()
    unmapped: dict[str, dict[str, str]] = {}
    for property_name, property_schema in schema.items():
        if not isinstance(property_name, str) or not isinstance(property_schema, dict):
            continue
        property_type = property_schema.get("type")
        if property_type not in WRITABLE_PROPERTY_TYPES or property_name in mapped_targets or property_name in ignored:
            continue
        unmapped[property_name] = {
            "type": str(property_type),
            "value_status": "missing_value",
            "write_status": "omitted_unmapped",
        }
    return unmapped



def _warning_message(warning: str) -> str:
    return warning.rsplit(":", 1)[-1] if ":" in warning else warning



def _warning_detail(warning: str, blocking_warnings: set[str]) -> dict[str, str]:
    if warning.startswith("unmapped_writable_schema_field:"):
        return {
            "code": warning,
            "severity": "notice",
            "category": "unmapped_writable_field",
            "message": _warning_message(warning),
        }
    if warning in blocking_warnings:
        return {
            "code": warning,
            "severity": "blocking",
            "category": "confirmation",
            "message": _warning_message(warning),
        }
    if warning.startswith("summary_content_source_missing:"):
        return {
            "code": warning,
            "severity": "review",
            "category": "enrichment",
            "message": _warning_message(warning),
        }
    if warning.startswith("ambiguous_field_mapping:"):
        category = "field_mapping"
    else:
        category = "diagnostic"
    return {
        "code": warning,
        "severity": "notice",
        "category": category,
        "message": _warning_message(warning),
    }



def _warning_details(
    warnings: list[str],
    non_blocking_warning_prefixes: list[str] | None = None,
) -> list[dict[str, str]]:
    blocking_warnings = set(confirmation_blocking_warnings(warnings, non_blocking_warning_prefixes))
    return [_warning_detail(warning, blocking_warnings) for warning in warnings]



def _cli_warning_sections(summary: dict[str, Any], warnings: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    details = summary.get("warning_details")
    warning_details = details if isinstance(details, list) else _warning_details(warnings or [])
    sections: dict[str, list[dict[str, Any]]] = {
        "blocking": [],
        "unwritten_fields": [],
        "review": [],
        "notices": [],
    }
    for detail in warning_details:
        if not isinstance(detail, dict):
            continue
        severity = detail.get("severity")
        category = detail.get("category")
        if severity == "blocking":
            sections["blocking"].append(dict(detail))
        elif severity == "review":
            sections["review"].append(dict(detail))
        elif category != "unmapped_writable_field":
            sections["notices"].append(dict(detail))

    unmapped_fields = summary.get("unmapped_writable_fields")
    if isinstance(unmapped_fields, dict):
        for field, field_summary in unmapped_fields.items():
            if not isinstance(field, str) or not isinstance(field_summary, dict):
                continue
            sections["unwritten_fields"].append({"field": field, **field_summary})
    return sections



def _trusted_field_sources(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("trusted_field_sources"))



def _asset_trust_required_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("asset_trust_required_fields"))



def _non_blocking_warning_prefixes(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("non_blocking_warning_prefixes"))



def _summary_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("summary_fields"))



def _summary_policy_for(parser_profile: dict[str, Any], record_key: str) -> dict[str, Any]:
    policies = parser_profile.get("summary_policy", {})
    if not isinstance(policies, dict):
        return {}
    policy = policies.get(record_key, {})
    return policy if isinstance(policy, dict) else {}



def _summary_content_source_fields(policy: dict[str, Any]) -> list[str]:
    return _string_list(policy.get("content_source_fields"))



def _summary_enrichment_requirements(
    normalized_record: dict[str, Any],
    parser_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for record_key in _summary_fields(parser_profile):
        policy = _summary_policy_for(parser_profile, record_key)
        if not policy.get("requires_content_source"):
            continue
        content_source_fields = _summary_content_source_fields(policy)
        has_content_source = any(
            normalized_record.get(source_field) not in (None, "", [], {})
            for source_field in content_source_fields
        )
        if has_content_source:
            continue
        normalized_record.pop(record_key, None)
        requirement = {
            "field": record_key,
            "kind": "content_summary",
            "preferred_skill": policy.get("preferred_skill", "summarize"),
            "requires_content_source": True,
            "status": "blocked",
            "reason": "content_source_missing",
        }
        if "fallback" in policy:
            requirement["fallback"] = policy["fallback"]
        requirements.append(requirement)
    return requirements



def _mapping_warnings_for_structure(structure: dict[str, Any]) -> list[str]:
    return [
        warning
        for source in structure.get("data_sources", {}).values()
        for warning in (source.get("mapping_warnings") or [])
    ]



def _blocking_mapping_warnings_for_structure(
    structure: dict[str, Any],
    content_type: str,
    default_profile: dict[str, Any] | None = None,
) -> list[str]:
    blocking_warnings: list[str] = []
    for source in structure.get("data_sources", {}).values():
        source_profile = parser_profile_for(structure, source, content_type, default_profile)
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


LOCATION_PROOF_KEYS = ("database_id", "parent_page_id", "parent_database_id", "parent_data_source_id")


def _location_proof_fields(*sources: dict[str, Any] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in LOCATION_PROOF_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value:
                fields.setdefault(key, value)
    return fields


def _primary_write_target(
    *,
    operation: dict[str, Any],
    title: Any,
    target_page: str | None,
    write_status: str,
    location_proof: dict[str, Any] | None = None,
    context_verification_source: str | None = None,
) -> dict[str, Any]:
    page_id = operation.get("page_id")
    target = {
        "type": "primary_page",
        "action": "update_page" if page_id else "create_page",
        "title": title,
        "target_page": target_page,
        "target_data_source": operation.get("target_data_source"),
        "data_source_id": operation.get("data_source_id"),
        "view_id": operation.get("view_id"),
        "display_view_name": operation.get("view_name"),
        "display_view_type": operation.get("view_type"),
        "target_kind": operation.get("target_kind"),
        "page_id": page_id,
        "page_id_status": "known" if page_id else "pending_after_apply",
    }
    for optional_key in ("view_id", "display_view_name", "display_view_type", "target_kind"):
        if target.get(optional_key) is None:
            target.pop(optional_key)
    if location_proof:
        target.update(location_proof)
    if context_verification_source:
        target["context_verification_source"] = context_verification_source
    if write_status != "ready":
        target["write_status"] = write_status
    return target


def _completion_write_target(
    *,
    operation: dict[str, Any],
    normalized_record: dict[str, Any],
    structure: dict[str, Any],
    write_status: str,
    context_verification_source: str | None = None,
) -> dict[str, Any] | None:
    source_record_key = operation.get("source_record_key")
    target_data_source_id = operation.get("target_data_source_id")
    if not isinstance(source_record_key, str) or not isinstance(target_data_source_id, str):
        return None
    target_data_source = _data_source_by_id(structure, target_data_source_id)
    target = {
        "type": "relation_page",
        "action": "update_page",
        "source_record_key": source_record_key,
        "source_value": normalized_record.get(source_record_key),
        "target_data_source": target_data_source.get("title") if target_data_source else None,
        "target_data_source_id": target_data_source_id,
        "page_id": None,
        "page_id_status": "pending_relation_resolution",
    }
    target.update(_location_proof_fields(target_data_source))
    if context_verification_source:
        target["context_verification_source"] = context_verification_source
    if write_status != "ready":
        target["write_status"] = write_status
    return target


def _write_targets(
    *,
    operations: list[dict[str, Any]],
    completion_operations: list[dict[str, Any]],
    normalized_record: dict[str, Any],
    target_page: str | None,
    structure: dict[str, Any],
    write_status: str,
    context_verification_source: str | None = None,
) -> list[dict[str, Any]]:
    target_structure = structure.get("target") if isinstance(structure.get("target"), dict) else {}
    targets = [
        _primary_write_target(
            operation=operation,
            title=normalized_record.get("title"),
            target_page=target_page,
            write_status=write_status,
            location_proof=_location_proof_fields(_data_source_by_id(structure, operation.get("data_source_id")), target_structure),
            context_verification_source=context_verification_source,
        )
        for operation in operations
        if operation.get("type") == "create_or_update_page"
    ]
    for operation in completion_operations:
        target = _completion_write_target(
            operation=operation,
            normalized_record=normalized_record,
            structure=structure,
            write_status=write_status,
            context_verification_source=context_verification_source,
        )
        if target is not None:
            targets.append(target)
    return targets


def _data_source_by_id(structure: dict[str, Any], data_source_id: str) -> dict[str, Any] | None:
    for data_source in structure.get("data_sources", {}).values():
        if isinstance(data_source, dict) and data_source.get("data_source_id") == data_source_id:
            return data_source
    return None


def _relation_completion_profiles(parser_profile: dict[str, Any]) -> list[dict[str, Any]]:
    completions = parser_profile.get("relation_completions", [])
    if not isinstance(completions, list):
        return []
    return [completion for completion in completions if isinstance(completion, dict)]


def _completion_asset_operations(
    config: AppConfig,
    content_type: str,
    record: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, Any],
    allow_download: bool,
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for record_key, target_field in field_mapping.items():
        if schema.get(target_field, {}).get("type") != "files":
            continue
        source_url = record.get(record_key)
        if not _is_url(source_url):
            continue
        operations.append(
            {
                "type": "file",
                "source_url": source_url,
                "local_cache_path": (
                    _asset_cache_path(config, content_type, record_key, source_url)
                    if allow_download
                    else None
                ),
                "target_field": target_field,
                "action": "download_and_attach" if allow_download else "attach_external_url",
                "record_key": record_key,
                "status": "planned",
                "warning": None,
            }
        )
    return operations


def _completion_labeled_values(
    raw_input: str,
    parser_profile: dict[str, Any],
    completion: dict[str, Any],
) -> dict[str, Any]:
    labels = completion.get("labels", {})
    if not isinstance(labels, dict):
        return {}

    known_labels = _known_parser_labels(parser_profile)
    for value in labels.values():
        for label in _string_list(value):
            if label not in known_labels:
                known_labels.append(label)

    values: dict[str, Any] = {}
    for record_key, record_labels in labels.items():
        if not isinstance(record_key, str):
            continue
        value = extract_labeled_value(raw_input, _string_list(record_labels), known_labels)
        if value is None:
            continue
        values[record_key] = _coerce_record_value(record_key, value, parser_profile)
    return values



def build_relation_completion_operations(
    *,
    config: AppConfig,
    content_type: str,
    structure: dict[str, Any],
    parser_profile: dict[str, Any],
    raw_input: str,
    normalized_record: dict[str, Any],
    allow_download: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operations: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for completion in _relation_completion_profiles(parser_profile):
        source_record_key = completion.get("source_record_key")
        target_data_source_id = completion.get("target_data_source_id")
        field_mapping = completion.get("field_mapping", {})
        if (
            not isinstance(source_record_key, str)
            or not isinstance(target_data_source_id, str)
            or not isinstance(field_mapping, dict)
        ):
            continue
        target_data_source = _data_source_by_id(structure, target_data_source_id)
        if not target_data_source:
            continue
        schema = target_data_source.get("schema", {})
        if not isinstance(schema, dict):
            continue

        completion_values = _completion_labeled_values(raw_input, parser_profile, completion)
        completion_record = {
            record_key: normalized_record.get(record_key)
            for record_key in field_mapping
            if normalized_record.get(record_key) not in (None, "", [], {})
        }
        completion_record.update(
            {
                record_key: value
                for record_key, value in completion_values.items()
                if record_key in field_mapping and value not in (None, "", [], {})
            }
        )
        operation_field_mapping = {
            record_key: target_field
            for record_key, target_field in field_mapping.items()
            if isinstance(record_key, str)
            and isinstance(target_field, str)
            and record_key in completion_record
            and target_field in schema
        }
        writable_fields = {
            record_key: {
                "target_field": target_field if isinstance(target_field, str) else None,
                "value_status": _value_status(completion_record.get(record_key)),
                "write_status": "planned" if record_key in operation_field_mapping else "omitted_missing_value",
            }
            for record_key, target_field in field_mapping.items()
            if isinstance(record_key, str)
        }
        summaries.append(
            {
                "source_record_key": source_record_key,
                "target_data_source": target_data_source.get("title"),
                "writable_fields": writable_fields,
            }
        )
        if not normalized_record.get(source_record_key) or not operation_field_mapping:
            continue
        operations.append(
            {
                "type": "complete_relation_page",
                "source_record_key": source_record_key,
                "target_data_source_id": target_data_source_id,
                "field_mapping": operation_field_mapping,
                "record": {
                    record_key: completion_record[record_key]
                    for record_key in operation_field_mapping
                },
                "asset_operations": _completion_asset_operations(
                    config,
                    content_type,
                    completion_record,
                    operation_field_mapping,
                    schema,
                    allow_download,
                ),
            }
        )
    return operations, summaries



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
    relation_completion_summaries: list[dict[str, Any]] | None = None,
    write_targets: list[dict[str, Any]] | None = None,
    enrichment_requirements: list[dict[str, Any]] | None = None,
    unmapped_writable_fields: dict[str, dict[str, str]] | None = None,
    non_blocking_warning_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    key_fields: dict[str, dict[str, str | None]] = {}
    for key in summary_key_fields or []:
        key_fields[key] = {
            "target_field": field_mapping.get(key) or schema_fields.get(key),
            "value_status": _value_status(normalized_record.get(key)),
        }

    writable_fields: dict[str, dict[str, str | None]] = {}
    for key, target_field in schema_fields.items():
        value_status = _value_status(normalized_record.get(key))
        write_status = "planned" if key in field_mapping else "omitted_missing_value"
        writable_fields[key] = {
            "target_field": field_mapping.get(key) or target_field,
            "value_status": value_status,
            "write_status": write_status,
        }

    summary = {
        "target_page": target_page,
        "target_data_source": target_data_source,
        "title": normalized_record.get("title"),
        "content_type": content_type,
        "state": normalized_record.get("state"),
        "mapped_fields": dict(field_mapping),
        "key_fields": key_fields,
        "writable_fields": writable_fields,
        "write_targets": list(write_targets or []),
        "asset_actions": [_asset_summary(operation) for operation in asset_operations],
        "requires_confirmation": requires_confirmation,
        "confirmation_reason": confirmation_reason,
        "warnings": list(warnings),
        "warning_details": _warning_details(warnings, non_blocking_warning_prefixes),
    }
    if relation_completion_summaries:
        summary["relation_completions"] = relation_completion_summaries
    if enrichment_requirements:
        summary["enrichment_requirements"] = enrichment_requirements
    if unmapped_writable_fields:
        summary["unmapped_writable_fields"] = dict(unmapped_writable_fields)
    return summary



def build_plan_cli_summary(plan: WritePlan) -> dict[str, Any]:
    target = {
        "page_title": plan.target.page_title,
        "page_id": plan.target.page_id,
        "data_source_id": plan.target.data_source_id,
        "view_id": plan.target.view_id,
        "view_name": plan.target.view_name,
        "view_type": plan.target.view_type,
        "target_kind": plan.target.target_kind,
        "parent_page_id": plan.target.parent_page_id,
        "confidence": plan.target.confidence,
        "source": plan.target.source,
    }
    for optional_key in ("view_id", "view_name", "view_type", "target_kind", "parent_page_id"):
        if target.get(optional_key) is None:
            target.pop(optional_key)
    return {
        "plan_id": plan.plan_id,
        "content_type": plan.content_type,
        "target": target,
        "summary": plan.summary,
        "warnings": list(plan.warnings),
        "warning_sections": _cli_warning_sections(plan.summary, plan.warnings),
        "requires_confirmation": plan.requires_confirmation,
        "confirmation_reason": plan.confirmation_reason,
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
    states_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = extract_title(raw_input, parser_profile)
    return {
        "title": title,
        "state": normalize_state(state, states_config),
        "cover": default_cover_url(content_type, title),
    }


def _record_defaults(parser_profile: dict[str, Any] | None) -> dict[str, Any]:
    defaults = parser_profile.get("record_defaults", {}) if isinstance(parser_profile, dict) else {}
    if not isinstance(defaults, dict):
        return {}
    return {key: value for key, value in defaults.items() if isinstance(key, str)}


def _profile_normalized_record(
    *,
    raw_input: str,
    state: str | None,
    content_type: str,
    parser_profile: dict[str, Any],
    states_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = _base_normalized_record(
        raw_input=raw_input,
        state=state,
        content_type=content_type,
        parser_profile=parser_profile,
        states_config=states_config,
    )
    record.update(_record_defaults(parser_profile))
    record.update(_profile_labeled_values(raw_input, parser_profile))
    return record


def _book_normalized_record(
    *,
    raw_input: str,
    state: str | None,
    parser_profile: dict[str, Any],
    states_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _profile_normalized_record(
        raw_input=raw_input,
        state=state,
        content_type="book",
        parser_profile=parser_profile,
        states_config=states_config,
    )


def _podcast_normalized_record(
    *,
    raw_input: str,
    state: str | None,
    parser_profile: dict[str, Any],
    states_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _profile_normalized_record(
        raw_input=raw_input,
        state=state,
        content_type="podcast_episode",
        parser_profile=parser_profile,
        states_config=states_config,
    )


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


DATE_GENERIC_LABELS = ["时间", "日期", "完成时间", "完成日期"]


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _schema_input_labels(property_name: str, property_type: str, single_unmapped_date_field: bool) -> list[str]:
    labels = [property_name]
    if property_type == "date" and single_unmapped_date_field:
        labels.extend(DATE_GENERIC_LABELS)
    return _unique_strings(labels)


def _schema_input_field_labels(
    raw_input: str,
    fields: dict[str, str],
    schema: dict[str, Any],
    parser_profile: dict[str, Any],
) -> dict[str, list[str]]:
    mapped_targets = {target_field for target_field in fields.values() if isinstance(target_field, str)}
    unmapped_date_fields = [
        property_name
        for property_name, property_schema in schema.items()
        if isinstance(property_name, str)
        and isinstance(property_schema, dict)
        and property_schema.get("type") == "date"
        and property_name not in mapped_targets
    ]
    single_unmapped_date_field = len(unmapped_date_fields) == 1
    candidate_labels: dict[str, list[str]] = {}
    for property_name, property_schema in schema.items():
        if not isinstance(property_name, str) or not isinstance(property_schema, dict):
            continue
        property_type = property_schema.get("type")
        if property_type not in WRITABLE_PROPERTY_TYPES or property_name in mapped_targets:
            continue
        labels = _schema_input_labels(property_name, str(property_type), single_unmapped_date_field)
        candidate_labels[property_name] = labels

    all_candidate_labels = [label for labels in candidate_labels.values() for label in labels]
    known_labels = _unique_strings(_known_parser_labels(parser_profile) + all_candidate_labels)
    return {
        property_name: labels
        for property_name, labels in candidate_labels.items()
        if extract_labeled_value(raw_input, labels, known_labels) is not None
    }


def _parser_profile_with_schema_input_labels(
    parser_profile: dict[str, Any],
    schema_input_labels: dict[str, list[str]],
) -> dict[str, Any]:
    if not schema_input_labels:
        return parser_profile
    labels = dict(parser_profile.get("labels", {})) if isinstance(parser_profile.get("labels"), dict) else {}
    for record_key, record_labels in schema_input_labels.items():
        labels[record_key] = _unique_strings(_string_list(labels.get(record_key)) + record_labels)
    return {**parser_profile, "labels": labels}


def _parser_profile_with_mapped_schema_field_labels(
    parser_profile: dict[str, Any],
    fields: dict[str, str],
    field_sources: dict[str, str],
    schema: dict[str, Any],
) -> dict[str, Any]:
    labels = dict(parser_profile.get("labels", {})) if isinstance(parser_profile.get("labels"), dict) else {}
    schema_input_labels: dict[str, list[str]] = {}
    for record_key, target_field in fields.items():
        if not isinstance(record_key, str) or not isinstance(target_field, str):
            continue
        if record_key in labels:
            continue
        if field_sources.get(record_key) not in {"user_binding", "profile"}:
            continue
        if not isinstance(schema.get(target_field), dict):
            continue
        schema_input_labels[record_key] = [target_field]
    return _parser_profile_with_schema_input_labels(parser_profile, schema_input_labels)


def _apply_schema_input_field_mappings(
    *,
    raw_input: str,
    fields: dict[str, str],
    field_sources: dict[str, str],
    schema: dict[str, Any],
    parser_profile: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any], bool]:
    schema_input_labels = _schema_input_field_labels(raw_input, fields, schema, parser_profile)
    if not schema_input_labels:
        return fields, field_sources, parser_profile, False
    updated_fields = dict(fields)
    updated_field_sources = dict(field_sources)
    for record_key in schema_input_labels:
        updated_fields[record_key] = record_key
        updated_field_sources[record_key] = "profile"
    updated_profile = _parser_profile_with_schema_input_labels(parser_profile, schema_input_labels)
    return updated_fields, updated_field_sources, updated_profile, True



def _state_option_names(property_schema: dict[str, Any] | None) -> set[str]:
    if not isinstance(property_schema, dict) or property_schema.get("type") not in {"select", "status"}:
        return set()
    options = property_schema.get("options", [])
    type_options = property_schema.get(str(property_schema.get("type")))
    if (not isinstance(options, list) or not options) and isinstance(type_options, dict):
        options = type_options.get("options", [])
    if not isinstance(options, list):
        return set()
    return {
        option.get("name")
        for option in options
        if isinstance(option, dict) and isinstance(option.get("name"), str)
    }


def _plan_state_value(
    value: str | None,
    *,
    fields: dict[str, str],
    schema: dict[str, Any],
    states_config: dict[str, Any] | None,
) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        state_field = fields.get("state")
        property_schema = schema.get(state_field) if isinstance(state_field, str) else None
        if stripped in _state_option_names(property_schema):
            return stripped
    return normalize_state(value, states_config)



def _normalized_record_for_capture(
    capture: CaptureInput,
    content_type: str,
    parser_profile: dict[str, Any],
    states_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _profile_normalized_record(
        raw_input=capture.raw_input,
        state=capture.state,
        content_type=content_type,
        parser_profile=parser_profile,
        states_config=states_config,
    )


PAGE_PARENT_COMPATIBLE_HINTS = {"article", "note", "plain_text", "text", "unknown", None}
PAGE_PARENT_INCOMPATIBLE_HINTS = {"book", "podcast", "podcast_episode"}


def _plain_page_capture_compatible(capture: CaptureInput, content_type: str) -> bool:
    hint = capture.content_type_hint
    normalized_hint = hint.casefold() if isinstance(hint, str) else None
    normalized_content_type = content_type.casefold() if isinstance(content_type, str) else None
    if normalized_hint in PAGE_PARENT_INCOMPATIBLE_HINTS or normalized_content_type in PAGE_PARENT_INCOMPATIBLE_HINTS:
        return False
    if normalized_hint in PAGE_PARENT_COMPATIBLE_HINTS or normalized_content_type in PAGE_PARENT_COMPATIBLE_HINTS:
        return True
    return capture.input_shape_hint in {"plain_text", "markdown", None}


def _extract_plain_page_title(raw_input: str) -> str:
    labeled = re.search(r"^\s*(?:标题|Title)\s*[:：]\s*(.+?)\s*$", raw_input, flags=re.IGNORECASE | re.MULTILINE)
    if labeled:
        title = labeled.group(1).strip()
        if title:
            return title[:80]
    heading = re.search(r"^\s*#\s+(.+?)\s*$", raw_input, flags=re.MULTILINE)
    if heading:
        title = heading.group(1).strip()
        if title:
            return title[:80]
    for line in raw_input.splitlines():
        title = line.strip()
        if title:
            return title[:80]
    return "Untitled"


def _v2_root_page_title(graph: dict[str, Any], page_id: str) -> str | None:
    pages = graph.get("pages") if isinstance(graph.get("pages"), dict) else {}
    page = pages.get(page_id)
    if not isinstance(page, dict):
        return None
    title = page.get("title")
    return title if isinstance(title, str) and title else None


def _v2_page_parent_plan(
    capture: CaptureInput,
    cache: CacheV2Store,
    content_type: str,
    states_config: dict[str, Any] | None = None,
) -> WritePlan | None:
    if not _plain_page_capture_compatible(capture, content_type):
        return None
    alias_name = capture.target_hint
    if not isinstance(alias_name, str) or not alias_name:
        return None
    alias = cache.find_alias(alias_name)
    if not isinstance(alias, dict):
        return None
    graph_id = alias.get("graph_id")
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    if not isinstance(graph, dict):
        return None
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    parent_page_id = root.get("id") if root.get("kind") == "page" else None
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    if not isinstance(parent_page_id, str) or not parent_page_id or data_sources:
        return None

    title = _extract_plain_page_title(capture.raw_input)
    body_blocks = build_body_blocks(capture.raw_input, title=title)
    target_page = _v2_root_page_title(graph, parent_page_id)
    operation = {
        "operation_id": "create_child_page:0",
        "type": "create_child_page",
        "parent_page_id": parent_page_id,
        "title": title,
        "body_blocks": body_blocks,
    }
    normalized_record = {"title": title, "state": normalize_state(capture.state, states_config)}
    write_targets = [
        {
            "type": "primary_page",
            "action": "create_child_page",
            "title": title,
            "target_page": target_page,
            "parent_page_id": parent_page_id,
            "target_kind": "page_parent",
            "page_id": None,
            "page_id_status": "pending_after_apply",
            "context_verification_source": "v2_page_graph",
        }
    ]
    summary = build_plan_summary(
        content_type=content_type,
        target_page=target_page,
        target_data_source=None,
        normalized_record=normalized_record,
        field_mapping={},
        schema_fields={},
        asset_operations=[],
        requires_confirmation=False,
        confirmation_reason=None,
        warnings=[],
        write_targets=write_targets,
    )
    summary["body_block_count"] = len(body_blocks)
    plan = WritePlan(
        plan_id=plan_id_for(capture),
        content_type=content_type,
        target=Target(
            page_title=target_page,
            page_id=parent_page_id,
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_id=graph_id,
            target_kind="page_parent",
            parent_page_id=parent_page_id,
        ),
        summary=summary,
        normalized_record=normalized_record,
        field_mapping={},
        operations=[operation],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
        capture_input=capture.to_dict(),
    )
    cache.write_plan(plan.plan_id, plan.to_dict())
    return plan


def _shell_arg(value: str) -> str:
    if value and all(ch.isalnum() or ch in "@%+=:,./-" for ch in value):
        return value
    return shlex.quote(value)


def unresolved_plan(
    capture: CaptureInput,
    content_type: str,
    reason: str,
    warnings: list[str] | None = None,
    states_config: dict[str, Any] | None = None,
) -> WritePlan:
    title = extract_title(capture.raw_input)
    normalized_record = {"title": title, "state": normalize_state(capture.state, states_config)}
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


def build_capture_plan(capture: CaptureInput, cache: CacheStore | CacheV2Store) -> WritePlan:
    content_type = classify_content_type(capture)
    states_config = cache.read_json(cache.config.states_file, {"states": {}})
    if isinstance(cache, CacheV2Store):
        page_parent_plan = _v2_page_parent_plan(capture, cache, content_type, states_config)
        if page_parent_plan is not None:
            return page_parent_plan
    config_data = cache.read_json(cache.config.config_file, {})
    default_parser_profile = _parser_profile_default_from_config(config_data, content_type)
    resolution = resolve_capture_target(capture, cache, content_type)
    if resolution.get("status") in {
        "target_missing",
        "target_not_resolved",
        "ambiguous_target",
        "target_context_unverified",
        "target_context_mismatch",
    }:
        reason = "target_not_resolved" if resolution.get("status") == "target_missing" else str(resolution.get("status") or "target_not_resolved")
        return unresolved_plan(
            capture,
            content_type,
            reason,
            states_config=states_config,
        )

    structure = resolution.get("structure")
    if not isinstance(structure, dict) or not structure:
        warnings = ["目标页面未解析，需要先选择或确认存储页面。"]
        page_id = resolution.get("page_id")
        alias_name = resolution.get("alias") or capture.target_hint
        if isinstance(page_id, str) and page_id and isinstance(alias_name, str):
            warnings.append(
                f"capture-to-notion target scan --page-id {_shell_arg(page_id)} --alias {_shell_arg(alias_name)}"
            )
        return unresolved_plan(capture, content_type, "target_structure_missing", warnings, states_config=states_config)

    _, data_source = primary_data_source(structure, content_type)
    if not data_source:
        reason = (
            "data_source_ambiguous"
            if len(_writable_data_source_candidates(structure)) > 1
            else "primary_data_source_missing"
        )
        return unresolved_plan(capture, content_type, reason, states_config=states_config)

    fields = data_source.get("fields", {})
    field_sources = data_source.get("field_sources", {})
    data_source_schema = data_source.get("schema", {})
    parser_profile = _parser_profile_with_title_cleanup_terms(
        parser_profile_for(structure, data_source, content_type, default_parser_profile),
        capture,
        structure,
    )
    fields, field_sources, parser_profile, cache_updated = _apply_schema_input_field_mappings(
        raw_input=capture.raw_input,
        fields=fields if isinstance(fields, dict) else {},
        field_sources=field_sources if isinstance(field_sources, dict) else {},
        schema=data_source_schema if isinstance(data_source_schema, dict) else {},
        parser_profile=parser_profile,
    )
    parser_profile = _parser_profile_with_mapped_schema_field_labels(
        parser_profile,
        fields if isinstance(fields, dict) else {},
        field_sources if isinstance(field_sources, dict) else {},
        data_source_schema if isinstance(data_source_schema, dict) else {},
    )
    if cache_updated:
        data_source["fields"] = fields
        data_source["field_sources"] = field_sources
        target_id = resolution.get("target_id")
        if isinstance(target_id, str) and target_id:
            cache.write_json(cache.config.targets_dir / f"{target_id}.json", structure)
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
    normalized_record["state"] = _plan_state_value(
        capture.state,
        fields=fields,
        schema=data_source_schema if isinstance(data_source_schema, dict) else {},
        states_config=states_config,
    )
    enrichment_requirements = _summary_enrichment_requirements(normalized_record, parser_profile)
    cover_url = normalized_record.get("cover")

    confirmation_reason = structure.get("confirmation_reason")
    warnings = list(data_source.get("mapping_warnings") or [])
    for warning in untrusted_mapping_warnings:
        if warning not in warnings:
            warnings.append(warning)
    blocking_mapping_warnings = confirmation_blocking_warnings(warnings, non_blocking_warning_prefixes)
    structure_mapping_warnings = _mapping_warnings_for_structure(structure)
    blocking_structure_mapping_warnings = _blocking_mapping_warnings_for_structure(
        structure,
        content_type,
        default_parser_profile,
    )
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
    for requirement in enrichment_requirements:
        warning = f"summary_content_source_missing:{requirement['field']}"
        if warning not in warnings:
            warnings.append(warning)
    if not confirmation_reason and enrichment_requirements:
        confirmation_reason = "summary_content_source_missing"
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
        or enrichment_requirements
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
    unmapped_writable_fields = _unmapped_writable_schema_fields(
        data_source_schema if isinstance(data_source_schema, dict) else {},
        fields if isinstance(fields, dict) else {},
        _ambiguous_mapping_candidate_fields(warnings),
    )
    for property_name in unmapped_writable_fields:
        warning = f"unmapped_writable_schema_field:{property_name}"
        if warning not in warnings:
            warnings.append(warning)
    asset_operations = build_asset_operations(
        cache.config,
        content_type,
        normalized_record,
        asset_mapping,
        capture.options.allow_asset_download,
    )
    completion_operations, completion_summaries = build_relation_completion_operations(
        config=cache.config,
        content_type=content_type,
        structure=structure,
        parser_profile=parser_profile,
        raw_input=capture.raw_input,
        normalized_record=normalized_record,
        allow_download=capture.options.allow_asset_download,
    )
    write_operation = {
        "type": "create_or_update_page",
        "target_data_source": data_source.get("title"),
        "data_source_id": data_source.get("data_source_id"),
    }
    for operation_key, resolution_key in (
        ("view_id", "view_id"),
        ("view_name", "view_name"),
        ("view_type", "view_type"),
        ("target_kind", "target_kind"),
    ):
        value = resolution.get(resolution_key)
        if value is not None:
            write_operation[operation_key] = value
    existing_page_id = resolution.get("existing_page_id")
    if isinstance(existing_page_id, str) and existing_page_id:
        write_operation["page_id"] = existing_page_id
    planned_operations = [write_operation]
    operations = [] if requires_confirmation else planned_operations
    planned_completion_operations = [] if requires_confirmation else completion_operations
    planned_asset_operations = [] if requires_confirmation else asset_operations
    write_targets = _write_targets(
        operations=planned_operations,
        completion_operations=completion_operations,
        normalized_record=normalized_record,
        target_page=structure.get("target", {}).get("title"),
        structure=structure,
        write_status="requires_confirmation" if requires_confirmation else "ready",
        context_verification_source=resolution.get("context_verification_source") if isinstance(resolution.get("context_verification_source"), str) else None,
    )

    plan = WritePlan(
        plan_id=plan_id_for(capture),
        content_type=content_type,
        target=Target(
            page_title=structure.get("target", {}).get("title"),
            page_id=structure.get("target", {}).get("page_id"),
            data_source_id=data_source.get("data_source_id"),
            confidence="high",
            source=str(resolution.get("source") or "alias_cache"),
            target_id=resolution.get("target_id") or structure.get("target", {}).get("target_id"),
            view_id=resolution.get("view_id"),
            view_name=resolution.get("view_name"),
            view_type=resolution.get("view_type"),
            display_page_id=resolution.get("page_id"),
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
            relation_completion_summaries=completion_summaries,
            write_targets=write_targets,
            enrichment_requirements=enrichment_requirements,
            unmapped_writable_fields=unmapped_writable_fields,
            non_blocking_warning_prefixes=non_blocking_warning_prefixes,
        ),
        normalized_record=normalized_record,
        field_mapping=field_mapping,
        operations=operations,
        asset_operations=planned_asset_operations,
        sources=[{"title": "cover", "url": cover_url}] if _is_url(cover_url) else [],
        warnings=warnings,
        requires_confirmation=requires_confirmation,
        confirmation_reason=confirmation_reason,
        completion_operations=planned_completion_operations,
        planned_operations=planned_operations if requires_confirmation else [],
        planned_asset_operations=asset_operations if requires_confirmation else [],
        planned_completion_operations=completion_operations if requires_confirmation else [],
        capture_input=capture.to_dict(),
    )
    if not requires_confirmation:
        if isinstance(cache, CacheV2Store):
            cache.write_plan(plan.plan_id, plan.to_dict())
        else:
            cache.save_plan(plan.plan_id, plan.to_dict())
    return plan
