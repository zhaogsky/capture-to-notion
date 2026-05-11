from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CaptureOptions:
    allow_web_search: bool = True
    allow_target_search: bool = True
    allow_asset_download: bool = True
    dry_run: bool = False


@dataclass
class CaptureInput:
    raw_input: str
    target_hint: str | None = None
    state: str | None = "initialized"
    content_type_hint: str | None = None
    user_intent: str = "capture_to_notion"
    options: CaptureOptions = field(default_factory=CaptureOptions)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptureInput":
        option_data = data.get("options", {})
        allowed_option_keys = set(CaptureOptions.__dataclass_fields__.keys())
        filtered_option_data = {
            key: value
            for key, value in option_data.items()
            if key in allowed_option_keys
        }
        options = CaptureOptions(**filtered_option_data)
        return cls(
            raw_input=data["raw_input"],
            target_hint=data.get("target_hint"),
            state=data.get("state", "initialized"),
            content_type_hint=data.get("content_type_hint"),
            user_intent=data.get("user_intent", "capture_to_notion"),
            options=options,
        )


@dataclass
class Target:
    page_title: str | None
    page_id: str | None
    data_source_id: str | None
    confidence: str
    source: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Target":
        return cls(
            page_title=data["page_title"],
            page_id=data["page_id"],
            data_source_id=data["data_source_id"],
            confidence=data["confidence"],
            source=data["source"],
        )


@dataclass
class AssetOperation:
    type: str
    source_url: str | None
    local_cache_path: str | None
    target_field: str | None
    action: str
    record_key: str = "cover"
    status: str = "planned"
    warning: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssetOperation":
        return cls(
            type=data["type"],
            source_url=data["source_url"],
            local_cache_path=data["local_cache_path"],
            target_field=data["target_field"],
            action=data["action"],
            record_key=data.get("record_key", "cover"),
            status=data.get("status", "planned"),
            warning=data.get("warning"),
        )


@dataclass
class WritePlan:
    plan_id: str
    content_type: str
    target: Target
    summary: dict[str, Any] = field(default_factory=dict, kw_only=True)
    normalized_record: dict[str, Any]
    field_mapping: dict[str, str]
    operations: list[dict[str, Any]]
    asset_operations: list[AssetOperation]
    sources: list[dict[str, str]]
    warnings: list[str]
    requires_confirmation: bool
    confirmation_reason: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WritePlan":
        return cls(
            plan_id=data["plan_id"],
            content_type=data["content_type"],
            target=Target.from_dict(data["target"]),
            summary=data.get("summary", {}),
            normalized_record=data["normalized_record"],
            field_mapping=data["field_mapping"],
            operations=data["operations"],
            asset_operations=[
                AssetOperation.from_dict(operation)
                for operation in data["asset_operations"]
            ],
            sources=data["sources"],
            warnings=data["warnings"],
            requires_confirmation=data["requires_confirmation"],
            confirmation_reason=data["confirmation_reason"],
        )

    def to_dict(self) -> dict[str, Any]:
        asset_operations = [asdict(operation) for operation in self.asset_operations]
        for operation in asset_operations:
            if operation.get("record_key") == "cover":
                operation.pop("record_key")
        return {
            "plan_id": self.plan_id,
            "content_type": self.content_type,
            "target": asdict(self.target),
            "summary": self.summary,
            "normalized_record": self.normalized_record,
            "field_mapping": self.field_mapping,
            "operations": self.operations,
            "asset_operations": asset_operations,
            "sources": self.sources,
            "warnings": self.warnings,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_reason": self.confirmation_reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")
