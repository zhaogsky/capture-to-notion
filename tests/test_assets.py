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
            "uploaded_name": "cover.jpg",
            "mime_type": "image/jpeg",
            "local_cache_path": str(cache_path),
            "file_upload_id": "file-1",
        }
    ]
    assert warnings == []


def test_execute_asset_operations_upload_unavailable_does_not_fall_back_to_external_url(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = FakeUploadAdapter(upload_result=None)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert cache_path.read_bytes() == b"image-bytes"
    assert "cover" not in updated_record
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "upload_unavailable",
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


@pytest.mark.parametrize(
    ("payload", "expected_name", "expected_mime_type"),
    [
        (b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "asset.gif", "image/gif"),
        (b"%PDF-1.7\n%file-bytes", "asset.pdf", "application/pdf"),
        (b"\x00\x00\x00 ftypavif\x00\x00\x00\x00", "asset.avif", "image/avif"),
    ],
)
def test_execute_asset_operations_infers_generic_file_extension_from_downloaded_bytes(
    tmp_path,
    payload,
    expected_name,
    expected_mime_type,
):
    cache_path = tmp_path / "assets" / "asset.bin"
    uploaded = {"type": "file_upload", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Document"},
        [
            make_operation(
                "download_and_attach",
                source_url="https://example.com/download/asset",
                local_cache_path=str(cache_path),
                record_key="attachment",
            )
        ],
        adapter,
        downloader=lambda url: payload,
    )

    inferred_path = tmp_path / "assets" / expected_name
    assert not cache_path.exists()
    assert inferred_path.read_bytes() == payload
    assert adapter.calls == [(inferred_path, expected_name, expected_mime_type)]
    assert updated_record["attachment"] == uploaded
    assert results[0]["status"] == "uploaded"
    assert results[0]["uploaded_name"] == expected_name
    assert results[0]["mime_type"] == expected_mime_type
    assert results[0]["local_cache_path"] == str(inferred_path)
    assert results[0]["file_upload_id"] == "file-1"
    assert warnings == []


def test_execute_asset_operations_infers_image_extension_from_downloaded_bytes(tmp_path):
    cache_path = tmp_path / "assets" / "author-picture.bin"
    uploaded = {"type": "file_upload", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [
            make_operation(
                "download_and_attach",
                source_url="https://example.com/author/2248404",
                local_cache_path=str(cache_path),
            )
        ],
        adapter,
        downloader=lambda url: b"\xff\xd8\xff\xe0\x00\x10JFIF\x00image-bytes",
    )

    inferred_path = tmp_path / "assets" / "author-picture.jpg"
    assert not cache_path.exists()
    assert inferred_path.read_bytes() == b"\xff\xd8\xff\xe0\x00\x10JFIF\x00image-bytes"
    assert adapter.calls == [(inferred_path, "author-picture.jpg", "image/jpeg")]
    assert updated_record["cover"] == uploaded
    assert results[0]["status"] == "uploaded"
    assert warnings == []


def test_execute_asset_operations_infers_image_extension_from_existing_cache(tmp_path):
    cache_path = tmp_path / "assets" / "author-picture.bin"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00cached-image")
    uploaded = {"type": "file_upload", "file_upload": {"id": "file-1"}}
    adapter = FakeUploadAdapter(upload_result=uploaded)

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [
            make_operation(
                "download_and_attach",
                source_url="https://example.com/author/2248404",
                local_cache_path=str(cache_path),
            )
        ],
        adapter,
        downloader=lambda url: (_ for _ in ()).throw(RuntimeError("should not download")),
    )

    inferred_path = tmp_path / "assets" / "author-picture.jpg"
    assert not cache_path.exists()
    assert inferred_path.read_bytes() == b"\xff\xd8\xff\xe0\x00\x10JFIF\x00cached-image"
    assert adapter.calls == [(inferred_path, "author-picture.jpg", "image/jpeg")]
    assert updated_record["cover"] == uploaded
    assert results[0]["status"] == "uploaded"
    assert warnings == []


def test_execute_asset_operations_upload_failure_does_not_fall_back_to_external_url(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = FakeUploadAdapter(upload_error=RuntimeError("upload failed"))

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert "cover" not in updated_record
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "upload_failed",
            "source_url": "https://example.com/cover.jpg",
        }
    ]
    assert warnings == ["asset_upload_failed:cover:https://example.com/cover.jpg"]


def test_execute_asset_operations_download_and_attach_removes_source_url_until_upload_succeeds(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    adapter = FakeUploadAdapter(upload_error=RuntimeError("upload failed"))

    updated_record, results, warnings = execute_asset_operations(
        {"title": "Book", "cover": "https://example.com/cover.jpg"},
        [make_operation("download_and_attach", local_cache_path=str(cache_path))],
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    assert updated_record == {"title": "Book"}
    assert results[0]["status"] == "upload_failed"
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


def test_execute_asset_operations_cache_write_failure_does_not_fall_back_to_external_url(tmp_path, monkeypatch):
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

    assert "cover" not in updated_record
    assert results[0]["status"] == "download_failed"
    assert warnings == ["asset_download_failed:cover:https://example.com/cover.jpg"]
    assert adapter.calls == []


def test_execute_asset_operations_download_failure_does_not_fall_back_to_external_url(tmp_path):
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
    assert "cover" not in updated_record
    assert results == [
        {
            "field": "cover",
            "action": "download_and_attach",
            "status": "download_failed",
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
            "uploaded_name": "poster.png",
            "mime_type": "image/png",
            "local_cache_path": str(cache_path),
            "file_upload_id": "file-1",
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
