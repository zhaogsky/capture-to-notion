import pytest

from capture_to_notion.schema import (
    PROPERTY_TYPE_BUILDERS,
    READONLY_PROPERTY_TYPES,
    SCHEMA_PROPERTY_TYPES,
    SUPPORTED_TYPES,
    WRITABLE_PROPERTY_TYPES,
    build_properties,
    cover_url_from_page,
    file_urls_from_property,
    normalize_database_schema,
    property_has_value,
    resolve_field_mapping,
    schema_hash,
    semantic_field_mapping,
)


def test_build_properties_for_text_status_select_url_and_date():
    schema = {
        "名称": {"type": "title"},
        "摘要": {"type": "rich_text"},
        "阅读状态": {"type": "status"},
        "分类": {"type": "select"},
        "链接": {"type": "url"},
        "出版日期": {"type": "date"},
    }
    field_mapping = {
        "title": "名称",
        "summary": "摘要",
        "state": "阅读状态",
        "category": "分类",
        "url": "链接",
        "published_at": "出版日期",
    }
    record = {
        "title": "可能性的艺术",
        "summary": "一本关于政治和现实可能性的书。",
        "state": "想读",
        "category": "政治",
        "url": "https://example.com/book",
        "published_at": "2024-01-02",
    }

    assert build_properties(record, field_mapping, schema) == {
        "名称": {"title": [{"text": {"content": "可能性的艺术"}}]},
        "摘要": {"rich_text": [{"text": {"content": "一本关于政治和现实可能性的书。"}}]},
        "阅读状态": {"status": {"name": "想读"}},
        "分类": {"select": {"name": "政治"}},
        "链接": {"url": "https://example.com/book"},
        "出版日期": {"date": {"start": "2024-01-02"}},
    }


def test_build_properties_skips_empty_unknown_and_unmapped_values():
    schema = {
        "名称": {"type": "title"},
        "摘要": {"type": "rich_text"},
    }
    field_mapping = {
        "title": "名称",
        "summary": "摘要",
        "missing_schema": "不存在字段",
    }
    record = {
        "title": "可能性的艺术",
        "summary": "",
        "missing_schema": "ignored",
        "unknown": "ignored",
        "none_value": None,
    }

    assert build_properties(record, field_mapping, schema) == {
        "名称": {"title": [{"text": {"content": "可能性的艺术"}}]}
    }


def test_build_properties_supports_date_dict_with_start_and_end():
    schema = {
        "出版日期": {"type": "date"},
    }
    field_mapping = {
        "published_at": "出版日期",
    }
    record = {
        "published_at": {"start": "2024-01-02", "end": "2024-01-03"},
    }

    assert build_properties(record, field_mapping, schema) == {
        "出版日期": {"date": {"start": "2024-01-02", "end": "2024-01-03"}},
    }


def test_build_properties_normalizes_date_dict_end_value():
    schema = {
        "出版日期": {"type": "date"},
    }
    field_mapping = {
        "published_at": "出版日期",
    }
    record = {
        "published_at": {"start": "2024-01-02", "end": 20240103},
    }

    assert build_properties(record, field_mapping, schema) == {
        "出版日期": {"date": {"start": "2024-01-02", "end": "20240103"}},
    }


def test_build_properties_skips_invalid_date_dict_without_start():
    schema = {
        "出版日期": {"type": "date"},
    }
    field_mapping = {
        "published_at": "出版日期",
    }

    assert build_properties(
        {"published_at": {"end": "2024-01-03"}},
        field_mapping,
        schema,
    ) == {}
    assert build_properties(
        {"published_at": {"start": ""}},
        field_mapping,
        schema,
    ) == {}


def test_schema_hash_is_stable_for_field_order():
    left = {
        "名称": {"name": "名称", "type": "title", "id": "title"},
        "阅读状态": {"name": "阅读状态", "type": "status", "id": "status"},
    }
    right = {
        "阅读状态": {"type": "status", "id": "status", "name": "阅读状态"},
        "名称": {"id": "title", "type": "title", "name": "名称"},
    }

    assert schema_hash(left) == schema_hash(right)


