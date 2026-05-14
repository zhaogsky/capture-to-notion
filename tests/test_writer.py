from capture_to_notion.models import AssetOperation, Target, WritePlan
from capture_to_notion.writer import NotionWriterError, apply_write_plan, build_plan_properties


def make_plan(data_source_id="ds-books"):
    return WritePlan(
        plan_id="plan-1",
        content_type="book",
        target=Target(
            page_title="书单",
            page_id="page-books",
            data_source_id=data_source_id,
            confidence="high",
            source="alias_cache",
        ),
        normalized_record={
            "title": "可能性的艺术",
            "state": "想读",
            "cover": "https://example.com/cover.jpg",
        },
        field_mapping={
            "title": "名称",
            "state": "阅读状态",
            "cover": "封面",
        },
        operations=[{"type": "create_or_update_page", "data_source_id": data_source_id}],
        asset_operations=[
            AssetOperation(
                type="cover",
                source_url="https://example.com/cover.jpg",
                local_cache_path=None,
                target_field="封面",
                action="attach_external_url",
            )
        ],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )


def make_target_structure():
    return {
        "data_sources": {
            "books": {
                "data_source_id": "ds-books",
                "schema": {
                    "名称": {"type": "title"},
                    "阅读状态": {"type": "status"},
                    "封面": {"type": "files"},
                    "作者": {"type": "relation", "target_database_id": "ds-authors"},
                },
            }
        }
    }


class FakeApplyAdapter:
    def __init__(self, relation_responses=None, upload_result=None, upload_error: Exception | None = None):
        self.calls = []
        self.relation_responses = relation_responses or {}
        self.relation_calls = []
        self.upload_result = upload_result
        self.upload_error = upload_error
        self.upload_calls = []

    def create_page(self, data_source_id, properties):
        self.calls.append(("create_page", data_source_id, properties))
        return {"id": "new-page", "url": "https://notion.so/new-page"}

    def update_page(self, page_id, properties):
        self.calls.append(("update_page", page_id, properties))
        return {"id": page_id, "url": f"https://notion.so/{page_id}"}

    def query_database_title_exact(self, database_id, title):
        self.relation_calls.append((database_id, title))
        return self.relation_responses.get((database_id, title), [])

    def upload_file_for_property(self, path, name, mime_type):
        self.upload_calls.append((path, name, mime_type))
        if self.upload_error is not None:
            raise self.upload_error
        return self.upload_result


def test_build_plan_properties_uses_matching_data_source_schema():
    assert build_plan_properties(make_plan(), make_target_structure()) == {
        "名称": {"title": [{"text": {"content": "可能性的艺术"}}]},
        "阅读状态": {"status": {"name": "想读"}},
        "封面": {
            "files": [
                {
                    "type": "external",
                    "name": "cover.jpg",
                    "external": {"url": "https://example.com/cover.jpg"},
                }
            ]
        },
    }


def test_build_plan_properties_applies_state_mapping():
    plan = make_plan()
    plan.normalized_record["state"] = "initialized"
    target_structure = make_target_structure()
    target_structure["state_mapping"] = {
        "field": "阅读状态",
        "values": {"initialized": "Reading"},
    }

    assert build_plan_properties(plan, target_structure)["阅读状态"] == {
        "status": {"name": "Reading"}
    }


def test_build_plan_properties_raises_when_target_schema_missing():
    target_structure = {"data_sources": {}}

    try:
        build_plan_properties(make_plan(), target_structure)
    except NotionWriterError as exc:
        assert "ds-books" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")


def test_build_plan_properties_raises_when_plan_has_no_data_source_id():
    target_structure = {"data_sources": {}}
    plan = make_plan(data_source_id=None)

    try:
        build_plan_properties(plan, target_structure)
    except NotionWriterError as exc:
        assert "data_source_id" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")


def test_apply_write_plan_creates_page_with_built_properties():
    plan = make_plan()
    plan.warnings = ["check title"]
    adapter = FakeApplyAdapter()

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.calls == [
        (
            "create_page",
            "ds-books",
            {
                "名称": {"title": [{"text": {"content": "可能性的艺术"}}]},
                "阅读状态": {"status": {"name": "想读"}},
                "封面": {
                    "files": [
                        {
                            "type": "external",
                            "name": "cover.jpg",
                            "external": {"url": "https://example.com/cover.jpg"},
                        }
                    ]
                },
            },
        )
    ]
    assert result == {
        "plan_id": "plan-1",
        "applied": True,
        "results": [
            {
                "type": "create_or_update_page",
                "action": "create_page",
                "page_id": "new-page",
                "url": "https://notion.so/new-page",
            }
        ],
        "asset_results": [
            {
                "field": "cover",
                "action": "attach_external_url",
                "status": "external_url",
                "source_url": "https://example.com/cover.jpg",
            }
        ],
        "warnings": ["check title"],
    }


