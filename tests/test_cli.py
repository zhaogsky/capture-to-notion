import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


BOOK_PARSER_PROFILE = {
    "book": {
        "labels": {
            "author": ["作者", "author"],
            "isbn": ["ISBN", "isbn"],
            "publisher": ["出版社", "publisher"],
            "page_count": ["页数", "pages", "page_count"],
        }
    }
}


def seed_book_target(tmp_path):
    config_dir = tmp_path
    targets_dir = config_dir / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        config_dir / "aliases.json",
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "description": "书籍、作者、阅读状态、封面",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    write_json(
        targets_dir / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "verified_at": "2026-05-05T10:00:00Z"},
            "parser_profile": BOOK_PARSER_PROFILE,
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "content_types": ["book"],
                    "schema_hash": "abc123",
                    "fields": {
                        "title": "名称",
                        "author": "作者",
                        "isbn": "ISBN",
                        "publisher": "出版社",
                        "page_count": "页数",
                        "state": "阅读状态",
                        "cover": "封面",
                    },
                }
            },
            "state_mapping": {"field": "阅读状态", "values": {"initialized": "想读", "completed": "已读"}},
            "asset_mapping": {"cover": {"field": "封面", "type": "files", "strategy": "download_and_attach"}},
        },
    )


def run_cli(args, tmp_path):
    env = {"CAPTURE_TO_NOTION_CONFIG_DIR": str(tmp_path)}
    return subprocess.run(
        [sys.executable, "-m", "capture_to_notion.cli", *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


def test_cache_inspect_outputs_json(tmp_path, monkeypatch):
    env = {"CAPTURE_TO_NOTION_CONFIG_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "capture_to_notion.cli", "cache", "inspect"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["config_root"] == str(tmp_path)
    assert data["aliases"] == {}
    assert data["routes"] == {}


def test_target_suggest_without_route_requires_confirmation(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps({"raw_input": "存一下这期播客 https://example.com/episode/1"}, ensure_ascii=False), encoding="utf-8")

    result = run_cli(["target", "suggest", "--input", str(input_file)], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["content_type"] == "podcast_episode"
    assert data["suggestions"] == []
    assert data["requires_confirmation"] is True


def test_capture_preflight_stdout_outputs_valid_json(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
            "intent_hint": "direct_write",
        },
    )

    result = run_cli(["capture", "preflight", "--input", str(input_file)], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["content_type"] == "book"
    assert data["intent_hint"] == "direct_write"
    assert data["target"]["status"] == "cache_hit"
    assert {"action": "plan_directly", "reason": "direct_plan_allowed"} in data["safe_actions"]



def test_capture_preflight_compact_stdout_omits_full_structure(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
            "intent_hint": "direct_write",
        },
    )

    result = run_cli(["capture", "preflight", "--input", str(input_file), "--compact"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["content_type"] == "book"
    assert data["intent_hint"] == "direct_write"
    assert data["target"]["status"] == "cache_hit"
    assert {"action": "plan_directly", "reason": "direct_plan_allowed"} in data["safe_actions"]
    assert "data_sources" not in data["structure"]



def test_capture_plan_stdout_outputs_valid_json(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        },
    )

    result = run_cli(["capture", "plan", "--input", str(input_file)], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["content_type"] == "book"
    assert data["target"]["data_source_id"] == "ds-books"



def test_capture_plan_alias_missing_target_cache_suggests_target_scan(tmp_path):
    write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "书单": {
                    "type": "page",
                    "page_id": "page-books",
                    "target_id": "bookshelf",
                }
            }
        },
    )
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        },
    )

    result = run_cli(["capture", "plan", "--input", str(input_file)], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["requires_confirmation"] is True
    assert data["confirmation_reason"] == "target_structure_missing"
    assert "capture-to-notion target scan --page-id page-books --alias 书单" in data["warnings"]


def test_capture_plan_stdout_includes_review_summary(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            "target_hint": "书单",
            "state": "想读",
            "content_type_hint": "book",
        },
    )

    result = run_cli(["capture", "plan", "--input", str(input_file)], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["summary"]["target_page"] == "书单"
    assert data["summary"]["target_data_source"] == "Books"
    assert data["summary"]["state"] == "initialized"
    assert "cover" not in data["summary"]["mapped_fields"]
    assert data["summary"]["key_fields"]["isbn"] == {"target_field": "ISBN", "value_status": "present"}



def test_capture_plan_compact_stdout_omits_execution_payload(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单 作者：刘瑜 ISBN：9787559847357 页数：400",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        },
    )

    result = run_cli(["capture", "plan", "--input", str(input_file), "--compact"], tmp_path)

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["content_type"] == "book"
    assert data["target"]["data_source_id"] == "ds-books"
    assert data["summary"]["target_page"] == "书单"
    assert data["summary"]["key_fields"]["isbn"] == {"target_field": "ISBN", "value_status": "present"}
    assert "normalized_record" not in data
    assert "field_mapping" not in data
    assert "operations" not in data
    assert "asset_operations" not in data
    assert "completion_operations" not in data
    assert "capture_input" not in data



def test_capture_plan_compact_stdout_keeps_full_output_file(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    output_file = tmp_path / "plan.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        },
    )

    result = run_cli(["capture", "plan", "--input", str(input_file), "--output", str(output_file), "--compact"], tmp_path)

    assert result.returncode == 0
    stdout_data = json.loads(result.stdout)
    file_data = json.loads(output_file.read_bytes().decode("utf-8"))
    assert stdout_data != file_data
    assert stdout_data["plan_id"] == file_data["plan_id"]
    assert "operations" not in stdout_data
    assert "operations" in file_data
    assert "normalized_record" in file_data
    assert "capture_input" in file_data



def test_capture_plan_output_writes_utf8_json_and_prints_stdout(tmp_path):
    seed_book_target(tmp_path)
    input_file = tmp_path / "capture.json"
    output_file = tmp_path / "plan.json"
    write_json(
        input_file,
        {
            "raw_input": "把《可能性的艺术》初始化到书单",
            "target_hint": "书单",
            "state": "initialized",
            "content_type_hint": "book",
        },
    )

    result = run_cli(["capture", "plan", "--input", str(input_file), "--output", str(output_file)], tmp_path)

    assert result.returncode == 0
    stdout_data = json.loads(result.stdout)
    file_data = json.loads(output_file.read_bytes().decode("utf-8"))
    assert stdout_data == file_data
    assert file_data["content_type"] == "book"


def test_capture_plan_missing_input_file_exits_nonzero_with_readable_stderr(tmp_path):
    missing_file = tmp_path / "missing.json"

    result = run_cli(["capture", "plan", "--input", str(missing_file)], tmp_path)

    assert result.returncode != 0
    assert result.returncode == 2
    assert "错误:" in result.stderr
    assert "输入文件不存在" in result.stderr


def test_capture_plan_invalid_json_exits_nonzero_with_readable_stderr(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not-valid-json", encoding="utf-8")

    result = run_cli(["capture", "plan", "--input", str(bad_json)], tmp_path)

    assert result.returncode != 0
    assert result.returncode == 2
    assert "错误:" in result.stderr
    assert "JSON 无效" in result.stderr


def test_capture_plan_invalid_capture_input_exits_nonzero_with_readable_stderr(tmp_path):
    invalid_capture = tmp_path / "invalid_capture.json"
    write_json(invalid_capture, {"target_hint": "书单"})

    result = run_cli(["capture", "plan", "--input", str(invalid_capture)], tmp_path)

    assert result.returncode != 0
    assert result.returncode == 2
    assert "错误:" in result.stderr
    assert "输入内容无效" in result.stderr


def test_capture_verify_help_is_available(tmp_path):
    result = run_cli(["capture", "verify", "--help"], tmp_path)

    assert result.returncode == 0
    assert "--page-id" in result.stdout


def test_capture_verify_missing_page_id_exits_with_readable_error(tmp_path):
    result = run_cli(["capture", "verify"], tmp_path)

    assert result.returncode == 2
    assert "--page-id" in result.stderr
