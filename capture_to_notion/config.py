from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = {
    "notion": {
        "auth": {"env_token_name": "NOTION_TOKEN"},
        "default_workspace": "default",
        "api_version": "2026-03-11",
    },
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
    "parser_profiles": {
        "defaults": {
            "book": {
                "required_schema_fields": ["cover", "author", "isbn", "page_count", "state"],
                "required_value_fields": ["author", "isbn", "page_count"],
                "summary_key_fields": ["cover", "author", "isbn", "page_count"],
                "trusted_field_sources": ["explicit", "profile"],
                "asset_trust_required_fields": ["cover"],
                "primary_score_fields": {"title": 20, "state": 10, "cover": 10, "author": 35, "publisher": 15, "isbn": 35},
                "record_defaults": {"author": None, "isbn": None, "publisher": None, "page_count": None},
                "value_types": {"page_count": "integer", "current_page": "integer", "reading_count": "integer"},
            }
        }
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
    cache_v2_dir: Path
    graphs_v2_dir: Path
    profiles_v2_dir: Path
    plans_v2_dir: Path
    assets_v2_dir: Path
    aliases_v2_file: Path


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
    cache_v2_dir = root / "cache-v2"
    graphs_v2_dir = cache_v2_dir / "graphs"
    profiles_v2_dir = cache_v2_dir / "profiles"
    plans_v2_dir = cache_v2_dir / "plans"
    assets_v2_dir = cache_v2_dir / "assets"

    for path in [
        root,
        logs_dir,
        cache_v2_dir,
        graphs_v2_dir,
        profiles_v2_dir,
        plans_v2_dir,
        assets_v2_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    config_file = root / "config.json"
    aliases_file = root / "aliases.json"
    routes_file = root / "routes.json"
    states_file = root / "states.json"
    aliases_v2_file = cache_v2_dir / "aliases.json"

    write_json_if_missing(config_file, DEFAULT_CONFIG)
    write_json_if_missing(states_file, DEFAULT_STATES)
    write_json_if_missing(aliases_v2_file, {"cache_version": 2, "aliases": {}})

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
        cache_v2_dir=cache_v2_dir,
        graphs_v2_dir=graphs_v2_dir,
        profiles_v2_dir=profiles_v2_dir,
        plans_v2_dir=plans_v2_dir,
        assets_v2_dir=assets_v2_dir,
        aliases_v2_file=aliases_v2_file,
    )
