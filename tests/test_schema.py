import pytest

from capture_to_notion.schema import (
    PAGE_PROPERTY_VALUE_TYPES,
    PROPERTY_TYPE_BUILDERS,
    READONLY_PROPERTY_TYPES,
    SCHEMA_PROPERTY_TYPES,
    SUPPORTED_TYPES,
    WRITABLE_PROPERTY_TYPES,
    build_properties,
    confirmation_blocking_warnings,
    cover_url_from_page,
    file_urls_from_property,
    normalize_database_schema,
    property_has_value,
    resolve_field_mapping,
    schema_hash,
)


def test_schema_module_does_not_expose_business_field_mapping():
    import capture_to_notion.schema as schema_module

    assert not hasattr(schema_module, "semantic_field_mapping")
    assert not hasattr(schema_module, "SEMANTIC_FIELD_RULES")
    assert not hasattr(schema_module, "FIELD_KEYS")


def test_confirmation_blocking_warnings_uses_explicit_non_blocking_prefixes():
    warnings = [
        "ambiguous_field_mapping:page_count:Page Count,Pages",
        "ambiguous_field_mapping:author:Author,Creator",
    ]

    assert confirmation_blocking_warnings(
        warnings,
        non_blocking_prefixes=["ambiguous_field_mapping:page_count:"],
    ) == ["ambiguous_field_mapping:author:Author,Creator"]


def test_confirmation_blocking_warnings_blocks_everything_without_policy():
    warnings = ["ambiguous_field_mapping:page_count:Page Count,Pages"]

    assert confirmation_blocking_warnings(warnings) == warnings


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


def test_property_value_builders_cover_supported_write_types_without_business_keys():
    mapped_schema = {
        "Primary Text": {"type": "title"},
        "Long Text": {"type": "rich_text"},
        "Metric": {"type": "number"},
        "Workflow State": {"type": "status"},
        "Single Choice": {"type": "select"},
        "Timeline": {"type": "date"},
        "Reference Link": {"type": "url"},
        "Attachments": {"type": "files"},
        "Related Item": {"type": "relation"},
        "Flag": {"type": "checkbox"},
        "Contact Email": {"type": "email"},
        "Contact Phone": {"type": "phone_number"},
        "Assigned People": {"type": "people"},
        "Tags": {"type": "multi_select"},
    }
    business_schema = {
        "Title": {"type": "title"},
        "State": {"type": "status"},
        "ISBN": {"type": "rich_text"},
        "Page Count": {"type": "number"},
        "Author": {"type": "relation"},
        "封面": {"type": "files"},
    }
    schema = {**mapped_schema, **business_schema}
    record = {
        "value_title": "可能性的艺术",
        "value_text": "公共讨论",
        "value_number": 400,
        "value_status": "想读",
        "value_select": "政治",
        "value_date": "2022-01-01",
        "value_url": "https://example.com/book",
        "value_files": "https://example.com/cover.jpg",
        "value_relation": ["page-related"],
        "value_checkbox": True,
        "value_email": "reader@example.com",
        "value_phone": "+86 13900000000",
        "value_people": ["user-1", "user-2"],
        "value_multi_select": ["政治", "历史"],
        "title": "业务标题不应被猜测",
        "state": "业务状态不应被猜测",
        "isbn": "业务 ISBN 不应被猜测",
        "page_count": 999,
        "author": ["business-author"],
        "cover": "https://example.com/business-cover.jpg",
    }
    mapping = {
        "value_title": "Primary Text",
        "value_text": "Long Text",
        "value_number": "Metric",
        "value_status": "Workflow State",
        "value_select": "Single Choice",
        "value_date": "Timeline",
        "value_url": "Reference Link",
        "value_files": "Attachments",
        "value_relation": "Related Item",
        "value_checkbox": "Flag",
        "value_email": "Contact Email",
        "value_phone": "Contact Phone",
        "value_people": "Assigned People",
        "value_multi_select": "Tags",
    }

    properties = build_properties(record, mapping, schema)

    assert {item["type"] for item in mapped_schema.values()} == WRITABLE_PROPERTY_TYPES
    assert set(properties) == set(mapped_schema)
    assert properties["Primary Text"]["title"][0]["text"]["content"] == "可能性的艺术"
    assert properties["Long Text"]["rich_text"][0]["text"]["content"] == "公共讨论"
    assert properties["Metric"] == {"number": 400}
    assert properties["Workflow State"] == {"status": {"name": "想读"}}
    assert properties["Single Choice"] == {"select": {"name": "政治"}}
    assert properties["Timeline"] == {"date": {"start": "2022-01-01"}}
    assert properties["Reference Link"] == {"url": "https://example.com/book"}
    assert properties["Attachments"]["files"][0]["external"]["url"] == "https://example.com/cover.jpg"
    assert properties["Related Item"] == {"relation": [{"id": "page-related"}]}
    assert properties["Flag"] == {"checkbox": True}
    assert properties["Contact Email"] == {"email": "reader@example.com"}
    assert properties["Contact Phone"] == {"phone_number": "+86 13900000000"}
    assert properties["Assigned People"] == {"people": [{"id": "user-1"}, {"id": "user-2"}]}
    assert properties["Tags"] == {"multi_select": [{"name": "政治"}, {"name": "历史"}]}


