"""Computer-use style control of Viber Desktop, plus message-level actions built on it.

Platform-neutral: talks to the backend's screenshot/click/key/type/scroll/elements primitives.
All coordinates exposed to callers are pixels in the image `screenshot()` returns.
"""

from __future__ import annotations

import io
import re
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
    own_left = chat["rect"][0] + (chat["rect"][2] - chat["rect"][0]) * 0.25
    view = _feed_view(elements, chat)
    by_parent: dict[int, list[dict]] = {}
    for el in elements:
        if el["parent"] is not None:
            by_parent.setdefault(el["parent"], []).append(el)
    def make(text_el, image_el, likes):
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
                out.append(make(text_el, image_el, [k for k in kids if k["class"] == "QQuickLoader"]))
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
    for _ in range(4):
        button = scroll_to_bottom_button(b.elements())
        if button is None:
            break
        b.click_at(*_mid(button["rect"]))
        clicked = True
        time.sleep(1.0)
    b.scroll("chat", -60)  # settle at the very bottom
    time.sleep(0.6)
    return {"at_latest": scroll_to_bottom_button(b.elements()) is None, "used_button": clicked}


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
    # Viber uploads after the preview closes; failed photos raise a "Your last N messages were
    # not sent" banner. Wait for it to settle and press its Resend button ourselves.
    resends = _settle_and_resend(len(files))
    newest = next((m for m in reversed(_messages(b.elements()))), None)
    result = {"sent": True, "files": [f.name for f in files], "caption": caption,
              "verified": bool(newest and newest["mine"]) and not resends["still_unsent"],
              "resend_attempts": resends["attempts"]}
    if resends["still_unsent"]:
        result["note"] = ("Viber still shows 'messages were not sent' after resending — send the "
                          "failed photos again (fresh files, a few at a time).")
    return result


def _resend_button(elements: list[dict]):
    """The red 'messages were not sent' banner's Resend / Resend all button, or None."""
    return next((e for e in elements if e["type"] == "Button" and e["class"] == "BannerButton"
                 and e["name"].lower().startswith("resend")), None)


def _settle_and_resend(n_files: int, max_attempts: int = 2) -> dict:
    """Give the upload time to finish, then press Resend while the not-sent banner shows."""
    b = _backend()
    settle = min(4 + 1.5 * n_files, 30)
    attempts = 0
    while True:
        time.sleep(settle)
        button = _resend_button(b.elements())
        if button is None:
            return {"attempts": attempts, "still_unsent": False}
        if attempts >= max_attempts:
            return {"attempts": attempts, "still_unsent": True}
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
    time.sleep(0.8)
    items = [e for e in b.elements() if e["type"] == "MenuItem" and e["name"] != "System"]
    names = [e["name"] for e in items]
    choice = next((e for e in items if e["name"].lower() == action.strip().lower()), None)
    if choice is None:
        b.press_key("escape")
        raise UIError(f"'{action}' isn't offered for that message. Available: {', '.join(names)}")
    before = {e["name"] for e in b.elements() if e["type"] == "Button" and e["name"]}
    b.click_at(*_mid(choice["rect"]))
    time.sleep(0.8)
    result = {"message": n, "chose": choice["name"], "menu": names}
    # Some actions (e.g. Pin) open a confirmation dialog; surface its buttons.
    dialog = sorted({e["name"] for e in b.elements() if e["type"] == "Button" and e["name"]} - before)
    if dialog:
        result["confirmation_dialog_buttons"] = dialog
        result["note"] = ("Viber is asking for confirmation. Click one of these buttons with "
                          "list_elements/click_element (or close_viewer to cancel).")
    return result


def _compose_and_maybe_send(text: str, send: bool) -> dict:
    """Replace the message box content with `text`; click send if asked."""
    b = _backend()
    b.clipboard_set(text)
    b.click_compose()
    b.delete()
    b.paste()
    if not send:
        return {"sent": False, "drafted": text}
    b.click_send()
    time.sleep(1.2)
    return {"sent": True, "text": text}


def reply_to_message(n: int, text: str, send: bool = True) -> dict:
    """Quote-reply to message n with `text` (draft unless send=True)."""
    if not text:
        raise UIError("Reply text is empty.")
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
    after = next((m for m in _messages(_backend().elements()) if m["mine"] and m["text"] == text), None)
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
    pane = next((e for e in elements if e["class"] == "SplitView"), None)
    return pane["rect"][0] if pane else width * 0.28


