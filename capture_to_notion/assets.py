from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from capture_to_notion.config import AppConfig
from capture_to_notion.models import AssetOperation


def cover_cache_path(config: AppConfig, content_type: str, source_url: str) -> Path:
    bucket = "podcast_episodes" if content_type == "podcast_episode" else "books"
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(source_url.split("?", 1)[0]).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    return config.covers_dir / bucket / f"{digest}{suffix}"


def plan_cover_asset(
    config: AppConfig,
    content_type: str,
    cover_url: str | None,
    target_field: str | None,
    allow_download: bool = True,
) -> AssetOperation:
    if not target_field:
        return AssetOperation(
            type="cover_image",
            source_url=cover_url,
            local_cache_path=None,
            target_field=None,
            action="skip",
            status="skipped",
            warning="target_has_no_cover_field",
        )
    if not cover_url:
        return AssetOperation(
            type="cover_image",
            source_url=None,
            local_cache_path=None,
            target_field=target_field,
            action="skip",
            status="skipped",
            warning="cover_url_not_found",
        )
    local_path = cover_cache_path(config, content_type, cover_url) if allow_download else None
    return AssetOperation(
        type="cover_image",
        source_url=cover_url,
        local_cache_path=str(local_path) if local_path else None,
        target_field=target_field,
        action="download_and_attach" if allow_download else "attach_external_url",
    )



def default_download(url: str) -> bytes:
    with urlopen(url) as response:
        return response.read()


HEAD_FALLBACK_STATUSES = {403, 405, 501}


def _image_response_result(response: Any, method: str) -> dict[str, Any]:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    headers = getattr(response, "headers", {})
    content_type = headers.get("Content-Type", "") if hasattr(headers, "get") else ""
    return {
        "ok": status in {200, 206} and content_type.lower().startswith("image/"),
        "status": status,
        "content_type": content_type,
        "method": method,
    }


def _image_error_result(exc: HTTPError, method: str) -> dict[str, Any]:
    return {"ok": False, "status": exc.code, "content_type": "", "method": method}


def verify_image_url(url: str) -> dict[str, Any]:
    try:
        with urlopen(Request(url, method="HEAD")) as response:
            return _image_response_result(response, "HEAD")
    except HTTPError as exc:
        if exc.code not in HEAD_FALLBACK_STATUSES:
            return _image_error_result(exc, "HEAD")

    try:
        with urlopen(Request(url, method="GET", headers={"Range": "bytes=0-0"})) as response:
            return _image_response_result(response, "GET")
    except HTTPError as exc:
        return _image_error_result(exc, "GET")


def _mime_type_for_path(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def execute_asset_operations(
    record: dict[str, Any],
    asset_operations: list[AssetOperation],
    adapter: Any,
    downloader: Callable[[str], bytes] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    updated_record = dict(record)
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    download = downloader or default_download

    for operation in asset_operations:
        source_url = operation.source_url
        result = {
            "field": operation.record_key,
            "action": operation.action,
            "status": operation.status,
            "source_url": source_url,
        }

        if operation.action == "skip":
            result["status"] = "skipped"
            results.append(result)
            continue

        if operation.action == "attach_external_url":
            updated_record[operation.record_key] = source_url
            result["status"] = "external_url"
            results.append(result)
            continue

        if operation.action != "download_and_attach":
            results.append(result)
            continue

        if not source_url:
            updated_record[operation.record_key] = source_url
            result["status"] = "external_url"
            results.append(result)
            continue

        cache_path = Path(operation.local_cache_path) if operation.local_cache_path else None
        if cache_path is not None and not cache_path.exists():
            try:
                payload = download(source_url)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(payload)
            except Exception:
                updated_record[operation.record_key] = source_url
                warnings.append(f"asset_download_failed:{operation.record_key}:{source_url}")
                result["status"] = "external_url"
                results.append(result)
                continue

        upload = getattr(adapter, "upload_file_for_property", None)
        if not callable(upload) or cache_path is None:
            updated_record[operation.record_key] = source_url
            warnings.append(f"asset_upload_unavailable:{operation.record_key}:{source_url}")
            result["status"] = "external_url"
            results.append(result)
            continue

        try:
            uploaded_file = upload(cache_path, cache_path.name, _mime_type_for_path(cache_path))
        except Exception:
            updated_record[operation.record_key] = source_url
            warnings.append(f"asset_upload_failed:{operation.record_key}:{source_url}")
            result["status"] = "external_url"
            results.append(result)
            continue

        if uploaded_file is None:
            updated_record[operation.record_key] = source_url
            warnings.append(f"asset_upload_unavailable:{operation.record_key}:{source_url}")
            result["status"] = "external_url"
            results.append(result)
            continue

        updated_record[operation.record_key] = uploaded_file
        result["status"] = "uploaded"
        results.append(result)

    return updated_record, results, warnings
