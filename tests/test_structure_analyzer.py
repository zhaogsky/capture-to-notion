from capture_to_notion.structure_analyzer import analyze_target_structure


def test_analyze_target_structure_groups_properties_by_notion_type():
    structure = {
        "data_sources": {
            "ds-books": {
                "data_source_id": "ds-books",
                "title": "Books",
                "role": "primary",
                "schema": {
                    "Title": {"id": "title", "name": "Title", "type": "title"},
                    "Summary": {"id": "summary", "name": "Summary", "type": "rich_text"},
                    "Published": {"id": "published", "name": "Published", "type": "date"},
                    "Cover": {"id": "cover", "name": "Cover", "type": "files"},
                    "Link": {"id": "link", "name": "Link", "type": "url"},
                    "Pages": {"id": "pages", "name": "Pages", "type": "number"},
                    "Done": {"id": "done", "name": "Done", "type": "checkbox"},
                    "Owner": {"id": "owner", "name": "Owner", "type": "people"},
                    "Email": {"id": "email", "name": "Email", "type": "email"},
                    "Phone": {"id": "phone", "name": "Phone", "type": "phone_number"},
                    "Tags": {
                        "id": "tags",
                        "name": "Tags",
                        "type": "multi_select",
                        "options": [{"name": "A", "color": "blue"}],
                    },
                    "Category": {
                        "id": "category",
                        "name": "Category",
                        "type": "select",
                        "options": [{"name": "Essay", "color": "green"}],
                    },
                    "Workflow": {
                        "id": "workflow",
                        "name": "Workflow",
                        "type": "status",
                        "options": [{"name": "Active", "color": "yellow"}],
                    },
                    "Related": {
                        "id": "related",
                        "name": "Related",
                        "type": "relation",
                        "target_database_id": "db-related",
                    },
                },
            }
        }
    }

    result = analyze_target_structure(structure)

    assert result["risk_flags"] == []
    assert result["structure_complexity"] == {
        "data_source_count": 1,
        "writable_candidate_count": 1,
        "risk_count": 0,
    }

    candidate = result["data_source_candidates"][0]
    property_types = candidate["capabilities"]["property_types"]

    assert candidate["id"] == "ds-books"
    assert candidate["name"] == "Books"
    assert candidate["role"] == "primary"
    assert candidate["capabilities"]["writable"] is True
    assert property_types["title"]["properties"][0]["name"] == "Title"
    assert property_types["rich_text"]["properties"][0]["name"] == "Summary"
    assert property_types["date"]["properties"][0]["name"] == "Published"
    assert property_types["files"]["properties"][0]["name"] == "Cover"
    assert property_types["url"]["properties"][0]["name"] == "Link"
    assert property_types["number"]["properties"][0]["name"] == "Pages"
    assert property_types["checkbox"]["properties"][0]["name"] == "Done"
    assert property_types["people"]["properties"][0]["name"] == "Owner"
    assert property_types["email"]["properties"][0]["name"] == "Email"
    assert property_types["phone_number"]["properties"][0]["name"] == "Phone"
    assert property_types["multi_select"]["properties"][0]["options"] == [{"name": "A", "color": "blue"}]
    assert property_types["select"]["properties"][0]["options"] == [{"name": "Essay", "color": "green"}]
    assert property_types["status"]["properties"][0]["options"] == [{"name": "Active", "color": "yellow"}]
    assert property_types["relation"]["properties"][0]["target_database_id"] == "db-related"



def test_analyze_target_structure_marks_name_pattern_risk_from_policy():
    structure = {
        "data_sources": {
            "ds-nav": {
                "data_source_id": "ds-nav",
                "title": "Navigation Index",
                "schema": {
                    "Title": {"id": "title", "name": "Title", "type": "title"},
                },
            }
        }
    }

    result = analyze_target_structure(
        structure,
        policy={
            "name_risk_patterns": [
                {"flag": "navigation_like_name", "keywords": ["navigation", "index"]}
            ]
        },
    )

    assert result["risk_flags"] == ["navigation_like_name"]
    assert result["data_source_candidates"][0]["risk_flags"] == ["navigation_like_name"]



def test_analyze_target_structure_marks_tracking_shape_without_business_field_mapping():
    structure = {
        "data_sources": {
            "ds-generic": {
                "data_source_id": "ds-generic",
                "title": "Generic Workspace",
                "schema": {
                    "Column A": {"id": "a", "name": "Column A", "type": "title"},
                    "Column B": {
                        "id": "b",
                        "name": "Column B",
                        "type": "status",
                        "options": [{"name": "Queued", "color": "gray"}],
                    },
                    "Column C": {"id": "c", "name": "Column C", "type": "date"},
                    "Column D": {"id": "d", "name": "Column D", "type": "checkbox"},
                },
            }
        }
    }

    result = analyze_target_structure(structure)

    assert result["risk_flags"] == ["tracking_shape"]
    assert result["data_source_candidates"][0]["risk_flags"] == ["tracking_shape"]
