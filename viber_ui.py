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
    own_left = chat["rect"][0] + (chat["rect"][2] - chat["rect"][0]) * 0.25
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


def _press_point(m: dict) -> tuple[float, float]:
    """Where to click/right-click a message: its text, or the picture above "Remix image"."""
    if m["kind"] == "image" and not m["text"]:
        l, t, _, _ = m["image_rect"]
        return l + 60, t - 60
    return _mid(m["rect"])


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


def reply_to_message(n: int, text: str, send: bool = False) -> dict:
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


def _ocr_px() -> list[dict]:
    """OCR observations with pixel rects (screenshot coordinates)."""
    b = _backend()
    img = b.screenshot()
    out = []
    for o in b.observe():
        out.append({"text": o["text"].strip(), "x": o["x"] * img.width, "y": o["y"] * img.height,
                    "w": o["w"] * img.width, "h": o["h"] * img.height, "W": img.width,
                    "H": img.height})
    return out


def _ocr_center(o: dict) -> tuple[float, float]:
    return o["x"] + o["w"] / 2, o["y"] + o["h"] / 2


def _norm(s: str) -> str:
    """Lowercase alphanumerics with OCR look-alikes folded (I/l/1 → i, 0 → o)."""
    s = "".join(ch for ch in s.lower() if ch.isalnum())
    return s.translate(str.maketrans({"l": "i", "1": "i", "0": "o"}))


def _left_column(obs: list[dict]) -> list[dict]:
    return [o for o in obs if o["x"] < o["W"] * 0.3]


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


def _select_member(name: str = "", number: str = "") -> str:
    """Search the new-group picker and tick one contact. Returns the row's display name."""
    _picker_search(number.replace("+63", "0") if number else name)
    digit_fold = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})

    def find(rows):
        if number:
            want = number[-10:]
            return next((o for o in rows if want in "".join(
                ch for ch in o["text"].translate(digit_fold) if ch.isdigit())), None)
        return next((o for o in rows if _norm(o["text"]) == _norm(name)), None)

    hit, obs, rows = None, [], []
    for attempt in range(3):  # results can render a moment late
        obs = _ocr_px()
        rows = [o for o in _left_column(obs) if o["y"] > obs[0]["H"] * 0.12] if obs else []
        hit = find(rows)
        if hit:
            break
        time.sleep(1.0)
    if hit is None:
        raise UIError(f"Couldn't find '{name or number}' in Viber's contacts for the group.")
    # The display name sits on the line above a number; the row is clickable anywhere.
    display = name
    if number:
        above = [o for o in rows if o["y"] < hit["y"] and hit["y"] - o["y"] < hit["h"] * 3
                 and abs(o["x"] - hit["x"]) < 30 and len(o["text"]) > 1]  # not the ○ tick
        display = max(above, key=lambda o: o["y"])["text"] if above else number
    before = _invite_count(obs)
    _backend().click_at(*_ocr_center(hit))
    time.sleep(0.8)
    after = _invite_count(_ocr_px())
    if before is not None and after is not None and after != before + 1:
        raise UIError(f"Selecting '{name or number}' didn't register (invited {before} → {after}).")
    return display


def _open_new_group() -> None:
    """Open Viber's New group screen (pencil → New group)."""
    b = _backend()
    els = b.elements()
    search = next((e for e in els if e["class"] == "SearchItem"), None)
    if search is None:
        raise UIError("Viber's main chat list isn't showing (close any panel first).")
    sy = _mid(search["rect"])[1]
    pencil = min((e for e in els if e["type"] == "Button" and e["rect"][0] >= search["rect"][2] - 2
                  and abs(_mid(e["rect"])[1] - sy) < 15), key=lambda e: e["rect"][0], default=None)
    if pencil is None:
        raise UIError("Couldn't find Viber's new-chat (pencil) button.")
    b.click_at(*_mid(pencil["rect"]))
    hits, _ = _wait_ocr(lambda t: t.lower() == "new group")
    if not hits:
        raise UIError("Viber's 'New group' option didn't appear.")
    b.click_at(*_ocr_center(hits[0]))
    time.sleep(1.2)


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
        fold = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})
        want = number[-10:]
        for _ in range(3):
            obs = _ocr_px()
            rows = [o for o in _left_column(obs) if o["y"] > obs[0]["H"] * 0.2] if obs else []
            if any("no results" in o["text"].lower() for o in rows):
                return None
            hit = next((o for o in rows if want in "".join(
                ch for ch in o["text"].translate(fold) if ch.isdigit())), None)
            if hit:
                above = [o for o in rows if o["y"] < hit["y"] and hit["y"] - o["y"] < hit["h"] * 3
                         and abs(o["x"] - hit["x"]) < 30 and len(o["text"]) > 1]
                return max(above, key=lambda o: o["y"])["text"] if above else number
            time.sleep(1.0)
        return None
    finally:
        _close_new_group()


def create_group(gc_name: str, members: list[str], client_number: str, self_name: str = "") -> dict:
    """Create a Viber group named `gc_name` with `members` (contact names, in order) and then
    the client's number. Check the number with number_on_viber first. Leaves the new group open."""
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
        for member in members:
            if self_name and _norm(member) == _norm(self_name):
                continue  # the creator is in the group automatically
            _select_member(name=member)
            added.append(member)
        client_display = _select_member(number=client_number)
        added.append(f"{client_display} ({client_number})")
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
    hits, _ = _wait_ocr(lambda t: bool(head) and _norm(t).startswith(head), timeout=25,
                        left_only=False)
    return {"created": bool(hits), "gc_name": gc_name, "members_added": added,
            "client_display_name": client_display}


def mention(display_name: str, greeting: str = "Hello") -> dict:
    """Send '<greeting> @<display_name>' in the open chat, picking the person from Viber's
    mention list so they get notified."""
    b = _backend()
    b.click_compose()
    b.delete()
    # Typing the start of the name filters Viber's mention list (it only shows a few rows).
    first = display_name.split()[0] if display_name.split() else ""
    b.type_into_focus(f"{greeting} @{first[:4]}")
    time.sleep(1.5)
    compose = next(e for e in b.elements() if e["type"] == "Edit" and e["class"].startswith("QQuickTextEdit"))
    top = compose["rect"][1]
    obs = _ocr_px()
    picks = [o for o in obs if top - 400 < o["y"] < top - 5 and o["x"] > obs[0]["W"] * 0.28
             and _norm(o["text"]) == _norm(display_name)] if obs else []
    if not picks:
        b.delete()
        raise UIError(f"'{display_name}' isn't in this chat's mention list.")
    b.click_at(*_ocr_center(max(picks, key=lambda o: o["y"])))  # nearest the box
    time.sleep(0.6)
    value = b._value() if hasattr(b, "_value") else ""
    if "@" not in value or _norm(display_name) not in _norm(value):
        b.delete()
        raise UIError("The mention didn't insert; nothing sent.")
    b.click_send()
    time.sleep(1.2)
    return {"sent": True, "mentioned": display_name}


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
