from capture_to_notion.classifier import classify_content_type, normalize_state
from capture_to_notion.models import CaptureInput, CaptureOptions


def test_classifies_book_from_hint_and_title_marks():
    capture = CaptureInput(
        raw_input="把《可能性的艺术》初始化到书单",
        target_hint="书单",
        state="初始化",
        content_type_hint="book",
        user_intent="capture_to_notion",
        options=CaptureOptions(),
    )

    assert classify_content_type(capture) == "book"
    assert normalize_state(capture.state) == "initialized"


def test_classifies_podcast_from_url_language():
    capture = CaptureInput(
        raw_input="存一下这期播客 https://example.com/episode/123",
        target_hint=None,
        state="听完",
        content_type_hint=None,
        user_intent="capture_to_notion",
        options=CaptureOptions(),
    )

    assert classify_content_type(capture) == "podcast_episode"
    assert normalize_state(capture.state) == "completed"


def test_unknown_state_defaults_to_initialized():
    assert normalize_state(None) == "initialized"
    assert normalize_state("收藏") == "initialized"


def test_normalize_state_uses_configured_aliases_and_canonical_keys():
    states_config = {
        "states": {
            "queued": {"aliases": ["Queue Me", "待办"]},
            "done": {"aliases": ["Finished"]},
        }
    }

    assert normalize_state("queue me", states_config) == "queued"
    assert normalize_state("Finished", states_config) == "done"
    assert normalize_state(" DONE ", states_config) == "done"


def test_content_type_hint_prioritizes_over_conflicting_text():
    capture = CaptureInput(
        raw_input="这期播客很不错，帮我记录一下",
        target_hint=None,
        state="初始化",
        content_type_hint="book",
        user_intent="capture_to_notion",
        options=CaptureOptions(),
    )

    assert classify_content_type(capture) == "book"


def test_content_type_hint_accepts_profile_defined_types():
    capture = CaptureInput(
        raw_input="标题：CTN E2E View Constraint 2026-06-02\n标签：社会",
        target_hint="ctn-e2e-view-source-ds",
        state=None,
        content_type_hint="note",
        user_intent="capture_to_notion",
        options=CaptureOptions(),
    )

    assert classify_content_type(capture) == "note"


def test_normalize_state_handles_whitespace_and_case_aliases():
    assert normalize_state("  COMPLETED  ") == "completed"
    assert normalize_state("  已读  ") == "completed"


def test_classify_content_type_returns_unknown_for_unrelated_text():
    capture = CaptureInput(
        raw_input="今天开会讨论下周安排",
        target_hint=None,
        state="初始化",
        content_type_hint=None,
        user_intent="capture_to_notion",
        options=CaptureOptions(),
    )

    assert classify_content_type(capture) == "unknown"


def test_capture_input_from_dict_does_not_infer_default_state_and_ignores_extra_options():
    capture = CaptureInput.from_dict(
        {
            "raw_input": "记录这个",
            "options": {
                "allow_web_search": False,
                "unexpected_option": True,
            },
        }
    )

    assert capture.target_hint is None
    assert capture.state is None
    assert capture.content_type_hint is None
    assert capture.user_intent == "capture_to_notion"
    assert capture.options.allow_web_search is False
    assert capture.options.allow_target_search is True
    assert capture.options.allow_asset_download is True
    assert capture.options.dry_run is False


def test_capture_input_preserves_ai_structured_enrichment_payload():
    capture = CaptureInput.from_dict(
        {
            "raw_input": "补全这个条目",
            "structured_record": {"title": "Example", "rating": 5},
            "entities": [
                {
                    "entity_id": "speaker-1",
                    "entity_type": "person",
                    "record": {"name": "Ada", "bio": "Mathematician"},
                    "field_mapping": {"name": "Name", "bio": "Bio"},
                    "target": {"kind": "relation_target", "relation_key": "speaker"},
                    "sources": ["source-1"],
                }
            ],
            "enrichment": {
                "record_patch": {"summary": "AI 整理后的摘要"},
                "entities": [],
                "sources": ["source-1"],
                "conflicts": [],
                "confirmation_status": "confirmed",
            },
            "sources": [
                {
                    "source_id": "source-1",
                    "type": "web",
                    "title": "Reference",
                    "url": "https://example.com/ref",
                    "provided_by": "skill_ai",
                    "confidence": "medium",
                }
            ],
            "verification_requirements": [
                {"target_id": "speaker-page", "required": True, "fields": ["name", "bio"]}
            ],
        }
    )

    assert capture.structured_record == {"title": "Example", "rating": 5}
    assert capture.entities[0]["entity_id"] == "speaker-1"
    assert capture.enrichment["confirmation_status"] == "confirmed"
    assert capture.sources[0]["provided_by"] == "skill_ai"
    assert capture.verification_requirements[0]["target_id"] == "speaker-page"
    assert capture.to_dict()["structured_record"] == {"title": "Example", "rating": 5}
