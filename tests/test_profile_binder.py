import pytest

from capture_to_notion.profile_binder import bind_write_profile, resolve_write_profile


def test_bind_write_profile_requires_data_source_in_graph():
    graph = {"data_sources": {}, "views": {}}

    with pytest.raises(ValueError, match="data_source not found"):
        bind_write_profile(
            graph,
            profile_id="program",
            content_type="podcast_episode",
            data_source_id="missing",
            view_id=None,
            field_mapping={"title": "Name"},
            field_sources={"title": "user_binding"},
        )


def test_bind_write_profile_requires_view_in_graph():
    graph = {
        "graph_id": "graph-1",
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {},
    }

    with pytest.raises(ValueError, match="view not found"):
        bind_write_profile(
            graph,
            profile_id="program",
            content_type="podcast_episode",
            data_source_id="ds-1",
            view_id="missing-view",
            field_mapping={"title": "Name"},
            field_sources={"title": "user_binding"},
        )


def test_bind_write_profile_requires_view_to_match_data_source():
    graph = {
        "graph_id": "graph-1",
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "other-ds", "type": "gallery"}},
    }

    with pytest.raises(ValueError, match="view does not target data_source"):
        bind_write_profile(
            graph,
            profile_id="program",
            content_type="podcast_episode",
            data_source_id="ds-1",
            view_id="view-1",
            field_mapping={"title": "Name"},
            field_sources={"title": "user_binding"},
        )


def test_bind_write_profile_records_view_and_data_source():
    graph = {
        "graph_id": "graph-1",
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-1", "type": "gallery"}},
    }

    profile = bind_write_profile(
        graph,
        profile_id="program",
        content_type="podcast_episode",
        data_source_id="ds-1",
        view_id="view-1",
        field_mapping={"title": "Name"},
        field_sources={"title": "user_binding"},
        state_mapping={"field": "Status"},
        parser_profile={"trusted_field_sources": ["user_binding"]},
    )

    write_profile = profile["write_profiles"]["podcast_episode"]
    assert profile["cache_version"] == 2
    assert profile["profile_id"] == "program"
    assert profile["graph_id"] == "graph-1"
    assert write_profile["content_type"] == "podcast_episode"
    assert write_profile["canonical_data_source_id"] == "ds-1"
    assert write_profile["canonical_view_id"] == "view-1"
    assert write_profile["field_mapping"] == {"title": "Name"}
    assert write_profile["field_sources"] == {"title": "user_binding"}
    assert write_profile["state_mapping"] == {"field": "Status"}
    assert write_profile["parser_profile"] == {"trusted_field_sources": ["user_binding"]}


def test_resolve_write_profile_returns_view_backed_target():
    graph = {
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "data_source_id": "ds-1", "type": "gallery", "name": "Episodes"}},
    }
    profile = {
        "write_profiles": {
            "podcast_episode": {
                "canonical_data_source_id": "ds-1",
                "canonical_view_id": "view-1",
                "field_mapping": {"title": "Name"},
                "field_sources": {"title": "user_binding"},
            }
        }
    }

    resolved = resolve_write_profile(graph, profile, content_type="podcast_episode")

    assert resolved["target_kind"] == "view_backed_data_source"
    assert resolved["data_source_id"] == "ds-1"
    assert resolved["view_id"] == "view-1"
    assert resolved["view_name"] == "Episodes"
    assert resolved["view_type"] == "gallery"
    assert resolved["selection_source"] == "write_profile"
    assert resolved["field_mapping"] == {"title": "Name"}


def test_resolve_write_profile_returns_data_source_target_without_view():
    graph = {
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {},
    }
    profile = {
        "write_profiles": {
            "note": {
                "canonical_data_source_id": "ds-1",
                "canonical_view_id": None,
                "field_mapping": {"title": "Name"},
                "field_sources": {"title": "user_binding"},
            }
        }
    }

    resolved = resolve_write_profile(graph, profile, content_type="note")

    assert resolved["target_kind"] == "data_source"
    assert resolved["data_source_id"] == "ds-1"
    assert resolved["view_id"] is None


def test_resolve_write_profile_returns_none_for_missing_content_type():
    assert resolve_write_profile({"data_sources": {}, "views": {}}, {"write_profiles": {}}, content_type="missing") is None
