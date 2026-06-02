from __future__ import annotations

import hashlib
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capture_to_notion.assets import plan_cover_asset, verify_image_url
from capture_to_notion.blocks import build_body_blocks
from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import AppConfig
from capture_to_notion.classifier import classify_content_type, normalize_state
from capture_to_notion.models import AssetOperation, CaptureInput, Target, WritePlan
from capture_to_notion.notion_adapter import NotionAdapter
from capture_to_notion.path_utils import graph_object_path, graph_visual_path
from capture_to_notion.people import resolve_record_people_with_facts
from capture_to_notion.schema import WRITABLE_PROPERTY_TYPES, confirmation_blocking_warnings
from capture_to_notion.target_resolver import resolve_capture_target
from capture_to_notion.view_constraints import ViewWriteConstraints, derive_view_write_constraints


METADATA_DELIMITER_PATTERN = r"[\s,，;；|｜]"
METADATA_COLON_PATTERN = r"[:：]"
METADATA_ASSIGNMENT_PATTERN = rf"(?:{METADATA_COLON_PATTERN}|=|改成|设为|更新为|调整为|变更为)"
METADATA_LABEL_PREFIX_PATTERN = r"(?:然后|并且|同时|并|把|将)?"
METADATA_VALUE_TERMINATOR_PATTERN = r"[\r\n;；]"


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


def _template_facts(data_source: dict[str, Any]) -> list[dict[str, Any]]:
    templates = data_source.get("templates")
    if not isinstance(templates, list):
        return []
    return [dict(template) for template in templates if isinstance(template, dict) and isinstance(template.get("template_id"), str)]


def _template_choice_from_capture(capture: CaptureInput, data_source_id: str) -> dict[str, Any]:
    choice = capture.options.template_choice
    if isinstance(choice, dict):
        return choice
    if isinstance(choice, str):
        return {"decision": choice}
    decisions = capture.enrichment.get("template_decisions") if isinstance(capture.enrichment, dict) else None
    if isinstance(decisions, dict):
        data_source_decision = decisions.get(data_source_id)
        if isinstance(data_source_decision, dict):
            return data_source_decision
        if isinstance(data_source_decision, str):
            return {"decision": data_source_decision}
    return {}


def _template_options_for_capture(capture: CaptureInput, data_source: dict[str, Any]) -> dict[str, Any] | None:
    templates = _template_facts(data_source)
    if not templates:
        return None
    data_source_id = data_source.get("data_source_id")
    if not isinstance(data_source_id, str) or not data_source_id:
        return None
    choice = _template_choice_from_capture(capture, data_source_id)
    raw_decision = str(choice.get("decision") or choice.get("action") or "undecided")
    decision = "skip_template" if raw_decision in {"skip", "none", "no_template"} else raw_decision
    selected_template_id = choice.get("template_id") or choice.get("selected_template_id")
    if decision == "use_template" and not isinstance(selected_template_id, str):
        default_template = next((template for template in templates if template.get("is_default") is True), templates[0])
        selected_template_id = default_template.get("template_id")
    apply_status = "facts_only"
    facts_only = True
    if decision == "use_template":
        apply_status = "unsupported"
        facts_only = False
    elif decision in {"skip_template", "none"}:
        decision = "skip_template"
        apply_status = "skipped"
        facts_only = False
    else:
        decision = "undecided"
        selected_template_id = None
    return {
        "data_source_id": data_source_id,
        "templates": templates,
        "decision": decision,
        "selected_template_id": selected_template_id if isinstance(selected_template_id, str) else None,
        "facts_only": facts_only,
        "apply_status": apply_status,
    }


def _write_target_template_summary(template_options: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(template_options, dict):
        return None
    return {
        "decision": template_options.get("decision"),
        "selected_template_id": template_options.get("selected_template_id"),
        "apply_status": template_options.get("apply_status"),
    }


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
            if key in {"asset_trust_required_fields", "non_blocking_warning_prefixes", "blocking_warning_prefixes"}:
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



def _warnings_matching_prefixes(warnings: list[str] | None, prefixes: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized_prefixes = tuple(prefixes or ())
    if not normalized_prefixes:
        return []
    return [warning for warning in (warnings or []) if warning.startswith(normalized_prefixes)]



def _warning_detail(warning: str, blocking_warnings: set[str], explicit_blocking_warnings: set[str] | None = None) -> dict[str, str]:
    explicit_blocking_warnings = explicit_blocking_warnings or set()
    if warning.startswith("unmapped_writable_schema_field:"):
        return {
            "code": warning,
            "severity": "notice",
            "category": "unmapped_writable_field",
            "message": _warning_message(warning),
        }
    if warning.startswith("relation_resolution_required:"):
        return {
            "code": warning,
            "severity": "blocking",
            "category": "relation_resolution",
            "message": _warning_message(warning),
        }
    if warning.startswith("relation_resolution_pending:"):
        return {
            "code": warning,
            "severity": "blocking" if warning in explicit_blocking_warnings else "review",
            "category": "relation_resolution",
            "message": _warning_message(warning),
        }
    if warning.startswith("people_resolution_required:"):
        return {
            "code": warning,
            "severity": "blocking",
            "category": "people_resolution",
            "message": _warning_message(warning),
        }
    if warning.startswith("people_ambiguous:"):
        return {
            "code": warning,
            "severity": "blocking" if warning in explicit_blocking_warnings else "review",
            "category": "people_resolution",
            "message": _warning_message(warning),
        }
    if warning.startswith("asset_url_inaccessible:"):
        return {
            "code": warning,
            "severity": "blocking",
            "category": "asset",
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
    blocking_warning_prefixes: list[str] | None = None,
) -> list[dict[str, str]]:
    explicit_blocking_warnings = set(_warnings_matching_prefixes(warnings, blocking_warning_prefixes))
    blocking_warnings = set(confirmation_blocking_warnings(warnings, non_blocking_warning_prefixes))
    blocking_warnings.update(explicit_blocking_warnings)
    return [_warning_detail(warning, blocking_warnings, explicit_blocking_warnings) for warning in warnings]



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



def _asset_url_check_fields(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("asset_url_check_fields"))



def _non_blocking_warning_prefixes(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("non_blocking_warning_prefixes"))



def _blocking_warning_prefixes(parser_profile: dict[str, Any]) -> list[str]:
    return _string_list(parser_profile.get("blocking_warning_prefixes"))



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


_PAGE_ID_RE = re.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|page[_-].+)$")


def _looks_like_relation_page_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_PAGE_ID_RE.match(value.strip()))


def _relation_resolution_warnings(
    normalized_record: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, Any],
    resolved_relation_values: set[tuple[str, Any]] | None = None,
) -> list[str]:
    warnings: list[str] = []
    for record_key, target_field in field_mapping.items():
        property_schema = schema.get(target_field)
        if not isinstance(property_schema, dict) or property_schema.get("type") != "relation":
            continue
        value = normalized_record.get(record_key)
        if value in (None, "", [], {}):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item in (None, "", [], {}):
                continue
            if resolved_relation_values and (record_key, item) in resolved_relation_values:
                continue
            if not _looks_like_relation_page_id(item):
                warnings.append(f"relation_resolution_pending:{record_key}:{item}")
    return warnings



def _relation_target_database_id(property_schema: dict[str, Any]) -> str | None:
    target_database_id = property_schema.get("target_database_id")
    if isinstance(target_database_id, str) and target_database_id:
        return target_database_id
    relation_schema = property_schema.get("relation")
    if isinstance(relation_schema, dict) and isinstance(relation_schema.get("database_id"), str):
        return relation_schema["database_id"]
    return None



def _data_source_id_for_database(structure: dict[str, Any], database_id: str) -> str | None:
    candidates: list[str] = []
    for data_source in structure.get("data_sources", {}).values():
        if isinstance(data_source, dict) and data_source.get("database_id") == database_id:
            data_source_id = data_source.get("data_source_id")
            if isinstance(data_source_id, str) and data_source_id:
                candidates.append(data_source_id)
    graph = structure.get("graph")
    graph_data_sources = graph.get("data_sources") if isinstance(graph, dict) else None
    if isinstance(graph_data_sources, dict):
        for data_source in graph_data_sources.values():
            if isinstance(data_source, dict) and data_source.get("database_id") == database_id:
                data_source_id = data_source.get("data_source_id")
                if isinstance(data_source_id, str) and data_source_id:
                    candidates.append(data_source_id)
    unique_candidates = list(dict.fromkeys(candidates))
    return unique_candidates[0] if len(unique_candidates) == 1 else None



