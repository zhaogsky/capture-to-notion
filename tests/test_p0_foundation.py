from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping

import pytest

import capture_to_notion
import capture_to_notion.cli
import capture_to_notion.diagnostics
from capture_to_notion.cache import CacheStore
from capture_to_notion.config import ensure_config
from capture_to_notion.diagnostics import doctor_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    args: list[str],
    tmp_path: Path,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CAPTURE_TO_NOTION_CONFIG_DIR"] = str(tmp_path / "config")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "capture_to_notion.cli", *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_cli_help_uses_capture_to_notion_name(tmp_path: Path) -> None:
    result = run_cli(["--help"], tmp_path)

    assert result.returncode == 0
    assert "usage: capture-to-notion" in result.stdout
    assert "notion-skill" not in result.stdout
    assert "notion-capture" not in result.stdout


def test_python_package_imports_from_current_package() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import capture_to_notion; print(capture_to_notion.__version__)"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == capture_to_notion.__version__


def test_version_does_not_create_config_files(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"

    result = run_cli(["version"], tmp_path)

    assert result.returncode == 0
    assert not config_dir.exists()



def test_version_does_not_initialize_notion_adapter_or_leak_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret = "secret-token-value"
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NOTION_TOKEN", secret)

    def fail_from_config(config):
        raise AssertionError(f"NotionAdapter.from_config should not run: {secret}")

    monkeypatch.setattr(
        capture_to_notion.cli.NotionAdapter,
        "from_config",
        fail_from_config,
    )

    result = capture_to_notion.cli.main(["version"])
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["command"] == "capture-to-notion"
    assert secret not in captured.out
    assert secret not in captured.err



def test_version_outputs_runtime_paths_without_secrets(tmp_path: Path) -> None:
    result = run_cli(["version"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["command"] == "capture-to-notion"
    assert data["version"] == capture_to_notion.__version__
    assert data["package"] == "capture_to_notion"
    assert data["config_root"] == str(tmp_path / "config")
    assert data["skill_path"].endswith("capture-to-notion")
    assert "token" not in result.stdout.lower()


def test_doctor_reports_nested_config_token_without_revealing_secret(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": "secret-token-value"}}}),
        encoding="utf-8",
    )

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["command"] == "capture-to-notion"
    assert data["version"] == capture_to_notion.__version__
    assert data["package"] == "capture_to_notion"
    assert data["config_root"] == str(config_dir)
    assert data["checks"]["config_root"]["path"] == str(config_dir)
    assert data["checks"]["config_file"]["exists"] is True
    assert data["checks"]["config_file"]["valid_json"] is True
    assert data["checks"]["token"]["configured"] is True
    assert data["checks"]["token"]["next_steps"] == []
    assert "secret-token-value" not in result.stdout
    assert result.stderr == ""


def test_doctor_warns_about_target_cache_missing_field_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "z-bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "title": "Books",
                    "fields": {"title": "名称", "author": "作者"},
                    "schema": {
                        "名称": {"type": "title"},
                        "作者": {"type": "rich_text"},
                    },
                }
            },
        },
    )
    cache.write_json(
        config.targets_dir / "a-authorshelf.json",
        {
            "target": {"page_id": "page-authors", "title": "作者"},
            "data_sources": {
                "authors": {
                    "title": "Authors",
                    "fields": {"name": "姓名"},
                }
            },
        },
    )
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "My Shelf": {
                    "type": "page",
                    "page_id": "page-books",
                    "title": "书单",
                    "target_id": "z-bookshelf",
                }
            }
        },
    )
    report = doctor_report(config)
    stale_check = report["checks"]["target_cache_field_sources"]
    assert list(report["checks"]) == [
        "config_root",
        "config_file",
        "token",
        "legacy_config_dir",
        "target_cache_field_sources",
    ]
    assert stale_check["status"] == "warning"
    assert stale_check["details"] == {
        "targets_missing_field_sources": ["a-authorshelf", "z-bookshelf"],
        "targets_requiring_rescan": [
            {
                "target_id": "a-authorshelf",
                "target_title": "作者",
                "page_id": "page-authors",
            },
            {
                "target_id": "z-bookshelf",
                "target_title": "书单",
                "page_id": "page-books",
            },
        ],
        "rescan_commands": [
            "capture-to-notion target scan --page-id page-authors --target-id a-authorshelf",
            "capture-to-notion target scan --page-id page-books --alias 'My Shelf'",
        ],
        "message": "Rescan these targets to record mapping field_sources.",
    }


