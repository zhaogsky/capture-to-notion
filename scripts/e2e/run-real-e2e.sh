#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}"
RUN_ID="${RUN_ID:-CTN-E2E-$(date +%Y%m%d-%H%M%S)}"
ARTIFACT_ROOT="${CTN_E2E_OUTPUT_DIR:-$SCRIPT_DIR/artifacts}"
ARTIFACT_DIR="${ARTIFACT_DIR:-$ARTIFACT_ROOT/$RUN_ID}"
MODE="readonly"
SCENARIO="all"
FAILURES=0

PLAIN_ALIAS="${CTN_E2E_PLAIN_ALIAS:-ctn-e2e-plain-pages}"
KNOWLEDGE_ALIAS="${CTN_E2E_KNOWLEDGE_ALIAS:-ctn-e2e-knowledge-notes}"
PODCAST_ALIAS="${CTN_E2E_PODCAST_ALIAS:-ctn-e2e-podcasts}"
BOOK_ALIAS="${CTN_E2E_BOOK_ALIAS:-ctn-e2e-books}"
URL_ALIAS="${CTN_E2E_URL_ALIAS:-ctn-e2e-url}"
VIEW_SOURCE_ALIAS="${CTN_E2E_VIEW_SOURCE_ALIAS:-ctn-e2e-view-source}"
VIEW_TARGET_ALIAS="${CTN_E2E_VIEW_TARGET_ALIAS:-ctn-e2e-view-target}"
VIEW_SOURCE_PAGE_ID="${CTN_E2E_VIEW_SOURCE_PAGE_ID:-}"
VIEW_TARGET_PAGE_ID="${CTN_E2E_VIEW_TARGET_PAGE_ID:-}"
SEARCH_QUERY="${CTN_E2E_SEARCH_QUERY:-工具}"
CONFIRM_RISKY="${CTN_E2E_CONFIRM_RISKY:-false}"
ASSET_URL="${CTN_E2E_ASSET_URL:-https://www.notion.so/images/page-cover/nasa_robert_stewart_spacewalk_2.jpg}"
CACHE_MISS_ALIAS="${CTN_E2E_CACHE_MISS_ALIAS:-ctn-e2e-cache-miss-$RUN_ID}"

usage() {
  cat <<'USAGE'
Usage: scripts/e2e/run-real-e2e.sh [--readonly|--write-sandbox] [--scenario NAME]

Runs reusable real Capture to Notion E2E checks with the real CLI, real config,
real cache-v2, and real Notion API. It does not use mocks or Notion MCP.

Modes:
  --readonly       Run read-only checks plus preflight/plan. Default.
  --write-sandbox  Also run capture apply --confirmed for ctn-e2e-* write scenarios.

Scenarios:
  all, env, suggest, search, suggest-search, plain-page, structured-note,
  podcast, book, book-initialized, book-completed, url-gate, existing-update,
  relation, assets, views-scan, view-clone, cache-miss-sync

Config via environment variables:
  CTN_E2E_PLAIN_ALIAS, CTN_E2E_KNOWLEDGE_ALIAS, CTN_E2E_PODCAST_ALIAS
  CTN_E2E_BOOK_ALIAS, CTN_E2E_URL_ALIAS, CTN_E2E_SEARCH_QUERY
  CTN_E2E_VIEW_SOURCE_ALIAS, CTN_E2E_VIEW_TARGET_ALIAS
  CTN_E2E_VIEW_SOURCE_PAGE_ID, CTN_E2E_VIEW_TARGET_PAGE_ID
  CTN_E2E_ASSET_URL, CTN_E2E_OUTPUT_DIR, RUN_ID, ARTIFACT_DIR
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --readonly) MODE="readonly"; shift ;;
    --write-sandbox) MODE="write-sandbox"; shift ;;
    --scenario)
      SCENARIO="${2:-}"
      if [[ -z "$SCENARIO" ]]; then
        echo "--scenario requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$ARTIFACT_DIR" "$ARTIFACT_DIR/cleanup"
RESULT="$ARTIFACT_DIR/RESULT.md"
: > "$RESULT"

ctn() {
  uv run --project "$PROJECT_DIR" capture-to-notion "$@"
}

append_result() {
  printf '%s\n' "$*" >> "$RESULT"
}

scenario_enabled() {
  local name="$1"
  [[ "$SCENARIO" == "all" || "$SCENARIO" == "$name" ]]
}

mark_skipped() {
  local scenario="$1"
  local reason="$2"
  append_result "| $scenario | SKIPPED | $reason |"
}

mark_pass() {
  local scenario="$1"
  local note="$2"
  append_result "| $scenario | PASS | $note |"
}

