import json
from pathlib import Path

from capture_to_notion import cli
from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_input(path: Path, data: dict) -> None:
    _write_json(path, data)


def _seed_allowed_target(config) -> None:
    _write_json(
        config.aliases_file,
        {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}},
    )
    _write_json(
        config.targets_dir / "books.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {}},
            "asset_mapping": {},
        },
    )
    _write_json(
        config.graphs_v2_dir / "books.json",
        {
            "cache_version": 2,
            "graph_id": "books",
            "root": {"kind": "page", "id": "page-books"},
            "pages": {"page-books": {"page_id": "page-books", "title": "书单"}},
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "views": {},
        },
    )
    _write_json(
        config.profiles_v2_dir / "books-profile.json",
        {
            "cache_version": 2,
            "profile_id": "books-profile",
            "graph_id": "books",
            "write_profiles": {
                "book": {
                    "canonical_data_source_id": "ds-books",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "user_binding", "state": "user_binding"},
                    "state_mapping": {"field": "阅读状态", "values": {}},
                    "asset_mapping": {},
                    "relation_mapping": {},
                    "parser_profile": {
                        "required_schema_fields": [],
                        "required_value_fields": [],
                        "trusted_field_sources": ["user_binding"],
                    },
                }
            },
        },
    )
    _write_json(
        config.aliases_v2_file,
        {"cache_version": 2, "aliases": {"书单": {"graph_id": "books", "profile_id": "books-profile", "kind": "write_profile"}}},
    )


class ScopedDataSourceSyncAdapter:
    def __init__(self) -> None:
        self.data_source_calls = []

    def retrieve_data_source(self, data_source_id: str) -> dict:
        self.data_source_calls.append(data_source_id)
        return {
            "id": data_source_id,
            "title": [{"plain_text": "Books"}],
            "properties": {
                "书名": {"id": "title", "type": "title", "title": {}},
                "阅读状态": {"id": "status", "type": "status", "status": {"options": []}},
            },
        }


class PodcastChildDatabaseSyncAdapter:
    def __init__(self) -> None:
        self.data_source_calls = []
        self.database_calls = []

    def retrieve_data_source(self, data_source_id: str) -> dict:
        self.data_source_calls.append(data_source_id)
        if data_source_id != "ds-houhulianwang-child":
            raise AssertionError(f"unexpected data_source sync: {data_source_id}")
        return {
            "id": data_source_id,
            "title": [{"plain_text": "后互联网时代的乱弹单集"}],
            "parent": {"type": "database_id", "database_id": "db-houhulianwang-child"},
            "properties": {
                "主题": {"id": "title", "type": "title", "title": {}},
                "状态": {"id": "status", "type": "status", "status": {"options": []}},
                "内容描述": {"id": "description", "type": "rich_text", "rich_text": {}},
            },
        }

    def retrieve_database(self, database_id: str) -> dict:
        self.database_calls.append(database_id)
        if database_id != "db-houhulianwang-child":
            raise AssertionError(f"unexpected database sync: {database_id}")
        return {"id": database_id, "parent": {"type": "page_id", "page_id": "page-houhulianwang"}}