def test_doctor_warns_about_target_cache_partial_field_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "title": "Books",
                    "fields": {"title": "名称", "author": "作者"},
                    "field_sources": {"title": "profile"},
                }
            },
        },
    )

    report = doctor_report(config)

    assert report["checks"]["target_cache_field_sources"]["status"] == "warning"
    assert report["checks"]["target_cache_field_sources"]["details"] == {
        "targets_missing_field_sources": ["bookshelf"],
        "targets_requiring_rescan": [
            {
                "target_id": "bookshelf",
                "target_title": "书单",
                "page_id": "page-books",
            }
        ],
        "rescan_commands": ["capture-to-notion target scan --page-id page-books --target-id bookshelf"],
        "message": "Rescan these targets to record mapping field_sources.",
    }


def test_doctor_does_not_include_data_source_id_for_page_target_rescan(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {
                "page_id": "page-books",
                "title": "书单",
                "target_id": "bookshelf",
                "data_source_id": "ds-books",
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "fields": {"title": "名称"},
                }
            },
        },
    )

    report = doctor_report(config)

    assert report["checks"]["target_cache_field_sources"]["details"]["targets_requiring_rescan"] == [
        {
            "target_id": "bookshelf",
            "target_title": "书单",
            "page_id": "page-books",
        }
    ]
    assert report["checks"]["target_cache_field_sources"]["details"]["rescan_commands"] == [
        "capture-to-notion target scan --page-id page-books --target-id bookshelf"
    ]



def test_doctor_suggests_data_source_rescan_for_stale_direct_data_source_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "articles.json",
        {
            "target": {
                "page_id": None,
                "title": "Articles",
                "target_id": "articles",
                "data_source_id": "ds-articles",
            },
            "data_sources": {
                "ds-articles": {
                    "data_source_id": "ds-articles",
                    "title": "Articles",
                    "fields": {"title": "Name"},
                }
            },
        },
    )
    cache.write_json(
        config.aliases_file,
        {
            "aliases": {
                "Article DB": {
                    "type": "data_source",
                    "data_source_id": "ds-articles",
                    "title": "Articles",
                    "target_id": "articles",
                }
            }
        },
    )

    report = doctor_report(config)

    assert report["checks"]["target_cache_field_sources"]["details"]["targets_requiring_rescan"] == [
        {
            "target_id": "articles",
            "target_title": "Articles",
            "page_id": None,
            "data_source_id": "ds-articles",
        }
    ]
    assert report["checks"]["target_cache_field_sources"]["details"]["rescan_commands"] == [
        "capture-to-notion target scan --data-source-id ds-articles --alias 'Article DB'"
    ]



def test_doctor_suggests_data_source_rescan_when_legacy_target_lacks_data_source_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "articles.json",
        {
            "target": {
                "page_id": None,
                "title": "Articles",
                "target_id": "articles",
            },
            "data_sources": {
                "ds-articles": {
                    "data_source_id": "ds-articles",
                    "title": "Articles",
                    "fields": {"title": "Name"},
                }
            },
        },
    )

    report = doctor_report(config)

    assert report["checks"]["target_cache_field_sources"]["details"]["targets_requiring_rescan"] == [
        {
            "target_id": "articles",
            "target_title": "Articles",
            "page_id": None,
            "data_source_id": "ds-articles",
        }
    ]
    assert report["checks"]["target_cache_field_sources"]["details"]["rescan_commands"] == [
        "capture-to-notion target scan --data-source-id ds-articles --target-id articles"
    ]



