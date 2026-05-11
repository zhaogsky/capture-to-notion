from pathlib import Path

from capture_to_notion.config import AppConfig, ensure_config


def test_ensure_config_creates_default_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    config = ensure_config()

    assert isinstance(config, AppConfig)
    assert config.root == tmp_path
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "aliases.json").read_text(encoding="utf-8") == '{\n  "aliases": {}\n}\n'
    assert (tmp_path / "routes.json").read_text(encoding="utf-8") == '{\n  "routes": {}\n}\n'
    assert (tmp_path / "states.json").exists()
    assert (tmp_path / "targets").is_dir()
    assert (tmp_path / "plans").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "cache" / "assets" / "covers" / "books").is_dir()
    assert (tmp_path / "cache" / "assets" / "covers" / "podcast_episodes").is_dir()


def test_ensure_config_does_not_overwrite_existing_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))

    aliases_path = tmp_path / "aliases.json"
    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    custom_aliases = '{\n  "aliases": {\n    "custom": "自定义"\n  }\n}\n'
    aliases_path.write_text(custom_aliases, encoding="utf-8")

    ensure_config()

    assert aliases_path.read_text(encoding="utf-8") == custom_aliases
