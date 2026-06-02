from __future__ import annotations

from typing import Any

from capture_to_notion.config import DEFAULT_STATES
from capture_to_notion.models import CaptureInput


def _state_aliases(states_config: dict[str, Any] | None) -> dict[str, str]:
    config = states_config if isinstance(states_config, dict) else DEFAULT_STATES
    states = config.get("states", {})
    if not isinstance(states, dict):
        return {}

    aliases: dict[str, str] = {}
    for canonical, state_config in states.items():
        if not isinstance(canonical, str):
            continue
        aliases[canonical.strip().lower()] = canonical
        raw_aliases = state_config.get("aliases", []) if isinstance(state_config, dict) else []
        if not isinstance(raw_aliases, list):
            continue
        for alias in raw_aliases:
            if isinstance(alias, str):
                aliases[alias.strip().lower()] = canonical
    return aliases


def normalize_state(
    value: str | None,
    states_config: dict[str, Any] | None = None,
    default_state: str = "initialized",
) -> str:
    if value is None:
        return default_state
    normalized = value.strip().lower()
    return _state_aliases(states_config).get(normalized, default_state)


def classify_content_type(capture: CaptureInput) -> str:
    if isinstance(capture.content_type_hint, str) and capture.content_type_hint.strip():
        return capture.content_type_hint.strip()

    text = capture.raw_input.lower()
    if "播客" in text or "podcast" in text or "/episode" in text:
        return "podcast_episode"
    if "《" in capture.raw_input and "》" in capture.raw_input:
        return "book"
    if "isbn" in text or "书" in capture.raw_input:
        return "book"
    return "unknown"