def test_apply_write_plan_resolves_author_relation_before_building_properties():
    plan = make_plan()
    plan.normalized_record["author"] = "刘瑜"
    plan.field_mapping["author"] = "作者"
    adapter = FakeApplyAdapter(relation_responses={("ds-authors", "刘瑜"): [{"id": "author-page-1"}]})

    result = apply_write_plan(plan, make_target_structure(), adapter)

    properties = adapter.calls[0][2]
    assert properties["作者"] == {"relation": [{"id": "author-page-1"}]}
    assert adapter.relation_calls == [("ds-authors", "刘瑜")]
    assert result["warnings"] == []


def test_apply_write_plan_skips_unresolved_author_relation_and_returns_warning():
    plan = make_plan()
    plan.normalized_record["author"] = "不存在"
    plan.field_mapping["author"] = "作者"
    adapter = FakeApplyAdapter()

    result = apply_write_plan(plan, make_target_structure(), adapter)

    properties = adapter.calls[0][2]
    assert "作者" not in properties
    assert result["warnings"] == ["relation_unresolved:author:不存在"]


def test_apply_write_plan_resolves_schema_driven_relation_key_before_building_properties():
    plan = make_plan()
    plan.normalized_record["contributor"] = "张三"
    plan.field_mapping["contributor"] = "贡献者"
    target_structure = make_target_structure()
    target_structure["data_sources"]["books"]["schema"]["贡献者"] = {
        "type": "relation",
        "target_database_id": "ds-contributors",
    }
    adapter = FakeApplyAdapter(
        relation_responses={("ds-contributors", "张三"): [{"id": "contributor-page-1"}]}
    )

    result = apply_write_plan(plan, target_structure, adapter)

    properties = adapter.calls[0][2]
    assert properties["贡献者"] == {"relation": [{"id": "contributor-page-1"}]}
    assert adapter.relation_calls == [("ds-contributors", "张三")]
    assert result["warnings"] == []


def test_apply_write_plan_uses_uploaded_cover_file_object(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    uploaded = {"type": "file_upload", "name": "cover.jpg", "file_upload": {"id": "file-1"}}
    plan = make_plan()
    plan.asset_operations = [
        AssetOperation(
            type="cover_image",
            source_url="https://example.com/cover.jpg",
            local_cache_path=str(cache_path),
            target_field="封面",
            action="download_and_attach",
        )
    ]
    adapter = FakeApplyAdapter(upload_result=uploaded)

    result = apply_write_plan(
        plan,
        make_target_structure(),
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    properties = adapter.calls[0][2]
    assert properties["封面"] == {"files": [uploaded]}
    assert result["asset_results"][0]["status"] == "uploaded"
    assert result["warnings"] == []


def test_apply_write_plan_falls_back_to_external_cover_when_upload_unavailable(tmp_path):
    cache_path = tmp_path / "covers" / "cover.jpg"
    plan = make_plan()
    plan.asset_operations = [
        AssetOperation(
            type="cover_image",
            source_url="https://example.com/cover.jpg",
            local_cache_path=str(cache_path),
            target_field="封面",
            action="download_and_attach",
        )
    ]
    adapter = FakeApplyAdapter(upload_result=None)

    result = apply_write_plan(
        plan,
        make_target_structure(),
        adapter,
        downloader=lambda url: b"image-bytes",
    )

    properties = adapter.calls[0][2]
    assert properties["封面"] == {
        "files": [
            {
                "type": "external",
                "name": "cover.jpg",
                "external": {"url": "https://example.com/cover.jpg"},
            }
        ]
    }
    assert result["asset_results"][0]["status"] == "external_url"
    assert result["warnings"] == ["asset_upload_unavailable:cover:https://example.com/cover.jpg"]


def test_apply_write_plan_updates_page_when_operation_has_page_id():
    plan = make_plan()
    plan.operations = [
        {"type": "create_or_update_page", "data_source_id": "ds-books", "page_id": "page-123"}
    ]
    adapter = FakeApplyAdapter()

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.calls[0][0] == "update_page"
    assert adapter.calls[0][1] == "page-123"
    assert result["results"] == [
        {
            "type": "create_or_update_page",
            "action": "update_page",
            "page_id": "page-123",
            "url": "https://notion.so/page-123",
        }
    ]


def test_apply_write_plan_rejects_empty_operations_and_does_not_call_adapter():
    plan = make_plan()
    plan.operations = []
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, make_target_structure(), adapter)
    except NotionWriterError as exc:
        assert "operations" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert adapter.calls == []


def test_apply_write_plan_rejects_unsupported_operation():
    plan = make_plan()
    plan.operations = [{"type": "delete_page"}]
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, make_target_structure(), adapter)
    except NotionWriterError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert adapter.calls == []


