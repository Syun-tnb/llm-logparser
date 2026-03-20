from llm_logparser.core.analyzer_common import (
    normalize_role,
    resolve_canonical_text,
    safe_ratio,
)


def test_normalize_role_handles_known_unknown_and_empty_inputs():
    assert normalize_role("USER") == "user"
    assert normalize_role(" assistant ") == "assistant"
    assert normalize_role("moderator") == "unknown"
    assert normalize_role("") == "unknown"
    assert normalize_role(None) == "unknown"


def test_resolve_canonical_text_prefers_text_then_content_parts_then_empty():
    assert resolve_canonical_text({"text": "hello"}) == ("hello", "text")
    assert resolve_canonical_text(
        {"text": None, "content": {"parts": ["alpha", "beta"]}}
    ) == ("alpha\nbeta", "content.parts")
    assert resolve_canonical_text(
        {"content": {"parts": ["alpha", 1, None, "beta"]}}
    ) == ("alpha\nbeta", "content.parts")
    assert resolve_canonical_text({"content": {"parts": []}}) == ("", "empty")
    assert resolve_canonical_text({}) == ("", "empty")


def test_safe_ratio_returns_stable_float_and_handles_zero_denominator():
    assert safe_ratio(1, 4) == 0.25
    assert safe_ratio(2, 3) == 0.6667
    assert safe_ratio(5, 0) == 0.0
    assert safe_ratio(0, 0) == 0.0
    assert isinstance(safe_ratio(5, 0), float)
