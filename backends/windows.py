"""Windows backend for Viber Desktop — runs in the background.

Unlike the macOS backend, this one never moves the cursor, uses the real keyboard, touches the
clipboard, or activates Viber, so you can keep working while it runs:

- Perception: the Viber window is rendered off-screen with PrintWindow (works while covered or
  on another monitor) and read with the built-in Windows.Media.Ocr.
- Locating: Viber (Qt Quick) exposes a UI Automation tree, so the search box, message box and
  send button are found by identity (not pixel offsets) and their text is read back via UIA.
- Input: clicks and characters are *posted* to Viber's window (WM_LBUTTON*/WM_CHAR/WM_KEY*),
  which Qt handles without the window becoming active. UIA writes are avoided because Qt
  activates the window on them; they are only a fallback for text posting can't produce
  (non-BMP emoji, newlines), followed by handing the foreground straight back.

The domain layer's clipboard/select/delete/paste sequence maps onto that: `clipboard_set` just
stages the text, `click_compose`/`click_search` pick and click the target, `delete` backspaces
it empty, `paste`/`type_text` type the text.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image
from uiautomation.uiautomation import _AutomationClient  # the raw IUIAutomation (for caching)

from . import _winlogic as wl
from .base import Backend, OCRError

_VIBER_EXE = "viber.exe"
_PW_RENDERFULLCONTENT = 2
_WHEEL_GAP = 0.005  # between posted wheel notches; Qt queues them (30 ms each made 60 notches ~2 s)

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

# ---- OCR (Windows.Media.Ocr) — runs in its own thread/event loop ----------------------
_ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viber-ocr")
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from winrt.windows.media.ocr import OcrEngine
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            from winrt.windows.globalization import Language
            eng = OcrEngine.try_create_from_language(Language("en-US"))
        if eng is None:
            raise OCRError(
                "No Windows OCR language available. Install an English language pack: "
                "Settings > Time & language > Language & region > Add a language (English)."
            )
        _engine = eng
    return _engine


async def _recognize(bgra: bytes, width: int, height: int):
    """OCR raw BGRA pixels. Returns a list of lines, each a list of (text, x, y, w, h) words in
    image px. The pixels go straight into a SoftwareBitmap: the PNG encode + decode round trip
    this replaced cost more than the recognition itself (and gave identical words)."""
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    engine = _get_engine()
    writer = DataWriter()
    writer.write_bytes(bgra)
    bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.IGNORE)
    bitmap.copy_from_buffer(writer.detach_buffer())
    result = await engine.recognize_async(bitmap)
    return [[(w.text, w.bounding_rect.x, w.bounding_rect.y, w.bounding_rect.width,
              w.bounding_rect.height) for w in line.words] for line in result.lines]


def _ocr_image(img: Image.Image):
    """OCR a PIL image (any mode) — runs on the OCR thread, which owns the engine."""
    return asyncio.run(_recognize(img.convert("RGBA").tobytes("raw", "BGRA"), img.width, img.height))


# ---- UI Automation — COM objects stay on one initialized thread -----------------------
_uia_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viber-uia",
                               initializer=auto.InitializeUIAutomationInCurrentThread)


def _on_uia(fn, *args):
    return _uia_pool.submit(fn, *args).result(timeout=30)


def _is_main_window(hwnd: int) -> bool:
    """The "Rakuten Viber" UI, not one of Qt's hidden helper windows (theme observer, tray, ...)."""
    return "viber" in win32gui.GetWindowText(hwnd).lower()


# Properties fetched in bulk for every element of a tree level (one cross-process call per
# level via FindAllBuildCache). uiautomation's Control wrappers fetch each property with its own
# round trip, which made a walk of Viber's tree take ~2x longer and a value read ~50x longer.
_UIA_PROPS = (auto.PropertyId.BoundingRectangleProperty, auto.PropertyId.ControlTypeProperty,
              auto.PropertyId.NameProperty, auto.PropertyId.AutomationIdProperty,
              auto.PropertyId.ClassNameProperty, auto.PropertyId.ValueValueProperty)
_SCOPE_CHILDREN = 2  # TreeScope_Children
_SCOPE_ELEMENT_CHILDREN = 3  # TreeScope_Element | TreeScope_Children (cache the children too)
_uia_cache: dict = {}  # per-process COM objects (request, condition), created on the UIA thread