def test_build_properties_does_not_write_business_fields_without_explicit_mapping():
    schema = {
        "Author": {"type": "relation"},
        "ISBN": {"type": "rich_text"},
        "Page Count": {"type": "number"},
        "Cover": {"type": "files"},
    }
    record = {
        "author": ["page-author"],
        "isbn": "9787559847357",
        "page_count": 400,
        "cover": "https://example.com/cover.jpg",
    }

    assert build_properties(record, {}, schema) == {}


def test_build_properties_coerces_checkbox_strings():
    schema = {"已读": {"type": "checkbox"}}
    field_mapping = {"read": "已读"}

    assert build_properties({"read": "是"}, field_mapping, schema) == {"已读": {"checkbox": True}}
    assert build_properties({"read": "否"}, field_mapping, schema) == {"已读": {"checkbox": False}}



def test_build_properties_coerces_integer_strings_for_number_properties():
    properties = build_properties(
        {"page_count": "1"},
        {"page_count": "页数"},
        {"页数": {"type": "number"}},
    )

    assert properties["页数"] == {"number": 1}


def test_build_properties_ignores_official_readonly_property_types_even_when_mapped():
    schema = {f"Read Only {property_type}": {"type": property_type} for property_type in READONLY_PROPERTY_TYPES}
    field_mapping = {property_type: f"Read Only {property_type}" for property_type in READONLY_PROPERTY_TYPES}
    record = {property_type: "value" for property_type in READONLY_PROPERTY_TYPES}

    assert build_properties(record, field_mapping, schema) == {}



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


def test_build_properties_accepts_raw_notion_property_payload():
    rich_text_payload = {
        "rich_text": [
            {
                "type": "text",
                "text": {"content": "Source", "link": {"url": "https://example.com"}},
                "annotations": {"bold": True},
            }
        ]
    }
    relation_payload = {"relation": [{"id": "page-1"}]}

    assert build_properties(
        {
            "summary": {"$notion": rich_text_payload},
            "related": {"$notion": relation_payload},
        },
        {"summary": "Summary", "related": "Related"},
        {"Summary": {"type": "rich_text"}, "Related": {"type": "relation"}},
    ) == {"Summary": rich_text_payload, "Related": relation_payload}


