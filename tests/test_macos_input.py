"""MacBackend input with the AX calls faked (macOS only: the module imports Quartz/AppKit)."""

import sys
from pathlib import Path

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS backend", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import macos  # noqa: E402
from backends.base import OCRError  # noqa: E402


class Field:
    def __init__(self, value="", accepts=True):
        self.value, self.accepts, self.focused = value, accepts, 0


def _backend(monkeypatch, field):
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._focus_el = field

    def get(el, attr):
        return {"AXRole": "AXTextArea", "AXValue": el.value, "AXIdentifier": "field"}.get(attr)

    def set_attr(el, attr, value):
        if attr == "AXFocused" and value:
            el.focused += 1
            el.accepts = True  # what re-focusing does for Qt after a hand-off
        elif attr == "AXValue":
            el.value = value

    def post_key(code, flags=0, text=None):
        if field.accepts:
            field.value += text
    monkeypatch.setattr(macos, "_get", get)
    monkeypatch.setattr(macos, "AXUIElementSetAttributeValue", set_attr)
    monkeypatch.setattr(b, "_post_key", post_key)
    monkeypatch.setattr(b, "_focused", lambda: field, raising=False)  # PR #39's focus check
    monkeypatch.setattr(macos.time, "sleep", lambda s: None)
    return b


def test_typing_focuses_the_field_again_after_a_real_key_hand_off(monkeypatch):
    # 2026-10-28: the GC rename pressed End/Backspace (real keys, Viber brought to the front and
    # back) and the posted name never arrived: "The group name didn't take (got '')".
    field = Field("", accepts=False)
    b = _backend(monkeypatch, field)
    b.type_into_focus("AIRA-WH")
    assert field.focused == 1 and field.value == "AIRA-WH"


def test_typing_that_never_lands_is_an_error_not_a_silent_success(monkeypatch):
    field = Field("Hello po, @Unknown")
    b = _backend(monkeypatch, field)
    monkeypatch.setattr(b, "_post_key", lambda *a, **k: None)  # characters dropped
    monkeypatch.setattr(macos.time, "time", iter(range(0, 1000)).__next__)
    with pytest.raises(OCRError):
        b.type_into_focus("! Please check the listing above po.")


def test_a_hid_click_keeps_the_field_typing_goes_to(monkeypatch):
    # Picking a mention row / ticking a picker circle is a real click; typing after it still
    # belongs in the field clicked before.
    field = Field()
    b = _backend(monkeypatch, field)
    monkeypatch.setattr(b, "_to_screen", lambda x, y: (x, y))
    monkeypatch.setattr(b, "_hit", lambda sx, sy: None)
    monkeypatch.setattr(b, "_with_viber_front", lambda action: None)
    b.click_at(380, 714)
    assert b._focus_el is field


def test_a_real_click_on_another_field_moves_typing_there(monkeypatch):
    # State-machine hunt 2026-11-06: with Viber's search open the hit test can miss the search
    # box, so the click is a real one, and _focus_el stayed on the field clicked before (the
    # open chat's message box). type_into_focus then focused that box again and typed the query
    # into it, where an Enter sends it to the client.
    box, search = Field(), Field()
    b = _backend(monkeypatch, box)
    now = {"focus": box}  # what has Viber's keyboard focus

    def set_attr(el, attr, value):
        if attr == "AXFocused" and value:
            now["focus"] = el
        elif attr == "AXValue":
            el.value = value
    monkeypatch.setattr(macos, "_get", lambda el, attr: {
        "AXRole": "AXTextArea", "AXValue": el.value, "AXIdentifier": "box" if el is box else "search"}.get(attr))
    monkeypatch.setattr(macos, "AXUIElementSetAttributeValue", set_attr)
    monkeypatch.setattr(b, "_focused", lambda: now["focus"], raising=False)
    monkeypatch.setattr(b, "_post_key", lambda code, flags=0, text=None: setattr(
        now["focus"], "value", now["focus"].value + text))
    monkeypatch.setattr(b, "_to_screen", lambda x, y: (x, y))
    monkeypatch.setattr(b, "_hit", lambda sx, sy: None)
    monkeypatch.setattr(b, "_with_viber_front", lambda action: now.update(focus=search))  # the real click
    b.click_at(150, 40)
    b.type_into_focus("Ivy")
    assert search.value == "Ivy" and box.value == ""


