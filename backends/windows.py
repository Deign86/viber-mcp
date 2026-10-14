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
import io
import os
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

from . import _winlogic as wl
from .base import Backend, OCRError

_VIBER_EXE = "viber.exe"
_PW_RENDERFULLCONTENT = 2

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
except Exception:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

# ---- OCR (Windows.Media.Ocr) — runs in its own thread/event loop ----------------------
_ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viber-ocr")
_engine = None


async def _recognize(png: bytes):
    """Return (list of lines, each a list of (text, x, y, w, h) words in image px)."""
    global _engine
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    if _engine is None:
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

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await _engine.recognize_async(bitmap)
    return [[(w.text, w.bounding_rect.x, w.bounding_rect.y, w.bounding_rect.width,
              w.bounding_rect.height) for w in line.words] for line in result.lines]


def _ocr_png(png: bytes):
    return asyncio.run(_recognize(png))


# ---- UI Automation — COM objects stay on one initialized thread -----------------------
_uia_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viber-uia",
                               initializer=auto.InitializeUIAutomationInCurrentThread)


def _on_uia(fn, *args):
    return _uia_pool.submit(fn, *args).result(timeout=30)


def _is_main_window(hwnd: int) -> bool:
    """The "Rakuten Viber" UI, not one of Qt's hidden helper windows (theme observer, tray, ...)."""
    return "viber" in win32gui.GetWindowText(hwnd).lower()


def _find_desc(root, predicate, max_depth: int = 6):
    """First descendant (breadth-first) satisfying `predicate`, or None."""
    level = [root]
    for _ in range(max_depth):
        nxt = []
        for ctrl in level:
            for child in ctrl.GetChildren():
                if predicate(child):
                    return child
                nxt.append(child)
        level = nxt
    return None


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


def _is_compose(c) -> bool:
    return c.ControlTypeName == "EditControl" and c.ClassName.startswith("QQuickTextEdit")


def _is_search(c) -> bool:
    return c.ControlTypeName == "EditControl" and c.ClassName.startswith("TextFieldItem")


def _is_send(c) -> bool:
    return c.AutomationId.endswith("SendToolbarButton")


class WindowsBackend(Backend):
    name = "windows"

    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._pids_at = 0.0
        self._staged: str | None = None   # text "on the clipboard" (never the real clipboard)
        self._target = _is_compose        # element that type/paste/delete act on

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
        os.startfile(wl.normalize_viber_url(url))
        for _ in range(10):
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() != prev:
                break
        _restore_foreground(prev)

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

    def observe(self) -> list[dict]:
        img, _ = self.grab()
        if img.width < 1400:  # Windows OCR is more accurate on larger text
            img = img.resize((img.width * 2, img.height * 2))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        try:
            lines = _ocr_pool.submit(_ocr_png, buf.getvalue()).result(timeout=40)
        except OCRError:
            raise
        except ImportError as exc:
            raise OCRError(f"Windows OCR packages missing ({exc}). Run `uv sync`.") from exc
        except Exception as exc:
            raise OCRError(f"Windows OCR failed: {exc!r}") from exc
        segs = [s for words in lines for s in wl.split_line(words)]
        return wl.to_observations(segs, img.width, img.height)

    # --- UIA element access (locate + read only; UIA writes make Qt activate the window) --
    def _rect(self, predicate, what: str) -> tuple[int, int, int, int]:
        def run():
            el = _find_desc(auto.ControlFromHandle(self._hwnd()), predicate)
            if el is None:
                raise OCRError(f"Viber's {what} was not found (is a chat open?).")
            r = el.BoundingRectangle
            return r.left, r.top, r.right, r.bottom
        return _on_uia(run)

    def _value(self) -> str:
        def run():
            el = _find_desc(auto.ControlFromHandle(self._hwnd()), self._target)
            return el.GetValuePattern().Value if el else ""
        return _on_uia(run)

    # --- posted input: goes to Viber's window queue, never to the cursor/keyboard ------
    def _post_click(self, x: float, y: float) -> None:
        hwnd = self._hwnd()
        cx, cy = win32gui.ScreenToClient(hwnd, (int(x), int(y)))
        lp = win32api.MAKELONG(cx & 0xFFFF, cy & 0xFFFF)
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        time.sleep(0.05)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp)

    def _click_element(self, predicate, what: str) -> None:
        l, t, r, b = self._rect(predicate, what)
        self._post_click((l + r) / 2, (t + b) / 2)

    def _post_key(self, vk: int) -> None:
        hwnd = self._hwnd()
        win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 1)
        win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, 1 | (3 << 30))

    def _wait_value(self, expected: str, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._value() == expected:
                return True
            time.sleep(0.15)
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
        prev = win32gui.GetForegroundWindow()

        def run():
            el = _find_desc(auto.ControlFromHandle(hwnd), self._target)
            if el is None:
                raise OCRError("Viber's text box was not found (is a chat open?).")
            el.GetValuePattern().SetValue(text)
        try:
            _on_uia(run)
        finally:
            # Qt activates the window asynchronously; catch it as soon as it happens.
            for _ in range(20):
                time.sleep(0.05)
                if win32gui.GetForegroundWindow() != prev:
                    break
            _restore_foreground(prev)
            time.sleep(0.3)
            _restore_foreground(prev)
        if not self._wait_value(text):
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
        self._post_key(win32con.VK_END)
        for _ in range(len(current) + 2):
            self._post_key(win32con.VK_BACK)
        if not self._wait_value(""):
            raise OCRError("Could not clear the existing text in Viber.")

    def paste(self) -> None:
        if self._staged is None:
            raise OCRError("Nothing staged to paste.")
        self._enter(self._staged)

    # --- element actions -------------------------------------------------------------
    def click_compose(self) -> None:
        self._target = _is_compose
        self._click_element(_is_compose, "message box")

    def click_search(self) -> None:
        self._target = _is_search
        self._click_element(_is_search, "search box")

    def click_send(self) -> None:
        self._click_element(_is_send, "send button")

    def click_result(self, norm_y: float) -> None:
        """Click the left-column row whose text top is at normalized window height `norm_y`."""
        l, t, r, b = win32gui.GetWindowRect(self._hwnd())
        self._post_click(l + (r - l) * 0.15, t + (b - t) * norm_y + 8)

    def click_panel_close(self) -> None:
        pass  # side panels don't matter: the send button is found by identity, not position

    def debug_info(self) -> dict:
        hwnd = self._hwnd()

        def found():
            root = auto.ControlFromHandle(hwnd)
            return {name: _find_desc(root, pred) is not None for name, pred in
                    (("search_box", _is_search), ("compose_box", _is_compose),
                     ("send_button", _is_send))}
        return {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd),
                "rect": win32gui.GetWindowRect(hwnd), "uia": _on_uia(found)}

