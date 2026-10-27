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


def test_find_result_row_prefers_whole_word_over_longer_prefix_above_it():
    # Regression: the topmost prefix match used to win ("Max" opened "Maxine Cruz").
    for query, rows, want in [
        ("Max", ["Maxine Cruz", "Max Alpha"], "Max Alpha"),
        ("Jean", ["Jeanette Uy", "Jean Dela Cruz"], "Jean Dela Cruz"),
        ("Anthony", ["Mark Anthony Santos", "Anthony Reyes"], "Anthony Reyes"),
    ]:
        obs = [{"text": t, "x": 0.1, "y": 0.2 + 0.1 * i} for i, t in enumerate(rows)]
        assert _find_result_row(query, obs)[0] == want, query


def test_await_results_skips_the_stale_chat_list(monkeypatch):
    # Live-app regression: the first read after typing still shows the old chat list, where the
    # same name sits at another y. Only a read with a results header, agreeing twice, is clickable.
    import viber_send
    stale = [{"text": "Max Alpha", "x": 0.1, "y": 0.942}]
    results = [{"text": "Conversations", "x": 0.05, "y": 0.13}, {"text": "Max Alpha", "x": 0.1, "y": 0.179}]
    reads = iter([stale, stale, results, results, results])

    class FakeBackend:
        def observe(self, region=None):
            return next(reads)

    monkeypatch.setattr(viber_send, "backend", FakeBackend())
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    _, match = viber_send._await_results("Max")
    assert match[:2] == ("Max Alpha", 0.179)


def test_paste_pauses_only_for_fire_and_forget_backends(monkeypatch):
    import viber_send
    for confirmed, expect_pauses in ((True, False), (False, True)):
        class FakeBackend:
            input_confirmed = confirmed
            def observe(self, region=None): return []
            def __getattr__(self, name): return lambda *a: None
        slept = []
        monkeypatch.setattr(viber_send, "backend", FakeBackend())
        monkeypatch.setattr(viber_send.time, "sleep", slept.append)
        viber_send._paste_into_compose("hi")
        assert bool(slept) == expect_pauses, confirmed


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


def test_await_results_reads_a_few_times_when_ocr_is_slow(monkeypatch):
    # Live-app regression (macOS, Intel): one OCR read took longer than the whole timeout, it
    # showed the old chat list, and open_my_notes failed with "no matching chat".
    import viber_send
    stale = [{"text": "Eugene Gre", "x": 0.1, "y": 0.3}]
    results = [{"text": "Conversations", "x": 0.05, "y": 0.13}, {"text": "My Notes", "x": 0.1, "y": 0.179}]
    reads = iter([stale, results, results, results])
    clock = {"t": 0.0}

    class SlowBackend:
        def observe(self, region=None):
            clock["t"] += 3.0  # each read outlasts the 2.4 s timeout
            return next(reads)

    monkeypatch.setattr(viber_send, "backend", SlowBackend())
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    monkeypatch.setattr(viber_send.time, "monotonic", lambda: clock["t"])
    _, match = viber_send._await_results("My Notes")
    assert match is not None and match[0] == "My Notes"
