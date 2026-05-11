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

    def target_reference(
        self, alias_name: str | None = None, target_id: str | None = None
    ) -> dict[str, str | None] | None:
        aliases = self.aliases()
        resolved_alias = alias_name
        if alias_name:
            alias = aliases.get(alias_name)
            if not isinstance(alias, dict):
                return None
            alias_target_id = alias.get("target_id")
            return {
                "alias": resolved_alias,
                "target_id": alias_target_id if isinstance(alias_target_id, str) else None,
            }

        if not isinstance(target_id, str):
            return None
        for candidate_alias, alias in sorted(aliases.items()):
            if isinstance(alias, dict) and alias.get("target_id") == target_id:
                resolved_alias = candidate_alias
                break
        return {"alias": resolved_alias, "target_id": target_id}

    def _read_target_cache(self, target_id: str) -> tuple[dict[str, Any] | None, str]:
        path = self.config.targets_dir / f"{target_id}.json"
        if not path.exists():
            return None, "missing_cache"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None, "invalid_cache"
        if not isinstance(data, dict):
            return None, "invalid_cache"
        return data, "cached"

    def target_cache_status(self, target_id: str) -> str:
        return self._read_target_cache(target_id)[1]

    def target_structure(self, target_id: str | None) -> dict[str, Any] | None:
        if not target_id:
            return None
        structure, status = self._read_target_cache(target_id)
        if status == "missing_cache":
            return None
        if status == "invalid_cache":
            return {}
        return structure

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

    def target_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for alias_name, alias in sorted(self.aliases().items()):
            if not isinstance(alias, dict):
                continue
            target_id = alias.get("target_id")
            if isinstance(target_id, str):
                structure, status = self._read_target_cache(target_id)
            else:
                structure, status = None, "missing_cache"
            if structure is None:
                summaries.append(
                    {
                        "alias": alias_name,
                        "target_id": target_id,
                        "page_id": alias.get("page_id"),
                        "title": None,
                        "description": alias.get("description"),
                        "data_sources": [],
                        "content_types": [],
                        "verified_at": None,
                        "status": status,
                    }
                )
                continue
            data_sources = structure.get("data_sources", {})
            source_titles: list[str] = []
            content_types: set[str] = set()
            if isinstance(data_sources, dict):
                for data_source in data_sources.values():
                    if not isinstance(data_source, dict):
                        continue
                    title = data_source.get("title")
                    if isinstance(title, str):
                        source_titles.append(title)
                    raw_content_types = data_source.get("content_types", [])
                    if isinstance(raw_content_types, (list, tuple, set)):
                        for content_type in raw_content_types:
                            if isinstance(content_type, str):
                                content_types.add(content_type)
            target = structure.get("target", {}) if isinstance(structure.get("target"), dict) else {}
            summaries.append(
                {
                    "alias": alias_name,
                    "target_id": target_id,
                    "page_id": alias.get("page_id") or target.get("page_id"),
                    "title": target.get("title"),
                    "description": alias.get("description"),
                    "data_sources": source_titles,
                    "content_types": sorted(content_types),
                    "verified_at": target.get("verified_at"),
                    "status": "cached",
                }
            )
        return summaries

    def target_detail(self, alias_name: str | None = None, target_id: str | None = None) -> dict[str, Any] | None:
        aliases = self.aliases()
        resolved_alias = alias_name
        if alias_name:
            alias = aliases.get(alias_name)
            if not isinstance(alias, dict):
                return None
            target_id = alias.get("target_id")
        else:
            for candidate_alias, alias in sorted(aliases.items()):
                if isinstance(alias, dict) and alias.get("target_id") == target_id:
                    resolved_alias = candidate_alias
                    break

        if not isinstance(target_id, str):
            return None

        structure, status = self._read_target_cache(target_id)
        if structure is None:
            return None

        reference = self.target_reference(alias_name=alias_name, target_id=target_id)
        if reference is not None:
            resolved_alias = reference["alias"]

        raw_target = structure.get("target")
        target: dict[str, Any] = {}
        if isinstance(raw_target, dict):
            for field in ("page_id", "title", "verified_at"):
                value = raw_target.get(field)
                if value is not None:
                    target[field] = value

        raw_data_sources = structure.get("data_sources", {})
        data_sources: list[dict[str, Any]] = []
        if isinstance(raw_data_sources, dict):
            for key, data_source in sorted(raw_data_sources.items()):
                if not isinstance(data_source, dict):
                    continue
                data_sources.append(
                    {
                        "key": key,
                        "data_source_id": data_source.get("data_source_id"),
                        "title": data_source.get("title"),
                        "role": data_source.get("role"),
                        "content_types": data_source.get("content_types"),
                        "schema_hash": data_source.get("schema_hash"),
                        "fields": data_source.get("fields"),
                    }
                )

        return {
            "alias": resolved_alias,
            "target_id": target_id,
            "target_file": str(self.config.targets_dir / f"{target_id}.json"),
            "target": target,
            "data_sources": data_sources,
            "state_mapping": structure.get("state_mapping"),
            "asset_mapping": structure.get("asset_mapping"),
            "status": status,
        }

    def save_plan(self, plan_id: str, data: dict[str, Any]) -> Path:
        path = self.config.plans_dir / f"{plan_id}.json"
        self.write_json(path, data)
        return path
