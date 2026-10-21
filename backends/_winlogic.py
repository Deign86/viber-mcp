"""Pure-Python helpers for the Windows backend (no OS imports, so unit-testable anywhere)."""

from __future__ import annotations

def normalize_viber_url(url: str) -> str:
    """Windows Viber wants the '+' in `number=+63...` percent-encoded."""
    return url.replace("number=+", "number=%2B")


def split_line(words, gap_factor: float = 3.0):
    """Split one OCR line into segments wherever there's a big horizontal gap.

    Windows OCR can join the sidebar and the conversation into a single "line" when they sit
    at the same height; the domain logic filters by x-position, so they must be separate.

    `words`: iterable of (text, x, y, w, h) in pixels, left to right.
    Returns a list of (text, x, y, w, h).
    """
    segs: list[dict] = []
    cur: dict | None = None
    for text, x, y, w, h in words:
        if cur is None:
            cur = {"text": text, "l": x, "t": y, "r": x + w, "b": y + h, "h": h}
            continue
        gap = x - cur["r"]
        if gap > gap_factor * max(h, cur["h"]):
            segs.append(cur)
            cur = {"text": text, "l": x, "t": y, "r": x + w, "b": y + h, "h": h}
        else:
            cur["text"] += " " + text
            cur["r"] = max(cur["r"], x + w)
            cur["t"] = min(cur["t"], y)
            cur["b"] = max(cur["b"], y + h)
            cur["h"] = max(cur["h"], h)
    if cur is not None:
        segs.append(cur)
    return [(s["text"], s["l"], s["t"], s["r"] - s["l"], s["b"] - s["t"]) for s in segs]


def to_observations(segments, img_w: float, img_h: float) -> list[dict]:
    """Convert pixel segments to the Backend contract: normalized {text, x, y} (+ w, h)."""
    obs = [
        {"text": t, "x": x / img_w, "y": y / img_h, "w": w / img_w, "h": h / img_h}
        for t, x, y, w, h in segments
        if t.strip()
    ]
    obs.sort(key=lambda o: (o["y"], o["x"]))
    return obs


def ad_close_point(elements: list[dict]) -> tuple[float, float] | None:
    """The ✕ of Viber's "Sponsored" ad box (bottom of the chat list), or None if no ad shows.
    While it shows, Qt's UIA tree loops back on itself and drops the chat pane, so the message
    box, send button and messages can't be found ("No chat is open"). The ✕ sits 24px in from the
    ad's right edge, 16px below its top; the flag (report ad) is further left — never click that."""
    ad = next((e for e in elements if e["type"] == "Document" and e["class"] == "QObject"
               and 280 <= e["rect"][2] - e["rect"][0] <= 320
               and 230 <= e["rect"][3] - e["rect"][1] <= 270), None)
    return (ad["rect"][2] - 24, ad["rect"][1] + 16) if ad else None


def promo_close_point(elements: list[dict]) -> tuple[float, float] | None:
    """The ✕ of Viber's "Discover a Premium Experience" (Viber Plus) pop-up, or None. Closing the
    Sponsored ad opens it, and it covers the chat so every later click misses. Recognised by its
    "Take me there" button; the ✕ is the pop-up's icon button (never "Take me there")."""
    for i, e in enumerate(elements):
        if e["type"] != "Window" or e["class"] != "QQuickPopupItem":
            continue
        kids = [k for k in elements if k["parent"] == i]
        if any(k["type"] == "Button" and k["name"].strip().lower() == "take me there" for k in kids):
            close = next((k for k in kids if k["type"] == "Button" and k["class"] == "IconButton"), None)
            if close:
                r = close["rect"]
                return (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return None
