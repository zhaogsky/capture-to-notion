from __future__ import annotations

from typing import Any, Callable

from capture_to_notion.assets import execute_asset_operations
from capture_to_notion.models import AssetOperation, WritePlan
from capture_to_notion.notion_adapter import NotionApiError, NotionNotFoundError, NotionRateLimitError
from capture_to_notion.relations import resolve_record_relations
from capture_to_notion.schema import build_properties


COMPLETE_RELATION_PAGE = "complete_relation_page"
CREATE_OR_UPDATE_PAGE = "create_or_update_page"
EXPECTED_NOTION_WRITE_ERRORS = (NotionApiError, NotionNotFoundError, NotionRateLimitError)


class NotionWriterError(Exception):
    pass


class PartialWriteError(NotionWriterError):
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


def _record_with_state_mapping(
    record: dict[str, Any],
    field_mapping: dict[str, str],
    target_structure: dict[str, Any],
) -> dict[str, Any]:
    state_mapping = target_structure.get("state_mapping", {})
    if not isinstance(state_mapping, dict):
        return record
    field = state_mapping.get("field")
    values = state_mapping.get("values", {})
    if not isinstance(field, str) or not isinstance(values, dict):
        return record

    mapped_record = dict(record)
    for record_key, target_field in field_mapping.items():
        if target_field != field:
            continue
        value = mapped_record.get(record_key)
        if value in values:
            mapped_record[record_key] = values[value]
    return mapped_record