def test_apply_write_plan_rejects_missing_operation_data_source_id():
    plan = make_plan()
    plan.operations = [{"type": "create_or_update_page"}]
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, make_target_structure(), adapter)
    except NotionWriterError as exc:
        assert "data_source_id" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert adapter.calls == []


def test_apply_write_plan_rejects_mismatched_operation_data_source_id():
    plan = make_plan()
    plan.operations = [
        {"type": "create_or_update_page", "data_source_id": "ds-other"}
    ]
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, make_target_structure(), adapter)
    except NotionWriterError as exc:
        assert "data_source_id" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert adapter.calls == []


def test_apply_write_plan_prevalidates_all_operations_before_adapter_calls():
    plan = make_plan()
    plan.operations = [
        {"type": "create_or_update_page", "data_source_id": "ds-books"},
        {"type": "create_or_update_page", "data_source_id": "ds-other"},
    ]
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, make_target_structure(), adapter)
    except NotionWriterError as exc:
        assert "data_source_id" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert adapter.calls == []


def test_apply_write_plan_allows_non_cover_files_asset_as_only_writable_output():
    plan = make_plan()
    plan.normalized_record = {}
    plan.field_mapping = {"attachment": "附件"}
    plan.asset_operations = [
        AssetOperation(
            type="file",
            source_url="https://example.com/file.pdf",
            local_cache_path=None,
            target_field="附件",
            action="attach_external_url",
            record_key="attachment",
        )
    ]
    target_structure = make_target_structure()
    target_structure["data_sources"]["books"]["schema"]["附件"] = {"type": "files"}
    adapter = FakeApplyAdapter()

    result = apply_write_plan(plan, target_structure, adapter)

    assert result["applied"] is True
    assert adapter.calls[0][2] == {
        "附件": {
            "files": [
                {
                    "type": "external",
                    "name": "file.pdf",
                    "external": {"url": "https://example.com/file.pdf"},
                }
            ]
        }
    }


def test_apply_write_plan_rejects_empty_properties():
    plan = make_plan()
    plan.normalized_record = {}
    plan.asset_operations = []
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, make_target_structure(), adapter)
    except NotionWriterError as exc:
        assert "properties" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert adapter.calls == []


def test_apply_write_plan_completes_resolved_relation_page_from_operation_mapping():
    plan = make_plan()
    plan.normalized_record["author"] = "刘瑜"
    plan.field_mapping["author"] = "作者"
    plan.completion_operations = [
        {
            "type": "complete_relation_page",
            "source_record_key": "author",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"author_picture": "Author Picture", "country": "国籍"},
            "record": {
                "author_picture": "https://example.com/author.jpg",
                "country": "美国",
            },
            "schema": {
                "Author Picture": {"type": "files"},
                "国籍": {"type": "select"},
            },
            "asset_operations": [],
        }
    ]
    adapter = FakeApplyAdapter(relation_responses={("ds-authors", "刘瑜"): [{"id": "author-page-1"}]})

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.calls[1] == (
        "update_page",
        "author-page-1",
        {
            "Author Picture": {
                "files": [
                    {
                        "type": "external",
                        "name": "author.jpg",
                        "external": {"url": "https://example.com/author.jpg"},
                    }
                ]
            },
            "国籍": {"select": {"name": "美国"}},
        },
    )
    assert result["completion_results"] == [
        {
            "type": "complete_relation_page",
            "action": "update_page",
            "source_record_key": "author",
            "page_id": "author-page-1",
            "url": "https://notion.so/author-page-1",
        }
    ]
    assert result["warnings"] == []


def test_apply_write_plan_skips_completion_when_relation_unresolved():
    plan = make_plan()
    plan.normalized_record["author"] = "不存在"
    plan.field_mapping["author"] = "作者"
    plan.completion_operations = [
        {
            "type": "complete_relation_page",
            "source_record_key": "author",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"country": "国籍"},
            "record": {"country": "美国"},
            "schema": {"国籍": {"type": "select"}},
            "asset_operations": [],
        }
    ]
    adapter = FakeApplyAdapter()

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert len(adapter.calls) == 1
    assert result["completion_results"] == []
    assert result["warnings"] == [
        "relation_unresolved:author:不存在",
        "completion_relation_unresolved:author",
    ]


