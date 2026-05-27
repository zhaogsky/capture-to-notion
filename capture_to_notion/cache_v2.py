from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from capture_to_notion.config import AppConfig


class CacheV2Store:
    def __init__(self, config: AppConfig):
        self.config = config

    def read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
        return data if isinstance(data, dict) else default

    def write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def graph_path(self, graph_id: str) -> Path:
        return self.config.graphs_v2_dir / f"{graph_id}.json"

    def profile_path(self, profile_id: str) -> Path:
        return self.config.profiles_v2_dir / f"{profile_id}.json"

    def plan_path(self, plan_id: str) -> Path:
        return self.config.plans_v2_dir / f"{plan_id}.json"

    def read_graph(self, graph_id: str) -> dict[str, Any] | None:
        data = self.read_json(self.graph_path(graph_id), {})
        if data.get("cache_version") != 2:
            return None
        return data

    def write_graph(self, graph_id: str, graph: dict[str, Any]) -> None:
        data = dict(graph)
        data["cache_version"] = 2
        data["graph_id"] = graph_id
        self.write_json(self.graph_path(graph_id), data)

    def read_profile(self, profile_id: str) -> dict[str, Any] | None:
        data = self.read_json(self.profile_path(profile_id), {})
        if data.get("cache_version") != 2:
            return None
        return data

    def write_profile(self, profile_id: str, profile: dict[str, Any]) -> None:
        data = dict(profile)
        data["cache_version"] = 2
        data["profile_id"] = profile_id
        self.write_json(self.profile_path(profile_id), data)

    def read_plan(self, plan_id: str) -> dict[str, Any] | None:
        data = self.read_json(self.plan_path(plan_id), {})
        if data.get("cache_version") != 2:
            return None
        return data

    def write_plan(self, plan_id: str, plan: dict[str, Any]) -> None:
        data = dict(plan)
        data["cache_version"] = 2
        data["plan_id"] = plan_id
        self.write_json(self.plan_path(plan_id), data)

    def aliases(self) -> dict[str, Any]:
        data = self.read_json(self.config.aliases_v2_file, {"cache_version": 2, "aliases": {}})
        if data.get("cache_version") != 2:
            return {}
        aliases = data.get("aliases")
        return aliases if isinstance(aliases, dict) else {}

    def find_alias(self, alias: str | None) -> dict[str, Any] | None:
        if not alias:
            return None
        value = self.aliases().get(alias)
        return value if isinstance(value, dict) else None

    def bind_alias(self, alias: str, *, graph_id: str, profile_id: str | None, kind: str) -> None:
        data = self.read_json(self.config.aliases_v2_file, {"cache_version": 2, "aliases": {}})
        if data.get("cache_version") != 2:
            data = {"cache_version": 2, "aliases": {}}
        aliases = data.setdefault("aliases", {})
        aliases[alias] = {"graph_id": graph_id, "profile_id": profile_id, "kind": kind}
        self.write_json(self.config.aliases_v2_file, data)
