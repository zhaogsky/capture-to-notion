from capture_to_notion.view_constraints import derive_view_write_constraints


def test_derives_status_equals_filter_by_property_name():
    result = derive_view_write_constraints(
        {
            "filter": {"property": "状态", "status": {"equals": "Next"}},
        },
        {
            "状态": {"id": "state", "type": "status", "name": "状态"},
        },
    )

    assert result.values == {"状态": "Next"}
    assert result.warnings == []


def test_derives_quick_filter_by_property_id():
    result = derive_view_write_constraints(
        {
            "quick_filters": {
                "state_id": {"status": {"equals": "Next"}},
                "archived_id": {"checkbox": {"equals": False}},
            }
        },
        {
            "状态": {"id": "state_id", "type": "status", "name": "状态"},
            "归档": {"id": "archived_id", "type": "checkbox", "name": "归档"},
        },
    )

    assert result.values == {"状态": "Next", "归档": False}
    assert result.warnings == []


def test_derives_quick_filter_by_view_configuration_property_id():
    result = derive_view_write_constraints(
        {
            "quick_filters": {
                ">\\=P": {"status": {"equals": "Next"}},
            },
            "configuration": {
                "properties": [
                    {"property_id": ">\\=P", "property_name": "状态", "visible": True},
                ]
            },
        },
        {
            "状态": {"type": "status", "name": "状态"},
        },
    )

    assert result.values == {"状态": "Next"}
    assert result.warnings == []



def test_derives_quick_filter_by_decoded_schema_property_id():
    result = derive_view_write_constraints(
        {
            "quick_filters": {
                ">\\=P": {"status": {"equals": "Next"}},
            },
        },
        {
            "状态": {"id": "%3E%5C%3DP", "type": "status", "name": "状态"},
        },
    )

    assert result.values == {"状态": "Next"}
    assert result.warnings == []



def test_derives_status_group_quick_filter_as_single_writable_option():
    result = derive_view_write_constraints(
        {
            "quick_filters": {
                ">\\=P": {"status": {"equals": "In progress"}},
            },
            "configuration": {
                "properties": [
                    {"property_id": ">\\=P", "property_name": "状态", "visible": True},
                ]
            },
        },
        {
            "状态": {
                "type": "status",
                "name": "状态",
                "status": {
                    "options": [
                        {"id": "next", "name": "Next"},
                        {"id": "reading", "name": "Reading"},
                        {"id": "finished", "name": "Finished"},
                    ],
                    "groups": [
                        {"id": "todo", "name": "To-do", "option_ids": ["next"]},
                        {"id": "active", "name": "In progress", "option_ids": ["reading"]},
                        {"id": "done", "name": "Complete", "option_ids": ["finished"]},
                    ],
                },
            },
        },
    )

    assert result.values == {"状态": "Reading"}
    assert result.warnings == []



def test_reports_unsupported_compound_or_filter():
    result = derive_view_write_constraints(
        {
            "filter": {
                "or": [
                    {"property": "状态", "status": {"equals": "Next"}},
                    {"property": "状态", "status": {"equals": "Reading"}},
                ]
            }
        },
        {
            "状态": {"id": "state", "type": "status", "name": "状态"},
        },
    )

    assert result.values == {}
    assert result.warnings == ["view_constraint_unsupported:compound_or"]


def test_reports_conflicting_constraints_for_same_property():
    result = derive_view_write_constraints(
        {
            "filter": {
                "and": [
                    {"property": "状态", "status": {"equals": "Next"}},
                    {"property": "状态", "status": {"equals": "Reading"}},
                ]
            }
        },
        {
            "状态": {"id": "state", "type": "status", "name": "状态"},
        },
    )

    assert result.values == {}
    assert result.warnings == ["view_constraint_conflict:状态:Next:Reading"]


def test_exposes_structured_view_constraint_details_for_verification():
    result = derive_view_write_constraints(
        {
            "filter": {
                "and": [
                    {"property": "状态", "status": {"equals": "Next"}},
                    {"property": "分类", "select": {"does_not_equal": "Archive"}},
                ]
            },
            "quick_filters": {"归档": {"checkbox": {"equals": False}}},
        },
        {
            "状态": {"id": "state", "type": "status", "name": "状态"},
            "分类": {"id": "category", "type": "select", "name": "分类"},
            "归档": {"id": "archived", "type": "checkbox", "name": "归档"},
        },
    )

    assert result.to_dict() == {
        "values": {"状态": "Next", "归档": False},
        "warnings": ["view_constraint_unsupported:分类"],
        "unsupported": ["view_constraint_unsupported:分类"],
        "conflicts": [],
    }
