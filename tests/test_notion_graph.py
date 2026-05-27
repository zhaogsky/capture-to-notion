from capture_to_notion.notion_graph import (
    normalize_block,
    normalize_database,
    normalize_data_source,
    normalize_page,
    normalize_view,
    property_capability,
    schema_hash,
)


def test_property_capability_classifies_official_types():
    assert property_capability({"type": "title"}) == "writable"
    assert property_capability({"type": "rich_text"}) == "writable"
    assert property_capability({"type": "relation"}) == "writable"
    assert property_capability({"type": "created_time"}) == "read_only"
    assert property_capability({"type": "formula"}) == "computed"
    assert property_capability({"type": "rollup"}) == "computed"
    assert property_capability({"type": "place"}) == "limited"
    assert property_capability({"type": "unknown"}) == "unsupported"


def test_schema_hash_is_stable_for_key_order():
    assert schema_hash({"B": {"type": "date"}, "A": {"type": "title"}}) == schema_hash(
        {"A": {"type": "title"}, "B": {"type": "date"}}
    )


def test_normalize_database_records_data_sources():
    database = normalize_database(
        {
            "id": "db-1",
            "title": [{"plain_text": "Episodes"}],
            "parent": {"type": "page_id", "page_id": "page-1"},
            "is_inline": True,
            "data_sources": [{"id": "ds-1"}],
        }
    )

    assert database["object"] == "database"
    assert database["database_id"] == "db-1"
    assert database["title"] == "Episodes"
    assert database["parent"] == {"type": "page_id", "id": "page-1"}
    assert database["is_inline"] is True
    assert database["data_source_ids"] == ["ds-1"]
    assert database["view_ids"] == []


def test_normalize_data_source_records_schema_and_capabilities():
    data_source = normalize_data_source(
        {
            "id": "ds-1",
            "title": [{"plain_text": "Rows"}],
            "parent": {"type": "database_id", "database_id": "db-1"},
            "database_parent": {"type": "page_id", "page_id": "page-1"},
            "properties": {
                "Name": {"id": "title", "type": "title", "name": "Name"},
                "Created": {"id": "c", "type": "created_time", "name": "Created"},
            },
        }
    )

    assert data_source["object"] == "data_source"
    assert data_source["data_source_id"] == "ds-1"
    assert data_source["database_id"] == "db-1"
    assert data_source["title"] == "Rows"
    assert data_source["parent"] == {"type": "database_id", "id": "db-1"}
    assert data_source["database_parent"] == {"type": "page_id", "id": "page-1"}
    assert data_source["property_capabilities"] == {"Name": "writable", "Created": "read_only"}
    assert data_source["schema_hash"] == schema_hash(data_source["schema"])
    assert data_source["queryable"] is True
    assert data_source["writable"] is True


def test_normalize_view_records_display_context():
    view = normalize_view(
        {
            "id": "view-1",
            "name": "Episodes",
            "type": "gallery",
            "database_id": "db-1",
            "data_source_id": "ds-1",
            "filter": {"and": []},
            "sorts": [],
            "quick_filters": {"status": {}},
            "configuration": {"gallery": {}},
        },
        location={"type": "page_id", "id": "page-1", "discovered_from": "page_scan"},
    )

    assert view["object"] == "view"
    assert view["view_id"] == "view-1"
    assert view["name"] == "Episodes"
    assert view["type"] == "gallery"
    assert view["database_id"] == "db-1"
    assert view["data_source_id"] == "ds-1"
    assert view["location"] == {"type": "page_id", "id": "page-1", "discovered_from": "page_scan"}
    assert view["filter"] == {"and": []}
    assert view["sorts"] == []
    assert view["quick_filters"] == {"status": {}}
    assert view["configuration"] == {"gallery": {}}


def test_normalize_page_distinguishes_record_page():
    page = normalize_page(
        {
            "id": "page-row-1",
            "parent": {"type": "data_source_id", "data_source_id": "ds-1"},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Row"}]}},
        }
    )

    assert page["object"] == "page"
    assert page["page_id"] == "page-row-1"
    assert page["kind"] == "record_page"
    assert page["title"] == "Row"
    assert page["parent"] == {"type": "data_source_id", "id": "ds-1"}
    assert page["property_values"] == {"Name": {"type": "title", "title": [{"plain_text": "Row"}]}}
    assert page["block_ids"] == []


def test_normalize_page_distinguishes_container_page():
    page = normalize_page({"id": "page-1", "parent": {"type": "workspace", "workspace": True}, "properties": {}})

    assert page["kind"] == "container_page"
    assert page["parent"] == {"type": "workspace", "id": "workspace"}


def test_normalize_block_records_parent_page_and_child_database():
    block = normalize_block(
        {
            "id": "block-1",
            "type": "child_database",
            "parent": {"type": "page_id", "page_id": "page-1"},
            "has_children": False,
            "child_database": {"title": "Episodes"},
        }
    )

    assert block == {
        "object": "block",
        "block_id": "block-1",
        "type": "child_database",
        "parent_page_id": "page-1",
        "has_children": False,
        "child_database": {"title": "Episodes"},
    }
