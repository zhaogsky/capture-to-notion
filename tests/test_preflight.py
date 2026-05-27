from __future__ import annotations

import json

from capture_to_notion.cache import CacheStore
from capture_to_notion.cache_v2 import CacheV2Store
from capture_to_notion.config import AppConfig
from capture_to_notion.models import CaptureInput
import capture_to_notion.preflight as preflight_module
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def _app_config(tmp_path):
    return AppConfig(
        root=tmp_path,
        config_file=tmp_path / "config.json",
        aliases_file=tmp_path / "aliases.json",
        routes_file=tmp_path / "routes.json",
        states_file=tmp_path / "states.json",
        targets_dir=tmp_path / "targets",
        plans_dir=tmp_path / "plans",
        logs_dir=tmp_path / "logs",
        covers_dir=tmp_path / "cache" / "assets" / "covers",
        cache_v2_dir=tmp_path / "cache-v2",
        graphs_v2_dir=tmp_path / "cache-v2" / "graphs",
        profiles_v2_dir=tmp_path / "cache-v2" / "profiles",
        plans_v2_dir=tmp_path / "cache-v2" / "plans",
        assets_v2_dir=tmp_path / "cache-v2" / "assets",
        aliases_v2_file=tmp_path / "cache-v2" / "aliases.json",
    )


def _cache(tmp_path):
    return CacheStore(_app_config(tmp_path))


def _v2_cache(tmp_path):
    return CacheV2Store(_app_config(tmp_path))



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
    assert preflight["workflow"]["target_resolution"] == {
        "status": "target_missing",
        "source": "missing",
        "target_type": "unknown",
    }
    assert preflight["workflow"]["planning"] == {
        "status": "missing",
        "next_action": "suggest_target",
        "reason": "target_missing",
    }
    assert {"action": "suggest_target", "reason": "target_missing"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "target_missing"} in preflight["blocked_actions"]



def test_build_capture_preflight_summary_omits_full_structure():
    preflight = {
        "content_type": "book",
        "intent_hint": "direct_write",
        "input_shape_hint": "plain_text",
        "target_context_hint": "books",
        "target_scope_hint": "single_target",
        "user_requested_action": "write",
        "target": {"status": "risky_target", "target_id": "bookshelf"},
        "structure": {
            "risk_flags": ["navigation_like_name"],
            "recommendations": [{"action": "confirm_risky_target"}],
            "structure_complexity": {"data_source_count": 2},
            "data_sources": {"large": {"schema": {"Name": {"type": "title"}}}},
        },
        "safe_actions": [{"action": "confirm_risky_target", "reason": "risky_target"}],
        "blocked_actions": [{"action": "plan_directly", "reason": "risky_target_requires_confirmation"}],
        "confirmation_needed": ["risky_target"],
        "workflow": {
            "planning": {
                "status": "risky",
                "next_action": "confirm_risky_target",
                "reason": "risky_target_requires_confirmation",
            }
        },
    }

    assert hasattr(preflight_module, "build_capture_preflight_summary")

    summary = preflight_module.build_capture_preflight_summary(preflight)

    assert summary == {
        "content_type": "book",
        "intent_hint": "direct_write",
        "input_shape_hint": "plain_text",
        "target_context_hint": "books",
        "target_scope_hint": "single_target",
        "user_requested_action": "write",
        "target": {"status": "risky_target", "target_id": "bookshelf"},
        "structure": {
            "risk_flags": ["navigation_like_name"],
            "recommendations": [{"action": "confirm_risky_target"}],
            "structure_complexity": {"data_source_count": 2},
        },
        "safe_actions": [{"action": "confirm_risky_target", "reason": "risky_target"}],
        "blocked_actions": [{"action": "plan_directly", "reason": "risky_target_requires_confirmation"}],
        "confirmation_needed": ["risky_target"],
        "workflow": {
            "planning": {
                "status": "risky",
                "next_action": "confirm_risky_target",
                "reason": "risky_target_requires_confirmation",
            }
        },
        "next_action": "confirm_risky_target",
        "next_action_reason": "risky_target_requires_confirmation",
    }
    assert "data_sources" not in summary["structure"]
    assert summary["workflow"] == preflight["workflow"]



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

    assert preflight["workflow"]["identity_enrichment"] == {
        "status": "recommended",
        "reason": "external_url_input",
    }
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



