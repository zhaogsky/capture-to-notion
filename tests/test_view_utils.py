from copy import deepcopy

from capture_to_notion.view_utils import remap_view_property_references


def test_remap_view_property_references_updates_configuration_sorts_filter_and_quick_filters():
    source_schema = {
        "主题": {"id": "src-title", "type": "title"},
        "状态": {"id": "src-status", "type": "status"},
    }
    target_schema = {
        "主题": {"id": "dst-title", "type": "title"},
        "状态": {"id": "dst-status", "type": "status"},
    }
    view = {
        "type": "gallery",
        "configuration": {
            "properties": [
                {"property_id": "src-title", "visible": True},
                {"property_id": "src-status", "visible": False},
            ]
        },
        "sorts": [{"property": "src-status", "direction": "ascending"}],
        "filter": {
            "and": [
                {"property": "src-title", "title": {"is_not_empty": True}},
                {"property": "src-status", "status": {"equals": "已完成"}},
            ]
        },
        "quick_filters": {"filters": [{"property": "src-status", "status": {"equals": "已完成"}}]},
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"]["properties"] == [
        {"property_id": "dst-title", "visible": True},
        {"property_id": "dst-status", "visible": False},
    ]
    assert remapped["sorts"] == [{"property": "dst-status", "direction": "ascending"}]
    assert remapped["filter"] == {
        "and": [
            {"property": "dst-title", "title": {"is_not_empty": True}},
            {"property": "dst-status", "status": {"equals": "已完成"}},
        ]
    }
    assert remapped["quick_filters"] == {"filters": [{"property": "dst-status", "status": {"equals": "已完成"}}]}


