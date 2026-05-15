from __future__ import annotations

from typing import Any

from capture_to_notion.cache import CacheStore
from capture_to_notion.classifier import classify_content_type
from capture_to_notion.models import CaptureInput
from capture_to_notion.structure_analyzer import analyze_target_structure


def _action(action: str, reason: str) -> dict[str, str]:
    return {"action": action, "reason": reason}


def _base_preflight(capture: CaptureInput) -> dict[str, Any]:
    return {
        "content_type": classify_content_type(capture),
        "intent_hint": capture.intent_hint,
        "input_shape_hint": capture.input_shape_hint,
        "target_context_hint": capture.target_context_hint,
        "target_scope_hint": capture.target_scope_hint,
        "user_requested_action": capture.user_requested_action,
        "target": {},
        "structure": None,
        "safe_actions": [],
        "blocked_actions": [],
        "confirmation_needed": [],
    }



def _has_external_url(capture: CaptureInput) -> bool:
    text = capture.raw_input.casefold()
    return capture.input_shape_hint == "external_url" or "http://" in text or "https://" in text



def _append_input_shape_actions(preflight: dict[str, Any], capture: CaptureInput) -> None:
    if not _has_external_url(capture):
        return
    preflight["safe_actions"].append(_action("ask_before_parse_url", "external_url_input"))
    preflight["blocked_actions"].append(_action("parse_url_directly", "recommendation_required"))


def _target_from_alias(hint: str, alias: dict[str, Any]) -> dict[str, Any]:
    target = {
        "hint": hint,
        "status": "cache_missing",
        "page_id": alias.get("page_id"),
        "target_id": alias.get("target_id"),
    }
    return {key: value for key, value in target.items() if value is not None}


def _has_stale_schema(structure: dict[str, Any]) -> bool:
    data_sources = structure.get("data_sources")
    if not isinstance(data_sources, dict):
        return False
    return any(
        isinstance(data_source, dict) and data_source.get("schema_status") == "stale"
        for data_source in data_sources.values()
    )


def build_capture_preflight_summary(preflight: dict[str, Any]) -> dict[str, Any]:
    structure = preflight.get("structure")
    structure_summary = None
    if isinstance(structure, dict):
        structure_summary = {
            key: structure[key]
            for key in ("risk_flags", "recommendations", "structure_complexity")
            if key in structure
        }

    return {
        "content_type": preflight.get("content_type"),
        "intent_hint": preflight.get("intent_hint"),
        "input_shape_hint": preflight.get("input_shape_hint"),
        "target_context_hint": preflight.get("target_context_hint"),
        "target_scope_hint": preflight.get("target_scope_hint"),
        "user_requested_action": preflight.get("user_requested_action"),
        "target": preflight.get("target", {}),
        "structure": structure_summary,
        "safe_actions": list(preflight.get("safe_actions") or []),
        "blocked_actions": list(preflight.get("blocked_actions") or []),
        "confirmation_needed": list(preflight.get("confirmation_needed") or []),
    }



def build_capture_preflight(capture: CaptureInput, cache: CacheStore) -> dict[str, Any]:
    preflight = _base_preflight(capture)
    _append_input_shape_actions(preflight, capture)

    if not capture.target_hint:
        preflight["target"] = {"status": "target_missing"}
        preflight["safe_actions"].append(_action("suggest_target", "target_missing"))
        preflight["blocked_actions"].append(_action("plan_directly", "target_missing"))
        return preflight

    alias = cache.find_alias(capture.target_hint)
    if not isinstance(alias, dict):
        preflight["target"] = {"hint": capture.target_hint, "status": "target_not_resolved"}
        preflight["safe_actions"].append(_action("suggest_target", "target_not_resolved"))
        preflight["blocked_actions"].append(_action("plan_directly", "target_not_resolved"))
        return preflight

    target_id = alias.get("target_id")
    preflight["target"] = _target_from_alias(capture.target_hint, alias)
    if not isinstance(target_id, str) or not target_id:
        preflight["safe_actions"].append(_action("scan_target", "target_structure_missing"))
        preflight["blocked_actions"].append(_action("plan_directly", "target_structure_missing"))
        return preflight

    structure = cache.target_structure(target_id)
    if not structure:
        preflight["safe_actions"].append(_action("scan_target", "target_structure_missing"))
        preflight["blocked_actions"].append(_action("plan_directly", "target_structure_missing"))
        return preflight

    analysis = analyze_target_structure(structure)
    preflight["structure"] = analysis

    has_stale_schema = _has_stale_schema(structure)
    has_risk_flags = bool(analysis.get("risk_flags"))

    if has_stale_schema:
        preflight["target"]["status"] = "schema_stale"
        preflight["safe_actions"].append(_action("scan_target", "schema_stale"))
        preflight["blocked_actions"].append(_action("plan_directly", "schema_stale"))
        preflight["confirmation_needed"].append("schema_stale")
        if has_risk_flags:
            preflight["safe_actions"].append(_action("confirm_risky_target", "risky_target"))
            preflight["blocked_actions"].append(_action("plan_directly", "risky_target_requires_confirmation"))
            preflight["confirmation_needed"].append("risky_target")
        return preflight

    preflight["target"]["status"] = "cache_hit"

    if has_risk_flags:
        preflight["target"]["status"] = "risky_target"
        preflight["safe_actions"].append(_action("confirm_risky_target", "risky_target"))
        preflight["blocked_actions"].append(_action("plan_directly", "risky_target_requires_confirmation"))
        preflight["confirmation_needed"].append("risky_target")
        return preflight

    preflight["safe_actions"].append(_action("plan_directly", "direct_plan_allowed"))
    preflight["blocked_actions"].append(_action("apply_directly", "plan_required"))
    return preflight
