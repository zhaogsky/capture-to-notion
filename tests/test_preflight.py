from __future__ import annotations

import json

from capture_to_notion.cache import CacheStore
from capture_to_notion.config import AppConfig
from capture_to_notion.models import CaptureInput
from capture_to_notion.preflight import build_capture_preflight


def test_capture_input_accepts_preflight_hints():
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》放到书单",
            "intent_hint": "direct_write",
            "input_shape_hint": "title_text",
            "target_context_hint": "books_database",
            "target_scope_hint": "single_target",
            "user_requested_action": "save",
        }
    )

    assert capture.intent_hint == "direct_write"
    assert capture.input_shape_hint == "title_text"
    assert capture.target_context_hint == "books_database"
    assert capture.target_scope_hint == "single_target"
    assert capture.user_requested_action == "save"


def test_capture_input_remains_backward_compatible_without_preflight_hints():
    capture = CaptureInput.from_dict({"raw_input": "记录这个"})

    assert capture.intent_hint is None
    assert capture.input_shape_hint is None
    assert capture.target_context_hint is None
    assert capture.target_scope_hint is None
    assert capture.user_requested_action is None
    assert capture.state == "initialized"
    assert capture.user_intent == "capture_to_notion"


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def _cache(tmp_path):
    config = AppConfig(
        root=tmp_path,
        config_file=tmp_path / "config.json",
        aliases_file=tmp_path / "aliases.json",
        routes_file=tmp_path / "routes.json",
        states_file=tmp_path / "states.json",
        targets_dir=tmp_path / "targets",
        plans_dir=tmp_path / "plans",
        logs_dir=tmp_path / "logs",
        covers_dir=tmp_path / "cache" / "assets" / "covers",
    )
    return CacheStore(config)



def test_build_capture_preflight_returns_skeleton_hints_and_content_type(tmp_path):
    capture = CaptureInput.from_dict(
        {
            "raw_input": "把《可能性的艺术》放到书单",
            "intent_hint": "direct_write",
            "input_shape_hint": "title_text",
            "target_context_hint": "books_database",
            "target_scope_hint": "single_target",
            "user_requested_action": "save",
        }
    )

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["content_type"] == "book"
    assert preflight["intent_hint"] == "direct_write"
    assert preflight["input_shape_hint"] == "title_text"
    assert preflight["target_context_hint"] == "books_database"
    assert preflight["target_scope_hint"] == "single_target"
    assert preflight["user_requested_action"] == "save"



def test_preflight_without_target_suggests_target(tmp_path):
    capture = CaptureInput.from_dict({"raw_input": "记录一下《可能性的艺术》"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "target_missing"
    assert {"action": "suggest_target", "reason": "target_missing"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "target_missing"} in preflight["blocked_actions"]



def test_preflight_with_missing_alias_requests_target_resolution(tmp_path):
    capture = CaptureInput.from_dict({"raw_input": "记录一下《可能性的艺术》", "target_hint": "书单"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"] == {"hint": "书单", "status": "target_not_resolved"}
    assert {"action": "suggest_target", "reason": "target_not_resolved"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "target_not_resolved"} in preflight["blocked_actions"]



def test_preflight_external_url_recommends_asking_before_parse(tmp_path):
    capture = CaptureInput.from_dict(
        {
            "raw_input": "https://example.com/episode/1",
            "input_shape_hint": "external_url",
        }
    )

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert {"action": "ask_before_parse_url", "reason": "external_url_input"} in preflight["safe_actions"]
    assert {"action": "parse_url_directly", "reason": "recommendation_required"} in preflight["blocked_actions"]



def test_preflight_with_missing_cache_requests_scan(tmp_path):
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "书单": {"page_id": "page-books", "target_id": "bookshelf"},
            }
        },
    )
    capture = CaptureInput.from_dict({"raw_input": "记录一下《可能性的艺术》", "target_hint": "书单"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "cache_missing"
    assert preflight["target"]["page_id"] == "page-books"
    assert preflight["target"]["target_id"] == "bookshelf"
    assert {"action": "scan_target", "reason": "target_structure_missing"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "target_structure_missing"} in preflight["blocked_actions"]



def test_preflight_with_cache_hit_uses_cached_structure(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "书单": {"page_id": "page-books", "target_id": "bookshelf"},
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "schema": {"Name": {"type": "title"}, "URL": {"type": "url"}},
                }
            },
        },
    )
    capture = CaptureInput.from_dict({"raw_input": "记录一下《可能性的艺术》", "target_hint": "书单"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["structure"]["structure_complexity"]["data_source_count"] == 1
    assert {"action": "plan_directly", "reason": "direct_plan_allowed"} in preflight["safe_actions"]
    assert {"action": "apply_directly", "reason": "plan_required"} in preflight["blocked_actions"]



def test_preflight_with_stale_schema_requests_rescan(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "书单": {"page_id": "page-books", "target_id": "bookshelf"},
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "schema_status": "stale",
                    "schema": {"Name": {"type": "title"}},
                }
            },
        },
    )
    capture = CaptureInput.from_dict({"raw_input": "记录一下《可能性的艺术》", "target_hint": "书单"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "schema_stale"
    assert {"action": "scan_target", "reason": "schema_stale"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "schema_stale"} in preflight["blocked_actions"]
    assert preflight["confirmation_needed"] == ["schema_stale"]



def test_preflight_with_stale_schema_preserves_risky_target_confirmation(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "入口": {"page_id": "page-index", "target_id": "index"},
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "index.json",
        {
            "target": {"page_id": "page-index", "title": "入口"},
            "data_sources": {
                "nav": {
                    "data_source_id": "ds-nav",
                    "title": "Navigation Index",
                    "schema_status": "stale",
                    "schema": {"Name": {"type": "title"}},
                }
            },
        },
    )
    capture = CaptureInput.from_dict({"raw_input": "记录一下", "target_hint": "入口"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "schema_stale"
    assert "navigation_like_name" in preflight["structure"]["risk_flags"]
    assert {"action": "scan_target", "reason": "schema_stale"} in preflight["safe_actions"]
    assert {"action": "confirm_risky_target", "reason": "risky_target"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "schema_stale"} in preflight["blocked_actions"]
    assert {"action": "plan_directly", "reason": "risky_target_requires_confirmation"} in preflight["blocked_actions"]
    assert preflight["confirmation_needed"] == ["schema_stale", "risky_target"]



def test_preflight_with_risky_target_blocks_direct_plan(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "入口": {"page_id": "page-index", "target_id": "index"},
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "index.json",
        {
            "target": {"page_id": "page-index", "title": "入口"},
            "data_sources": {
                "nav": {
                    "data_source_id": "ds-nav",
                    "title": "Navigation Index",
                    "schema": {"Name": {"type": "title"}},
                }
            },
        },
    )
    capture = CaptureInput.from_dict({"raw_input": "记录一下", "target_hint": "入口"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "risky_target"
    assert "navigation_like_name" in preflight["structure"]["risk_flags"]
    assert {"action": "confirm_risky_target", "reason": "risky_target"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "risky_target_requires_confirmation"} in preflight["blocked_actions"]
    assert preflight["confirmation_needed"] == ["risky_target"]