def test_normalize_database_schema_includes_multi_select_options():
    database = {
        "properties": {
            "标签": {
                "id": "tag",
                "type": "multi_select",
                "multi_select": {"options": [{"name": "政治", "color": "red"}]},
            }
        }
    }

    assert normalize_database_schema(database) == {
        "标签": {
            "name": "标签",
            "id": "tag",
            "type": "multi_select",
            "options": [{"name": "政治", "color": "red"}],
        }
    }


def test_property_type_registry_keeps_builder_and_type_sets_consistent():
    assert SUPPORTED_TYPES == SCHEMA_PROPERTY_TYPES
    assert WRITABLE_PROPERTY_TYPES == set(PROPERTY_TYPE_BUILDERS)
    assert READONLY_PROPERTY_TYPES == SCHEMA_PROPERTY_TYPES - WRITABLE_PROPERTY_TYPES
    assert WRITABLE_PROPERTY_TYPES.isdisjoint(READONLY_PROPERTY_TYPES)
    assert {"title", "rich_text", "files", "relation", "checkbox"} <= WRITABLE_PROPERTY_TYPES
    assert {
        "created_by",
        "created_time",
        "formula",
        "last_edited_by",
        "last_edited_time",
        "place",
        "rollup",
        "unique_id",
    } <= READONLY_PROPERTY_TYPES



def test_normalize_database_schema_keeps_writable_and_readonly_property_types():
    database = {
        "properties": {
            "评分": {"id": "rating", "type": "number", "number": {"format": "number"}},
            "已完成": {"id": "done", "type": "checkbox", "checkbox": {}},
            "邮箱": {"id": "email", "type": "email", "email": {}},
            "电话": {"id": "phone", "type": "phone_number", "phone_number": {}},
            "成员": {"id": "people", "type": "people", "people": {}},
            "公式": {"id": "formula", "type": "formula", "formula": {"expression": "1"}},
            "汇总": {"id": "rollup", "type": "rollup", "rollup": {}},
            "创建时间": {"id": "created_time", "type": "created_time", "created_time": {}},
            "创建者": {"id": "created_by", "type": "created_by", "created_by": {}},
            "最后编辑时间": {"id": "last_edited_time", "type": "last_edited_time", "last_edited_time": {}},
            "最后编辑者": {"id": "last_edited_by", "type": "last_edited_by", "last_edited_by": {}},
            "编号": {"id": "unique_id", "type": "unique_id", "unique_id": {"prefix": "BK"}},
        }
    }

    normalized = normalize_database_schema(database)

    assert {name: item["type"] for name, item in normalized.items()} == {
        "公式": "formula",
        "创建者": "created_by",
        "创建时间": "created_time",
        "已完成": "checkbox",
        "成员": "people",
        "最后编辑时间": "last_edited_time",
        "最后编辑者": "last_edited_by",
        "汇总": "rollup",
        "电话": "phone_number",
        "邮箱": "email",
        "评分": "number",
        "编号": "unique_id",
    }


def test_build_properties_for_external_file_url_and_relation_ids():
    schema = {
        "封面": {"type": "files"},
        "作者": {"type": "relation", "target_database_id": "db-authors"},
    }
    field_mapping = {
        "cover": "封面",
        "author": "作者",
    }
    record = {
        "cover": "https://example.com/covers/book.jpg",
        "author": ["author-page-1", "author-page-2"],
    }

    assert build_properties(record, field_mapping, schema) == {
        "封面": {
            "files": [
                {
                    "type": "external",
                    "name": "book.jpg",
                    "external": {"url": "https://example.com/covers/book.jpg"},
                }
            ]
        },
        "作者": {"relation": [{"id": "author-page-1"}, {"id": "author-page-2"}]},
    }


def test_build_properties_accepts_file_dict_and_single_relation_id():
    schema = {
        "封面": {"type": "files"},
        "作者": {"type": "relation", "target_database_id": "db-authors"},
    }
    field_mapping = {
        "cover": "封面",
        "author": "作者",
    }
    record = {
        "cover": {"url": "https://example.com/image", "name": "cover.png"},
        "author": "author-page-1",
    }

    assert build_properties(record, field_mapping, schema) == {
        "封面": {
            "files": [
                {
                    "type": "external",
                    "name": "cover.png",
                    "external": {"url": "https://example.com/image"},
                }
            ]
        },
        "作者": {"relation": [{"id": "author-page-1"}]},
    }


