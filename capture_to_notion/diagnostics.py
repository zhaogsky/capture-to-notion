from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from capture_to_notion import __version__
from capture_to_notion.config import AppConfig


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



def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None



def _token_configured(config_data: dict[str, Any] | None) -> bool:
    auth: dict[str, Any] = {}
    if config_data:
        notion = config_data.get("notion")
        if isinstance(notion, dict):
            auth_data = notion.get("auth")
            if isinstance(auth_data, dict):
                auth = auth_data

    token = auth.get("token")
    if isinstance(token, str) and token.strip():
        return True

    if "env_token_name" in auth:
        env_token_name = auth.get("env_token_name")
        if not isinstance(env_token_name, str):
            return False
    else:
        env_token_name = "NOTION_TOKEN"
    return bool(os.environ.get(env_token_name))


def _token_next_steps(configured: bool) -> list[str]:
    if configured:
        return []
    return ["Set NOTION_TOKEN or configure notion.auth.env_token_name/token in config.json."]


def _legacy_config_dir_next_steps(legacy_config_dir: Path, exists: bool) -> list[str]:
    if not exists:
        return []
    return [
        f"Review legacy config at {legacy_config_dir}; migrate settings into CAPTURE_TO_NOTION_CONFIG_DIR before deleting it."
    ]


