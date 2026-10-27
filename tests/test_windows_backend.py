"""Unit tests for the Windows backend's speed-critical pieces, with fakes instead of Viber:
the cached UIA walk (`_Node`, `_levels`, `_elements`), `wait_still`, `observe(region)`, the
cached value reads and their invalidation, the non-blocking `open_url`, and the wheel gap.
They run on Windows (CI is windows-latest) and skip elsewhere, where pywin32/WinRT don't install."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows backend only")
if sys.platform == "win32":
    import win32con
    import win32gui

    import backends.windows as bw
    from backends import OCRError
    from PIL import Image

RECT = bw.auto.PropertyId.BoundingRectangleProperty if sys.platform == "win32" else 0
CTYPE = bw.auto.PropertyId.ControlTypeProperty if sys.platform == "win32" else 0
NAME = bw.auto.PropertyId.NameProperty if sys.platform == "win32" else 0
AUTOID = bw.auto.PropertyId.AutomationIdProperty if sys.platform == "win32" else 0
CLS = bw.auto.PropertyId.ClassNameProperty if sys.platform == "win32" else 0
VALUE = bw.auto.PropertyId.ValueValueProperty if sys.platform == "win32" else 0


class FakeArray:
    def __init__(self, items):
        self.items = items
        self.Length = len(items)

    def GetElement(self, i):
        return self.items[i]


class FakeElement:
    """A UIA element as the cache request would return it: properties by id, children on demand."""
    def __init__(self, ctype=50026, cls="Item_QMLTYPE_12", name="", autoid="", value=None, rect=(0, 0, 10, 10),
                 children=(), cached_children=True):
        self.props = {CTYPE: ctype, CLS: cls, NAME: name, AUTOID: autoid, VALUE: value,
                      RECT: (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]) if rect else None}
        self.children, self.cached_children = list(children), cached_children
        self.find_calls = 0

    def GetCachedPropertyValue(self, pid):
        return self.props[pid]

    def FindAllBuildCache(self, scope, condition, request):
        self.find_calls += 1
        return FakeArray(self.children)

    def GetCachedChildren(self):
        if not self.cached_children:
            raise ValueError("NULL COM pointer access")  # what comtypes raises for a leaf
        return FakeArray(self.children)


def _tree(root, monkeypatch):
    """Point `_levels` at a fake root for hwnd 1."""
    class UA:
        def ElementFromHandle(self, hwnd):
            return root
    monkeypatch.setattr(bw, "_uia_cache", {"ua": UA(), "request": object(), "condition": object()})


def test_node_reads_cached_properties_and_rect_math():
    el = FakeElement(ctype=50004, cls="QQuickTextEdit_QMLTYPE_475", name="box", autoid="x.SendToolbarButton",
                     value="draft", rect=(10, 20, 110, 70))
    node = bw._Node(el, None)
    assert (node.ControlTypeName, node.ClassName, node.AutomationId, node.Name, node.Value) == \
        ("EditControl", "QQuickTextEdit_QMLTYPE_475", "x.SendToolbarButton", "box", "draft")
    assert node.rect == (10, 20, 110, 70)
    assert bw._is_compose(node) and bw._is_send(node) and not bw._is_search(node)
    empty = bw._Node(FakeElement(value=12345, rect=None), node)
    assert empty.rect == (0, 0, 0, 0) and empty.Value == "" and empty.parent is node


def test_levels_is_breadth_first_two_levels_per_call_and_depth_bounded(monkeypatch):
    leaf = FakeElement(name="leaf", cached_children=False)
    deep = FakeElement(name="d4", children=[FakeElement(name="d5", children=[FakeElement(name="d6")])])
    c = FakeElement(name="c", children=[deep])
    a, b = FakeElement(name="a", children=[c]), FakeElement(name="b", children=[leaf])
    root = FakeElement(name="root", children=[a, b])
    _tree(root, monkeypatch)
    names = [n.Name for n in bw._levels(1, max_depth=9)]
    assert names == ["a", "c", "b", "leaf", "d4", "d5", "d6"]      # parents before children
    assert root.find_calls == 1 and a.find_calls == 0 and c.find_calls == 1  # grandchildren come cached
    nodes = {n.Name: n for n in bw._levels(1, max_depth=9)}
    assert nodes["c"].parent is nodes["a"] and nodes["a"].parent is None
    assert [n.Name for n in bw._levels(1, max_depth=2)] == ["a", "c", "b", "leaf"]
    assert [n.Name for n in bw._levels(1, max_depth=1)] == ["a", "b"]


def test_levels_terminates_on_a_looping_tree(monkeypatch):
    # While the Sponsored ad shows, Qt's tree loops back on itself: the walk must stay bounded.
    root = FakeElement(name="root")
    child = FakeElement(name="child", children=[root])
    root.children = [child]
    _tree(root, monkeypatch)
    assert len(list(bw._levels(1, max_depth=9))) == 9


def test_elements_filters_empty_and_offscreen_and_links_parents(monkeypatch):
    edit = FakeElement(ctype=50004, cls="QQuickTextEdit_QML_3", value="hi", rect=(320, 900, 760, 940))
    offscreen = FakeElement(name="scrolled out", rect=(400, 2000, 600, 2100),
                            children=[FakeElement(name="under offscreen", rect=(400, 100, 500, 120))])
    empty = FakeElement(name="loader", rect=None, children=[edit])
    pane = FakeElement(ctype=50033, cls="SplitView_QMLTYPE_9", name="", rect=(308, 0, 1100, 967),
                       children=[empty, offscreen])
    _tree(FakeElement(children=[pane]), monkeypatch)
    b = bw.WindowsBackend()
    monkeypatch.setattr(b, "_hwnd", lambda: 1)
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda hwnd: (0, 0, 1100, 967))
    out = b._elements()
    # Breadth-first: the pane, then (its empty loader and off-screen child are dropped) the
    # loader's edit and the off-screen item's on-screen child — both with no listed parent.
    assert [(e["type"], e["class"], e["text"], e["name"], e["parent"]) for e in out] == [
        ("Pane", "SplitView", "", "", None),
        ("Edit", "QQuickTextEdit", "hi", "", None),
        ("Group", "Item", "", "under offscreen", None)]
    assert out[1]["rect"] == [320, 900, 760, 940]


class FakeImg:
    def __init__(self, data):
        self.data = data

    def tobytes(self):
        return self.data


def test_wait_still_returns_on_two_identical_frames_or_timeout(monkeypatch):
    b = bw.WindowsBackend()
    frames = iter([b"a", b"b", b"c", b"c", b"d"])
    monkeypatch.setattr(b, "grab", lambda: (FakeImg(next(frames)), None))
    slept = []
    monkeypatch.setattr(bw.time, "sleep", slept.append)
    assert b.wait_still(timeout=5.0) >= 0
    assert len(slept) == 4            # the lead-in + one per compared frame until c == c
    ticks = iter([0.0, 0.1, 0.2, 5.0, 5.1])
    monkeypatch.setattr(bw.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(b, "grab", lambda: (FakeImg(bw.os.urandom(4)), None))
    assert b.wait_still(timeout=2.0) >= 2.0   # always changing: gives up at the timeout


class FakePool:
    """Stands in for the OCR thread: records the image it was handed, answers with canned words."""
    def __init__(self, words):
        self.words, self.images = words, []

    def submit(self, fn, img):
        self.images.append(img)
        pool = self

        class Done:
            def result(self, timeout=None):
                return [[(t, x, y, w, h) for (t, x, y, w, h) in pool.words]]
        return Done()


def test_observe_full_and_region_reads_give_window_coordinates(monkeypatch):
    b = bw.WindowsBackend()
    img = Image.new("RGB", (1000, 800), "white")
    monkeypatch.setattr(b, "grab", lambda: (img, (0, 0, 1000, 800)))
    # One word at (200, 100) of the OCR'd image, 100 px wide, 20 px tall.
    pool = FakePool([("hello", 200, 100, 100, 20)])
    monkeypatch.setattr(bw, "_ocr_pool", pool)
    full = b.observe()
    assert pool.images[-1].size == (2000, 1600)                   # <1400 px wide → read at 2x
    assert full == [{"text": "hello", "x": 0.1, "y": 0.0625, "w": 0.05, "h": 0.0125}]
    # The same word position inside a crop maps back through the crop's place in the window.
    side = b.observe(region=(0, 0, 0.3, 1))
    assert pool.images[-1].size == (600, 1600)                    # the sidebar crop, at 2x
    assert side[0]["x"] == pytest.approx(200 / 600 * 0.3) and side[0]["y"] == pytest.approx(100 / 1600)
    assert side[0]["w"] == pytest.approx(100 / 600 * 0.3)
    band = b.observe(region=(0.5, 0.5, 1, 0.64))
    assert pool.images[-1].size == (1000, 224)                    # a crop away from the origin
    assert band[0]["x"] == pytest.approx(0.5 + 200 / 1000 * 0.5)
    assert band[0]["y"] == pytest.approx(0.5 + 100 / 224 * 0.14)
    tiny = b.observe(region=(0, 0, 0.01, 0.01))                   # too small to OCR: whole window
    assert pool.images[-1].size == (2000, 1600) and tiny == full


def test_observe_reports_ocr_failures_as_ocr_errors(monkeypatch):
    b = bw.WindowsBackend()
    monkeypatch.setattr(b, "grab", lambda: (Image.new("RGB", (100, 100)), None))

    class Broken:
        def submit(self, fn, img):
            class Done:
                def result(self, timeout=None):
                    raise RuntimeError("engine died")
            return Done()
    monkeypatch.setattr(bw, "_ocr_pool", Broken())
    with pytest.raises(OCRError, match="engine died"):
        b.observe()


class FakeNode:
    def __init__(self, value="", broken=False):
        self._value, self.broken, self.set_calls = value, broken, []
        self.rect = (0, 0, 10, 10)

    def value_pattern(self):
        node = self

        class Pattern:
            @property
            def Value(self):
                if node.broken:
                    raise OSError("element gone")
                return node._value

            def SetValue(self, text):
                node.set_calls.append(text)
                node._value = text
        return Pattern()


def test_value_reads_the_located_box_and_refinds_it_when_gone(monkeypatch):
    b = bw.WindowsBackend()
    monkeypatch.setattr(b, "_hwnd", lambda: 1)
    finds = []
    fresh = FakeNode("fresh")
    monkeypatch.setattr(bw, "_find_desc", lambda hwnd, pred, max_depth=6: finds.append(pred) or fresh)
    b._target_node = FakeNode("cached")
    assert b._value() == "cached" and finds == []              # no tree walk
    b._target_node.broken = True
    assert b._value() == "fresh" and finds == [bw._is_compose]  # gone → re-found once
    assert b._target_node is fresh
    assert b._value() == "fresh" and len(finds) == 1


def test_value_is_empty_when_no_box_exists(monkeypatch):
    b = bw.WindowsBackend()
    monkeypatch.setattr(b, "_hwnd", lambda: 1)
    monkeypatch.setattr(bw, "_find_desc", lambda hwnd, pred, max_depth=6: None)
    assert b._value() == "" and b._target_node is None


def _posted(monkeypatch, b):
    posted = []
    monkeypatch.setattr(b, "_hwnd", lambda: 1)
    monkeypatch.setattr(win32gui, "PostMessage", lambda hwnd, msg, wp, lp: posted.append((msg, wp)))
    monkeypatch.setattr(win32gui, "GetWindowRect", lambda hwnd: (0, 0, 1100, 967))
    monkeypatch.setattr(win32gui, "ScreenToClient", lambda hwnd, pt: pt)
    monkeypatch.setattr(bw.time, "sleep", lambda s: None)
    return posted


@pytest.mark.parametrize("action", [
    lambda b: b.click_at(10, 10),
    lambda b: b.click_result(0.3),
    lambda b: b.press_key("escape"),
])
def test_anything_that_can_navigate_forgets_the_located_box(monkeypatch, action):
    b = bw.WindowsBackend()
    _posted(monkeypatch, b)
    b._target_node = FakeNode("x")
    action(b)
    assert b._target_node is None


def test_click_compose_keeps_the_box_it_clicked_for_value_reads(monkeypatch):
    b = bw.WindowsBackend()
    posted = _posted(monkeypatch, b)
    node = FakeNode("draft")
    monkeypatch.setattr(b, "_locate", lambda pred, what: node)
    monkeypatch.setattr(b, "_dismiss_remix", lambda: False)
    b.click_compose()
    assert b._target is bw._is_compose and b._target_node is node
    assert [m for m, _ in posted] == [win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP]
    assert b._value() == "draft"


def test_enter_posts_bmp_characters_and_falls_back_to_set_value_for_emoji(monkeypatch):
    b = bw.WindowsBackend()
    posted = _posted(monkeypatch, b)
    node = FakeNode("")
    b._target_node = node

    def post(hwnd, msg, wp, lp):
        posted.append((msg, wp))
        if msg == win32con.WM_CHAR:
            node._value += chr(wp)
    monkeypatch.setattr(win32gui, "PostMessage", post)
    monkeypatch.setattr(bw, "_uia_write_keeping_focus", lambda fn: fn())
    clock = [0.0]  # the posted attempt at the emoji text must time out: run it on a virtual clock
    monkeypatch.setattr(bw.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(bw.time, "time", lambda: clock[0])
    b._enter("hi")
    assert [chr(wp) for m, wp in posted if m == win32con.WM_CHAR] == ["h", "i"] and node.set_calls == []
    node._value = ""
    b._enter("ok 😀")                 # the emoji can't be posted: UIA SetValue instead
    assert node.set_calls == ["ok 😀"] and node._value == "ok 😀"


def test_delete_backspaces_single_line_text_and_set_values_multiline(monkeypatch):
    b = bw.WindowsBackend()
    posted = _posted(monkeypatch, b)
    node = FakeNode("abc")
    b._target_node = node

    def post(hwnd, msg, wp, lp):
        posted.append((msg, wp))
        if msg == win32con.WM_KEYDOWN and wp == win32con.VK_BACK:
            node._value = node._value[:-1]
    monkeypatch.setattr(win32gui, "PostMessage", post)
    monkeypatch.setattr(bw, "_uia_write_keeping_focus", lambda fn: fn())
    b.delete()
    assert node._value == "" and node.set_calls == []
    assert sum(1 for m, wp in posted if m == win32con.WM_KEYDOWN and wp == win32con.VK_BACK) == 5  # len + 2
    node._value = "line1\nline2"
    b.delete()
    assert node.set_calls == [""] and node._value == ""


def test_open_url_hands_the_foreground_back_from_a_thread(monkeypatch):
    b = bw.WindowsBackend()
    started, opened = [], []

    class FakeThread:
        def __init__(self, target, name="", daemon=False):
            started.append((target, name, daemon))

        def start(self):
            pass
    monkeypatch.setattr(bw.threading, "Thread", FakeThread)
    monkeypatch.setattr(bw.os, "startfile", opened.append)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 42)
    b._target_node = FakeNode("x")
    b.open_url("viber://chat?number=+639171234567")
    assert opened == ["viber://chat?number=%2B639171234567"]
    assert b._target_node is None
    (target, name, daemon), = started
    assert name == "viber-fg-guard" and daemon and callable(target)   # the caller never waits on it


def test_scroll_posts_a_wheel_notch_per_step_with_a_tiny_gap(monkeypatch):
    b = bw.WindowsBackend()
    posted = _posted(monkeypatch, b)
    slept = []
    monkeypatch.setattr(bw.time, "sleep", slept.append)
    monkeypatch.setattr(b, "_rect", lambda pred, what: (_ for _ in ()).throw(OCRError("no box")))
    b.scroll("chat", -60)
    wheel = [wp for m, wp in posted if m == win32con.WM_MOUSEWHEEL]
    assert len(wheel) == 60 and all(((wp >> 16) & 0xFFFF) == (-120 & 0xFFFF) for wp in wheel)
    assert sum(slept) == pytest.approx(60 * bw._WHEEL_GAP) and sum(slept) < 0.5   # was 1.8 s


def test_panel_check_is_not_needed_on_windows():
    assert bw.WindowsBackend.panel_shifts_send is False and bw.WindowsBackend.input_confirmed is True


def test_uia_timeout_abandons_the_stuck_thread_and_starts_a_fresh_one(monkeypatch):
    import concurrent.futures

    class StuckFuture:
        def result(self, timeout=None):
            raise concurrent.futures.TimeoutError()

    class StuckPool:
        def __init__(self): self.shutdowns = []
        def submit(self, fn, *args): return StuckFuture()
        def shutdown(self, wait=True, cancel_futures=False): self.shutdowns.append((wait, cancel_futures))
    stuck = StuckPool()
    monkeypatch.setattr(bw, "_uia_pool", stuck)
    with pytest.raises(OCRError, match="didn't answer"):
        bw._on_uia(lambda: None)
    assert stuck.shutdowns == [(False, True)] and bw._uia_pool is not stuck  # abandoned, replaced
    try:
        assert bw._on_uia(lambda x: x * 2, 21) == 42                           # the fresh thread works
    finally:
        bw._uia_pool.shutdown(wait=False)


def test_main_window_is_the_viber_ui_not_the_viberpc_popup(monkeypatch):
    # 2026-10-26: the "ViberPC" notification popup also contains "viber"; a substring match
    # picked it over "Rakuten Viber" and every capture/search/read on the live app failed.
    titles = {1: "ViberPC", 2: "QTrayIconMessageWindow", 3: "_q_titlebar", 4: "Rakuten Viber",
              5: "Viber", 6: ""}
    monkeypatch.setattr(win32gui, "GetWindowText", lambda h: titles[h])
    assert [h for h in titles if bw._is_main_window(h)] == [4, 5]

    monkeypatch.setattr(win32gui, "EnumWindows", lambda cb, extra: [cb(h, extra) for h in titles])
    monkeypatch.setattr(win32gui, "GetParent", lambda h: 0)
    monkeypatch.setattr(bw.win32process, "GetWindowThreadProcessId", lambda h: (0, 99))
    backend = bw.WindowsBackend.__new__(bw.WindowsBackend)
    monkeypatch.setattr(backend, "_viber_pids", lambda: {99}, raising=False)
    assert backend._find_hwnd() == 4      # the popup comes first in z-order but is skipped


def test_click_search_closes_the_remix_image_popup_first(monkeypatch):
    # The ChatGPT "Remix your image" pop-up covered the window, so the search click missed.
    from types import SimpleNamespace
    b = bw.WindowsBackend()
    posted = _posted(monkeypatch, b)
    later = SimpleNamespace(rect=(520, 576, 716, 608))
    found = [later, None]
    monkeypatch.setattr(bw, "_on_uia", lambda fn, *a: found.pop(0))
    monkeypatch.setattr(b, "wait_still", lambda *a, **k: 0.0)
    monkeypatch.setattr(b, "_locate", lambda pred, what: FakeNode(""))
    b.click_search()
    downs = [m for m, _ in posted if m == win32con.WM_LBUTTONDOWN]
    assert len(downs) == 2                      # "Maybe later", then the search box
    b.click_search()                            # no pop-up: just the search box
    assert len([m for m, _ in posted if m == win32con.WM_LBUTTONDOWN]) == 3
