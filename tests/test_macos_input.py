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


VIBER, USER_APP, OTHER_APP = 9, 5, 7


def _front_backend(monkeypatch, foreground):
    """A backend whose front app is `foreground()`; records the apps made frontmost."""
    import threading

    b = macos.MacBackend.__new__(macos.MacBackend)
    b._handoff, b._pid = threading.Lock(), VIBER
    fronted = []
    monkeypatch.setattr(b, "_stop_if_away", lambda: None)
    monkeypatch.setattr(b, "_app", lambda: ("app", VIBER))
    monkeypatch.setattr(b, "foreground", foreground)
    monkeypatch.setattr(b, "_top_window_owner", lambda: VIBER)  # Viber's window on top
    monkeypatch.setattr(macos, "_alive", lambda pid: True)
    monkeypatch.setattr(macos, "AXUIElementCreateApplication", lambda pid: ("app", pid))
    monkeypatch.setattr(macos, "AXUIElementSetAttributeValue",
                        lambda el, attr, value: fronted.append(el[1]) if attr == "AXFrontmost" else 0)
    monkeypatch.setattr(macos.Quartz, "CGEventCreate", lambda source: None)
    monkeypatch.setattr(macos.Quartz, "CGEventGetLocation", lambda event: (0, 0))
    monkeypatch.setattr(macos.Quartz, "CGWarpMouseCursorPosition", lambda point: None)
    monkeypatch.setattr(macos.Quartz, "CGAssociateMouseAndMouseCursorPosition", lambda on: None)
    clock = [0.0]
    monkeypatch.setattr(macos.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(macos.time, "time", lambda: clock[0])
    return b, fronted


def test_a_hand_off_that_fails_still_gives_the_front_back(monkeypatch):
    # Viber was asked to the front, didn't get there in time, and the raise skipped the
    # hand-back: when its activation landed later it kept the front, and the user's typing.
    b, fronted = _front_backend(monkeypatch, lambda: USER_APP)
    with pytest.raises(OCRError, match="front"):
        b._with_viber_front(lambda: pytest.fail("input was sent"))
    assert fronted == [VIBER, USER_APP]


def test_a_hand_off_returns_only_once_the_user_app_is_back(monkeypatch):
    # The hand-back lands asynchronously: the next hand-off (Down, then Tab, in a mention) read
    # Viber as the front app, skipped bringing it forward, and its key reached the user's app.
    reads = {"all": 0, "after_hand_back": 0}

    def foreground():
        reads["all"] += 1
        if USER_APP not in fronted:  # the user's app until Viber is asked forward
            return USER_APP if reads["all"] == 1 else VIBER
        reads["after_hand_back"] += 1
        return VIBER if reads["after_hand_back"] <= 3 else USER_APP
    b, fronted = _front_backend(monkeypatch, foreground)
    b._with_viber_front(lambda: None)
    assert fronted == [VIBER, USER_APP] and reads["after_hand_back"] >= 4


FINDER = 3


def _windows(*pids):
    return [{"kCGWindowLayer": 0, "kCGWindowOwnerPID": p,
             "kCGWindowBounds": {"Width": 800, "Height": 600}} for p in pids]


def test_the_front_app_is_the_active_one_not_the_topmost_window(monkeypatch):
    # Measured 2026-11-07: after a click on the desktop Finder is active while Viber's window
    # stays on top. foreground() read window order only and said Viber, so a real key (Escape,
    # Return) went to Finder.
    b = macos.MacBackend.__new__(macos.MacBackend)
    monkeypatch.setattr(macos.Quartz, "CGWindowListCopyWindowInfo",
                        lambda options, wid: _windows(VIBER, USER_APP))
    monkeypatch.setattr(macos, "NSWorkspace", type("WS", (), {"sharedWorkspace": staticmethod(
        lambda: type("W", (), {"runningApplications": lambda self: [
            type("A", (), {"processIdentifier": lambda self: FINDER,
                           "activationPolicy": lambda self: 0})()]})())}))
    monkeypatch.setattr(macos, "_app_frontmost", lambda pid: pid == FINDER)
    assert b._top_window_owner() == VIBER
    assert b.foreground() == FINDER


def test_a_hand_off_needs_viber_active_not_just_its_window_on_top(monkeypatch):
    # Same state: window order said Viber was in front, so the hand-off skipped bringing it
    # forward and sent its real key to Finder. Now Viber is made active first and Finder gets
    # the front back.
    sent = []

    def foreground():
        if VIBER not in fronted:
            return FINDER
        return VIBER if FINDER not in fronted else FINDER
    b, fronted = _front_backend(monkeypatch, foreground)
    b._with_viber_front(lambda: sent.append(foreground()))
    assert sent == [VIBER] and fronted == [VIBER, FINDER]


def test_the_hand_back_guard_leaves_an_app_the_user_switched_to(monkeypatch):
    # hold_foreground gave the front back to the user's earlier app whenever anything else had
    # it, so switching to a third app within the 2-4 s pulled the user back.
    fronts = iter([VIBER] + [OTHER_APP] * 200)  # Viber activated itself, then the user switched
    b, fronted = _front_backend(monkeypatch, lambda: next(fronts))
    b.hold_foreground(USER_APP, 4.0)
    assert fronted == [USER_APP]


def test_typing_into_a_field_that_is_gone_is_refused(monkeypatch):
    # _focus_el no longer answering (the page rebuilt) switched the focus check off: the
    # characters went to whatever had Viber's focus.
    field = Field()
    b = _backend(monkeypatch, field)
    monkeypatch.setattr(macos, "_get", lambda el, attr: None)
    monkeypatch.setattr(b, "_post_key", lambda *a, **k: pytest.fail("posted blind"))
    monkeypatch.setattr(b, "_with_viber_front", lambda action: pytest.fail("real keys sent"))
    with pytest.raises(OCRError, match="isn't there any more"):
        b.type_into_focus("Hazel")
    with pytest.raises(OCRError, match="isn't there any more"):
        b.press_key("backspace", 5)


def test_a_revoked_accessibility_grant_is_noticed(monkeypatch):
    # Once trusted, the grant was never asked again: revoked mid-run, every AX read then failed
    # as "not found (is a chat open?)".
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._trusted, b._asked_ax, b._pid = True, True, VIBER
    monkeypatch.setattr(macos, "_alive", lambda pid: True)
    monkeypatch.setattr(macos, "AXIsProcessTrusted", lambda: False)
    with pytest.raises(OCRError, match="Accessibility"):
        b._app()


def test_click_at_walks_up_from_inner_label_to_press_button_in_background(monkeypatch):
    # Clicking the center of a button or menu item can hit its inner AXStaticText/AXImage child.
    # Walking up AXParent to the enclosing AXButton lets AXPress run in the background without
    # stealing foreground focus via _with_viber_front.
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._focus_el = None
    pressed = []
    parents = {"inner_label": "send_btn", "send_btn": "toolbar"}
    roles = {"inner_label": "AXStaticText", "send_btn": "AXButton", "toolbar": "AXGroup"}
    monkeypatch.setattr(b, "_to_screen", lambda x, y: (x, y))
    monkeypatch.setattr(b, "_hit", lambda sx, sy: "inner_label")
    monkeypatch.setattr(macos, "_get", lambda el, attr: (
        roles.get(el) if attr == "AXRole" else parents.get(el) if attr == "AXParent" else None))
    monkeypatch.setattr(macos, "_actions", lambda el: ["AXPress"] if el == "send_btn" else [])
    monkeypatch.setattr(macos, "AXUIElementPerformAction",
                        lambda el, action: (pressed.append((el, action)), 0)[1])
    monkeypatch.setattr(b, "_with_viber_front", lambda action: pytest.fail("stole focus"))
    b.click_at(500, 500)
    assert pressed == [("send_btn", "AXPress")]


def test_visible_caches_hit_tests_and_resolves_ancestors_in_memory(monkeypatch):
    b = macos.MacBackend.__new__(macos.MacBackend)
    els = [
        {"type": "Window", "name": "", "class": "", "text": "", "parent": None,
         "rect": [0, 0, 1000, 800], "_frame": (0.0, 0.0, 1000.0, 800.0)},
        {"type": "Button", "name": "Send", "class": "SendToolbarButton", "text": "", "parent": 0,
         "rect": [700, 700, 740, 740], "_frame": (700.0, 700.0, 40.0, 40.0)},
        {"type": "Text", "name": "Send", "class": "", "text": "Send", "parent": 1,
         "rect": [700, 700, 740, 740], "_frame": (700.0, 700.0, 40.0, 40.0)},
    ]
    refs = ["win_ref", "btn_ref", "txt_ref"]
    index_of = {hash(r): [(r, i)] for i, r in enumerate(refs)}
    hit_calls = []

    def fake_hit(app, x, y, _out):
        hit_calls.append((x, y))
        return 0, "txt_ref"

    monkeypatch.setattr(macos, "CFHash", hash)
    monkeypatch.setattr(macos, "CFEqual", lambda a, b: a == b)
    monkeypatch.setattr(macos, "AXUIElementCopyElementAtPosition", fake_hit)
    monkeypatch.setattr(macos, "_get", lambda el, attr: pytest.fail("AXParent IPC should not be called"))
    vis = b._visible("app", els, refs, index_of, (0.0, 0.0, 1000.0, 800.0))
    vis_els, vis_refs = b._reindex(els, refs, vis)
    # Both Button and Text share center (720, 720): only 1 hit-test IPC call for that point,
    # and Button sees that "txt_ref" (index 2) is its descendant in memory without AXParent IPC.
    assert hit_calls.count((720.0, 720.0)) == 1
    assert [e["type"] for e in vis_els] == ["Window", "Button", "Text"]
    assert vis_refs == ["win_ref", "btn_ref", "txt_ref"]


def test_visible_only_if_skips_hit_testing_non_matching_elements(monkeypatch):
    b = macos.MacBackend.__new__(macos.MacBackend)
    els = [
        {"type": "Window", "name": "", "class": "", "text": "", "parent": None,
         "rect": [0, 0, 1000, 800], "_frame": (0.0, 0.0, 1000.0, 800.0)},
        {"type": "Text", "name": "Other", "class": "", "text": "Other", "parent": 0,
         "rect": [100, 100, 200, 120], "_frame": (100.0, 100.0, 100.0, 20.0)},
        {"type": "Button", "name": "Send", "class": "SendToolbarButton", "text": "", "parent": 0,
         "rect": [700, 700, 740, 740], "_frame": (700.0, 700.0, 40.0, 40.0)},
    ]
    refs = ["win_ref", "txt_ref", "btn_ref"]
    index_of = {hash(r): [(r, i)] for i, r in enumerate(refs)}
    hit_calls = []

    def fake_hit(app, x, y, _out):
        hit_calls.append((x, y))
        return 0, "btn_ref"

    monkeypatch.setattr(macos, "CFHash", hash)
    monkeypatch.setattr(macos, "CFEqual", lambda a, b: a == b)
    monkeypatch.setattr(macos, "AXUIElementCopyElementAtPosition", fake_hit)
    vis = b._visible("app", els, refs, index_of, (0.0, 0.0, 1000.0, 800.0),
                     only_if=macos.ml.is_send)
    vis_els, _ = b._reindex(els, refs, vis)
    assert hit_calls == [(720.0, 720.0)]
    assert [e["class"] for e in vis_els] == ["SendToolbarButton"]

