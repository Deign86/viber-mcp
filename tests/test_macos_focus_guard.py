"""MacBackend's focus guard with the AX calls faked (macOS only: the module imports Quartz)."""

import sys
from pathlib import Path

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS backend", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import macos  # noqa: E402
from backends.base import OCRError  # noqa: E402

SEARCH = "QGuiApplication.MainWindow.SearchItem_QML_812.TextFieldItem_QMLTYPE_155"
COMPOSE = "QGuiApplication.MainWindow.StackView_QMLTYPE_578.QQuickTextEdit_QML_1132"


class Field:
    def __init__(self, ident, value=""):
        self.ident, self.value = ident, value


def _backend(monkeypatch, clicked, focus_stays_on):
    """`clicked` is the field type_into_focus should type in; Viber's keyboard focus stays on
    `focus_stays_on` whatever AXFocused says (as the message box kept it on 2026-10-28)."""
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._focus_el = clicked
    posted = []

    def get(el, attr):
        return {"AXRole": "AXTextField", "AXValue": el.value, "AXIdentifier": el.ident}.get(attr)

    def post_key(code, flags=0, text=None):
        posted.append(text)
        focus_stays_on.value += text
    monkeypatch.setattr(macos, "_get", get)
    monkeypatch.setattr(macos, "AXUIElementSetAttributeValue", lambda el, attr, value: 0)
    monkeypatch.setattr(b, "_focused", lambda: focus_stays_on)
    monkeypatch.setattr(b, "_post_key", post_key)
    monkeypatch.setattr(macos.time, "sleep", lambda s: None)
    return b, posted


def test_nothing_is_posted_while_the_message_box_keeps_the_focus(monkeypatch):
    # 2026-10-28: AXFocused on the search box didn't take; posted characters would have gone
    # into the open client chat's message box.
    search, compose = Field(SEARCH), Field(COMPOSE)
    b, posted = _backend(monkeypatch, search, compose)
    with pytest.raises(OCRError, match="message box"):
        b.type_into_focus("Hazel")
    assert posted == [] and compose.value == ""


def test_typing_goes_ahead_when_the_clicked_field_has_the_focus(monkeypatch):
    search = Field(SEARCH)
    b, posted = _backend(monkeypatch, search, search)
    b.type_into_focus("Hazel")
    assert search.value == "Hazel"