def test_build_properties_accepts_prebuilt_external_file_object():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }
    file_object = {
        "type": "external",
        "name": "cover.jpg",
        "external": {"url": "https://example.com/cover.jpg"},
    }

    assert build_properties({"cover": file_object}, field_mapping, schema) == {
        "封面": {"files": [file_object]}
    }


def test_build_properties_accepts_prebuilt_file_object():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }
    file_object = {
        "type": "file",
        "name": "cover.jpg",
        "file": {"url": "https://example.com/cover.jpg", "expiry_time": "2026-05-10T00:00:00.000Z"},
    }

    assert build_properties({"cover": file_object}, field_mapping, schema) == {
        "封面": {"files": [file_object]}
    }


def test_build_properties_accepts_prebuilt_file_upload_object():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }
    file_object = {
        "type": "file_upload",
        "name": "cover.jpg",
        "file_upload": {"id": "file-upload-id"},
    }

    assert build_properties({"cover": file_object}, field_mapping, schema) == {
        "封面": {"files": [file_object]}
    }


def test_build_properties_skips_arbitrary_file_dict_values():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }

    assert build_properties({"cover": {"unexpected": "value"}}, field_mapping, schema) == {}


def test_build_properties_skips_invalid_external_file_values():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }

    for invalid_cover in ["file:///tmp/a.png", "javascript:alert(1)", "/tmp/a.png", 123]:
        assert build_properties({"cover": invalid_cover}, field_mapping, schema) == {}


def test_build_properties_supports_multi_select_from_list_and_string():
    schema = {
        "标签": {"type": "multi_select"},
    }
    field_mapping = {
        "tag": "标签",
    }

    assert build_properties({"tag": ["政治", "历史"]}, field_mapping, schema) == {
        "标签": {"multi_select": [{"name": "政治"}, {"name": "历史"}]}
    }
    assert build_properties({"tag": "政治"}, field_mapping, schema) == {
        "标签": {"multi_select": [{"name": "政治"}]}
    }


def test_build_properties_accepts_valid_https_external_file_url():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }

    assert build_properties(
        {"cover": "https://example.com/covers/book.jpg"},
        field_mapping,
        schema,
    ) == {
        "封面": {
            "files": [
                {
                    "type": "external",
                    "name": "book.jpg",
                    "external": {"url": "https://example.com/covers/book.jpg"},
                }
            ]
        }
    }


def test_build_properties_supports_number_checkbox_email_phone_and_people():
    schema = {
        "评分": {"type": "number"},
        "已完成": {"type": "checkbox"},
        "邮箱": {"type": "email"},
        "电话": {"type": "phone_number"},
        "成员": {"type": "people"},
    }
    field_mapping = {
        "rating": "评分",
        "done": "已完成",
        "email": "邮箱",
        "phone": "电话",
        "assignees": "成员",
    }
    record = {
        "rating": 9.5,
        "done": True,
        "email": "reader@example.com",
        "phone": "+86 13900000000",
        "assignees": ["user-1", "user-2"],
    }

    assert build_properties(record, field_mapping, schema) == {
        "评分": {"number": 9.5},
        "已完成": {"checkbox": True},
        "邮箱": {"email": "reader@example.com"},
        "电话": {"phone_number": "+86 13900000000"},
        "成员": {"people": [{"id": "user-1"}, {"id": "user-2"}]},
    }


def test_build_properties_skips_readonly_formula_rollup_and_system_fields():
    schema = {
        "自动评分": {"type": "formula"},
        "汇总作者": {"type": "rollup"},
        "创建时间": {"type": "created_time"},
        "创建者": {"type": "created_by"},
        "最后编辑时间": {"type": "last_edited_time"},
        "最后编辑者": {"type": "last_edited_by"},
        "编号": {"type": "unique_id"},
    }
    field_mapping = {
        "score": "自动评分",
        "authors": "汇总作者",
        "created_at": "创建时间",
        "creator": "创建者",
        "edited_at": "最后编辑时间",
        "editor": "最后编辑者",
        "serial": "编号",
    }
    record = {
        "score": 98,
        "authors": "A",
        "created_at": "2026-05-11T00:00:00Z",
        "creator": "user-1",
        "edited_at": "2026-05-11T01:00:00Z",
        "editor": "user-2",
        "serial": "BOOK-1",
    }

    assert build_properties(record, field_mapping, schema) == {}