def test_preflight_resolves_database_item_existing_page_from_target_cache(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {"aliases": {"可能性的艺术": {"type": "page", "page_id": "page-book-1", "target_id": "book-item"}}},
    )
    _write_json(
        tmp_path / "targets" / "book-item.json",
        {
            "target": {
                "page_id": "page-book-1",
                "title": "可能性的艺术",
                "target_id": "book-item",
                "data_source_id": "ds-books",
            },
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "schema": {"名称": {"type": "title"}},
                }
            },
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict({"raw_input": "《可能性的艺术》", "target_hint": "可能性的艺术"}),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["target"]["source"] == "target_hint_alias"
    assert preflight["target"]["data_source_id"] == "ds-books"
    assert preflight["target"]["existing_page_id"] == "page-book-1"
    assert preflight["workflow"]["target_resolution"]["target_type"] == "existing_page"
    assert preflight["workflow"]["target_resolution"]["existing_page_id"] == "page-book-1"
    assert preflight["workflow"]["planning"] == {
        "status": "allowed",
        "next_action": "capture_plan",
        "reason": "direct_plan_allowed",
    }



def test_preflight_data_source_alias_with_parent_page_does_not_resolve_as_existing_page(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {"aliases": {"书库": {"type": "data_source", "data_source_id": "ds-books", "target_id": "books-ds"}}},
    )
    _write_json(
        tmp_path / "targets" / "books-ds.json",
        {
            "target": {
                "page_id": "page-books",
                "title": "Books",
                "target_id": "books-ds",
                "data_source_id": "ds-books",
            },
            "data_sources": {
                "ds-books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "schema": {"名称": {"type": "title"}},
                }
            },
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict({"raw_input": "《新书》", "target_hint": "书库", "content_type_hint": "book"}),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["target"]["source"] == "data_source_alias"
    assert preflight["target"]["page_id"] == "page-books"
    assert preflight["target"]["data_source_id"] == "ds-books"
    assert "existing_page_id" not in preflight["target"]
    assert preflight["workflow"]["target_resolution"]["target_type"] == "data_source"
    assert "existing_page_id" not in preflight["workflow"]["target_resolution"]



def test_preflight_uses_unique_reliable_route_when_target_hint_missing(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(tmp_path / "routes.json", {"routes": {"book": {"preferred_targets": [{"alias": "书单", "confidence": "high"}]}}})
    _write_json(tmp_path / "aliases.json", {"aliases": {"书单": {"page_id": "page-books", "target_id": "bookshelf"}}})
    _write_json(
        tmp_path / "targets" / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {
                "books": {
                    "data_source_id": "ds-books",
                    "title": "Books",
                    "role": "primary",
                    "schema": {"名称": {"type": "title"}},
                }
            },
        },
    )

    preflight = build_capture_preflight(CaptureInput.from_dict({"raw_input": "《可能性的艺术》"}), cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["target"]["source"] == "route_preferred_target"
    assert preflight["target"]["alias"] == "书单"



def test_preflight_blocks_route_target_when_page_context_unverified(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(tmp_path / "routes.json", {"routes": {"book": {"preferred_targets": [{"alias": "书单", "confidence": "high"}]}}})
    _write_json(tmp_path / "aliases.json", {"aliases": {"书单": {"page_id": "page-books", "target_id": "bookshelf"}}})
    _write_json(
        tmp_path / "targets" / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {"books": {"data_source_id": "ds-books", "role": "primary", "schema": {"名称": {"type": "title"}}}},
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict({"raw_input": "《可能性的艺术》", "target_context_hint": "节目页", "target_scope_hint": "under_page"}),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "target_context_unverified"
    assert preflight["target"]["target_context_hint"] == "节目页"
    assert preflight["workflow"]["planning"] == {
        "status": "blocked",
        "next_action": "scan_target",
        "reason": "target_context_unverified",
    }
    assert {"action": "plan_directly", "reason": "target_context_unverified"} in preflight["blocked_actions"]



def test_preflight_verified_page_context_alias_allows_capture_plan(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(tmp_path / "aliases.json", {"aliases": {"节目页": {"page_id": "page-show", "target_id": "show-page"}}})
    _write_json(
        tmp_path / "targets" / "show-page.json",
        {
            "target": {"page_id": "page-show", "title": "节目页", "target_id": "show-page"},
            "data_sources": {"episodes": {"data_source_id": "ds-episodes", "role": "primary", "schema": {"名称": {"type": "title"}}}},
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict({"raw_input": "记录《访谈》", "target_context_hint": "节目页", "target_scope_hint": "under_page"}),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["target"]["source"] == "target_context_alias"
    assert preflight["target"]["target_context_verified"] is True
    assert preflight["workflow"]["target_resolution"]["context_verification_source"] == "page_id_match"
    assert preflight["workflow"]["planning"]["next_action"] == "capture_plan"



def _seed_v2_episode_target(tmp_path, *, data_source_location=None):
    data_source = {
        "data_source_id": "ds-episodes",
        "title": "Episodes",
        "schema": {"名称": {"name": "名称", "type": "title"}},
    }
    if data_source_location:
        data_source.update(data_source_location)
    _write_json(
        tmp_path / "cache-v2" / "graphs" / "show-episodes.json",
        {
            "cache_version": 2,
            "graph_id": "show-episodes",
            "root": {"kind": "page", "id": "page-show"},
            "pages": {"page-show": {"page_id": "page-show", "title": "后互联网时代的乱弹"}},
            "data_sources": {"ds-episodes": data_source},
            "views": {},
        },
    )
    _write_json(
        tmp_path / "cache-v2" / "profiles" / "show-profile.json",
        {
            "cache_version": 2,
            "profile_id": "show-profile",
            "graph_id": "show-episodes",
            "write_profiles": {
                "podcast_episode": {
                    "canonical_data_source_id": "ds-episodes",
                    "canonical_view_id": None,
                    "field_mapping": {"title": "名称"},
                    "field_sources": {"title": "user_binding"},
                    "parser_profile": {"trusted_field_sources": ["user_binding"]},
                    "state_mapping": {},
                    "asset_mapping": {},
                    "relation_mapping": {},
                }
            },
        },
    )
    _write_json(
        tmp_path / "cache-v2" / "aliases.json",
        {
            "cache_version": 2,
            "aliases": {
                "后互联网时代的乱弹-单集列表": {
                    "graph_id": "show-episodes",
                    "profile_id": "show-profile",
                    "kind": "write_profile",
                }
            },
        },
    )


def test_v2_preflight_syncs_explicit_child_alias_when_page_context_location_facts_missing(tmp_path):
    _seed_v2_episode_target(tmp_path)

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "记录《访谈》",
                "target_hint": "后互联网时代的乱弹-单集列表",
                "target_context_hint": "child database under Notion page 后互联网时代的乱弹; parent page id page-show",
                "target_scope_hint": "data_source",
                "content_type_hint": "podcast_episode",
            }
        ),
        cache=_v2_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "target_context_cache_incomplete"
    assert preflight["target"]["target_context_verified"] is False
    assert preflight["target"]["sync"] == {
        "scope": "data_source",
        "target_id": "show-episodes",
        "alias": "后互联网时代的乱弹-单集列表",
        "page_id": "page-show",
    }
    assert preflight["workflow"]["planning"] == {
        "status": "blocked",
        "next_action": "sync_target_cache",
        "reason": "cache_location_facts_missing",
    }


def test_v2_preflight_allows_explicit_child_alias_when_parent_page_location_matches(tmp_path):
    _seed_v2_episode_target(tmp_path, data_source_location={"parent_page_id": "page-show", "database_id": "db-episodes"})

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "记录《访谈》",
                "target_hint": "后互联网时代的乱弹-单集列表",
                "target_context_hint": "child database under Notion page 后互联网时代的乱弹; parent page id page-show",
                "target_scope_hint": "data_source",
                "content_type_hint": "podcast_episode",
            }
        ),
        cache=_v2_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["target"]["parent_page_id"] == "page-show"
    assert preflight["target"]["target_context_verified"] is True
    assert preflight["workflow"]["target_resolution"]["context_verification_source"] == "parent_page_id_match"
    assert preflight["workflow"]["planning"]["next_action"] == "capture_plan"


def test_v2_preflight_blocks_explicit_child_alias_when_parent_page_location_mismatches(tmp_path):
    _seed_v2_episode_target(tmp_path, data_source_location={"parent_page_id": "page-other", "database_id": "db-episodes"})

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "记录《访谈》",
                "target_hint": "后互联网时代的乱弹-单集列表",
                "target_context_hint": "child database under Notion page 后互联网时代的乱弹; parent page id page-show",
                "target_scope_hint": "data_source",
                "content_type_hint": "podcast_episode",
            }
        ),
        cache=_v2_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "target_context_mismatch"
    assert preflight["target"]["target_context_verified"] is False
    assert preflight["workflow"]["planning"] == {
        "status": "blocked",
        "next_action": "scan_target",
        "reason": "target_context_mismatch",
    }


def test_preflight_syncs_explicit_child_alias_when_page_context_location_facts_missing(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "后互联网时代的乱弹-单集列表": {
                    "type": "data_source",
                    "data_source_id": "ds-episodes",
                    "target_id": "show-episodes",
                    "title": "后互联网时代的乱弹-单集列表",
                }
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "show-episodes.json",
        {
            "target": {"target_id": "show-episodes", "data_source_id": "ds-episodes"},
            "data_sources": {"episodes": {"data_source_id": "ds-episodes", "role": "primary", "schema": {"名称": {"type": "title"}}}},
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "记录《访谈》",
                "target_hint": "后互联网时代的乱弹-单集列表",
                "target_context_hint": "child database under Notion page 后互联网时代的乱弹; parent page id page-show",
                "target_scope_hint": "data_source",
            }
        ),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "target_context_cache_incomplete"
    assert preflight["target"]["target_context_verified"] is False
    assert preflight["target"]["cache_completeness"]["status"] == "incomplete"
    assert preflight["target"]["sync"] == {
        "scope": "data_source",
        "target_id": "show-episodes",
        "data_source_id": "ds-episodes",
        "alias": "后互联网时代的乱弹-单集列表",
    }
    assert preflight["workflow"]["planning"] == {
        "status": "blocked",
        "next_action": "sync_target_cache",
        "reason": "cache_location_facts_missing",
    }
    assert {"action": "sync_target_cache", "reason": "cache_location_facts_missing"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "cache_location_facts_missing"} in preflight["blocked_actions"]



def test_preflight_allows_explicit_child_alias_when_parent_page_location_matches(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "后互联网时代的乱弹-单集列表": {
                    "type": "data_source",
                    "data_source_id": "ds-episodes",
                    "target_id": "show-episodes",
                }
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "show-episodes.json",
        {
            "target": {"target_id": "show-episodes", "data_source_id": "ds-episodes"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "parent_page_id": "page-show",
                    "role": "primary",
                    "schema": {"名称": {"type": "title"}},
                }
            },
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "记录《访谈》",
                "target_hint": "后互联网时代的乱弹-单集列表",
                "target_context_hint": "child database under Notion page 后互联网时代的乱弹; parent page id page-show",
                "target_scope_hint": "data_source",
            }
        ),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["target"]["parent_page_id"] == "page-show"
    assert preflight["target"]["target_context_verified"] is True
    assert preflight["workflow"]["target_resolution"]["context_verification_source"] == "parent_page_id_match"
    assert preflight["workflow"]["planning"]["next_action"] == "capture_plan"