def _relation_target_data_source_id(
    structure: dict[str, Any],
    target_field: str,
    property_schema: dict[str, Any],
) -> str | None:
    target_data_source_id = property_schema.get("target_data_source_id")
    if isinstance(target_data_source_id, str) and target_data_source_id:
        return target_data_source_id
    relation_schema = property_schema.get("relation")
    if isinstance(relation_schema, dict) and isinstance(relation_schema.get("data_source_id"), str):
        return relation_schema["data_source_id"]
    relations = structure.get("relations", [])
    if isinstance(relations, list):
        for relation in relations:
            if not isinstance(relation, dict) or relation.get("field") != target_field:
                continue
            target_data_source_id = relation.get("target_data_source_id")
            if isinstance(target_data_source_id, str) and target_data_source_id:
                return target_data_source_id
    target_database_id = _relation_target_database_id(property_schema)
    if isinstance(target_database_id, str) and target_database_id:
        return _data_source_id_for_database(structure, target_database_id)
    return None



def _relation_policy(structure: dict[str, Any], record_key: str, target_field: str) -> dict[str, Any]:
    relation_mapping = structure.get("relation_mapping")
    if not isinstance(relation_mapping, dict):
        return {}
    for key in (record_key, target_field):
        policy = relation_mapping.get(key)
        if isinstance(policy, dict):
            return policy
    return {}



def _relation_action_summaries(
    normalized_record: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, Any],
    structure: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for record_key, target_field in field_mapping.items():
        property_schema = schema.get(target_field)
        if not isinstance(property_schema, dict) or property_schema.get("type") != "relation":
            continue
        value = normalized_record.get(record_key)
        if value in (None, "", [], {}):
            continue
        values = value if isinstance(value, list) else [value]
        policy = _relation_policy(structure, record_key, target_field)
        create_missing = policy.get("create_missing") is True
        for item in values:
            if item in (None, "", [], {}) or _looks_like_relation_page_id(item):
                continue
            summaries.append(
                {
                    "record_key": record_key,
                    "target_field": target_field,
                    "value": item,
                    "action": "create_missing_relation_page" if create_missing else "resolve_relation_page",
                    "target_database_id": _relation_target_database_id(property_schema),
                    "target_data_source_id": _relation_target_data_source_id(structure, target_field, property_schema),
                    "page_id": None,
                    "page_id_status": "pending_after_apply" if create_missing else "pending_relation_resolution",
                }
            )
    return summaries


_COMPUTED_PROPERTY_TYPES = {"formula", "rollup", "created_time", "created_by", "last_edited_time", "last_edited_by", "unique_id"}
_ENRICHABLE_PROPERTY_TYPES = {"files", "rich_text", "url", "email", "phone_number", "number", "date"}
_CHOICE_PROPERTY_TYPES = {"select", "status", "multi_select", "relation", "people"}


def _title_field_name(schema: dict[str, Any]) -> str | None:
    for field_name, field_schema in schema.items():
        if isinstance(field_schema, dict) and field_schema.get("type") == "title":
            return str(field_name)
    return None


def _relation_target_field_status(property_type: str) -> str:
    if property_type in _COMPUTED_PROPERTY_TYPES:
        return "computed"
    if property_type in _CHOICE_PROPERTY_TYPES:
        return "needs_user_choice"
    if property_type in _ENRICHABLE_PROPERTY_TYPES:
        return "needs_enrichment"
    return "needs_enrichment"


def _relation_target_has_unresolved_fields(writable_fields: dict[str, dict[str, Any]]) -> bool:
    return any(
        field.get("write_status") in {"needs_enrichment", "needs_user_choice"}
        for field in writable_fields.values()
    )



def _relation_target_plans(
    relation_actions: list[dict[str, Any]],
    structure: dict[str, Any],
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for action in relation_actions:
        if action.get("action") != "create_missing_relation_page":
            continue
        target_data_source_id = action.get("target_data_source_id")
        if not isinstance(target_data_source_id, str) or not target_data_source_id:
            continue
        target_data_source = _data_source_by_id(structure, target_data_source_id)
        schema = target_data_source.get("schema") if isinstance(target_data_source, dict) else None
        if not isinstance(schema, dict):
            continue
        title_field = _title_field_name(schema)
        writable_fields: dict[str, dict[str, Any]] = {}
        omitted_fields: dict[str, dict[str, Any]] = {}
        if title_field:
            writable_fields["title"] = {"target_field": title_field, "value_status": "present", "write_status": "planned", "type": "title"}
        for field_name, field_schema in schema.items():
            if not isinstance(field_schema, dict) or field_name == title_field:
                continue
            property_type = field_schema.get("type")
            if not isinstance(property_type, str):
                continue
            status = _relation_target_field_status(property_type)
            if status == "computed":
                omitted_fields[str(field_name)] = {"type": property_type, "write_status": "computed"}
                continue
            writable_fields[str(field_name)] = {
                "target_field": str(field_name),
                "value_status": "missing_value",
                "write_status": status,
                "type": property_type,
            }
        plans.append(
            {
                "source_record_key": action.get("record_key"),
                "source_value": action.get("value"),
                "action": "create_page",
                "target_data_source": target_data_source.get("title") if isinstance(target_data_source, dict) else None,
                "target_data_source_id": target_data_source_id,
                "page_id": None,
                "page_id_status": "pending_after_apply",
                "writable_fields": writable_fields,
                "omitted_fields": omitted_fields,
                "shell_page_risk": _relation_target_has_unresolved_fields(writable_fields),
            }
        )
    return plans



def _requirement_decisions(capture: CaptureInput) -> list[dict[str, Any]]:
    decisions = capture.enrichment.get("requirement_decisions") if isinstance(capture.enrichment, dict) else None
    if not isinstance(decisions, list):
        return []
    return [decision for decision in decisions if isinstance(decision, dict)]



def _relation_resolution_requirement_facts(capture: CaptureInput) -> list[dict[str, Any]]:
    facts = capture.enrichment.get("relation_resolution_requirements") if isinstance(capture.enrichment, dict) else None
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict)]



def _people_resolution_requirement_facts(capture: CaptureInput) -> list[dict[str, Any]]:
    facts = capture.enrichment.get("people_resolution_requirements") if isinstance(capture.enrichment, dict) else None
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict)]



def _people_resolution_adapter(config: AppConfig) -> Any | None:
    try:
        return NotionAdapter.from_config(config)
    except Exception:
        return None