def _ocr_px() -> list[dict]:
    """OCR observations with pixel rects (screenshot coordinates). Each carries SB, the sidebar's
    right edge, so sidebar text can be told apart from the chat pane's."""
    b = _backend()
    img = b.screenshot()
    sidebar_right = _sidebar_right(b.elements(), img.width)
    out = []
    for o in b.observe():
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


def _wait_ocr(predicate, timeout: float = 6.0, left_only: bool = True):
    deadline = time.time() + timeout
    while time.time() < deadline:
        obs = _ocr_px()
        hits = [o for o in (_left_column(obs) if left_only else obs) if predicate(o["text"])]
        if hits:
            return hits, obs
        time.sleep(0.5)
    return [], []


def _invite_count(obs: list[dict]) -> int | None:
    for o in _left_column(obs):
        t = o["text"].replace(" ", "")
        if "/250" in t:
            digits = t.split("/250")[0][-3:]
            num = "".join(ch for ch in digits if ch.isdigit())
            return int(num) if num else None
    return None


def _picker_search(query: str) -> None:
    """Type `query` into the new-group screen's "Search name or number" box."""
    b = _backend()
    b.click_search()
    b.delete()
    b.type_into_focus(query)
    time.sleep(1.6)


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


def _select_member(name: str = "", number: str = "") -> str:
    """Search the new-group picker and tick one contact. Returns the row's display name."""
    _picker_search(number.replace("+63", "0") if number else name)

    def find(rows):
        if number:
            return find_number_row(rows, number)[0]
        return next((o for o in rows if _norm(o["text"]) == _norm(name)), None)

    hit, obs, rows = None, [], []
    for _ in range(3):  # results can render a moment late
        obs = _ocr_px()
        rows = [o for o in _left_column(obs) if o["y"] > obs[0]["H"] * 0.12] if obs else []
        hit = find(rows)
        if hit:
            break
        time.sleep(1.0)
    if hit is None:
        raise UIError(f"Couldn't find '{name or number}' in Viber's contacts for the group.")
    display = find_number_row(rows, number)[1] if number else name
    before = _invite_count(obs)
    b = _backend()
    # Click the row text first; if the count doesn't rise, click the row's select circle.
    targets = [_ocr_center(hit), select_circle_point(b.elements(), hit, bool(number))]
    after = before
    for point in targets:
        if point is None:
            continue
        b.click_at(*point)
        for _ in range(6):  # the count can take a moment to update
            time.sleep(0.5)
            after = _invite_count(_ocr_px())
            if before is None or after is None or after == before + 1:
                return display
    raise UIError(f"Selecting '{name or number}' didn't register (invited {before} → {after}).")


def select_circle_point(elements: list[dict], line: dict, line_is_number: bool):
    """Where a picker row's select circle (○) is. It sits at the search box's right edge (the
    picker list and the search box share the sidebar's fixed width), vertically between the
    row's name line and its number line — so just below a name line, just above a number line."""
    search = next((e for e in elements if e["type"] == "Edit" and e["class"].startswith("TextFieldItem")), None)
    if search is None:
        return None
    center_y = line["y"] + line["h"] / 2
    return search["rect"][2] - 5, center_y + (-15 if line_is_number else 6)


def new_group_screen_state(left_texts: list[str]) -> str:
    """What the sidebar shows: "group_form" (New group screen with "Group name*"), "menu" (the
    new-chat panel with its "New group" option), or "chats" (anything else)."""
    low = [t.strip().lower() for t in left_texts]
    if any(t.startswith("group name") for t in low):
        return "group_form"
    if "new group" in low:
        return "menu"
    return "chats"


_ALREADY_GROUP = "already have a group together"


def existing_group_from_dialog(texts: list[str]) -> str | None:
    """Viber's "You already have a group together!" dialog (Start chat with members who already
    share a group): the existing group's name (OCR, often truncated "..."), or None if no dialog."""
    low = [t.strip().lower() for t in texts]
    if not any(_ALREADY_GROUP in t for t in low):
        return None
    start = next(i for i, t in enumerate(low) if _ALREADY_GROUP in t)
    for t, l in zip(texts[start + 1:], low[start + 1:]):
        if l and l not in ("open", "create another group", "x", "×"):
            return t.strip()
    return ""


