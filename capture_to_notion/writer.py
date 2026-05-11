from __future__ import annotations

from typing import Any, Callable

from capture_to_notion.assets import execute_asset_operations
from capture_to_notion.models import WritePlan
from capture_to_notion.relations import resolve_record_relations
from capture_to_notion.schema import build_properties


class NotionWriterError(Exception):
    pass


def _data_source_schema(target_structure: dict[str, Any], data_source_id: str) -> dict[str, dict[str, Any]]:
    for data_source in target_structure.get("data_sources", {}).values():
        if data_source.get("data_source_id") == data_source_id:
            schema = data_source.get("schema", {})
            if isinstance(schema, dict):
                return schema
            break
    raise NotionWriterError(f"Target schema not found for data_source_id: {data_source_id}")


def _plan_schema(plan: WritePlan, target_structure: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data_source_id = plan.target.data_source_id
    if not data_source_id:
        raise NotionWriterError("Plan target is missing data_source_id")
    return _data_source_schema(target_structure, data_source_id)


def _build_record_properties(
    record: dict[str, Any],
    plan: WritePlan,
    schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return build_properties(record, plan.field_mapping, schema)


def _has_writable_asset_property(plan: WritePlan, schema: dict[str, dict[str, Any]]) -> bool:
    for operation in plan.asset_operations:
        if operation.action == "skip" or not operation.source_url:
            continue
        field_name = plan.field_mapping.get(operation.record_key)
        if field_name and schema.get(field_name, {}).get("type") == "files":
            return True
    return False


def build_plan_properties(plan: WritePlan, target_structure: dict[str, Any]) -> dict[str, Any]:
    return _build_record_properties(plan.normalized_record, plan, _plan_schema(plan, target_structure))


def _validate_write_operations(plan: WritePlan) -> None:
    for operation in plan.operations:
        operation_type = operation.get("type")
        if operation_type != "create_or_update_page":
            raise NotionWriterError(f"Unsupported write operation type: {operation_type}")

        operation_data_source_id = operation.get("data_source_id")
        if operation_data_source_id != plan.target.data_source_id:
            raise NotionWriterError(
                "Operation data_source_id does not match plan target data_source_id"
            )


def apply_write_plan(
    plan: WritePlan,
    target_structure: dict[str, Any],
    adapter: Any,
    downloader: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    if not plan.operations:
        raise NotionWriterError("Plan has no operations to apply")

    _validate_write_operations(plan)
    schema = _plan_schema(plan, target_structure)

    working_record, relation_warnings = resolve_record_relations(
        dict(plan.normalized_record),
        plan.field_mapping,
        target_structure,
        adapter,
    )
    properties = _build_record_properties(working_record, plan, schema)
    if not properties and not _has_writable_asset_property(plan, schema):
        raise NotionWriterError("Plan produced no properties to write")

    working_record, asset_results, asset_warnings = execute_asset_operations(
        working_record,
        plan.asset_operations,
        adapter,
        downloader=downloader,
    )
    properties = _build_record_properties(working_record, plan, schema)
    if not properties:
        raise NotionWriterError("Plan produced no properties to write")

    results: list[dict[str, Any]] = []
    for operation in plan.operations:
        operation_type = operation.get("type")
        page_id = operation.get("page_id")
        if page_id:
            response = adapter.update_page(page_id, properties)
            action = "update_page"
        else:
            response = adapter.create_page(plan.target.data_source_id, properties)
            action = "create_page"

        result = {
            "type": operation_type,
            "action": action,
        }
        response_page_id = response.get("id") if isinstance(response, dict) else None
        response_url = response.get("url") if isinstance(response, dict) else None
        if response_page_id is not None:
            result["page_id"] = response_page_id
        if response_url is not None:
            result["url"] = response_url
        results.append(result)

    return {
        "plan_id": plan.plan_id,
        "applied": True,
        "results": results,
        "asset_results": asset_results,
        "warnings": plan.warnings + relation_warnings + asset_warnings,
    }
