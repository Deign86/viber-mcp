"""macOS backend: Quartz (windows + synthetic input), Apple Vision (OCR), AppKit (clipboard).

All macOS- and Viber-macOS-layout-specific code lives here. Key macOS realities baked in:

- Synthetic input MUST come from a real HID event source (`kCGEventSourceStateHIDSystemState`);
  Viber's Qt widgets silently drop plain CGEvents / System Events keystrokes.
- Typed characters must carry zeroed modifier flags, or a lingering Cmd (from Cmd+A) turns the
  next character into a shortcut (e.g. "M" → Cmd+M = minimize).
- The sidebar, search box, send button, and panel-close ✕ are at FIXED PIXEL offsets from the
  window edges (their pixel widths don't scale), so they're computed from bounds in px, not
  fractions. Only the compose box uses a fraction (the input is wide and forgiving).
"""

from __future__ import annotations

import subprocess
import tempfile
import time

import Quartz  # type: ignore
import Vision  # type: ignore
from AppKit import NSPasteboard, NSStringPboardType  # type: ignore
from Foundation import NSURL  # type: ignore

from .base import Backend, OCRError

_BUNDLE_ID = "com.viber.osx"

# macOS virtual key codes.
_KEY_A = 0
_KEY_V = 9
_KEY_DELETE = 51

# Viber UI element locations (measured on v28.2.0). Fractions of the window unless noted "px".
_COMPOSE_FRAC = (0.45, 0.96)
_RESULT_X_FRAC = 0.14
_SEND_OFFSET_PX = (28, 30)       # in from bottom-right corner
_PANEL_CLOSE_OFFSET_PX = (25, 90)  # in from top-right corner (x in, y down)
_SEARCH_OFFSET_PX = (175, 52)    # from top-left (sidebar has fixed px width)


