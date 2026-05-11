# Capture to Notion P0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P0 foundation for `capture-to-notion`: safety checks, CLI compatibility tests, migration history, `version`, and `doctor` diagnostics.

**Architecture:** Keep the current CLI structure in `capture_to_notion/cli.py`, but move reusable diagnostics into a small focused module. Tests should cover real CLI entrypoints, old-name regressions, secret redaction, and diagnostic output before implementation code is changed.

**Tech Stack:** Python 3.11+, pytest, argparse, stdlib `json`, `pathlib`, `os`, `shutil`, `sys`.

---

## File Structure

### Files to create

- `/Users/aaron/.claude/skills/capture-to-notion/CHANGELOG.md`
  - Records rename and migration history.
- `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py`
  - Owns `version` and `doctor` data collection.
- `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`
  - Tests P0 safety, naming, version, and doctor behavior.

### Files to modify

- `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`
  - Adds `version` and `doctor` commands.
  - Keeps write operations gated by existing confirmation checks.
- `/Users/aaron/.claude/skills/capture-to-notion/README.md`
  - Adds short links to `doctor`, `version`, and changelog.
- `/Users/aaron/.claude/skills/capture-to-notion/README.zh-CN.md`
  - Adds short links to `doctor`, `version`, and changelog.

### Files to inspect but not necessarily modify

- `/Users/aaron/.claude/skills/capture-to-notion/pyproject.toml`
  - Must remain `name = "capture-to-notion"` and script `capture-to-notion = "capture_to_notion.cli:main"`.
- `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/config.py`
  - Existing config root must remain `CAPTURE_TO_NOTION_CONFIG_DIR` or `~/.config/capture-to-notion`.
- `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/notion_adapter.py`
  - Token handling must not print token values.

---

## Task 1: Add P0 CLI compatibility and naming regression tests

**Files:**
- Create: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`
- Read only: `/Users/aaron/.claude/skills/capture-to-notion/pyproject.toml`
- Read only: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/config.py`
- Read only: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`

- [ ] **Step 1: Write failing tests for real CLI entrypoint expectations**

Create `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py` with this initial content:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CAPTURE_TO_NOTION_CONFIG_DIR"] = str(tmp_path / "config")
    return subprocess.run(
        [sys.executable, "-m", "capture_to_notion.cli", *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_cli_help_uses_capture_to_notion_name(tmp_path: Path) -> None:
    result = run_cli(["--help"], tmp_path)

    assert result.returncode == 0
    assert "usage: capture-to-notion" in result.stdout
    assert "notion-skill" not in result.stdout
    assert "notion-capture" not in result.stdout


def test_python_package_imports_from_current_package() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import capture_to_notion; print(capture_to_notion.__version__)"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0.1.0"
```

- [ ] **Step 2: Run the new tests to verify baseline**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py -v
```

Expected result before adding later tests:

```text
2 passed
```

If this fails, stop and fix compatibility before continuing.

- [ ] **Step 3: Add runtime-file old-name regression test**

Append this to `tests/test_p0_foundation.py`:

```python

def test_runtime_files_do_not_reference_old_runtime_names() -> None:
    runtime_files = [
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "capture_to_notion" / "cli.py",
        PROJECT_ROOT / "capture_to_notion" / "config.py",
        PROJECT_ROOT / "capture_to_notion" / "notion_adapter.py",
        PROJECT_ROOT / "SKILL.md",
    ]
    forbidden = [
        "notion-skill",
        "notion_skill",
        "notion-capture",
        "NOTION_SKILL_CONFIG_DIR",
        ".config/notion-skill",
    ]

    violations: list[str] = []
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)} contains {token}")

    assert violations == []
```

- [ ] **Step 4: Run old-name regression test**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_runtime_files_do_not_reference_old_runtime_names -v
```

Expected:

```text
PASSED
```

- [ ] **Step 5: Run full current test suite**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Expected:

```text
all tests pass
```

Do not commit unless the user explicitly asks.

---

## Task 2: Add CHANGELOG migration history

**Files:**
- Create: `/Users/aaron/.claude/skills/capture-to-notion/CHANGELOG.md`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`

- [ ] **Step 1: Write failing test for changelog migration record**

Append this to `tests/test_p0_foundation.py`:

```python

