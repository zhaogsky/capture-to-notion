from __future__ import annotations

from capture_to_notion.models import CaptureInput


INITIALIZED_ALIASES = {"initialized", "初始化", "待处理", "待读", "待听", "想读", "想听", "收藏"}
COMPLETED_ALIASES = {"completed", "完成", "已完成", "已读", "读完", "听完"}


def normalize_state(value: str | None) -> str:
    if value is None:
        return "initialized"
    normalized = value.strip().lower()
    if normalized in COMPLETED_ALIASES:
        return "completed"
    if normalized in INITIALIZED_ALIASES:
        return "initialized"
    return "initialized"


def classify_content_type(capture: CaptureInput) -> str:
    if capture.content_type_hint in {"book", "podcast_episode"}:
        return capture.content_type_hint

    text = capture.raw_input.lower()
    if "播客" in text or "podcast" in text or "/episode" in text:
        return "podcast_episode"
    if "《" in capture.raw_input and "》" in capture.raw_input:
        return "book"
    if "isbn" in text or "书" in capture.raw_input:
        return "book"
    return "unknown"