def test_property_has_value_uses_official_page_property_value_shape():
    assert property_has_value({"type": "title", "title": [{"plain_text": "Title"}]}) is True
    assert property_has_value({"type": "rich_text", "rich_text": []}) is False
    assert property_has_value({"type": "checkbox", "checkbox": False}) is True
    assert property_has_value({"type": "number", "number": 0}) is True
    assert property_has_value({"type": "relation", "relation": [{"id": "page-1"}], "has_more": False}) is True
    assert property_has_value({"type": "rollup", "rollup": {"type": "number", "number": 3}}) is True
    assert property_has_value({"type": "unique_id", "unique_id": {"prefix": "BK", "number": 7}}) is True
    assert property_has_value({"type": "url", "url": ""}) is False
    assert property_has_value({"type": "files", "files": []}) is False


def test_file_urls_from_property_only_reads_notion_files_values():
    property_data = {
        "type": "files",
        "files": [
            {"type": "external", "external": {"url": "https://example.com/external.jpg"}},
            {"type": "file", "file": {"url": "https://secure.notion-static.com/file.jpg"}},
            {"type": "file_upload", "file_upload": {"id": "upload-1"}},
            {"type": "external", "external": {"url": ""}},
        ],
    }

    assert file_urls_from_property(property_data) == [
        "https://example.com/external.jpg",
        "https://secure.notion-static.com/file.jpg",
    ]
    assert file_urls_from_property({"type": "url", "url": "https://example.com/file.jpg"}) == []


def test_cover_url_from_page_reads_notion_page_cover_shapes():
    assert cover_url_from_page({"cover": {"type": "external", "external": {"url": "https://example.com/cover.jpg"}}}) == "https://example.com/cover.jpg"
    assert cover_url_from_page({"cover": {"type": "file", "file": {"url": "https://secure.notion-static.com/cover.jpg"}}}) == "https://secure.notion-static.com/cover.jpg"
    assert cover_url_from_page({"cover": None}) is None


def test_resolve_field_mapping_uses_only_cached_or_explicit_mapping():
    schema = {
        "任意标题字段": {"type": "title"},
        "任意状态字段": {"type": "status"},
        "任意文件字段": {"type": "files"},
    }

    assert resolve_field_mapping(
        schema,
        cached_fields={"title": "任意标题字段", "cover": "missing-field"},
        explicit_mapping={"state": "任意状态字段", "cover": "任意文件字段"},
    ) == {
        "title": "任意标题字段",
        "state": "任意状态字段",
        "cover": "任意文件字段",
    }


def test_resolve_field_mapping_does_not_infer_business_aliases():
    schema = {
        "书名": {"type": "title"},
        "阅读状态": {"type": "status"},
        "封面": {"type": "files"},
    }

    assert resolve_field_mapping(schema) == {}


