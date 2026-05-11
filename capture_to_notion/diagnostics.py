from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from capture_to_notion import __version__


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



def doctor_report(config_root_path: Path) -> dict[str, Any]:
    config_file = config_root_path / "config.json"
    config_data = _load_json_file(config_file)
    legacy_config_dir = Path.home() / ".config" / "notion-skill"
    parent_dir = config_root_path.parent

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
            "configured": _token_configured(config_data),
            "source": "redacted",
        },
        "legacy_config_dir": {
            "path": str(legacy_config_dir),
            "exists": legacy_config_dir.exists(),
        },
    }
    return report
