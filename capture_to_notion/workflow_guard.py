from __future__ import annotations

from typing import Any


CAPTURE_PLAN_ACTION = "capture_plan"
SYNC_TARGET_CACHE_ACTION = "sync_target_cache"


def planning_from_workflow(workflow: dict[str, Any] | None) -> dict[str, Any]:
    planning = workflow.get("planning") if isinstance(workflow, dict) else None
    return planning if isinstance(planning, dict) else {}


def preflight_next_action(preflight: dict[str, Any]) -> str | None:
    next_action = planning_from_workflow(preflight.get("workflow")).get("next_action")
    return next_action if isinstance(next_action, str) else None


def preflight_reason(preflight: dict[str, Any]) -> str | None:
    reason = planning_from_workflow(preflight.get("workflow")).get("reason")
    return reason if isinstance(reason, str) else None


def target_resolution_from_preflight(preflight: dict[str, Any]) -> dict[str, Any]:
    workflow = preflight.get("workflow")
    target_resolution = workflow.get("target_resolution") if isinstance(workflow, dict) else None
    return target_resolution if isinstance(target_resolution, dict) else {}


def scoped_sync_request(preflight: dict[str, Any]) -> dict[str, Any] | None:
    if preflight_next_action(preflight) != SYNC_TARGET_CACHE_ACTION:
        return None
    return assert_scoped_sync_request(preflight)


def assert_scoped_sync_request(preflight: dict[str, Any]) -> dict[str, Any]:
    sync = target_resolution_from_preflight(preflight).get("sync")
    if not isinstance(sync, dict):
        raise ValueError("next_action=sync_target_cache requires workflow.target_resolution.sync")
    data_source_id = sync.get("data_source_id")
    page_id = sync.get("page_id")
    has_data_source = isinstance(data_source_id, str) and bool(data_source_id)
    has_page = isinstance(page_id, str) and bool(page_id)
    if has_data_source == has_page:
        raise ValueError("next_action=sync_target_cache requires exactly one explicit data_source_id or page_id")
    return sync


def assert_preflight_allows_plan(preflight: dict[str, Any]) -> None:
    next_action = preflight_next_action(preflight)
    if next_action == CAPTURE_PLAN_ACTION:
        return
    reason = preflight_reason(preflight) or "not_allowed"
    raise ValueError(f"capture plan is blocked by preflight: next_action={next_action or 'missing'}, reason={reason}")


def assert_plan_workflow_allows_apply(workflow: dict[str, Any] | None) -> None:
    next_action = planning_from_workflow(workflow).get("next_action")
    if next_action == CAPTURE_PLAN_ACTION:
        return
    reason = planning_from_workflow(workflow).get("reason") or "not_allowed"
    raise ValueError(f"capture apply is blocked by plan workflow: next_action={next_action or 'missing'}, reason={reason}")
