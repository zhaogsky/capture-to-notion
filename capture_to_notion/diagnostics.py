from __future__ import annotations

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
