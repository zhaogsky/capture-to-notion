from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping

import capture_to_notion
import capture_to_notion.cli


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    args: list[str],
    tmp_path: Path,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CAPTURE_TO_NOTION_CONFIG_DIR"] = str(tmp_path / "config")
    if extra_env:
        env.update(extra_env)
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
    assert result.stdout.strip() == capture_to_notion.__version__


def test_version_does_not_create_config_files(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"

    result = run_cli(["version"], tmp_path)

    assert result.returncode == 0
    assert not config_dir.exists()



def test_version_does_not_initialize_notion_adapter_or_leak_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret = "secret-token-value"
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NOTION_TOKEN", secret)

    def fail_from_config(config):
        raise AssertionError(f"NotionAdapter.from_config should not run: {secret}")

    monkeypatch.setattr(
        capture_to_notion.cli.NotionAdapter,
        "from_config",
        fail_from_config,
    )

    result = capture_to_notion.cli.main(["version"])
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["command"] == "capture-to-notion"
    assert secret not in captured.out
    assert secret not in captured.err



def test_version_outputs_runtime_paths_without_secrets(tmp_path: Path) -> None:
    result = run_cli(["version"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["command"] == "capture-to-notion"
    assert data["version"] == capture_to_notion.__version__
    assert data["package"] == "capture_to_notion"
    assert data["config_root"] == str(tmp_path / "config")
    assert data["skill_path"].endswith("capture-to-notion")
    assert "token" not in result.stdout.lower()


def test_doctor_reports_nested_config_token_without_revealing_secret(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": "secret-token-value"}}}),
        encoding="utf-8",
    )

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["command"] == "capture-to-notion"
    assert data["version"] == capture_to_notion.__version__
    assert data["package"] == "capture_to_notion"
    assert data["config_root"] == str(config_dir)
    assert data["checks"]["config_root"]["path"] == str(config_dir)
    assert data["checks"]["config_file"]["exists"] is True
    assert data["checks"]["config_file"]["valid_json"] is True
    assert data["checks"]["token"]["configured"] is True
    assert "secret-token-value" not in result.stdout
    assert result.stderr == ""


def test_doctor_reports_default_notion_token_env_without_revealing_secret(tmp_path: Path) -> None:
    result = run_cli(["doctor"], tmp_path, extra_env={"NOTION_TOKEN": "secret-token-value"})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["config_file"]["exists"] is False
    assert data["checks"]["config_file"]["valid_json"] is False
    assert data["checks"]["token"]["configured"] is True
    assert "secret-token-value" not in result.stdout
    assert result.stderr == ""


def test_doctor_does_not_accept_legacy_top_level_notion_token(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion_token": "secret-token-value"}),
        encoding="utf-8",
    )

    result = run_cli(["doctor"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["token"]["configured"] is False
    assert "secret-token-value" not in result.stdout
    assert result.stderr == ""


def test_doctor_does_not_initialize_notion_adapter_or_leak_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret = "secret-token-value"
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion": {"auth": {"token": secret}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(config_dir))

    def fail_from_config(config):
        raise AssertionError(f"NotionAdapter.from_config should not run: {secret}")

    monkeypatch.setattr(
        capture_to_notion.cli.NotionAdapter,
        "from_config",
        fail_from_config,
    )

    result = capture_to_notion.cli.main(["doctor"])
    captured = capsys.readouterr()

    assert result == 0
    data = json.loads(captured.out)
    assert data["checks"]["token"]["configured"] is True
    assert secret not in captured.out
    assert secret not in captured.err


def test_capture_apply_requires_confirmation_before_initializing_notion_adapter_or_leaking_secret(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    secret = "secret-token-value"
    config_dir = tmp_path / "config"
    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("NOTION_TOKEN", secret)

    def fail_from_config(config):
        raise AssertionError(f"NotionAdapter.from_config should not run: {secret}")

    monkeypatch.setattr(
        capture_to_notion.cli.NotionAdapter,
        "from_config",
        fail_from_config,
    )

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "plan_id": "plan-test",
                "content_type": "books",
                "target": {
                    "page_title": "Books",
                    "page_id": "page-123",
                    "data_source_id": "data-source-123",
                    "confidence": "high",
                    "source": "test",
                },
                "normalized_record": {"title": "Test Book"},
                "field_mapping": {"title": "Name"},
                "operations": [{"type": "upsert", "field": "title", "value": "Test Book"}],
                "asset_operations": [],
                "sources": [],
                "warnings": [],
                "requires_confirmation": True,
                "confirmation_reason": "duplicate_target",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = capture_to_notion.cli.main(["capture", "apply", "--plan", str(plan_path)])
    captured = capsys.readouterr()

    assert result == 2
    assert "计划需要确认后才能执行" in captured.err
    assert secret not in captured.out
    assert secret not in captured.err



def test_doctor_warns_when_legacy_config_dir_exists(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"notion_token": "secret-token-value"}),
        encoding="utf-8",
    )
    fake_home = tmp_path / "home"
    legacy_dir = fake_home / ".config" / "notion-skill"
    legacy_dir.mkdir(parents=True)

    result = run_cli(["doctor"], tmp_path, extra_env={"HOME": str(fake_home)})

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["checks"]["legacy_config_dir"]["exists"] is True
    assert data["checks"]["legacy_config_dir"]["path"] == str(legacy_dir)
    assert "secret-token-value" not in result.stdout



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


def test_readmes_document_p0_diagnostics_commands() -> None:
    readme_paths = [
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "README.zh-CN.md",
    ]
    fenced_legacy_command_pattern = re.compile(
        r"```(?:[a-zA-Z0-9_+-]+)?\n[\s\S]*?^(?:\$\s*)?(?:notion-skill|notion-capture)\b",
        re.MULTILINE,
    )
    shell_legacy_command_pattern = re.compile(
        r"^(?:\$\s*)?(?:notion-skill|notion-capture)\b",
        re.MULTILINE,
    )
    recommended_legacy_command_pattern = re.compile(
        r"(?i)(?<!not\s)(?<!n't\s)\b(?:run|use|execute)\s+`(?:notion-skill|notion-capture)\b[^`]*`",
    )

    for path in readme_paths:
        text = path.read_text(encoding="utf-8")
        assert "capture-to-notion version" in text
        assert "capture-to-notion doctor" in text
        assert "CHANGELOG.md" in text
        assert fenced_legacy_command_pattern.search(text) is None
        assert shell_legacy_command_pattern.search(text) is None
        assert recommended_legacy_command_pattern.search(text) is None



def test_changelog_records_capture_to_notion_rename() -> None:
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")

    assert "capture-to-notion" in text
    assert "notion-skill" in text
    assert "notion-capture" in text
    assert "notion_skill" in text
    assert "capture_to_notion" in text
    assert "~/.config/capture-to-notion" in text



def test_changelog_uses_portable_install_command() -> None:
    changelog = PROJECT_ROOT / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")

    assert "/Users/aaron/" not in text
    assert "uv tool install --force --editable ." in text
