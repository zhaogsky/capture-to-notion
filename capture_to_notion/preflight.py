from __future__ import annotations

from typing import Any

from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.classifier import classify_content_type
from capture_to_notion.models import CaptureInput
from capture_to_notion.planner import _plain_page_capture_compatible
from capture_to_notion.structure_analyzer import analyze_target_structure
from capture_to_notion.target_resolver import resolve_capture_target


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
        "workflow": {},
    }



def _has_external_url(capture: CaptureInput) -> bool:
    text = capture.raw_input.casefold()
    return capture.input_shape_hint == "external_url" or "http://" in text or "https://" in text



def _append_input_shape_actions(preflight: dict[str, Any], capture: CaptureInput) -> None:
    if not _has_external_url(capture):
        return
    preflight["safe_actions"].append(_action("ask_before_parse_url", "external_url_input"))
    preflight["blocked_actions"].append(_action("parse_url_directly", "recommendation_required"))


def _target_type_from_resolution(resolution: dict[str, Any]) -> str:
    if resolution.get("existing_page_id"):
        return "existing_page"
    if resolution.get("data_source_id"):
        return "data_source"
    if resolution.get("page_id") or resolution.get("target_id"):
        return "target_container"
    return "unknown"


def _workflow_target_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    workflow_target = {
        "status": resolution.get("status"),
        "source": resolution.get("source"),
        "target_type": _target_type_from_resolution(resolution),
        "alias": resolution.get("alias"),
        "page_id": resolution.get("page_id"),
        "parent_page_id": resolution.get("parent_page_id"),
        "target_id": resolution.get("target_id"),
        "data_source_id": resolution.get("data_source_id"),
        "view_id": resolution.get("view_id"),
        "view_name": resolution.get("view_name"),
        "view_type": resolution.get("view_type"),
        "parent_data_source_id": resolution.get("parent_data_source_id"),
        "database_id": resolution.get("database_id"),
        "parent_database_id": resolution.get("parent_database_id"),
        "existing_page_id": resolution.get("existing_page_id"),
        "target_context_hint": resolution.get("target_context_hint"),
        "target_scope_hint": resolution.get("target_scope_hint"),
        "target_context_verified": resolution.get("target_context_verified"),
        "context_verification_source": resolution.get("context_verification_source"),
        "target_path": resolution.get("target_path"),
        "target_path_complete": resolution.get("target_path_complete"),
        "visual_path": resolution.get("visual_path"),
        "visual_path_complete": resolution.get("visual_path_complete"),
        "cache_completeness": resolution.get("cache_completeness"),
        "cache_consistency": resolution.get("cache_consistency"),
        "sync": resolution.get("sync"),
    }
    if "candidates" in resolution:
        workflow_target["candidates"] = resolution.get("candidates")
    return {key: value for key, value in workflow_target.items() if value is not None}


def _identity_enrichment(capture: CaptureInput, resolution: dict[str, Any]) -> dict[str, str]:
    target_resolved = resolution.get("status") not in {
        "target_missing",
        "target_not_resolved",
        "ambiguous_target",
        "v2_target_missing",
        "write_profile_missing",
    }
    if _has_external_url(capture) and not target_resolved:
        return {"status": "recommended", "reason": "external_url_input"}
    if target_resolved:
        return {"status": "skipped", "reason": "target_already_resolved"}
    return {"status": "skipped", "reason": "not_requested_or_not_needed"}


def _set_workflow_resolution(preflight: dict[str, Any], capture: CaptureInput, resolution: dict[str, Any]) -> None:
    preflight["workflow"] = {
        "intent_routing": {
            "status": "routed",
            "content_type": preflight.get("content_type"),
            "intent_hint": capture.intent_hint,
            "input_shape_hint": capture.input_shape_hint,
            "user_requested_action": capture.user_requested_action,
        },
        "identity_enrichment": _identity_enrichment(capture, resolution),
        "target_resolution": _workflow_target_resolution(resolution),
        "planning": {"status": "pending", "reason": "not_evaluated"},
    }


def _set_planning(preflight: dict[str, Any], status: str, next_action: str, reason: str) -> None:
    preflight.setdefault("workflow", {})["planning"] = {
        "status": status,
        "next_action": next_action,
        "reason": reason,
    }


def _is_workflow_confirmed(capture: CaptureInput, confirmation: str) -> bool:
    return confirmation in capture.workflow_confirmations


def _is_page_parent_scope(scope_hint: str | None) -> bool:
    if scope_hint in {"page_parent", "existing_page"}:
        return True
    scope = scope_hint.casefold() if isinstance(scope_hint, str) else ""
    if "page" not in scope:
        return False
    return not any(token in scope for token in ("data", "database", "view", "list", "table", "gallery", "board"))



def _is_page_parent_direct_write(capture: CaptureInput) -> bool:
    return (
        _is_page_parent_scope(capture.target_scope_hint)
        and capture.intent_hint == "direct_write"
        and capture.user_requested_action == "write"
    )