def test_changelog_records_capture_to_notion_rename() -> None:
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")

    assert "capture-to-notion" in text
    assert "notion-skill" in text
    assert "notion-capture" in text
    assert "notion_skill" in text
    assert "capture_to_notion" in text
    assert "~/.config/capture-to-notion" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_changelog_records_capture_to_notion_rename -v
```

Expected:

```text
FAILED ... FileNotFoundError
```

- [ ] **Step 3: Create minimal changelog**

Create `/Users/aaron/.claude/skills/capture-to-notion/CHANGELOG.md`:

```markdown
# Changelog

## 0.1.0 - 2026-05-11

### Renamed

- Renamed the Skill and CLI workflow to `capture-to-notion`.
- Replaced the old CLI/package names `notion-skill`, `notion-capture`, and `notion_skill` with `capture-to-notion` and `capture_to_notion`.
- Standardized the local configuration directory as `~/.config/capture-to-notion`.

### Migration Notes

- Reinstall the editable CLI with:

```bash
uv tool install --force --editable /Users/aaron/.claude/skills/capture-to-notion
```

- The old `notion-skill` CLI should not be used.
- If a previous local configuration exists under `~/.config/notion-skill`, migrate it deliberately rather than copying secrets into the Skill directory.
- Notion tokens belong in the tool's own local config or configured environment variable, not in Claude Code global settings and not in this Skill directory.
```

- [ ] **Step 4: Run changelog test to verify it passes**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_changelog_records_capture_to_notion_rename -v
```

Expected:

```text
PASSED
```

Do not commit unless the user explicitly asks.

---

## Task 3: Add `version` command

**Files:**
- Create: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`

- [ ] **Step 1: Write failing test for `version` JSON output**

Append this to `tests/test_p0_foundation.py`:

```python

