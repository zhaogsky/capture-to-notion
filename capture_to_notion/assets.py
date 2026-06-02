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


KNOWN_ASSET_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".avif"}


def _asset_suffix_for_bytes(payload: bytes) -> str | None:
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if payload.startswith(b"%PDF-"):
        return ".pdf"
    if len(payload) >= 12 and payload[4:8] == b"ftyp" and b"avif" in payload[8:16]:
        return ".avif"
    return None


def _cache_path_with_asset_suffix(cache_path: Path, payload: bytes) -> Path:
    suffix = _asset_suffix_for_bytes(payload)
    if suffix is None or cache_path.suffix.lower() in KNOWN_ASSET_SUFFIXES:
        return cache_path
    return cache_path.with_suffix(suffix)


def _normalize_cached_asset_path(cache_path: Path) -> Path:
    if cache_path.suffix.lower() in KNOWN_ASSET_SUFFIXES:
        return cache_path
    inferred_path = _cache_path_with_asset_suffix(cache_path, cache_path.read_bytes())
    if inferred_path != cache_path:
        cache_path.replace(inferred_path)
    return inferred_path


def _file_upload_id(uploaded_file: Any) -> str | None:
    if not isinstance(uploaded_file, dict):
        return None
    file_upload = uploaded_file.get("file_upload")
    if isinstance(file_upload, dict) and isinstance(file_upload.get("id"), str):
        return file_upload["id"]
    upload_id = uploaded_file.get("id")
    return upload_id if isinstance(upload_id, str) else None


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

        updated_record.pop(operation.record_key, None)

        if not source_url:
            result["status"] = "source_missing"
            results.append(result)
            continue

        cache_path = Path(operation.local_cache_path) if operation.local_cache_path else None
        if cache_path is not None and not cache_path.exists():
            try:
                payload = download(source_url)
                cache_path = _cache_path_with_asset_suffix(cache_path, payload)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(payload)
            except Exception:
                warnings.append(f"asset_download_failed:{operation.record_key}:{source_url}")
                result["status"] = "download_failed"
                results.append(result)
                continue
        elif cache_path is not None:
            try:
                cache_path = _normalize_cached_asset_path(cache_path)
            except Exception:
                warnings.append(f"asset_download_failed:{operation.record_key}:{source_url}")
                result["status"] = "download_failed"
                results.append(result)
                continue

        upload = getattr(adapter, "upload_file_for_property", None)
        if not callable(upload) or cache_path is None:
            warnings.append(f"asset_upload_unavailable:{operation.record_key}:{source_url}")
            result["status"] = "upload_unavailable"
            results.append(result)
            continue

        mime_type = _mime_type_for_path(cache_path)
        try:
            uploaded_file = upload(cache_path, cache_path.name, mime_type)
        except Exception:
            warnings.append(f"asset_upload_failed:{operation.record_key}:{source_url}")
            result["status"] = "upload_failed"
            results.append(result)
            continue

        if uploaded_file is None:
            warnings.append(f"asset_upload_unavailable:{operation.record_key}:{source_url}")
            result["status"] = "upload_unavailable"
            results.append(result)
            continue

        updated_record[operation.record_key] = uploaded_file
        result["status"] = "uploaded"
        result["uploaded_name"] = uploaded_file.get("name", cache_path.name) if isinstance(uploaded_file, dict) else cache_path.name
        result["mime_type"] = uploaded_file.get("mime_type", mime_type) if isinstance(uploaded_file, dict) else mime_type
        result["local_cache_path"] = str(cache_path)
        file_upload_id = _file_upload_id(uploaded_file)
        if file_upload_id:
            result["file_upload_id"] = file_upload_id
        results.append(result)

    return updated_record, results, warnings
