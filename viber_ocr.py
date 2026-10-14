"""Read the visible Viber conversation via the platform backend's window capture + OCR.

Platform-neutral: all capture/OCR lives in the backend; this module just formats the
observations into the shapes the tools return.
"""

from __future__ import annotations

from backends import OCRError, get_backend

__all__ = ["OCRError", "observe", "read_open_chat", "read_chat_list"]

# The chat-list sidebar occupies roughly the left third of the window.
_SIDEBAR_MAX_X = 0.30


def observe() -> list[dict]:
    """Return OCR observations of the Viber window: [{"text", "x", "y"}], top-to-bottom."""
    return get_backend().observe()


def read_open_chat() -> dict:
    """OCR the visible Viber window; return its recognized lines and joined text."""
    obs = observe()
    lines = [o["text"] for o in obs]
    return {"line_count": len(lines), "lines": lines, "text": "\n".join(lines)}


def read_chat_list() -> dict:
    """Return only the left-sidebar (chat list) text — names, previews, times, unread counts.

    Filters OCR to the left column so the open conversation's text isn't mixed in.
    """
    lines = [o["text"] for o in observe() if o["x"] < _SIDEBAR_MAX_X]
    return {"line_count": len(lines), "lines": lines, "text": "\n".join(lines)}