def test_semantic_field_mapping_uses_real_chinese_schema_names():
    schema = {
        "书名": {"type": "title"},
        "阅读进度": {"type": "status"},
        "封面图": {"type": "files"},
        "豆瓣链接": {"type": "url"},
        "作者": {"type": "relation", "target_database_id": "db-authors"},
        "出版社": {"type": "rich_text"},
        "ISBN": {"type": "rich_text"},
        "备注": {"type": "rich_text"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {
            "title": "书名",
            "state": "阅读进度",
            "cover": "封面图",
            "url": "豆瓣链接",
            "author": "作者",
            "publisher": "出版社",
            "isbn": "ISBN",
            "notes": "备注",
        },
        "warnings": [],
        "requires_confirmation": False,
    }


def test_semantic_field_mapping_maps_multi_select_tag_without_warning():
    schema = {
        "标签": {"type": "multi_select"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {"tag": "标签"},
        "warnings": [],
        "requires_confirmation": False,
    }


def test_semantic_field_mapping_infers_podcast_fields_from_real_names():
    schema = {
        "标题": {"type": "title"},
        "播客": {"type": "relation", "target_database_id": "db-podcasts"},
        "单集链接": {"type": "url"},
        "发布日期": {"type": "date"},
        "状态": {"type": "select"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {
            "title": "标题",
            "podcast": "播客",
            "episode_url": "单集链接",
            "published_at": "发布日期",
            "state": "状态",
        },
        "warnings": [],
        "requires_confirmation": False,
    }


def test_semantic_field_mapping_keeps_type_fallback_for_unknown_names():
    schema = {
        "Name": {"type": "title"},
        "Status": {"type": "status"},
        "Cover": {"type": "files"},
        "Website": {"type": "url"},
        "Created": {"type": "date"},
        "Text": {"type": "rich_text"},
        "Tag": {"type": "select"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {
            "title": "Name",
            "state": "Status",
            "cover": "Cover",
            "url": "Website",
            "date": "Created",
            "notes": "Text",
            "tag": "Tag",
        },
        "warnings": [],
        "requires_confirmation": False,
    }


def test_semantic_field_mapping_does_not_treat_generic_files_as_cover():
    schema = {
        "Name": {"type": "title"},
        "附件": {"type": "files"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {"title": "Name"},
        "warnings": [],
        "requires_confirmation": False,
    }


@pytest.mark.parametrize("field_name", ["图片", "Image"])
def test_semantic_field_mapping_does_not_treat_generic_image_names_as_cover(field_name):
    schema = {
        "Name": {"type": "title"},
        field_name: {"type": "files"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {"title": "Name"},
        "warnings": [],
        "requires_confirmation": False,
    }


def test_semantic_field_mapping_fallback_is_deterministic_across_insertion_orders():
    schema_a = {
        "Name": {"type": "title"},
        "Zeta": {"type": "url"},
        "Alpha": {"type": "url"},
        "Text": {"type": "rich_text"},
        "Tag": {"type": "select"},
    }
    schema_b = {
        "Tag": {"type": "select"},
        "Text": {"type": "rich_text"},
        "Alpha": {"type": "url"},
        "Zeta": {"type": "url"},
        "Name": {"type": "title"},
    }

    result_a = semantic_field_mapping(schema_a)
    result_b = semantic_field_mapping(schema_b)

    expected_fields = {
        "title": "Name",
        "url": "Alpha",
        "notes": "Text",
        "tag": "Tag",
    }

    assert result_a["fields"] == expected_fields
    assert result_b["fields"] == expected_fields
    assert result_a["warnings"] == []
    assert result_b["warnings"] == []
    assert result_a["requires_confirmation"] is False
    assert result_b["requires_confirmation"] is False


def test_semantic_field_mapping_reports_ambiguous_candidates():
    schema = {
        "名称": {"type": "title"},
        "标题": {"type": "title"},
        "作者": {"type": "relation", "target_database_id": "db-authors"},
        "作者页面": {"type": "relation", "target_database_id": "db-authors"},
    }

    result = semantic_field_mapping(schema)

    assert result["fields"] == {
        "title": "名称",
        "author": "作者",
    }
    assert result["warnings"] == [
        "ambiguous_field_mapping:title:名称,标题",
        "ambiguous_field_mapping:author:作者,作者页面",
    ]
    assert result["requires_confirmation"] is True


def test_semantic_field_mapping_does_not_match_alias_with_wrong_type():
    schema = {
        "作者": {"type": "date"},
        "封面": {"type": "url"},
        "阅读状态": {"type": "rich_text"},
    }

    result = semantic_field_mapping(schema)

    assert result == {
        "fields": {
            "date": "作者",
            "url": "封面",
            "notes": "阅读状态",
        },
        "warnings": [],
        "requires_confirmation": False,
    }


def test_semantic_field_mapping_relation_fallback_does_not_overwrite_existing_semantic_key():
    schema = {
        "Text": {"type": "rich_text"},
        "notes": {"type": "relation", "target_database_id": "db-notes"},
    }

    result = semantic_field_mapping(schema)

    assert result["fields"]["notes"] == "Text"