mark_fail() {
  local scenario="$1"
  local reason="$2"
  FAILURES=1
  append_result "| $scenario | FAIL | $reason |"
}

is_sandbox_alias() {
  case "$1" in
    ctn-e2e-*) return 0 ;;
    *) return 1 ;;
  esac
}

run_cmd() {
  local label="$1"
  local out_dir="$2"
  local name="$3"
  shift 3
  mkdir -p "$out_dir"
  local stdout_file="$out_dir/$name.stdout.json"
  local stderr_file="$out_dir/$name.stderr.txt"
  local command_file="$out_dir/$name.command.txt"
  printf '%q ' "$@" > "$command_file"
  printf '\n' >> "$command_file"

  "$@" > "$stdout_file" 2> "$stderr_file"
  local status=$?
  if [[ $status -eq 0 ]]; then
    mark_pass "$label" "\`$stdout_file\`"
  else
    mark_fail "$label" "status=$status, stderr: \`$stderr_file\`"
  fi
  return $status
}

write_created_objects_manifest() {
  local manifest="$ARTIFACT_DIR/cleanup/created-objects.md"
  ARTIFACT_DIR="$ARTIFACT_DIR" RUN_ID="$RUN_ID" uv run --project "$PROJECT_DIR" python - <<'PY' > "$manifest"
import json
import os
from pathlib import Path

artifact_dir = Path(os.environ["ARTIFACT_DIR"])
run_id = os.environ["RUN_ID"]
rows = []
for path in sorted(artifact_dir.glob("**/*.stdout.json")):
    if "apply" not in path.name and "create-database" not in path.name:
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    scenario = path.parent.name
    rel_path = path.relative_to(artifact_dir)
    sections = []
    for section in ("results", "completion_results", "asset_results", "created_views"):
        values = data.get(section)
        if isinstance(values, list):
            sections.append((section, values))
    direct = []
    for key in ("created_database_id", "data_source_id", "database_id", "page_id", "url"):
        if isinstance(data.get(key), str) and data.get(key):
            direct.append({key: data[key], "type": key, "action": "reported"})
    if direct:
        sections.append(("direct", direct))
    for section, values in sections:
        for item in values:
            if not isinstance(item, dict):
                continue
            refs = {key: item.get(key) or "" for key in ("page_id", "data_source_id", "database_id", "url", "id")}
            if not any(isinstance(value, str) and value for value in refs.values()):
                continue
            rows.append({
                "scenario": scenario,
                "section": section,
                "type": item.get("type") or "",
                "action": item.get("action") or "",
                "page_id": refs["page_id"] or refs["id"],
                "data_source_id": refs["data_source_id"],
                "database_id": refs["database_id"],
                "url": refs["url"],
                "source": str(rel_path),
            })

print("# Created Notion Objects")
print()
print(f"Run ID: {run_id}")
print()
print("This manifest is generated from apply/create outputs. Review objects manually before deleting anything.")
print()
if not rows:
    print("No created or updated Notion object references were found in this run.")
else:
    print("| Scenario | Section | Type | Action | Page ID | Data source ID | Database ID | URL | Source |")
    print("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(str(row[key]).replace("|", "\\|") for key in ("scenario", "section", "type", "action", "page_id", "data_source_id", "database_id", "url", "source")) + " |")
PY
  printf '%s' "$manifest"
}

preflight_route() {
  local preflight_file="$1"
  PREFLIGHT_FILE="$preflight_file" uv run --project "$PROJECT_DIR" python - <<'PY'
import json
import os
with open(os.environ["PREFLIGHT_FILE"], encoding="utf-8") as handle:
    data = json.load(handle)
workflow = data.get("workflow") if isinstance(data, dict) else {}
planning = workflow.get("planning") if isinstance(workflow, dict) else {}
print(f"{planning.get('next_action') or ''}\t{planning.get('reason') or ''}")
PY
}

cache_page_id_for_alias() {
  local alias_name="$1"
  ALIAS_NAME="$alias_name" uv run --project "$PROJECT_DIR" python - <<'PY'
import os
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
cache = CacheV2Store(ensure_config())
alias = cache.find_alias(os.environ["ALIAS_NAME"])
graph = cache.read_graph(alias.get("graph_id")) if isinstance(alias, dict) else None
root = graph.get("root") if isinstance(graph, dict) else None
print(root.get("id", "") if isinstance(root, dict) and root.get("kind") == "page" else "")
PY
}

write_json_file() {
  local path="$1" raw_input="$2" target_hint="$3" content_type_hint="$4" state="$5"
  local intent_hint="$6" input_shape_hint="$7" target_scope_hint="$8" allow_asset_download="$9" existing_page_id="${10:-}" target_context_hint="${11:-}"
  RAW_INPUT="$raw_input" TARGET_HINT="$target_hint" CONTENT_TYPE_HINT="$content_type_hint" STATE_VALUE="$state" \
  INTENT_HINT="$intent_hint" INPUT_SHAPE_HINT="$input_shape_hint" TARGET_SCOPE_HINT="$target_scope_hint" \
  ALLOW_ASSET_DOWNLOAD="$allow_asset_download" CONFIRM_RISKY="$CONFIRM_RISKY" EXISTING_PAGE_ID="$existing_page_id" TARGET_CONTEXT_HINT="$target_context_hint" \
  uv run --project "$PROJECT_DIR" python - <<'PY' > "$path"
import json
import os
payload = {
    "raw_input": os.environ["RAW_INPUT"],
    "target_hint": os.environ["TARGET_HINT"] or None,
    "intent_hint": os.environ["INTENT_HINT"] or None,
    "input_shape_hint": os.environ["INPUT_SHAPE_HINT"] or None,
    "target_scope_hint": os.environ["TARGET_SCOPE_HINT"] or None,
    "target_context_hint": os.environ["TARGET_CONTEXT_HINT"] or None,
    "user_requested_action": "write",
    "options": {
        "allow_web_search": False,
        "allow_target_search": True,
        "allow_asset_download": os.environ["ALLOW_ASSET_DOWNLOAD"].lower() == "true",
    },
}
if os.environ["CONTENT_TYPE_HINT"]:
    payload["content_type_hint"] = os.environ["CONTENT_TYPE_HINT"]
if os.environ["STATE_VALUE"]:
    payload["state"] = os.environ["STATE_VALUE"]
if os.environ["EXISTING_PAGE_ID"]:
    payload["existing_page_id"] = os.environ["EXISTING_PAGE_ID"]
if os.environ["CONFIRM_RISKY"].lower() == "true":
    payload["workflow_confirmations"] = ["risky_target"]
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY
}

json_query() {
  local file="$1" expr="$2"
  JSON_FILE="$file" JSON_EXPR="$expr" uv run --project "$PROJECT_DIR" python - <<'PY'
import json
import os
with open(os.environ["JSON_FILE"], encoding="utf-8") as handle:
    data = json.load(handle)
safe_globals = {
    "__builtins__": {},
    "data": data,
    "any": any,
    "all": all,
    "next": next,
    "isinstance": isinstance,
    "dict": dict,
    "list": list,
    "str": str,
    "len": len,
    "bool": bool,
}
value = eval(os.environ["JSON_EXPR"], safe_globals, safe_globals)
if isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
elif value is None:
    print("")
else:
    print(value)
PY
}

apply_primary_page_id() {
  local file="$1"
  json_query "$file" "next((item.get('page_id') for item in data.get('results', []) if isinstance(item, dict) and item.get('page_id')), '')"
}

assert_python() {
  local label="$1" file="$2" code="$3" success_note="$4" fail_note="$5"
  ASSERT_FILE="$file" ASSERT_CODE="$code" uv run --project "$PROJECT_DIR" python - <<'PY'
import json
import os
import sys
with open(os.environ["ASSERT_FILE"], encoding="utf-8") as handle:
    data = json.load(handle)
safe_globals = {
    "__builtins__": {},
    "data": data,
    "env": os.environ,
    "any": any,
    "all": all,
    "next": next,
    "isinstance": isinstance,
    "dict": dict,
    "list": list,
    "str": str,
    "len": len,
    "bool": bool,
}
ok = bool(eval(os.environ["ASSERT_CODE"], safe_globals, safe_globals))
sys.exit(0 if ok else 1)
PY
  local status=$?
  if [[ $status -eq 0 ]]; then
    mark_pass "$label" "$success_note"
  else
    mark_fail "$label" "$fail_note"
  fi
  return $status
}

assert_plan_write_targets() {
  local label="$1" file="$2"
  assert_python "$label" "$file" "isinstance(data.get('summary'), dict) and isinstance(data['summary'].get('write_targets'), list) and len(data['summary']['write_targets']) > 0 and any(isinstance(item, dict) and ('target_path' in item or 'target_path_complete' in item or item.get('page_id')) for item in data['summary']['write_targets'])" "write_targets present" "summary.write_targets missing or lacks target context"
}

assert_writable_fields_present() {
  local label="$1" file="$2"
  shift 2
  EXPECTED_FIELDS="$*" assert_python "$label" "$file" "isinstance(data.get('summary'), dict) and isinstance(data['summary'].get('writable_fields'), dict) and all(field in data['summary']['writable_fields'] for field in env['EXPECTED_FIELDS'].split())" "required writable fields present" "required writable fields missing: $*"
}

assert_apply_action() {
  local label="$1" file="$2" action="$3" expected_page_id="${4:-}"
  EXPECTED_ACTION="$action" EXPECTED_PAGE_ID="$expected_page_id" assert_python "$label" "$file" "any(isinstance(item, dict) and item.get('action') == env['EXPECTED_ACTION'] and (not env['EXPECTED_PAGE_ID'] or item.get('page_id') == env['EXPECTED_PAGE_ID']) for item in data.get('results', []))" "action $action verified" "expected action $action not found"
}

assert_views_scan_graph() {
  local label="$1" file="$2"
  assert_python "$label" "$file" "isinstance(data.get('views'), list) and len(data['views']) > 0 and isinstance(data.get('target_capabilities'), dict) and data['target_capabilities'].get('view_context') is True" "views present and view_context=true" "scan output did not include graph-backed views"
}

assert_view_clone_output() {
  local label="$1" file="$2"
  assert_python "$label" "$file" "bool(data.get('created_database_id')) and isinstance(data.get('created_views'), list) and len(data['created_views']) > 0" "database and views cloned" "created database/views missing"
}

assert_relation_completion() {
  local label="$1" file="$2"
  assert_python "$label" "$file" "isinstance(data.get('completion_results'), list) and any(isinstance(item, dict) and item.get('action') == 'update_page' for item in data['completion_results'])" "relation completion updated page" "completion_results missing update_page"
}

assert_asset_result() {
  local label="$1" file="$2"
  assert_python "$label" "$file" "isinstance(data.get('asset_results'), list) and len(data['asset_results']) > 0" "asset_results present" "asset_results missing"
}

run_preflight_plan_apply() {
  local scenario_id="$1" scenario_dir="$2" input_file="$3" target_alias="$4"
  local plan_file="$scenario_dir/plan.json"
  mkdir -p "$scenario_dir"

  run_cmd "$scenario_id preflight" "$scenario_dir" "preflight" ctn capture preflight --input "$input_file" --compact || return 1
  local route next_action reason
  route="$(preflight_route "$scenario_dir/preflight.stdout.json")"
  next_action="${route%%$'\t'*}"
  reason="${route#*$'\t'}"
  if [[ "$next_action" != "capture_plan" && "$next_action" != "sync_target_cache" ]]; then
    mark_fail "$scenario_id plan" "blocked_by_preflight next_action=$next_action reason=$reason"
    return 1
  fi
  run_cmd "$scenario_id plan" "$scenario_dir" "plan-compact" ctn capture plan --input "$input_file" --output "$plan_file" --compact || return 1
  assert_plan_write_targets "$scenario_id write-targets" "$scenario_dir/plan-compact.stdout.json"

  if [[ "$MODE" != "write-sandbox" ]]; then
    mark_skipped "$scenario_id apply" "readonly mode"
    return 0
  fi
  if ! is_sandbox_alias "$target_alias"; then
    mark_skipped "$scenario_id apply" "target alias is not ctn-e2e-*: $target_alias"
    return 0
  fi
  run_cmd "$scenario_id apply" "$scenario_dir" "apply" ctn capture apply --plan "$plan_file" --confirmed
}

run_standard_capture() {
  local scenario_id="$1" dir_name="$2" raw="$3" alias="$4" content_type="$5" state="$6" input_shape="$7" scope="$8" assets="$9" existing_page_id="${10:-}" target_context_hint="${11:-}"
  local dir="$ARTIFACT_DIR/$dir_name"
  mkdir -p "$dir"
  write_json_file "$dir/input.json" "$raw" "$alias" "$content_type" "$state" "direct_write" "$input_shape" "$scope" "$assets" "$existing_page_id" "$target_context_hint"
  run_preflight_plan_apply "$scenario_id" "$dir" "$dir/input.json" "$alias"
}

create_cache_miss_page_and_alias() {
  local alias_name="$1"
  ALIAS_NAME="$alias_name" RUN_ID="$RUN_ID" uv run --project "$PROJECT_DIR" python - <<'PY'
import os
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import ensure_config
from capture_to_notion.notion_adapter import NotionAdapter

config = ensure_config()
cache = CacheV2Store(config)
sandbox_alias = cache.find_alias("ctn-e2e-sandbox")
graph = cache.read_graph(sandbox_alias.get("graph_id")) if isinstance(sandbox_alias, dict) else None
root = graph.get("root") if isinstance(graph, dict) else None
parent_page_id = root.get("id") if isinstance(root, dict) and root.get("kind") == "page" else None
if not parent_page_id:
    raise SystemExit("ctn-e2e-sandbox alias is not initialized")
adapter = NotionAdapter.from_config(config)
page = adapter.create_child_page(parent_page_id, f"{os.environ['RUN_ID']} Cache Miss Target")
page_id = str(page["id"])
cache.bind_alias(os.environ["ALIAS_NAME"], graph_id=os.environ["ALIAS_NAME"], profile_id=None, kind="graph")
print(page_id)
PY
}

write_view_clone_files() {
  local scan_file="$1" out_dir="$2"
  SCAN_FILE="$scan_file" OUT_DIR="$out_dir" uv run --project "$PROJECT_DIR" python - <<'PY'
import json
import os
from pathlib import Path
scan = json.loads(Path(os.environ["SCAN_FILE"]).read_text(encoding="utf-8"))
graph_file = scan.get("graph_file")
if not isinstance(graph_file, str) or not graph_file:
    raise SystemExit("scan output missing graph_file")
graph = json.loads(Path(graph_file).read_text(encoding="utf-8"))
expected_fields = {"Name", "State", "Priority", "Notes"}
source_data_source_ids = {
    data_source_id
    for data_source_id, data_source in graph.get("data_sources", {}).items()
    if isinstance(data_source, dict)
    and data_source.get("title") == "View Source Items"
    and expected_fields.issubset(set((data_source.get("schema") or {}).keys()))
}
views = [
    view
    for view in graph.get("views", {}).values()
    if isinstance(view, dict)
    and view.get("data_source_id") in source_data_source_ids
    and view.get("name") == "CTN E2E Inbox"
]
if not views:
    raise SystemExit("source graph has no CTN E2E Inbox view for View Source Items")
schema = {
    "properties": {
        "Name": {"type": "title", "title": {}},
        "State": {"type": "select", "select": {"options": [{"name": "Inbox", "color": "gray"}, {"name": "Done", "color": "green"}]}},
        "Priority": {"type": "number", "number": {"format": "number"}},
        "Notes": {"type": "rich_text", "rich_text": {}},
    }
}
Path(os.environ["OUT_DIR"], "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
Path(os.environ["OUT_DIR"], "views.json").write_text(json.dumps({"views": views}, ensure_ascii=False, indent=2), encoding="utf-8")
PY
}

append_result "# Capture to Notion Real E2E Result"
append_result ""
append_result "Run ID: $RUN_ID"
append_result "Mode: $MODE"
append_result "Artifact dir: $ARTIFACT_DIR"
append_result ""
append_result "## Summary"
append_result ""
append_result "| Scenario | Result | Notes |"
append_result "|---|---|---|"

if scenario_enabled "env"; then
  DIR="$ARTIFACT_DIR/00-env"
  run_cmd "E2E-01 doctor" "$DIR" "doctor" ctn doctor
  run_cmd "E2E-01 cache-inspect" "$DIR" "cache-inspect" ctn cache inspect
  run_cmd "E2E-01 target-list" "$DIR" "target-list" ctn target list
fi

if scenario_enabled "suggest" || scenario_enabled "suggest-search"; then
  DIR="$ARTIFACT_DIR/01-suggest"
  mkdir -p "$DIR"
  write_json_file "$DIR/input.json" "$RUN_ID 保存一条真实 E2E 播客单集测试" "" "podcast_episode" "" "recommend_target" "structured_notes" "" "false"
  if run_cmd "E2E-02 target-suggest" "$DIR" "target-suggest" ctn target suggest --input "$DIR/input.json"; then
    assert_python "E2E-02 suggestion-path" "$DIR/target-suggest.stdout.json" "isinstance(data.get('suggestions'), list) and len(data['suggestions']) > 0 and any(isinstance(item, dict) and ('path' in item or 'path_complete' in item) for item in data['suggestions'])" "suggestion exposes path fields" "suggestion path/path_complete missing"
  fi
fi

if scenario_enabled "search" || scenario_enabled "suggest-search"; then
  DIR="$ARTIFACT_DIR/01-search"
  if run_cmd "E2E-03 target-search" "$DIR" "target-search" ctn target search --query "$SEARCH_QUERY" --limit 5 --include-parent-path --compact; then
    assert_python "E2E-03 search-path" "$DIR/target-search.stdout.json" "isinstance(data.get('results'), list) and len(data['results']) > 0 and any(isinstance(item, dict) and ('path' in item or 'parent_path' in item) for item in data['results'])" "search exposes path fields" "search path/parent_path missing"
  fi
fi

if scenario_enabled "plain-page"; then
  run_standard_capture "E2E-04" "02-plain-page" \
    "$RUN_ID 普通页面测试：这是一条用于验证 Capture to Notion 普通子页面写入的真实内容。" \
    "$PLAIN_ALIAS" "" "" "plain_text" "page_parent" "false"
  if [[ "$MODE" == "write-sandbox" && -f "$ARTIFACT_DIR/02-plain-page/apply.stdout.json" ]]; then
    assert_apply_action "E2E-04 apply-action" "$ARTIFACT_DIR/02-plain-page/apply.stdout.json" "create_child_page"
  fi
fi

if scenario_enabled "structured-note"; then
  run_standard_capture "E2E-05" "03-structured-note" \
    "标题：$RUN_ID 结构化知识笔记
主题：Capture to Notion E2E
要点：
1. 验证结构化文本解析
2. 验证正文 blocks 写入
3. 验证目标路径展示" \
    "$KNOWLEDGE_ALIAS" "" "" "structured_notes" "page_parent" "false"
fi

if scenario_enabled "podcast"; then
  run_standard_capture "E2E-06" "04-podcast" \
    "标题：$RUN_ID 播客单集测试
播客：三五环
状态：已听
平台：小宇宙
主播：CTN E2E Host
简介：这是一条用于 Capture to Notion 真实 E2E 的播客单集测试。
链接：https://example.com/ctn-e2e-podcast" \
    "$PODCAST_ALIAS" "podcast_episode" "completed" "structured_notes" "data_source" "false"
  if [[ "$MODE" == "write-sandbox" && -f "$ARTIFACT_DIR/04-podcast/apply.stdout.json" ]]; then
    assert_apply_action "E2E-06 apply-action" "$ARTIFACT_DIR/04-podcast/apply.stdout.json" "create_page"
  fi
fi

if scenario_enabled "book" || scenario_enabled "book-initialized"; then
  DIR="$ARTIFACT_DIR/05-book-initialized"
  run_standard_capture "E2E-07" "05-book-initialized" \
    "书名：$RUN_ID 测试书籍
作者：CTN E2E Author
ISBN：9780000000002
页数：321
封面：$ASSET_URL
简介：这是一条用于 Capture to Notion 真实 E2E 的书籍初始化测试。" \
    "$BOOK_ALIAS" "book" "initialized" "structured_notes" "data_source" "true"
  if [[ -f "$DIR/plan-compact.stdout.json" ]]; then
    assert_writable_fields_present "E2E-07 book-key-fields" "$DIR/plan-compact.stdout.json" title state author isbn page_count cover
  fi
fi

if scenario_enabled "book-completed"; then
  DIR="$ARTIFACT_DIR/06-book-completed"
  mkdir -p "$DIR"
  if [[ "$MODE" != "write-sandbox" ]]; then
    mark_skipped "E2E-08 book-completed" "readonly mode requires a created prerequisite page"
  else
    setup_dir="$DIR/setup"
    run_standard_capture "E2E-08 setup" "06-book-completed/setup" \
      "书名：$RUN_ID Update 前置书籍
作者：CTN E2E Author
ISBN：9780000000808
页数：180
简介：E2E update prerequisite。" \
      "$BOOK_ALIAS" "book" "initialized" "structured_notes" "data_source" "false"
    page_id="$(apply_primary_page_id "$setup_dir/apply.stdout.json")"
    if [[ -z "$page_id" ]]; then
      mark_fail "E2E-08 book-completed" "setup did not produce page_id"
    else
      write_json_file "$DIR/input.json" "书名：$RUN_ID Update 前置书籍
作者：CTN E2E Author
ISBN：9780000000808
页数：180
状态：已读
简介：E2E completed update。" "$BOOK_ALIAS" "book" "completed" "direct_write" "structured_notes" "data_source" "false" "$page_id"
      if run_preflight_plan_apply "E2E-08" "$DIR" "$DIR/input.json" "$BOOK_ALIAS"; then
        assert_apply_action "E2E-08 update-action" "$DIR/apply.stdout.json" "update_page" "$page_id"
      fi
    fi
  fi
fi

if scenario_enabled "url-gate"; then
  DIR="$ARTIFACT_DIR/07-url-gate"
  run_standard_capture "E2E-09" "07-url-gate" \
    "保存这篇文章：https://example.com/ctn-e2e-url" \
    "$URL_ALIAS" "" "" "external_url" "page_parent" "false"
  assert_python "E2E-09 url-options" "$DIR/input.json" "data.get('options', {}).get('allow_web_search') is False and data.get('input_shape_hint') == 'external_url'" "URL gate options disable web fetch" "URL input allowed web fetch or wrong shape"
fi

if scenario_enabled "existing-update"; then
  DIR="$ARTIFACT_DIR/08-existing-update"
  mkdir -p "$DIR"
  if [[ "$MODE" != "write-sandbox" ]]; then
    mark_skipped "E2E-10 existing-update" "readonly mode requires a created prerequisite page"
  else
    setup_dir="$DIR/setup"
    run_standard_capture "E2E-10 setup" "08-existing-update/setup" \
      "$RUN_ID existing update 前置普通页面。" \
      "$PLAIN_ALIAS" "" "" "plain_text" "page_parent" "false"
    page_id="$(apply_primary_page_id "$setup_dir/apply.stdout.json")"
    if [[ -z "$page_id" ]]; then
      mark_fail "E2E-10 existing-update" "setup did not produce page_id"
    else
      write_json_file "$DIR/input.json" "$RUN_ID existing update：第二次写入必须 append 到同一个页面，而不是重复创建。" "$PLAIN_ALIAS" "" "" "direct_write" "plain_text" "existing_page" "false" "$page_id"
      if run_preflight_plan_apply "E2E-10" "$DIR" "$DIR/input.json" "$PLAIN_ALIAS"; then
        assert_apply_action "E2E-10 update-action" "$DIR/apply.stdout.json" "append_page_content" "$page_id"
      fi
    fi
  fi
fi

if scenario_enabled "relation"; then
  DIR="$ARTIFACT_DIR/09-relation"
  run_standard_capture "E2E-11" "09-relation" \
    "书名：$RUN_ID Relation 测试书籍
作者：CTN E2E Author
关联作者：CTN E2E Author
作者状态：initialized
ISBN：9780000000011
页数：211
简介：这是一条用于验证 relation completion 的真实 E2E。" \
    "$BOOK_ALIAS" "book" "initialized" "structured_notes" "data_source" "false"
  if [[ -f "$DIR/plan-compact.stdout.json" ]]; then
    assert_python "E2E-11 relation-plan" "$DIR/plan-compact.stdout.json" "isinstance(data.get('summary'), dict) and isinstance(data['summary'].get('relation_completions'), list) and len(data['summary']['relation_completions']) > 0 and any(isinstance(item, dict) and item.get('type') == 'relation_page' for item in data['summary'].get('write_targets', []))" "relation completion planned" "relation completion missing from plan"
  fi
  if [[ "$MODE" == "write-sandbox" && -f "$DIR/apply.stdout.json" ]]; then
    assert_relation_completion "E2E-11 relation-apply" "$DIR/apply.stdout.json"
  fi
fi

if scenario_enabled "assets"; then
  DIR="$ARTIFACT_DIR/10-assets"
  run_standard_capture "E2E-12" "10-assets" \
    "书名：$RUN_ID Asset 测试书籍
作者：CTN E2E Author
ISBN：9780000000012
页数：212
封面：$ASSET_URL
简介：这是一条用于验证 cover asset handling 的真实 E2E。" \
    "$BOOK_ALIAS" "book" "initialized" "structured_notes" "data_source" "true"
  if [[ -f "$DIR/plan-compact.stdout.json" ]]; then
    assert_python "E2E-12 asset-plan" "$DIR/plan-compact.stdout.json" "isinstance(data.get('summary'), dict) and isinstance(data['summary'].get('asset_actions'), list) and len(data['summary']['asset_actions']) > 0" "asset action planned" "asset action missing from plan"
  fi
  if [[ "$MODE" == "write-sandbox" && -f "$DIR/apply.stdout.json" ]]; then
    assert_asset_result "E2E-12 asset-apply" "$DIR/apply.stdout.json"
  fi
fi

if scenario_enabled "views-scan"; then
  DIR="$ARTIFACT_DIR/11-views-scan"
  mkdir -p "$DIR"
  source_page_id="$VIEW_SOURCE_PAGE_ID"
  if [[ -z "$source_page_id" ]]; then
    source_page_id="$(cache_page_id_for_alias "$VIEW_SOURCE_ALIAS")"
  fi
  if [[ -n "$source_page_id" ]]; then
    if run_cmd "E2E-13 target-scan-view-source" "$DIR" "target-scan-view-source" ctn target scan --page-id "$source_page_id" --alias "$VIEW_SOURCE_ALIAS"; then
      assert_views_scan_graph "E2E-13 graph-validation" "$DIR/target-scan-view-source.stdout.json"
    fi
  else
    mark_skipped "E2E-13 views-scan" "no CTN_E2E_VIEW_SOURCE_PAGE_ID and alias not cached: $VIEW_SOURCE_ALIAS"
  fi
fi

if scenario_enabled "view-clone"; then
  DIR="$ARTIFACT_DIR/12-view-clone"
  mkdir -p "$DIR"
  if [[ "$MODE" != "write-sandbox" ]]; then
    mark_skipped "E2E-14 view-clone" "readonly mode"
  else
    source_page_id="$VIEW_SOURCE_PAGE_ID"
    target_page_id="$VIEW_TARGET_PAGE_ID"
    if [[ -z "$source_page_id" ]]; then source_page_id="$(cache_page_id_for_alias "$VIEW_SOURCE_ALIAS")"; fi
    if [[ -z "$target_page_id" ]]; then target_page_id="$(cache_page_id_for_alias "$VIEW_TARGET_ALIAS")"; fi
    clone_alias="ctn-e2e-view-clone-$RUN_ID"
    if [[ -z "$source_page_id" || -z "$target_page_id" ]]; then
      mark_skipped "E2E-14 view-clone" "source/target page id unavailable"
    elif run_cmd "E2E-14 scan-source" "$DIR" "scan-source" ctn target scan --page-id "$source_page_id" --alias "$VIEW_SOURCE_ALIAS"; then
      if write_view_clone_files "$DIR/scan-source.stdout.json" "$DIR"; then
        if run_cmd "E2E-14 create-database-from-views" "$DIR" "create-database-from-views" ctn target create-database --page-id "$target_page_id" --title "$RUN_ID View Clone" --schema "$DIR/schema.json" --views "$DIR/views.json" --alias "$clone_alias" --target-id "$clone_alias"; then
          assert_view_clone_output "E2E-14 clone-validation" "$DIR/create-database-from-views.stdout.json"
        fi
      else
        mark_fail "E2E-14 view-clone" "failed to prepare cloned views file"
      fi
    fi
  fi
fi

if scenario_enabled "cache-miss-sync"; then
  DIR="$ARTIFACT_DIR/13-cache-miss-sync"
  mkdir -p "$DIR"
  if [[ "$MODE" != "write-sandbox" ]]; then
    mark_skipped "E2E-15 cache-miss-sync" "readonly mode cannot create an unscanned real target"
  else
    cache_miss_page_id="$(create_cache_miss_page_and_alias "$CACHE_MISS_ALIAS")"
    write_json_file "$DIR/input.json" "$RUN_ID cache miss/sync 测试：先验证缺 cache 路由，再扫描同步目标。" "$CACHE_MISS_ALIAS" "" "" "direct_write" "plain_text" "page_parent" "false"
    if run_cmd "E2E-15 preflight-before-sync" "$DIR" "preflight-before-sync" ctn capture preflight --input "$DIR/input.json" --compact; then
      route="$(preflight_route "$DIR/preflight-before-sync.stdout.json")"
      next_action="${route%%$'\t'*}"
      if [[ "$next_action" != "scan_target" && "$next_action" != "sync_target_cache" ]]; then
        mark_fail "E2E-15 cache-miss-route" "expected scan_target or sync_target_cache before scan, got $next_action"
      else
        mark_pass "E2E-15 cache-miss-route" "next_action=$next_action"
      fi
    fi
    if run_cmd "E2E-15 target-scan-cache-miss" "$DIR" "target-scan-cache-miss" ctn target scan --page-id "$cache_miss_page_id" --alias "$CACHE_MISS_ALIAS"; then
      run_preflight_plan_apply "E2E-15" "$DIR" "$DIR/input.json" "$CACHE_MISS_ALIAS"
    fi
  fi
fi

append_result ""
append_result "## Configuration"
append_result ""
append_result "- PROJECT_DIR: $PROJECT_DIR"
append_result "- PLAIN_ALIAS: $PLAIN_ALIAS"
append_result "- KNOWLEDGE_ALIAS: $KNOWLEDGE_ALIAS"
append_result "- PODCAST_ALIAS: $PODCAST_ALIAS"
append_result "- BOOK_ALIAS: $BOOK_ALIAS"
append_result "- URL_ALIAS: $URL_ALIAS"
append_result "- VIEW_SOURCE_ALIAS: $VIEW_SOURCE_ALIAS"
append_result "- VIEW_TARGET_ALIAS: $VIEW_TARGET_ALIAS"
append_result "- CACHE_MISS_ALIAS: $CACHE_MISS_ALIAS"
append_result "- SEARCH_QUERY: $SEARCH_QUERY"
append_result "- ASSET_URL: $ASSET_URL"
append_result "- CONFIRM_RISKY: $CONFIRM_RISKY"
append_result ""
CREATED_OBJECTS_MANIFEST="$(write_created_objects_manifest)"
append_result "## Cleanup"
append_result ""
append_result "Created objects manifest: \`$CREATED_OBJECTS_MANIFEST\`"
append_result ""
append_result "Record created Notion objects from apply outputs before deleting anything. This script intentionally does not delete Notion content."

printf 'Real E2E artifacts: %s\n' "$ARTIFACT_DIR"
printf 'Result report: %s\n' "$RESULT"
printf 'Created objects manifest: %s\n' "$CREATED_OBJECTS_MANIFEST"
exit "$FAILURES"
