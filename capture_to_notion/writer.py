from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from capture_to_notion.assets import execute_asset_operations
from capture_to_notion.blocks import split_block_batches
from capture_to_notion.models import AssetOperation, WritePlan
from capture_to_notion.notion_adapter import NotionApiError, NotionNotFoundError, NotionRateLimitError
from capture_to_notion.people import resolve_record_people
from capture_to_notion.relations import resolve_record_relations
from capture_to_notion.schema import build_properties


APPEND_PAGE_CONTENT = "append_page_content"
COMPLETE_RELATION_PAGE = "complete_relation_page"
CREATE_CHILD_PAGE = "create_child_page"
CREATE_OR_UPDATE_PAGE = "create_or_update_page"
UPDATE_PAGE_PROPERTIES = "update_page_properties"
EXPECTED_NOTION_WRITE_ERRORS = (NotionApiError, NotionNotFoundError, NotionRateLimitError)


class NotionWriterError(Exception):
    pass


class PartialWriteError(NotionWriterError):
    pass


def _iter_target_data_sources(target_structure: dict[str, Any]):
    data_sources = target_structure.get("data_sources")
    if isinstance(data_sources, dict):
        yield from data_sources.values()

    graph = target_structure.get("graph")
    graph_data_sources = graph.get("data_sources") if isinstance(graph, dict) else None
    if isinstance(graph_data_sources, dict):
        yield from graph_data_sources.values()


def _data_source_schema(target_structure: dict[str, Any], data_source_id: str) -> dict[str, dict[str, Any]]:
    for data_source in _iter_target_data_sources(target_structure):
        if isinstance(data_source, dict) and data_source.get("data_source_id") == data_source_id:
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


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not any(character.isspace() for character in parsed.netloc)
    )


def _page_cover_for_plan(plan: WritePlan) -> dict[str, Any] | None:
    for operation in plan.asset_operations:
        if operation.type == "cover_image" and operation.action != "skip" and _is_http_url(operation.source_url):
            return {"type": "external", "external": {"url": operation.source_url}}
    return None


def build_plan_properties(plan: WritePlan, target_structure: dict[str, Any]) -> dict[str, Any]:
    return _build_record_properties(plan.normalized_record, plan, target_structure, _plan_schema(plan, target_structure))


def _validate_plan_target(plan: WritePlan, target_structure: dict[str, Any]) -> None:
    data_source_id = plan.target.data_source_id
    target_kind = plan.target.target_kind
    if data_source_id:
        _data_source_schema(target_structure, data_source_id)
    elif target_kind == "page_parent":
        if not (plan.target.parent_page_id or plan.target.page_id):
            raise NotionWriterError("Plan page_parent target is missing parent_page_id or page_id")
    elif target_kind == "existing_page":
        if not plan.target.page_id:
            raise NotionWriterError("Plan existing_page target is missing page_id")
    else:
        raise NotionWriterError("Plan target is missing data_source_id")

    target = target_structure.get("target")
    cached_page_id = target.get("page_id") if isinstance(target, dict) else None
    plan_context_page_id = plan.target.parent_page_id if target_kind == "existing_page" else plan.target.page_id
    if (
        isinstance(plan_context_page_id, str)
        and plan_context_page_id
        and isinstance(cached_page_id, str)
        and cached_page_id
        and plan_context_page_id != cached_page_id
    ):
        raise NotionWriterError("Plan target page_id does not match target_structure page_id")



def _operation_parent_page_id(plan: WritePlan) -> str | None:
    return plan.target.parent_page_id or plan.target.page_id



def _validate_write_operations(plan: WritePlan) -> None:
    for operation in plan.operations:
        operation_type = operation.get("type")
        if operation_type == CREATE_OR_UPDATE_PAGE:
            operation_data_source_id = operation.get("data_source_id")
            if operation_data_source_id != plan.target.data_source_id:
                raise NotionWriterError(
                    "Operation data_source_id does not match plan target data_source_id"
                )
            continue

        if operation_type == CREATE_CHILD_PAGE:
            operation_parent_page_id = operation.get("parent_page_id")
            if operation_parent_page_id != _operation_parent_page_id(plan):
                raise NotionWriterError(
                    "Operation parent_page_id does not match plan target parent_page_id"
                )
            operation_title = operation.get("title")
            if not isinstance(operation_title, str) or not operation_title:
                raise NotionWriterError("CREATE_CHILD_PAGE operation title must be a non-empty string")
            continue

        if operation_type == APPEND_PAGE_CONTENT:
            operation_page_id = operation.get("page_id")
            if operation_page_id != plan.target.page_id:
                raise NotionWriterError(
                    "Operation page_id does not match plan target page_id"
                )
            continue

        raise NotionWriterError(f"Unsupported write operation type: {operation_type}")


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