def _open_new_group() -> None:
    """Open Viber's New group screen (pencil → New group). Looks at the screen before each click,
    because the pencil TOGGLES the new-chat panel: clicking it while the panel is already open
    (e.g. left over from an earlier attempt) closes it."""
    b = _backend()
    texts: list[str] = []
    for _ in range(4):
        obs = _left_column(_ocr_px())
        texts = [o["text"] for o in obs]
        state = new_group_screen_state(texts)
        if state == "group_form":
            return
        if state == "menu":
            option = next(o for o in obs if o["text"].strip().lower() == "new group")
            b.click_at(*_ocr_center(option))
            time.sleep(1.2)
            continue
        els = b.elements()
        search = next((e for e in els if e["class"] == "SearchItem"), None)
        if search is None:
            b.press_key("escape")  # some panel/dialog is covering the chat list
            time.sleep(0.6)
            continue
        sy = _mid(search["rect"])[1]
        pencil = min((e for e in els if e["type"] == "Button" and e["rect"][0] >= search["rect"][2] - 2
                      and abs(_mid(e["rect"])[1] - sy) < 15), key=lambda e: e["rect"][0], default=None)
        if pencil is None:
            raise UIError("Couldn't find Viber's new-chat (pencil) button.")
        b.click_at(*_mid(pencil["rect"]))
        _wait_ocr(lambda t: t.lower() == "new group", timeout=3.0)
    raise UIError("Couldn't open Viber's New group screen. The sidebar showed: "
                  + " | ".join(texts[:8]))


def _close_new_group() -> None:
    """Leave the New group screen without creating anything."""
    b = _backend()
    _picker_search("")
    for _ in range(2):
        back = next((e for e in b.elements() if e["type"] == "Button"
                     and e["class"] == "IconButton" and e["rect"][0] < 60 and e["rect"][1] < 80), None)
        if back:
            b.click_at(*_mid(back["rect"]))
            time.sleep(0.8)


def number_on_viber(number: str) -> str | None:
    """Does this phone number have a Viber account? Checked in the New group picker (the main
    search offers "Start a conversation" for any number, so it can't tell). Returns the
    account's display name, or None. Creates nothing."""
    _open_new_group()
    try:
        _picker_search(number.replace("+63", "0"))
        for _ in range(3):
            obs = _ocr_px()
            rows = [o for o in _left_column(obs) if o["y"] > obs[0]["H"] * 0.2] if obs else []
            if any("no results" in o["text"].lower() for o in rows):
                return None
            hit, display = find_number_row(rows, number)
            if hit:
                return display
            time.sleep(1.0)
        return None
    finally:
        _close_new_group()