def test_version_outputs_runtime_paths_without_secrets(tmp_path: Path) -> None:
    result = run_cli(["version"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["command"] == "capture-to-notion"
    assert data["version"] == "0.1.0"
    assert data["package"] == "capture_to_notion"
    assert data["config_root"] == str(tmp_path / "config")
    assert data["skill_path"].endswith("capture-to-notion")
    assert "token" not in result.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails because command does not exist**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_version_outputs_runtime_paths_without_secrets -v
```

Expected:

```text
FAILED ... invalid choice: 'version'
```

- [ ] **Step 3: Add diagnostics module**

Create `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py`:

```python
from __future__ import annotations

import sys
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


def version_info(config: AppConfig) -> dict[str, Any]:
    return {
        "command": COMMAND_NAME,
        "version": __version__,
        "package": PACKAGE_NAME,
        "python": sys.version.split()[0],
        "package_path": str(Path(__file__).resolve().parent),
        "skill_path": str(skill_path()),
        "config_root": str(config.root),
        "editable_install": is_editable_install(),
    }
```

- [ ] **Step 4: Wire `version` command into CLI**

Modify `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`.

Add import near existing imports:

```python
from capture_to_notion.diagnostics import version_info
```

Add command function after `cmd_cache_inspect`:

```python

def cmd_version(args: argparse.Namespace) -> int:
    config = ensure_config()
    print_json(version_info(config))
    return 0
```

Add parser wiring in `build_parser()` after subparsers are created:

```python
    version_parser = subparsers.add_parser("version")
    version_parser.set_defaults(func=cmd_version)
```

- [ ] **Step 5: Run version test to verify it passes**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_version_outputs_runtime_paths_without_secrets -v
```

Expected:

```text
PASSED
```

- [ ] **Step 6: Run full test suite**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Expected:

```text
all tests pass
```

Do not commit unless the user explicitly asks.

---

## Task 4: Add `doctor` command with read-only diagnostics

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`

- [ ] **Step 1: Write failing test for healthy doctor output**

Append this to `tests/test_p0_foundation.py`:

```python

def test_doctor_reports_config_and_token_without_revealing_secret(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    secret = "secret-notion-token-value"
    (config_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": secret}, "default_workspace": "default"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    checks = {check["name"]: check for check in data["checks"]}
    assert checks["config_root"]["status"] == "ok"
    assert checks["config_file"]["status"] == "ok"
    assert checks["notion_token"]["status"] == "ok"
    assert secret not in result.stdout
```

- [ ] **Step 2: Write failing test for old config warning**

Append this to `tests/test_p0_foundation.py`:

```python

def test_doctor_warns_when_legacy_config_dir_exists(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    legacy_dir = home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    checks = {check["name"]: check for check in data["checks"]}
    assert checks["legacy_config_dir"]["status"] == "warning"
    assert "~/.config/notion-skill" in checks["legacy_config_dir"]["message"]
```

- [ ] **Step 3: Run doctor tests to verify they fail**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_doctor_reports_config_and_token_without_revealing_secret tests/test_p0_foundation.py::test_doctor_warns_when_legacy_config_dir_exists -v
```

Expected:

```text
FAILED ... invalid choice: 'doctor'
```

- [ ] **Step 4: Add doctor diagnostics implementation**

Append to `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/diagnostics.py`:

```python
import json
import os


def check_result(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}


def _config_json(config: AppConfig) -> dict[str, Any]:
    try:
        data = json.loads(config.config_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _path_writable(path: Path) -> bool:
    return path.exists() and os.access(path, os.W_OK)


def doctor_report(config: AppConfig) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    checks.append(check_result("command", "ok", COMMAND_NAME))
    checks.append(check_result("package", "ok", PACKAGE_NAME))
    checks.append(check_result("config_root", "ok" if config.root.exists() else "error", str(config.root)))
    checks.append(check_result("config_file", "ok" if config.config_file.exists() else "error", str(config.config_file)))

    data = _config_json(config)
    auth_config = data.get("notion", {}).get("auth", {}) if isinstance(data.get("notion"), dict) else {}
    env_token_name = auth_config.get("env_token_name", "NOTION_TOKEN")
    has_config_token = bool(auth_config.get("token"))
    has_env_token = bool(os.environ.get(env_token_name))
    if has_config_token:
        checks.append(check_result("notion_token", "ok", "configured in local config"))
    elif has_env_token:
        checks.append(check_result("notion_token", "ok", f"configured via environment variable {env_token_name}"))
    else:
        checks.append(check_result("notion_token", "warning", f"missing local token and environment variable {env_token_name}"))

    for name, path in [
        ("targets_dir", config.targets_dir),
        ("plans_dir", config.plans_dir),
        ("logs_dir", config.logs_dir),
        ("covers_dir", config.covers_dir),
    ]:
        checks.append(check_result(name, "ok" if _path_writable(path) else "error", str(path)))

    legacy_config_dir = Path.home() / ".config" / "notion-skill"
    if legacy_config_dir.exists():
        checks.append(check_result("legacy_config_dir", "warning", "legacy config exists at ~/.config/notion-skill"))
    else:
        checks.append(check_result("legacy_config_dir", "ok", "no legacy config directory found"))

    ok = all(check["status"] != "error" for check in checks)
    return {"ok": ok, "checks": checks}
```

- [ ] **Step 5: Wire `doctor` command into CLI**

Modify `/Users/aaron/.claude/skills/capture-to-notion/capture_to_notion/cli.py`.

Change diagnostics import to:

```python
from capture_to_notion.diagnostics import doctor_report, version_info
```

Add command function after `cmd_version`:

```python

def cmd_doctor(args: argparse.Namespace) -> int:
    config = ensure_config()
    print_json(doctor_report(config))
    return 0
```

Add parser wiring in `build_parser()` after `version`:

```python
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.set_defaults(func=cmd_doctor)
```

- [ ] **Step 6: Run doctor tests to verify they pass**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_doctor_reports_config_and_token_without_revealing_secret tests/test_p0_foundation.py::test_doctor_warns_when_legacy_config_dir_exists -v
```

Expected:

```text
PASSED
```

- [ ] **Step 7: Run full test suite**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Expected:

```text
all tests pass
```

Do not commit unless the user explicitly asks.

---

## Task 5: Preserve apply safety and token redaction in tests

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`
- Existing coverage: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_capture_apply.py`

- [ ] **Step 1: Add test that doctor and version do not call Notion adapter**

Append this to `tests/test_p0_foundation.py`:

```python

def test_doctor_and_version_do_not_initialize_notion_adapter(tmp_path: Path, monkeypatch) -> None:
    import capture_to_notion.cli as cli_module

    def fail_from_config(config):
        raise AssertionError("doctor/version must not initialize NotionAdapter")

    monkeypatch.setattr(cli_module.NotionAdapter, "from_config", fail_from_config)

    version_exit = cli_module.main(["version"])
    doctor_exit = cli_module.main(["doctor"])

    assert version_exit == 0
    assert doctor_exit == 0
```

- [ ] **Step 2: Run the test**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_doctor_and_version_do_not_initialize_notion_adapter -v
```

Expected after Tasks 3 and 4:

```text
PASSED
```

- [ ] **Step 3: Add test that apply still requires confirmation before adapter**

Do not duplicate existing coverage. Verify this existing test still passes:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_capture_apply.py::test_capture_apply_requires_confirmation_before_adapter -v
```

Expected:

```text
PASSED
```

- [ ] **Step 4: Run full test suite**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Expected:

```text
all tests pass
```

Do not commit unless the user explicitly asks.

---

## Task 6: Update README files with P0 commands

**Files:**
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/README.md`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/README.zh-CN.md`
- Modify: `/Users/aaron/.claude/skills/capture-to-notion/tests/test_p0_foundation.py`

- [ ] **Step 1: Write failing README command coverage test**

Append this to `tests/test_p0_foundation.py`:

```python

def test_readmes_document_p0_commands() -> None:
    english = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for text in (english, chinese):
        assert "capture-to-notion doctor" in text
        assert "capture-to-notion version" in text
        assert "CHANGELOG.md" in text
```

- [ ] **Step 2: Run test to verify it fails before README update**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_readmes_document_p0_commands -v
```

Expected:

```text
FAILED
```

- [ ] **Step 3: Update English README common commands**

In `/Users/aaron/.claude/skills/capture-to-notion/README.md`, add these entries under `## Common Commands` after help/cache inspect:

```markdown
Show runtime version and paths:

```bash
capture-to-notion version
```

Check local configuration and runtime health:

```bash
capture-to-notion doctor
```

See migration notes and command history in [`CHANGELOG.md`](CHANGELOG.md).
```

- [ ] **Step 4: Update Chinese README common commands**

In `/Users/aaron/.claude/skills/capture-to-notion/README.zh-CN.md`, add these entries under `## 常用命令` after help/cache inspect:

```markdown
查看当前运行版本和路径：

```bash
capture-to-notion version
```

检查本地配置和运行环境：

```bash
capture-to-notion doctor
```

命名迁移和历史变化见 [`CHANGELOG.md`](CHANGELOG.md)。
```

- [ ] **Step 5: Run README test to verify it passes**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest tests/test_p0_foundation.py::test_readmes_document_p0_commands -v
```

Expected:

```text
PASSED
```

- [ ] **Step 6: Run full test suite**

Run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Expected:

```text
all tests pass
```

Do not commit unless the user explicitly asks.

---

## Final Verification

After all tasks are complete, run:

```bash
uv --directory /Users/aaron/.claude/skills/capture-to-notion run --with pytest python -m pytest
```

Expected:

```text
all tests pass
```

Then manually verify the user-facing commands:

```bash
capture-to-notion --help
capture-to-notion version
capture-to-notion doctor
```

Expected:

- `--help` shows `usage: capture-to-notion`.
- `version` prints JSON with command, version, package path, skill path, and config root.
- `doctor` prints JSON checks and never prints token values.

## Self-Review Notes

Spec coverage:

- Safety and side-effect control: covered by preserving existing apply confirmation tests and adding doctor/version no-adapter test.
- Code compatibility: covered by CLI help and package import tests.
- Version and migration governance: covered by old-name regression test and CHANGELOG.
- Observability/debuggability: covered by `version` and `doctor`.

No placeholders remain in the task steps. All file paths, commands, and expected outcomes are explicit.
