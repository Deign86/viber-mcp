"""Read the visible Viber conversation via the platform backend's window capture + OCR.

Platform-neutral: all capture/OCR lives in the backend; this module just formats the
observations into the shapes the tools return.
"""

from __future__ import annotations

import re
import time

from backends import OCRError, get_backend

__all__ = ["OCRError", "observe", "read_open_chat", "read_chat_list", "scroll_and_read",
           "chat_rows", "row_stamp"]

# The chat-list sidebar occupies roughly the left third of the window (a fixed pixel width, so
# the fraction shrinks on wide windows: `_sidebar_fraction` reads the real edge when it can).
_SIDEBAR_MAX_X = 0.30


def observe(region=None) -> list[dict]:
    """Return OCR observations of the Viber window: [{"text", "x", "y"}], top-to-bottom.
    `region` (normalized l, t, r, b) lets the backend OCR only that part — a hint, so filter."""
    return get_backend().observe(region)


def read_open_chat() -> dict:
    """OCR the visible Viber window; return its recognized lines and joined text."""
    obs = observe()
    lines = [o["text"] for o in obs]
    return {"line_count": len(lines), "lines": lines, "text": "\n".join(lines)}


# OCR mangles the colon ("1243 PM", "1-25 PM", "9.49 AM"), so it's optional.
_ROW_TIME = re.compile(r"(\d{1,2}[:.\-]?\d{2}\s*[AP]M|yesterday|monday|tuesday|wednesday|thursday|"
                       r"friday|saturday|sunday|\d{1,2}/\d{1,2}/\d{4})$", re.I)


def row_stamp(text: str) -> str | None:
    """The time/day stamp of a chat-list / search-result row ("11:02 AM", "Yesterday"), ignoring
    a short prefix such as the OCR'd read tick ("W 10:13 AM", "v'/ 12:43 PM"); None if it isn't one."""
    t = text.strip()
    m = _ROW_TIME.search(t)
    return m.group(1) if m and m.start() <= 4 else None


_TICK_WORDS = {"w", "v", "vv", "ww", "v'", "v/", "v'/"}


def _ticked(stamp: dict, line: list[dict]) -> bool:
    """Does the row's stamp line carry the sent/read tick Viber draws only on OUR last message?
    OCR renders it as a short prefix on the stamp ("W 12:28 PM") or as its own tiny word ("w")
    between the name and the time."""
    t = stamp["text"].strip()
    m = _ROW_TIME.search(t)
    if m and m.start() > 0 and t[:m.start()].strip():
        return True
    return any(o is not stamp and o["x"] < stamp["x"] and o["text"].strip().lower() in _TICK_WORDS
               for o in line)


_LIST_TOP_Y = 0.18   # below the search box, folder icons and the All/Unread filter pills
_BADGE_RE = re.compile(r"^\d{1,3}$")


def chat_rows(obs: list[dict], sidebar_right: float = _SIDEBAR_MAX_X) -> list[dict]:
    """Group the sidebar's OCR lines into chat rows: {name, time, preview, unread, last_from, y}.

    A row starts at a line that ends with a time/day stamp near the sidebar's right edge; the
    name is the leftmost text on that line and the preview is the lines under it until the next
    stamp. `unread` is the badge count (a bare number at the right edge, 0 if none). `last_from`
    is "me" when the preview starts with "Me:" or the stamp carries our sent/read tick, else
    "them" — best-effort from OCR. `y` is the name line's normalized top, for clicks. Pure."""
    left = sorted((o for o in obs if o["x"] < sidebar_right and o["y"] > _LIST_TOP_Y),
                  key=lambda o: o["y"])
    stamps = [o for o in left if o["x"] > sidebar_right * 0.55 and row_stamp(o["text"])]
    rows = []
    for i, stamp in enumerate(stamps):
        end = stamps[i + 1]["y"] - 0.012 if i + 1 < len(stamps) else 1.0
        line = [o for o in left if abs(o["y"] - stamp["y"]) < 0.012]
        names = [o for o in line if o["x"] < stamp["x"] and len(o["text"].strip()) > 3]
        first = min(names, key=lambda o: o["x"]) if names else stamp
        below = [o for o in left if stamp["y"] + 0.012 <= o["y"] < end]
        badge = next((o for o in below if o["x"] > sidebar_right * 0.75
                      and _BADGE_RE.match(o["text"].strip())), None)
        preview = " ".join(o["text"].strip() for o in below
                           if o is not badge and o["x"] < sidebar_right * 0.85)
        mine = preview.lower().startswith("me:") or _ticked(stamp, line)
        rows.append({"name": first["text"].strip() if names else "", "time": row_stamp(stamp["text"]),
                     "preview": preview, "unread": int(badge["text"]) if badge else 0,
                     "last_from": "me" if mine else "them", "y": first["y"]})
    return rows