def _v2_page_parent_ready_target(
    capture: CaptureInput,
    cache: CacheStore | CacheV2Store,
    resolution: dict[str, Any],
    content_type: str,
) -> dict[str, str] | None:
    if not isinstance(cache, CacheV2Store) or not _is_page_parent_direct_write(capture):
        return None
    if not _plain_page_capture_compatible(capture, content_type):
        return None
    if resolution.get("status") != "write_profile_missing":
        return None
    if resolution.get("target_context_verified") is False:
        return None

    alias_name = resolution.get("alias") if isinstance(resolution.get("alias"), str) else capture.target_hint
    alias = cache.find_alias(alias_name)
    if not isinstance(alias, dict) or alias.get("kind") != "graph":
        return None
    graph_id = alias.get("graph_id")
    graph = cache.read_graph(graph_id) if isinstance(graph_id, str) else None
    if not isinstance(graph, dict):
        return None
    root = graph.get("root") if isinstance(graph.get("root"), dict) else {}
    data_sources = graph.get("data_sources") if isinstance(graph.get("data_sources"), dict) else {}
    page_id = root.get("id") if root.get("kind") == "page" else None
    if not isinstance(page_id, str) or data_sources:
        return None
    return {
        "alias": alias_name,
        "target_id": graph_id,
        "page_id": page_id,
        "target_context_verified": resolution.get("target_context_verified", True),
        "context_verification_source": resolution.get("context_verification_source", "v2_page_graph"),
        "target_path": resolution.get("target_path"),
        "target_path_complete": resolution.get("target_path_complete"),
    }


def _target_from_resolution(resolution: dict[str, Any], hint: str | None = None) -> dict[str, Any]:
    target = {
        "hint": hint,
        "status": resolution.get("status"),
        "page_id": resolution.get("page_id"),
        "parent_page_id": resolution.get("parent_page_id"),
        "target_id": resolution.get("target_id"),
        "data_source_id": resolution.get("data_source_id"),
        "view_id": resolution.get("view_id"),
        "view_name": resolution.get("view_name"),
        "view_type": resolution.get("view_type"),
        "parent_data_source_id": resolution.get("parent_data_source_id"),
        "database_id": resolution.get("database_id"),
        "parent_database_id": resolution.get("parent_database_id"),
        "existing_page_id": resolution.get("existing_page_id"),
        "target_context_hint": resolution.get("target_context_hint"),
        "target_scope_hint": resolution.get("target_scope_hint"),
        "target_context_verified": resolution.get("target_context_verified"),
        "context_verification_source": resolution.get("context_verification_source"),
        "target_path": resolution.get("target_path"),
        "target_path_complete": resolution.get("target_path_complete"),
        "visual_path": resolution.get("visual_path"),
        "visual_path_complete": resolution.get("visual_path_complete"),
        "cache_completeness": resolution.get("cache_completeness"),
        "cache_consistency": resolution.get("cache_consistency"),
        "sync": resolution.get("sync"),
    }
    if resolution.get("status") != "target_not_resolved":
        target["source"] = resolution.get("source")
        target["alias"] = resolution.get("alias")
    return {key: value for key, value in target.items() if value is not None}


def _has_stale_schema(structure: dict[str, Any]) -> bool:
    data_sources = structure.get("data_sources")
    if not isinstance(data_sources, dict):
        return False
    return any(
        isinstance(data_source, dict) and data_source.get("schema_status") == "stale"
        for data_source in data_sources.values()
    )


def _preflight_target_semantics(target: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "target_kind",
        "page_id",
        "data_source_id",
        "view_id",
        "view_name",
        "view_type",
        "parent_page_id",
        "target_path",
        "target_path_complete",
        "visual_path",
        "visual_path_complete",
    )
    return {key: target[key] for key in keys if target.get(key) is not None}



def _preflight_review(preflight: dict[str, Any], planning: dict[str, Any]) -> dict[str, Any]:
    target = preflight.get("target")
    target = target if isinstance(target, dict) else {}
    return {
        "target_semantics": _preflight_target_semantics(target),
        "safe_actions": list(preflight.get("safe_actions") or []),
        "blocked_actions": list(preflight.get("blocked_actions") or []),
        "next_action": planning.get("next_action"),
        "next_action_reason": planning.get("reason"),
    }



def build_capture_preflight_summary(preflight: dict[str, Any]) -> dict[str, Any]:
    structure = preflight.get("structure")
    structure_summary = None
    if isinstance(structure, dict):
        structure_summary = {
            key: structure[key]
            for key in ("risk_flags", "recommendations", "structure_complexity")
            if key in structure
        }
    workflow = preflight.get("workflow", {})
    planning = workflow.get("planning") if isinstance(workflow, dict) else {}
    planning = planning if isinstance(planning, dict) else {}

    return {
        "content_type": preflight.get("content_type"),
        "intent_hint": preflight.get("intent_hint"),
        "input_shape_hint": preflight.get("input_shape_hint"),
        "target_context_hint": preflight.get("target_context_hint"),
        "target_scope_hint": preflight.get("target_scope_hint"),
        "user_requested_action": preflight.get("user_requested_action"),
        "target": preflight.get("target", {}),
        "structure": structure_summary,
        "review": _preflight_review(preflight, planning),
        "safe_actions": list(preflight.get("safe_actions") or []),
        "blocked_actions": list(preflight.get("blocked_actions") or []),
        "confirmation_needed": list(preflight.get("confirmation_needed") or []),
        "workflow": workflow,
        "next_action": planning.get("next_action"),
        "next_action_reason": planning.get("reason"),
    }