def test_remap_view_property_references_drops_unmapped_property_configuration_entries():
    source_schema = {"主题": {"id": "src-title", "type": "title"}, "缺失": {"id": "src-missing", "type": "rich_text"}}
    target_schema = {"主题": {"id": "dst-title", "type": "title"}}
    view = {
        "configuration": {
            "properties": [
                {"property_id": "src-title", "visible": True},
                {"property_id": "src-missing", "visible": True},
            ]
        },
        "sorts": [{"property": "src-missing", "direction": "ascending"}],
        "filter": {"property": "src-missing", "rich_text": {"is_not_empty": True}},
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"]["properties"] == [{"property_id": "dst-title", "visible": True}]
    assert remapped["sorts"] == []
    assert remapped["filter"] == {}
    assert remapped["warnings"] == [
        {"code": "view_property_not_mapped", "source_property_id": "src-missing", "source_property_name": "缺失"}
    ]


def test_remap_view_property_references_returns_single_deep_copied_view_dict_with_warnings():
    source_schema = {"主题": {"id": "src-title", "type": "title"}, "缺失": {"id": "src-missing", "type": "rich_text"}}
    target_schema = {"主题": {"id": "dst-title", "type": "title"}}
    view = {
        "configuration": {"properties": [{"property_id": "src-title", "visible": True}]},
        "filter": {
            "or": [
                {"property": "src-title", "title": {"is_not_empty": True}},
                {"property": "src-missing", "rich_text": {"is_not_empty": True}},
            ]
        },
        "untouched": {"nested": ["value"]},
    }
    original = deepcopy(view)

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert isinstance(remapped, dict)
    assert view == original
    assert remapped is not view
    assert remapped["untouched"] is not view["untouched"]
    assert remapped["filter"] == {"or": [{"property": "dst-title", "title": {"is_not_empty": True}}]}
    assert remapped["warnings"] == [
        {"code": "view_property_not_mapped", "source_property_id": "src-missing", "source_property_name": "缺失"}
    ]


def test_remap_view_property_references_drops_unmapped_quick_filters_and_dedupes_warning():
    source_schema = {"状态": {"id": "src-status", "type": "status"}, "缺失": {"id": "src-missing", "type": "rich_text"}}
    target_schema = {"状态": {"id": "dst-status", "type": "status"}}
    view = {
        "quick_filters": {
            "filters": [
                {"property": "src-missing", "rich_text": {"is_not_empty": True}},
                {"and": [{"property": "src-missing", "rich_text": {"is_not_empty": True}}]},
            ]
        }
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["quick_filters"] == {"filters": []}
    assert remapped["warnings"] == [
        {"code": "view_property_not_mapped", "source_property_id": "src-missing", "source_property_name": "缺失"}
    ]


def test_remap_view_property_references_returns_empty_filter_when_top_level_compound_entries_removed():
    source_schema = {"缺失": {"id": "src-missing", "type": "rich_text"}}
    target_schema = {}
    view = {"filter": {"and": [{"property": "src-missing", "rich_text": {"is_not_empty": True}}]}}

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["filter"] == {}
    assert remapped["warnings"] == [
        {"code": "view_property_not_mapped", "source_property_id": "src-missing", "source_property_name": "缺失"}
    ]


def test_remap_view_property_references_preserves_nested_null_values():
    source_schema = {"状态": {"id": "src-status", "type": "status"}}
    target_schema = {"状态": {"id": "dst-status", "type": "status"}}
    view = {
        "filter": {
            "property": "src-status",
            "status": {"equals": None},
            "metadata": {"nullable": None},
            "values": ["active", None],
        }
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["filter"] == {
        "property": "dst-status",
        "status": {"equals": None},
        "metadata": {"nullable": None},
        "values": ["active", None],
    }
    assert remapped["warnings"] == []


def test_remap_view_property_references_maps_url_decoded_source_property_ids():
    source_schema = {"状态": {"id": "%7ELMg", "type": "status"}}
    target_schema = {"状态": {"id": "dst-status", "type": "status"}}
    view = {"sorts": [{"property": "~LMg", "direction": "ascending"}]}

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["sorts"] == [{"property": "dst-status", "direction": "ascending"}]
    assert remapped["warnings"] == []



def test_remap_view_property_references_maps_quick_filter_source_property_id_key_to_target_id():
    source_schema = {"状态": {"id": "src-status", "type": "status"}}
    target_schema = {"状态": {"id": "dst-status", "type": "status"}}
    view = {"quick_filters": {"src-status": ["已完成"]}}

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["quick_filters"] == {"dst-status": ["已完成"]}
    assert remapped["warnings"] == []



def test_remap_view_property_references_warns_with_source_id_when_name_reference_missing_in_target():
    source_schema = {"缺失": {"id": "src-missing", "type": "rich_text"}}
    target_schema = {}
    view = {"filter": {"property": "缺失", "rich_text": {"is_not_empty": True}}}

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["filter"] == {}
    assert remapped["warnings"] == [
        {"code": "view_property_not_mapped", "source_property_id": "src-missing", "source_property_name": "缺失"}
    ]



def test_remap_view_property_references_maps_property_name_references_to_target_ids():
    source_schema = {
        "标题": {"id": "src-title", "type": "title"},
        "状态": {"id": "src-status", "type": "status"},
    }
    target_schema = {
        "标题": {"id": "dst-title", "type": "title"},
        "状态": {"id": "dst-status", "type": "status"},
    }
    view = {
        "sorts": [{"property": "状态", "direction": "ascending"}],
        "filter": {"property": "标题", "title": {"is_not_empty": True}},
        "quick_filters": {
            "filters": [{"property": "状态", "status": {"equals": "已完成"}}],
            "状态": ["已完成"],
        },
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["sorts"] == [{"property": "dst-status", "direction": "ascending"}]
    assert remapped["filter"] == {"property": "dst-title", "title": {"is_not_empty": True}}
    assert remapped["quick_filters"] == {
        "filters": [{"property": "dst-status", "status": {"equals": "已完成"}}],
        "dst-status": ["已完成"],
    }
    assert remapped["warnings"] == []



def test_remap_view_property_references_updates_map_by_configuration_key():
    source_schema = {"地点": {"id": "src-place", "type": "rich_text"}}
    target_schema = {"地点": {"id": "dst-place", "type": "rich_text"}}
    view = {"configuration": {"map": {"map_by": "src-place"}}}

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"] == {"map": {"map_by": "dst-place"}}
    assert remapped["warnings"] == []



def test_remap_view_property_references_updates_nested_toggle_column_id_configuration_key():
    source_schema = {"开关": {"id": "src-toggle", "type": "checkbox"}}
    target_schema = {"开关": {"id": "dst-toggle", "type": "checkbox"}}
    view = {"configuration": {"some_nested": {"toggle_column_id": "src-toggle"}}}

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"] == {"some_nested": {"toggle_column_id": "dst-toggle"}}
    assert remapped["warnings"] == []



def test_remap_view_property_references_updates_property_id_suffix_keys_and_preserves_none_values():
    source_schema = {
        "日期": {"id": "src-date", "type": "date"},
        "状态": {"id": "src-status", "type": "status"},
        "封面": {"id": "src-cover", "type": "files"},
    }
    target_schema = {
        "日期": {"id": "dst-date", "type": "date"},
        "状态": {"id": "dst-status", "type": "status"},
        "封面": {"id": "dst-cover", "type": "files"},
    }
    view = {
        "configuration": {
            "timeline": {
                "date_property_id": "src-date",
                "end_date_property_id": "src-date",
                "ordinary_none": None,
            },
            "group_by": {"property_id": "src-status", "empty_group": None},
            "cover": {"property_id": "src-cover"},
        },
        "filter": {
            "and": [
                {"property_id": "src-status", "status": {"equals": None}},
                {"metadata": {"nullable": None}},
            ]
        },
    }

    remapped = remap_view_property_references(view, source_schema, target_schema)

    assert remapped["configuration"] == {
        "timeline": {
            "date_property_id": "dst-date",
            "end_date_property_id": "dst-date",
            "ordinary_none": None,
        },
        "group_by": {"property_id": "dst-status", "empty_group": None},
        "cover": {"property_id": "dst-cover"},
    }
    assert remapped["filter"] == {
        "and": [
            {"property_id": "dst-status", "status": {"equals": None}},
            {"metadata": {"nullable": None}},
        ]
    }
    assert remapped["warnings"] == []