class _Node:
    """One UIA element with its properties read from the cache. Attribute names match the
    uiautomation Control ones the predicates use (ControlTypeName, ClassName, AutomationId)."""
    __slots__ = ("element", "parent", "index", "ControlTypeName", "ClassName", "AutomationId",
                 "Name", "Value", "rect", "_pattern")

    def __init__(self, element, parent: "_Node | None") -> None:
        self.element, self.parent, self.index, self._pattern = element, parent, None, None
        get = element.GetCachedPropertyValue
        self.ControlTypeName = auto.ControlTypeNames.get(get(auto.PropertyId.ControlTypeProperty), "")
        self.ClassName = get(auto.PropertyId.ClassNameProperty) or ""
        self.AutomationId = get(auto.PropertyId.AutomationIdProperty) or ""
        self.Name = get(auto.PropertyId.NameProperty) or ""
        value = get(auto.PropertyId.ValueValueProperty)
        self.Value = value if isinstance(value, str) else ""
        rect = get(auto.PropertyId.BoundingRectangleProperty)
        try:  # SAFEARRAY of left, top, width, height
            self.rect = (rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])
        except (TypeError, IndexError):
            self.rect = (0, 0, 0, 0)

    def value_pattern(self):
        """The live ValuePattern (cached on the node: ~1 ms per read instead of a re-find)."""
        if self._pattern is None:
            self._pattern = auto.Control.CreateControlFromElement(self.element).GetValuePattern()
        return self._pattern


def _levels(hwnd: int, max_depth: int):
    """Yield _Nodes of Viber's UIA tree breadth-first (parents before children), fetching two
    levels (children + their cached children) with their properties per call: each call is a
    round trip to Viber's GUI thread, which costs about a frame while Qt animates. Must run on
    the UIA thread. Depth-limited because Qt's tree can loop back on itself while the Sponsored
    ad shows."""
    if not _uia_cache:
        ua = _AutomationClient.instance().IUIAutomation
        request = ua.CreateCacheRequest()
        for prop in _UIA_PROPS:
            request.AddProperty(prop)
        request.TreeFilter = ua.RawViewCondition  # the same view uiautomation's walker uses
        request.TreeScope = _SCOPE_ELEMENT_CHILDREN
        _uia_cache.update(ua=ua, request=request, condition=ua.CreateTrueCondition())
    ua, request, condition = _uia_cache["ua"], _uia_cache["request"], _uia_cache["condition"]
    level, depth = [(ua.ElementFromHandle(hwnd), None)], 0
    while level and depth < max_depth:
        nxt = []
        for element, parent in level:
            found = element.FindAllBuildCache(_SCOPE_CHILDREN, condition, request)
            for i in range(found.Length):
                node = _Node(found.GetElement(i), parent)
                yield node
                if depth + 1 >= max_depth:
                    continue
                try:
                    kids = node.element.GetCachedChildren()
                    count = kids.Length
                except (ValueError, AttributeError, OSError):  # a leaf: NULL cached children
                    continue
                for j in range(count):
                    kid = _Node(kids.GetElement(j), node)
                    yield kid
                    nxt.append((kid.element, kid))
        level, depth = nxt, depth + 2


def _find_desc(hwnd: int, predicate, max_depth: int = 6) -> _Node | None:
    """First descendant (breadth-first) satisfying `predicate`, or None. UIA thread only."""
    return next((n for n in _levels(hwnd, max_depth) if predicate(n)), None)


_QML_SUFFIX = re.compile(r"(_QMLTYPE_\d+|_QML_\d+)+$")
_KEYS = {
    "escape": win32con.VK_ESCAPE, "esc": win32con.VK_ESCAPE, "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN, "backspace": win32con.VK_BACK, "delete": win32con.VK_DELETE,
    "tab": win32con.VK_TAB, "space": win32con.VK_SPACE, "up": win32con.VK_UP,
    "down": win32con.VK_DOWN, "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
    "pageup": win32con.VK_PRIOR, "pagedown": win32con.VK_NEXT, "home": win32con.VK_HOME,
    "end": win32con.VK_END,
}
_BUTTONS = {
    "left": (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK,
             win32con.MK_LBUTTON),
    "right": (win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.WM_RBUTTONDBLCLK,
              win32con.MK_RBUTTON),
    "middle": (win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.WM_MBUTTONDBLCLK,
               win32con.MK_MBUTTON),
}