def build_capture_preflight(capture: CaptureInput, cache: CacheStore | CacheV2Store) -> dict[str, Any]:
    preflight = _base_preflight(capture)
    _append_input_shape_actions(preflight, capture)

    resolution = resolve_capture_target(capture, cache, preflight["content_type"])
    _set_workflow_resolution(preflight, capture, resolution)
    preflight["target"] = _target_from_resolution(resolution, capture.target_hint)
    status = resolution.get("status")

    page_parent_target = _v2_page_parent_ready_target(capture, cache, resolution, preflight["content_type"])
    if page_parent_target is not None:
        ready_target = {**page_parent_target, "status": "v2_page_parent_ready", "source": "v2_alias"}
        preflight["target"].update(ready_target)
        preflight["workflow"].setdefault("target_resolution", {}).update(ready_target)
        preflight["safe_actions"].append(_action("capture_plan", "v2_page_parent_ready"))
        _set_planning(preflight, "allowed", "capture_plan", "v2_page_parent_ready")
        return preflight

    if status == "target_missing":
        preflight["target"] = _target_from_resolution(resolution)
        preflight["safe_actions"].append(_action("suggest_target", "target_missing"))
        preflight["blocked_actions"].append(_action("plan_directly", "target_missing"))
        _set_planning(preflight, "missing", "suggest_target", "target_missing")
        return preflight

    if status in {"v2_target_missing", "write_profile_missing"}:
        preflight["safe_actions"].append(_action("scan_target", status))
        preflight["blocked_actions"].append(_action("plan_directly", status))
        _set_planning(preflight, "structure_missing", "scan_target", status)
        return preflight

    if status in {"target_not_resolved", "ambiguous_target"}:
        reason = "ambiguous_target" if status == "ambiguous_target" else "target_not_resolved"
        preflight["safe_actions"].append(_action("suggest_target", reason))
        preflight["blocked_actions"].append(_action("plan_directly", reason))
        if status == "ambiguous_target":
            preflight["confirmation_needed"].append("ambiguous_target")
            _set_planning(preflight, "ambiguous", "choose_target", reason)
        else:
            _set_planning(preflight, "unresolved", "suggest_target", reason)
        return preflight

    if status == "target_context_cache_incomplete":
        reason = "cache_location_facts_missing"
        preflight["safe_actions"].append(_action("sync_target_cache", reason))
        preflight["blocked_actions"].append(_action("plan_directly", reason))
        _set_planning(preflight, "blocked", "sync_target_cache", reason)
        return preflight

    if status == "target_cache_stale":
        reason = "cache_page_title_mismatch"
        preflight["safe_actions"].append(_action("sync_target_cache", reason))
        preflight["blocked_actions"].append(_action("plan_directly", reason))
        _set_planning(preflight, "blocked", "sync_target_cache", reason)
        return preflight

    if status in {"target_context_unverified", "target_context_mismatch"}:
        reason = status
        preflight["safe_actions"].append(_action("scan_target", reason))
        preflight["blocked_actions"].append(_action("plan_directly", reason))
        _set_planning(preflight, "blocked", "scan_target", reason)
        return preflight

    target_id = resolution.get("target_id")
    structure = resolution.get("structure")
    if not isinstance(target_id, str) or not target_id or not isinstance(structure, dict) or not structure:
        preflight["safe_actions"].append(_action("scan_target", "target_structure_missing"))
        preflight["blocked_actions"].append(_action("plan_directly", "target_structure_missing"))
        _set_planning(preflight, "structure_missing", "scan_target", "target_structure_missing")
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
        _set_planning(preflight, "schema_stale", "scan_target", "schema_stale")
        return preflight

    preflight["target"]["status"] = "cache_hit"

    if has_risk_flags and not _is_workflow_confirmed(capture, "risky_target"):
        preflight["target"]["status"] = "risky_target"
        preflight["safe_actions"].append(_action("confirm_risky_target", "risky_target"))
        preflight["blocked_actions"].append(_action("plan_directly", "risky_target_requires_confirmation"))
        preflight["confirmation_needed"].append("risky_target")
        _set_planning(preflight, "risky", "confirm_risky_target", "risky_target_requires_confirmation")
        return preflight

    if has_risk_flags:
        preflight["target"]["status"] = "cache_hit"
        preflight["safe_actions"].append(_action("plan_directly", "risky_target_confirmed"))
        _set_planning(preflight, "allowed", "capture_plan", "risky_target_confirmed")
        return preflight

    preflight["safe_actions"].append(_action("plan_directly", "direct_plan_allowed"))
    preflight["blocked_actions"].append(_action("apply_directly", "plan_required"))
    _set_planning(preflight, "allowed", "capture_plan", "direct_plan_allowed")
    return preflight