def create_group(gc_name: str, members: list[str], client_number: str, self_name: str = "") -> dict:
    """Create a Viber group named `gc_name`: the client's number first, then `members` (contact
    names, in order). Check the number with number_on_viber first. Leaves the new group open."""
    b = _backend()
    _open_new_group()

    name_box = next((e for e in b.elements() if e["type"] == "Edit" and e["class"] == "TextField"), None)
    if name_box is None:
        raise UIError("The new-group 'Group name' box didn't appear.")
    b.click_at(*_mid(name_box["rect"]))
    b.type_into_focus(gc_name)
    time.sleep(0.4)
    typed = next((e["text"] for e in b.elements() if e["type"] == "Edit" and e["class"] == "TextField"), "")
    if typed != gc_name:
        raise UIError(f"Group name didn't take (got {typed!r}). Nothing was created; close the panel.")

    added = []
    try:
        # Client first: if their number can't be added, stop before touching the staff.
        client_display = _select_member(number=client_number)
        added.append(f"{client_display} ({client_number})")
        for member in members:
            if self_name and _norm(member) == _norm(self_name):
                continue  # the creator is in the group automatically
            _select_member(name=member)
            added.append(member)
    except UIError:
        _close_new_group()  # leave the half-filled screen without creating anything
        raise

    _picker_search("")
    start = next((e for e in b.elements() if e["type"] == "Button" and e["name"] == "Start chat"), None)
    if start is None:
        raise UIError("Couldn't find 'Start chat'. Nothing was created; close the panel.")
    prev = b.foreground()
    b.click_at(*_mid(start["rect"]))
    b.hold_foreground(prev, 3.0)  # Viber activates itself when the group is created
    # Header and chat-list titles are truncated ("TESTCLIENT-WH-(300S..."), so compare a prefix.
    head = _norm(gc_name)[:10]
    hits, obs = _wait_ocr(lambda t: (bool(head) and _norm(t).startswith(head))
                          or _ALREADY_GROUP in t.lower(), timeout=25, left_only=False)
    existing = existing_group_from_dialog([o["text"] for o in obs])
    if existing is not None:
        # The client already shares a group with the staff: nothing was created. Close the
        # dialog and the New group screen (never "Create Another Group" — that duplicates).
        b.press_key("escape")
        time.sleep(0.6)
        _close_new_group()
        return {"created": False, "existing_group": existing, "gc_name": gc_name,
                "members_added": added, "client_display_name": client_display}
    return {"created": bool(hits), "gc_name": gc_name, "members_added": added,
            "client_display_name": client_display}


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
    return [o for o in _ocr_px() if top - 400 < o["y"] < top - 5 and o["x"] > o.get("SB", o["W"] * 0.28)]


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
    query = f"@{first[:4]}"
    baseline = {o["text"] for o in _mention_zone(b)}  # chat text that was there before the list
    b.type_into_focus(before + query)
    time.sleep(1.5)
    rows = [o for o in _mention_zone(b) if o["text"] not in baseline]
    if not any(_norm(o["text"]) == want for o in rows):
        b.delete()
        raise UIError(f"'{display_name}' isn't in this chat's mention list.")
    # Which row is highlighted first can't be read from OCR, so try each until the inserted
    # mention is exactly this name.
    for downs in range(min(len(rows), 8)):
        if downs:  # undo the wrong pick, keeping `before` (and any quote) in place
            b.press_key("backspace", max(1, len(b._value().translate(_ISOLATES)) - len(before)))
            b.type_into_focus(query)
            time.sleep(1.2)
            b.press_key("down", downs)
        b.press_key("tab")
        time.sleep(0.5)
        if mention_inserted(b._value(), before, display_name):
            break
    else:
        b.delete()
        raise UIError(f"Couldn't insert a mention of exactly '{display_name}'; nothing sent.")
    # A space right after the mention closes the list; sending while it's open can swap the pick.
    b.type_into_focus(after if after[:1].isspace() else " " + after)
    time.sleep(0.6)
    if any(want in _norm(o["text"]) for o in _mention_zone(b) if o["text"] not in baseline):
        b.press_key("escape")
        time.sleep(0.4)
    value = b._value()
    if not mention_inserted(value, before, display_name, after):
        b.delete()
        raise UIError(f"The mention changed before sending ({value!r}); nothing sent.")
    b.click_send()
    time.sleep(1.2)
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
    time.sleep(1.5)
    after = shows_number(_ocr_px())
    return None if before else after  # already on screen before the click: inconclusive


def mention(display_name: str, greeting: str = "Hello", number: str = "") -> dict:
    """Send '<greeting> @<display_name>' in the open chat (the person is notified). With
    `number`, also check the sent mention opens that number's chat ("verified_number") —
    which leaves that 1:1 chat open, not the group."""
    sent = send_with_mention(f"{greeting} ", display_name)
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


def _is_delete_for_myself(name: str) -> bool:
    return "myself" in name.lower()


def _is_delete_for_everyone(name: str) -> bool:
    s = name.lower()
    # Groups: "Delete for all participants"; 1:1s: "Delete for me and <contact name>".
    # Never "Delete without a trace".
    return "all participants" in s or "everyone" in s or s.startswith("delete for me and ")


def _delete_menu_pick(names: list[str], for_everyone: bool) -> str | None:
    """Which message-menu item to click to delete. Older 1:1 menus offer a plain "Delete" (the
    everyone/myself choice comes after); group chats list "Delete for myself", "Delete without
    a trace" and "Delete for all participants" directly, and 1:1 chats now list "Delete for
    myself", "Delete without a trace" and "Delete for me and <contact>". None = nothing
    suitable."""
    plain = [s for s in names if s.lower() == "delete"]
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
    menu = _named(b.elements(), "MenuItem", lambda s: s.startswith("delete"))
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
    everyone = [e for e in elements if e["name"] and "everyone" in e["name"].lower()]
    myself = [e for e in elements if e["name"] and "myself" in e["name"].lower()]
    choice = None if pick.lower() == "delete" else item
    if pick.lower() == "delete" and (everyone or myself):
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