def test_apply_write_plan_deduplicates_completion_relation_page_ids():
    plan = make_plan()
    plan.normalized_record["author"] = ["刘瑜", "刘瑜"]
    plan.field_mapping["author"] = "作者"
    plan.completion_operations = [
        {
            "type": "complete_relation_page",
            "source_record_key": "author",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"country": "国籍"},
            "record": {"country": "美国"},
            "schema": {"国籍": {"type": "select"}},
            "asset_operations": [],
        }
    ]
    adapter = FakeApplyAdapter(relation_responses={("ds-authors", "刘瑜"): [{"id": "author-page-1"}]})

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.calls.count(("update_page", "author-page-1", {"国籍": {"select": {"name": "美国"}}})) == 1
    assert result["completion_results"] == [
        {
            "type": "complete_relation_page",
            "action": "update_page",
            "source_record_key": "author",
            "page_id": "author-page-1",
            "url": "https://notion.so/author-page-1",
        }
    ]


def test_apply_write_plan_skips_completion_when_source_value_is_not_page_id():
    plan = make_plan()
    plan.normalized_record["author_page_id"] = 123
    plan.completion_operations = [
        {
            "type": "complete_relation_page",
            "source_record_key": "author_page_id",
            "target_data_source_id": "ds-authors",
            "field_mapping": {"country": "国籍"},
            "record": {"country": "美国"},
            "schema": {"国籍": {"type": "select"}},
            "asset_operations": [],
        }
    ]
    adapter = FakeApplyAdapter()

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert len(adapter.calls) == 1
    assert result["completion_results"] == []
    assert result["warnings"] == ["completion_invalid_page_id:author_page_id"]


def test_apply_write_plan_skips_completion_when_schema_missing():
    plan = make_plan()
    plan.normalized_record["author"] = "刘瑜"
    plan.field_mapping["author"] = "作者"
    plan.completion_operations = [
        {
            "type": "complete_relation_page",
            "source_record_key": "author",
            "field_mapping": {"country": "国籍"},
            "record": {"country": "美国"},
            "asset_operations": [],
        }
    ]
    adapter = FakeApplyAdapter(relation_responses={("ds-authors", "刘瑜"): [{"id": "author-page-1"}]})

    result = apply_write_plan(plan, make_target_structure(), adapter)

    assert len(adapter.calls) == 1
    assert result["completion_results"] == []
    assert result["warnings"] == ["completion_schema_missing:author"]


def test_apply_write_plan_does_not_execute_assets_when_schema_missing():
    plan = make_plan()
    plan.asset_operations = [
        AssetOperation(
            type="cover_image",
            source_url="https://example.com/cover.jpg",
            local_cache_path="/tmp/cover.jpg",
            target_field="封面",
            action="download_and_attach",
        )
    ]
    adapter = FakeApplyAdapter(upload_result={"type": "file_upload"})
    downloads = []

    def fake_downloader(url: str) -> bytes:
        downloads.append(url)
        return b"image-bytes"

    try:
        apply_write_plan(plan, {"data_sources": {}}, adapter, downloader=fake_downloader)
    except NotionWriterError as exc:
        assert "ds-books" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert downloads == []
    assert adapter.upload_calls == []
    assert adapter.calls == []


def test_apply_write_plan_does_not_execute_assets_when_data_source_id_missing():
    plan = make_plan(data_source_id=None)
    plan.asset_operations = [
        AssetOperation(
            type="cover_image",
            source_url="https://example.com/cover.jpg",
            local_cache_path="/tmp/cover.jpg",
            target_field="封面",
            action="download_and_attach",
        )
    ]
    adapter = FakeApplyAdapter(upload_result={"type": "file_upload"})
    downloads = []

    def fake_downloader(url: str) -> bytes:
        downloads.append(url)
        return b"image-bytes"

    try:
        apply_write_plan(plan, make_target_structure(), adapter, downloader=fake_downloader)
    except NotionWriterError as exc:
        assert "data_source_id" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert downloads == []
    assert adapter.upload_calls == []
    assert adapter.calls == []


def test_apply_write_plan_does_not_execute_assets_when_no_writable_property_exists():
    plan = make_plan()
    plan.normalized_record = {}
    plan.field_mapping = {}
    plan.asset_operations = [
        AssetOperation(
            type="cover_image",
            source_url="https://example.com/cover.jpg",
            local_cache_path="/tmp/cover.jpg",
            target_field="封面",
            action="download_and_attach",
        )
    ]
    adapter = FakeApplyAdapter(upload_result={"type": "file_upload"})
    downloads = []

    def fake_downloader(url: str) -> bytes:
        downloads.append(url)
        return b"image-bytes"

    try:
        apply_write_plan(plan, make_target_structure(), adapter, downloader=fake_downloader)
    except NotionWriterError as exc:
        assert "properties" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")

    assert downloads == []
    assert adapter.upload_calls == []
    assert adapter.calls == []
