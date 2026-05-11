from pathlib import Path
from urllib.error import HTTPError

import pytest

from capture_to_notion.assets import execute_asset_operations, verify_image_url
from capture_to_notion.models import AssetOperation


class FakeUploadAdapter:
    def __init__(self, upload_result=None, upload_error: Exception | None = None):
        self.upload_result = upload_result
        self.upload_error = upload_error
        self.calls = []

    def upload_file_for_property(self, path: Path, name: str, mime_type: str):
        self.calls.append((path, name, mime_type))
        if self.upload_error is not None:
            raise self.upload_error
        return self.upload_result


class NoUploadAdapter:
    pass


def make_operation(
    action: str,
    source_url: str = "https://example.com/cover.jpg",
    local_cache_path: str | None = None,
    record_key: str = "cover",
) -> AssetOperation:
    return AssetOperation(
        type="cover_image",
        source_url=source_url,
        local_cache_path=local_cache_path,
        target_field="封面",
        action=action,
        record_key=record_key,
    )


def test_execute_asset_operations_external_url_action_does_not_upload():
    record = {"title": "Book"}
    adapter = FakeUploadAdapter(upload_result={"type": "file_upload"})

    updated_record, results, warnings = execute_asset_operations(
        record,
        [make_operation("attach_external_url")],
        adapter,
    )

    assert record == {"title": "Book"}
    assert updated_record["cover"] == "https://example.com/cover.jpg"
    assert results == [
        {
            "field": "cover",
            "action": "attach_external_url",
            "status": "external_url",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == []
    assert adapter.calls == []


def test_execute_asset_operations_download_and_upload_success(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    uploaded = {"type": "file_upload", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)
    downloads = []

    def fake_downloader(url: str) -> bytes:
        downloads.append(url)
        return b"image-bytes"

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=fake_downloader,
    )

    assert downloads == ["https://example.com/cover.jpg"]
    assert cache_path.read_bytes() == b"image-bytes"
    assert adapter.calls == [(cache_path, "cover.jpg", "image/jpeg")]
    assert updated_record["cover"] == uploaded
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "uploaded",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == []


def test_execute_asset_operations_upload_unavailable_falls_back_to_external_url(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = FakeUploadAdapter(upload_result=None)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert cache_path.read_bytes() == b"image-bytes"
    assert updated_record["cover"] == "https://example.com/cover.jpg"
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "external_url",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == ["asset_upload_unavailable:cover:https://example.com/cover.jpg"]


def test_execute_asset_operations_uses_cache_file_mime_type(tmp_path):
    cache_path = tmp_path / "covers" / "cover.png"
    uploaded = {"type": "file_upload", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)

    execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert adapter.calls == [(cache_path, "cover.png", "image/png")]


def test_execute_asset_operations_upload_failure_falls_back_to_external_url(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = FakeUploadAdapter(upload_error=RuntimeError("upload failed"))

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert updated_record["cover"] == "https://example.com/cover.jpg"
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "external_url",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == ["asset_upload_failed:cover:https://example.com/cover.jpg"]


def test_execute_asset_operations_uses_existing_cache_without_downloading(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"cached-image")
    uploaded = {"type": "file_upload", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)
    downloads = []

    def fake_downloader(url: str) -> bytes:
        downloads.append(url)
        raise RuntimeError("should not download")

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=fake_downloader,
    )

    assert downloads == []
    assert updated_record["cover"] == uploaded
    assert results[0]["status"] == "uploaded"
    assert warnings == []


def test_execute_asset_operations_cache_write_failure_falls_back_to_external_url(tmp_path, monkeypatch):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = FakeUploadAdapter(upload_result={"type": "file_upload"})

    def failing_write_bytes(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", failing_write_bytes)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert updated_record["cover"] == "https://example.com/cover.jpg"
    assert results[0]["status"] == "external_url"
    assert warnings == ["asset_download_failed:cover:https://example.com/cover.jpg"]
    assert adapter.calls == []


def test_execute_asset_operations_download_failure_falls_back_to_external_url(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = NoUploadAdapter()

    def failing_downloader(url: str) -> bytes:
        raise RuntimeError("download failed")

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=failing_downloader,
    )

    assert not cache_path.exists()
    assert updated_record["cover"] == "https://example.com/cover.jpg"
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "external_url",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == ["asset_download_failed:cover:https://example.com/cover.jpg"]


def test_execute_asset_operations_skip_leaves_record_unchanged():
    record = {"title": "Book"}

    updated_record, results, warnings = execute_asset_operations(
        record,
        [make_operation("skip")],
        FakeUploadAdapter(upload_result={"type": "file_upload"}),
    )

    assert updated_record == record
    assert updated_record is not record
    assert results == [
        {
            "field": "cover",
            "action": "skip",
            "status": "skipped",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == []


def test_execute_asset_operations_uses_record_key_for_result_record_and_warnings(tmp_path):
    cache_path = tmp_path / "assets" / "poster.png"
    uploaded = {"type": "file_upload", "name": "poster.png", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Movie"},
        [make_operation("download_and_attach", source_url="https://example.com/poster.png", local_cache_path=str(cache_path), record_key="poster")],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert updated_record["poster"] == uploaded
    assert "cover" not in updated_record
    assert results == [
        {
            "field": "poster",
            "action": "download_and_attach",
            "status": "uploaded",
            "source_url": "https://example.com/poster.png",
        }
    ]
    assert warnings == []


def test_verify_image_url_falls_back_to_ranged_get_when_head_is_forbidden(monkeypatch):
    calls = []

    class FakeResponse:
        status = 206
        headers = {"Content-Type": "image/jpeg"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(request):
        calls.append((request.get_method(), request.headers))
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        return FakeResponse()

    monkeypatch.setattr("capture_to_notion.assets.urlopen", fake_urlopen)

    result = verify_image_url("https://example.com/notion-signed-image.jpg")

    assert calls == [("HEAD", {}), ("GET", {"Range": "bytes=0-0"})]
    assert result == {"ok": True, "status": 206, "content_type": "image/jpeg", "method": "GET"}


def test_verify_image_url_falls_back_when_head_is_not_implemented(monkeypatch):
    calls = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "Image/JPEG"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(request):
        calls.append(request.get_method())
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 501, "Not Implemented", {}, None)
        return FakeResponse()

    monkeypatch.setattr("capture_to_notion.assets.urlopen", fake_urlopen)

    result = verify_image_url("https://example.com/notion-signed-image.jpg")

    assert calls == ["HEAD", "GET"]
    assert result == {"ok": True, "status": 200, "content_type": "Image/JPEG", "method": "GET"}


def test_verify_image_url_returns_failure_when_ranged_get_fails(monkeypatch):
    def fake_urlopen(request):
        if request.get_method() == "HEAD":
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("capture_to_notion.assets.urlopen", fake_urlopen)

    result = verify_image_url("https://example.com/notion-signed-image.jpg")

    assert result == {"ok": False, "status": 404, "content_type": "", "method": "GET"}


def test_asset_operation_from_dict_defaults_record_key_to_cover():
    operation = AssetOperation.from_dict(
        {
            "type": "cover_image",
            "source_url": "https://example.com/cover.jpg",
            "local_cache_path": None,
            "target_field": "封面",
            "action": "attach_external_url",
        }
    )

    assert operation.record_key == "cover"
