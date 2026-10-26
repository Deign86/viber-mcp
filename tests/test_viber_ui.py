import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import viber_ui  # noqa: E402
from backends import get_backend  # noqa: E402
from viber_ui import UIError, _attach_button, _invite_count, _messages, _preview  # noqa: E402


def el(kind, cls, rect, parent=None, text="", name=""):
    return {"type": kind, "name": name, "class": cls, "text": text, "parent": parent, "rect": rect}


PANE = [308, 71, 792, 959]
THEIRS_WIDE = [371, 100, 633, 140]   # crosses the pane's middle but starts at the avatar edge
MINE = [548, 200, 760, 240]
MINE_SHORT = [815, 300, 860, 330]


def wrapped_chat():
    elements = [el("Pane", "StackView", PANE)]
    # date separator: no text, no image
    elements.append(el("Group", "FeedDelegate", [308, 80, 792, 95], parent=0))
    for rect, text in ((THEIRS_WIDE, "from them"), (MINE, "from me"), (MINE_SHORT, "ok")):
        delegate = len(elements)
        elements.append(el("Group", "FeedDelegate", [308, rect[1] - 5, 792, rect[3] + 5], parent=0))
        elements.append(el("Text", "TextEditItem", rect, parent=delegate, text=text))
        elements.append(el("Group", "QQuickLoader", [rect[2] + 5, rect[1], rect[2] + 25, rect[3]], parent=delegate))
    return elements


def direct_chat():
    elements = [el("Pane", "StackView", PANE)]
    for rect, text in ((THEIRS_WIDE, "from them"), (MINE, "from me")):
        elements.append(el("Text", "TextEditItem", rect, parent=0, text=text))
        elements.append(el("Group", "QQuickLoader", [rect[2] + 5, rect[1] + 5, rect[2] + 25, rect[3] - 5], parent=0))
    elements.append(el("Button", "RemixImageButton", [540, 400, 700, 500], parent=0))
    elements.append(el("Group", "QQuickLoader", [705, 440, 725, 460], parent=0))
    return elements


@pytest.mark.parametrize("layout", [wrapped_chat, direct_chat])
def test_messages_both_layouts_skip_separators_and_number_from_newest(layout):
    messages = _messages(layout())
    tops = [m["rect"][1] for m in messages]
    assert tops == sorted(tops)
    assert "" not in [m["text"] for m in messages if m["kind"] == "text"]
    assert messages[-1]["n"] == 1 and messages[0]["n"] == len(messages)


def test_wrapped_layout_counts_and_texts():
    messages = _messages(wrapped_chat())
    assert [m["text"] for m in messages] == ["from them", "from me", "ok"]


def test_direct_layout_includes_images():
    messages = _messages(direct_chat())
    assert [m["kind"] for m in messages] == ["text", "text", "image"]
    assert messages[2]["image_rect"] == [540, 400, 700, 500]


def test_wide_bubble_from_someone_else_is_not_mine():
    by_text = {m["text"]: m for m in _messages(wrapped_chat())}
    assert by_text["from them"]["mine"] is False
    assert by_text["from me"]["mine"] is True
    assert by_text["ok"]["mine"] is True


def test_like_rect_is_the_loader_level_with_that_message():
    messages = _messages(direct_chat())
    by_text = {m["text"]: m for m in messages}
    assert by_text["from them"]["like_rect"][1] == THEIRS_WIDE[1] + 5
    assert by_text["from me"]["like_rect"][1] == MINE[1] + 5
    image = next(m for m in messages if m["kind"] == "image")
    assert image["like_rect"] == [705, 440, 725, 460]


def test_messages_without_chat_pane_raises():
    with pytest.raises(UIError):
        _messages([el("Pane", "Other", [0, 0, 10, 10])])


def test_list_messages_reads_through_backend_contract(fake_backend):
    fake_backend._elements = wrapped_chat()
    listing = viber_ui.list_messages()
    assert listing["count"] == 3
    assert [m["from"] for m in listing["messages"]] == ["them", "me", "me"]
    assert get_backend() is fake_backend


