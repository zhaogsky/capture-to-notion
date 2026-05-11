from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = {
    "notion": {"auth": {"env_token_name": "NOTION_TOKEN"}, "default_workspace": "default"},
    "behavior": {
        "confirmation": {
            "require_for_first_target": True,
            "require_for_schema_change": True,
            "require_for_target_suggestion": True,
        },
        "search": {"allow_web_search": True},
        "assets": {"allow_cover_download": True, "cache_covers": True},
        "apply": {"default_dry_run": False},
    },
}

DEFAULT_STATES = {
    "states": {
        "initialized": {"aliases": ["初始化", "待处理", "待读", "待听", "想读", "想听"]},
        "completed": {"aliases": ["完成", "已完成", "已读", "读完", "听完"]},
    }
}


@dataclass(frozen=True)
class AppConfig:
    root: Path
    config_file: Path
    aliases_file: Path
    routes_file: Path
    states_file: Path
    targets_dir: Path
    plans_dir: Path
    logs_dir: Path
    covers_dir: Path


def config_root() -> Path:
    override = os.environ.get("CAPTURE_TO_NOTION_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "capture-to-notion"


def write_json_if_missing(path: Path, data: dict) -> None:
    if not path.exists():
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_config() -> AppConfig:
    root = config_root()
    targets_dir = root / "targets"
    plans_dir = root / "plans"
    logs_dir = root / "logs"
    covers_dir = root / "cache" / "assets" / "covers"

    for path in [
        root,
        targets_dir,
        plans_dir,
        logs_dir,
        root / "cache" / "pages",
        root / "cache" / "data-sources",
        root / "cache" / "searches",
        root / "cache" / "enrichment",
        covers_dir / "books",
        covers_dir / "podcast_episodes",
    ]:
        path.mkdir(parents=True, exist_ok=True)

    config_file = root / "config.json"
    aliases_file = root / "aliases.json"
    routes_file = root / "routes.json"
    states_file = root / "states.json"

    write_json_if_missing(config_file, DEFAULT_CONFIG)
    write_json_if_missing(aliases_file, {"aliases": {}})
    write_json_if_missing(routes_file, {"routes": {}})
    write_json_if_missing(states_file, DEFAULT_STATES)

    return AppConfig(
        root=root,
        config_file=config_file,
        aliases_file=aliases_file,
        routes_file=routes_file,
        states_file=states_file,
        targets_dir=targets_dir,
        plans_dir=plans_dir,
        logs_dir=logs_dir,
        covers_dir=covers_dir,
    )