def test_capture_plan_blocks_when_preflight_next_action_is_suggest_target(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    ensure_config()
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(input_path, {"raw_input": "记录一下《可能性的艺术》"})

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 2
    assert not output_path.exists()
    stderr = capsys.readouterr().err
    assert "next_action=scan_target" in stderr
    assert "reason=v2_target_missing" in stderr


def test_capture_plan_blocks_when_preflight_next_action_is_scan_target(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _write_json(config.aliases_file, {"aliases": {"书单": {"type": "page", "page_id": "page-books", "target_id": "books"}}})
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(input_path, {"raw_input": "记录一下《可能性的艺术》", "target_hint": "书单"})

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 2
    assert not output_path.exists()
    stderr = capsys.readouterr().err
    assert "next_action=scan_target" in stderr


def test_capture_plan_blocks_when_context_unverified(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _write_json(config.routes_file, {"routes": {"book": {"preferred_targets": [{"alias": "书单", "confidence": "high"}]}}})
    _seed_allowed_target(config)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(
        input_path,
        {"raw_input": "《可能性的艺术》", "content_type_hint": "book", "target_context_hint": "节目页", "target_scope_hint": "under_page"},
    )

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 2
    assert not output_path.exists()
    stderr = capsys.readouterr().err
    assert "next_action=scan_target" in stderr
    assert "reason=v2_target_missing" in stderr



def test_capture_plan_writes_context_verification_when_allowed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _seed_allowed_target(config)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(
        input_path,
        {"raw_input": "《可能性的艺术》", "target_hint": "书单", "target_context_hint": "书单", "target_scope_hint": "under_page", "content_type_hint": "book"},
    )

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["requires_confirmation"] is False
    plan = json.loads(output_path.read_text(encoding="utf-8"))
    assert plan["preflight_workflow"]["target_resolution"]["data_source_id"] == "ds-books"
    assert plan["preflight_workflow"]["target_resolution"]["source"] == "v2_profile"
    assert plan["preflight_workflow"]["planning"]["next_action"] == "capture_plan"



def test_capture_plan_writes_preflight_workflow_when_allowed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _seed_allowed_target(config)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(input_path, {"raw_input": "《可能性的艺术》", "target_hint": "书单", "content_type_hint": "book"})

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["requires_confirmation"] is False
    plan = json.loads(output_path.read_text(encoding="utf-8"))
    assert plan["preflight_workflow"]["planning"] == {
        "status": "allowed",
        "next_action": "capture_plan",
        "reason": "direct_plan_allowed",
    }



def test_capture_plan_syncs_data_source_cache_then_reruns_preflight(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _write_json(
        config.targets_dir / "books.json",
        {
            "target": {"target_id": "books", "title": "Books"},
            "parser_profile": {
                "book": {
                    "field_mapping": {"title": "书名", "state": "阅读状态"},
                    "required_schema_fields": [],
                    "required_value_fields": [],
                    "trusted_field_sources": ["profile"],
                }
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "fields": {"title": "书名", "state": "阅读状态"},
                    "field_sources": {"title": "profile", "state": "profile"},
                    "schema": {
                        "书名": {"name": "书名", "type": "title"},
                        "阅读状态": {"name": "阅读状态", "type": "status"},
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {}},
            "asset_mapping": {},
        },
    )
    adapter = ScopedDataSourceSyncAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    original_preflight = cli.build_capture_preflight
    calls = {"count": 0}

    def sync_then_real_preflight(capture, cache):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "workflow": {
                    "planning": {
                        "status": "blocked",
                        "next_action": "sync_target_cache",
                        "reason": "target_location_facts_missing",
                    },
                    "target_resolution": {
                        "sync": {
                            "data_source_id": "ds-books",
                            "target_id": "books",
                            "alias": "书单",
                        }
                    },
                }
            }
        return original_preflight(capture, cache)

    monkeypatch.setattr(cli, "build_capture_preflight", sync_then_real_preflight)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(input_path, {"raw_input": "《可能性的艺术》", "target_hint": "书单", "content_type_hint": "book"})

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 2
    assert adapter.data_source_calls == ["ds-books"]
    assert calls["count"] == 2
    assert not output_path.exists()
    assert "v2_target_missing" in capsys.readouterr().err
    synced_graph = json.loads((config.graphs_v2_dir / "books.json").read_text(encoding="utf-8"))
    assert synced_graph["root"] == {"kind": "data_source", "id": "ds-books"}



def test_capture_plan_scoped_sync_prevents_schema_compatible_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _write_json(
        config.routes_file,
        {"routes": {"podcast_episode": {"preferred_targets": [{"alias": "通用播客库", "confidence": "high"}]}}},
    )
    parser_profile = {
        "podcast_episode": {
            "field_mapping": {"title": "主题", "state": "状态", "description": "内容描述"},
            "primary_score_fields": {"title": 20, "state": 10},
            "required_schema_fields": [],
            "required_value_fields": [],
            "trusted_field_sources": ["profile"],
        }
    }
    data_source = {
        "title": "Episodes",
        "role": "primary",
        "content_types": ["podcast_episode"],
        "fields": {"title": "主题", "state": "状态", "description": "内容描述"},
        "field_sources": {"title": "profile", "state": "profile", "description": "profile"},
        "schema": {
            "主题": {"name": "主题", "type": "title"},
            "状态": {"name": "状态", "type": "status"},
            "内容描述": {"name": "内容描述", "type": "rich_text"},
        },
    }
    _write_json(
        config.aliases_file,
        {
            "aliases": {
                "通用播客库": {"type": "data_source", "data_source_id": "ds-generic-podcast", "target_id": "generic-podcast"},
                "后互联网时代的乱弹-单集列表": {
                    "type": "data_source",
                    "data_source_id": "ds-houhulianwang-child",
                    "target_id": "houhulianwang-episodes",
                },
            }
        },
    )
    _write_json(
        config.targets_dir / "generic-podcast.json",
        {
            "target": {
                "target_id": "generic-podcast",
                "title": "通用播客库",
                "data_source_id": "ds-generic-podcast",
                "parent_page_id": "page-generic-podcast",
            },
            "parser_profile": parser_profile,
            "data_sources": {"ds-generic-podcast": {**data_source, "data_source_id": "ds-generic-podcast", "parent_page_id": "page-generic-podcast"}},
        },
    )
    _write_json(
        config.targets_dir / "houhulianwang-episodes.json",
        {
            "target": {"target_id": "houhulianwang-episodes", "title": "后互联网时代的乱弹-单集列表", "data_source_id": "ds-houhulianwang-child"},
            "parser_profile": parser_profile,
            "data_sources": {"ds-houhulianwang-child": {**data_source, "data_source_id": "ds-houhulianwang-child"}},
        },
    )
    adapter = PodcastChildDatabaseSyncAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(
        input_path,
        {
            "raw_input": "《第214期 两部影片的故事》 简介：本期节目总结",
            "target_hint": "后互联网时代的乱弹-单集列表",
            "target_context_hint": "child database under page; parent page id page-houhulianwang",
            "target_scope_hint": "data_source",
            "content_type_hint": "podcast_episode",
        },
    )

    preflight = cli.build_capture_preflight(cli.load_capture_input(str(input_path)), CacheStore(config))
    assert preflight["workflow"]["planning"]["next_action"] == "sync_target_cache"

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 2
    assert adapter.data_source_calls == []
    assert adapter.database_calls == []
    assert not output_path.exists()
    assert "v2_target_missing" in capsys.readouterr().err



def test_v2_capture_plan_syncs_target_cache_then_plans(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _write_json(
        config.graphs_v2_dir / "houhulianwang-episodes.json",
        {
            "cache_version": 2,
            "graph_id": "houhulianwang-episodes",
            "root": {"kind": "data_source", "id": "ds-houhulianwang-child"},
            "data_sources": {
                "ds-houhulianwang-child": {
                    "data_source_id": "ds-houhulianwang-child",
                    "title": "后互联网时代的乱弹单集",
                    "schema": {
                        "主题": {"name": "主题", "type": "title"},
                        "状态": {"name": "状态", "type": "status"},
                        "内容描述": {"name": "内容描述", "type": "rich_text"},
                    },
                }
            },
            "views": {},
        },
    )
    _write_json(
        config.profiles_v2_dir / "houhulianwang-profile.json",
        {
            "cache_version": 2,
            "profile_id": "houhulianwang-profile",
            "graph_id": "houhulianwang-episodes",
            "write_profiles": {
                "podcast_episode": {
                    "canonical_data_source_id": "ds-houhulianwang-child",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "主题", "state": "状态", "description": "内容描述"},
                    "field_sources": {"title": "user_binding", "state": "user_binding", "description": "user_binding"},
                    "state_mapping": {"field": "状态", "values": {}},
                    "asset_mapping": {},
                    "relation_mapping": {},
                    "parser_profile": {"required_schema_fields": [], "required_value_fields": [], "trusted_field_sources": ["user_binding"]},
                }
            },
        },
    )
    _write_json(
        config.aliases_v2_file,
        {
            "cache_version": 2,
            "aliases": {
                "后互联网时代的乱弹-单集列表": {
                    "graph_id": "houhulianwang-episodes",
                    "profile_id": "houhulianwang-profile",
                    "kind": "write_profile",
                }
            },
        },
    )
    adapter = PodcastChildDatabaseSyncAdapter()
    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(lambda cls, config: adapter))
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(
        input_path,
        {
            "raw_input": "《第214期 两部影片的故事》 简介：本期节目总结",
            "target_hint": "后互联网时代的乱弹-单集列表",
            "target_context_hint": "child database under page; parent page id page-houhulianwang",
            "target_scope_hint": "data_source",
            "content_type_hint": "podcast_episode",
        },
    )

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 0
    assert adapter.data_source_calls == ["ds-houhulianwang-child"]
    assert adapter.database_calls == ["db-houhulianwang-child"]
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["target"]["data_source_id"] == "ds-houhulianwang-child"
    assert output_path.exists()
    graph = json.loads((config.graphs_v2_dir / "houhulianwang-episodes.json").read_text(encoding="utf-8"))
    assert graph["data_sources"]["ds-houhulianwang-child"]["parent_page_id"] == "page-houhulianwang"


def test_capture_plan_sync_target_cache_requires_explicit_scope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    ensure_config()
    original_preflight = cli.build_capture_preflight

    def missing_scope_preflight(capture, cache):
        return {
            "workflow": {
                "planning": {
                    "status": "blocked",
                    "next_action": "sync_target_cache",
                    "reason": "target_location_facts_missing",
                },
                "target_resolution": {"sync": {"target_id": "books", "alias": "书单"}},
            }
        }

    monkeypatch.setattr(cli, "build_capture_preflight", missing_scope_preflight)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(input_path, {"raw_input": "《可能性的艺术》", "target_hint": "书单", "content_type_hint": "book"})

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 2
    assert not output_path.exists()
    assert "next_action=sync_target_cache" in capsys.readouterr().err
    monkeypatch.setattr(cli, "build_capture_preflight", original_preflight)



def test_capture_plan_with_sufficient_cache_does_not_construct_adapter(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    config = ensure_config()
    _seed_allowed_target(config)

    def fail_from_config(cls, config):
        raise AssertionError("capture plan must not sync when cache facts are sufficient")

    monkeypatch.setattr(cli.NotionAdapter, "from_config", classmethod(fail_from_config))
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "plan.json"
    _write_input(input_path, {"raw_input": "《可能性的艺术》", "target_hint": "书单", "content_type_hint": "book"})

    exit_code = cli.main(["capture", "plan", "--input", str(input_path), "--output", str(output_path), "--compact"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["requires_confirmation"] is False
    assert output_path.exists()