def _resolve_record_people_for_plan(
    normalized_record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
    capture: CaptureInput,
    config: AppConfig,
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    adapter = _people_resolution_adapter(config)
    if adapter is None:
        return normalized_record, [], []
    resolved_record, warnings, facts = resolve_record_people_with_facts(
        normalized_record,
        field_mapping,
        target_structure,
        adapter,
        decisions=_requirement_decisions(capture),
    )
    requirements = facts.get("people_resolution_requirements") if isinstance(facts, dict) else None
    return resolved_record, warnings, [fact for fact in requirements if isinstance(fact, dict)] if isinstance(requirements, list) else []



def _people_resolution_decision_key(decision: dict[str, Any]) -> Any:
    return decision.get("source_record_key") if decision.get("source_record_key") is not None else decision.get("record_key")



def _people_resolution_decision_matches(
    decision: dict[str, Any],
    *,
    record_key: str,
    source_value: Any,
    target_field: str | None = None,
) -> bool:
    if decision.get("target_type") is not None and decision.get("target_type") != "people_resolution":
        return False
    if _people_resolution_decision_key(decision) is not None and _people_resolution_decision_key(decision) != record_key:
        return False
    if decision.get("source_value") is not None and decision.get("source_value") != source_value:
        return False
    decision_field = decision.get("target_field") or decision.get("field")
    if target_field is not None and decision_field is not None and decision_field != target_field:
        return False
    return True



def _people_resolution_decision_user_id(decision: dict[str, Any]) -> str | None:
    for key in ("user_id", "id", "value"):
        value = decision.get(key)
        if isinstance(value, str) and value:
            return value
    candidate = decision.get("candidate")
    if isinstance(candidate, dict):
        value = candidate.get("user_id") or candidate.get("id")
        if isinstance(value, str) and value:
            return value
    return None



def _apply_people_resolution_decisions(normalized_record: dict[str, Any], capture: CaptureInput) -> list[str]:
    warnings: list[str] = []
    for decision in _requirement_decisions(capture):
        if decision.get("target_type") is not None and decision.get("target_type") != "people_resolution":
            continue
        record_key = _people_resolution_decision_key(decision)
        if not isinstance(record_key, str) or record_key not in normalized_record:
            continue
        action = decision.get("action")
        if action in {"choose_existing", "use_existing"}:
            user_id = _people_resolution_decision_user_id(decision)
            if user_id:
                normalized_record[record_key] = user_id
            else:
                warnings.append(f"people_decision_invalid:{record_key}:{normalized_record.get(record_key)}")
        elif action in {"skip", "confirm_skip", "confirmed_skip"}:
            source_value = decision.get("source_value", normalized_record.get(record_key))
            normalized_record[record_key] = None
            warnings.append(f"people_skipped:{record_key}:{source_value}")
    return warnings



def _people_resolution_decision_for_fact(fact: dict[str, Any], capture: CaptureInput) -> dict[str, Any] | None:
    record_key = fact.get("record_key") or fact.get("source_record_key")
    if not isinstance(record_key, str):
        return None
    source_value = fact.get("source_value")
    target_field = fact.get("target_field") if isinstance(fact.get("target_field"), str) else None
    for decision in _requirement_decisions(capture):
        if _people_resolution_decision_matches(
            decision,
            record_key=record_key,
            source_value=source_value,
            target_field=target_field,
        ):
            return decision
    return None



def _people_resolution_requirements(
    *,
    capture: CaptureInput,
    field_mapping: dict[str, str],
    schema: dict[str, Any],
    required_value_fields: list[str],
    people_requirement_facts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    required_keys = set(required_value_fields)
    seen_requirements: set[tuple[Any, Any, Any]] = set()
    for fact in [*_people_resolution_requirement_facts(capture), *(people_requirement_facts or [])]:
        record_key = fact.get("record_key") or fact.get("source_record_key")
        source_value = fact.get("source_value")
        if not isinstance(record_key, str) or source_value in (None, "", [], {}):
            continue
        target_field = fact.get("target_field") or field_mapping.get(record_key)
        if not isinstance(target_field, str):
            continue
        property_schema = schema.get(target_field)
        if not isinstance(property_schema, dict) or property_schema.get("type") != "people":
            continue
        decision = _people_resolution_decision_for_fact(fact, capture)
        if decision is not None and decision.get("action") in {"choose_existing", "use_existing", "skip", "confirm_skip", "confirmed_skip"}:
            continue
        requirement_key = (record_key, source_value, target_field)
        if requirement_key in seen_requirements:
            continue
        seen_requirements.add(requirement_key)
        is_required = record_key in required_keys
        requirements.append(
            {
                "target_type": "people_resolution",
                "source_record_key": record_key,
                "source_value": source_value,
                "target_field": target_field,
                "required": is_required,
                "blocking": is_required,
                "candidates": fact.get("candidates") if isinstance(fact.get("candidates"), list) else [],
            }
        )
    return requirements



def _relation_resolution_decision_key(decision: dict[str, Any]) -> Any:
    return decision.get("source_record_key") if decision.get("source_record_key") is not None else decision.get("record_key")



def _relation_resolution_decision_matches(
    decision: dict[str, Any],
    *,
    record_key: str,
    source_value: Any,
    target_field: str | None = None,
) -> bool:
    if decision.get("target_type") is not None and decision.get("target_type") != "relation_resolution":
        return False
    if _relation_resolution_decision_key(decision) is not None and _relation_resolution_decision_key(decision) != record_key:
        return False
    if decision.get("source_value") is not None and decision.get("source_value") != source_value:
        return False
    decision_field = decision.get("target_field") or decision.get("field")
    if target_field is not None and decision_field is not None and decision_field != target_field:
        return False
    return True



def _relation_resolution_decision_page_id(decision: dict[str, Any]) -> str | None:
    for key in ("page_id", "id", "value"):
        value = decision.get(key)
        if isinstance(value, str) and value:
            return value
    candidate = decision.get("candidate")
    if isinstance(candidate, dict):
        value = candidate.get("page_id") or candidate.get("id")
        if isinstance(value, str) and value:
            return value
    return None



def _apply_relation_resolution_decisions(normalized_record: dict[str, Any], capture: CaptureInput) -> list[str]:
    warnings: list[str] = []
    for decision in _requirement_decisions(capture):
        if decision.get("target_type") is not None and decision.get("target_type") != "relation_resolution":
            continue
        record_key = _relation_resolution_decision_key(decision)
        if not isinstance(record_key, str) or record_key not in normalized_record:
            continue
        action = decision.get("action")
        if action in {"choose_existing", "use_existing"}:
            page_id = _relation_resolution_decision_page_id(decision)
            if page_id:
                normalized_record[record_key] = page_id
            else:
                warnings.append(f"relation_decision_invalid:{record_key}:{normalized_record.get(record_key)}")
        elif action in {"skip", "confirm_skip", "confirmed_skip"}:
            source_value = decision.get("source_value", normalized_record.get(record_key))
            normalized_record[record_key] = None
            warnings.append(f"relation_skipped:{record_key}:{source_value}")
    return warnings



def _chosen_existing_relation_values(capture: CaptureInput) -> set[tuple[str, Any]]:
    values: set[tuple[str, Any]] = set()
    for decision in _requirement_decisions(capture):
        if decision.get("target_type") is not None and decision.get("target_type") != "relation_resolution":
            continue
        if decision.get("action") not in {"choose_existing", "use_existing"}:
            continue
        record_key = _relation_resolution_decision_key(decision)
        page_id = _relation_resolution_decision_page_id(decision)
        if isinstance(record_key, str) and page_id:
            values.add((record_key, page_id))
    return values



def _relation_resolution_decision_for_fact(fact: dict[str, Any], capture: CaptureInput) -> dict[str, Any] | None:
    record_key = fact.get("record_key") or fact.get("source_record_key")
    if not isinstance(record_key, str):
        return None
    source_value = fact.get("source_value")
    target_field = fact.get("target_field") if isinstance(fact.get("target_field"), str) else None
    for decision in _requirement_decisions(capture):
        if _relation_resolution_decision_matches(
            decision,
            record_key=record_key,
            source_value=source_value,
            target_field=target_field,
        ):
            return decision
    return None



def _relation_resolution_requirements(
    *,
    capture: CaptureInput,
    field_mapping: dict[str, str],
    schema: dict[str, Any],
    required_value_fields: list[str],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    required_keys = set(required_value_fields)
    for fact in _relation_resolution_requirement_facts(capture):
        record_key = fact.get("record_key") or fact.get("source_record_key")
        source_value = fact.get("source_value")
        if not isinstance(record_key, str) or source_value in (None, "", [], {}):
            continue
        target_field = fact.get("target_field") or field_mapping.get(record_key)
        if not isinstance(target_field, str):
            continue
        property_schema = schema.get(target_field)
        if not isinstance(property_schema, dict) or property_schema.get("type") != "relation":
            continue
        decision = _relation_resolution_decision_for_fact(fact, capture)
        if decision is not None and decision.get("action") in {"choose_existing", "use_existing", "skip", "confirm_skip", "confirmed_skip"}:
            continue
        is_required = record_key in required_keys
        requirement = {
            "target_type": "relation_resolution",
            "source_record_key": record_key,
            "source_value": source_value,
            "target_field": target_field,
            "target_database_id": fact.get("target_database_id") or _relation_target_database_id(property_schema),
            "target_data_source_id": fact.get("target_data_source_id") or _relation_target_data_source_id({}, target_field, property_schema),
            "required": is_required,
            "blocking": is_required,
            "candidates": fact.get("candidates") if isinstance(fact.get("candidates"), list) else [],
        }
        if requirement["target_data_source_id"] is None:
            requirement.pop("target_data_source_id")
        requirements.append(requirement)
    return requirements



def _decision_matches_relation_target_field(
    decision: dict[str, Any],
    relation_target_plan: dict[str, Any],
    field: str,
    field_manifest: dict[str, Any],
) -> bool:
    for key in ("source_record_key", "source_value", "target_data_source_id"):
        if decision.get(key) is not None and decision.get(key) != relation_target_plan.get(key):
            return False
    if decision.get("target_type") is not None and decision.get("target_type") != "relation_target_page":
        return False
    decision_field = decision.get("field") or decision.get("target_field")
    if decision_field not in {field, field_manifest.get("target_field")}:
        return False
    return True



def _relation_target_field_decision(
    decisions: list[dict[str, Any]],
    relation_target_plan: dict[str, Any],
    field: str,
    field_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    for decision in decisions:
        if _decision_matches_relation_target_field(decision, relation_target_plan, field, field_manifest):
            return decision
    return None



def _apply_requirement_decisions_to_relation_target_plans(
    relation_target_plans: list[dict[str, Any]],
    capture: CaptureInput,
) -> None:
    decisions = _requirement_decisions(capture)
    if not decisions:
        return
    for relation_target_plan in relation_target_plans:
        writable_fields = relation_target_plan.get("writable_fields")
        if not isinstance(writable_fields, dict):
            continue
        for field, field_manifest in writable_fields.items():
            if not isinstance(field, str) or not isinstance(field_manifest, dict):
                continue
            decision = _relation_target_field_decision(decisions, relation_target_plan, field, field_manifest)
            if decision is None:
                continue
            action = decision.get("action")
            if action in {"provide_value", "choose_value"} and decision.get("value") not in (None, "", [], {}):
                field_manifest["value_status"] = "present"
                field_manifest["write_status"] = "planned"
                field_manifest["value"] = decision["value"]
                field_manifest["value_source"] = "user_decision"
            elif action in {"skip", "confirm_skip", "confirmed_skip"}:
                field_manifest["value_status"] = "skipped"
                field_manifest["write_status"] = "skipped"
                field_manifest["skip_reason"] = str(decision.get("reason") or "user_confirmed_skip")
        relation_target_plan["shell_page_risk"] = _relation_target_has_unresolved_fields(writable_fields)



def _relation_target_completion_asset_operation(
    *,
    config: AppConfig,
    content_type: str,
    field: str,
    field_manifest: dict[str, Any],
    allow_download: bool,
) -> dict[str, Any] | None:
    value = field_manifest.get("value")
    target_field = field_manifest.get("target_field")
    if field_manifest.get("type") != "files" or not _is_url(value) or not isinstance(target_field, str):
        return None
    return {
        "type": "file",
        "source_url": value,
        "local_cache_path": _asset_cache_path(config, content_type, field, value) if allow_download else None,
        "target_field": target_field,
        "action": "download_and_attach" if allow_download else "attach_external_url",
        "record_key": field,
        "status": "planned",
        "warning": None,
    }



def _relation_target_completion_operations(
    *,
    relation_target_plans: list[dict[str, Any]],
    config: AppConfig,
    content_type: str,
    allow_download: bool,
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for relation_target_plan in relation_target_plans:
        source_record_key = relation_target_plan.get("source_record_key")
        target_data_source_id = relation_target_plan.get("target_data_source_id")
        writable_fields = relation_target_plan.get("writable_fields")
        if not isinstance(source_record_key, str) or not isinstance(target_data_source_id, str) or not isinstance(writable_fields, dict):
            continue
        field_mapping: dict[str, str] = {}
        record: dict[str, Any] = {}
        asset_operations: list[dict[str, Any]] = []
        for field, field_manifest in writable_fields.items():
            if field == "title" or not isinstance(field, str) or not isinstance(field_manifest, dict):
                continue
            target_field = field_manifest.get("target_field")
            if field_manifest.get("write_status") != "planned" or not isinstance(target_field, str):
                continue
            value = field_manifest.get("value")
            if value in (None, "", [], {}):
                continue
            field_mapping[field] = target_field
            record[field] = value
            asset_operation = _relation_target_completion_asset_operation(
                config=config,
                content_type=content_type,
                field=field,
                field_manifest=field_manifest,
                allow_download=allow_download,
            )
            if asset_operation is not None:
                asset_operations.append(asset_operation)
        if field_mapping:
            operations.append(
                {
                    "type": "complete_relation_page",
                    "source_record_key": source_record_key,
                    "target_data_source_id": target_data_source_id,
                    "field_mapping": field_mapping,
                    "record": record,
                    "asset_operations": asset_operations,
                }
            )
    return operations



def _completion_operation_merge_key(operation: dict[str, Any]) -> tuple[Any, Any, Any] | None:
    if operation.get("type") != "complete_relation_page":
        return None
    source_record_key = operation.get("source_record_key")
    target_data_source_id = operation.get("target_data_source_id")
    if not isinstance(source_record_key, str) or not isinstance(target_data_source_id, str):
        return None
    return (operation.get("type"), source_record_key, target_data_source_id)



def _completion_operations_can_merge(existing: dict[str, Any], operation: dict[str, Any]) -> bool:
    if _completion_operation_merge_key(existing) != _completion_operation_merge_key(operation):
        return False
    existing_field_mapping = existing.get("field_mapping") if isinstance(existing.get("field_mapping"), dict) else {}
    operation_field_mapping = operation.get("field_mapping") if isinstance(operation.get("field_mapping"), dict) else {}
    existing_record = existing.get("record") if isinstance(existing.get("record"), dict) else {}
    operation_record = operation.get("record") if isinstance(operation.get("record"), dict) else {}
    return all(existing_field_mapping.get(key, value) == value for key, value in operation_field_mapping.items()) and all(
        existing_record.get(key, value) == value for key, value in operation_record.items()
    )



def _asset_operation_key(operation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        operation.get("type"),
        operation.get("source_url"),
        operation.get("local_cache_path"),
        operation.get("target_field"),
        operation.get("action"),
        operation.get("record_key"),
    )



def _merge_completion_operation(existing: dict[str, Any], operation: dict[str, Any]) -> None:
    existing.setdefault("field_mapping", {}).update(operation.get("field_mapping") if isinstance(operation.get("field_mapping"), dict) else {})
    existing.setdefault("record", {}).update(operation.get("record") if isinstance(operation.get("record"), dict) else {})
    existing_assets = existing.setdefault("asset_operations", [])
    if not isinstance(existing_assets, list):
        return
    seen_asset_keys = {
        _asset_operation_key(asset)
        for asset in existing_assets
        if isinstance(asset, dict)
    }
    for asset in operation.get("asset_operations") or []:
        if not isinstance(asset, dict):
            continue
        asset_key = _asset_operation_key(asset)
        if asset_key in seen_asset_keys:
            continue
        existing_assets.append(asset)
        seen_asset_keys.add(asset_key)



def _merged_completion_operations(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for operation in operations:
        normalized = dict(operation)
        if isinstance(operation.get("field_mapping"), dict):
            normalized["field_mapping"] = dict(operation["field_mapping"])
        if isinstance(operation.get("record"), dict):
            normalized["record"] = dict(operation["record"])
        if isinstance(operation.get("asset_operations"), list):
            normalized["asset_operations"] = [dict(asset) if isinstance(asset, dict) else asset for asset in operation["asset_operations"]]
        for existing in merged:
            if _completion_operations_can_merge(existing, normalized):
                _merge_completion_operation(existing, normalized)
                break
        else:
            merged.append(normalized)
    return merged



def _completion_operations_with_ids(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **operation,
            "operation_id": operation.get("operation_id") if isinstance(operation.get("operation_id"), str) and operation.get("operation_id") else f"completion:{index}",
        }
        for index, operation in enumerate(operations)
    ]



def _asset_url_warnings(
    asset_operations: list[AssetOperation],
    check_fields: list[str],
) -> list[str]:
    checked = set(check_fields)
    if not checked:
        return []
    warnings: list[str] = []
    for operation in asset_operations:
        if operation.record_key not in checked or not operation.source_url or operation.action == "skip":
            continue
        try:
            result = verify_image_url(operation.source_url)
        except Exception:
            result = {"ok": False}
        if not result.get("ok"):
            warnings.append(f"asset_url_inaccessible:{operation.record_key}:{operation.source_url}")
    return warnings


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


def _target_path_fields(path_info: dict[str, Any]) -> dict[str, Any]:
    path = path_info.get("path") if isinstance(path_info, dict) else None
    if not isinstance(path, str) or not path:
        return {}
    return {
        "target_path": path,
        "target_path_complete": bool(path_info.get("path_complete")),
    }


def _visual_path_fields(path_info: dict[str, Any]) -> dict[str, Any]:
    path = path_info.get("path") if isinstance(path_info, dict) else None
    if not isinstance(path, str) or not path:
        return {}
    return {
        "visual_path": path,
        "visual_path_complete": bool(path_info.get("path_complete")),
    }


def _write_target_path_fields(graph: dict[str, Any] | None, operation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(graph, dict):
        return {}
    fields: dict[str, Any] = {}
    view_id = operation.get("view_id")
    if isinstance(view_id, str) and view_id:
        fields.update(_visual_path_fields(graph_visual_path(graph, view_id, "view")))
    data_source_id = operation.get("data_source_id")
    if isinstance(data_source_id, str) and data_source_id:
        fields.update(_target_path_fields(graph_object_path(graph, data_source_id, "data_source")))
        return fields
    parent_page_id = operation.get("parent_page_id")
    if isinstance(parent_page_id, str) and parent_page_id:
        fields.update(_target_path_fields(graph_object_path(graph, parent_page_id, "page")))
    return fields


def _primary_write_target(
    *,
    operation: dict[str, Any],
    title: Any,
    target_page: str | None,
    write_status: str,
    location_proof: dict[str, Any] | None = None,
    context_verification_source: str | None = None,
    template: dict[str, Any] | None = None,
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
    if template:
        target["template"] = dict(template)
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


def _write_target_requirement_kind(write_status: Any, property_type: Any) -> tuple[str, str] | None:
    if write_status == "needs_enrichment" and property_type in _ENRICHABLE_PROPERTY_TYPES:
        return ("enrichment", "web_search_or_user_input")
    if write_status == "needs_user_choice" and property_type in _CHOICE_PROPERTY_TYPES:
        return ("user_choice", "ask_user")
    return None


def _write_target_enrichment_requirements(write_targets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for write_target in write_targets or []:
        if not isinstance(write_target, dict):
            continue
        writable_fields = write_target.get("writable_fields")
        if not isinstance(writable_fields, dict):
            continue
        target_type = write_target.get("type")
        for field, field_manifest in writable_fields.items():
            if not isinstance(field_manifest, dict):
                continue
            property_type = field_manifest.get("type") or field_manifest.get("property_type")
            requirement_kind = _write_target_requirement_kind(field_manifest.get("write_status"), property_type)
            if requirement_kind is None:
                continue
            requirement_type, default_action = requirement_kind
            target_field = field_manifest.get("target_field")
            requirement = {
                "target_type": target_type,
                "target_role": target_type,
                "source_record_key": write_target.get("source_record_key"),
                "source_value": write_target.get("source_value"),
                "target_data_source_id": write_target.get("target_data_source_id") or write_target.get("data_source_id"),
                "field": str(field),
                "target_field": target_field if isinstance(target_field, str) and target_field else str(field),
                "property_type": property_type,
                "requirement_type": requirement_type,
                "allowed_sources": [default_action],
                "default_action": default_action,
                "blocking": True,
            }
            requirements.append(requirement)
    return requirements



def _write_targets(
    *,
    operations: list[dict[str, Any]],
    completion_operations: list[dict[str, Any]],
    relation_target_plans: list[dict[str, Any]] | None,
    normalized_record: dict[str, Any],
    target_page: str | None,
    structure: dict[str, Any],
    write_status: str,
    context_verification_source: str | None = None,
    graph: dict[str, Any] | None = None,
    template_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    target_structure = structure.get("target") if isinstance(structure.get("target"), dict) else {}
    targets = [
        _primary_write_target(
            operation=operation,
            title=normalized_record.get("title"),
            target_page=target_page,
            write_status=write_status,
            location_proof={
                **_location_proof_fields(_data_source_by_id(structure, operation.get("data_source_id")), target_structure),
                **_write_target_path_fields(graph, operation),
            },
            context_verification_source=context_verification_source,
            template=_write_target_template_summary(template_options),
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
    for relation_target_plan in relation_target_plans or []:
        target = {"type": "relation_target_page", **relation_target_plan}
        if write_status != "ready":
            target["write_status"] = write_status
        targets.append(target)
    return targets


def _data_source_by_id(structure: dict[str, Any], data_source_id: str) -> dict[str, Any] | None:
    for data_source in structure.get("data_sources", {}).values():
        if isinstance(data_source, dict) and data_source.get("data_source_id") == data_source_id:
            return data_source
    graph = structure.get("graph")
    graph_data_sources = graph.get("data_sources") if isinstance(graph, dict) else None
    if isinstance(graph_data_sources, dict):
        for data_source in graph_data_sources.values():
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
    relation_action_summaries: list[dict[str, Any]] | None = None,
    relation_target_plans: list[dict[str, Any]] | None = None,
    relation_resolution_requirements: list[dict[str, Any]] | None = None,
    people_resolution_requirements: list[dict[str, Any]] | None = None,
    write_targets: list[dict[str, Any]] | None = None,
    enrichment_requirements: list[dict[str, Any]] | None = None,
    unmapped_writable_fields: dict[str, dict[str, str]] | None = None,
    non_blocking_warning_prefixes: list[str] | None = None,
    blocking_warning_prefixes: list[str] | None = None,
    required_value_fields: list[str] | None = None,
    view_context: dict[str, Any] | None = None,
    template_options: dict[str, Any] | None = None,
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
        "warning_details": _warning_details(warnings, non_blocking_warning_prefixes, blocking_warning_prefixes),
    }
    if view_context:
        summary["view_context"] = dict(view_context)
    if template_options:
        summary["template_options"] = dict(template_options)
    if relation_completion_summaries:
        summary["relation_completions"] = relation_completion_summaries
    if relation_action_summaries:
        summary["relation_actions"] = relation_action_summaries
    if relation_target_plans:
        summary["relation_target_plans"] = relation_target_plans
    if relation_resolution_requirements:
        summary["relation_resolution_requirements"] = relation_resolution_requirements
    if people_resolution_requirements:
        summary["people_resolution_requirements"] = people_resolution_requirements
    if enrichment_requirements:
        summary["enrichment_requirements"] = enrichment_requirements
    if unmapped_writable_fields:
        summary["unmapped_writable_fields"] = dict(unmapped_writable_fields)
    if required_value_fields:
        summary["required_value_fields"] = list(required_value_fields)
    return summary



def _compact_target_semantics(target: Target) -> dict[str, Any]:
    target_kind = target.target_kind or ("data_source" if target.data_source_id else "page_parent")
    semantics = {
        "target_kind": target_kind,
        "page_title": target.page_title,
        "page_id": target.page_id,
        "data_source_id": target.data_source_id,
        "view_id": target.view_id,
        "view_name": target.view_name,
        "view_type": target.view_type,
        "parent_page_id": target.parent_page_id,
    }
    return {key: value for key, value in semantics.items() if value is not None}



def _compact_view_constraints(warnings: list[str], constraint_summary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    values = constraint_summary.get("values") if isinstance(constraint_summary, dict) else None
    if isinstance(values, dict):
        for field, value in values.items():
            if isinstance(field, str):
                constraints.append({"status": "enforced", "field": field, "value": value})
    for warning in warnings:
        parts = warning.split(":")
        if len(parts) >= 4 and parts[0] == "view_constraint_conflict":
            constraints.append({"status": "conflict", "field": parts[1], "expected": parts[2], "actual": ":".join(parts[3:])})
        elif len(parts) >= 2 and parts[0] == "view_constraint_unmapped_field":
            constraints.append({"status": "unmapped", "field": ":".join(parts[1:])})
        elif warning.startswith("view_constraint_"):
            constraints.append({"status": "unresolved", "code": warning})
    return constraints



def _compact_expected_fields(summary: dict[str, Any]) -> list[dict[str, Any]]:
    writable_fields = summary.get("writable_fields")
    if not isinstance(writable_fields, dict):
        return []
    fields: list[dict[str, Any]] = []
    for record_key, field_summary in writable_fields.items():
        if not isinstance(record_key, str) or not isinstance(field_summary, dict):
            continue
        fields.append({"record_key": record_key, **field_summary})
    return fields



def _compact_asset_field(asset: dict[str, Any]) -> str | None:
    for key in ("field", "record_key"):
        value = asset.get(key)
        if isinstance(value, str) and value:
            return value
    return None



def _compact_relation_target_expectation(write_target: dict[str, Any]) -> dict[str, Any]:
    planned_fields: list[str] = []
    enrichment_required_fields: list[str] = []
    user_choice_required_fields: list[str] = []
    skipped_fields: list[str] = []
    computed_fields: list[str] = []

    writable_fields = write_target.get("writable_fields")
    if isinstance(writable_fields, dict):
        for field, manifest in writable_fields.items():
            if not isinstance(field, str) or not isinstance(manifest, dict):
                continue
            write_status = manifest.get("write_status")
            if write_status == "planned":
                planned_fields.append(field)
            elif write_status == "needs_enrichment":
                enrichment_required_fields.append(field)
            elif write_status == "needs_user_choice":
                user_choice_required_fields.append(field)
            elif write_status == "skipped":
                skipped_fields.append(field)
            elif write_status == "computed":
                computed_fields.append(field)

    omitted_fields = write_target.get("omitted_fields")
    if isinstance(omitted_fields, dict):
        for field, manifest in omitted_fields.items():
            if not isinstance(field, str) or not isinstance(manifest, dict):
                continue
            if manifest.get("write_status") == "computed":
                computed_fields.append(field)

    expectation = {
        "target_type": "relation_target_page",
        "source_record_key": write_target.get("source_record_key"),
        "source_value": write_target.get("source_value"),
        "target_data_source_id": write_target.get("target_data_source_id"),
        "page_id_status": write_target.get("page_id_status"),
        "planned_fields": planned_fields,
        "computed_fields": computed_fields,
        "shell_page_risk": bool(write_target.get("shell_page_risk")),
    }
    if enrichment_required_fields:
        expectation["enrichment_required_fields"] = enrichment_required_fields
    if user_choice_required_fields:
        expectation["user_choice_required_fields"] = user_choice_required_fields
    if skipped_fields:
        expectation["skipped_fields"] = skipped_fields
    return expectation



def _view_visibility_expectation(summary: dict[str, Any]) -> dict[str, Any] | None:
    view_context = summary.get("view_context")
    if not isinstance(view_context, dict):
        return None
    constraints = view_context.get("constraints")
    if not isinstance(constraints, dict):
        return None
    return {
        "view_id": view_context.get("view_id"),
        "view_name": view_context.get("view_name"),
        "view_type": view_context.get("view_type"),
        "constraints": dict(constraints),
    }



def _compact_verification_targets(
    *,
    summary: dict[str, Any],
    primary_data_source_id: str | None,
    fields: list[str],
    relation_fields: list[str],
    asset_fields: list[str],
    page_cover: str | None,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    write_targets = summary.get("write_targets")
    if not isinstance(write_targets, list):
        return targets
    for write_target in write_targets:
        if not isinstance(write_target, dict):
            continue
        target_type = write_target.get("type")
        if target_type == "primary_page":
            page_id = write_target.get("page_id")
            page_id_status = write_target.get("page_id_status")
            if page_id_status is None:
                page_id_status = "known" if page_id else "pending_after_apply"
            target = {
                "target_type": "primary_page",
                "action": write_target.get("action"),
                "page_id": page_id,
                "page_id_status": page_id_status,
                "data_source_id": write_target.get("data_source_id") or primary_data_source_id,
                "fields": fields,
                "relations": relation_fields,
                "assets": asset_fields,
                "page_cover": page_cover,
                **({"template": dict(write_target["template"])} if isinstance(write_target.get("template"), dict) else {}),
            }
            view_visibility = _view_visibility_expectation(summary)
            if view_visibility is not None:
                target["view_visibility"] = view_visibility
            targets.append(target)
        elif target_type == "relation_target_page":
            targets.append(_compact_relation_target_expectation(write_target))
    return targets



def _compact_verification_expectations(plan: WritePlan) -> dict[str, Any]:
    summary = plan.summary if isinstance(plan.summary, dict) else {}
    relation_actions = summary.get("relation_actions")
    relation_fields = []
    if isinstance(relation_actions, list):
        for action in relation_actions:
            if not isinstance(action, dict):
                continue
            field = action.get("field") or action.get("record_key")
            if isinstance(field, str):
                relation_fields.append(field)

    asset_fields = []
    asset_actions = summary.get("asset_actions")
    if isinstance(asset_actions, list):
        for action in asset_actions:
            if isinstance(action, dict) and (field := _compact_asset_field(action)):
                asset_fields.append(field)

    page_cover = None
    for operation in plan.asset_operations:
        if operation.type == "cover_image" and operation.action != "skip" and operation.source_url:
            page_cover = operation.source_url
            break

    required_value_fields = summary.get("required_value_fields")
    fields = [field for field in required_value_fields if isinstance(field, str)] if isinstance(required_value_fields, list) else []
    return {
        "fields": fields,
        "relations": relation_fields,
        "assets": asset_fields,
        "page_cover": page_cover,
        "targets": _compact_verification_targets(
            summary=summary,
            primary_data_source_id=plan.target.data_source_id,
            fields=fields,
            relation_fields=relation_fields,
            asset_fields=asset_fields,
            page_cover=page_cover,
        ),
    }



def _first_write_target_path_fields(summary: dict[str, Any]) -> dict[str, Any]:
    write_targets = summary.get("write_targets")
    first_write_target = write_targets[0] if isinstance(write_targets, list) and write_targets and isinstance(write_targets[0], dict) else {}
    fields: dict[str, Any] = {}
    for path_key in ("target_path", "visual_path"):
        path = first_write_target.get(path_key)
        if isinstance(path, str) and path:
            fields[path_key] = path
            fields[f"{path_key}_complete"] = bool(first_write_target.get(f"{path_key}_complete"))
    return fields



def _compact_plan_review(plan: WritePlan, warning_sections: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    summary = plan.summary if isinstance(plan.summary, dict) else {}
    relation_actions = summary.get("relation_actions")
    relation_target_plans = summary.get("relation_target_plans")
    relation_resolution_requirements = summary.get("relation_resolution_requirements")
    people_resolution_requirements = summary.get("people_resolution_requirements")
    asset_actions = summary.get("asset_actions")
    target_semantics = _compact_target_semantics(plan.target)
    target_semantics.update(_first_write_target_path_fields(summary))
    enrichment_requirements = summary.get("enrichment_requirements")
    template_options = summary.get("template_options")
    view_context = summary.get("view_context") if isinstance(summary.get("view_context"), dict) else {}
    constraint_summary = view_context.get("constraints") if isinstance(view_context, dict) else None
    review = {
        "target_semantics": target_semantics,
        "view_constraints": _compact_view_constraints(plan.warnings, constraint_summary if isinstance(constraint_summary, dict) else None),
        "expected_fields": _compact_expected_fields(summary),
        "relation_actions": relation_actions if isinstance(relation_actions, list) else [],
        "relation_target_plans": relation_target_plans if isinstance(relation_target_plans, list) else [],
        "asset_actions": asset_actions if isinstance(asset_actions, list) else [],
        "blocking_warnings": warning_sections["blocking"],
        "verification_expectations": _compact_verification_expectations(plan),
    }
    if isinstance(relation_resolution_requirements, list) and relation_resolution_requirements:
        review["relation_resolution_requirements"] = relation_resolution_requirements
    if isinstance(people_resolution_requirements, list) and people_resolution_requirements:
        review["people_resolution_requirements"] = people_resolution_requirements
    if isinstance(enrichment_requirements, list):
        review["enrichment_requirements"] = enrichment_requirements
    if isinstance(template_options, dict):
        review["template_options"] = dict(template_options)
    return review



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
    if isinstance(plan.summary, dict):
        target.update(_first_write_target_path_fields(plan.summary))
    for optional_key in ("view_id", "view_name", "view_type", "target_kind", "parent_page_id"):
        if target.get(optional_key) is None:
            target.pop(optional_key)
    warning_sections = _cli_warning_sections(plan.summary, plan.warnings)
    return {
        "plan_id": plan.plan_id,
        "content_type": plan.content_type,
        "target": target,
        "summary": plan.summary,
        "review": _compact_plan_review(plan, warning_sections),
        "warnings": list(plan.warnings),
        "warning_sections": warning_sections,
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
        "state": normalize_state(state, states_config) if state is not None else None,
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


def _state_mapping_values(state_mapping: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(state_mapping, dict):
        return {}
    values = state_mapping.get("values")
    mapping = values if isinstance(values, dict) else state_mapping
    return {
        str(source): target
        for source, target in mapping.items()
        if isinstance(target, str)
    }


def _plan_state_value(
    value: str | None,
    *,
    fields: dict[str, str],
    schema: dict[str, Any],
    states_config: dict[str, Any] | None,
    state_mapping: dict[str, Any] | None = None,
) -> str | None:
    if value is None:
        return None
    state_field = fields.get("state")
    property_schema = schema.get(state_field) if isinstance(state_field, str) else None
    option_names = _state_option_names(property_schema)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in option_names:
            return stripped
    normalized = normalize_state(value, states_config)
    mapping = _state_mapping_values(state_mapping)
    mapped = mapping.get(normalized)
    if mapped is None and isinstance(value, str):
        mapped = mapping.get(value.strip())
    if isinstance(mapped, str) and (not option_names or mapped in option_names):
        return mapped
    return normalized



def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _view_for_resolution(structure: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any] | None:
    view_id = resolution.get("view_id")
    views = structure.get("views")
    if not isinstance(view_id, str) or not isinstance(views, dict):
        return None
    view = views.get(view_id)
    return view if isinstance(view, dict) else None


def _apply_view_constraints(
    normalized_record: dict[str, Any],
    fields: dict[str, str],
    data_source_schema: dict[str, Any],
    structure: dict[str, Any],
    resolution: dict[str, Any],
) -> ViewWriteConstraints:
    view = _view_for_resolution(structure, resolution)
    if view is None:
        return ViewWriteConstraints({}, [])
    constraints = derive_view_write_constraints(view, data_source_schema)
    warnings = list(constraints.warnings)
    field_to_record_key = {field_name: record_key for record_key, field_name in fields.items()}
    for field_name, value in constraints.values.items():
        record_key = field_to_record_key.get(field_name)
        if not record_key:
            warnings.append(f"view_constraint_unmapped_field:{field_name}")
            continue
        existing = normalized_record.get(record_key)
        if _is_present(existing) and existing != value:
            warnings.append(f"view_constraint_conflict:{field_name}:{value}:{existing}")
            continue
        normalized_record[record_key] = value
    return ViewWriteConstraints(dict(constraints.values), warnings)


def _merge_record_patch(record: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    for key, value in patch.items():
        if _is_present(value):
            merged[key] = value
    return merged


def _confirmed_enrichment_record_patch(capture: CaptureInput) -> dict[str, Any]:
    if capture.enrichment.get("confirmation_status") != "confirmed":
        return {}
    record_patch = capture.enrichment.get("record_patch")
    return record_patch if isinstance(record_patch, dict) else {}


def _enrichment_conflict_warnings(capture: CaptureInput) -> list[str]:
    if capture.enrichment.get("confirmation_status") == "confirmed":
        return []
    conflicts = capture.enrichment.get("conflicts")
    if not isinstance(conflicts, list):
        return []
    warnings: list[str] = []
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        field = conflict.get("field")
        if isinstance(field, str) and field:
            warnings.append(f"enrichment_conflict:{field}")
        else:
            warnings.append("enrichment_conflict")
    return warnings


def _normalized_record_for_capture(
    capture: CaptureInput,
    content_type: str,
    parser_profile: dict[str, Any],
    states_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_record = _profile_normalized_record(
        raw_input=capture.raw_input,
        state=capture.state,
        content_type=content_type,
        parser_profile=parser_profile,
        states_config=states_config,
    )
    normalized_record = _merge_record_patch(normalized_record, capture.structured_record)
    return _merge_record_patch(normalized_record, _confirmed_enrichment_record_patch(capture))


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
    body_blocks = capture.body_blocks or build_body_blocks(capture.raw_input, title=title)
    target_page = _v2_root_page_title(graph, parent_page_id)
    existing_page_id = capture.existing_page_id if isinstance(capture.existing_page_id, str) and capture.existing_page_id else None
    if existing_page_id:
        operation = {
            "operation_id": "append_page_content:0",
            "type": "append_page_content",
            "page_id": existing_page_id,
            "body_blocks": body_blocks,
        }
        write_target = {
            "type": "primary_page",
            "action": "append_page_content",
            "title": title,
            "target_page": target_page,
            "parent_page_id": parent_page_id,
            "target_kind": "existing_page",
            "page_id": existing_page_id,
            "page_id_status": "known",
            "context_verification_source": "v2_page_graph",
            **_target_path_fields(graph_object_path(graph, parent_page_id, "page")),
        }
        target_kind = "existing_page"
        target_page_id = existing_page_id
    else:
        operation = {
            "operation_id": "create_child_page:0",
            "type": "create_child_page",
            "parent_page_id": parent_page_id,
            "title": title,
            "body_blocks": body_blocks,
        }
        write_target = {
            "type": "primary_page",
            "action": "create_child_page",
            "title": title,
            "target_page": target_page,
            "parent_page_id": parent_page_id,
            "target_kind": "page_parent",
            "page_id": None,
            "page_id_status": "pending_after_apply",
            "context_verification_source": "v2_page_graph",
            **_target_path_fields(graph_object_path(graph, parent_page_id, "page")),
        }
        target_kind = "page_parent"
        target_page_id = parent_page_id
    normalized_record = {"title": title, "state": normalize_state(capture.state, states_config)}
    write_targets = [write_target]
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
            page_id=target_page_id,
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_id=graph_id,
            target_kind=target_kind,
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
    blocking_warning_prefixes = _blocking_warning_prefixes(parser_profile)
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
        state_mapping=structure.get("state_mapping") if isinstance(structure.get("state_mapping"), dict) else None,
    )
    relation_decision_warnings = _apply_relation_resolution_decisions(normalized_record, capture)
    people_decision_warnings = _apply_people_resolution_decisions(normalized_record, capture)
    view_constraints = _apply_view_constraints(
        normalized_record,
        fields,
        data_source_schema if isinstance(data_source_schema, dict) else {},
        structure,
        resolution,
    )
    view_constraint_warnings = list(view_constraints.warnings)
    enrichment_requirements = _summary_enrichment_requirements(normalized_record, parser_profile)
    enrichment_conflict_warnings = _enrichment_conflict_warnings(capture)
    cover_url = normalized_record.get("cover")

    confirmation_reason = structure.get("confirmation_reason")
    warnings = list(data_source.get("mapping_warnings") or [])
    template_options = _template_options_for_capture(capture, data_source)
    template_requires_confirmation = bool(template_options and template_options.get("apply_status") == "unsupported")
    if template_requires_confirmation:
        selected_template_id = template_options.get("selected_template_id")
        warning = f"template_apply_unsupported:{selected_template_id}" if selected_template_id else "template_apply_unsupported"
        if warning not in warnings:
            warnings.append(warning)
        if not confirmation_reason:
            confirmation_reason = "template_apply_unsupported"
    for warning in enrichment_conflict_warnings:
        if warning not in warnings:
            warnings.append(warning)
    for warning in relation_decision_warnings:
        if warning not in warnings:
            warnings.append(warning)
    for warning in people_decision_warnings:
        if warning not in warnings:
            warnings.append(warning)
    for warning in view_constraint_warnings:
        if warning not in warnings:
            warnings.append(warning)
    for warning in untrusted_mapping_warnings:
        if warning not in warnings:
            warnings.append(warning)
    blocking_mapping_warnings = confirmation_blocking_warnings(warnings, non_blocking_warning_prefixes)
    blocking_view_constraint_warnings = [warning for warning in view_constraint_warnings if warning.startswith("view_constraint_")]
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
    asset_operations = build_asset_operations(
        cache.config,
        content_type,
        normalized_record,
        asset_mapping,
        capture.options.allow_asset_download,
    )
    asset_url_warnings = _asset_url_warnings(asset_operations, _asset_url_check_fields(parser_profile))
    for warning in asset_url_warnings:
        if warning not in warnings:
            warnings.append(warning)
    if not confirmation_reason and enrichment_conflict_warnings:
        confirmation_reason = "enrichment_conflict"
    if not confirmation_reason and enrichment_requirements:
        confirmation_reason = "summary_content_source_missing"
    if not confirmation_reason and asset_url_warnings:
        confirmation_reason = "asset_url_inaccessible"
    if not confirmation_reason and untrusted_mapping_warnings:
        confirmation_reason = "untrusted_field_mapping"
    if not confirmation_reason and blocking_view_constraint_warnings:
        confirmation_reason = "view_constraint_unresolved"
    if not confirmation_reason and blocking_structure_mapping_warnings:
        confirmation_reason = "field_mapping_ambiguous"
    if not confirmation_reason and missing_fields:
        confirmation_reason = f"{content_type}_schema_incomplete"
    if not confirmation_reason and missing_values:
        confirmation_reason = f"{content_type}_key_values_missing"
    requires_confirmation = bool(
        structure_requires_confirmation
        or untrusted_mapping_warnings
        or blocking_view_constraint_warnings
        or blocking_structure_mapping_warnings
        or missing_fields
        or missing_values
        or enrichment_conflict_warnings
        or enrichment_requirements
        or asset_url_warnings
        or template_requires_confirmation
    )
    field_mapping = build_plan_field_mapping(
        normalized_record,
        trusted_fields,
        data_source.get("schema", {}),
        asset_mapping,
    )
    people_requirement_field_mapping = dict(field_mapping)
    people_resolution_warnings: list[str] = []
    people_requirement_facts: list[dict[str, Any]] = []
    if field_mapping:
        normalized_record, people_resolution_warnings, people_requirement_facts = _resolve_record_people_for_plan(
            normalized_record,
            field_mapping,
            structure,
            capture,
            cache.config,
        )
        field_mapping = build_plan_field_mapping(
            normalized_record,
            trusted_fields,
            data_source.get("schema", {}),
            asset_mapping,
        )
        people_requirement_field_mapping.update(field_mapping)
    for warning in people_resolution_warnings:
        if warning not in warnings:
            warnings.append(warning)
    blocking_people_resolution_warnings = [
        warning
        for warning in people_resolution_warnings
        if warning.startswith(("people_unresolved:", "people_query_failed:", "people_decision_invalid:"))
    ]
    if blocking_people_resolution_warnings:
        if not confirmation_reason:
            confirmation_reason = "people_resolution_required"
        requires_confirmation = True
    unmapped_writable_fields = _unmapped_writable_schema_fields(
        data_source_schema if isinstance(data_source_schema, dict) else {},
        fields if isinstance(fields, dict) else {},
        _ambiguous_mapping_candidate_fields(warnings),
    )
    relation_resolution_requirements = _relation_resolution_requirements(
        capture=capture,
        field_mapping=field_mapping,
        schema=data_source_schema if isinstance(data_source_schema, dict) else {},
        required_value_fields=required_value_fields,
    )
    for requirement in relation_resolution_requirements:
        if requirement.get("blocking") is not True:
            continue
        source_record_key = requirement.get("source_record_key")
        source_value = requirement.get("source_value")
        if isinstance(source_record_key, str) and source_value not in (None, "", [], {}):
            warning = f"relation_resolution_required:{source_record_key}:{source_value}"
            if warning not in warnings:
                warnings.append(warning)
            if not confirmation_reason:
                confirmation_reason = "relation_resolution_required"
            requires_confirmation = True
    people_resolution_requirements = _people_resolution_requirements(
        capture=capture,
        field_mapping=people_requirement_field_mapping,
        schema=data_source_schema if isinstance(data_source_schema, dict) else {},
        required_value_fields=required_value_fields,
        people_requirement_facts=people_requirement_facts,
    )
    for requirement in people_resolution_requirements:
        if requirement.get("blocking") is not True:
            continue
        source_record_key = requirement.get("source_record_key")
        source_value = requirement.get("source_value")
        if isinstance(source_record_key, str) and source_value not in (None, "", [], {}):
            warning = f"people_resolution_required:{source_record_key}:{source_value}"
            if warning not in warnings:
                warnings.append(warning)
            if not confirmation_reason:
                confirmation_reason = "people_resolution_required"
            requires_confirmation = True
    relation_actions = _relation_action_summaries(
        normalized_record,
        field_mapping,
        data_source_schema if isinstance(data_source_schema, dict) else {},
        structure,
    )
    relation_target_plans = _relation_target_plans(relation_actions, structure)
    _apply_requirement_decisions_to_relation_target_plans(relation_target_plans, capture)
    for relation_target_plan in relation_target_plans:
        if relation_target_plan.get("shell_page_risk") is not True:
            continue
        source_record_key = relation_target_plan.get("source_record_key")
        source_value = relation_target_plan.get("source_value")
        if isinstance(source_record_key, str) and source_value not in (None, "", [], {}):
            warning = f"relation_target_shell_page:{source_record_key}:{source_value}"
            if warning not in warnings:
                warnings.append(warning)
            if not confirmation_reason:
                confirmation_reason = "relation_target_shell_page"
            requires_confirmation = True
    for warning in _relation_resolution_warnings(
        normalized_record,
        field_mapping,
        data_source_schema if isinstance(data_source_schema, dict) else {},
        _chosen_existing_relation_values(capture),
    ):
        if warning not in warnings:
            warnings.append(warning)
    policy_blocking_warnings = _warnings_matching_prefixes(warnings, blocking_warning_prefixes)
    if not confirmation_reason and policy_blocking_warnings:
        confirmation_reason = "blocking_warning"
    requires_confirmation = bool(requires_confirmation or policy_blocking_warnings)
    completion_operations, completion_summaries = build_relation_completion_operations(
        config=cache.config,
        content_type=content_type,
        structure=structure,
        parser_profile=parser_profile,
        raw_input=capture.raw_input,
        normalized_record=normalized_record,
        allow_download=capture.options.allow_asset_download,
    )
    completion_operations.extend(
        _relation_target_completion_operations(
            relation_target_plans=relation_target_plans,
            config=cache.config,
            content_type=content_type,
            allow_download=capture.options.allow_asset_download,
        )
    )
    completion_operations = _completion_operations_with_ids(_merged_completion_operations(completion_operations))
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
    graph = cache.read_graph(resolution.get("target_id")) if isinstance(cache, CacheV2Store) and isinstance(resolution.get("target_id"), str) else None
    write_targets = _write_targets(
        operations=planned_operations,
        completion_operations=completion_operations,
        relation_target_plans=relation_target_plans,
        normalized_record=normalized_record,
        target_page=structure.get("target", {}).get("title"),
        structure=structure,
        write_status="requires_confirmation" if requires_confirmation else "ready",
        context_verification_source=resolution.get("context_verification_source") if isinstance(resolution.get("context_verification_source"), str) else None,
        graph=graph,
        template_options=template_options,
    )
    enrichment_requirements.extend(_write_target_enrichment_requirements(write_targets))
    view_context = {
        key: resolution[key]
        for key in ("view_id", "view_name", "view_type")
        if resolution.get(key) is not None
    }
    if view_constraints.values or view_constraints.warnings:
        view_context["constraints"] = view_constraints.to_dict()
    if not view_context:
        view_context = None

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
            relation_action_summaries=relation_actions,
            relation_target_plans=relation_target_plans,
            relation_resolution_requirements=relation_resolution_requirements,
            people_resolution_requirements=people_resolution_requirements,
            write_targets=write_targets,
            enrichment_requirements=enrichment_requirements,
            unmapped_writable_fields=unmapped_writable_fields,
            non_blocking_warning_prefixes=non_blocking_warning_prefixes,
            blocking_warning_prefixes=blocking_warning_prefixes,
            required_value_fields=required_value_fields,
            view_context=view_context,
            template_options=template_options,
        ),
        normalized_record=normalized_record,
        field_mapping=field_mapping,
        operations=operations,
        asset_operations=planned_asset_operations,
        sources=[*capture.sources, *([{"title": "cover", "url": cover_url}] if _is_url(cover_url) else [])],
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
