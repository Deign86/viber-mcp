"""MacBackend with Viber's window on another Space (another app full screen), AX/CG faked
(macOS only: the module imports Quartz)."""

import sys
from pathlib import Path

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS backend", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import macos  # noqa: E402
from backends.base import OCRError  # noqa: E402

_WINDOW = {"kCGWindowNumber": 6397, "kCGWindowBounds": {"X": 8, "Y": 33, "Width": 1085, "Height": 775}}


def _away_backend(monkeypatch):
    """2026-11-01: with Safari full screen the server's focus() ran `open -b Viber`, which
    switched the user into Viber. Off its Space the window isn't drawn either (captures showed an
    old frame), so nothing may read it, click it or bring it forward."""
    b = macos.MacBackend.__new__(macos.MacBackend)
    monkeypatch.setattr(b, "_onscreen_window", lambda: None)
    monkeypatch.setattr(b, "_offspace_window", lambda: _WINDOW)
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **k: pytest.fail(f"ran {a[0]}"))
    monkeypatch.setattr(macos, "AXUIElementSetAttributeValue",
                        lambda *a: pytest.fail("set an AX attribute (AXFrontmost?)"))
    return b


def test_a_window_on_another_space_is_not_reopened_or_read(monkeypatch):
    b = _away_backend(monkeypatch)
    b.focus()  # leaves it where it is
    with pytest.raises(OCRError, match="full screen"):
        b._window()


def test_no_hand_off_to_the_front_while_viber_is_on_another_space(monkeypatch):
    b = _away_backend(monkeypatch)
    with pytest.raises(OCRError, match="full screen"):
        b._with_viber_front(lambda: pytest.fail("real input was sent"))


def test_no_viber_link_while_viber_is_on_another_space(monkeypatch):
    b = _away_backend(monkeypatch)
    with pytest.raises(OCRError, match="full screen"):
        b.open_url("viber://chat?number=%2B639170000000")
