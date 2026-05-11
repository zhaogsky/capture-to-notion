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


def test_capture_input_from_dict_uses_defaults_and_ignores_extra_options():
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
    assert capture.state == "initialized"
    assert capture.content_type_hint is None
    assert capture.user_intent == "capture_to_notion"
    assert capture.options.allow_web_search is False
    assert capture.options.allow_target_search is True
    assert capture.options.allow_asset_download is True
    assert capture.options.dry_run is False
