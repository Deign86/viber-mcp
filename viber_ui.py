"""Computer-use style control of Viber Desktop, plus message-level actions built on it.

Platform-neutral: talks to the backend's screenshot/click/key/type/scroll/elements primitives.
All coordinates exposed to callers are pixels in the image `screenshot()` returns.
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
import time
from pathlib import Path

from backends import get_backend
from viber_send import _poll, own_message_texts, require_open_chat, sent_confirmed

# Screenshots wider/taller than this are downscaled (vision models downscale anyway);
# coordinates are converted back with `_scale`.
_MAX_SIDE = 1568
_scale = 1.0
_last_elements: list[dict] = []

# Element types worth showing even without a name/text.
_INTERACTIVE = {"Button", "MenuItem", "CheckBox", "Edit", "ListItem", "TabItem", "Hyperlink"}


class UIError(Exception):
    """Raised when a UI action can't be carried out safely."""


def _backend():
    return get_backend()


# ----------------------------------------------------------------------- primitives


def screenshot() -> bytes:
    """PNG of the Viber window. Sets the coordinate scale used by click/scroll."""
    global _scale
    img = _backend().screenshot()
    longest = max(img.size)
    _scale = min(1.0, _MAX_SIDE / longest)
    if _scale < 1.0:
        img = img.resize((round(img.width * _scale), round(img.height * _scale)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _unscale(v: float) -> float:
    return v / _scale


def click(x: float, y: float, button: str = "left", double: bool = False) -> dict:
    _backend().click_at(_unscale(x), _unscale(y), button=button, double=double)
    time.sleep(0.4)
    return {"clicked": [x, y], "button": button, "double": double}


def press_key(key: str, repeat: int = 1) -> dict:
    _backend().press_key(key, repeat)
    time.sleep(0.3)
    return {"pressed": key, "repeat": repeat}


def type_text(text: str) -> dict:
    if not text:
        raise UIError("Nothing to type.")
    _backend().type_into_focus(text)
    return {"typed": text}


def scroll(x: float, y: float, direction: str = "down", amount: int = 5) -> dict:
    if direction not in ("up", "down"):
        raise UIError("direction must be 'up' or 'down'")
    amount = max(1, min(int(amount), 50))
    _backend().scroll_at(_unscale(x), _unscale(y), amount if direction == "up" else -amount)
    time.sleep(0.6)
    return {"scrolled": direction, "amount": amount, "at": [x, y]}


def _center(rect) -> tuple[int, int]:
    return round((rect[0] + rect[2]) / 2 * _scale), round((rect[1] + rect[3]) / 2 * _scale)


def list_elements(query: str | None = None) -> str:
    """Numbered list of visible controls/text, with click points in screenshot pixels."""
    global _last_elements
    _last_elements = _backend().elements()
    q = (query or "").strip().lower()
    rows = []
    for i, el in enumerate(_last_elements):
        label = el["name"] or el["text"]
        if not label and el["type"] not in _INTERACTIVE:
            continue
        line = f"{i}: {el['type']} {el['class']}"
        if el["name"]:
            line += f" name={el['name']!r}"
        if el["text"]:
            line += f" text={el['text'][:120]!r}"
        cx, cy = _center(el["rect"])
        line += f" @({cx},{cy})"
        if q and q not in line.lower():
            continue
        rows.append(line)
    if not rows:
        return "No matching elements." if q else "No elements found (is Viber open?)."
    return "\n".join(rows)


def click_element(element_id: int, button: str = "left", double: bool = False) -> dict:
    if not 0 <= element_id < len(_last_elements):
        raise UIError("Unknown element id — call list_elements first (ids change as the UI changes).")
    el = _last_elements[element_id]
    cx, cy = _center(el["rect"])
    click(cx, cy, button=button, double=double)
    return {"clicked_element": element_id, "type": el["type"], "name": el["name"],
            "text": el["text"][:120], "button": button}


# ------------------------------------------------------------------- message actions


def _messages(elements: list[dict]) -> list[dict]:
    """Visible messages of the open chat, top-to-bottom, from the UIA tree."""
    chat = next((e for e in elements if e["class"] == "StackView"), None)
    if chat is None:
        raise UIError("No chat is open in Viber.")
    own_left = chat["rect"][0] + (chat["rect"][2] - chat["rect"][0]) * 0.25
    view = _feed_view(elements, chat)
    by_parent: dict[int, list[dict]] = {}
    for el in elements:
        if el["parent"] is not None:
            by_parent.setdefault(el["parent"], []).append(el)
    def make(text_el, image_el, likes, delivered=None):
        rect = (text_el or image_el)["rect"]
        # The reaction (heart) button sits beside the bubble, level with it.
        like = next((k for k in likes if rect[1] - 30 <= _mid(k["rect"])[1] <= rect[3] + 30), None)
        return {
            "kind": "image" if image_el else "text",
            "text": text_el["text"] if text_el else "",
            # Own bubbles are right-aligned: theirs start next to the avatar on the left, ours
            # start well right of it. (Testing the right edge misfires on wide bubbles.)
            "mine": rect[0] > own_left,
            "rect": rect,
            "image_rect": image_el["rect"] if image_el else None,
            "like_rect": like["rect"] if like else None,
            "view": view,
            # Photos only: True once uploaded (Viber adds the bubble's AbstractButton then; while
            # uploading it shows a static clock instead). None where the tree can't tell.
            "delivered": delivered,
        }

    def is_text(e):
        return e["class"].startswith("TextEditItem") and e["text"]

    out = []
    chat_i = elements.index(chat)
    # Viber exposes messages either wrapped in a FeedDelegate each, or (depending on how the
    # list was laid out) as direct children of the chat pane. Handle both.
    for i, el in enumerate(elements):
        if el["class"] == "FeedDelegate":
            kids = by_parent.get(i, [])
            text_el = next((k for k in kids if is_text(k)), None)
            image_el = next((k for k in kids if k["class"] == "RemixImageButton"), None)
            if text_el or image_el:  # skip date separators, notices without text
                delivered = any(k["class"] == "AbstractButton" for k in kids) if image_el else None
                out.append(make(text_el, image_el, [k for k in kids if k["class"] == "QQuickLoader"],
                                delivered))
    direct = by_parent.get(chat_i, [])
    likes = [k for k in direct if k["class"] == "QQuickLoader"]
    for el in direct:
        if is_text(el):
            out.append(make(el, None, likes))
        elif el["class"] == "RemixImageButton":
            out.append(make(None, el, likes))
    out.sort(key=lambda m: m["rect"][1])
    for n, m in enumerate(reversed(out), start=1):
        m["n"] = n  # 1 = newest visible
    return out


def scroll_to_bottom_button(elements: list[dict]):
    """The chat's round "↓" jump-to-newest button (only shown when scrolled up), or None.
    (Not the chat list's ScrollToTopItem.)"""
    return next((e for e in elements if e["type"] == "Button" and e["class"] == "ScrollToBottomItem"), None)


def jump_to_latest() -> dict:
    """Show the newest messages of the open chat. Wheel-scrolling alone can't get there from far
    up (Viber loads history in chunks), so click the "↓" button while it's showing."""
    b = _backend()
    clicked = False

    def at_bottom():
        return scroll_to_bottom_button(b.elements()) is None
    for _ in range(4):
        button = scroll_to_bottom_button(b.elements())
        if button is None:
            break
        b.click_at(*_mid(button["rect"]))
        clicked = True
        _poll(at_bottom, timeout=1.5, first=0.2)  # the "↓" goes once the newest message shows
    b.scroll("chat", -60)  # settle at the very bottom
    return {"at_latest": bool(_poll(at_bottom, timeout=1.0, first=0.2)), "used_button": clicked}


def list_messages() -> dict:
    msgs = _messages(_backend().elements())
    return {
        "count": len(msgs),
        # Who has the last word: staff rule — a client chat must never end with the client's message.
        "last_from": ("me" if msgs[-1]["mine"] else "them") if msgs else None,
        "note": "Only messages currently on screen. n=1 is the newest. Use scroll_chat for older ones.",
        "messages": [{"n": m["n"], "from": "me" if m["mine"] else "them", "kind": m["kind"],
                      "text": m["text"]} for m in msgs],
    }


def _row(m: dict) -> dict:
    return {"from": "me" if m["mine"] else "them", "kind": m["kind"], "text": m["text"]}


def _merge_older(acc: list[dict], screen: list[dict]) -> tuple[list[dict], bool]:
    """Prepend what `screen` (oldest first, scrolled further up) shows above `acc`. They are
    joined at their longest overlap — screen's bottom = acc's top — so repeated texts ("ok",
    photos) aren't dropped the way a seen-set would drop them. Returns (merged, overlapped)."""
    for k in range(min(len(acc), len(screen)), 0, -1):
        if screen[len(screen) - k:] == acc[:k]:
            return screen[:len(screen) - k] + acc, True
    return screen + acc, False


def backread(screens: int = 40, until: str = "") -> dict:
    """The open chat's history, oldest first: jump to the newest message, then scroll up a
    screen at a time, stitching the screens together, until the chat's first message (the top
    stops moving), `until` text shows up, or `screens` runs out. Ends back at the newest."""
    b = _backend()
    want = until.strip().lower()
    jump_to_latest()
    acc = [_row(m) for m in _messages(b.elements())]
    gaps, stalls, reached_top = 0, 0, False
    for _ in range(max(1, int(screens))):
        if want and any(want in m["text"].lower() for m in acc):
            break
        before = [_row(m) for m in _messages(b.elements())]
        b.scroll("chat", 10)
        # Viber renders older history in chunks a moment after the wheel.
        _poll(lambda: [_row(m) for m in _messages(b.elements())] != before, timeout=2.0, first=0.15)
        merged, overlapped = _merge_older(acc, [_row(m) for m in _messages(b.elements())])
        if len(merged) == len(acc):
            stalls += 1
            if stalls >= 2:  # twice nothing older: this is the start of the chat
                reached_top = True
                break
            continue
        stalls = 0
        gaps += not overlapped
        acc = merged
    jump_to_latest()
    return {
        "count": len(acc),
        "last_from": acc[-1]["from"] if acc else None,
        "reached_top": reached_top,
        "gaps": gaps,
        "note": "Oldest first. reached_top=false: older messages exist (raise screens). gaps>0: a "
                "scroll skipped past some messages without overlap.",
        "messages": [{"i": i, **m} for i, m in enumerate(acc, start=1)],
    }


def open_image(n: int = 1) -> bytes:
    """Open the n-th newest visible image full-size; returns a screenshot of the viewer."""
    images = [m for m in reversed(_messages(_backend().elements())) if m["kind"] == "image"]
    if not images:
        raise UIError("No images are visible in the open chat (scroll_chat up to find older ones).")
    if not 1 <= n <= len(images):
        raise UIError(f"Only {len(images)} image(s) visible; n must be 1..{len(images)}.")
    l, t, _, _ = images[n - 1]["image_rect"]
    # The picture sits directly above its "Remix image" button, starting at the same left edge.
    _backend().click_at(l + 60, t - 60)
    time.sleep(1.5)
    return screenshot()


def close_viewer() -> dict:
    """Close the image viewer, a file-send preview (discarding its files), or a menu/dialog."""
    b = _backend()
    preview = _preview(b.elements())
    if preview and preview[3]:
        b.click_at(*_mid(preview[3]["rect"]))  # Escape doesn't dismiss the send preview
    else:
        b.press_key("escape")
    time.sleep(0.6)
    return {"closed": _preview(b.elements()) is None}


def _mid(rect) -> tuple[float, float]:
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def _preview(elements: list[dict]):
    """(caption box, send button, add-more button) of Viber's file-preview overlay, or None."""
    # The caption whose overlay holds the send button: macOS also lists the hidden ChatGPT
    # panel's TextArea, ahead of the caption. Otherwise the first TextArea, as before.
    found = None
    for caption in (e for e in elements if e["type"] == "Edit" and e["class"] == "TextArea"):
        parts = _preview_parts(elements, caption)
        if parts is not None and (found is None or (parts[1] is not None and found[1] is None)):
            found = parts
        if found is not None and found[1] is not None:
            break
    return found


def _preview_parts(elements: list[dict], caption: dict):
    if caption["parent"] is None:
        return None
    overlay = elements[caption["parent"]]["parent"]  # caption → ScrollView → overlay
    if overlay is None:
        return None  # a TextArea right under the window isn't an overlay's caption
    cy = _mid(caption["rect"])[1]
    send = next((e for e in elements if e["type"] == "Button" and e["class"] == "RoundIconButton"
                 and e["parent"] == overlay and abs(_mid(e["rect"])[1] - cy) < 30), None)
    # The "+" (add more files) sits just above the caption, right of the delete-file button.
    # (The overlay's close ✕ is also an IconButton, far up in the top-right corner.)
    add = max((e for e in elements if e["type"] == "Button" and e["class"] == "IconButton"
               and e["parent"] == overlay and e["rect"][3] <= caption["rect"][1]
               and caption["rect"][1] - e["rect"][3] < 120),
              key=lambda e: e["rect"][0], default=None)
    close = min((e for e in elements if e["type"] == "Button" and e["class"] == "IconButton"
                 and e["parent"] == overlay), key=lambda e: e["rect"][1], default=None)
    return caption, send, add, close


def _attach_button(elements: list[dict]):
    """The "+" (attach) button: leftmost icon button on the message box's row."""
    compose = next((e for e in elements if e["type"] == "Edit" and e["class"].startswith("QQuickTextEdit")), None)
    if compose is None:
        raise UIError("No chat is open in Viber (no message box).")
    cy = _mid(compose["rect"])[1]
    row = [e for e in elements if e["type"] == "Button" and e["class"].startswith("IconButton")
           and abs(_mid(e["rect"])[1] - cy) < 20 and e["rect"][2] <= compose["rect"][0]]
    if not row:
        raise UIError("Couldn't find Viber's attach (+) button.")
    return min(row, key=lambda e: e["rect"][0])


def _open_picker(button: dict) -> None:
    """Click `button` until Viber's file picker opens. The first click is sometimes swallowed
    (e.g. it only ends an active search), so retry a few times."""
    b = _backend()
    for _ in range(3):
        b.click_at(*_mid(button["rect"]))
        for _ in range(10):
            time.sleep(0.2)
            if b.file_dialog_open():
                return
    raise UIError("Viber's file picker didn't open.")


def _pick_file(path: str) -> None:
    """Fill the open "Send a file" dialog with `path` and confirm it."""
    b = _backend()
    if not b.file_dialog_choose(path):
        b.file_dialog_cancel()
        raise UIError(f"Viber didn't accept the file: {path}")


def send_files(paths: list[str], caption: str = "", dry_run: bool = False, expect_chat: str = "") -> dict:
    """Attach local files to the open chat and send them together (photos, PDFs, videos, ...).
    With `expect_chat`, refuses unless that chat is the open one."""
    files = [Path(p).expanduser().resolve() for p in paths]
    if not files:
        raise UIError("No files given.")
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise UIError(f"File(s) not found: {', '.join(missing)}")
    require_open_chat(expect_chat)
    b = _backend()
    if _preview(b.elements()):
        raise UIError("A file preview is already open in Viber — send or close it first (close_viewer).")

    _open_picker(_attach_button(b.elements()))
    _pick_file(str(files[0]))
    for f in files[1:]:
        preview = _wait_preview()
        if preview[2] is None:
            raise UIError("Couldn't find the preview's add-more (+) button.")
        _open_picker(preview[2])
        _pick_file(str(f))

    caption_box, send, _, _ = _wait_preview()
    if caption:
        b.click_at(*_mid(caption_box["rect"]))
        b.type_into_focus(caption)
    if dry_run:
        return {"sent": False, "files": [f.name for f in files], "caption": caption,
                "note": "The files are in Viber's send preview, unsent. Click its send button or "
                        "close_viewer to cancel."}
    if send is None:
        raise UIError("Couldn't find the preview's send button; nothing was sent.")
    b.click_at(*_mid(send["rect"]))
    for _ in range(30):
        time.sleep(0.3)
        if _preview(b.elements()) is None:
            break
    else:
        raise UIError("The preview is still open — the files may not have been sent. Take a screenshot.")
    # Viber uploads after the preview closes; failed photos raise a "Your last N messages were
    # not sent" banner. Wait for it to settle and press its Resend button ourselves.
    resends = _settle_and_resend(len(files))
    newest = next((m for m in reversed(_messages(b.elements()))), None)
    result = {"sent": True, "files": [f.name for f in files], "caption": caption,
              "verified": (bool(newest and newest["mine"]) and not resends["still_unsent"]
                           and not resends["uploading"]),
              "resend_attempts": resends["attempts"]}
    if resends["uploading"]:
        result["note"] = ("Some photos were still uploading when the wait ran out — check they all "
                          "arrived before sending anything else to this chat.")
    if resends["still_unsent"]:
        result["note"] = ("Viber still shows 'messages were not sent' after resending — send the "
                          "failed photos again (fresh files, a few at a time).")
    return result


def _resend_button(elements: list[dict]):
    """The red 'messages were not sent' banner's Resend / Resend all button, or None."""
    return next((e for e in elements if e["type"] == "Button" and e["class"] == "BannerButton"
                 and e["name"].lower().startswith("resend")), None)


def _feed_still(b, timeout: float, interval: float = 0.3, frames: int = 4) -> bool:
    """Wait until the message feed has stopped changing (`frames` consecutive identical captures
    `interval` apart) or `timeout`. While an album uploads, Viber animates a progress ring on
    the bubbles (nothing of it is in the UIA tree); a still feed means the album is sent, or
    failed with the not-sent banner up. Returns whether it went still. Backends without
    captures just wait `timeout`."""
    try:
        els = b.elements()
        chat = next(e for e in els if e["class"] == "StackView")
        box = tuple(int(v) for v in _feed_view(els, chat))
        b.screenshot().crop(box)
    except (NotImplementedError, AttributeError, StopIteration):
        time.sleep(timeout)
        return False
    deadline, last, same = time.time() + timeout, None, 0
    while time.time() < deadline:
        time.sleep(interval)
        digest = hashlib.blake2b(b.screenshot().crop(box).tobytes(), digest_size=8).digest()
        same = same + 1 if digest == last else 0
        last = digest
        if same >= frames - 1:
            return True
    return False


def _uploads_pending(elements: list[dict], n_files: int) -> bool | None:
    """Whether any of my photos among the newest `n_files` messages is still uploading. None
    when the tree can't tell (no photo of mine with a delivery marker on screen, e.g. macOS)."""
    try:
        msgs = _messages(elements)
    except UIError:
        return None
    photos = [m for m in msgs[-n_files:] if m["mine"] and m["kind"] == "image"
              and m.get("delivered") is not None]
    if not photos:
        return None
    return not all(m["delivered"] for m in photos)


def _wait_uploads(b, n_files: int, timeout: float, reads: int = 3) -> bool:
    """Poll until the newest photos have been delivered on `reads` consecutive reads (the feed
    going still isn't enough: a photo waiting to upload shows a static clock), the not-sent
    banner shows, or `timeout`. Returns False only if photos were still uploading at the end."""
    deadline, done = time.time() + timeout, 0
    while time.time() < deadline:
        els = b.elements()
        if _resend_button(els) is not None:
            return True
        pending = _uploads_pending(els, n_files)
        if pending is None:
            return True
        done = 0 if pending else done + 1
        if done >= reads:
            return True
        time.sleep(0.3)
    return False


def _settle_and_resend(n_files: int, max_attempts: int = 2) -> dict:
    """Wait for the upload to finish (the feed goes still and the newest photos show as
    delivered), then press Resend while the not-sent banner shows. The old fixed wait (4 s +
    1.5 s per file, at most 30 s) caps the still-feed wait; the delivery wait allows 3 s/file.
    On 2026-10-26 a listing's separator was sent while photos 4-7 still showed their clocks."""
    b = _backend()
    cap = min(4 + 1.5 * n_files, 30)
    attempts = 0
    while True:
        _feed_still(b, timeout=cap)
        uploaded = _wait_uploads(b, n_files, timeout=max(30, 3 * n_files))
        button = _resend_button(b.elements())
        if button is None:
            return {"attempts": attempts, "still_unsent": False, "uploading": not uploaded}
        if attempts >= max_attempts:
            return {"attempts": attempts, "still_unsent": True, "uploading": not uploaded}
        b.click_at(*_mid(button["rect"]))
        attempts += 1


def _wait_preview():
    for _ in range(30):
        preview = _preview(_backend().elements())
        if preview:
            return preview
        time.sleep(0.3)
    raise UIError("Viber's file preview didn't appear.")


def _feed_view(elements: list[dict], chat: dict) -> tuple[float, float, float, float]:
    """The on-screen part of the message feed: the chat pane minus its header bar and the
    message box row. UIA reports full rects for messages scrolled partly out of view."""
    l, t, r, b = chat["rect"]
    compose = next((e for e in elements if e["type"] == "Edit" and e["class"].startswith("QQuickTextEdit")
                    and t < e["rect"][1] < b), None)
    return l, t + 72, r, (compose["rect"][1] - 12) if compose else b


def _press_point(m: dict) -> tuple[float, float]:
    """Where to click/right-click a message: its text, or the picture above "Remix image".
    A message taller than the feed (long listing) has its centre off-screen, so then press
    inside the visible part instead: near its left edge (away from the timestamp), halfway
    down (clear of the reminder banner and the reply bar that can overlay the feed's ends)."""
    vl, vt, vr, vb = m.get("view") or (float("-inf"), float("-inf"), float("inf"), float("inf"))
    if m["kind"] == "image" and not m["text"]:
        l, t, _, _ = m["image_rect"]
        return l + 60, min(max(t - 60, vt + 10), vb - 10)
    l, t, r, b = m["rect"]
    if t >= vt and b <= vb:
        return _mid(m["rect"])
    top, bottom = max(t, vt), min(b, vb)
    if bottom <= top:
        raise UIError("That message is scrolled out of view — scroll_chat to bring it on screen.")
    return l + min(40, (r - l) / 2), (top + bottom) / 2


def _message(n: int) -> dict:
    msg = next((m for m in _messages(_backend().elements()) if m["n"] == n), None)
    if msg is None:
        raise UIError(f"No visible message n={n}. Call list_messages to see the numbers.")
    return msg


def message_action(n: int, action: str) -> dict:
    """Right-click message n and choose `action` from Viber's menu (Reply, Edit, Forward, Pin,
    Copy message, Translate, Set reminder, Select, ...). Returns the menu that was offered."""
    if action.strip().lower() == "delete":
        raise UIError("Use unsend_message to delete — it checks ownership and confirms the choice.")
    b = _backend()
    b.click_at(*_press_point(_message(n)), button="right")

    def menu_items(els):
        return [e for e in els if e["type"] == "MenuItem" and e["name"] != "System"]
    # The menu renders a moment after the right-click; read it as soon as it's there.
    els = _poll(lambda: els if menu_items(els := b.elements()) else None, timeout=1.5, first=0.15)
    els = els or b.elements()
    items = menu_items(els)
    names = [e["name"] for e in items]
    choice = next((e for e in items if e["name"].lower() == action.strip().lower()), None)
    if choice is None:
        b.press_key("escape")
        raise UIError(f"'{action}' isn't offered for that message. Available: {', '.join(names)}")
    before = {e["name"] for e in els if e["type"] == "Button" and e["name"]}
    b.click_at(*_mid(choice["rect"]))
    _poll(lambda: not menu_items(b.elements()), timeout=1.5, first=0.15)  # menu closed
    time.sleep(0.2)  # a confirmation dialog can follow it
    result = {"message": n, "chose": choice["name"], "menu": names}
    # Some actions (e.g. Pin) open a confirmation dialog; surface its buttons.
    dialog = sorted({e["name"] for e in b.elements() if e["type"] == "Button" and e["name"]} - before)
    if dialog:
        result["confirmation_dialog_buttons"] = dialog
        result["note"] = ("Viber is asking for confirmation. Click one of these buttons with "
                          "list_elements/click_element (or close_viewer to cancel).")
    return result


def _compose_and_maybe_send(text: str, send: bool) -> dict:
    """Replace the message box content with `text`; click send if asked. `verified` = the text
    showed up as a new message of mine (exact, from the UI tree)."""
    b = _backend()
    b.clipboard_set(text)
    before = own_message_texts(b)
    b.click_compose()
    b.delete()
    b.paste()
    if not send:
        return {"sent": False, "drafted": text}
    b.click_send()
    # The box clears once Viber accepts the send (was a fixed 1.2 s).
    _poll(lambda: _compose_text(b.elements()) == "", timeout=2.5, first=0.15)
    verified = _poll(lambda: sent_confirmed(text, before, b), timeout=2.0) if before is not None else None
    return {"sent": True, "text": text, "verified": verified}


def reply_to_message(n: int, text: str, send: bool = True, expect_chat: str = "") -> dict:
    """Quote-reply to message n with `text` (draft unless send=True). With `expect_chat`, refuses
    unless that chat is the open one."""
    if not text:
        raise UIError("Reply text is empty.")
    require_open_chat(expect_chat)
    quoted = _message(n)
    message_action(n, "Reply")
    result = _compose_and_maybe_send(text, send)
    result["replying_to"] = quoted["text"] or "(image)"
    return result


def edit_message(n: int, text: str) -> dict:
    """Change the text of one of MY sent messages (n from list_messages) and save it."""
    if not text:
        raise UIError("New text is empty.")
    target = _message(n)
    if not target["mine"]:
        raise UIError("That message isn't yours — only your own messages can be edited.")
    message_action(n, "Edit")
    _compose_and_maybe_send(text, send=True)
    after = _poll(lambda: next((m for m in _messages(_backend().elements())
                                if m["mine"] and m["text"] == text), None), timeout=2.0)
    return {"edited": True, "from": target["text"], "to": text, "verified": after is not None}


def like_message(n: int) -> dict:
    """Toggle the heart reaction on message n."""
    msg = _message(n)
    if msg["like_rect"] is None:
        raise UIError("That message has no reaction button.")
    _backend().click_at(*_mid(msg["like_rect"]))
    time.sleep(0.6)
    return {"toggled_like": n, "message": msg["text"] or "(image)"}


# ------------------------------------------------------------------ group chats


def _sidebar_right(elements: list[dict], width: float) -> float:
    """Right edge of Viber's left sidebar: where the chat pane (SplitView) begins."""
    import viber_ocr
    pane = viber_ocr._chat_pane(elements)
    return pane["rect"][0] if pane else width * 0.28


_SB_CACHE: dict = {}  # (backend, window size) -> sidebar right edge: fixed while the window is


def _sidebar_right_cached(b, img) -> float:
    """`_sidebar_right` without a UIA walk per read (that walk was most of an `_ocr_px` call).
    Only a found pane is cached: it can be missing from the tree while the Sponsored ad shows."""
    key = (id(b), img.width, img.height)
    if key not in _SB_CACHE:
        import viber_ocr
        pane = viber_ocr._chat_pane(b.elements())
        if pane is None:
            return img.width * 0.28
        _SB_CACHE.clear()
        _SB_CACHE[key] = pane["rect"][0]
    return _SB_CACHE[key]


def _ocr_px(region_px=None) -> list[dict]:
    """OCR observations with pixel rects (screenshot coordinates). Each carries SB, the sidebar's
    right edge, so sidebar text can be told apart from the chat pane's. `region_px` = (l, t, r, b)
    in screenshot pixels (r/b None = the edge) limits the OCR to that part — a hint, so filter."""
    b = _backend()
    img = b.screenshot()
    sidebar_right = _sidebar_right_cached(b, img)
    region = None
    if region_px is not None:
        l, t, r, bt = region_px
        region = (max(0, l) / img.width, max(0, t) / img.height,
                  (img.width if r is None else r) / img.width,
                  (img.height if bt is None else bt) / img.height)
    out = []
    for o in b.observe(region):
        out.append({"text": o["text"].strip(), "x": o["x"] * img.width, "y": o["y"] * img.height,
                    "w": o["w"] * img.width, "h": o["h"] * img.height, "W": img.width,
                    "H": img.height, "SB": sidebar_right})
    return out


def _ocr_center(o: dict) -> tuple[float, float]:
    return o["x"] + o["w"] / 2, o["y"] + o["h"] / 2


def _norm(s: str) -> str:
    """Lowercase alphanumerics with OCR look-alikes folded (I/l/1 → i, 0 → o)."""
    s = "".join(ch for ch in s.lower() if ch.isalnum())
    return s.translate(str.maketrans({"l": "i", "1": "i", "0": "o"}))


def _left_column(obs: list[dict]) -> list[dict]:
    """Only text that lies entirely inside the sidebar. A chat-pane banner like "Rafael …
    +639175177830 is not in your contact list" starts near the edge but runs into the chat pane —
    matching it instead of the picker row is how the client never got selected."""
    return [o for o in obs
            if o["x"] + o["w"] <= o.get("SB", o["W"] * 0.28) + 4]


def _wait_ocr(predicate, timeout: float = 6.0, left_only: bool = True, region_px=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        obs = _ocr_px(region_px)
        hits = [o for o in (_left_column(obs) if left_only else obs) if predicate(o["text"])]
        if hits:
            return hits, obs
        time.sleep(0.3)
    return [], []


def _invite_count(obs: list[dict], rows: list[dict] | None = None) -> int | None:
    """The picker's "N/250" selected counter, read from `rows` (default: the left sidebar)."""
    for o in _left_column(obs) if rows is None else rows:
        t = o["text"].replace(" ", "")
        if "/250" in t:
            digits = t.split("/250")[0][-3:]
            num = "".join(ch for ch in digits if ch.isdigit())
            return int(num) if num else None
    return None


_DIGIT_FOLD = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})


def find_number_row(rows: list[dict], number: str):
    """In OCR rows of a contact list, find the row showing `number` (tolerating O/0, I/l/1 OCR
    slips) and the display name on the line above it (ignoring the ○ select tick).
    Returns (row, display_name) or (None, "")."""
    want = number[-10:]
    hit = next((o for o in rows if want in "".join(
        ch for ch in o["text"].translate(_DIGIT_FOLD) if ch.isdigit())), None)
    if hit is None:
        return None, ""
    above = [o for o in rows if o["y"] < hit["y"] and hit["y"] - o["y"] < hit["h"] * 3
             and abs(o["x"] - hit["x"]) < 30 and len(o["text"]) > 1]
    return hit, (max(above, key=lambda o: o["y"])["text"] if above else number)


def select_circle_point(elements: list[dict], line: dict, line_is_number: bool, search: dict | None = None):
    """Where a picker row's select circle (○) is. It sits at the search box's right edge (the
    picker list and the search box share the sidebar's fixed width), vertically between the
    row's name line and its number line — so just below a name line, just above a number line.
    `search` is that picker's search box (default: the first one, the left sidebar's)."""
    search = search or next((e for e in elements if e["type"] == "Edit"
                             and e["class"].startswith("TextFieldItem")), None)
    if search is None:
        return None
    center_y = line["y"] + line["h"] / 2
    return search["rect"][2] - 5, center_y + (-15 if line_is_number else 6)


_PRIVACY_BLOCKED = re.compile(r"(.+?)\s+was not added to the group due to (?:their|his|her) privacy settings",
                              re.I)


def privacy_blocked_from_dialog(texts: list[str]) -> str | None:
    """Viber's "Couldn't be added — <Name> was not added to the group due to their privacy
    settings" dialog, shown right after "Start chat": the person's name ("" if unreadable), or
    None if no dialog. OCR may split the sentence over lines, so they're joined first."""
    joined = " ".join(t.strip() for t in texts)
    m = _PRIVACY_BLOCKED.search(joined)
    if m:
        return m.group(1).split("Couldn't be added")[-1].strip(" .:")
    return "" if "privacy settings" in joined.lower() and "not added" in joined.lower() else None


def _popup(elements: list[dict]):
    """Viber's modal dialog (a QQuickPopupItem window named by its title, e.g. "Pin note?",
    with NotificationButton children), or None."""
    return next((e for e in elements if e["type"] == "Window" and e["class"] == "QQuickPopupItem"), None)


def _dismiss_privacy_dialog(obs: list[dict]) -> str | None:
    """A client whose privacy settings block group invites: the group IS created, just without
    them, and a "Couldn't be added" dialog pops up (sometimes a moment after the header). Close
    it; returns the blocked person's name ("" if unreadable), or None if no dialog. Dialogs are
    QQuickPopupItem windows in the UIA tree, so waiting for one is a few cheap walks and the
    sentence is OCR'd only when a dialog is up (this was four full OCR reads, ~3 s)."""
    b = _backend()
    blocked = privacy_blocked_from_dialog([o["text"] for o in obs])
    if blocked is None:
        popup = _poll(lambda: _popup(b.elements()), timeout=1.5, first=0.1, interval=0.25)
        if popup is None:
            return None
        texts = [popup["name"]] + [o["text"] for o in _ocr_px(tuple(popup["rect"]))]
        blocked = privacy_blocked_from_dialog(texts)
        if blocked is None:
            return None  # some other dialog: not ours to close
    ok = next((e for e in b.elements() if e["type"] == "Button" and e["name"].strip().lower() == "ok"), None)
    if ok:
        b.click_at(*_mid(ok["rect"]))
    else:
        b.press_key("escape")
    time.sleep(0.5)
    return blocked


# --- Group from the client's 1:1 chat: header "Add participants" → tick staff → Save → rename.
# More reliable than the New group screen (whose name box / picker kept failing): the client is
# already ticked, and nothing is created until Save.

def _side_panel(elements: list[dict]):
    return next((e for e in elements if e["class"] == "SideBarContent"), None)


def add_participants_picker(elements: list[dict]):
    """(search box, Save button) of the open chat's "Add participants" picker in the right side
    panel, or None when it isn't showing."""
    panel = _side_panel(elements)
    if panel is None:
        return None
    inside = [e for e in elements if e["rect"][0] >= panel["rect"][0] - 2]
    search = next((e for e in inside if e["type"] == "Edit" and e["class"].startswith("TextFieldItem")), None)
    save = next((e for e in inside if e["type"] == "Button" and e["name"].strip() == "Save"), None)
    return (search, save) if search and save else None


def _panel_rows(obs: list[dict], search: dict) -> list[dict]:
    """OCR lines of the side-panel picker: right of the panel's left edge, below its search box."""
    left = search["rect"][0] - 40
    return [o for o in obs if o["x"] >= left and o["y"] > search["rect"][3]]


def _panel_region(search: dict):
    """The pixel region `_panel_rows` keeps, for `_ocr_px(region_px=...)` (the panel is ~1/3 of
    the window, so its reads are ~3x cheaper than a full one)."""
    return (search["rect"][0] - 40, search["rect"][3], None, None)


def picker_name_row(rows: list[dict], name: str):
    """The picker row whose name line is exactly `name` (OCR look-alikes folded), or None."""
    return next((o for o in rows if _norm(o["text"]) == _norm(name)), None)


def _compose_text(elements: list[dict]) -> str:
    return next((e["text"] for e in elements if e["type"] == "Edit"
                 and e["class"].startswith("QQuickTextEdit")), "")


def _type_first_letter_checked(b, name: str) -> None:
    """Type `name` into the focused picker search box, first letter on its own: staff saw the
    first letter dropped ("ark Anthony Santos", then the retry fixed it). The letter is typed
    again if the box doesn't show it, then the rest follows."""
    def box():
        p = add_participants_picker(b.elements())
        return p[0]["text"] if p else None
    b.type_into_focus(name[0])
    if not _poll(lambda: box() == name[0], timeout=0.6, first=0.05) and box() == "":
        b.type_into_focus(name[0])
    b.type_into_focus(name[1:])


def _panel_search_type(name: str) -> dict:
    """Type `name` into the picker's search box (replacing what's there). A click that misses
    the box leaves focus in the message box, so text that lands there is backspaced away."""
    b = _backend()
    for _ in range(3):
        els = b.elements()
        picker = add_participants_picker(els)
        if picker is None:
            raise UIError("The Add participants picker closed unexpectedly; nothing was saved.")
        search, compose_before = picker[0], _compose_text(els)
        b.click_at(*_mid(search["rect"]))
        time.sleep(0.2)
        if search["text"]:
            b.press_key("end")
            b.press_key("backspace", len(search["text"]) + 2)
        _type_first_letter_checked(b, name)

        # The box's UIA value says where the text landed (was a fixed 1.2 s).
        def landed():
            e = b.elements()
            p = add_participants_picker(e)
            return e if p and p[0]["text"] == name else None
        els = _poll(landed, timeout=1.5, first=0.1) or b.elements()
        picker = add_participants_picker(els)
        if picker and picker[0]["text"] == name:
            return picker[0]
        stray = _compose_text(els)
        if stray != compose_before and stray.endswith(name):
            b.click_compose()
            b.press_key("end")
            b.press_key("backspace", len(name))
    raise UIError(f"Couldn't type '{name}' into the Add participants search; nothing was saved.")


def _tick_participant(name: str) -> None:
    """Search the Add participants picker for `name` and tick its row."""
    b = _backend()
    search = _panel_search_type(name)
    region = _panel_region(search)
    hit, rows = None, []
    deadline = time.time() + 3.0
    while True:  # results can render a moment late
        rows = _panel_rows(_ocr_px(region), search)
        hit = picker_name_row(rows, name)
        if hit or time.time() >= deadline:
            break
        time.sleep(0.25)
    if hit is None:
        raise UIError(f"Couldn't find '{name}' in the Add participants list; nothing was saved.")
    before = _invite_count([], rows)
    for point in (select_circle_point([], hit, False, search=search), _ocr_center(hit)):
        b.click_at(*point)
        after = before
        deadline = time.time() + 3.0
        while time.time() < deadline:  # the counter can take a moment to update
            time.sleep(0.2)
            after = _invite_count([], _panel_rows(_ocr_px(region), search))
            if before is None or after is None or after == before + 1:
                return
    raise UIError(f"Ticking '{name}' didn't register (selected {before} → {after}); nothing was saved.")


def side_panel_back_point(elements: list[dict]):
    """Where a side-panel sub-page's ← back arrow is (top-left of the panel), or None with no
    panel. Only buttons INSIDE the panel count: the chat pane's own ← (history back) sits just
    left of it and navigates to the previous chat. Some pages (Groups in common) don't expose the
    arrow to UIA, so fall back to its measured spot, 23px in and 20px down from the panel corner."""
    panel = _side_panel(elements)
    if panel is None:
        return None
    pl, pt = panel["rect"][0], panel["rect"][1]
    back = next((e for e in elements if e["type"] == "Button" and e["class"] == "IconButton"
                 and pl <= e["rect"][0] < pl + 50 and e["rect"][1] < pt + 40), None)
    return _mid(back["rect"]) if back else (pl + 23, pt + 20)


def _close_side_panel_page() -> None:
    """Leave a side-panel page (the picker) via its top-left back arrow, without saving."""
    b = _backend()
    point = side_panel_back_point(b.elements())
    if point:
        b.click_at(*point)
        b.wait_still()  # the page slides; a UIA walk during that costs ~1 s
        # Back lands on the panel's main page (pencil showing) or closes the panel.
        _poll(lambda: name_edit_button(els := b.elements()) is not None or _side_panel(els) is None,
              timeout=1.2)


def name_edit_button(elements: list[dict]):
    """The pencil next to the chat's name on the info panel's main page: the left one of the two
    icon buttons (pencil, star) on the name row. The panel's top-corner close/back buttons are
    excluded by position. None when the main page isn't showing."""
    panel = _side_panel(elements)
    if panel is None:
        return None
    icons = [e for e in elements if e["type"] == "Button" and e["class"] == "IconButton"
             and e["rect"][0] >= panel["rect"][0] and e["rect"][1] > panel["rect"][1] + 100]
    for e in sorted(icons, key=lambda e: e["rect"][1]):
        row = [o for o in icons if abs(o["rect"][1] - e["rect"][1]) < 6]
        if len(row) == 2:
            return min(row, key=lambda o: o["rect"][0])
    return None


def rename_field(elements: list[dict], pencil: dict):
    """The group-name box the pencil opens: an edit level with the pencil, left of it."""
    py = _mid(pencil["rect"])[1]
    return next((e for e in elements if e["type"] == "Edit" and abs(_mid(e["rect"])[1] - py) < 12
                 and e["rect"][2] <= pencil["rect"][0]), None)


def _info_main_page() -> list[dict]:
    """Show the open chat's info panel main page (the one with the name ✎ and Participants,
    Groups in common, ...), from wherever the side panel is. Returns its elements."""
    b = _backend()
    for _ in range(4):
        els = b.elements()
        if name_edit_button(els):
            return els
        if _side_panel(els):
            _close_side_panel_page()  # on a sub-page (picker, Participants, ...): go back
            continue
        info = next((e for e in els if e["class"] == "InfoButton"), None)
        if info is None:
            raise UIError("Couldn't find the chat's info button.")
        b.click_at(*_mid(info["rect"]))
        b.wait_still()  # the panel slides in
        _poll(lambda: name_edit_button(b.elements()) is not None, timeout=2.0)
    raise UIError("Couldn't open the chat's info panel.")


def _rename_open_chat(gc_name: str, expect: str = "") -> None:
    """Rename the open group from its info panel (pencil → type → ✓).

    Right after Save the info panel is still sliding in, and a pencil click then was lost: the
    group stayed "Group" with the panel open (4 times on 2026-10-22). So wait for the panel to
    settle, re-locate the pencil, and retry the click until the name box opens."""
    b = _backend()
    field = pencil = None
    for _ in range(3):
        _info_main_page()
        b.wait_still()
        pencil = name_edit_button(b.elements())
        if pencil is None:
            continue
        b.click_at(*_mid(pencil["rect"]))
        field = _poll(lambda: rename_field(b.elements(), pencil), timeout=1.5, first=0.1)
        if field is not None:
            break
    if field is None:
        raise UIError("The group name box didn't open.")
    # A new post elsewhere can switch the open chat (APR LISTING ONLY took over mid-rename on
    # 2026-10-22): only rename the chat we expect, never an existing group.
    if expect and not _name_matches(field["text"], expect):
        b.press_key("escape")
        raise UIError(f"The open chat is '{field['text']}', not the new '{expect}'; nothing was renamed.")
    b.click_at(*_mid(field["rect"]))
    b.press_key("end")
    b.press_key("backspace", len(field["text"]) + 2)
    b.type_into_focus(gc_name)

    def typed_now():
        return next((e["text"] for e in b.elements() if e["type"] == "Edit" and e["rect"] == field["rect"]), "")
    typed = typed_now() if _poll(lambda: typed_now() == gc_name, timeout=1.5, first=0.1) else typed_now()
    if typed != gc_name:
        raise UIError(f"The group name didn't take (got {typed!r}); rename it to '{gc_name}' by hand.")
    # The pencil turned into ✓ in the same spot; the box closes once the name is saved.
    for _ in range(2):
        b.click_at(*_mid(pencil["rect"]))
        if _poll(lambda: rename_field(b.elements(), pencil) is None, timeout=1.5, first=0.1):
            return
    raise UIError(f"The new group name wasn't saved; rename it to '{gc_name}' by hand.")


def _name_matches(actual: str, expect: str) -> bool:
    """Is chat name `actual` the one meant by `expect`? Exact after folding OCR look-alikes, or
    (for names of 8+ characters) `expect` as a prefix — the sidebar/header truncates long group
    names ("MON-WH-(2000SQM,..."). Short names ("Group") must match exactly."""
    a, e = _norm(actual), _norm(expect.rstrip(". …"))
    if "~" in expect:
        # OCR reads the "~" of a placeholder GC name as "-", "e", "(-"…: let it be 0-2 characters.
        pat = ".{0,2}".join(re.escape(_norm(p)) for p in expect.rstrip(". …").split("~"))
        if re.fullmatch(pat, a):
            return True
    return bool(e) and (a == e or (len(e) >= 8 and (a.startswith(e) or e.startswith(a))))


def _close_info_panel() -> bool:
    """Close the right-side info panel via its top-right ✕ (only buttons INSIDE the panel count).
    True once no panel shows; False if there is none to close or it stayed."""
    b = _backend()
    els = b.elements()
    panel = _side_panel(els)
    if panel is None:
        return False
    pl, pt, pr = panel["rect"][0], panel["rect"][1], panel["rect"][2]
    close = max((e for e in els if e["type"] == "Button" and e["class"] == "IconButton"
                 and pl <= e["rect"][0] and e["rect"][2] <= pr + 2 and e["rect"][1] < pt + 60),
                key=lambda e: e["rect"][0], default=None)
    if close is None:
        return False
    b.click_at(*_mid(close["rect"]))
    return bool(_poll(lambda: _side_panel(b.elements()) is None, timeout=1.5, first=0.1))


def rename_group(new_name: str, expect_current: str = "") -> dict:
    """Rename the OPEN group chat (info panel → ✎ → type → ✓) to `new_name`. Refuses when the
    open chat isn't a group, or when `expect_current` is given and isn't the group's name, so a
    chat that switched under us is never renamed. Verifies the header shows the new name."""
    import viber_send
    new_name = new_name.strip()
    if not new_name:
        raise UIError("The new group name is empty.")
    b = _backend()
    info = viber_send.current_chat()
    if not info["open"]:
        raise UIError("No chat is open in Viber; nothing was renamed.")
    if not info["is_group"]:
        raise UIError(f"The open chat '{info['title']}' isn't a group (no 'N participants' under its "
                      "name); nothing was renamed.")
    if expect_current and not _name_matches(info["title"], expect_current):
        raise UIError(f"The open chat is '{info['title']}', not '{expect_current}'; nothing was renamed.")
    had_panel = _side_panel(b.elements()) is not None
    _rename_open_chat(new_name, expect=expect_current)
    head = _norm(new_name)[:10]
    shown, _ = _wait_ocr(lambda t: _norm(t).startswith(head), timeout=8, left_only=False)
    if not had_panel:
        _close_info_panel()
    return {"renamed": bool(shown), "from": info["title"], "to": new_name,
            "note": None if shown else "The header doesn't show the new name yet; take a screenshot."}


def groups_in_common(number: str = "") -> dict:
    """The groups the OPEN 1:1 chat's contact already shares with us (info panel → Groups in
    common). Non-empty = EXISTING CLIENT: one GC per client, never make another. With `number`,
    also refuses unless the open chat is that number's 1:1 chat."""
    import viber_send
    b = _backend()
    info = viber_send.current_chat()
    if not info["open"]:
        raise UIError("No chat is open in Viber.")
    if info["is_group"]:
        raise UIError(f"'{info['title']}' is a group; open the client's 1:1 chat first (Groups in common "
                      "is read from there).")
    if number.strip():
        chat = next((e for e in b.elements() if e["class"] == "StackView"), None)
        if not is_one_to_one_with(_ocr_px(), number.strip(), (chat["rect"][1] + 72) if chat else 140):
            raise UIError(f"The open chat '{info['title']}' isn't {number}'s 1:1 chat.")
    had_panel = _side_panel(b.elements()) is not None
    groups = _groups_in_common()
    if not had_panel:
        _close_info_panel()
    return {"chat": info["title"], "groups": groups, "existing_client": bool(groups)}


def _close_sidebar_menu(menu) -> None:
    """Close the chat list's context menu without picking anything. A posted Escape doesn't close
    it (checked 2026-10-24); a click on the sidebar's "Chats" label does, and that label does
    nothing itself. Escape is the fallback when the label can't be found."""
    import viber_ocr
    b = _backend()
    label = next((o for o in viber_ocr.observe(region=(0, 0, 0.34, 0.25))
                  if o["y"] < 0.2 and o["x"] < 0.1 and o["text"].strip().lower() == "chats"), None)
    if label is not None:
        img = b.screenshot()
        b.click_at((label["x"] + label.get("w", 0.03) / 2) * img.width,
                   (label["y"] + label.get("h", 0.01) / 2) * img.height)
    else:
        b.press_key("escape")
    _poll(lambda: not menu(), timeout=1.5, first=0.1)


def mark_as_read(name: str) -> dict:
    """Clear a chat's unread badge from the sidebar: right-click its row → \"Mark as read\". Opening
    a chat in the background doesn't clear the badge, so inbox sweeps do this. Only that menu
    item is ever clicked (the same menu holds \"Delete conversation\"); a chat that is already
    read (\"Mark as unread\" offered) is left as it is."""
    import viber_ocr
    name = name.strip()
    if not name:
        raise UIError("Which chat? Give its name as the sidebar shows it.")
    b = _backend()
    sidebar = viber_ocr._sidebar_fraction()
    rows = viber_ocr.chat_rows(viber_ocr.observe(region=(0, 0, sidebar + 0.04, 1)), sidebar)
    exact = [r for r in rows if _norm(r["name"]) == _norm(name)]
    hits = exact or [r for r in rows if r["name"] and _name_matches(r["name"], name)]
    if not hits:
        raise UIError(f"'{name}' isn't in the visible chat list (scroll_chat_list, or check the name). "
                      f"Visible: {', '.join(r['name'] for r in rows if r['name']) or 'none'}.")
    if len(hits) > 1:
        raise UIError(f"Several visible chats match '{name}': {', '.join(r['name'] for r in hits)}. "
                      "Give the full name.")
    row = hits[0]
    img = b.screenshot()
    b.click_at(sidebar / 2 * img.width, (row["y"] + 0.01) * img.height, button="right")

    def menu():
        return [e for e in b.elements() if e["type"] == "MenuItem" and e["name"].strip()]
    items = _poll(menu, timeout=2.0, first=0.2) or []
    by_name = {e["name"].strip().lower(): e for e in items}
    if "mark as read" in by_name:
        b.click_at(*_mid(by_name["mark as read"]["rect"]))
        _poll(lambda: not menu(), timeout=1.5, first=0.1)
        return {"chat": row["name"], "marked_read": True}
    _close_sidebar_menu(menu)
    if "mark as unread" in by_name:
        return {"chat": row["name"], "marked_read": False, "already_read": True}
    raise UIError(f"The row's menu didn't offer 'Mark as read' (got: {', '.join(by_name) or 'no menu'}); "
                  "nothing was clicked. Take a screenshot.")


def is_one_to_one_with(obs: list[dict], number: str, header_bottom: float) -> bool:
    """Is the open chat `number`'s own 1:1 chat? Proof: its "<number> is not in your contact
    list" banner, or the number in the header — and the header isn't a group's "N participants".
    (A client GC also shows the number, inside the posted template — that must not count.)"""
    want = number[-10:]

    def has_number(t):
        return want in "".join(ch for ch in t.translate(_DIGIT_FOLD) if ch.isdigit())

    header = [o for o in obs if o["y"] < header_bottom]
    if any(re.fullmatch(r"\d+\s+participants", o["text"].strip().lower()) for o in header):
        return False
    return any(has_number(o["text"]) and ("not in your contact list" in o["text"].lower()
                                          or o["y"] < header_bottom) for o in obs)


def groups_in_common_from_page(texts: list[str]) -> list[str]:
    """Group names listed under the info panel's "Groups in common" header (OCR lines)."""
    out, seen = [], False
    for t in texts:
        s, low = t.strip(), t.strip().lower()
        if low == "groups in common":
            seen = True
        elif seen and len(s) > 2 and "no group" not in low and "in common" not in low:
            out.append(s)
    return out


def _groups_in_common() -> list[str]:
    """Groups the open 1:1 chat's contact already shares with us (info panel → Groups in common)."""
    b = _backend()
    els = _info_main_page()
    row = next((e for e in els if e["type"] == "Button" and e["name"].strip() == "Groups in common"), None)
    if row is None:
        raise UIError("Couldn't open the chat's 'Groups in common'; nothing was created.")
    b.click_at(*_mid(row["rect"]))
    # The sub-page slides over the main page (its pencil goes) and its list fades in: wait for
    # the screen to settle, then confirm with one walk.
    b.wait_still()
    els = _poll(lambda: els if name_edit_button(els := b.elements()) is None else None, timeout=1.5)
    panel = _side_panel(els or b.elements())
    region = (panel["rect"][0], 0, None, None) if panel else None
    texts = [o["text"] for o in _ocr_px(region) if panel and o["x"] >= panel["rect"][0]]
    _close_side_panel_page()
    return groups_in_common_from_page(texts)


def existing_group_with(client_number: str) -> str | None:
    """The first group `client_number` already shares with us, checked from their OPEN 1:1 chat
    (info panel → Groups in common); None if none. Raises UIError when the open chat isn't their
    1:1 chat (e.g. a brand-new number whose chat didn't open), so the caller can decide."""
    b = _backend()
    chat = next((e for e in b.elements() if e["class"] == "StackView"), None)
    if not is_one_to_one_with(_ocr_px(), client_number, (chat["rect"][1] + 72) if chat else 140):
        raise UIError(f"The open chat isn't {client_number}'s 1:1 chat.")
    shared = _groups_in_common()
    return shared[0] if shared else None


def create_group_from_chat(gc_name: str, members: list[str], client_number: str,
                           self_name: str = "") -> dict:
    """Create the client's group from their OPEN 1:1 chat: header "Add participants" (the client is
    already ticked) → tick `members` → Save → rename to `gc_name`. Message the client first so the
    chat exists. Leaves the new group open."""
    b = _backend()
    for attempt in range(2):
        els, obs = b.elements(), _ocr_px()
        chat = next((e for e in els if e["class"] == "StackView"), None)
        if is_one_to_one_with(obs, client_number, (chat["rect"][1] + 72) if chat else 140):
            break
        if attempt == 0:  # the viber:// link sometimes doesn't navigate: try once more
            b.open_url(f"viber://chat?number={client_number}")
            time.sleep(5.0)
    else:
        raise UIError(f"The open chat isn't {client_number}'s 1:1 chat (no 'not in your contact "
                      "list' banner or number in the header); nothing was created.")
    client_display = name_from_contact_banner([o["text"] for o in obs], client_number)
    shared = _groups_in_common()
    if shared:  # an existing client: never make a second GC
        return {"created": False, "existing_group": shared[0], "gc_name": gc_name,
                "members_added": [], "client_display_name": client_display}
    els = b.elements()
    chat = next((e for e in els if e["class"] == "StackView"), None)
    if not is_one_to_one_with(_ocr_px(), client_number, (chat["rect"][1] + 72) if chat else 140):
        raise UIError(f"Viber left {client_number}'s 1:1 chat before Add participants; nothing was "
                      "created.")
    button = next((e for e in b.elements() if e["class"] == "AddParticipantsButton"), None)
    if button is None:
        raise UIError("Couldn't find the chat's Add participants button; nothing was created.")
    b.click_at(*_mid(button["rect"]))
    b.wait_still()  # the picker slides in with the contact list; walking during that costs ~1 s
    if not _poll(lambda: add_participants_picker(b.elements()), timeout=5.0, interval=0.2):
        raise UIError("The Add participants picker didn't open; nothing was created.")

    added = []
    try:
        for member in members:
            if self_name and _norm(member) == _norm(self_name):
                continue  # the creator is in the group automatically
            _tick_participant(member)
            added.append(member)
    except UIError:
        _close_side_panel_page()  # leave without saving: nothing is created
        raise

    save = add_participants_picker(b.elements())[1]
    prev = b.foreground()
    b.click_at(*_mid(save["rect"]))
    # Viber activates itself when the group is created; guard the user's window from a thread
    # so the wait for the group starts at once (the guard used to block for its 2 s).
    threading.Thread(target=b.hold_foreground, args=(prev, 2.0), daemon=True).start()
    # The new group opens as "Group" with "<N> participants" under it.
    hits, obs = _wait_ocr(lambda t: re.fullmatch(r"\d+\s+participants", t.strip().lower()) is not None,
                          timeout=20, left_only=False)
    if not hits:
        return {"created": False, "gc_name": gc_name, "members_added": added,
                "client_display_name": client_display, "privacy_blocked": None}
    blocked = _dismiss_privacy_dialog(obs)
    try:
        _rename_open_chat(gc_name, expect="Group")
    except UIError:
        renamed = []  # the group exists; the caller reports the missing rename
    else:
        head = _norm(gc_name)[:10]
        renamed, _ = _wait_ocr(lambda t: _norm(t).startswith(head), timeout=8, left_only=False)
    return {"created": True, "renamed": bool(renamed), "gc_name": gc_name, "members_added": added,
            "client_display_name": client_display, "privacy_blocked": blocked}


_ISOLATES = str.maketrans("", "", "⁨⁩")


def is_placeholder_name(name: str) -> bool:
    """A picker label that isn't the person's Viber name: blank, "Unknown", or just a number."""
    n = _norm(name)
    return n in ("", "unknown", "unknowncontact") or re.fullmatch(r"[\d\s+()-]+", name.strip()) is not None


def name_from_contact_banner(lines: list[str], number: str) -> str:
    """The Viber name an unsaved number's 1:1 chat shows in its banner
    "<Name> +63953… is not in your contact list". "" unless the banner is for `number`."""
    for t in lines:
        m = re.match(r"\s*(.+?)\s+(\+?[\dOoIl|SB][\dOoIl|SB ]{8,}?)\s+is not in your contact",
                      t.translate(_ISOLATES), re.I)
        digits = "".join(ch for ch in m.group(2).translate(_DIGIT_FOLD) if ch.isdigit()) if m else ""
        if digits and digits[-10:] == number[-10:]:
            return m.group(1).strip()
    return ""


def mention_inserted(value: str, before: str, name: str, after: str = "") -> bool:
    """Is the message box exactly before + @name (+ after)? A whole-name match: Viber's mention
    list also offers members whose name merely contains it ("Mark Anthony Santos" for
    "Anthony"), and inserting one of those mentions the wrong person."""
    v = value.translate(_ISOLATES).rstrip()
    if not v.startswith(before):
        return False
    rest = v[len(before):]
    tail = after.strip()
    if tail:
        if not rest.endswith(tail):
            return False
        rest = rest[:-len(tail)]
    return rest.lstrip().startswith("@") and _norm(rest) == _norm(name)


def _mention_zone(b) -> list[dict]:
    """OCR lines of the chat pane just above the message box, where the mention list pops up."""
    compose = next(e for e in b.elements() if e["type"] == "Edit" and e["class"].startswith("QQuickTextEdit"))
    top = compose["rect"][1]
    return [o for o in _ocr_px((0, top - 400, None, top - 5))
            if top - 400 < o["y"] < top - 5 and o["x"] > o.get("SB", o["W"] * 0.28)]


def send_with_mention(before: str, display_name: str, after: str = "",
                      quote_n: int | None = None) -> dict:
    """Send "<before>@<display_name><after>" in the open chat, picking the person from Viber's
    mention list so they're notified. With quote_n, it's sent as a quote-reply to that message.

    The row is picked with the keyboard (Down × k, Tab) and the inserted text is checked to be
    exactly this name; clicking the OCR'd row and sending with the list still open once mentioned
    "Mark Anthony Santos" instead of "Anthony"."""
    b = _backend()
    if quote_n is not None:
        message_action(quote_n, "Reply")
    b.click_compose()
    b.delete()
    want = _norm(display_name)
    # Typing the start of the name filters Viber's mention list (it only shows a few rows).
    first = display_name.split()[0] if display_name.split() else ""
    query = f"@{first[:3] if is_placeholder_name(display_name) else first[:4]}"
    baseline = {o["text"] for o in _mention_zone(b)}  # chat text that was there before the list
    b.type_into_focus(before + query)

    def new_rows():
        return [o for o in _mention_zone(b) if o["text"] not in baseline]
    # The list pops up a moment after the "@"; once it shows, read it again so it's complete.
    rows = _poll(new_rows, timeout=2.0, first=0.3)
    if rows:
        time.sleep(0.25)
        rows = new_rows() or rows
    hits = sum(_norm(o["text"]) == want for o in rows)
    if not hits:
        b.delete()
        raise UIError(f"'{display_name}' isn't in this chat's mention list.")
    if hits > 1:  # e.g. two brand-new numbers, both "Unknown": either pick could be wrong
        b.delete()
        raise UIError(f"{hits} participants are named '{display_name}' in the mention list — "
                      "ambiguous; nothing sent.")
    # Which row is highlighted first can't be read from OCR, so try each until the inserted
    # mention is exactly this name.
    for downs in range(min(len(rows), 8)):
        if downs:  # undo the wrong pick, keeping `before` (and any quote) in place
            b.press_key("backspace", max(1, len(b._value().translate(_ISOLATES)) - len(before)))
            b.type_into_focus(query)
            _poll(new_rows, timeout=2.0, first=0.3)
            time.sleep(0.25)
            b.press_key("down", downs)
        pre = b._value()
        b.press_key("tab")
        # Wait for Tab to insert *a* mention (the box changes), then check it's the right one —
        # waiting for the right one would cost the full timeout on every wrong pick.
        _poll(lambda: b._value() != pre, timeout=1.0, first=0.1, interval=0.05)
        if mention_inserted(b._value(), before, display_name):
            break
    else:
        b.delete()
        raise UIError(f"Couldn't insert a mention of exactly '{display_name}'; nothing sent.")
    # A space right after the mention closes the list; sending while it's open can swap the pick.
    glue = bool(after) and not after[:1].isspace()  # punctuation right after the name: "@Unknown!"
    b.type_into_focus(" " if glue else after if after[:1].isspace() else " " + after)
    time.sleep(0.3)
    if any(want in _norm(o["text"]) for o in _mention_zone(b) if o["text"] not in baseline):
        b.press_key("escape")
        time.sleep(0.4)
    if glue:
        # The list is shut now; drop the closing space so the punctuation follows the name.
        for _ in range(2):  # Viber may have added its own space after the mention too
            if b._value().translate(_ISOLATES).endswith((" ", " ")):
                b.press_key("backspace")
                time.sleep(0.3)
        if not mention_inserted(b._value(), before, display_name):
            # Backspace took the mention with it: start over, keeping the space.
            b.delete()
            return send_with_mention(before, display_name, " " + after, quote_n)
        b.type_into_focus(after)
        time.sleep(0.3)
    value = b._value()
    if not mention_inserted(value, before, display_name, after):
        b.delete()
        raise UIError(f"The mention changed before sending ({value!r}); nothing sent.")
    b.click_send()
    _poll(lambda: b._value() == "", timeout=2.5, first=0.15)  # the box clears on send
    return {"sent": True, "mentioned": display_name, "text": value}


def mention_opens_number(display_name: str, number: str) -> bool | None:
    """Click the newest sent @display_name in the open chat and check that the chat it opens
    (the person's 1:1 chat: number under the name, or in the "not in your contact list" banner)
    shows `number`. None when it can't be told. Leaves that 1:1 chat open."""
    want, digits = _norm(display_name), number[-10:]

    def shows_number(obs):
        return any(digits in "".join(ch for ch in o["text"].translate(_DIGIT_FOLD) if ch.isdigit())
                   for o in obs)

    obs = [o for o in _ocr_px() if o["x"] > o.get("SB", o["W"] * 0.28)]
    lines = [o for o in obs if "@" in o["text"] and want in _norm(o["text"])]
    if not lines:
        return None
    line = max(lines, key=lambda o: o["y"])  # newest
    before = shows_number(obs)
    t = line["text"]
    at = t.index("@")
    # Click the middle of the "@name" token, estimating its x from its share of the line.
    frac = (at + (len(display_name) + 1) / 2) / max(1, len(t))
    b = _backend()
    b.click_at(line["x"] + line["w"] * min(frac, 0.95), line["y"] + line["h"] / 2)
    after = bool(_poll(lambda: shows_number(_ocr_px()), timeout=2.0, first=0.3, interval=0.2))
    return None if before else after  # already on screen before the click: inconclusive


def mention(display_name: str, greeting: str = "Hello", number: str = "", after: str = "") -> dict:
    """Send '<greeting> @<display_name><after>' in the open chat (the person is notified). With
    `number`, also check the sent mention opens that number's chat ("verified_number") —
    which leaves that 1:1 chat open, not the group."""
    sent = send_with_mention(f"{greeting} ", display_name, after)
    if number:
        sent["verified_number"] = mention_opens_number(display_name, number)
    return sent


def sender_above(lines: list[dict], bubble_rect, floor_y: float) -> str:
    """The sender name Viber draws at the top of someone else's bubble in group chats, found in
    OCR lines (pixel rects) between `floor_y` (the previous message's bottom) and the message text.
    A link card can sit between the name and the text, so the whole gap is searched; the name is
    the topmost line aligned with the text's left edge (card text is indented). "" if none."""
    left, top = bubble_rect[0], bubble_rect[1]
    # Names line up exactly with the text; quoted blocks are indented and hold "Label:" lines.
    candidates = [o for o in lines if floor_y <= o["y"] < top - 2 and abs(o["x"] - left) <= 5
                  and ":" not in o["text"]
                  and len(o["text"]) > 2 and not re.search(r"\d{1,2}:\d{2}", o["text"])
                  and "http" not in o["text"].lower()]
    return min(candidates, key=lambda o: o["y"])["text"].strip() if candidates else ""


def message_sender(n: int) -> str:
    """Who posted message n of the open chat ("" for your own messages or when not shown)."""
    elements = _backend().elements()
    msgs = _messages(elements)
    msg = next((m for m in msgs if m["n"] == n), None)
    if msg is None:
        raise UIError(f"No visible message n={n}.")
    if msg["mine"]:
        return ""
    earlier = [m["rect"][3] for m in msgs if m["rect"][3] <= msg["rect"][1]]
    # Never above the chat's header bar (its title would otherwise look like a sender name).
    pane = next((e for e in elements if e["class"] == "StackView"), None)
    header_bottom = pane["rect"][1] + 72 if pane else 0
    floor_y = max([header_bottom] + earlier) if earlier else max(header_bottom, msg["rect"][1] - 220)
    return sender_above(_ocr_px(), msg["rect"], floor_y)


def _named(elements: list[dict], el_type: str, predicate) -> list[dict]:
    return [e for e in elements if e["type"] == el_type and e["name"] and predicate(e["name"].lower())]


_BIDI_MARKS = dict.fromkeys(map(ord, "⁦⁧⁨⁩‎‏‪‫‬‭‮"))


def _menu_label(name: str) -> str:
    """Lowercased menu label without bidi isolate/embedding marks (Viber wraps contact names in
    U+2068/U+2069) and with whitespace (incl. NBSP) collapsed."""
    return " ".join(name.translate(_BIDI_MARKS).split()).lower()


def _is_delete_for_myself(name: str) -> bool:
    return "myself" in _menu_label(name)


def _is_delete_for_everyone(name: str) -> bool:
    s = _menu_label(name)
    # Groups: "Delete for all participants"; 1:1s: "Delete for me and ⁨<contact name>⁩"
    # ("Unknown" for unsaved numbers). Never "Delete for myself" / "Delete without a trace".
    return "all participants" in s or "everyone" in s or s.startswith("delete for me and ")


def _delete_menu_pick(names: list[str], for_everyone: bool) -> str | None:
    """Which message-menu item to click to delete. Older 1:1 menus offer a plain "Delete" (the
    everyone/myself choice comes after); group chats list "Delete for myself", "Delete without
    a trace" and "Delete for all participants" directly, and 1:1 chats now list "Delete for
    myself", "Delete without a trace" and "Delete for me and <contact>". None = nothing
    suitable."""
    plain = [s for s in names if _menu_label(s) == "delete"]
    if plain:
        return plain[0]
    if for_everyone:
        wanted = [s for s in names if _is_delete_for_everyone(s)]
    else:
        wanted = [s for s in names if _is_delete_for_myself(s)]
    return wanted[0] if wanted else None


def unsend_message(text: str | None = None, n: int | None = None,
                   for_everyone: bool = True) -> dict:
    """Delete one of MY messages in the open chat, chosen by exact/unique text or by n."""
    b = _backend()
    msgs = _messages(b.elements())
    mine = [m for m in msgs if m["mine"]]
    if n is not None:
        target = next((m for m in msgs if m["n"] == n), None)
        if target is None:
            raise UIError(f"No visible message n={n}. Call list_messages to see the numbers.")
        if not target["mine"]:
            raise UIError("That message isn't yours — only your own messages can be unsent.")
    elif text:
        wanted = text.strip()
        exact = [m for m in mine if m["text"].strip() == wanted]
        partial = [m for m in mine if wanted.lower() in m["text"].lower()]
        matches = exact or partial
        if not matches:
            raise UIError("None of your visible messages match that text (scroll_chat up if it's older).")
        if len(matches) > 1:
            raise UIError(f"{len(matches)} of your messages match; pass n= from list_messages instead.")
        target = matches[0]
    else:
        raise UIError("Say which message: text=... or n=... (see list_messages).")

    b.click_at(*_press_point(target), button="right")
    time.sleep(0.8)

    # Only the context-menu item (MenuItem), never the info panel's "Delete" chat button.
    menu = _named(b.elements(), "MenuItem", lambda s: _menu_label(s).startswith("delete"))
    names = [e["name"] for e in menu]
    pick = _delete_menu_pick(names, for_everyone)
    if pick is None:
        b.press_key("escape")
        if any(_is_delete_for_myself(s) for s in names):
            raise UIError("Viber only offers 'Delete for myself' — the message is too old to unsend "
                          "for everyone. Nothing was deleted.")
        raise UIError("Viber didn't offer Delete for that message.")
    item = next(e for e in menu if e["name"] == pick)
    dl, dt, dr, db = item["rect"]
    b.click_at((dl + dr) / 2, (dt + db) / 2)
    time.sleep(1.0)

    # Group chats put the choice in the menu itself (picked above). 1:1 chats show a plain
    # "Delete", then ask "for everyone" vs "for myself"; My Notes deletes immediately.
    elements = b.elements()
    everyone = [e for e in elements if e["name"] and _is_delete_for_everyone(e["name"])]
    myself = [e for e in elements if e["name"] and _is_delete_for_myself(e["name"])]
    plain = _menu_label(pick) == "delete"
    choice = None if plain else item
    if plain and (everyone or myself):
        options = everyone if for_everyone else myself
        if not options:
            b.press_key("escape")
            raise UIError("Viber only offers 'Delete for myself' — the message is too old to unsend "
                          "for everyone. Nothing was deleted.")
        choice = options[0]
        cl, ct, cr, cb = choice["rect"]
        b.click_at((cl + cr) / 2, (ct + cb) / 2)
        time.sleep(1.2)

    after = _messages(b.elements())
    still_there = any(m["mine"] and m["text"] == target["text"] and m["kind"] == target["kind"]
                      and abs(m["rect"][1] - target["rect"][1]) < 4 for m in after)
    return {
        "unsent": True,
        "message": target["text"] or "(image)",
        "mode": choice["name"] if choice else "deleted",
        "verified": not still_there,
    }
