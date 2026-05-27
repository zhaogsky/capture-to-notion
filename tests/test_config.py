import json
from pathlib import Path

from capture_to_notion.config import AppConfig, ensure_config


def test_ensure_config_creates_default_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    config = ensure_config()

    assert isinstance(config, AppConfig)
    assert config.root == tmp_path
    assert (tmp_path / "config.json").exists()
    assert not (tmp_path / "aliases.json").exists()
    assert not (tmp_path / "routes.json").exists()
    assert (tmp_path / "states.json").exists()
    assert not (tmp_path / "targets").exists()
    assert not (tmp_path / "plans").exists()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "cache-v2" / "graphs").is_dir()
    assert (tmp_path / "cache-v2" / "profiles").is_dir()
    assert (tmp_path / "cache-v2" / "plans").is_dir()
    assert (tmp_path / "cache-v2" / "assets").is_dir()
    assert not (tmp_path / "cache" / "pages").exists()
    assert not (tmp_path / "cache" / "data-sources").exists()
    assert not (tmp_path / "cache" / "searches").exists()
    assert not (tmp_path / "cache" / "enrichment").exists()
    assert not (tmp_path / "cache" / "assets" / "covers" / "books").exists()
    assert not (tmp_path / "cache" / "assets" / "covers" / "podcast_episodes").exists()


def test_ensure_config_does_not_overwrite_existing_v2_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    aliases_path = tmp_path / "cache-v2" / "aliases.json"
    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    custom_aliases = '{\n  "cache_version": 2,\n  "aliases": {\n    "custom": {\n      "graph_id": "graph-custom",\n      "profile_id": null,\n      "kind": "graph"\n    }\n  }\n}\n'
    aliases_path.write_text(custom_aliases, encoding="utf-8")

    ensure_config()

    assert aliases_path.read_text(encoding="utf-8") == custom_aliases


def test_default_config_contains_v2_cache_paths_and_notion_api_version(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    config = ensure_config()
    data = json.loads(config.config_file.read_text(encoding="utf-8"))

    assert data["notion"]["api_version"] == "2026-03-11"
    assert config.cache_v2_dir == tmp_path / "cache-v2"
    assert config.graphs_v2_dir == tmp_path / "cache-v2" / "graphs"
    assert config.profiles_v2_dir == tmp_path / "cache-v2" / "profiles"
    assert config.aliases_v2_file == tmp_path / "cache-v2" / "aliases.json"
    assert config.plans_v2_dir == tmp_path / "cache-v2" / "plans"
    assert config.assets_v2_dir == tmp_path / "cache-v2" / "assets"
    assert config.aliases_v2_file.read_text(encoding="utf-8") == '{\n  "cache_version": 2,\n  "aliases": {}\n}\n'


def test_ensure_config_writes_book_parser_profile_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    ensure_config()

    config_data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    book_defaults = config_data["parser_profiles"]["defaults"]["book"]

    assert book_defaults["required_schema_fields"] == ["cover", "author", "isbn", "page_count", "state"]
    assert book_defaults["required_value_fields"] == ["author", "isbn", "page_count"]
    assert book_defaults["summary_key_fields"] == ["cover", "author", "isbn", "page_count"]
    assert book_defaults["trusted_field_sources"] == ["explicit", "profile"]
    assert book_defaults["asset_trust_required_fields"] == ["cover"]
    assert book_defaults["primary_score_fields"] == {"title": 20, "state": 10, "cover": 10, "author": 35, "publisher": 15, "isbn": 35}
    assert book_defaults["record_defaults"] == {"author": None, "isbn": None, "publisher": None, "page_count": None}
    assert book_defaults["value_types"] == {"page_count": "integer", "current_page": "integer", "reading_count": "integer"}
    assert "labels" not in book_defaults
    assert "作者" not in json.dumps(book_defaults, ensure_ascii=False)