def test_build_properties_supports_explicit_clear_values():
    schema = {
        "Summary": {"type": "rich_text"},
        "Reference": {"type": "url"},
        "Timeline": {"type": "date"},
        "Metric": {"type": "number"},
        "Single Choice": {"type": "select"},
        "Workflow State": {"type": "status"},
        "Related": {"type": "relation"},
        "Attachments": {"type": "files"},
        "People": {"type": "people"},
        "Tags": {"type": "multi_select"},
        "Flag": {"type": "checkbox"},
    }
    field_mapping = {
        "summary": "Summary",
        "url": "Reference",
        "date": "Timeline",
        "number": "Metric",
        "select": "Single Choice",
        "status": "Workflow State",
        "relation": "Related",
        "files": "Attachments",
        "people": "People",
        "tags": "Tags",
        "flag": "Flag",
    }
    record = {key: {"$clear": True} for key in field_mapping}

    assert build_properties(record, field_mapping, schema) == {
        "Summary": {"rich_text": []},
        "Reference": {"url": None},
        "Timeline": {"date": None},
        "Metric": {"number": None},
        "Single Choice": {"select": None},
        "Workflow State": {"status": None},
        "Related": {"relation": []},
        "Attachments": {"files": []},
        "People": {"people": []},
        "Tags": {"multi_select": []},
        "Flag": {"checkbox": False},
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


def test_property_has_value_uses_official_page_property_value_types():
    assert all(
        property_has_value({"type": property_type, property_type: "value"})
        for property_type in PAGE_PROPERTY_VALUE_TYPES
    )
    assert property_has_value({"type": "unsupported_widget", "unsupported_widget": "value"}) is False
    assert property_has_value({"type": "verification", "verification": {}}) is False



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


def test_normalize_database_schema_preserves_supported_notion_property_types():
    raw_schema = {
        "Title": {"type": "title", "title": {}},
        "Text": {"type": "rich_text", "rich_text": {}},
        "Number": {"type": "number", "number": {}},
        "Select": {"type": "select", "select": {}},
        "Status": {"type": "status", "status": {}},
        "Date": {"type": "date", "date": {}},
        "Url": {"type": "url", "url": {}},
        "Files": {"type": "files", "files": {}},
        "Relation": {"type": "relation", "relation": {"database_id": "db-related", "data_source_id": "ds-related"}},
        "Checkbox": {"type": "checkbox", "checkbox": {}},
        "Email": {"type": "email", "email": {}},
        "Phone": {"type": "phone_number", "phone_number": {}},
    }
    normalized = normalize_database_schema(raw_schema)

    assert {name: value["type"] for name, value in normalized.items()} == {
        "Title": "title",
        "Text": "rich_text",
        "Number": "number",
        "Select": "select",
        "Status": "status",
        "Date": "date",
        "Url": "url",
        "Files": "files",
        "Relation": "relation",
        "Checkbox": "checkbox",
        "Email": "email",
        "Phone": "phone_number",
    }
    assert normalized["Relation"]["target_database_id"] == "db-related"
    assert normalized["Relation"]["target_data_source_id"] == "ds-related"



def test_normalize_database_schema_allows_raw_property_named_properties():
    raw_schema = {
        "properties": {"type": "rich_text", "rich_text": {}},
        "Title": {"type": "title", "title": {}},
    }

    normalized = normalize_database_schema(raw_schema)

    assert normalized["properties"]["type"] == "rich_text"
    assert normalized["Title"]["type"] == "title"



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
    assert PAGE_PROPERTY_VALUE_TYPES == SCHEMA_PROPERTY_TYPES | {"verification"}
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


def test_build_properties_strips_upload_metadata_from_file_upload_object():
    schema = {
        "封面": {"type": "files"},
    }
    field_mapping = {
        "cover": "封面",
    }
    file_object = {
        "type": "file_upload",
        "name": "cover.jpg",
        "mime_type": "image/jpeg",
        "local_cache_path": "/tmp/cover.jpg",
        "file_upload": {"id": "file-upload-id"},
    }

    assert build_properties({"cover": file_object}, field_mapping, schema) == {
        "封面": {
            "files": [
                {
                    "type": "file_upload",
                    "name": "cover.jpg",
                    "file_upload": {"id": "file-upload-id"},
                }
            ]
        }
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