class MacBackend(Backend):
    name = "macos"

    def __init__(self) -> None:
        # A real HID event source so Qt accepts our synthetic events.
        self._src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)

    # --- app / process ---------------------------------------------------------------
    def app_running(self) -> bool:
        return subprocess.run(["pgrep", "-x", "Viber"], capture_output=True).returncode == 0

    def focus(self) -> None:
        subprocess.run(["open", "-b", _BUNDLE_ID], check=False)
        time.sleep(0.6)

    def open_url(self, url: str) -> None:
        subprocess.run(["open", url], check=True)

    # --- window discovery ------------------------------------------------------------
    def _window(self) -> dict:
        options = (
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        )
        windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        # Owner is "Rakuten Viber"; skip the tiny menu-bar window (>200px filter).
        viber = [
            w
            for w in windows
            if "viber" in (w.get("kCGWindowOwnerName") or "").lower()
            and w.get("kCGWindowNumber")
            and float(w.get("kCGWindowBounds", {}).get("Width", 0)) > 200
            and float(w.get("kCGWindowBounds", {}).get("Height", 0)) > 200
        ]
        if not viber:
            raise OCRError(
                "No visible Viber window found. Make sure Viber is running, unminimized, and a "
                "chat is open in the foreground (and not in macOS fullscreen)."
            )
        return max(viber, key=lambda w: (
            float(w["kCGWindowBounds"]["Width"]) * float(w["kCGWindowBounds"]["Height"])
        ))

    def _bounds(self) -> dict:
        return self._window()["kCGWindowBounds"]

    # --- perception ------------------------------------------------------------------
    def observe(self, region=None) -> list[dict]:  # region: hint only, the whole window is read
        window = self._window()
        window_id = int(window["kCGWindowNumber"])
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            self._capture(window_id, tmp.name)
            return self._ocr(tmp.name)

    def _capture(self, window_id: int, out_path: str) -> None:
        # -x no sound, -o omit shadow, -l capture the window by id.
        proc = subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(window_id), out_path],
            capture_output=True, text=True, timeout=15,
        )
        import os
        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise OCRError(
                "Failed to capture the Viber window. Grant Screen Recording permission to the "
                f"terminal/host app in System Settings. Detail: {proc.stderr.strip()}"
            )

    def _ocr(self, image_path: str) -> list[dict]:
        url = NSURL.fileURLWithPath_(image_path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        results: list[dict] = []

        def _completion(request, error):
            for obs in request.results() or []:
                top = obs.topCandidates_(1)
                if not top:
                    continue
                box = obs.boundingBox()  # origin bottom-left
                results.append({
                    "text": top[0].string(),
                    "x": box.origin.x,
                    "y": 1.0 - (box.origin.y + box.size.height),  # flip to top-down
                })

        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_completion)
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        success, error = handler.performRequests_error_([request], None)
        if not success:
            raise OCRError(f"Vision OCR failed: {error}")
        results.sort(key=lambda o: o["y"])
        return results

    # --- clipboard -------------------------------------------------------------------
    def clipboard_get(self) -> str | None:
        return NSPasteboard.generalPasteboard().stringForType_(NSStringPboardType)

    def clipboard_set(self, text: str) -> None:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSStringPboardType)

    # --- low-level HID input ---------------------------------------------------------
    def _post(self, event) -> None:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _click_xy(self, x: float, y: float) -> None:
        down = Quartz.CGEventCreateMouseEvent(self._src, Quartz.kCGEventLeftMouseDown, (x, y), Quartz.kCGMouseButtonLeft)
        up = Quartz.CGEventCreateMouseEvent(self._src, Quartz.kCGEventLeftMouseUp, (x, y), Quartz.kCGMouseButtonLeft)
        self._post(down)
        time.sleep(0.06)
        self._post(up)

    def _key(self, code: int, flags: int = 0) -> None:
        down = Quartz.CGEventCreateKeyboardEvent(self._src, code, True)
        up = Quartz.CGEventCreateKeyboardEvent(self._src, code, False)
        if flags:
            Quartz.CGEventSetFlags(down, flags)
            Quartz.CGEventSetFlags(up, flags)
        self._post(down)
        time.sleep(0.03)
        self._post(up)

    # --- keyboard (semantic) ---------------------------------------------------------
    def type_text(self, text: str) -> None:
        for ch in text:
            for down in (True, False):
                e = Quartz.CGEventCreateKeyboardEvent(self._src, 0, down)
                Quartz.CGEventSetFlags(e, 0)  # no modifiers — plain character
                Quartz.CGEventKeyboardSetUnicodeString(e, len(ch), ch)
                self._post(e)
                time.sleep(0.012)

    def select_all(self) -> None:
        self._key(_KEY_A, Quartz.kCGEventFlagMaskCommand)

    def delete(self) -> None:
        self._key(_KEY_DELETE)

    def paste(self) -> None:
        self._key(_KEY_V, Quartz.kCGEventFlagMaskCommand)

    # --- clicks on Viber UI elements -------------------------------------------------
    def click_compose(self) -> None:
        b = self._bounds()
        self._click_xy(b["X"] + b["Width"] * _COMPOSE_FRAC[0], b["Y"] + b["Height"] * _COMPOSE_FRAC[1])

    def click_send(self) -> None:
        b = self._bounds()
        self._click_xy(b["X"] + b["Width"] - _SEND_OFFSET_PX[0], b["Y"] + b["Height"] - _SEND_OFFSET_PX[1])

    def click_search(self) -> None:
        b = self._bounds()
        self._click_xy(b["X"] + _SEARCH_OFFSET_PX[0], b["Y"] + _SEARCH_OFFSET_PX[1])

    def click_result(self, norm_y: float) -> None:
        b = self._bounds()
        self._click_xy(b["X"] + b["Width"] * _RESULT_X_FRAC, b["Y"] + b["Height"] * norm_y)

    def click_panel_close(self) -> None:
        b = self._bounds()
        self._click_xy(b["X"] + b["Width"] - _PANEL_CLOSE_OFFSET_PX[0], b["Y"] + _PANEL_CLOSE_OFFSET_PX[1])