def test_doctor_accepts_target_cache_with_complete_field_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    cache = CacheStore(config)
    cache.write_json(
        config.targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "title": "Books",
                    "fields": {"title": "名称", "author": "作者"},
                    "field_sources": {"title": "profile", "author": "explicit"},
                }
            },
        },
    )

    report = doctor_report(config)

    assert report["checks"]["target_cache_field_sources"] == {
        "name": "target_cache_field_sources",
        "status": "ok",
        "details": {
            "targets_missing_field_sources": [],
            "rescan_commands": [],
            "message": "All cached targets with fields include field_sources.",
        },
    }


def test_doctor_reports_default_notion_token_env_without_revealing_secret(tmp_path: Path) -> None:
    result = run_cli(["doctor"], tmp_path, extra_env={"NOTION_TOKEN": "secret-token-value"})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["config_file"]["exists"] is False
    assert data["checks"]["config_file"]["valid_json"] is False
    assert data["checks"]["token"]["configured"] is True
    assert data["checks"]["token"]["next_steps"] == []
    assert "secret-token-value" not in result.stdout
    assert result.stderr == ""



def test_doctor_reports_next_steps_when_token_is_missing(tmp_path: Path) -> None:
    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["token"] == {
        "configured": False,
        "source": "redacted",
        "next_steps": [
            "Set NOTION_TOKEN or configure notion.auth.env_token_name/token in config.json."
        ],
    }



def test_doctor_does_not_fallback_to_default_env_when_env_token_name_is_blank(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"env_token_name": ""}}}),
        encoding="utf-8",
    )

    result = run_cli(["doctor"], tmp_path, extra_env={"NOTION_TOKEN": "secret-token-value"})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["token"]["configured"] is False
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr


def test_doctor_does_not_accept_legacy_top_level_notion_token(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion_token": "secret-token-value"}),
        encoding="utf-8",
    )

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["token"]["configured"] is False
    assert "secret-token-value" not in result.stdout
    assert result.stderr == ""


def test_doctor_does_not_initialize_notion_adapter_or_leak_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret = "secret-token-value"
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": secret}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(config_dir))

    def fail_from_config(config):
        raise AssertionError(f"NotionAdapter.from_config should not run: {secret}")

    monkeypatch.setattr(
        capture_to_notion.cli.NotionAdapter,
        "from_config",
        fail_from_config,
    )

    result = capture_to_notion.cli.main(["doctor"])
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["checks"]["token"]["configured"] is True
    assert secret not in captured.out
    assert secret not in captured.err


def test_capture_apply_requires_confirmation_before_initializing_notion_adapter_or_leaking_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret = "secret-token-value"
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("NOTION_TOKEN", secret)

    def fail_from_config(config):
        raise AssertionError(f"NotionAdapter.from_config should not run: {secret}")

    monkeypatch.setattr(
        capture_to_notion.cli.NotionAdapter,
        "from_config",
        fail_from_config,
    )

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "plan-test",
                "content_type": "books",
                "target": {
                    "page_title": "Books",
                    "page_id": "page-123",
                    "data_source_id": "data-source-123",
                    "confidence": "high",
                    "source": "test",
                },
                "normalized_record": {"title": "Test Book"},
                "field_mapping": {"title": "Name"},
                "operations": [{"type": "upsert", "field": "title", "value": "Test Book"}],
                "asset_operations": [],
                "sources": [],
                "warnings": [],
                "requires_confirmation": True,
                "confirmation_reason": "duplicate_target",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = capture_to_notion.cli.main(["capture", "apply", "--plan", str(plan_path)])
    captured = capsys.readouterr()

    assert result == 2
    assert "计划需要确认后才能执行" in captured.err
    assert secret not in captured.out
    assert secret not in captured.err



