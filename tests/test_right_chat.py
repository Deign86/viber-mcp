"""The open chat can change under us (staff using Viber, a viber:// link that didn't navigate, a
new post pulling another chat to the front) — messages went to the wrong person more than once.
These cover the header reader (`current_chat`), the `expect_chat` guard on every tool that acts
on "the open chat", and send verification from the UI tree instead of OCR."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backends  # noqa: E402
import viber_send  # noqa: E402
import viber_ui  # noqa: E402
from viber_send import SendError  # noqa: E402

PANE = [308, 71, 792, 1079]   # the chat pane (StackView) in a 1101 x 1087 window, live 2026-10-24


def _ui(kind, cls, rect, parent=None, text="", name=""):
    return {"type": kind, "name": name, "class": cls, "text": text, "parent": parent, "rect": list(rect)}


def _obs(text, x, y, w=0.08, h=0.012):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


class _Img:
    width, height = 1101, 1087


# Header band OCR as captured live: the top nav ("Viber Out") sits ABOVE the pane, the pinned
# message bar ("09178128999") BELOW the header band; an icon OCRs as "00 0" right of the title.
BEATRIZ = [_obs("Viber Out", 0.54, 0.043), _obs("Beatriz Alpha", 0.35, 0.083), _obs("00 0", 0.65, 0.089),
           _obs("Last seen today at 12:55 PM", 0.35, 0.106), _obs("09178128999", 0.294, 0.144)]
GROUP = [_obs("MON-WH-(2000SQM, BULACAN)", 0.35, 0.083), _obs("5 participants", 0.35, 0.106)]
UNSAVED = [_obs("+63 917 517 7830", 0.35, 0.083), _obs("Last seen yesterday", 0.35, 0.106)]


class HeaderBackend:
    """A backend whose window shows `header` in the chat pane's header band."""
    input_confirmed = True
    panel_shifts_send = False

    def __init__(self, header, messages=()):
        self.header, self.messages = header, [list(m) for m in messages]  # [text, mine]
        self.calls, self.staged, self.compose = [], "", ""

    def app_running(self): return True
    def screenshot(self): return _Img()
    def focus(self): self.calls.append("focus")

    def elements(self):
        els = [_ui("Pane", "StackView", PANE)]
        for i, (text, mine) in enumerate(self.messages):
            top = 160 + i * 60
            d = len(els)
            els.append(_ui("Group", "FeedDelegate", [308, top - 5, 792, top + 45], parent=0))
            els.append(_ui("Text", "TextEditItem", [548 if mine else 320, top, 760 if mine else 600, top + 40],
                           parent=d, text=text))
        els.append(_ui("Edit", "QQuickTextEdit", [320, 1000, 760, 1040], parent=0, text=self.compose))
        return els

    def observe(self, region=None):
        return list(self.header) + [_obs("Type a message...", 0.4, 0.97)]

    def clipboard_get(self): return "user's clipboard"
    def clipboard_set(self, text): self.calls.append(("clipboard", text)); self.staged = text
    def click_compose(self): self.calls.append("compose")
    def select_all(self): pass
    def delete(self): self.compose = ""
    def paste(self): self.compose = self.staged
    def click_send(self):
        self.calls.append("send")
        self.messages.append([self.compose, True])
        self.compose = ""


def _install(monkeypatch, fake):
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    return fake


# ------------------------------------------------------------------- current_chat


def test_current_chat_reads_the_header_band_only(monkeypatch):
    _install(monkeypatch, HeaderBackend(BEATRIZ))
    got = viber_send.current_chat()
    assert got == {"open": True, "title": "Beatriz Alpha", "subtitle": "Last seen today at 12:55 PM",
                   "is_group": False, "participants": None, "number": ""}


def test_current_chat_sees_groups_and_unsaved_numbers(monkeypatch):
    _install(monkeypatch, HeaderBackend(GROUP))
    got = viber_send.current_chat()
    assert got["is_group"] and got["participants"] == 5 and got["title"] == "MON-WH-(2000SQM, BULACAN)"
    _install(monkeypatch, HeaderBackend(UNSAVED))
    got = viber_send.current_chat()
    assert got["number"] == "+639175177830" and not got["is_group"]


def test_current_chat_with_nothing_open(monkeypatch):
    _install(monkeypatch, HeaderBackend([]))
    assert viber_send.current_chat()["open"] is False


# --------------------------------------------------------------- expect_chat guard


@pytest.mark.parametrize("expect", ["Beatriz Alpha", "Beatriz", "beatriz aIpha", "Beatriz Alpha (staff)"])
def test_require_open_chat_accepts_the_open_chats_name(monkeypatch, expect):
    _install(monkeypatch, HeaderBackend(BEATRIZ))
    assert viber_send.require_open_chat(expect)["title"] == "Beatriz Alpha"


def test_require_open_chat_accepts_a_number_in_the_header(monkeypatch):
    _install(monkeypatch, HeaderBackend(UNSAVED))
    assert viber_send.require_open_chat("+639175177830")["title"] == "+63 917 517 7830"
    assert viber_send.require_open_chat("09175177830")


