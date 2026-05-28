"""Utilities for rendering generic Notion page body blocks."""

from __future__ import annotations

import re
from typing import Any

_MAX_RICH_TEXT_CHARS = 1900
_NUMBERED_LIST_RE = re.compile(r"^\d\.\s+(.*)$")


def build_body_blocks(raw_input: str, *, title: str) -> list[dict[str, Any]]:
    """Convert basic Markdown-like text into Notion block payloads."""
    blocks: list[dict[str, Any]] = []
    paragraph_lines: list[str] = []
    lines = raw_input.splitlines()
    index = 0

    def flush_paragraph() -> None:
        if paragraph_lines:
            blocks.extend(_text_blocks("paragraph", "\n".join(paragraph_lines)))
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _is_title_line(stripped, title=title):
            flush_paragraph()
            index += 1
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.extend(_code_blocks("\n".join(code_lines)))
            continue

        if stripped == "":
            flush_paragraph()
            index += 1
            continue

        if stripped == "---":
            flush_paragraph()
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            index += 1
            continue

        block_type, content = _line_block(stripped)
        if block_type is None:
            paragraph_lines.append(line)
        else:
            flush_paragraph()
            blocks.extend(_text_blocks(block_type, content))

        index += 1

    flush_paragraph()
    return blocks


def split_block_batches(
    blocks: list[dict[str, Any]], *, limit: int = 100
) -> list[list[dict[str, Any]]]:
    """Split blocks into batches respecting Notion's child append limit."""
    if limit <= 0:
        raise ValueError("limit must be greater than 0")
    return [blocks[index : index + limit] for index in range(0, len(blocks), limit)]


def _is_title_line(line: str, *, title: str) -> bool:
    return line in {title, f"# {title}", f"标题：{title}", f"Title: {title}"}


def _line_block(line: str) -> tuple[str | None, str]:
    if line.startswith("### "):
        return "heading_3", line[4:]
    if line.startswith("## "):
        return "heading_2", line[3:]
    if line.startswith("# "):
        return "heading_1", line[2:]
    if line.startswith("- "):
        return "bulleted_list_item", line[2:]
    numbered_match = _NUMBERED_LIST_RE.match(line)
    if numbered_match:
        return "numbered_list_item", numbered_match.group(1)
    if line.startswith("> "):
        return "quote", line[2:]
    return None, line


def _text_blocks(block_type: str, content: str) -> list[dict[str, Any]]:
    return [
        {
            "object": "block",
            "type": block_type,
            block_type: {"rich_text": [_rich_text(chunk)]},
        }
        for chunk in _chunks(content)
    ]


def _code_blocks(content: str) -> list[dict[str, Any]]:
    return [
        {
            "object": "block",
            "type": "code",
            "code": {"rich_text": [_rich_text(chunk)], "language": "plain text"},
        }
        for chunk in _chunks(content)
    ]


def _rich_text(content: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": content}}


def _chunks(content: str) -> list[str]:
    if content == "":
        return [""]
    return [
        content[index : index + _MAX_RICH_TEXT_CHARS]
        for index in range(0, len(content), _MAX_RICH_TEXT_CHARS)
    ]
