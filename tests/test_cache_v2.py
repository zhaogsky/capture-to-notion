from capture_to_notion.cache_v2 import CacheV2Store


def test_v2_store_writes_and_reads_graph(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    graph = {"cache_version": 2, "graph_id": "graph-1", "root": {"kind": "page", "id": "page-1"}}
    store.write_graph("graph-1", graph)

    assert store.read_graph("graph-1") == graph
    assert not (tmp_path / "targets" / "graph-1.json").exists()


def test_v2_store_writes_and_reads_profile(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    profile = {"cache_version": 2, "profile_id": "profile-1", "graph_id": "graph-1", "write_profiles": {}}
    store.write_profile("profile-1", profile)

    assert store.read_profile("profile-1") == profile


def test_v2_store_writes_and_reads_plan(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    plan = {"cache_version": 2, "plan_id": "plan-1", "target": {}}
    store.write_plan("plan-1", plan)

    assert store.read_plan("plan-1") == plan
    assert not (tmp_path / "plans" / "plan-1.json").exists()


def test_v2_store_aliases_do_not_read_legacy_aliases(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    config.aliases_file.write_text('{"aliases":{"Old":{"target_id":"legacy"}}}', encoding="utf-8")

    store = CacheV2Store(config)

    assert store.aliases() == {}


def test_v2_store_binds_alias_to_graph_and_profile(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)

    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    assert store.find_alias("Program") == {"graph_id": "graph-1", "profile_id": "profile-1", "kind": "page"}


def test_v2_store_rejects_non_v2_documents(tmp_path, monkeypatch):
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    store.graph_path("legacy").write_text('{"graph_id":"legacy"}', encoding="utf-8")

    assert store.read_graph("legacy") is None
