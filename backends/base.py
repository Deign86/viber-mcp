"""Platform backend interface for driving Viber Desktop.

A Backend encapsulates everything OS- and Viber-layout-specific: window capture + OCR,
synthetic input, clipboard, app focus/launch, and the concrete on-screen locations of Viber's
UI elements (compose box, send button, search field, result rows, panel-close button).

The domain logic in `viber_send.py` / `viber_ocr.py` is written entirely against this interface
and never touches pixels, key codes, or OS APIs — so porting to Windows/Linux means writing a new
Backend subclass (plus re-tuning that platform's UI-element locations) without changing the
recipient resolution, search-result scoring, verification, or send pipeline.

Note: the *semantic* clicks (click_compose/send/search/result/panel_close) exist because element
positions differ per OS and per Viber layout; a Windows backend may instead implement them via UI
Automation and ignore coordinates entirely.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class OCRError(Exception):
    """Raised when the Viber window can't be found, captured, or OCR'd."""


class Backend(ABC):
    """Everything platform-specific needed to drive Viber Desktop."""

    name: str = "base"
    # True if type_text/delete/paste return only once the field holds the expected text and
    # click_compose/click_search wait for focus themselves — callers can then skip fixed pauses.
    input_confirmed: bool = False
    # True if an open right-side panel displaces the send button (coordinate-based clicks), so
    # the domain must OCR for the panel and close it before sending.
    panel_shifts_send: bool = True
    # True where an Escape with nothing left to close disturbs the user (macOS hides Viber's
    # window and real key events take the foreground): closing a search then skips the Escape
    # once clearing the box has already closed it.
    avoid_spare_escape: bool = False

    # --- app / process ---------------------------------------------------------------
    def access(self, ask: bool = False) -> dict[str, bool]:
        """The OS permissions this backend needs, {name: granted}; ask=True shows the OS's own
        prompts for the missing ones (at server start, so the first request doesn't fail on
        them). None are needed by default."""
        return {}

    @abstractmethod
    def app_running(self) -> bool:
        """True if the Viber desktop app process is alive."""

    @abstractmethod
    def focus(self) -> None:
        """Bring Viber to the foreground (and un-minimize if needed)."""

    @abstractmethod
    def open_url(self, url: str) -> None:
        """Hand a `viber://` URL to the OS (opens/navigates a chat)."""

    # --- perception ------------------------------------------------------------------
    @abstractmethod
    def observe(self, region=None) -> list[dict]:
        """Capture the Viber window and OCR it.

        Returns observations sorted top-to-bottom: [{"text": str, "x": float, "y": float}],
        where x is the left edge and y the top position, both normalized 0..1 over the window.
        `region` = (l, t, r, b), normalized, is a performance HINT: a backend may OCR only that
        part of the window (coordinates stay window-relative) or ignore it, so callers must still
        filter by position. Raises OCRError if no usable Viber window is visible.
        """

    def wait_still(self, timeout: float = 2.0) -> float:
        """Wait until the window has stopped changing (an animation ended), or `timeout`; returns
        the seconds waited. Backends that can't diff captures cheaply just pause briefly."""
        time.sleep(min(timeout, 0.4))
        return min(timeout, 0.4)

    # --- clipboard -------------------------------------------------------------------
    @abstractmethod
    def clipboard_get(self) -> str | None: ...

    @abstractmethod
    def clipboard_set(self, text: str) -> None: ...

    # --- keyboard --------------------------------------------------------------------
    @abstractmethod
    def type_text(self, text: str) -> None:
        """Type `text` as unicode characters (no modifiers)."""

    @abstractmethod
    def select_all(self) -> None:
        """Select-all in the focused field (Cmd+A / Ctrl+A)."""

    @abstractmethod
    def delete(self) -> None:
        """Delete/backspace the selection or preceding char."""

    @abstractmethod
    def paste(self) -> None:
        """Paste the clipboard into the focused field (Cmd+V / Ctrl+V)."""

    # --- clicks on Viber UI elements (layout-specific) -------------------------------
    @abstractmethod
    def click_compose(self) -> None:
        """Click the message compose box to focus it."""

    @abstractmethod
    def click_send(self) -> None:
        """Click the send button."""

    @abstractmethod
    def click_search(self) -> None:
        """Click/focus the search field."""

    @abstractmethod
    def click_result(self, norm_y: float) -> None:
        """Click a search/chat-list result row at normalized vertical position `norm_y`."""

    @abstractmethod
    def click_panel_close(self) -> None:
        """Click the ✕ that closes the right-side info/settings panel."""

    def scroll(self, area: str, notches: int) -> None:
        """Scroll `area` ("chat" = open conversation, "chats" = sidebar list) by mouse-wheel
        `notches`; positive scrolls up (older messages / top of list)."""
        self._unsupported("Scrolling")

    # --- computer-use primitives (x/y = pixels in the screenshot image) ----------------
    def screenshot(self):
        """The Viber window as a PIL image."""
        self._unsupported("Screenshots")

    def click_at(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        self._unsupported("Clicking by coordinates")

    def press_key(self, key: str, repeat: int = 1) -> None:
        self._unsupported("Key presses")

    def type_into_focus(self, text: str) -> None:
        self._unsupported("Typing")

    def scroll_at(self, x: float, y: float, notches: int) -> None:
        self._unsupported("Scrolling")

    def elements(self) -> list[dict]:
        """[{type, name, class, text, parent, rect:[l,t,r,b]}] in screenshot pixels."""
        self._unsupported("Listing UI elements")

    def foreground(self) -> int:
        return 0

    def hold_foreground(self, prev: int, seconds: float) -> None:
        """Keep the user's window in front for `seconds` (no-op where not needed)."""

    def paste_files(self, paths: list[str], taken) -> bool:
        """Paste `paths` into the open chat's message box, all in one send preview, without the
        file picker. `taken()` says whether Viber shows them yet. False where the backend can't
        (the caller then attaches them through the picker)."""
        return False

    def file_dialog_open(self) -> bool:
        self._unsupported("Sending files")

    def file_dialog_choose(self, path: str) -> bool:
        self._unsupported("Sending files")

    def file_dialog_cancel(self) -> None:
        self._unsupported("Sending files")

    def _unsupported(self, what: str):
        raise NotImplementedError(f"{what} isn't implemented for the {self.name} backend yet.")
