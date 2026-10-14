import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends._winlogic import normalize_viber_url, split_line, to_observations  # noqa: E402
from viber_send import _find_result_row, _score_result  # noqa: E402
from viber_ui import _norm  # noqa: E402


def test_norm_folds_ocr_lookalikes():
    assert _norm("TESTCLlENT") == _norm("TESTCLIENT")
    assert _norm("T0M 1") == _norm("tom i")


def test_score_matches_ocr_misread_of_client_name():
    assert _score_result("TESTCLIENT-WH", "TESTCLlENT-WH-(300S...") > 0


def test_exact_match_outranks_prefix():
    assert _score_result("APG CLIENTS", "APG CLIENTS") > _score_result("APG CLIENTS", "APG CLIENTS 2")


def test_substring_scores_below_prefix():
    assert _score_result("Ivy", "Divya") < _score_result("Ivy", "Ivy Cloma")
    assert _score_result("Ivy", "Divya") > 0


def test_non_result_rows_are_rejected():
    for junk in ("+63 917 123 4567", "0917 123 4567", "Conversations", "Contacts", "Search...", "12 contacts"):
        assert _score_result("Ivy", junk) <= 0, junk


def test_find_result_row_prefers_prefix_over_substring_above_it():
    rows = [
        {"text": "Divya", "x": 0.1, "y": 0.2},
        {"text": "Ivy Cloma-Rosales", "x": 0.1, "y": 0.3},
        {"text": "Ivy", "x": 0.6, "y": 0.2},  # chat pane, ignored
    ]
    assert _find_result_row("Ivy", rows)[0] == "Ivy Cloma-Rosales"


def test_find_result_row_ignores_search_bar_and_returns_none():
    assert _find_result_row("Ivy", [{"text": "Ivy", "x": 0.1, "y": 0.05}]) is None


def test_split_line_separates_sidebar_from_chat_text():
    words = [("Ivy", 20, 100, 30, 12), ("Cloma", 55, 100, 40, 12), ("Hello", 400, 100, 40, 12),
             ("there", 445, 100, 40, 12)]
    segments = split_line(words)
    assert [s[0] for s in segments] == ["Ivy Cloma", "Hello there"]
    assert segments[1][1] == 400


def test_split_line_keeps_close_words_together():
    assert len(split_line([("a", 0, 0, 10, 10), ("b", 15, 0, 10, 10)])) == 1


def test_to_observations_normalizes_sorts_and_drops_blank():
    segments = [("lower", 100, 500, 50, 10), ("  ", 0, 0, 5, 5), ("upper", 200, 100, 50, 10)]
    observations = to_observations(segments, 1000, 1000)
    assert [o["text"] for o in observations] == ["upper", "lower"]
    assert observations[0]["x"] == 0.2 and observations[0]["y"] == 0.1


def test_normalize_viber_url_encodes_plus():
    assert normalize_viber_url("viber://chat?number=+639171234567") == "viber://chat?number=%2B639171234567"
