import pytest

from llm_logparser.cli.parser_builder import build_parser
from llm_logparser.core.i18n import _, set_locale


def test_top_level_parser_help_uses_i18n_strings():
    set_locale("ja-JP")

    help_text = build_parser().format_help()

    assert _("cli.description") in help_text
    assert _("cli.option.config.help") in help_text
    assert _("cli.option.profile.help") in help_text
    assert _("cli.option.non_interactive.help") in help_text
    assert _("cli.export.help") in help_text
    assert _("cli.chain.help") in help_text


def test_parse_help_uses_i18n_strings(capsys):
    set_locale("ja-JP")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["parse", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert _("cli.parse.opt.input.help") in help_text
    assert _("cli.parse.opt.validate_schema.help") in help_text


def test_unknown_locale_falls_back_to_english_for_parser_help():
    set_locale("fr-FR")

    help_text = build_parser().format_help()

    assert _("cli.description") == "CLI interface for LLM Log Parser (MVP)"
    assert _("cli.option.config.help") == "Path to config.yaml"
    assert _("cli.analyze.help") == "Analyze canonical parsed JSONL threads"
    assert "CLI interface for LLM Log Parser (MVP)" in help_text
    assert "Path to config.yaml" in help_text
    assert "Analyze canonical parsed JSONL threads" in help_text


def test_analyze_metrics_help_mentions_token_stats_dependency(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "metrics", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "metrics.json" in help_text
    assert "token_stats.json" in help_text
    assert "Run `analyze tokens` first." in help_text
    assert "--skip-existing" in help_text
    assert "--dry-run" in help_text
    assert "rebuilt and overwritten" in help_text


def test_analyze_tokens_help_mentions_skip_existing_policy(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "tokens", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--skip-existing" in help_text
    assert "--dry-run" in help_text
    assert "rebuilt and overwritten" in help_text


def test_analyze_semantic_prototype_help_mentions_backend_options(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "semantic-prototype", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--backend" in help_text
    assert "--model" in help_text
    assert "--max-input-bytes" in help_text
    assert "--chunk-overlap-bytes" in help_text
    assert "deterministic-hash" in help_text
    assert "ollama" in help_text


def test_analyze_semantic_prototype_parser_uses_current_default_min_score():
    set_locale("en-US")
    parser = build_parser()

    args = parser.parse_args(["analyze", "semantic-prototype"])

    assert args.min_score == 0.62
    assert args.top_k == 5


def test_analyze_semantic_preview_help_mentions_lookup_options(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "semantic-preview", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--cluster-id" in help_text
    assert "--conversation-id" in help_text
    assert "--thread" in help_text
    assert "--window" in help_text
    assert "--top-clusters" in help_text
    assert "--min-cluster-size" in help_text
    assert "--cross-thread-only" in help_text
    assert "--json" in help_text
    assert "--top-k" in help_text
    assert "--max-chars" in help_text


def test_analyze_semantic_topic_help_mentions_ollama_options(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "semantic-topic", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--model" in help_text
    assert "--cluster-id" in help_text
    assert "--top-clusters" in help_text
    assert "--min-cluster-size" in help_text
    assert "--cross-thread-only" in help_text
    assert "--base-url" in help_text
    assert "--timeout-seconds" in help_text
    assert "--state-locale" in help_text
    assert "--json" in help_text


def test_analyze_semantic_topics_help_mentions_artifact_builder_options(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "semantic-topics", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--model" in help_text
    assert "--cluster-id" in help_text
    assert "--min-cluster-size" in help_text
    assert "--cross-thread-only" in help_text
    assert "--base-url" in help_text
    assert "--timeout-seconds" in help_text
    assert "--state-locale" in help_text


def test_analyze_semantic_topic_explore_help_mentions_navigation_options(capsys):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["analyze", "semantic-topic-explore", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--topic-id" in help_text
    assert "--message-id" in help_text
    assert "--conversation-id" in help_text
    assert "--thread" in help_text
    assert "--hide-single-window" in help_text
    assert "--min-window-count" in help_text
    assert "--min-conversation-count" in help_text
    assert "--min-displayable-messages" in help_text
    assert "--view" in help_text
    assert "--full-messages" in help_text
    assert "--max-chars" in help_text
    assert "--json" in help_text


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["parse", "--help"], "Input: raw export file or directory"),
        (["chain", "--help"], "Input: raw export file or directory"),
        (["extract", "--help"], "Input: raw export file or directory"),
        (["export", "--help"], "Input: single parsed.jsonl file"),
        (
            ["analyze", "stats", "--help"],
            "Input: parsed.jsonl file or directory containing parsed.jsonl",
        ),
        (
            ["analyze", "sqlite-build", "--help"],
            "Input: directory only; root containing per-provider artifact directories",
        ),
        (
            ["analyze", "semantic-preview", "--help"],
            "Input: directory only; provider artifact root containing message_windows.jsonl, window_clusters.jsonl, and optional window_neighbors.jsonl files",
        ),
    ],
)
def test_help_text_clarifies_input_semantics(capsys, argv, expected):
    set_locale("en-US")
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    normalized_help_text = " ".join(help_text.split())
    assert expected in normalized_help_text
