from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Literal

from capture_to_notion.notion_adapter import NotionNotFoundError
from capture_to_notion.schema import cover_url_from_page, file_urls_from_property, property_has_value

UrlChecker = Callable[[str], bool]


def _request_url_is_accessible(url: str, method: str) -> bool | None:
    headers = {"Range": "bytes=0-0"} if method == "GET" else {}
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        if method == "HEAD" and exc.code in {403, 405}:
            return None
        return False
    except (OSError, ValueError):
        return False


def url_is_accessible(url: str) -> bool:
    head_result = _request_url_is_accessible(url, "HEAD")
    if head_result is not None:
        return head_result
    return bool(_request_url_is_accessible(url, "GET"))


def _all_urls_accessible(urls: list[str], url_checker: UrlChecker | None) -> bool:
    if url_checker is None:
        return bool(urls)
    return all(url_checker(url) for url in urls)


def _check_page_cover(page: dict[str, Any], url_checker: UrlChecker | None, expected_url: str | None = None) -> dict[str, Any]:
    url = cover_url_from_page(page)
    if not url:
        return {"status": "missing"}
    if expected_url and url != expected_url:
        return {"status": "mismatch", "expected_url": expected_url, "actual_url": url}
    if url_checker is None or url_checker(url):
        return {"status": "present"}
    return {"status": "inaccessible"}


def _status(check: dict[str, Any]) -> str:
    return str(check.get("status", "missing"))


def _warning_for_check(name: str, check: dict[str, Any]) -> str | None:
    status = _status(check)
    if status in {"present", "satisfied"}:
        return None
    if status == "inaccessible":
        return f"inaccessible:{name}"
    if status == "mismatch":
        return f"mismatch:{name}"
    if status == "failed":
        return f"failed:{name}"
    if status == "not_guaranteed":
        return f"not_guaranteed:{name}"
    return f"missing:{name}"


def _default_checks() -> dict[str, dict[str, Any]]:
    return {}