def test_preflight_blocks_explicit_child_alias_when_parent_page_location_mismatches(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "后互联网时代的乱弹-单集列表": {
                    "type": "data_source",
                    "data_source_id": "ds-episodes",
                    "target_id": "show-episodes",
                }
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "show-episodes.json",
        {
            "target": {"target_id": "show-episodes", "data_source_id": "ds-episodes"},
            "data_sources": {
                "episodes": {
                    "data_source_id": "ds-episodes",
                    "parent_page_id": "page-other",
                    "role": "primary",
                    "schema": {"名称": {"type": "title"}},
                }
            },
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {
                "raw_input": "记录《访谈》",
                "target_hint": "后互联网时代的乱弹-单集列表",
                "target_context_hint": "child database under Notion page 后互联网时代的乱弹; parent page id page-show",
                "target_scope_hint": "data_source",
            }
        ),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "target_context_mismatch"
    assert preflight["target"]["target_context_verified"] is False
    assert preflight["workflow"]["planning"] == {
        "status": "blocked",
        "next_action": "scan_target",
        "reason": "target_context_mismatch",
    }



def test_preflight_blocks_explicit_target_context_mismatch(tmp_path):
    (tmp_path / "targets").mkdir()
    _write_json(
        tmp_path / "aliases.json",
        {
            "aliases": {
                "书单": {"page_id": "page-books", "target_id": "bookshelf"},
                "节目页": {"page_id": "page-show", "target_id": "show-page"},
            }
        },
    )
    _write_json(
        tmp_path / "targets" / "bookshelf.json",
        {
            "target": {"page_id": "page-books", "title": "书单", "target_id": "bookshelf"},
            "data_sources": {"books": {"data_source_id": "ds-books", "role": "primary", "schema": {"名称": {"type": "title"}}}},
        },
    )
    _write_json(
        tmp_path / "targets" / "show-page.json",
        {
            "target": {"page_id": "page-show", "title": "节目页", "target_id": "show-page"},
            "data_sources": {"episodes": {"data_source_id": "ds-episodes", "role": "primary", "schema": {"名称": {"type": "title"}}}},
        },
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict(
            {"raw_input": "记录《访谈》", "target_hint": "书单", "target_context_hint": "节目页", "target_scope_hint": "under_page"}
        ),
        cache=_cache(tmp_path),
    )

    assert preflight["target"]["status"] == "target_context_mismatch"
    assert preflight["target"]["target_context_verified"] is False
    assert preflight["workflow"]["planning"] == {
        "status": "blocked",
        "next_action": "scan_target",
        "reason": "target_context_mismatch",
    }
    assert {"action": "plan_directly", "reason": "target_context_mismatch"} in preflight["blocked_actions"]



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



def _seed_risky_target(tmp_path):
    (tmp_path / "targets").mkdir(exist_ok=True)
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


def test_preflight_with_risky_target_blocks_direct_plan(tmp_path):
    _seed_risky_target(tmp_path)
    capture = CaptureInput.from_dict({"raw_input": "记录一下", "target_hint": "入口"})

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "risky_target"
    assert "navigation_like_name" in preflight["structure"]["risk_flags"]
    assert {"action": "confirm_risky_target", "reason": "risky_target"} in preflight["safe_actions"]
    assert {"action": "plan_directly", "reason": "risky_target_requires_confirmation"} in preflight["blocked_actions"]
    assert preflight["confirmation_needed"] == ["risky_target"]


def test_preflight_with_confirmed_risky_target_allows_plan(tmp_path):
    _seed_risky_target(tmp_path)
    capture = CaptureInput.from_dict(
        {
            "raw_input": "记录一下",
            "target_hint": "入口",
            "workflow_confirmations": ["risky_target"],
        }
    )

    preflight = build_capture_preflight(capture, cache=_cache(tmp_path))

    assert preflight["target"]["status"] == "cache_hit"
    assert preflight["workflow"]["planning"] == {
        "status": "allowed",
        "next_action": "capture_plan",
        "reason": "risky_target_confirmed",
    }
    assert preflight["confirmation_needed"] == []


def test_preflight_ignores_legacy_aliases_and_requires_v2_target(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    legacy = CacheStore(config)
    legacy.write_json(config.aliases_file, {"aliases": {"Program": {"target_id": "legacy"}}})
    legacy.write_json(
        config.targets_dir / "legacy.json",
        {"target": {"page_id": "page-legacy"}, "data_sources": {"ds-legacy": {"data_source_id": "ds-legacy"}}},
    )

    preflight = build_capture_preflight(
        CaptureInput.from_dict({"raw_input": "标题：Example", "target_hint": "Program", "content_type_hint": "podcast_episode"}),
        CacheV2Store(config),
    )

    assert preflight["workflow"]["planning"]["next_action"] == "scan_target"
    assert preflight["workflow"]["target_resolution"]["status"] == "v2_target_missing"


def test_preflight_resolves_v2_profile_to_view_backed_data_source(tmp_path, monkeypatch):
    from capture_to_notion.cache_v2 import CacheV2Store
    from capture_to_notion.config import ensure_config

    monkeypatch.setenv("CAPTURE_TO_NOTION_CONFIG_DIR", str(tmp_path))
    config = ensure_config()
    store = CacheV2Store(config)
    graph = {
        "cache_version": 2,
        "graph_id": "graph-1",
        "root": {"kind": "page", "id": "page-1"},
        "pages": {},
        "blocks": {},
        "databases": {},
        "data_sources": {"ds-1": {"data_source_id": "ds-1", "schema": {"Name": {"type": "title"}}}},
        "views": {"view-1": {"view_id": "view-1", "type": "gallery", "name": "Episodes", "data_source_id": "ds-1"}},
    }
    profile = {
        "cache_version": 2,
        "profile_id": "profile-1",
        "graph_id": "graph-1",
        "write_profiles": {
            "podcast_episode": {
                "canonical_data_source_id": "ds-1",
                "canonical_view_id": "view-1",
                "field_mapping": {"title": "Name"},
                "field_sources": {"title": "user_binding"},
            }
        },
    }
    store.write_graph("graph-1", graph)
    store.write_profile("profile-1", profile)
    store.bind_alias("Program", graph_id="graph-1", profile_id="profile-1", kind="page")

    preflight = build_capture_preflight(
        CaptureInput.from_dict({"raw_input": "标题：Example", "target_hint": "Program", "content_type_hint": "podcast_episode"}),
        store,
    )

    resolution = preflight["workflow"]["target_resolution"]
    assert preflight["workflow"]["planning"]["next_action"] == "capture_plan"
    assert resolution["data_source_id"] == "ds-1"
    assert resolution["view_id"] == "view-1"
    assert resolution["view_type"] == "gallery"
    assert resolution["context_verification_source"] == "write_profile"
