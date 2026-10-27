"""macOS backend: Accessibility (locate, read, set text, press buttons), Quartz (capture, input),
Apple Vision (OCR).

Like the Windows backend it runs in the background where macOS allows it:

| Step | How | Your Mac |
|------|-----|----------|
| See | In-process window capture (CGWindowListCreateImage) + Vision OCR | Works while Viber is covered (not minimized/closed). |
| Find controls | Qt's accessibility tree via AX; each AXIdentifier is the QML object path, so the classes match the Windows UIA ones (StackView, FeedDelegate, SendToolbarButton, …). | No pixel offsets. |
| Type / send | AXValue on the message/search box, AXPress on buttons. | Cursor, keyboard and front app untouched. |
| Typing | Characters posted to Viber's process (CGEventPostToPid). | Untouched. |
| Clicks with no AX element under them (chat rows, search results, right-click menus), scrolling and named keys (Escape, arrows, Enter) | Real HID events: Viber is brought to the front, then the user's front app and cursor are put back. | Brief hand-off. |

Measured on Viber 29.1 (macOS 15): posted mouse/scroll events are ignored by a background Qt
window, and posted Escape doesn't reach the image viewer, while posted characters do land in the
focused field; AXPress/AXValue work without activating Viber.
Qt also exposes items it isn't drawing (the hidden ChatGPT panel, pages under a side panel), so
`elements()` keeps only those that hit-testing at their visible centre returns.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import threading
import time
from collections import deque

import Quartz  # type: ignore
import Vision  # type: ignore
from ApplicationServices import (  # type: ignore
    AXIsProcessTrusted,
    AXIsProcessTrustedWithOptions,
    AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyElementAtPosition,
    AXUIElementCopyMultipleAttributeValues,
    AXUIElementCreateApplication,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXValueGetType,
    AXValueGetTypeID,
    AXValueGetValue,
    kAXValueAXErrorType,
    kAXValueCGPointType,
    kAXValueCGSizeType,
)
from CoreFoundation import CFEqual, CFGetTypeID, CFHash  # type: ignore
from Foundation import NSMakeRange, NSURL  # type: ignore
from PIL import Image

from . import _maclogic as ml
from . import _winlogic as wl  # pure element helpers (Remix / Viber Plus pop-ups) are shared
from .base import Backend, OCRError

_BUNDLE_ID = "com.viber.osx"
_ATTRS = ["AXRole", "AXIdentifier", "AXTitle", "AXDescription", "AXValue", "AXPosition",
          "AXSize", "AXChildren"]
_MAX_NODES = 4000


def _get(el, attr):
    err, value = AXUIElementCopyAttributeValue(el, attr, None)
    return value if err == 0 else None


def _is_error(value) -> bool:
    return (value is not None and CFGetTypeID(value) == AXValueGetTypeID()
            and AXValueGetType(value) == kAXValueAXErrorType)


def _frame(pos, size):
    if pos is None or size is None or _is_error(pos) or _is_error(size):
        return None
    _, pt = AXValueGetValue(pos, kAXValueCGPointType, None)
    _, sz = AXValueGetValue(size, kAXValueCGSizeType, None)
    return (pt.x, pt.y, sz.width, sz.height)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _actions(el) -> list[str]:
    err, names = AXUIElementCopyActionNames(el, None)
    return list(names or []) if err == 0 else []


class MacBackend(Backend):
    name = "macos"
    input_confirmed = True     # text goes in through AXValue and is read back
    panel_shifts_send = False  # the send button is found by identity, not position

    def __init__(self) -> None:
        # A real HID event source so Qt accepts our synthetic events.
        self._src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        self._pid: int | None = None
        self._target = ml.is_compose   # field type_text/delete/_value act on
        self._target_el = None         # its AX element, kept between calls
        self._focus_el = None          # text field the last click_at focused
        self._staged: str | None = None
        self._trusted = False
        self._asked_ax = self._asked_screen = False
        self._sb_cache: tuple = (None, 0)
        self._handoff = threading.Lock()  # held while real input needs Viber in front

    # --- app / process ---------------------------------------------------------------
    def _viber_pid(self) -> int | None:
        out = subprocess.run(["pgrep", "-x", "Viber"], capture_output=True, text=True)
        pids = [int(p) for p in out.stdout.split()]
        self._pid = pids[0] if pids else None
        return self._pid

    def app_running(self) -> bool:
        return self._viber_pid() is not None

    def _app(self):
        if not self._trusted:
            self._trusted = bool(AXIsProcessTrusted())
        if not self._trusted:
            if not self._asked_ax:  # once per run: shows macOS's prompt and lists the app
                self._asked_ax = True
                AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
            raise OCRError(
                "macOS hasn't allowed the Viber server to control Viber. In System Settings → "
                "Privacy & Security → Accessibility, turn on \"Viber MCP\" (or the app that "
                "started the server: uv, your terminal), then restart Claude.")
        pid = self._pid if self._pid is not None and _alive(self._pid) else self._viber_pid()
        if pid is None:
            raise OCRError("Viber is not running. Launch and sign in to Viber first.")
        return AXUIElementCreateApplication(pid)

    def _onscreen_window(self) -> dict | None:
        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        # Owner is "Rakuten Viber"; skip the menu-bar/status windows (>200px filter).
        viber = [
            w for w in windows
            if "viber" in (w.get("kCGWindowOwnerName") or "").lower()
            and w.get("kCGWindowNumber")
            and float(w.get("kCGWindowBounds", {}).get("Width", 0)) > 200
            and float(w.get("kCGWindowBounds", {}).get("Height", 0)) > 200
        ]
        if not viber:
            return None
        return max(viber, key=lambda w: (
            float(w["kCGWindowBounds"]["Width"]) * float(w["kCGWindowBounds"]["Height"])))

    def focus(self) -> None:
        """Make sure Viber's window is on screen (un-minimize, or reopen it after the red ✕
        closed it) without bringing it to the front when it already is."""
        if self._onscreen_window() is not None:
            return
        try:
            win = _get(self._app(), "AXMainWindow")
            if win is not None and _get(win, "AXMinimized"):
                AXUIElementSetAttributeValue(win, "AXMinimized", False)
        except OCRError:
            pass
        for args in (["open", "-g", "-b", _BUNDLE_ID], ["open", "-b", _BUNDLE_ID]):
            subprocess.run(args, check=False)
            for _ in range(15):
                time.sleep(0.1)
                if self._onscreen_window() is not None:
                    return

    def open_url(self, url: str) -> None:
        """Hand a viber:// link to Viber. Viber activates itself to handle it even with `open -g`,
        so the user's front app is handed back for a few seconds (in a thread: chat polling
        starts at once), as the Windows backend does."""
        self._target_el = None  # opening a chat rebuilds the page
        prev = self.foreground()
        subprocess.run(["open", "-g", url], check=True)
        if prev and prev != self._pid:
            threading.Thread(target=self.hold_foreground, args=(prev, 4.0), daemon=True).start()

    # --- window geometry -------------------------------------------------------------
    def _window(self) -> dict:
        window = self._onscreen_window()
        if window is None:
            self.focus()
            window = self._onscreen_window()
        if window is None:
            raise OCRError(
                "No visible Viber window found. Make sure Viber is running and signed in, in a "
                "normal window (not macOS fullscreen).")
        return window

    def _bounds(self) -> dict:
        return self._window()["kCGWindowBounds"]

    def _wframe(self) -> tuple[float, float, float, float]:
        b = self._bounds()
        return float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"])

    # --- perception ------------------------------------------------------------------
    def _cgimage(self):
        """The window as a CGImage at full resolution (2x on Retina), in-process (~0.3 s);
        `screencapture -l` (twice as slow) if that API is unavailable."""
        window_id = int(self._window()["kCGWindowNumber"])
        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if preflight is not None and not preflight():
            if not self._asked_screen:  # once per run: shows macOS's prompt and lists the app
                self._asked_screen = True
                Quartz.CGRequestScreenCaptureAccess()
            raise OCRError(
                "macOS hasn't allowed the Viber server to see Viber's window. In System Settings → "
                "Privacy & Security → Screen Recording, turn on \"Viber MCP\" (or the app that "
                "started the server), then restart Claude.")
        create = getattr(Quartz, "CGWindowListCreateImage", None)
        if create is not None:
            img = create(Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, window_id,
                         Quartz.kCGWindowImageBoundsIgnoreFraming)
            # While the window is being re-shown the image can come back without pixel data.
            if img is not None and Quartz.CGImageGetWidth(img) > 1 \
                    and Quartz.CGImageGetDataProvider(img) is not None:
                return img
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            # -x no sound, -o omit shadow, -l capture the window by id (works while covered).
            proc = subprocess.run(
                ["screencapture", "-x", "-o", "-l", str(window_id), tmp.name],
                capture_output=True, text=True, timeout=15)
            src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(tmp.name), None)
            img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None) if src is not None else None
        if proc.returncode != 0 or img is None:
            raise OCRError(
                "Failed to capture the Viber window. Grant Screen Recording permission to the "
                f"app running the server in System Settings. Detail: {proc.stderr.strip()}")
        return img

    @staticmethod
    def _pixels(img) -> tuple[bytes, int]:
        """(BGRA bytes, bytes per row) of a CGImage. A capture taken while the window is being
        re-shown can't hand its data over directly, so such an image is redrawn first."""
        provider = Quartz.CGImageGetDataProvider(img)
        data = Quartz.CGDataProviderCopyData(provider) if provider is not None else None
        if data is not None:
            return bytes(data), Quartz.CGImageGetBytesPerRow(img)
        w, h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
        ctx = Quartz.CGBitmapContextCreate(
            None, w, h, 8, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
            Quartz.kCGImageAlphaPremultipliedFirst | Quartz.kCGBitmapByteOrder32Little)
        Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), img)
        redrawn = Quartz.CGBitmapContextCreateImage(ctx)
        data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(redrawn))
        if data is None or not bytes(data).strip(b"\0"):
            # All black: the window is mid-redraw, or macOS withholds the pixels from this
            # process (Screen Recording not in effect for the app that started it).
            raise OCRError("Viber's window came back blank. Try again; if it keeps happening, "
                           "re-allow Screen Recording for the app running the server and restart it.")
        return bytes(data), Quartz.CGImageGetBytesPerRow(redrawn)

    def _pil(self, img) -> Image.Image:
        data, row = self._pixels(img)
        w, h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
        return Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", row, 1).convert("RGB")

    def grab(self) -> Image.Image:
        """The window at full resolution (2x on Retina)."""
        return self._pil(self._cgimage())

    def screenshot(self):
        """The Viber window as a PIL image in points (1 px = 1 pt, also on Retina), the
        coordinate space of elements() and click_at — viber_ui's pixel offsets assume it."""
        img = self.grab()
        _, _, w, h = self._wframe()
        if img.width != round(w):
            img = img.resize((round(w), round(h)))
        return img

    def observe(self, region=None) -> list[dict]:
        img = full = self._cgimage()
        full_w, full_h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
        box = wl.region_box(region, full_w, full_h)
        if box is not None:  # OCR cost scales with the area: the header band is ~1/10 of a read
            l, t, r, b = box
            img = Quartz.CGImageCreateWithImageInRect(img, Quartz.CGRectMake(l, t, r - l, b - t))
        obs = self._ocr(img)
        if box is not None:  # normalized over the crop -> over the whole window
            obs = wl.offset_observations(obs, box, full_w, full_h)
        if region is None or region[0] < 0.2:  # the chat list is in view: add its unread badges
            obs += self._badges(full, obs, region)
        obs.sort(key=lambda o: (o["y"], o["x"]))
        return obs

    def _sidebar_right(self, width_px: int) -> int:
        """Chat list's right edge in capture pixels (where the chat pane starts). Fixed while
        the window keeps its size, so the tree walk is cached."""
        key = (width_px, self._wframe()[2:])
        if self._sb_cache[0] != key:
            pane, _ = self._find(lambda e: e["class"] in ("SplitView", "StackView"), "", required=False)
            if pane is None:
                return int(width_px * 0.28)
            self._sb_cache = (key, int(pane["rect"][0] * width_px / self._wframe()[2]))
        return self._sb_cache[1]

    def _badges(self, cgimage, obs: list[dict], region) -> list[dict]:
        """The chat list's unread badges as observations (Vision drops a lone white digit in a
        purple circle): found by colour, turned into black digits on white, read in one strip."""
        full_w, full_h = Quartz.CGImageGetWidth(cgimage), Quartz.CGImageGetHeight(cgimage)
        right = self._sidebar_right(full_w)
        top = int(full_h * (region[1] if region else 0))
        bottom = int(full_h * (region[3] if region else 1))
        x0 = int(right * 0.6)
        crop = Quartz.CGImageCreateWithImageInRect(cgimage, Quartz.CGRectMake(x0, top, right - x0, bottom - top))
        if crop is None:
            return []
        col = self._pil(crop)
        boxes = ml.badge_boxes(col)
        if not boxes:
            return []
        digits = ["" for _ in boxes]
        # Vision is deterministic per image but picky about lone digits: a setting that reads
        # them all on one screen can drop one on another, so try a few until each badge is read.
        for scale, gap in ((2, 16), (3, 14), (2, 10)):
            if all(digits):
                break
            for i, text in enumerate(self._read_badges(col, boxes, scale, gap)):
                digits[i] = digits[i] or text
        out = []
        for (l, t, r, b), text in zip(boxes, digits):
            if not text:
                continue
            nx, ny = (x0 + l) / full_w, (top + t) / full_h
            if any(o["text"].strip() == text and abs(o["y"] - ny) < 0.02 and abs(o["x"] - nx) < 0.05
                   for o in obs):
                continue  # Vision read this one already
            out.append({"text": text, "x": nx, "y": ny, "w": (r - l) / full_w, "h": (b - t) / full_h})
        return out

    def _read_badges(self, col: Image.Image, boxes, scale: int, gap: int) -> list[str]:
        """Badges laid out like one line of text (white digits on purple -> black on white),
        read in one pass; each digit run goes to the badge under its character box."""
        tiles = [col.crop(box).convert("L").point(lambda v: 0 if v > 160 else 255) for box in boxes]
        tiles = [t.resize((t.width * scale, t.height * scale), Image.LANCZOS) for t in tiles]
        strip = Image.new("L", (sum(t.width + gap for t in tiles) + gap, max(t.height for t in tiles) + 20), 255)
        spans, x = [], gap
        for t in tiles:
            strip.paste(t, (x, 10))
            spans.append((x, x + t.width))
            x += t.width + gap
        found = ["" for _ in boxes]

        def _completion(request, error):
            for o in request.results() or []:
                cand = o.topCandidates_(1)[0]
                text = cand.string()
                for m in re.finditer(r"\d+", text):
                    rect, _ = cand.boundingBoxForRange_error_(NSMakeRange(m.start(), m.end() - m.start()), None)
                    if rect is None:
                        continue
                    bb = rect.boundingBox()
                    cx = (bb.origin.x + bb.size.width / 2) * strip.width
                    for i, (a, c) in enumerate(spans):
                        if a - gap / 2 <= cx <= c + gap / 2:
                            found[i] = m.group()

        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_completion)
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            self._cg_from_pil(strip.convert("RGB")), None)
        handler.performRequests_error_([request], None)
        return found

    @staticmethod
    def _cg_from_pil(img: Image.Image):
        data = img.convert("RGBA").tobytes("raw", "RGBA")
        provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
        return Quartz.CGImageCreate(img.width, img.height, 8, 32, img.width * 4,
                                    Quartz.CGColorSpaceCreateDeviceRGB(),
                                    Quartz.kCGImageAlphaPremultipliedLast, provider, None, False,
                                    Quartz.kCGRenderingIntentDefault)

    def _ocr(self, cgimage) -> list[dict]:
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
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
                    "w": box.size.width,
                    "h": box.size.height,
                })

        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_completion)
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        # No language correction: it rewrites names and numbers, and costs ~25% of the time.
        request.setUsesLanguageCorrection_(False)
        success, error = handler.performRequests_error_([request], None)
        if not success:
            raise OCRError(f"Vision OCR failed: {error}")
        return results

    def wait_still(self, timeout: float = 2.0, interval: float = 0.06) -> float:
        """Block until two captures are identical (a panel finished sliding), or `timeout`."""
        start, last = time.monotonic(), None
        time.sleep(interval)
        while True:
            digest = hashlib.blake2b(self._pixels(self._cgimage())[0], digest_size=8).digest()
            if digest == last or time.monotonic() - start >= timeout:
                return time.monotonic() - start
            last = digest
            time.sleep(interval)

    # --- accessibility tree ----------------------------------------------------------
    def _ax_window(self):
        app = self._app()
        x, y, w, h = self._wframe()
        for win in list(_get(app, "AXWindows") or []) + [_get(app, "AXMainWindow")]:
            if win is None:
                continue
            f = _frame(_get(win, "AXPosition"), _get(win, "AXSize"))
            if f and abs(f[0] - x) < 2 and abs(f[1] - y) < 2 and abs(f[2] - w) < 2:
                return app, win
        win = _get(app, "AXMainWindow")
        if win is None:
            raise OCRError("Viber's window isn't in the accessibility tree.")
        return app, win

    def _walk(self) -> tuple[list[dict], list]:
        """Visible elements (dicts like the Windows backend's) and their AX elements, BFS order,
        parents before children."""
        app, win = self._ax_window()
        wframe = self._wframe()
        origin = wframe[:2]
        out: list[dict] = []
        refs: list = []
        index_of: dict[int, list[tuple[object, int]]] = {}  # CFHash -> [(element, index)]
        queue = deque([(win, None)])
        seen = 0
        while queue and seen < _MAX_NODES:
            el, parent = queue.popleft()
            seen += 1
            err, vals = AXUIElementCopyMultipleAttributeValues(el, _ATTRS, 0, None)
            if err != 0 or vals is None:
                continue
            role, ident, title, desc, value, pos, size, kids = (
                None if _is_error(v) else v for v in vals)
            frame = _frame(pos, size)
            idx = None
            if frame is not None and frame[2] > 0 and frame[3] > 0 \
                    and ml.visible_center(frame, wframe) is not None:
                cls = ml.short_class(ident)
                kind = ml.ax_type(role, cls)
                idx = len(out)
                out.append({
                    "type": kind,
                    "name": str(title or desc or (value if kind == "Text" and value is not None else "")),
                    "class": cls,
                    "text": ml.edit_text(value) if kind == "Edit" else "",
                    "parent": parent,
                    "rect": ml.to_rect(frame, origin),
                    "_frame": frame,
                    "_ident": str(ident) if ident else "",
                })
                refs.append(el)
                index_of.setdefault(CFHash(el), []).append((el, idx))
            for kid in kids or []:
                queue.append((kid, idx if idx is not None else parent))
        visible = self._visible(app, out, refs, index_of, wframe)
        return ml.nest_popups(*self._reindex(out, refs, visible))

    def _lookup(self, index_of, el) -> int | None:
        for cand, idx in index_of.get(CFHash(el), []):
            if CFEqual(cand, el):
                return idx
        return None

    def _visible(self, app, out, refs, index_of, wframe) -> list[bool]:
        """Hit-test each element's visible centre: drawn iff the topmost element there is the
        element itself, one of its descendants, or one of its ancestors."""
        visible = [True] * len(out)
        for i, e in enumerate(out):
            if e["parent"] is None and e["type"] == "Window":
                continue
            if e["parent"] is not None and not visible[e["parent"]]:
                visible[i] = False  # inside a hidden container
                continue
            cx, cy = ml.visible_center(e["_frame"], wframe)
            err, hit = AXUIElementCopyElementAtPosition(app, cx, cy, None)
            if err != 0 or hit is None:
                visible[i] = False
                continue
            # Walk up from the hit: reaching i means hit is i or inside it.
            ok, node, steps = False, hit, 0
            hit_idx = None
            while node is not None and steps < 40:
                j = self._lookup(index_of, node)
                if j is not None:
                    hit_idx = j if hit_idx is None else hit_idx
                    if j == i:
                        ok = True
                        break
                node, steps = _get(node, "AXParent"), steps + 1
            if not ok and hit_idx is not None:  # is the hit one of i's ancestors?
                p = e["parent"]
                while p is not None:
                    if p == hit_idx:
                        ok = True
                        break
                    p = out[p]["parent"]
            visible[i] = ok
        return visible

    @staticmethod
    def _reindex(out, refs, visible):
        new_index: dict[int, int] = {}
        els, kept = [], []
        for i, e in enumerate(out):
            if not visible[i]:
                continue
            p = e["parent"]
            while p is not None and p not in new_index:
                p = out[p]["parent"]  # parent hidden: attach to the nearest kept ancestor
            new_index[i] = len(els)
            d = {k: v for k, v in e.items() if k != "_frame"}
            d["parent"] = new_index.get(p) if p is not None else None
            els.append(d)
            kept.append(refs[i])
        return els, kept

    def elements(self) -> list[dict]:
        """Visible UI elements: type, name, short class, text (edits), parent index, and rect in
        screenshot coordinates (points from the window's top-left), parents before children.
        Closes the ChatGPT "Remix your image" and Viber Plus pop-ups first."""
        els, _ = self._walk()
        for point in (wl.remix_close_point(els), wl.promo_close_point(els)):
            if point is not None:
                self.click_at(*point)
                self.wait_still()
                els, _ = self._walk()
        return els

    def _find(self, predicate, what: str, required: bool = True):
        els, refs = self._walk()
        for e, el in zip(els, refs):
            if predicate(e):
                return e, el
        if required:
            raise OCRError(f"Viber's {what} was not found (is a chat open?).")
        return None, None

    # --- text fields: AXValue in and out --------------------------------------------
    def _target_ref(self):
        if self._target_el is not None and _get(self._target_el, "AXRole") is not None:
            return self._target_el
        _, self._target_el = self._find(self._target, "text box")
        return self._target_el

    def _value(self) -> str:
        """The target field's text (the box picked by the last click_compose / click_search)."""
        try:
            value = _get(self._target_ref(), "AXValue")
        except OCRError:
            return ""
        return ml.edit_text(value)

    def _set_value(self, text: str) -> None:
        if AXUIElementSetAttributeValue(self._target_ref(), "AXValue", text) != 0:
            self._target_el = None
            if AXUIElementSetAttributeValue(self._target_ref(), "AXValue", text) != 0:
                raise OCRError("Could not set the text in Viber's text box.")

    def _wait_value(self, expected: str, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._value() == expected:
                return True
            time.sleep(0.05)
        return False

    def _enter(self, text: str) -> None:
        """Put `text` in the target field (already cleared) and wait until it reads back."""
        self._set_value(text)
        if not self._wait_value(text, timeout=3.0):
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
        if not self._value():
            return
        self._set_value("")
        if not self._wait_value("", timeout=3.0):
            raise OCRError("Could not clear the existing text in Viber.")

    def paste(self) -> None:
        if self._staged is None:
            raise OCRError("Nothing staged to paste.")
        self._enter(self._staged)

    # --- element actions -------------------------------------------------------------
    def _focus_target(self, predicate, what: str) -> None:
        self._target = predicate
        _, self._target_el = self._find(predicate, what)
        AXUIElementSetAttributeValue(self._target_el, "AXFocused", True)
        time.sleep(0.1)

    def click_compose(self) -> None:
        self._focus_target(ml.is_compose, "message box")

    def click_search(self) -> None:
        self._focus_target(ml.is_search, "search box")

    def click_send(self) -> None:
        # Viber rebuilds the button once the box has text, so look it up fresh each time.
        _, el = self._find(ml.is_send, "send button")
        if AXUIElementPerformAction(el, "AXPress") != 0:
            _, el = self._find(ml.is_send, "send button")
            if AXUIElementPerformAction(el, "AXPress") != 0:
                raise OCRError("Viber's send button didn't respond.")

    def click_result(self, norm_y: float) -> None:
        """Click the left-column row whose text top is at normalized window height `norm_y`."""
        _, _, w, h = self._wframe()
        self.click_at(w * 0.15, h * norm_y + 8)

    def click_panel_close(self) -> None:
        pass  # side panels don't matter: the send button is found by identity, not position

    # --- clicks and scrolling --------------------------------------------------------
    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        wx, wy, _, _ = self._wframe()
        return wx + x, wy + y

    def _hit(self, sx: float, sy: float):
        err, hit = AXUIElementCopyElementAtPosition(self._app(), sx, sy, None)
        return hit if err == 0 else None

    def click_at(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        """Click at screenshot coordinates. A plain left click on a control is an AXPress (or
        focuses a text field) in the background; anything else is a real click."""
        if button not in ("left", "right", "middle"):
            raise ValueError("button must be 'left', 'right' or 'middle'")
        self._target_el = None  # a click anywhere may navigate
        sx, sy = self._to_screen(x, y)
        if button == "left" and not double:
            hit = self._hit(sx, sy)
            role = _get(hit, "AXRole") if hit is not None else None
            if role in ("AXTextField", "AXTextArea"):
                AXUIElementSetAttributeValue(hit, "AXFocused", True)
                self._focus_el = hit
                time.sleep(0.1)
                return
            if hit is not None and role in ("AXButton", "AXCheckBox", "AXMenuItem",
                                            "AXRadioButton") and "AXPress" in _actions(hit):
                if AXUIElementPerformAction(hit, "AXPress") == 0:
                    return
        self._focus_el = None
        self._with_viber_front(lambda: self._hid_click(sx, sy, button, double))

    def _hid_click(self, sx: float, sy: float, button: str, double: bool) -> None:
        kinds = {
            "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft),
            "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight),
            "middle": (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGMouseButtonCenter),
        }
        down, up, btn = kinds[button]
        move = Quartz.CGEventCreateMouseEvent(self._src, Quartz.kCGEventMouseMoved, (sx, sy), btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
        time.sleep(0.05)
        for click in ((1, 2) if double else (1,)):
            for kind in (down, up):
                e = Quartz.CGEventCreateMouseEvent(self._src, kind, (sx, sy), btn)
                Quartz.CGEventSetIntegerValueField(e, Quartz.kCGMouseEventClickState, click)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                time.sleep(0.05)

    def _with_viber_front(self, action) -> None:
        """Run `action` (real HID input) with Viber in front, then give the front app and the
        cursor back. macOS delivers mouse and wheel input by position, so Viber must be on top;
        if it can't be brought there nothing is sent (the input would land in the user's app)."""
        with self._handoff:  # hold_foreground pauses meanwhile
            prev_pid = self.foreground() or None
            cursor = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            app = self._app()
            if prev_pid != self._pid:
                AXUIElementSetAttributeValue(app, "AXFrontmost", True)
                for _ in range(50):
                    if self.foreground() == self._pid:
                        break
                    time.sleep(0.02)
                time.sleep(0.05)
            if self.foreground() != self._pid:
                raise OCRError("Couldn't bring Viber to the front for a click; nothing was sent.")
            try:
                action()
            finally:
                time.sleep(0.1)
                Quartz.CGWarpMouseCursorPosition(cursor)
                Quartz.CGAssociateMouseAndMouseCursorPosition(True)
                if prev_pid is not None and prev_pid != self._pid:
                    AXUIElementSetAttributeValue(AXUIElementCreateApplication(prev_pid), "AXFrontmost", True)

    def scroll(self, area: str, notches: int) -> None:
        """Mouse-wheel `notches` over the chat list ("chats") or the conversation; positive
        scrolls up (older messages / top of the list)."""
        _, _, w, h = self._wframe()
        anchor, fallback_x = (ml.is_search, 0.15) if area == "chats" else (ml.is_compose, 0.65)
        e, _ = self._find(anchor, "", required=False)
        x = (e["rect"][0] + e["rect"][2]) / 2 if e else w * fallback_x
        self.scroll_at(x, h * 0.55, notches)

    def scroll_at(self, x: float, y: float, notches: int) -> None:
        sx, sy = self._to_screen(x, y)
        step = 1 if notches > 0 else -1

        def wheel():
            move = Quartz.CGEventCreateMouseEvent(self._src, Quartz.kCGEventMouseMoved, (sx, sy),
                                                  Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
            time.sleep(0.05)
            for _ in range(abs(int(notches))):
                e = Quartz.CGEventCreateScrollWheelEvent(self._src, Quartz.kCGScrollEventUnitLine, 1, 3 * step)
                Quartz.CGEventSetLocation(e, (sx, sy))
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                time.sleep(0.01)
            time.sleep(0.1)
        self._with_viber_front(wheel)

    # --- keys: posted to Viber's process (works while it's in the background) --------
    def _post_key(self, code: int, flags: int = 0, text: str | None = None) -> None:
        self._app()  # refreshes self._pid if Viber restarted
        pid = self._pid
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(self._src, code, down)
            Quartz.CGEventSetFlags(e, flags)  # zero: a leftover Cmd would turn keys into shortcuts
            if text is not None:
                Quartz.CGEventKeyboardSetUnicodeString(e, ml.utf16_units(text), text)
            Quartz.CGEventPostToPid(pid, e)
            time.sleep(0.012)

    def press_key(self, key: str, repeat: int = 1) -> None:
        code = ml.KEYCODES.get(key.strip().lower())
        if code is None:
            raise ValueError(f"Unknown key {key!r}. Supported: {', '.join(sorted(ml.KEYCODES))}. "
                             "Modifier shortcuts (Cmd+...) aren't offered.")
        self._target_el = None  # Escape/Enter may close or change the page
        shown = self._onscreen_window() is not None

        def keys():
            # Real key events: posted to the process, Escape doesn't reach the image viewer.
            for _ in range(max(1, min(int(repeat), 100))):
                for down in (True, False):
                    e = Quartz.CGEventCreateKeyboardEvent(self._src, code, down)
                    Quartz.CGEventSetFlags(e, 0)
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                    time.sleep(0.012)
                time.sleep(0.03)
        self._with_viber_front(keys)
        if shown and self._onscreen_window() is None:
            # On macOS an Escape with nothing left to close hides Viber's window (like the red ✕).
            time.sleep(0.2)
            if self._onscreen_window() is None:
                self.focus()

    def type_into_focus(self, text: str) -> None:
        """Type into whatever field has Viber's focus — click it first. Newlines (Enter would
        send) are appended through AXValue on the focused field instead."""
        if "\n" not in text and "\r" not in text:
            for ch in text:
                self._post_key(0, 0, ch)
            time.sleep(0.2 + len(text) / 400)
            return
        field = self._focus_el or _get(self._app(), "AXFocusedUIElement")
        if field is None or _get(field, "AXRole") not in ("AXTextField", "AXTextArea"):
            raise OCRError("Click the text field first, then type (needed for newlines).")
        current = _get(field, "AXValue") or ""
        AXUIElementSetAttributeValue(field, "AXValue", str(current) + text)

    # --- focus protection ------------------------------------------------------------
    def foreground(self) -> int:
        """Pid of the front app: the owner of the topmost normal window. (NSWorkspace's answer
        goes stale in a process without a run loop, like this server, and the system-wide AX
        focus is empty for some apps.)"""
        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        for w in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
            b = w.get("kCGWindowBounds", {})
            if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowAlpha", 1) > 0 \
                    and float(b.get("Width", 0)) > 50 and float(b.get("Height", 0)) > 50:
                return int(w.get("kCGWindowOwnerPID", 0))
        return 0

    def hold_foreground(self, prev: int, seconds: float) -> None:
        """Keep handing the front back to `prev` (a pid) for `seconds`."""
        if not prev or prev == self._pid:
            return
        end = time.time() + seconds
        while time.time() < end:
            if self._handoff.acquire(blocking=False):  # never mid-click
                try:
                    if self.foreground() != prev:
                        AXUIElementSetAttributeValue(AXUIElementCreateApplication(prev), "AXFrontmost", True)
                finally:
                    self._handoff.release()
            time.sleep(0.05)

    # --- Viber's "send a file" picker (a standard macOS open panel, as a sheet) -------
    def _file_sheet(self):
        try:
            _, win = self._ax_window()
        except OCRError:
            return None
        return next((c for c in _get(win, "AXChildren") or [] if _get(c, "AXRole") == "AXSheet"), None)

    @staticmethod
    def _child(el, role: str, title: str | None = None):
        return next((c for c in _get(el, "AXChildren") or [] if _get(c, "AXRole") == role
                     and (title is None or _get(c, "AXTitle") == title)), None)

    def file_dialog_open(self) -> bool:
        return self._file_sheet() is not None

    def file_dialog_choose(self, path: str) -> bool:
        """Choose `path` in the open panel: Go to Folder (Cmd+Shift+G) with the full path, then
        Open. Returns whether the panel closed (file accepted). The shortcut and Return need
        real key events, so Viber is in front for about a second; the rest is AX."""
        sheet = self._file_sheet()
        if sheet is None:
            raise OCRError("Viber's file picker isn't open.")
        entered = {"ok": False}

        def go_to():
            flags = Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift
            for down in (True, False):  # Cmd+Shift+G (G = key code 5)
                e = Quartz.CGEventCreateKeyboardEvent(self._src, 5, down)
                Quartz.CGEventSetFlags(e, flags)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                time.sleep(0.03)
            field = None
            for _ in range(30):
                goto = self._child(sheet, "AXSheet")
                field = self._child(goto, "AXTextField") if goto is not None else None
                if field is not None:
                    break
                time.sleep(0.1)
            if field is None:
                return
            AXUIElementSetAttributeValue(field, "AXValue", path)
            AXUIElementSetAttributeValue(field, "AXFocused", True)  # Return goes to the focused field
            time.sleep(0.2)
            for down in (True, False):
                e = Quartz.CGEventCreateKeyboardEvent(self._src, ml.KEYCODES["return"], down)
                Quartz.CGEventSetFlags(e, 0)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
                time.sleep(0.03)
            entered["ok"] = True

        self._with_viber_front(go_to)
        if not entered["ok"]:
            raise OCRError("Couldn't open Go to Folder in Viber's file picker.")
        open_btn = None
        for _ in range(30):  # the go-to field closes and Open enables once the file is selected
            open_btn = self._child(sheet, "AXButton", "Open")
            if open_btn is not None and _get(open_btn, "AXEnabled") and self._child(sheet, "AXSheet") is None:
                break
            time.sleep(0.1)
        else:
            return False
        AXUIElementPerformAction(open_btn, "AXPress")
        for _ in range(40):
            time.sleep(0.1)
            if self._file_sheet() is None:
                return True
        return False

    def file_dialog_cancel(self) -> None:
        sheet = self._file_sheet()
        if sheet is None:
            return
        goto = self._child(sheet, "AXSheet")
        if goto is not None:  # an open Go to Folder field first
            cancel = next((c for c in _get(goto, "AXChildren") or [] if _get(c, "AXRole") == "AXButton"
                           and _get(c, "AXTitle") in ("Cancel",)), None)
            if cancel is not None:
                AXUIElementPerformAction(cancel, "AXPress")
                time.sleep(0.3)
        cancel = self._child(sheet, "AXButton", "Cancel")
        if cancel is not None:
            AXUIElementPerformAction(cancel, "AXPress")