def test_require_open_chat_refuses_another_chat(monkeypatch):
    _install(monkeypatch, HeaderBackend(BEATRIZ))
    with pytest.raises(SendError, match="open chat is 'Beatriz Alpha'.*not 'Mark Anthony Santos'"):
        viber_send.require_open_chat("Mark Anthony Santos")
    with pytest.raises(SendError, match="not '\\+639175177830'"):
        viber_send.require_open_chat("+639175177830")
    assert viber_send.require_open_chat("") == {}
    assert viber_send.require_open_chat("  ") == {}


def test_reply_refuses_before_touching_the_message_box(monkeypatch):
    fake = _install(monkeypatch, HeaderBackend(BEATRIZ))
    with pytest.raises(SendError, match="Nothing was sent"):
        viber_send.reply("hello", expect_chat="Mark Anthony Santos")
    assert fake.calls == [] and fake.messages == []


def test_reply_to_the_expected_chat_sends_and_names_it(monkeypatch):
    fake = _install(monkeypatch, HeaderBackend(BEATRIZ))
    out = viber_send.reply("hello po", expect_chat="Beatriz")
    assert out == {"sent": True, "text": "hello po", "verified": True, "chat": "Beatriz Alpha"}
    assert fake.messages == [["hello po", True]]


def test_put_draft_and_send_files_and_quote_reply_honour_expect_chat(monkeypatch, tmp_path):
    fake = _install(monkeypatch, HeaderBackend(BEATRIZ, messages=[("question?", False)]))
    with pytest.raises(SendError):
        viber_send.paste_into_open_chat("draft", expect_chat="Carlo S")
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    with pytest.raises(SendError):
        viber_ui.send_files([str(photo)], expect_chat="Carlo S")
    with pytest.raises(SendError):
        viber_ui.reply_to_message(1, "answer", expect_chat="Carlo S")
    assert fake.calls == []  # nothing clicked, nothing staged


def test_send_listing_passes_expect_chat_to_every_step(monkeypatch):
    import sys
    import types

    import viber_drive

    seen = []
    monkeypatch.setattr(viber_drive, "get_listing", lambda fid: {
        "doc": "d", "client_text": "t", "photos": [{"id": "p1", "name": "1.jpg"}], "videos": []})
    monkeypatch.setattr(viber_drive, "download", lambda ids: [f"/c/{i}" for i in ids])
    monkeypatch.setattr(viber_drive, "prune_downloads", lambda: None)
    monkeypatch.setitem(sys.modules, "viber_send", types.SimpleNamespace(
        reply=lambda text, expect_chat="": seen.append(("reply", expect_chat)) or {"verified": True},
        require_open_chat=lambda expect: seen.append(("guard", expect)) or {}))
    monkeypatch.setitem(sys.modules, "viber_ui", types.SimpleNamespace(
        send_files=lambda paths, expect_chat="": seen.append(("files", expect_chat)) or {"verified": True}))
    viber_drive.send_listing("folder", expect_chat="JANE-WH")
    assert seen == [("guard", "JANE-WH"), ("reply", "JANE-WH"), ("files", "JANE-WH")]


# ---------------------------------------------------- verification from the UI tree


def test_sent_confirmed_needs_a_new_message_of_mine(monkeypatch):
    fake = _install(monkeypatch, HeaderBackend(BEATRIZ, messages=[("noted po, thank you", True)]))
    before = viber_send.own_message_texts()
    assert before == ["noted po, thank you"]
    # The same text was already there: not confirmed by the tree, and OCR doesn't show it either.
    assert viber_send.sent_confirmed("noted po, thank you", before) is False
    fake.messages.append(["noted po, thank you", True])
    assert viber_send.sent_confirmed("noted po, thank you", before) is True


def test_sent_confirmed_ignores_isolate_marks_and_long_texts_match_by_first_line(monkeypatch):
    fake = _install(monkeypatch, HeaderBackend(BEATRIZ))
    before = viber_send.own_message_texts()
    fake.messages.append(["Call ⁨0917 555 5803⁩ po", True])
    assert viber_send.sent_confirmed("Call 0917 555 5803 po", before) is True
    listing = "WAREHOUSE FOR LEASE – SILANG\n\nProperty Details:\n• 1,890 sqm"
    fake.messages.append(["WAREHOUSE FOR LEASE – SILANG\n\nProperty Details:\n• 1,890 sqm (rendered)", True])
    assert viber_send.sent_confirmed(listing, before) is True


def test_sent_confirmed_falls_back_to_ocr_without_a_ui_tree(monkeypatch):
    class NoTree(HeaderBackend):
        def elements(self): raise NotImplementedError
    fake = _install(monkeypatch, NoTree(BEATRIZ))
    assert viber_send.own_message_texts() is None
    fake.header = BEATRIZ + [_obs("hello there friend", 0.5, 0.5)]
    assert viber_send.sent_confirmed("hello there friend", None) is True


def test_list_messages_and_backread_say_who_has_the_last_word(monkeypatch):
    fake = _install(monkeypatch, HeaderBackend(BEATRIZ, messages=[("ours", True), ("theirs", False)]))
    assert viber_ui.list_messages()["last_from"] == "them"
    fake.messages.append(["reply", True])
    assert viber_ui.list_messages()["last_from"] == "me"
