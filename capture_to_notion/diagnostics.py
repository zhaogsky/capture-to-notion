from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from capture_to_notion import __version__
from capture_to_notion.config import AppConfig


COMMAND_NAME = "capture-to-notion"
PACKAGE_NAME = "capture_to_notion"


def skill_path() -> Path:
    return Path(__file__).resolve().parents[1]


def is_editable_install() -> bool:
    package_file = Path(__file__).resolve()
    return ".claude/skills/capture-to-notion" in package_file.as_posix()


def version_info(config_root_path: Path) -> dict[str, Any]:
    return {
        "command": COMMAND_NAME,
        "version": __version__,
        "package": PACKAGE_NAME,
        "python": sys.version.split()[0],
        "package_path": str(Path(__file__).resolve().parent),
        "skill_path": str(skill_path()),
        "config_root": str(config_root_path),
        "editable_install": is_editable_install(),
    }



def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None



def _token_configured(config_data: dict[str, Any] | None) -> bool:
    auth: dict[str, Any] = {}
    if config_data:
        notion = config_data.get("notion")
        if isinstance(notion, dict):
            auth_data = notion.get("auth")
            if isinstance(auth_data, dict):
                auth = auth_data

    token = auth.get("token")
    if isinstance(token, str) and token.strip():
        return True

    if "env_token_name" in auth:
        env_token_name = auth.get("env_token_name")
        if not isinstance(env_token_name, str):
            return False
    else:
        env_token_name = "NOTION_TOKEN"
    return bool(os.environ.get(env_token_name))


def _token_next_steps(configured: bool) -> list[str]:
    if configured:
        return []
    return ["Set NOTION_TOKEN or configure notion.auth.env_token_name/token in config.json."]


def _legacy_config_dir_next_steps(legacy_config_dir: Path, exists: bool) -> list[str]:
    if not exists:
        return []
    return [
        f"Review legacy config at {legacy_config_dir}; migrate settings into CAPTURE_TO_NOTION_CONFIG_DIR before deleting it."
    ]


def _stale_target_cache_entries(config: AppConfig) -> list[dict[str, str | None]]:
    stale_entries: list[dict[str, str | None]] = []
    for target_file in sorted(config.targets_dir.glob("*.json")):
        try:
            target_cache = json.loads(target_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(target_cache, dict):
            continue
        data_sources = target_cache.get("data_sources")
        if not isinstance(data_sources, dict):
            continue
        for source in data_sources.values():
            if not isinstance(source, dict):
                continue
            fields = source.get("fields")
            field_sources = source.get("field_sources")
            if not isinstance(fields, dict) or not fields:
                continue
            if not isinstance(field_sources, dict) or any(key not in field_sources for key in fields):
                target = target_cache.get("target")
                page_id = target.get("page_id") if isinstance(target, dict) else None
                stale_entries.append(
                    {
                        "target_id": target_file.stem,
                        "page_id": page_id if isinstance(page_id, str) else None,
                    }
                )
                break
    return stale_entries


def _aliases_by_target_id(config: AppConfig) -> dict[str, str]:
    data = _load_json_file(config.aliases_file) or {}
    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        return {}

    aliases_by_target_id: dict[str, str] = {}
    for alias_name, alias in sorted(aliases.items()):
        if not isinstance(alias_name, str) or not isinstance(alias, dict):
            continue
        target_id = alias.get("target_id")
        if isinstance(target_id, str) and target_id not in aliases_by_target_id:
            aliases_by_target_id[target_id] = alias_name
    return aliases_by_target_id


def _rescan_commands(config: AppConfig, stale_entries: list[dict[str, str | None]]) -> list[str]:
    aliases_by_target_id = _aliases_by_target_id(config)
    commands: list[str] = []
    for entry in stale_entries:
        target_id = entry.get("target_id")
        page_id = entry.get("page_id")
        if not target_id or not page_id:
            continue
        alias = aliases_by_target_id.get(target_id)
        target_option = f"--alias {shlex.quote(alias)}" if alias else f"--target-id {shlex.quote(target_id)}"
        commands.append(f"capture-to-notion target scan --page-id {shlex.quote(page_id)} {target_option}")
    return commands


def _config_from_root(config_root_path: Path) -> AppConfig:
    return AppConfig(
        root=config_root_path,
        config_file=config_root_path / "config.json",
        aliases_file=config_root_path / "aliases.json",
        routes_file=config_root_path / "routes.json",
        states_file=config_root_path / "states.json",
        targets_dir=config_root_path / "targets",
        plans_dir=config_root_path / "plans",
        logs_dir=config_root_path / "logs",
        covers_dir=config_root_path / "cache" / "assets" / "covers",
    )


def doctor_report(config: AppConfig | Path) -> dict[str, Any]:
    if isinstance(config, AppConfig):
        app_config = config
        config_root_path = config.root
    else:
        config_root_path = config
        app_config = _config_from_root(config_root_path)
    config_file = app_config.config_file
    config_data = _load_json_file(config_file)
    legacy_config_dir = Path.home() / ".config" / "notion-skill"
    parent_dir = config_root_path.parent

    stale_target_entries = _stale_target_cache_entries(app_config)
    missing_field_sources = [entry["target_id"] for entry in stale_target_entries if entry.get("target_id")]
    rescan_commands = _rescan_commands(app_config, stale_target_entries)
    field_sources_status = "warning" if missing_field_sources else "ok"
    field_sources_message = (
        "Rescan these targets to record mapping field_sources."
        if missing_field_sources
        else "All cached targets with fields include field_sources."
    )
    token_configured = _token_configured(config_data)
    legacy_config_dir_exists = legacy_config_dir.exists()

    report = version_info(config_root_path)
    report["checks"] = {
        "config_root": {
            "path": str(config_root_path),
            "exists": config_root_path.exists(),
            "is_dir": config_root_path.is_dir(),
            "writable": os.access(config_root_path, os.W_OK) if config_root_path.exists() else False,
            "parent_exists": parent_dir.exists(),
            "parent_writable": os.access(parent_dir, os.W_OK) if parent_dir.exists() else False,
        },
        "config_file": {
            "path": str(config_file),
            "exists": config_file.exists(),
            "readable": os.access(config_file, os.R_OK) if config_file.exists() else False,
            "valid_json": config_data is not None,
        },
        "token": {
            "configured": token_configured,
            "source": "redacted",
            "next_steps": _token_next_steps(token_configured),
        },
        "legacy_config_dir": {
            "path": str(legacy_config_dir),
            "exists": legacy_config_dir_exists,
            "next_steps": _legacy_config_dir_next_steps(legacy_config_dir, legacy_config_dir_exists),
        },
        "target_cache_field_sources": {
            "name": "target_cache_field_sources",
            "status": field_sources_status,
            "details": {
                "targets_missing_field_sources": missing_field_sources,
                "rescan_commands": rescan_commands,
                "message": field_sources_message,
            },
        },
    }
    return report