def test_doctor_warns_when_legacy_config_dir_exists(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion_token": "secret-token-value"}),
        encoding="utf-8",
    )
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)

    result = run_cli(["doctor"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["legacy_config_dir"] == {
        "path": str(legacy_dir),
        "exists": True,
        "next_steps": [
            f"Review legacy config at {legacy_dir}; migrate settings into CAPTURE_TO_NOTION_CONFIG_DIR before deleting it."
        ],
    }
    assert "secret-token-value" not in result.stdout



def test_doctor_reports_no_legacy_config_dir_next_steps_when_absent(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"

    result = run_cli(["doctor"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["legacy_config_dir"] == {
        "path": str(fake_home / ".config" / "notion-skill"),
        "exists": False,
        "next_steps": [],
    }



def test_config_migrate_dry_run_reports_pending_legacy_files_without_writing(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": "secret-token-value"}}}),
        encoding="utf-8",
    )
    (legacy_dir / "aliases.json").write_text(
        json.dumps({"aliases": {"books": {"target_id": "bookshelf"}}}),
        encoding="utf-8",
    )

    result = run_cli(["config", "migrate"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["legacy_exists"] is True
    assert data["confirmed"] is False
    assert data["pending_copy"] == ["aliases.json", "config.json"]
    assert data["migrated"] == []
    assert data["skipped_existing"] == []
    assert data["destination_root"] == str(tmp_path / "config")
    assert not (tmp_path / "config").exists()
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr



def test_config_migrate_confirmed_copies_missing_files_without_overwriting_existing_ones(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)
    legacy_secret = "secret-token-value"
    (legacy_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": legacy_secret}}}),
        encoding="utf-8",
    )
    (legacy_dir / "aliases.json").write_text(
        json.dumps({"aliases": {"legacy": {"target_id": "legacy-target"}}}),
        encoding="utf-8",
    )
    (legacy_dir / "targets" / "bookshelf.json").parent.mkdir(parents=True)
    (legacy_dir / "targets" / "bookshelf.json").write_text(
        json.dumps({"target": {"title": "书单"}}),
        encoding="utf-8",
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    existing_config = json.dumps({"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}})
    (config_dir / "config.json").write_text(existing_config, encoding="utf-8")

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["confirmed"] is True
    assert data["pending_copy"] == []
    assert data["migrated"] == ["aliases.json", "targets/bookshelf.json"]
    assert data["skipped_existing"] == ["config.json"]
    assert json.loads((config_dir / "aliases.json").read_text(encoding="utf-8")) == {
        "aliases": {"legacy": {"target_id": "legacy-target"}}
    }
    assert json.loads((config_dir / "targets" / "bookshelf.json").read_text(encoding="utf-8")) == {
        "target": {"title": "书单"}
    }
    assert (config_dir / "config.json").read_text(encoding="utf-8") == existing_config
    assert legacy_dir.exists()
    assert (legacy_dir / "config.json").exists()
    assert (legacy_dir / "aliases.json").exists()
    assert (legacy_dir / "targets" / "bookshelf.json").exists()
    assert "Legacy config directory was not deleted." in data["warnings"]
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr



def test_config_migrate_confirmed_rechecks_destination_before_copy_and_skips_existing(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "aliases.json").write_text(
        json.dumps({"aliases": {"legacy": {"target_id": "legacy-target"}}}),
        encoding="utf-8",
    )
    destination_root = tmp_path / "config"
    destination_path = destination_root / "aliases.json"
    original_exists = Path.exists
    exists_calls = {"count": 0}

    def controlled_exists(path: Path) -> bool:
        if path == destination_path:
            exists_calls["count"] += 1
            if exists_calls["count"] == 1:
                return False
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            destination_path.write_text(
                json.dumps({"aliases": {"current": {"target_id": "current-target"}}}),
                encoding="utf-8",
            )
            return True
        return original_exists(path)

    monkeypatch.setattr(capture_to_notion.diagnostics, "legacy_config_root", lambda: legacy_dir)
    monkeypatch.setattr(Path, "exists", controlled_exists)

    result = capture_to_notion.diagnostics.migrate_legacy_config(destination_root, confirmed=True)

    assert result["pending_copy"] == []
    assert result["migrated"] == []
    assert result["skipped_existing"] == ["aliases.json"]
    assert result["copy_results"] == [
        {"path": "aliases.json", "status": "skipped_existing"}
    ]
    assert json.loads(destination_path.read_text(encoding="utf-8")) == {
        "aliases": {"current": {"target_id": "current-target"}}
    }



def test_config_migrate_confirmed_uses_exclusive_copy_when_destination_appears(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": "legacy-token"}}}),
        encoding="utf-8",
    )
    destination_root = tmp_path / "config"
    destination_path = destination_root / "config.json"

    def race_copy(source_path: Path, destination_path_arg: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_text(
            json.dumps({"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}}),
            encoding="utf-8",
        )
        raise FileExistsError(destination_path_arg)

    monkeypatch.setattr(capture_to_notion.diagnostics, "legacy_config_root", lambda: legacy_dir)
    monkeypatch.setattr(capture_to_notion.diagnostics, "_copy_file_exclusive", race_copy)

    result = capture_to_notion.diagnostics.migrate_legacy_config(destination_root, confirmed=True)

    assert result["pending_copy"] == []
    assert result["migrated"] == []
    assert result["skipped_existing"] == ["config.json"]
    assert result["copy_results"] == [
        {"path": "config.json", "status": "skipped_existing"}
    ]
    assert json.loads(destination_path.read_text(encoding="utf-8")) == {
        "notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}
    }



def test_config_migrate_confirmed_returns_structured_error_when_copy_fails(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(parents=True)
    secret = "secret-token-value"
    (legacy_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": secret}}}),
        encoding="utf-8",
    )
    destination_root = tmp_path / "config"
    destination_path = destination_root / "config.json"
    concurrent_destination = {"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}}

    def fail_copy(source_path: Path, destination_path_arg: Path) -> None:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_text(json.dumps(concurrent_destination), encoding="utf-8")
        raise OSError(f"copy failed while handling {secret}")

    monkeypatch.setattr(capture_to_notion.diagnostics, "legacy_config_root", lambda: legacy_dir)
    monkeypatch.setattr(capture_to_notion.diagnostics, "_copy_file_exclusive", fail_copy)

    result = capture_to_notion.diagnostics.migrate_legacy_config(destination_root, confirmed=True)

    assert result["pending_copy"] == []
    assert result["migrated"] == []
    assert result["copy_results"] == [
        {
            "path": "config.json",
            "status": "error",
            "error_code": "copy_os_error",
            "message": "copy failed due to OS error",
        }
    ]
    assert result["errors"] == [
        {
            "path": "config.json",
            "error_code": "copy_os_error",
            "message": "copy failed due to OS error",
        }
    ]
    assert json.loads(destination_path.read_text(encoding="utf-8")) == concurrent_destination
    assert secret not in json.dumps(result, ensure_ascii=False)



def test_copy_file_exclusive_does_not_publish_partial_destination(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    destination_path = tmp_path / "destination.json"

    def fail_copyfileobj(source_file, destination_file) -> None:
        destination_file.write(b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr(capture_to_notion.diagnostics.shutil, "copyfileobj", fail_copyfileobj)

    with pytest.raises(OSError):
        capture_to_notion.diagnostics._copy_file_exclusive(source_path, destination_path)

    assert not destination_path.exists()
    assert list(tmp_path.glob(".destination.json.*.tmp")) == []



def test_config_migrate_confirmed_without_legacy_dir_does_not_create_destination(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["legacy_exists"] is False
    assert data["migrated"] == []
    assert data["pending_copy"] == []
    assert not (tmp_path / "config").exists()
    assert any("No legacy config directory found" in warning for warning in data["warnings"])



def test_config_migrate_only_copies_allowlisted_legacy_assets(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    (legacy_dir / "targets").mkdir(parents=True)
    (legacy_dir / "cache").mkdir(parents=True)
    (legacy_dir / "logs").mkdir(parents=True)
    (legacy_dir / "plans").mkdir(parents=True)
    (legacy_dir / "config.json").write_text(json.dumps({"notion": {"auth": {"token": "secret-token-value"}}}), encoding="utf-8")
    (legacy_dir / "aliases.json").write_text(json.dumps({"aliases": {"books": {"target_id": "bookshelf"}}}), encoding="utf-8")
    (legacy_dir / "states.json").write_text(json.dumps({"states": {"initialized": {}}}), encoding="utf-8")
    (legacy_dir / "routes.json").write_text(json.dumps({"routes": {"book": {}}}), encoding="utf-8")
    (legacy_dir / "targets" / "bookshelf.json").write_text(json.dumps({"target": {"title": "书单"}}), encoding="utf-8")
    (legacy_dir / "cache" / "searches.json").write_text(json.dumps({"q": [1]}), encoding="utf-8")
    (legacy_dir / "logs" / "run.log").write_text("secret-token-value", encoding="utf-8")
    (legacy_dir / "plans" / "latest.json").write_text(json.dumps({"plan_id": "p1"}), encoding="utf-8")
    (legacy_dir / "notes.txt").write_text("do not copy", encoding="utf-8")

    result = run_cli(["config", "migrate"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["pending_copy"] == [
        "aliases.json",
        "config.json",
        "states.json",
        "targets/bookshelf.json",
    ]
    assert data["migrated"] == []
    assert data["skipped_existing"] == []
    assert data["skipped_disallowed"] == [
        "cache/searches.json",
        "logs/run.log",
        "notes.txt",
        "plans/latest.json",
        "routes.json",
    ]
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr
    assert not (tmp_path / "config").exists()



def test_config_migrate_confirmed_skips_source_symlinks_and_unsafe_destinations(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    (legacy_dir / "targets").mkdir(parents=True)
    external_secret = tmp_path / "external-secret.json"
    external_secret.write_text(json.dumps({"notion": {"auth": {"token": "secret-token-value"}}}), encoding="utf-8")
    (legacy_dir / "aliases.json").symlink_to(external_secret)
    (legacy_dir / "config.json").write_text(json.dumps({"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}}), encoding="utf-8")
    (legacy_dir / "targets" / "bookshelf.json").write_text(json.dumps({"target": {"title": "书单"}}), encoding="utf-8")

    config_dir = tmp_path / "config"
    unsafe_target_root = tmp_path / "unsafe-target-root"
    unsafe_target_root.mkdir()
    config_dir.mkdir()
    (config_dir / "targets").symlink_to(unsafe_target_root, target_is_directory=True)

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["migrated"] == ["config.json"]
    assert data["skipped_symlinks"] == ["aliases.json"]
    assert data["skipped_unsafe"] == ["targets/bookshelf.json"]
    assert not (config_dir / "aliases.json").exists()
    assert not (unsafe_target_root / "bookshelf.json").exists()
    assert json.loads((config_dir / "config.json").read_text(encoding="utf-8")) == {
        "notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}
    }
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr



def test_config_migrate_skips_legacy_root_symlink(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    real_legacy_dir = tmp_path / "real-legacy"
    real_legacy_dir.mkdir()
    (real_legacy_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": "secret-token-value"}}}),
        encoding="utf-8",
    )
    legacy_parent = fake_home / ".config"
    legacy_parent.mkdir(parents=True)
    (legacy_parent / "notion-skill").symlink_to(real_legacy_dir, target_is_directory=True)

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["migrated"] == []
    assert data["pending_copy"] == []
    assert data["skipped_symlinks"] == ["."]
    assert not (tmp_path / "config" / "config.json").exists()
    assert "secret-token-value" not in result.stdout
    assert "secret-token-value" not in result.stderr



def test_config_migrate_confirmed_returns_structured_error_when_parent_create_fails(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    (legacy_dir / "targets").mkdir(parents=True)
    (legacy_dir / "targets" / "bookshelf.json").write_text(json.dumps({"target": {"title": "书单"}}), encoding="utf-8")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "targets").write_text("not a directory", encoding="utf-8")

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode != 0
    data = json.loads(result.stdout)
    assert data["pending_copy"] == []
    assert data["migrated"] == []
    assert data["copy_results"] == [
        {
            "path": "targets/bookshelf.json",
            "status": "error",
            "error_code": "copy_os_error",
            "message": "copy failed due to OS error",
        }
    ]
    assert data["errors"] == [
        {
            "path": "targets/bookshelf.json",
            "error_code": "copy_os_error",
            "message": "copy failed due to OS error",
        }
    ]



def test_config_migrate_confirmed_skips_destination_root_symlink(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(json.dumps({"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}}), encoding="utf-8")

    external_config_root = tmp_path / "external-config-root"
    external_config_root.mkdir()
    (tmp_path / "config").symlink_to(external_config_root, target_is_directory=True)

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["migrated"] == []
    assert data["skipped_unsafe"] == ["config.json"]
    assert not (external_config_root / "config.json").exists()
    assert (legacy_dir / "config.json").exists()



def test_config_migrate_confirmed_returns_structured_error_when_destination_root_is_file(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(json.dumps({"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}}), encoding="utf-8")
    (tmp_path / "config").write_text("not a directory", encoding="utf-8")

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode != 0
    data = json.loads(result.stdout)
    assert data["pending_copy"] == []
    assert data["migrated"] == []
    assert data["copy_results"] == [
        {
            "path": "config.json",
            "status": "error",
            "error_code": "copy_os_error",
            "message": "copy failed due to OS error",
        }
    ]
    assert data["errors"] == [
        {
            "path": "config.json",
            "error_code": "copy_os_error",
            "message": "copy failed due to OS error",
        }
    ]
    assert (legacy_dir / "config.json").exists()



def test_config_migrate_confirmed_skips_broken_destination_root_symlink(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(json.dumps({"notion": {"auth": {"env_token_name": "NOTION_TOKEN"}}}), encoding="utf-8")
    (tmp_path / "config").symlink_to(tmp_path / "missing-config-root", target_is_directory=True)

    result = run_cli(["config", "migrate", "--confirmed"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["migrated"] == []
    assert data["skipped_unsafe"] == ["config.json"]
    assert data["copy_results"] == []
    assert data["errors"] == []
    assert (legacy_dir / "config.json").exists()



def test_runtime_files_do_not_reference_old_runtime_names() -> None:
    runtime_files = [
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "capture_to_notion" / "cli.py",
        PROJECT_ROOT / "capture_to_notion" / "config.py",
        PROJECT_ROOT / "capture_to_notion" / "notion_adapter.py",
        PROJECT_ROOT / "SKILL.md",
    ]
    forbidden = [
        "notion-skill",
        "notion_skill",
        "notion-capture",
        "NOTION_SKILL_CONFIG_DIR",
        ".config/notion-skill",
    ]

    violations: list[str] = []
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)} contains {token}")

    assert violations == []


def test_readmes_document_p0_diagnostics_commands() -> None:
    readme_paths = [
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "README.zh-CN.md",
    ]
    fenced_legacy_command_pattern = re.compile(
        r"```(?:[a-zA-Z0-9_+-]+)?\n[\s\S]*?^(?:\$\s*)?(?:notion-skill|notion-capture)\b",
        re.MULTILINE,
    )
    shell_legacy_command_pattern = re.compile(
        r"^(?:\$\s*)?(?:notion-skill|notion-capture)\b",
        re.MULTILINE,
    )
    recommended_legacy_command_pattern = re.compile(
        r"(?i)(?<!not\s)(?<!n't\s)\b(?:run|use|execute)\s+`(?:notion-skill|notion-capture)\b[^`]*`",
    )

    for path in readme_paths:
        text = path.read_text(encoding="utf-8")
        assert "capture-to-notion version" in text
        assert "capture-to-notion doctor" in text
        assert "capture-to-notion config migrate" in text
        assert "capture-to-notion target list" in text
        assert "capture-to-notion target inspect" in text
        assert "capture-to-notion capture verify --page-id PAGE_ID" in text
        assert "CHANGELOG.md" in text
        assert "summary" in text
        assert "target_page" in text
        assert "key_fields" in text
        assert "book_key_values_missing" in text
        assert fenced_legacy_command_pattern.search(text) is None
        assert shell_legacy_command_pattern.search(text) is None
        assert recommended_legacy_command_pattern.search(text) is None



def test_changelog_records_capture_to_notion_rename() -> None:
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")

    assert "capture-to-notion" in text
    assert "notion-skill" in text
    assert "notion-capture" in text
    assert "notion_skill" in text
    assert "capture_to_notion" in text
    assert "~/.config/capture-to-notion" in text



def test_changelog_uses_portable_install_command() -> None:
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")

    assert "/Users/aaron/" not in text
    assert "uv tool install --force --editable ." in text