def _sidebar_fraction() -> float:
    """The sidebar's right edge as a fraction of the window, from the chat pane's UIA rect
    (the sidebar is a fixed pixel width); the usual fraction where UIA isn't available."""
    b = get_backend()
    try:
        pane = next((e for e in b.elements() if e["class"] == "SplitView"), None)
        width = b.screenshot().width
    except (NotImplementedError, AttributeError, OCRError):
        return _SIDEBAR_MAX_X
    if pane is None or not width:
        return _SIDEBAR_MAX_X
    return pane["rect"][0] / width


_FILTER_REGION = (0, 0, 0.34, 0.25)
_FOLD = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})


def _click_filter(label: str) -> None:
    """Click one of the chat list's filter pills ("All", "Unread") at its OCR'd position."""
    want = label.lower().translate(_FOLD)
    pill = next((o for o in observe(region=_FILTER_REGION)
                 if o["x"] < 0.2 and o["y"] < 0.22
                 and o["text"].strip().lower().translate(_FOLD) == want), None)
    if pill is None:
        raise OCRError(f"The chat list's '{label}' filter isn't on screen (is the chat list showing?).")
    b = get_backend()
    img = b.screenshot()
    b.click_at((pill["x"] + pill.get("w", 0.03) / 2) * img.width,
               (pill["y"] + pill.get("h", 0.01) / 2) * img.height)
    b.wait_still()


def read_chat_list(unread_only: bool = False) -> dict:
    """The left-sidebar chat list: its raw text lines plus structured rows (`chats`: name, time,
    preview, unread badge, who sent the last message). Filters OCR to the left column so the
    open conversation's text isn't mixed in. With unread_only, switches the list to Viber's
    "Unread" filter for the read and back to "All" afterwards."""
    if unread_only:
        _click_filter("Unread")
    try:
        sidebar = _sidebar_fraction()
        obs = [o for o in observe(region=(0, 0, sidebar + 0.04, 1)) if o["x"] < sidebar]
    finally:
        if unread_only:
            _click_filter("All")
    lines = [o["text"] for o in obs]
    rows = chat_rows(obs, sidebar)
    return {"line_count": len(lines), "lines": lines, "text": "\n".join(lines),
            "chats": [{k: v for k, v in r.items() if k != "y"} for r in rows],
            "filter": "unread" if unread_only else "all"}


def scroll_and_read(area: str, direction: str, amount: int) -> dict:
    """Scroll the open conversation (`area="chat"`) or the chat list (`"chats"`), then return
    what is visible there afterwards. `amount` is mouse-wheel notches (about 3 lines each)."""
    if area not in ("chat", "chats"):
        raise ValueError("area must be 'chat' or 'chats'")
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    amount = max(1, min(int(amount), 50))
    get_backend().scroll(area, amount if direction == "up" else -amount)
    time.sleep(0.8)  # let Viber load and render the newly exposed messages/rows
    result = read_chat_list() if area == "chats" else read_open_chat()
    result["scrolled"] = {"area": area, "direction": direction, "amount": amount}
    return result
