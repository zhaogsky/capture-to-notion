from capture_to_notion.models import AssetOperation, Target, WritePlan
from capture_to_notion.notion_adapter import NotionApiError
from capture_to_notion.writer import PartialWriteError, NotionWriterError, apply_write_plan, build_plan_properties


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
                type="cover_image",
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
        self.cover_calls = []

    def create_page(self, data_source_id, properties, cover=None, cover_source_url=None):
        self.calls.append(("create_page", data_source_id, properties))
        if cover is not None:
            self.cover_calls.append(("create_page", cover, cover_source_url))
        return {"id": "new-page", "url": "https://notion.so/new-page"}

    def update_page(self, page_id, properties, cover=None, cover_source_url=None):
        self.calls.append(("update_page", page_id, properties))
        if cover is not None:
            self.cover_calls.append(("update_page", cover, cover_source_url))
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


def test_apply_write_plan_rejects_target_page_id_mismatch():
    plan = make_plan()
    target_structure = make_target_structure()
    target_structure["target"] = {"page_id": "page-drifted"}
    adapter = FakeApplyAdapter()

    try:
        apply_write_plan(plan, target_structure, adapter)
    except NotionWriterError as exc:
        assert "page_id" in str(exc)
    else:
        raise AssertionError("expected NotionWriterError")
    assert adapter.calls == []



def make_page_parent_plan(blocks):
    return WritePlan(
        plan_id="plan-page-parent",
        content_type="article",
        target=Target(
            page_title="知识",
            page_id="parent-page",
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_kind="page_parent",
            parent_page_id="parent-page",
        ),
        summary={"title": "DeepSeek V4"},
        normalized_record={"title": "DeepSeek V4"},
        field_mapping={},
        operations=[{"type": "create_child_page", "parent_page_id": "parent-page", "title": "DeepSeek V4", "body_blocks": blocks}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )


def make_existing_page_plan(blocks):
    return WritePlan(
        plan_id="plan-existing-page",
        content_type="article",
        target=Target(
            page_title="知识页",
            page_id="existing-page",
            data_source_id=None,
            confidence="high",
            source="v2_page_graph",
            target_kind="existing_page",
        ),
        summary={"title": "DeepSeek V4"},
        normalized_record={"title": "DeepSeek V4"},
        field_mapping={},
        operations=[{"type": "append_page_content", "page_id": "existing-page", "body_blocks": blocks}],
        asset_operations=[],
        sources=[],
        warnings=[],
        requires_confirmation=False,
        confirmation_reason=None,
    )


class PageParentAdapter:
    def __init__(self, fail_on_append_call=None):
        self.created = []
        self.appended = []
        self.fail_on_append_call = fail_on_append_call
        self.append_calls = 0

    def create_child_page(self, parent_page_id, title, children=None, cover=None):
        self.created.append({"parent_page_id": parent_page_id, "title": title, "children": children or [], "cover": cover})
        return {"id": "created-page", "url": "https://notion.so/created-page"}

    def append_block_children(self, block_id, children):
        self.append_calls += 1
        if self.fail_on_append_call == self.append_calls:
            raise NotionApiError("append failed", status=400, code="validation_error")
        self.appended.append({"block_id": block_id, "children": children})
        return {"results": children}


def paragraph_block(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def test_apply_write_plan_creates_child_page_with_first_100_blocks():
    blocks = [paragraph_block(str(index)) for index in range(3)]
    adapter = PageParentAdapter()

    result = apply_write_plan(make_page_parent_plan(blocks), {"pages": {"parent-page": {"title": "知识"}}}, adapter)

    assert result["results"] == [{"type": "create_child_page", "action": "create_child_page", "page_id": "created-page", "url": "https://notion.so/created-page"}]
    assert adapter.created[0]["parent_page_id"] == "parent-page"
    assert adapter.created[0]["title"] == "DeepSeek V4"
    assert adapter.created[0]["children"] == blocks
    assert adapter.appended == []


def test_apply_write_plan_appends_remaining_blocks_after_create_limit():
    blocks = [paragraph_block(str(index)) for index in range(205)]
    adapter = PageParentAdapter()

    result = apply_write_plan(make_page_parent_plan(blocks), {"pages": {"parent-page": {"title": "知识"}}}, adapter)

    assert result["results"][0]["page_id"] == "created-page"
    assert len(adapter.created[0]["children"]) == 100
    assert [len(call["children"]) for call in adapter.appended] == [100, 5]
    assert all(call["block_id"] == "created-page" for call in adapter.appended)


def test_apply_write_plan_reports_partial_write_when_child_page_append_fails_after_create():
    blocks = [paragraph_block(str(index)) for index in range(205)]
    adapter = PageParentAdapter(fail_on_append_call=1)

    try:
        apply_write_plan(make_page_parent_plan(blocks), {"pages": {"parent-page": {"title": "知识"}}}, adapter)
    except PartialWriteError:
        pass
    else:
        raise AssertionError("expected PartialWriteError")

    assert len(adapter.created) == 1
    assert len(adapter.created[0]["children"]) == 100
    assert adapter.appended == []


def test_apply_write_plan_reports_partial_write_when_existing_page_later_append_fails():
    blocks = [paragraph_block(str(index)) for index in range(205)]
    adapter = PageParentAdapter(fail_on_append_call=2)

    try:
        apply_write_plan(make_existing_page_plan(blocks), {"pages": {"existing-page": {"title": "知识页"}}}, adapter)
    except PartialWriteError:
        pass
    else:
        raise AssertionError("expected PartialWriteError")

    assert [len(call["children"]) for call in adapter.appended] == [100]
    assert adapter.appended[0]["block_id"] == "existing-page"



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


def test_apply_write_plan_sets_page_cover_from_cover_asset_source_url():
    plan = make_plan()
    adapter = FakeApplyAdapter()

    apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.cover_calls == [
        (
            "create_page",
            {"type": "external", "external": {"url": "https://example.com/cover.jpg"}},
            None,
        )
    ]


def test_apply_write_plan_sets_page_cover_from_cover_image_operation_type():
    plan = make_plan()
    plan.asset_operations[0].type = "cover_image"
    plan.asset_operations[0].record_key = "thumbnail"
    adapter = FakeApplyAdapter()

    apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.cover_calls == [
        (
            "create_page",
            {"type": "external", "external": {"url": "https://example.com/cover.jpg"}},
            None,
        )
    ]


def test_apply_write_plan_does_not_set_page_cover_from_non_http_url():
    plan = make_plan()
    plan.asset_operations[0].source_url = "file:///tmp/cover.jpg"
    adapter = FakeApplyAdapter()

    apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.cover_calls == []


def test_apply_write_plan_does_not_set_page_cover_from_http_url_without_host():
    plan = make_plan()
    plan.asset_operations[0].source_url = "https://"
    adapter = FakeApplyAdapter()

    apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.cover_calls == []


def test_apply_write_plan_does_not_set_page_cover_from_http_url_without_hostname():
    plan = make_plan()
    plan.asset_operations[0].source_url = "https://:443/path"
    adapter = FakeApplyAdapter()

    apply_write_plan(plan, make_target_structure(), adapter)

    assert adapter.cover_calls == []


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
    assert adapter.cover_calls == [
        (
            "create_page",
            {"type": "external", "external": {"url": "https://example.com/cover.jpg"}},
            None,
        )
    ]
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
