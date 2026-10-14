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
