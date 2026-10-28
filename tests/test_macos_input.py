"""MacBackend input with the AX calls faked (macOS only: the module imports Quartz/AppKit)."""

import sys
from pathlib import Path

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS backend", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import macos  # noqa: E402
from backends.base import OCRError  # noqa: E402


class Field:
    def __init__(self, value="", accepts=True):
        self.value, self.accepts, self.focused = value, accepts, 0


def _backend(monkeypatch, field):
    b = macos.MacBackend.__new__(macos.MacBackend)
    b._focus_el = field

    def get(el, attr):
        return {"AXRole": "AXTextArea", "AXValue": el.value, "AXIdentifier": "field"}.get(attr)

    def set_attr(el, attr, value):
        if attr == "AXFocused" and value:
            el.focused += 1
            el.accepts = True  # what re-focusing does for Qt after a hand-off
        elif attr == "AXValue":
            el.value = value

    def post_key(code, flags=0, text=None):
        if field.accepts:
            field.value += text
    monkeypatch.setattr(macos, "_get", get)
    monkeypatch.setattr(macos, "AXUIElementSetAttributeValue", set_attr)
    monkeypatch.setattr(b, "_post_key", post_key)
    monkeypatch.setattr(b, "_focused", lambda: field, raising=False)  # PR #39's focus check
    monkeypatch.setattr(macos.time, "sleep", lambda s: None)
    return b


def test_typing_focuses_the_field_again_after_a_real_key_hand_off(monkeypatch):
    # 2026-10-28: the GC rename pressed End/Backspace (real keys, Viber brought to the front and
    # back) and the posted name never arrived: "The group name didn't take (got '')".
    field = Field("", accepts=False)
    b = _backend(monkeypatch, field)
    b.type_into_focus("AIRA-WH")
    assert field.focused == 1 and field.value == "AIRA-WH"


def test_typing_that_never_lands_is_an_error_not_a_silent_success(monkeypatch):
    field = Field("Hello po, @Unknown")
    b = _backend(monkeypatch, field)
    monkeypatch.setattr(b, "_post_key", lambda *a, **k: None)  # characters dropped
    monkeypatch.setattr(macos.time, "time", iter(range(0, 1000)).__next__)
    with pytest.raises(OCRError):
        b.type_into_focus("! Please check the listing above po.")


def test_a_hid_click_keeps_the_field_typing_goes_to(monkeypatch):
    # Picking a mention row / ticking a picker circle is a real click; typing after it still
    # belongs in the field clicked before.
    field = Field()
    b = _backend(monkeypatch, field)
    monkeypatch.setattr(b, "_to_screen", lambda x, y: (x, y))
    monkeypatch.setattr(b, "_hit", lambda sx, sy: None)
    monkeypatch.setattr(b, "_with_viber_front", lambda action: None)
    b.click_at(380, 714)
    assert b._focus_el is field