def _raise_partial_page_content_write(exc: Exception) -> None:
    raise PartialWriteError(
        "写入已部分完成，后续页面内容写入失败；请检查已创建或已追加的内容后重新生成 update 计划再重试"
    ) from exc


def _execute_page_content_operation(operation: dict[str, Any], adapter: Any) -> dict[str, Any]:
    operation_type = operation.get("type")
    body_blocks = operation.get("body_blocks", [])
    batches = split_block_batches(body_blocks) if body_blocks else []

    if operation_type == CREATE_CHILD_PAGE:
        parent_page_id = operation["parent_page_id"]
        title = operation["title"]
        first_batch = batches[0] if batches else []
        response = adapter.create_child_page(parent_page_id, title, children=first_batch)
        response_page_id = response.get("id") if isinstance(response, dict) else None
        page_id = response_page_id or operation.get("page_id")
        for batch in batches[1:]:
            try:
                adapter.append_block_children(page_id, batch)
            except EXPECTED_NOTION_WRITE_ERRORS as exc:
                _raise_partial_page_content_write(exc)
        result = {"type": operation_type, "action": CREATE_CHILD_PAGE}
        operation_id = operation.get("operation_id")
        if isinstance(operation_id, str) and operation_id:
            result["operation_id"] = operation_id
        if page_id is not None:
            result["page_id"] = page_id
        response_url = response.get("url") if isinstance(response, dict) else None
        if response_url is not None:
            result["url"] = response_url
        return result

    page_id = operation["page_id"]
    has_appended = False
    for batch in batches:
        try:
            adapter.append_block_children(page_id, batch)
        except EXPECTED_NOTION_WRITE_ERRORS as exc:
            if has_appended:
                _raise_partial_page_content_write(exc)
            raise
        has_appended = True
    return {"type": operation_type, "action": APPEND_PAGE_CONTENT, "page_id": page_id}



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

        record, asset_results, asset_warnings = execute_asset_operations(
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
            if asset_results:
                result["asset_results"] = asset_results
            results.append(result)

    return results, warnings


def _execute_update_page_properties_operation(operation: dict[str, Any], adapter: Any) -> dict[str, Any]:
    page_id = operation.get("page_id")
    record = operation.get("record")
    field_mapping = operation.get("field_mapping")
    schema = operation.get("schema")
    if not isinstance(page_id, str) or not page_id:
        raise NotionWriterError("update_page_properties operation page_id must be a non-empty string")
    if not isinstance(record, dict) or not isinstance(field_mapping, dict) or not isinstance(schema, dict):
        raise NotionWriterError("update_page_properties operation requires record, field_mapping, and schema")
    properties = build_properties(record, field_mapping, schema)
    if not properties:
        raise NotionWriterError("update_page_properties operation produced no properties")
    response = adapter.update_page(page_id, properties)
    result = {
        "type": UPDATE_PAGE_PROPERTIES,
        "action": "update_page",
        "page_id": response.get("id") if isinstance(response, dict) and response.get("id") is not None else page_id,
    }
    operation_id = operation.get("operation_id")
    if isinstance(operation_id, str) and operation_id:
        result["operation_id"] = operation_id
    response_url = response.get("url") if isinstance(response, dict) else None
    if response_url is not None:
        result["url"] = response_url
    return result


def _execute_explicit_plan_operations(plan: WritePlan, adapter: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for operation in plan.plan_operations:
        operation_type = operation.get("type")
        try:
            if operation_type == UPDATE_PAGE_PROPERTIES:
                results.append(_execute_update_page_properties_operation(operation, adapter))
                continue
            if operation_type in {CREATE_CHILD_PAGE, APPEND_PAGE_CONTENT}:
                results.append(_execute_page_content_operation(operation, adapter))
                continue
            raise NotionWriterError(f"Unsupported plan operation type: {operation_type}")
        except EXPECTED_NOTION_WRITE_ERRORS as exc:
            if results:
                raise PartialWriteError(
                    "写入已部分完成，后续操作失败；请检查已更新页面后重新生成 update 计划再重试"
                ) from exc
            raise
    return results



def _created_path(target_path: Any, title: Any) -> str | None:
    if isinstance(target_path, str) and target_path and isinstance(title, str) and title:
        return f"{target_path} / {title}"
    return None



def _written_targets(plan: WritePlan, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    write_targets = plan.summary.get("write_targets") if isinstance(plan.summary, dict) else None
    if not isinstance(write_targets, list) or not write_targets:
        return []
    page_results = [result for result in results if isinstance(result, dict) and result.get("page_id")]
    written: list[dict[str, Any]] = []
    for index, write_target in enumerate(write_targets):
        if not isinstance(write_target, dict):
            continue
        result = page_results[index] if index < len(page_results) else {}
        target = {
            key: write_target.get(key)
            for key in (
                "type",
                "action",
                "title",
                "parent_page_id",
                "data_source_id",
                "target_kind",
                "target_path",
                "target_path_complete",
                "visual_path",
                "visual_path_complete",
            )
            if write_target.get(key) is not None
        }
        page_id = result.get("page_id") or write_target.get("page_id")
        if page_id is not None:
            target["page_id"] = page_id
        url = result.get("url")
        if url is not None:
            target["url"] = url
        created_path = _created_path(write_target.get("target_path"), write_target.get("title"))
        if result and write_target.get("action") == CREATE_CHILD_PAGE and created_path is not None:
            target["created_path"] = created_path
        if target:
            written.append(target)
    return written


def apply_write_plan(
    plan: WritePlan,
    target_structure: dict[str, Any],
    adapter: Any,
    downloader: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    if not plan.operations and not (plan.plan_operations_explicit and plan.plan_operations):
        raise NotionWriterError("Plan has no operations to apply")

    _validate_plan_target(plan, target_structure)
    if plan.plan_operations_explicit:
        results = _execute_explicit_plan_operations(plan, adapter)
        response = {
            "plan_id": plan.plan_id,
            "applied": True,
            "results": results,
            "asset_results": [],
            "warnings": plan.warnings,
        }
        written_targets = _written_targets(plan, results)
        if written_targets:
            response["written_targets"] = written_targets
        return response

    _validate_write_operations(plan)
    has_data_source_operation = any(
        operation.get("type") == CREATE_OR_UPDATE_PAGE for operation in plan.operations
    )

    working_record = dict(plan.normalized_record)
    relation_warnings: list[str] = []
    asset_results: list[dict[str, Any]] = []
    asset_warnings: list[str] = []
    properties: dict[str, Any] = {}
    if has_data_source_operation:
        schema = _plan_schema(plan, target_structure)
        working_record, relation_warnings = resolve_record_relations(
            working_record,
            plan.field_mapping,
            target_structure,
            adapter,
        )
        people_warnings: list[str]
        working_record, people_warnings = resolve_record_people(
            working_record,
            plan.field_mapping,
            target_structure,
            adapter,
        )
        relation_warnings.extend(people_warnings)
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

    page_cover = _page_cover_for_plan(plan)
    results: list[dict[str, Any]] = []
    for operation in plan.operations:
        operation_type = operation.get("type")
        try:
            if operation_type in {CREATE_CHILD_PAGE, APPEND_PAGE_CONTENT}:
                results.append(_execute_page_content_operation(operation, adapter))
                continue

            page_id = operation.get("page_id")
            if page_id:
                response = (
                    adapter.update_page(page_id, properties, cover=page_cover)
                    if page_cover is not None
                    else adapter.update_page(page_id, properties)
                )
                action = "update_page"
            else:
                response = (
                    adapter.create_page(plan.target.data_source_id, properties, cover=page_cover)
                    if page_cover is not None
                    else adapter.create_page(plan.target.data_source_id, properties)
                )
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
    written_targets = _written_targets(plan, results)
    if written_targets:
        response["written_targets"] = written_targets
    if working_record != plan.normalized_record:
        response["resolved_record"] = working_record
    if plan.completion_operations:
        response["completion_results"] = completion_results
    return response