def _build_record_properties(
    record: dict[str, Any],
    plan: WritePlan,
    target_structure: dict[str, Any],
    schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return build_properties(_record_with_state_mapping(record, plan.field_mapping, target_structure), plan.field_mapping, schema)


def _has_writable_asset_property(plan: WritePlan, schema: dict[str, dict[str, Any]]) -> bool:
    for operation in plan.asset_operations:
        if operation.action == "skip" or not operation.source_url:
            continue
        field_name = plan.field_mapping.get(operation.record_key)
        if field_name and schema.get(field_name, {}).get("type") == "files":
            return True
    return False


def build_plan_properties(plan: WritePlan, target_structure: dict[str, Any]) -> dict[str, Any]:
    return _build_record_properties(plan.normalized_record, plan, target_structure, _plan_schema(plan, target_structure))


def _validate_write_operations(plan: WritePlan) -> None:
    for operation in plan.operations:
        operation_type = operation.get("type")
        if operation_type != CREATE_OR_UPDATE_PAGE:
            raise NotionWriterError(f"Unsupported write operation type: {operation_type}")

        operation_data_source_id = operation.get("data_source_id")
        if operation_data_source_id != plan.target.data_source_id:
            raise NotionWriterError(
                "Operation data_source_id does not match plan target data_source_id"
            )


def _resolved_page_ids(value: Any) -> tuple[list[str], bool]:
    values = value if isinstance(value, list) else [value]
    page_ids: list[str] = []
    seen: set[str] = set()
    invalid = False
    for item in values:
        if item in (None, ""):
            continue
        if not isinstance(item, str):
            invalid = True
            continue
        if item in seen:
            continue
        page_ids.append(item)
        seen.add(item)
    return page_ids, invalid


def _completion_schema(
    operation: dict[str, Any],
    target_structure: dict[str, Any],
) -> dict[str, dict[str, Any]] | None:
    schema = operation.get("schema")
    if isinstance(schema, dict):
        return schema
    data_source_id = operation.get("target_data_source_id")
    if not isinstance(data_source_id, str) or not data_source_id:
        return None
    try:
        return _data_source_schema(target_structure, data_source_id)
    except NotionWriterError:
        return None


def _completion_asset_operations(operation: dict[str, Any]) -> list[AssetOperation]:
    asset_operations = operation.get("asset_operations", [])
    if not isinstance(asset_operations, list):
        return []
    return [
        item if isinstance(item, AssetOperation) else AssetOperation.from_dict(item)
        for item in asset_operations
        if isinstance(item, (AssetOperation, dict))
    ]


def _execute_completion_operations(
    plan: WritePlan,
    resolved_record: dict[str, Any],
    target_structure: dict[str, Any],
    adapter: Any,
    downloader: Callable[[str], bytes] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    warnings: list[str] = []

    for operation in plan.completion_operations:
        operation_type = operation.get("type")
        if operation_type != COMPLETE_RELATION_PAGE:
            warnings.append(f"completion_unsupported_operation:{operation_type}")
            continue

        source_record_key = operation.get("source_record_key")
        if not isinstance(source_record_key, str) or not source_record_key:
            warnings.append("completion_source_record_key_missing")
            continue

        page_ids, has_invalid_page_id = _resolved_page_ids(resolved_record.get(source_record_key))
        if has_invalid_page_id:
            warnings.append(f"completion_invalid_page_id:{source_record_key}")
            continue
        if not page_ids:
            warnings.append(f"completion_relation_unresolved:{source_record_key}")
            continue

        schema = _completion_schema(operation, target_structure)
        if schema is None:
            warnings.append(f"completion_schema_missing:{source_record_key}")
            continue

        field_mapping = operation.get("field_mapping", {})
        record = operation.get("record", {})
        if not isinstance(field_mapping, dict) or not isinstance(record, dict):
            warnings.append(f"completion_invalid_payload:{source_record_key}")
            continue

        record, _asset_results, asset_warnings = execute_asset_operations(
            record,
            _completion_asset_operations(operation),
            adapter,
            downloader=downloader,
        )
        warnings.extend(asset_warnings)
        properties = build_properties(record, field_mapping, schema)
        if not properties:
            warnings.append(f"completion_no_properties:{source_record_key}")
            continue

        for page_id in page_ids:
            response = adapter.update_page(page_id, properties)
            result = {
                "type": operation_type,
                "action": "update_page",
                "source_record_key": source_record_key,
            }
            response_page_id = response.get("id") if isinstance(response, dict) else None
            response_url = response.get("url") if isinstance(response, dict) else None
            result["page_id"] = response_page_id if response_page_id is not None else page_id
            if response_url is not None:
                result["url"] = response_url
            results.append(result)

    return results, warnings


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
    properties = _build_record_properties(working_record, plan, target_structure, schema)
    if not properties and not _has_writable_asset_property(plan, schema):
        raise NotionWriterError("Plan produced no properties to write")

    working_record, asset_results, asset_warnings = execute_asset_operations(
        working_record,
        plan.asset_operations,
        adapter,
        downloader=downloader,
    )
    properties = _build_record_properties(working_record, plan, target_structure, schema)
    if not properties:
        raise NotionWriterError("Plan produced no properties to write")

    results: list[dict[str, Any]] = []
    for operation in plan.operations:
        operation_type = operation.get("type")
        page_id = operation.get("page_id")
        try:
            if page_id:
                response = adapter.update_page(page_id, properties)
                action = "update_page"
            else:
                response = adapter.create_page(plan.target.data_source_id, properties)
                action = "create_page"
        except EXPECTED_NOTION_WRITE_ERRORS as exc:
            if results:
                raise PartialWriteError(
                    "写入已部分完成，后续操作失败；请检查已创建页面后重新生成 update 计划再重试"
                ) from exc
            raise

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

    try:
        completion_results, completion_warnings = _execute_completion_operations(
            plan,
            working_record,
            target_structure,
            adapter,
            downloader=downloader,
        )
    except EXPECTED_NOTION_WRITE_ERRORS as exc:
        raise PartialWriteError(
            "写入已部分完成，后续补全失败；请检查已创建页面后重新生成 update 计划再重试"
        ) from exc

    response = {
        "plan_id": plan.plan_id,
        "applied": True,
        "results": results,
        "asset_results": asset_results,
        "warnings": plan.warnings + relation_warnings + asset_warnings + completion_warnings,
    }
    if plan.completion_operations:
        response["completion_results"] = completion_results
    return response