def _computed_value(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    value_type = payload.get("type")
    if not isinstance(value_type, str) or value_type not in payload:
        return payload
    value = payload.get(value_type)
    if value_type == "date" and isinstance(value, dict):
        return value.get("start")
    return value



def _property_actual_value(property_data: dict[str, Any], property_type: str) -> Any:
    value = property_data.get(property_type)
    if property_type in {"formula", "rollup"}:
        return _computed_value(value)
    if property_type in {"status", "select"} and isinstance(value, dict):
        return value.get("name")
    if property_type in {"title", "rich_text"} and isinstance(value, list):
        return _plain_text_from_rich_text(value)
    if property_type == "multi_select" and isinstance(value, list):
        return [item.get("name") for item in value if isinstance(item, dict)]
    if property_type == "relation" and isinstance(value, list):
        return [item.get("id") for item in value if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if property_type == "people" and isinstance(value, list):
        return [item.get("id") for item in value if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if property_type == "date" and isinstance(value, dict):
        return value.get("start")
    return value



def _normalized_expected_value(expected_value: Any, property_type: str, actual_value: Any = None) -> Any:
    if (property_type == "number" or isinstance(actual_value, (int, float))) and isinstance(expected_value, str):
        stripped = expected_value.strip()
        if stripped.isdigit() or (stripped.startswith(("-", "+")) and stripped[1:].isdigit()):
            return int(stripped)
        try:
            return float(stripped)
        except ValueError:
            return expected_value
    if isinstance(actual_value, bool) and isinstance(expected_value, str):
        normalized = expected_value.strip().lower()
        if normalized in {"true", "yes", "y", "1", "是", "真"}:
            return True
        if normalized in {"false", "no", "n", "0", "否", "假"}:
            return False
    return expected_value



def _has_verifiable_property_value(property_data: dict[str, Any], property_type: str) -> bool:
    if property_type in {"formula", "rollup"}:
        actual_value = _property_actual_value(property_data, property_type)
        return actual_value not in (None, "", [], {})
    return property_has_value(property_data)



def _value_satisfies_expectation(actual_value: Any, expected_value: Any, property_type: str) -> bool:
    normalized_expected = _normalized_expected_value(expected_value, property_type, actual_value)
    if isinstance(actual_value, list) and not isinstance(normalized_expected, list):
        return normalized_expected in actual_value
    return actual_value == normalized_expected



def _expected_relation_ids(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    ids: list[str] = []
    for item in values:
        if isinstance(item, str) and item:
            ids.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            ids.append(item["id"])
    return ids



def _relation_ids_from_property(property_data: dict[str, Any]) -> list[str]:
    relation = property_data.get("relation")
    if not isinstance(relation, list):
        return []
    return [item["id"] for item in relation if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]]



def _expected_file_urls(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    urls: list[str] = []
    for item in values:
        if isinstance(item, str) and item:
            urls.append(item)
            continue
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("url"), str) and item["url"]:
            urls.append(item["url"])
            continue
        urls.extend(file_urls_from_property({"type": "files", "files": [item]}))
    return urls



def _property_check(
    properties: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
    record_key: str,
    check_spec: dict[str, Any],
    url_checker: UrlChecker | None,
) -> dict[str, Any]:
    property_name = check_spec.get("property_name") if isinstance(check_spec.get("property_name"), str) else field_mapping.get(record_key)
    if not property_name:
        return {"status": "missing"}

    expected_type = check_spec.get("property_type")
    schema_type = schema.get(property_name, {}).get("type")
    if isinstance(expected_type, str) and schema_type is not None and schema_type != expected_type:
        return {"status": "missing", "property": property_name}

    property_data = properties.get(property_name)
    if not isinstance(property_data, dict):
        return {"status": "missing", "property": property_name}
    if isinstance(expected_type, str) and property_data.get("type") != expected_type:
        return {"status": "missing", "property": property_name}

    if expected_type == "files" and check_spec.get("check_urls"):
        urls = file_urls_from_property(property_data)
        if not urls:
            return {"status": "missing", "property": property_name}
        expected_urls = _expected_file_urls(check_spec.get("expected_urls"))
        if expected_urls and set(urls) != set(expected_urls):
            return {
                "status": "mismatch",
                "property": property_name,
                "expected_urls": expected_urls,
                "actual_urls": urls,
            }
        if _all_urls_accessible(urls, url_checker):
            return {"status": "present", "property": property_name}
        return {"status": "inaccessible", "property": property_name}

    if expected_type == "relation":
        actual_ids = _relation_ids_from_property(property_data)
        if not actual_ids:
            return {"status": "missing", "property": property_name}
        expected_ids = _expected_relation_ids(check_spec.get("expected_ids"))
        if expected_ids and set(actual_ids) != set(expected_ids):
            return {
                "status": "mismatch",
                "property": property_name,
                "expected_ids": expected_ids,
                "actual_ids": actual_ids,
            }
        return {"status": "present", "property": property_name}

    if isinstance(expected_type, str) and _has_verifiable_property_value(property_data, expected_type):
        if "expected_value" in check_spec:
            actual_value = _property_actual_value(property_data, expected_type)
            if expected_type == "rollup" and isinstance(actual_value, list):
                return {"status": "present", "property": property_name}
            expected_value = _normalized_expected_value(check_spec["expected_value"], expected_type, actual_value)
            if not _value_satisfies_expectation(actual_value, expected_value, expected_type):
                return {
                    "status": "mismatch",
                    "property": property_name,
                    "expected_value": expected_value,
                    "actual_value": actual_value,
                }
        return {"status": "present", "property": property_name}
    return {"status": "missing", "property": property_name}


def _view_visibility_check(
    properties: dict[str, Any],
    schema: dict[str, dict[str, Any]],
    view_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(view_context, dict):
        return None
    constraints = view_context.get("constraints")
    if not isinstance(constraints, dict):
        return None
    values = constraints.get("values")
    warnings = [warning for warning in constraints.get("warnings", []) if isinstance(warning, str)] if isinstance(constraints.get("warnings"), list) else []
    unsupported = [warning for warning in constraints.get("unsupported", []) if isinstance(warning, str)] if isinstance(constraints.get("unsupported"), list) else []
    if not isinstance(values, dict):
        values = {}

    constraint_checks: list[dict[str, Any]] = []
    failed = False
    for property_name, expected_value in values.items():
        if not isinstance(property_name, str):
            continue
        property_schema = schema.get(property_name)
        property_type = property_schema.get("type") if isinstance(property_schema, dict) else None
        property_data = properties.get(property_name)
        actual_value = None
        status = "failed"
        if isinstance(property_type, str) and isinstance(property_data, dict) and property_data.get("type") == property_type:
            actual_value = _property_actual_value(property_data, property_type)
            status = "satisfied" if _value_satisfies_expectation(actual_value, expected_value, property_type) else "failed"
        if status == "failed":
            failed = True
        constraint_checks.append(
            {
                "field": property_name,
                "expected_value": expected_value,
                "actual_value": actual_value,
                "status": status,
            }
        )

    status = "failed" if failed else "not_guaranteed" if unsupported else "satisfied"
    return {
        "status": status,
        "view_id": view_context.get("view_id"),
        "view_name": view_context.get("view_name"),
        "view_type": view_context.get("view_type"),
        "constraints": constraint_checks,
        "warnings": warnings,
    }



def _verification_checks(
    page: dict[str, Any],
    properties: dict[str, Any],
    url_checker: UrlChecker | None,
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
    requested_checks: dict[str, dict[str, Any]],
    include_page_cover: bool,
    expected_page_cover_url: str | None = None,
    view_context: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    result = {"page": {"status": "present" if page.get("object") == "page" else "missing"}}
    for record_key, check_spec in requested_checks.items():
        result[record_key] = _property_check(properties, field_mapping, schema, record_key, check_spec, url_checker)
    if include_page_cover:
        result["page_cover"] = _check_page_cover(page, url_checker, expected_page_cover_url)
    view_visibility = _view_visibility_check(properties, schema, view_context)
    if view_visibility is not None:
        result["view_visibility"] = view_visibility
    return result


def _plain_text_from_rich_text(items: list[dict[str, Any]] | None) -> str:
    if not isinstance(items, list):
        return ""
    return "".join(item.get("plain_text", "") for item in items if isinstance(item, dict))


def _plain_page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties")
    if not isinstance(properties, dict):
        return ""
    preferred = properties.get("title")
    if isinstance(preferred, dict) and preferred.get("type") == "title":
        return _plain_text_from_rich_text(preferred.get("title"))
    for property_data in properties.values():
        if isinstance(property_data, dict) and property_data.get("type") == "title":
            return _plain_text_from_rich_text(property_data.get("title"))
    return ""


def _block_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    if not isinstance(block_type, str):
        return ""
    payload = block.get(block_type)
    if not isinstance(payload, dict):
        return ""
    return _plain_text_from_rich_text(payload.get("rich_text"))


def verify_plain_page(
    page_id: str,
    adapter: Any,
    *,
    expected_title: str | None = None,
    expected_block_count: int | None = None,
    expected_text_samples: list[str] | None = None,
    block_count_mode: Literal["at_least", "exact"] = "at_least",
) -> dict[str, Any]:
    try:
        page = adapter.retrieve_page(page_id)
    except NotionNotFoundError:
        checks = {
            "page": {"status": "missing"},
            "title": {"status": "missing"},
            "body_blocks": {"status": "missing", "count": 0},
            "body_text_samples": {"status": "missing"},
        }
        return {"page_id": page_id, "verified": False, "checks": checks, "warnings": ["missing:page"]}

    page_object = page.get("object")
    if page_object is not None and page_object != "page":
        checks = {
            "page": {"status": "mismatch", "object": page_object},
            "title": {"status": "missing"},
            "body_blocks": {"status": "missing", "count": 0},
            "body_text_samples": {"status": "missing"},
        }
        return {"page_id": page_id, "verified": False, "checks": checks, "warnings": ["page_object_mismatch"]}

    blocks = adapter.list_block_children(page_id)
    if not isinstance(blocks, list):
        blocks = []
    body_text = "\n".join(_block_text(block) for block in blocks if isinstance(block, dict))
    warnings: list[str] = []

    title = _plain_page_title(page)
    title_present = bool(title) and (expected_title is None or title == expected_title)
    if not title_present:
        warnings.append("title_mismatch")

    required_block_count = expected_block_count if expected_block_count is not None else 1
    block_count = len(blocks)
    if block_count_mode == "exact":
        blocks_present = block_count == required_block_count
        block_warning = "body_blocks_count_mismatch"
        body_blocks_check = {
            "status": "present" if blocks_present else "mismatch",
            "count": block_count,
        }
        if not blocks_present:
            body_blocks_check.update({"expected_count": required_block_count, "mode": "exact"})
    else:
        blocks_present = block_count >= required_block_count
        block_warning = "body_blocks_missing"
        body_blocks_check = {"status": "present" if blocks_present else "missing", "count": block_count}
    if not blocks_present:
        warnings.append(block_warning)

    samples = expected_text_samples or []
    samples_present = all(sample in body_text for sample in samples)
    if not samples_present:
        warnings.append("body_text_samples_missing")

    checks = {
        "page": {"status": "present"},
        "title": {"status": "present" if title_present else "missing"},
        "body_blocks": body_blocks_check,
        "body_text_samples": {"status": "present" if samples_present else "missing"},
    }
    return {"page_id": page_id, "verified": not warnings, "checks": checks, "warnings": warnings}


def verify_capture_page(
    page_id: str,
    adapter: Any,
    url_checker: UrlChecker | None = None,
    *,
    field_mapping: dict[str, str] | None = None,
    schema: dict[str, dict[str, Any]] | None = None,
    checks: dict[str, dict[str, Any]] | None = None,
    include_page_cover: bool = True,
    expected_page_cover_url: str | None = None,
    view_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested_checks = checks or _default_checks()
    try:
        page = adapter.retrieve_page(page_id)
    except NotionNotFoundError:
        check_results = {"page": {"status": "missing"}}
        for record_key in requested_checks:
            check_results[record_key] = {"status": "missing"}
        if include_page_cover:
            check_results["page_cover"] = {"status": "missing"}
        return {
            "page_id": page_id,
            "verified": False,
            "checks": check_results,
            "warnings": [f"missing:{name}" for name in check_results],
        }

    properties = page.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    check_results = _verification_checks(
        page,
        properties,
        url_checker,
        field_mapping or {},
        schema or {},
        requested_checks,
        include_page_cover,
        expected_page_cover_url,
        view_context,
    )
    warnings = [warning for name, check in check_results.items() if (warning := _warning_for_check(name, check))]
    return {
        "page_id": page_id,
        "verified": not warnings,
        "checks": check_results,
        "warnings": warnings,
    }
