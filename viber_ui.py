"""Computer-use style control of Viber Desktop, plus message-level actions built on it.

Platform-neutral: talks to the backend's screenshot/click/key/type/scroll/elements primitives.
All coordinates exposed to callers are pixels in the image `screenshot()` returns.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

from backends import get_backend

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
    mid_x = (chat["rect"][0] + chat["rect"][2]) / 2
    by_parent: dict[int, list[dict]] = {}
    for el in elements:
        if el["parent"] is not None:
            by_parent.setdefault(el["parent"], []).append(el)
    out = []
    for i, el in enumerate(elements):
        if el["class"] != "FeedDelegate":
            continue
        kids = by_parent.get(i, [])
        text_el = next((k for k in kids if k["class"].startswith("TextEditItem") and k["text"]), None)
        image_el = next((k for k in kids if k["class"] == "RemixImageButton"), None)
        anchor = text_el or image_el
        if anchor is None:
            continue  # date separators, system notices without text, etc.
        rect = anchor["rect"]
        # Own bubbles are right-aligned; an image's Remix button sits at the bubble's left edge.
        mine = (rect[2] if text_el else rect[0]) > mid_x
        out.append({
            "kind": "image" if image_el else "text",
            "text": text_el["text"] if text_el else "",
            "mine": mine,
            "rect": rect,
            "image_rect": image_el["rect"] if image_el else None,
        })
    out.sort(key=lambda m: m["rect"][1])
    for n, m in enumerate(reversed(out), start=1):
        m["n"] = n  # 1 = newest visible
    return out


def list_messages() -> dict:
    msgs = _messages(_backend().elements())
    return {
        "count": len(msgs),
        "note": "Only messages currently on screen. n=1 is the newest. Use scroll_chat for older ones.",
        "messages": [{"n": m["n"], "from": "me" if m["mine"] else "them", "kind": m["kind"],
                      "text": m["text"]} for m in msgs],
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
    caption = next((e for e in elements if e["type"] == "Edit" and e["class"] == "TextArea"), None)
    if caption is None or caption["parent"] is None:
        return None
    overlay = elements[caption["parent"]]["parent"]  # caption → ScrollView → overlay
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


def send_files(paths: list[str], caption: str = "", dry_run: bool = False) -> dict:
    """Attach local files to the open chat and send them together (photos, PDFs, videos, ...)."""
    files = [Path(p).expanduser().resolve() for p in paths]
    if not files:
        raise UIError("No files given.")
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise UIError(f"File(s) not found: {', '.join(missing)}")
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
    newest = next((m for m in reversed(_messages(b.elements()))), None)
    return {"sent": True, "files": [f.name for f in files], "caption": caption,
            "verified": bool(newest and newest["mine"])}


def _wait_preview():
    for _ in range(30):
        preview = _preview(_backend().elements())
        if preview:
            return preview
        time.sleep(0.3)
    raise UIError("Viber's file preview didn't appear.")


def _named(elements: list[dict], el_type: str, predicate) -> list[dict]:
    return [e for e in elements if e["type"] == el_type and e["name"] and predicate(e["name"].lower())]


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

    l, t, r, bt = target["rect"] if target["kind"] == "text" else target["image_rect"]
    cx, cy = ((l + r) / 2, (t + bt) / 2) if target["kind"] == "text" else (l + 60, t - 60)
    b.click_at(cx, cy, button="right")
    time.sleep(0.8)

    # Only the context-menu item (MenuItem), never the info panel's "Delete" chat button.
    delete = _named(b.elements(), "MenuItem", lambda s: s == "delete")
    if not delete:
        b.press_key("escape")
        raise UIError("Viber didn't offer Delete for that message.")
    dl, dt, dr, db = delete[0]["rect"]
    b.click_at((dl + dr) / 2, (dt + db) / 2)
    time.sleep(1.0)

    # Real chats ask "for everyone" vs "for myself"; My Notes deletes immediately.
    elements = b.elements()
    everyone = [e for e in elements if e["name"] and "everyone" in e["name"].lower()]
    myself = [e for e in elements if e["name"] and "myself" in e["name"].lower()]
    choice = None
    if everyone or myself:
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