def _short_class(cls: str) -> str:
    """'RemixImageButton_QMLTYPE_475' -> 'RemixImageButton' (the numbers change between runs)."""
    return _QML_SUFFIX.sub("", cls)


def _restore_foreground(prev: int) -> None:
    """Give the foreground back to `prev` if Viber took it. Attaching to the current foreground
    thread's input lets SetForegroundWindow through Windows' focus-stealing lock."""
    cur = win32gui.GetForegroundWindow()
    if not prev or cur == prev or not win32gui.IsWindow(prev):
        return
    user32 = ctypes.windll.user32
    tid = win32process.GetWindowThreadProcessId(cur)[0]
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, tid, True)
    try:
        user32.SetForegroundWindow(prev)
    finally:
        user32.AttachThreadInput(me, tid, False)


def _uia_write_keeping_focus(fn) -> None:
    """Run a UIA write (which makes Qt activate Viber) while continuously handing the
    foreground back to the user's window. Long values take Qt seconds to apply, so the
    focus is guarded for the whole write, not just afterwards."""
    prev = win32gui.GetForegroundWindow()
    job = _uia_pool.submit(fn)
    settle_until = None
    while True:
        if win32gui.GetForegroundWindow() != prev:
            _restore_foreground(prev)
        if job.done():
            settle_until = settle_until or time.time() + 0.6  # late activation after the write
            if time.time() >= settle_until:
                break
        time.sleep(0.02)
    job.result(timeout=0)  # re-raise errors from the write


def _is_compose(c) -> bool:
    return c.ControlTypeName == "EditControl" and c.ClassName.startswith("QQuickTextEdit")


def _is_search(c) -> bool:
    return c.ControlTypeName == "EditControl" and c.ClassName.startswith("TextFieldItem")


def _is_send(c) -> bool:
    return c.AutomationId.endswith("SendToolbarButton")