def test_text_editing_keys_are_posted_without_bringing_viber_forward(monkeypatch):
    # 2026-11-01: the GC rename's End + Backspace brought Viber to the front (real keys); posted
    # to Viber's process they edit the clicked field in the background.
    field = Field("Group")
    b = _backend(monkeypatch, field)
    posted = []
    monkeypatch.setattr(b, "_post_key", lambda code, flags=0, text=None: posted.append(code))
    monkeypatch.setattr(b, "_with_viber_front", lambda action: pytest.fail("Viber was brought forward"))
    b.press_key("end")
    b.press_key("backspace", 7)
    assert posted == [macos.ml.KEYCODES["end"]] + [macos.ml.KEYCODES["backspace"]] * 7
    assert field.focused == 2


def test_other_keys_still_use_the_front_hand_off(monkeypatch):
    # Posted Escape doesn't reach the image viewer, and Return doesn't open a search result.
    b = _backend(monkeypatch, Field())
    handed = []
    monkeypatch.setattr(b, "_post_key", lambda *a, **k: pytest.fail("posted"))
    monkeypatch.setattr(b, "_with_viber_front", lambda action: handed.append(action))
    monkeypatch.setattr(b, "_onscreen_window", lambda: None)
    b.press_key("escape")
    assert len(handed) == 1


# --- State-machine hunt 2026-11-06 ----------------------------------------------------------


class _Item:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def types(self):
        return list(self.data)

    def dataForType_(self, t):
        return self.data[t]

    def setData_forType_(self, data, t):
        self.data[t] = data


class _Board:
    def __init__(self, text):
        self.items, self.count = [_Item({"public.utf8-plain-text": text})], 1

    def pasteboardItems(self):
        return self.items

    def clearContents(self):
        self.items, self.count = [], self.count + 1

    def writeObjects_(self, objects):
        self.items, self.count = list(objects), self.count + 1
        return True

    def changeCount(self):
        return self.count


def _paste_backend(monkeypatch, board):
    monkeypatch.setattr(macos, "NSPasteboard", type("PB", (), {"generalPasteboard": staticmethod(lambda: board)}))
    monkeypatch.setattr(macos, "NSPasteboardItem", type("PI", (), {
        "alloc": staticmethod(lambda: type("A", (), {"init": lambda self: _Item()})())}))
    monkeypatch.setattr(macos, "NSURL", type("U", (), {"fileURLWithPath_": staticmethod(lambda p: _Item({"url": p}))}))
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._focus_el = None
    monkeypatch.setattr(b, "click_compose", lambda: None)
    monkeypatch.setattr(b, "_check_focus", lambda field: None)
    monkeypatch.setattr(b, "_value", lambda: "")
    monkeypatch.setattr(b, "_post_key", lambda *a, **k: None)
    monkeypatch.setattr(macos.time, "sleep", lambda s: None)
    return b


def test_paste_files_gives_the_user_their_clipboard_back(monkeypatch):
    board = _Board(b"the user's text")
    assert _paste_backend(monkeypatch, board).paste_files(["/tmp/a.jpg"], lambda: True)
    assert [i.data for i in board.items] == [{"public.utf8-plain-text": b"the user's text"}]


def test_paste_files_keeps_what_the_user_copied_while_viber_took_the_files(monkeypatch):
    # paste_files put its snapshot back unconditionally, over anything the user copied during
    # the up-to-10 s wait for Viber to take the files.
    board = _Board(b"old")

    def taken():
        board.writeObjects_([_Item({"public.utf8-plain-text": b"copied meanwhile"})])
        return True
    assert _paste_backend(monkeypatch, board).paste_files(["/tmp/a.jpg"], taken)
    assert [i.data for i in board.items] == [{"public.utf8-plain-text": b"copied meanwhile"}]


def test_focus_doesnt_start_viber_when_it_isnt_running(monkeypatch):
    # focus()'s `open -g -b` / `open -b` fallback launched (and activated) a Viber that wasn't
    # running, around viber_app: past "Start Viber automatically" off and its update wait.
    b = macos.MacBackend.__new__(macos.MacBackend)
    monkeypatch.setattr(b, "_onscreen_window", lambda: None)
    monkeypatch.setattr(b, "_offspace_window", lambda: None)
    monkeypatch.setattr(b, "_viber_pid", lambda: None)
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **k: pytest.fail(f"ran {a[0]}"))
    with pytest.raises(OCRError, match="not running"):
        b.focus()