def _stale_target_cache_entries(config: AppConfig) -> list[dict[str, str | None]]:
    stale_entries: list[dict[str, str | None]] = []
    for target_file in sorted(config.targets_dir.glob("*.json")):
        try:
            target_cache = json.loads(target_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(target_cache, dict):
            continue
        data_sources = target_cache.get("data_sources")
        if not isinstance(data_sources, dict):
            continue
        for source_id, source in data_sources.items():
            if not isinstance(source, dict):
                continue
            fields = source.get("fields")
            field_sources = source.get("field_sources")
            if not isinstance(fields, dict) or not fields:
                continue
            if not isinstance(field_sources, dict) or any(key not in field_sources for key in fields):
                target = target_cache.get("target")
                page_id = target.get("page_id") if isinstance(target, dict) else None
                data_source_id = None
                if not isinstance(page_id, str):
                    data_source_id = target.get("data_source_id") if isinstance(target, dict) else None
                    if not isinstance(data_source_id, str):
                        data_source_id = source.get("data_source_id")
                    if not isinstance(data_source_id, str) and isinstance(source_id, str):
                        data_source_id = source_id
                target_title = target.get("title") if isinstance(target, dict) else None
                stale_entries.append(
                    {
                        "target_id": target_file.stem,
                        "target_title": target_title if isinstance(target_title, str) else None,
                        "page_id": page_id if isinstance(page_id, str) else None,
                        "data_source_id": data_source_id if isinstance(data_source_id, str) else None,
                    }
                )
                break
    return stale_entries


def _aliases_by_target_id(config: AppConfig) -> dict[str, str]:
    data = _load_json_file(config.aliases_file) or {}
    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        return {}

    aliases_by_target_id: dict[str, str] = {}
    for alias_name, alias in sorted(aliases.items()):
        if not isinstance(alias_name, str) or not isinstance(alias, dict):
            continue
        target_id = alias.get("target_id")
        if isinstance(target_id, str) and target_id not in aliases_by_target_id:
            aliases_by_target_id[target_id] = alias_name
    return aliases_by_target_id


def _rescan_commands(config: AppConfig, stale_entries: list[dict[str, str | None]]) -> list[str]:
    aliases_by_target_id = _aliases_by_target_id(config)
    commands: list[str] = []
    for entry in stale_entries:
        target_id = entry.get("target_id")
        page_id = entry.get("page_id")
        data_source_id = entry.get("data_source_id")
        if not target_id:
            continue
        scan_source_option = None
        if page_id:
            scan_source_option = f"--page-id {shlex.quote(page_id)}"
        elif data_source_id:
            scan_source_option = f"--data-source-id {shlex.quote(data_source_id)}"
        if not scan_source_option:
            continue
        alias = aliases_by_target_id.get(target_id)
        target_option = f"--alias {shlex.quote(alias)}" if alias else f"--target-id {shlex.quote(target_id)}"
        commands.append(f"capture-to-notion target scan {scan_source_option} {target_option}")
    return commands


def _config_from_root(config_root_path: Path) -> AppConfig:
    return AppConfig(
        root=config_root_path,
        config_file=config_root_path / "config.json",
        aliases_file=config_root_path / "aliases.json",
        routes_file=config_root_path / "routes.json",
        states_file=config_root_path / "states.json",
        targets_dir=config_root_path / "targets",
        plans_dir=config_root_path / "plans",
        logs_dir=config_root_path / "logs",
        covers_dir=config_root_path / "cache" / "assets" / "covers",
    )



def legacy_config_root() -> Path:
    return Path.home() / ".config" / "notion-skill"



_LEGACY_CONFIG_FILE_ALLOWLIST = {"config.json", "states.json", "aliases.json"}



def _legacy_config_files(source_root: Path) -> list[Path]:
    if not source_root.exists() or not source_root.is_dir():
        return []
    return sorted(path for path in source_root.rglob("*") if path.is_file() or path.is_symlink())



def _is_allowed_legacy_config_path(relative_path: str) -> bool:
    if relative_path in _LEGACY_CONFIG_FILE_ALLOWLIST:
        return True
    path = Path(relative_path)
    return len(path.parts) == 2 and path.parts[0] == "targets" and path.suffix == ".json"



def _destination_path_is_safe(config_root_path: Path, destination_path: Path) -> bool:
    if config_root_path.is_symlink():
        return False
    try:
        destination_path.resolve(strict=False).relative_to(config_root_path.resolve(strict=False))
    except ValueError:
        return False
    return True



def _copy_file_exclusive(source_path: Path, destination_path: Path) -> None:
    if source_path.is_symlink() or destination_path.parent.is_symlink():
        raise OSError("unsafe symlink path")

    temp_path: Path | None = None
    try:
        with source_path.open("rb") as source_file:
            with tempfile.NamedTemporaryFile(
                "xb",
                dir=destination_path.parent,
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as destination_file:
                temp_path = Path(destination_file.name)
                shutil.copyfileobj(source_file, destination_file)
                destination_file.flush()
                os.fsync(destination_file.fileno())
        shutil.copystat(source_path, temp_path)
        os.link(temp_path, destination_path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass



def migrate_legacy_config(config_root_path: Path, *, confirmed: bool) -> dict[str, Any]:
    source_root = legacy_config_root()
    pending_copy: list[str] = []
    skipped_existing: list[str] = []
    skipped_disallowed: list[str] = []
    skipped_symlinks: list[str] = []
    skipped_unsafe: list[str] = []
    migrated: list[str] = []
    copy_results: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    source_root_is_symlink = source_root.is_symlink()

    if source_root_is_symlink:
        skipped_symlinks.append(".")

    for source_path in [] if source_root_is_symlink else _legacy_config_files(source_root):
        relative_path = source_path.relative_to(source_root).as_posix()
        if source_path.is_symlink():
            skipped_symlinks.append(relative_path)
            continue
        if not _is_allowed_legacy_config_path(relative_path):
            skipped_disallowed.append(relative_path)
            continue
        destination_path = config_root_path / relative_path
        if not _destination_path_is_safe(config_root_path, destination_path):
            skipped_unsafe.append(relative_path)
        elif destination_path.exists():
            skipped_existing.append(relative_path)
            copy_results.append({"path": relative_path, "status": "skipped_existing"})
        else:
            pending_copy.append(relative_path)

    if confirmed and source_root.exists() and not source_root_is_symlink and pending_copy:
        try:
            config_root_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            for relative_path in pending_copy:
                error_result = {
                    "path": relative_path,
                    "error_code": "copy_os_error",
                    "message": "copy failed due to OS error",
                }
                errors.append(error_result)
                copy_results.append({"path": relative_path, "status": "error", **error_result})
            pending_copy = []
        for relative_path in pending_copy:
            source_path = source_root / relative_path
            destination_path = config_root_path / relative_path
            if not _destination_path_is_safe(config_root_path, destination_path):
                skipped_unsafe.append(relative_path)
                continue
            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                error_result = {
                    "path": relative_path,
                    "error_code": "copy_os_error",
                    "message": "copy failed due to OS error",
                }
                errors.append(error_result)
                copy_results.append({"path": relative_path, "status": "error", **error_result})
                continue
            if destination_path.exists():
                skipped_existing.append(relative_path)
                copy_results.append({"path": relative_path, "status": "skipped_existing"})
                continue
            try:
                _copy_file_exclusive(source_path, destination_path)
            except FileExistsError:
                skipped_existing.append(relative_path)
                copy_results.append({"path": relative_path, "status": "skipped_existing"})
                continue
            except OSError:
                error_result = {
                    "path": relative_path,
                    "error_code": "copy_os_error",
                    "message": "copy failed due to OS error",
                }
                errors.append(error_result)
                copy_results.append({"path": relative_path, "status": "error", **error_result})
                continue
            migrated.append(relative_path)
            copy_results.append({"path": relative_path, "status": "migrated"})
        pending_copy = []

    warnings: list[str] = []
    if not source_root.exists():
        warnings.append(f"No legacy config directory found at {source_root}.")
    elif not confirmed and pending_copy:
        warnings.append("Dry run only. Re-run with --confirmed to copy pending files.")
    if skipped_disallowed:
        warnings.append("Skipped non-config or runtime/transient legacy files outside the migration allowlist.")
    if skipped_symlinks:
        warnings.append("Skipped legacy symlink paths to avoid copying linked file contents.")
    if skipped_unsafe:
        warnings.append("Skipped legacy files whose destination would resolve outside the configured config root.")
    if errors:
        warnings.append("Some legacy files could not be copied due to OS errors.")
    warnings.append("Legacy config directory was not deleted.")

    return {
        "source_root": str(source_root),
        "destination_root": str(config_root_path),
        "legacy_exists": source_root.exists(),
        "confirmed": confirmed,
        "pending_copy": pending_copy,
        "migrated": migrated,
        "skipped_existing": skipped_existing,
        "skipped_disallowed": skipped_disallowed,
        "skipped_symlinks": skipped_symlinks,
        "skipped_unsafe": skipped_unsafe,
        "copy_results": copy_results,
        "errors": errors,
        "warnings": warnings,
    }


def doctor_report(config: AppConfig | Path) -> dict[str, Any]:
    if isinstance(config, AppConfig):
        app_config = config
        config_root_path = config.root
    else:
        config_root_path = config
        app_config = _config_from_root(config_root_path)
    config_file = app_config.config_file
    config_data = _load_json_file(config_file)
    legacy_config_dir = Path.home() / ".config" / "notion-skill"
    parent_dir = config_root_path.parent

    stale_target_entries = _stale_target_cache_entries(app_config)
    missing_field_sources = [entry["target_id"] for entry in stale_target_entries if entry.get("target_id")]
    rescan_commands = _rescan_commands(app_config, stale_target_entries)
    targets_requiring_rescan = []
    for entry in stale_target_entries:
        if not entry.get("target_id"):
            continue
        rescan_target = {
            "target_id": entry.get("target_id"),
            "target_title": entry.get("target_title"),
            "page_id": entry.get("page_id"),
        }
        if entry.get("data_source_id"):
            rescan_target["data_source_id"] = entry.get("data_source_id")
        targets_requiring_rescan.append(rescan_target)
    field_sources_status = "warning" if missing_field_sources else "ok"
    field_sources_message = (
        "Rescan these targets to record mapping field_sources."
        if missing_field_sources
        else "All cached targets with fields include field_sources."
    )
    token_configured = _token_configured(config_data)
    legacy_config_dir_exists = legacy_config_dir.exists()

    field_sources_details = {
        "targets_missing_field_sources": missing_field_sources,
        "rescan_commands": rescan_commands,
        "message": field_sources_message,
    }
    if targets_requiring_rescan:
        field_sources_details["targets_requiring_rescan"] = targets_requiring_rescan

    report = version_info(config_root_path)
    report["checks"] = {
        "config_root": {
            "path": str(config_root_path),
            "exists": config_root_path.exists(),
            "is_dir": config_root_path.is_dir(),
            "writable": os.access(config_root_path, os.W_OK) if config_root_path.exists() else False,
            "parent_exists": parent_dir.exists(),
            "parent_writable": os.access(parent_dir, os.W_OK) if parent_dir.exists() else False,
        },
        "config_file": {
            "path": str(config_file),
            "exists": config_file.exists(),
            "readable": os.access(config_file, os.R_OK) if config_file.exists() else False,
            "valid_json": config_data is not None,
        },
        "token": {
            "configured": token_configured,
            "source": "redacted",
            "next_steps": _token_next_steps(token_configured),
        },
        "legacy_config_dir": {
            "path": str(legacy_config_dir),
            "exists": legacy_config_dir_exists,
            "next_steps": _legacy_config_dir_next_steps(legacy_config_dir, legacy_config_dir_exists),
        },
        "target_cache_field_sources": {
            "name": "target_cache_field_sources",
            "status": field_sources_status,
            "details": field_sources_details,
        },
    }
    return report
