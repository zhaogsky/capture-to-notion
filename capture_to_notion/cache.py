from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from capture_to_notion.config import AppConfig


class CacheStore:
    def __init__(self, config: AppConfig):
        self.config = config

    def read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
        if not isinstance(data, dict):
            return default
        return data

    def write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def aliases(self) -> dict[str, Any]:
        data = self.read_json(self.config.aliases_file, {"aliases": {}})
        aliases = data.get("aliases")
        return aliases if isinstance(aliases, dict) else {}

    def routes(self) -> dict[str, Any]:
        data = self.read_json(self.config.routes_file, {"routes": {}})
        routes = data.get("routes")
        return routes if isinstance(routes, dict) else {}

    def find_alias(self, name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        return self.aliases().get(name)

    def target_structure(self, target_id: str | None) -> dict[str, Any] | None:
        if not target_id:
            return None
        path = self.config.targets_dir / f"{target_id}.json"
        if not path.exists():
            return None
        return self.read_json(path, {})

    def target_structure_for_data_source(self, data_source_id: str | None) -> dict[str, Any] | None:
        if not data_source_id:
            return None
        for path in sorted(self.config.targets_dir.glob("*.json")):
            structure = self.read_json(path, {})
            data_sources = structure.get("data_sources", {})
            if not isinstance(data_sources, dict):
                continue
            for data_source in data_sources.values():
                if isinstance(data_source, dict) and data_source.get("data_source_id") == data_source_id:
                    return structure
        return None

    def save_plan(self, plan_id: str, data: dict[str, Any]) -> Path:
        path = self.config.plans_dir / f"{plan_id}.json"
        self.write_json(path, data)
        return path
