from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

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


def _check_page_cover(page: dict[str, Any], url_checker: UrlChecker | None) -> dict[str, Any]:
    url = cover_url_from_page(page)
    if not url:
        return {"status": "missing"}
    if url_checker is None or url_checker(url):
        return {"status": "present"}
    return {"status": "inaccessible"}


def _status(check: dict[str, Any]) -> str:
    return str(check.get("status", "missing"))


def _warning_for_check(name: str, check: dict[str, Any]) -> str | None:
    status = _status(check)
    if status == "present":
        return None
    if status == "inaccessible":
        return f"inaccessible:{name}"
    return f"missing:{name}"


def _default_checks() -> dict[str, dict[str, Any]]:
    return {}


def _property_check(
    properties: dict[str, Any],
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
    record_key: str,
    check_spec: dict[str, Any],
    url_checker: UrlChecker | None,
) -> dict[str, Any]:
    property_name = field_mapping.get(record_key)
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
        if _all_urls_accessible(urls, url_checker):
            return {"status": "present", "property": property_name}
        return {"status": "inaccessible", "property": property_name}

    if property_has_value(property_data):
        return {"status": "present", "property": property_name}
    return {"status": "missing", "property": property_name}


def _verification_checks(
    page: dict[str, Any],
    properties: dict[str, Any],
    url_checker: UrlChecker | None,
    field_mapping: dict[str, str],
    schema: dict[str, dict[str, Any]],
    requested_checks: dict[str, dict[str, Any]],
    include_page_cover: bool,
) -> dict[str, dict[str, Any]]:
    result = {"page": {"status": "present" if page.get("object") == "page" else "missing"}}
    for record_key, check_spec in requested_checks.items():
        result[record_key] = _property_check(properties, field_mapping, schema, record_key, check_spec, url_checker)
    if include_page_cover:
        result["page_cover"] = _check_page_cover(page, url_checker)
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
    blocks_present = len(blocks) >= required_block_count
    if not blocks_present:
        warnings.append("body_blocks_missing")

    samples = expected_text_samples or []
    samples_present = all(sample in body_text for sample in samples)
    if not samples_present:
        warnings.append("body_text_samples_missing")

    checks = {
        "page": {"status": "present"},
        "title": {"status": "present" if title_present else "missing"},
        "body_blocks": {"status": "present" if blocks_present else "missing", "count": len(blocks)},
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
    )
    warnings = [warning for name, check in check_results.items() if (warning := _warning_for_check(name, check))]
    return {
        "page_id": page_id,
        "verified": not warnings,
        "checks": check_results,
        "warnings": warnings,
    }