def preview_overlay():
    return [
        el("Group", "Overlay", [200, 40, 1080, 1000]),                                   # 0
        el("Group", "ScrollView", [267, 903, 799, 923], parent=0),                       # 1
        el("Edit", "TextArea", [267, 903, 799, 923], parent=1),                          # 2
        el("Button", "RoundIconButton", [820, 898, 860, 938], parent=0),                 # 3 send
        el("Button", "IconButton", [553, 803, 613, 863], parent=0),                      # 4 "+"
        el("Group", "QQuickLoader", [300, 803, 360, 863], parent=0),                     # 5 delete file
        el("Button", "IconButton", [310, 810, 330, 830], parent=5),                      # 6 inner
        el("Button", "IconButton", [1057, 51, 1073, 67], parent=0),                      # 7 close
    ]


def test_preview_picks_plus_send_and_close():
    elements = preview_overlay()
    caption, send, add, close = _preview(elements)
    assert caption is elements[2]
    assert send is elements[3]
    assert add is elements[4]
    assert close is elements[7]


def test_preview_none_without_caption():
    elements = preview_overlay()
    assert _preview(elements[:2] + elements[3:]) is None


def test_attach_button_is_leftmost_icon_on_compose_row():
    elements = [
        el("Edit", "QQuickTextEdit", [400, 925, 730, 941]),
        el("Button", "IconButton", [324, 921, 348, 945]),
        el("CheckBox", "CheckBox", [356, 921, 380, 945]),
        el("Button", "SendToolbarButton", [740, 921, 770, 945]),
        el("Button", "IconButton", [900, 921, 920, 945]),  # right of the box: not on its left
    ]
    assert _attach_button(elements)["rect"][0] == 324


def test_attach_button_without_message_box_raises():
    with pytest.raises(UIError):
        _attach_button([el("Button", "IconButton", [324, 921, 348, 945])])


def ocr_row(text, x=10, y=50, w=200, h=20, width=1000, height=800):
    return {"text": text, "x": x, "y": y, "w": w, "h": h, "W": width, "H": height}


def test_invite_count_parses_counter_rows():
    assert _invite_count([ocr_row("2/250")]) == 2
    assert _invite_count([ocr_row("0/250")]) == 0
    assert _invite_count([ocr_row("Invite contacts", y=20), ocr_row("12/250", y=40)]) == 12


def test_invite_count_none_when_absent_or_outside_left_column():
    assert _invite_count([ocr_row("Invite contacts")]) is None
    assert _invite_count([ocr_row("5/250", x=600)]) is None


# ------------------------------------------- photos "not sent" banner (images failing to send)


class ClickingBackend:
    """Shows the not-sent banner until Resend has been clicked `fixes_after` times."""

    def __init__(self, fixes_after):
        self.fixes_after = fixes_after
        self.clicks = 0

    def elements(self):
        if self.clicks >= self.fixes_after:
            return []
        return [el("Button", "BannerButton", [968, 155, 1044, 175], name="Resend all")]

    def click_at(self, x, y, button="left"):
        self.clicks += 1


def test_resend_button_found_only_on_not_sent_banner():
    assert viber_ui._resend_button([el("Button", "BannerButton", [0, 0, 10, 10], name="Resend")])
    assert viber_ui._resend_button([el("Button", "IconButton", [0, 0, 10, 10], name="Resend")]) is None
    assert viber_ui._resend_button([]) is None


@pytest.mark.parametrize("fixes_after,attempts,unsent", [(0, 0, False), (1, 1, False), (9, 2, True)])
def test_settle_and_resend_presses_resend_until_banner_clears(monkeypatch, fixes_after, attempts, unsent):
    import backends

    fake = ClickingBackend(fixes_after)
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    report = viber_ui._settle_and_resend(3)
    assert report == {"attempts": attempts, "still_unsent": unsent, "uploading": False}
    assert fake.clicks == attempts