class WindowsBackend(Backend):
    name = "windows"
    input_confirmed = True  # _enter/delete wait on the box's UIA value
    panel_shifts_send = False  # the send button is found by identity, so no panel check

    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._pids_at = 0.0
        self._staged: str | None = None   # text "on the clipboard" (never the real clipboard)
        self._target = _is_compose        # element that type/paste/delete act on
        self._target_node: _Node | None = None  # that element, once located (value reads)

    # --- app / window ----------------------------------------------------------------
    def _viber_pids(self) -> set[int]:
        if time.time() - self._pids_at > 3.0:
            self._pids = {
                p.pid for p in psutil.process_iter(["name"])
                if (p.info["name"] or "").lower() == _VIBER_EXE
            }
            self._pids_at = time.time()
        return self._pids

    def app_running(self) -> bool:
        self._pids_at = 0.0
        return bool(self._viber_pids())

    def _find_hwnd(self) -> int | None:
        pids = self._viber_pids()
        found: list[int] = []

        def cb(hwnd, _):
            if (win32process.GetWindowThreadProcessId(hwnd)[1] in pids
                    and not win32gui.GetParent(hwnd) and _is_main_window(hwnd)):
                found.append(hwnd)
            return True

        win32gui.EnumWindows(cb, None)
        return found[0] if found else None

    def _hwnd(self) -> int:
        hwnd = self._find_hwnd()
        if hwnd is None:
            self._pids_at = 0.0
            hwnd = self._find_hwnd()
        if hwnd is None:
            raise OCRError("Viber window not found. Make sure Viber is running and signed in "
                           "(open it from the tray once if it is hidden).")
        return hwnd

    def focus(self) -> None:
        """Make Viber renderable WITHOUT activating it: un-minimize/show with SW_SHOWNOACTIVATE."""
        hwnd = self._hwnd()
        if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(0.5)

    def open_url(self, url: str) -> None:
        # The viber:// handler makes Viber navigate; Windows may raise its window. Hand the
        # foreground back to whatever the user was working in.
        prev = win32gui.GetForegroundWindow()
        self._target_node = None
        os.startfile(wl.normalize_viber_url(url))

        def guard():  # off the caller's thread: it can start polling for the chat right away
            for _ in range(10):
                time.sleep(0.3)
                if win32gui.GetForegroundWindow() != prev:
                    break
            _restore_foreground(prev)
        threading.Thread(target=guard, name="viber-fg-guard", daemon=True).start()

    # --- perception ------------------------------------------------------------------
    def grab(self):
        """Render the Viber window off-screen. Returns (PIL image, (l, t, r, b) screen rect)."""
        self.focus()
        hwnd = self._hwnd()
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        try:
            bmp.CreateCompatibleBitmap(src, w, h)
            mem.SelectObject(bmp)
            if not ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), _PW_RENDERFULLCONTENT):
                raise OCRError("Failed to capture the Viber window (PrintWindow).")
            info = bmp.GetInfo()
            img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                   bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)
        return img, (l, t, r, b)

    def wait_still(self, timeout: float = 2.0, interval: float = 0.06) -> float:
        """Block until two captures `interval` apart are identical (a sliding panel or a popup
        has finished animating), or `timeout`. UIA calls made while Qt animates each wait about
        a frame, so a tree walk then takes >1 s instead of ~50 ms: settle first, walk once.
        Returns the seconds waited."""
        start, last = time.monotonic(), None
        time.sleep(interval)  # let the posted click reach Qt before the first frame
        while True:
            img, _ = self.grab()
            digest = hashlib.blake2b(img.tobytes(), digest_size=8).digest()
            if digest == last or time.monotonic() - start >= timeout:
                return time.monotonic() - start
            last = digest
            time.sleep(interval)

    def observe(self, region=None) -> list[dict]:
        img, _ = self.grab()
        full_w, full_h = img.size
        scale = 2 if full_w < 1400 else 1  # Windows OCR is more accurate on larger text
        box = wl.region_box(region, full_w, full_h)
        if box is not None:
            img = img.crop(box)  # OCR cost scales with the area: the sidebar is ~1/5 of a read
        if scale != 1:
            img = img.resize((img.width * scale, img.height * scale))
        try:
            lines = _ocr_pool.submit(_ocr_image, img).result(timeout=40)
        except OCRError:
            raise
        except ImportError as exc:
            raise OCRError(f"Windows OCR packages missing ({exc}). Run `uv sync`.") from exc
        except Exception as exc:
            raise OCRError(f"Windows OCR failed: {exc!r}") from exc
        segs = [s for words in lines for s in wl.split_line(words)]
        obs = wl.to_observations(segs, img.width, img.height)
        if box is not None:
            obs = wl.offset_observations(obs, box, full_w, full_h)
        return obs

    # --- UIA element access (locate + read only; UIA writes make Qt activate the window) --
    def _locate(self, predicate, what: str) -> _Node:
        hwnd = self._hwnd()
        node = _on_uia(_find_desc, hwnd, predicate)
        if node is None and self._dismiss_ad():
            node = _on_uia(_find_desc, hwnd, predicate)
        if node is None:
            raise OCRError(f"Viber's {what} was not found (is a chat open?).")
        return node

    def _rect(self, predicate, what: str) -> tuple[int, int, int, int]:
        return self._locate(predicate, what).rect

    def _dismiss_ad(self) -> bool:
        """Close the "Sponsored" ad if it's showing and hiding the chat pane from UIA (see
        ad_close_point). True if it was closed."""
        els = self._elements()
        if any(e["class"] == "SplitView" for e in els):
            return False
        point = wl.ad_close_point(els)
        if point is None:
            return False
        self.click_at(*point)
        time.sleep(1.0)
        self._dismiss_promo(self._elements())  # closing the ad pops the Viber Plus upsell
        return True

    def _value(self) -> str:
        """The target field's text. Reads the element located by the last click_compose /
        click_search (~1 ms); re-finds it when it's gone (Qt rebuilt the page) or unknown."""
        hwnd = self._hwnd()

        def run():
            node = self._target_node
            if node is not None:
                try:
                    pattern = node.value_pattern()
                    if pattern is not None:
                        return pattern.Value
                except Exception:  # noqa: BLE001 — element gone: fall through to a re-find
                    pass
            node = _find_desc(hwnd, self._target)
            self._target_node = node
            pattern = node.value_pattern() if node is not None else None
            return pattern.Value if pattern is not None else ""
        return _on_uia(run)

    def _set_value(self, text: str, what: str = "text box") -> None:
        """UIA SetValue on the target field (makes Qt activate Viber — foreground is guarded)."""
        hwnd = self._hwnd()

        def run():
            node = self._target_node or _find_desc(hwnd, self._target)
            pattern = node.value_pattern() if node is not None else None
            if pattern is None:
                raise OCRError(f"Viber's {what} was not found (is a chat open?).")
            pattern.SetValue(text)
        _uia_write_keeping_focus(run)

    # --- posted input: goes to Viber's window queue, never to the cursor/keyboard ------
    def _post_click(self, x: float, y: float) -> None:
        hwnd = self._hwnd()
        cx, cy = win32gui.ScreenToClient(hwnd, (int(x), int(y)))
        lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def _click_element(self, predicate, what: str) -> _Node:
        node = self._locate(predicate, what)
        l, t, r, b = node.rect
        self._post_click((l + r) / 2, (t + b) / 2)
        return node

    def _post_key(self, vk: int) -> None:
        hwnd = self._hwnd()
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 1)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, 1 | (3 << 30))

    def _wait_value(self, expected: str, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._value() == expected:
                return True
            time.sleep(0.05)  # a cached value read costs ~1 ms, so poll often
        return False

    def _enter(self, text: str) -> None:
        """Type `text` into the target (already cleared). Posted WM_CHAR first; Qt drops
        non-BMP characters (most emoji) that way and Enter could send, so anything posted
        typing can't reproduce falls back to a UIA SetValue + handing the foreground back."""
        hwnd = self._hwnd()
        if "\n" not in text and "\r" not in text:
            for ch in text:
                if ord(ch) <= 0xFFFF:
                    win32gui.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 1)
            if self._wait_value(text, timeout=1.0 + len(text) / 200):
                return
        self._set_value(text)
        if not self._wait_value(text, timeout=8.0):
            raise OCRError("Could not enter the text into Viber.")

    # --- "clipboard" (staged text only; the real clipboard is never touched) ----------
    def clipboard_get(self) -> str | None:
        return None  # nothing to restore

    def clipboard_set(self, text: str) -> None:
        self._staged = text

    # --- keyboard-equivalents --------------------------------------------------------
    def type_text(self, text: str) -> None:
        self._enter(text)

    def select_all(self) -> None:
        pass  # delete() clears the whole box

    def delete(self) -> None:
        current = self._value()
        if not current:
            return
        # End only reaches the end of the caret's line, so Backspace can't clear multi-line
        # text; single-line text is backspaced (no focus change) and checked, with a UIA
        # clear as the fallback.
        single_line = not any(c in current for c in ("\n", "\r", chr(0x2029)))
        if single_line:
            self._post_key(win32con.VK_END)
            for _ in range(len(current) + 2):
                self._post_key(win32con.VK_BACK)
            if self._wait_value(""):
                return
        self._set_value("")
        if not self._wait_value("", timeout=5.0):
            raise OCRError("Could not clear the existing text in Viber.")

    def paste(self) -> None:
        if self._staged is None:
            raise OCRError("Nothing staged to paste.")
        self._enter(self._staged)

    # --- element actions -------------------------------------------------------------
    def click_compose(self) -> None:
        self._target = _is_compose
        self._target_node = self._click_element(_is_compose, "message box")
        time.sleep(0.25)  # let Qt move focus before keys arrive

    def click_search(self) -> None:
        self._target = _is_search
        self._target_node = self._click_element(_is_search, "search box")
        time.sleep(0.25)

    def click_send(self) -> None:
        self._click_element(_is_send, "send button")

    def click_result(self, norm_y: float) -> None:
        """Click the left-column row whose text top is at normalized window height `norm_y`."""
        self._target_node = None  # opening a chat rebuilds the page
        l, t, r, b = win32gui.GetWindowRect(self._hwnd())
        self._post_click(l + (r - l) * 0.15, t + (b - t) * norm_y + 8)

    def click_panel_close(self) -> None:
        pass  # side panels don't matter: the send button is found by identity, not position

    def scroll(self, area: str, notches: int) -> None:
        """Post WM_MOUSEWHEEL over the chat list or the conversation (cursor stays put)."""
        hwnd = self._hwnd()
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        anchor, fallback_x = (_is_search, 0.15) if area == "chats" else (_is_compose, 0.65)
        try:
            el_l, _, el_r, _ = self._rect(anchor, "")
            x = (el_l + el_r) / 2
        except OCRError:
            x = l + (r - l) * fallback_x
        y = t + (b - t) * 0.55
        lp = win32api.MAKELONG(int(x) & 0xFFFF, int(y) & 0xFFFF)
        step = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            win32gui.PostMessage(hwnd, win32con.WM_MOUSEWHEEL, ((120 * step) & 0xFFFF) << 16, lp)
            time.sleep(_WHEEL_GAP)

    # --- computer-use primitives (coordinates = pixels in the screenshot image) --------
    def screenshot(self):
        """The Viber window as a PIL image (rendered off-screen; Viber may be covered)."""
        return self.grab()[0]

    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        l, t, _, _ = win32gui.GetWindowRect(self._hwnd())
        return l + x, t + y

    def click_at(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        if button not in _BUTTONS:
            raise ValueError("button must be 'left', 'right' or 'middle'")
        down, up, dbl, mk = _BUTTONS[button]
        hwnd = self._hwnd()
        self._target_node = None  # a click anywhere may navigate
        sx, sy = self._to_screen(x, y)
        self._last_click = (sx, sy)
        cx, cy = win32gui.ScreenToClient(hwnd, (int(sx), int(sy)))
        lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        win32gui.PostMessage(hwnd, down, mk, lp)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, up, 0, lp)
        if double:
            time.sleep(0.05)
            win32gui.PostMessage(hwnd, dbl, mk, lp)
            time.sleep(0.05)
            win32gui.PostMessage(hwnd, up, 0, lp)

    def press_key(self, key: str, repeat: int = 1) -> None:
        vk = _KEYS.get(key.strip().lower())
        if vk is None:
            raise ValueError(f"Unknown key {key!r}. Supported: {', '.join(sorted(_KEYS))}. "
                             "Modifier shortcuts (Ctrl+...) can't be sent in the background.")
        self._target_node = None  # Escape/Enter may close or change the page
        for _ in range(max(1, min(int(repeat), 100))):
            self._post_key(vk)
            time.sleep(0.03)

    def type_into_focus(self, text: str) -> None:
        """Type into whatever field has Viber's (internal) focus — click it first. Characters
        posting can't produce (non-BMP emoji, newlines) go through UIA on the clicked field."""
        hwnd = self._hwnd()
        if "\n" not in text and "\r" not in text and all(ord(c) <= 0xFFFF for c in text):
            for ch in text:
                win32gui.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 1)
            time.sleep(0.2 + len(text) / 400)
            return
        point = getattr(self, "_last_click", None)
        if point is None:
            raise OCRError("Click the text field first, then type (needed for emoji/newlines).")
        def run():
            el = auto.ControlFromPoint(int(point[0]), int(point[1]))
            pattern = el.GetPattern(auto.PatternId.ValuePattern) if el else None
            if pattern is None:
                raise OCRError("The last-clicked spot isn't a text field; click the field first.")
            pattern.SetValue(pattern.Value + text)
        _uia_write_keeping_focus(run)

    def scroll_at(self, x: float, y: float, notches: int) -> None:
        sx, sy = self._to_screen(x, y)
        lp = win32api.MAKELONG(int(sx) & 0xFFFF, int(sy) & 0xFFFF)
        step = 1 if notches > 0 else -1
        for _ in range(abs(int(notches))):
            win32gui.PostMessage(self._hwnd(), win32con.WM_MOUSEWHEEL,
                                 ((120 * step) & 0xFFFF) << 16, lp)
            time.sleep(_WHEEL_GAP)

    def elements(self) -> list[dict]:
        """Visible UI elements: type, name, short class, text (edits), parent index, and
        rect in screenshot pixels, in tree order (parents before children). Closes the
        "Sponsored" ad first when it's hiding the chat pane."""
        els = self._elements()
        if not any(e["class"] == "SplitView" for e in els) and self._dismiss_ad():
            els = self._elements()
        if self._dismiss_promo(els):
            els = self._elements()
        return els

    def _dismiss_promo(self, els: list[dict]) -> bool:
        """Close the Viber Plus pop-up if it's covering the window (see promo_close_point)."""
        point = wl.promo_close_point(els)
        if point is None:
            return False
        self.click_at(*point)
        time.sleep(0.8)
        return True

    def _elements(self) -> list[dict]:
        hwnd = self._hwnd()

        def run():
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            out = []
            for node in _levels(hwnd, max_depth=9):
                nl, nt, nr, nb = node.rect
                if nr <= nl or nb <= nt:
                    continue
                if nr < l or nl > r or nb < t or nt > b:
                    continue  # scrolled out of view
                node.index = len(out)
                out.append({
                    "type": node.ControlTypeName.removesuffix("Control"),
                    "name": node.Name,
                    "class": _short_class(node.ClassName),
                    "text": node.Value if node.ControlTypeName == "EditControl" else "",
                    "parent": node.parent.index if node.parent is not None else None,
                    "rect": [nl - l, nt - t, nr - l, nb - t],
                })
            return out
        return _on_uia(run)

    # --- focus protection for actions where Viber activates itself (e.g. new group) ----
    def foreground(self) -> int:
        return win32gui.GetForegroundWindow()

    def hold_foreground(self, prev: int, seconds: float) -> None:
        """Keep handing the foreground back to `prev` for `seconds`."""
        end = time.time() + seconds
        while time.time() < end:
            if win32gui.GetForegroundWindow() != prev:
                _restore_foreground(prev)
            time.sleep(0.02)

    # --- Viber's "Send a file" dialog (a standard Win32 file dialog) -------------------
    def _file_dialog(self) -> int | None:
        pids = self._viber_pids()
        found: list[int] = []

        def cb(h, _):
            if (win32gui.GetClassName(h) == "#32770" and win32gui.IsWindowVisible(h)
                    and win32process.GetWindowThreadProcessId(h)[1] in pids):
                found.append(h)
            return True
        win32gui.EnumWindows(cb, None)
        return found[0] if found else None

    def file_dialog_open(self) -> bool:
        return self._file_dialog() is not None

    def file_dialog_choose(self, path: str) -> bool:
        """Put `path` in the dialog's file-name box and press Open — window messages only.
        Returns whether the dialog closed (file accepted). When it closes Windows re-activates
        Viber, so the foreground is handed back to the user's window."""
        prev = win32gui.GetForegroundWindow()
        dlg = self._file_dialog()
        if dlg is None:
            raise OCRError("Viber's file picker isn't open.")
        children: list[int] = []
        win32gui.EnumChildWindows(dlg, lambda h, _: children.append(h) or True, None)

        def is_name_box(h: int) -> bool:
            parent = win32gui.GetParent(h)
            return (win32gui.GetClassName(h) == "Edit"
                    and win32gui.GetClassName(parent) == "ComboBox"
                    and win32gui.GetClassName(win32gui.GetParent(parent)) == "ComboBoxEx32")
        name_box = next((h for h in children if is_name_box(h)), None)
        if name_box is None:
            raise OCRError("Couldn't find the file-name box in Viber's file picker.")
        win32gui.SendMessage(name_box, win32con.WM_SETTEXT, 0, path)
        win32gui.PostMessage(dlg, win32con.WM_COMMAND, win32con.IDOK, 0)
        try:
            for _ in range(40):
                time.sleep(0.1)
                if not win32gui.IsWindow(dlg) or not win32gui.IsWindowVisible(dlg):
                    return True
            return False
        finally:
            _restore_foreground(prev)
            time.sleep(0.2)
            _restore_foreground(prev)

    def file_dialog_cancel(self) -> None:
        dlg = self._file_dialog()
        if dlg is not None:
            win32gui.PostMessage(dlg, win32con.WM_COMMAND, win32con.IDCANCEL, 0)

    def debug_info(self) -> dict:
        hwnd = self._hwnd()

        def found():
            return {name: _find_desc(hwnd, pred) is not None for name, pred in
                    (("search_box", _is_search), ("compose_box", _is_compose),
                     ("send_button", _is_send))}
        return {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd),
                "rect": win32gui.GetWindowRect(hwnd), "uia": _on_uia(found)}

