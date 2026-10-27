"""macOS backend: the pure AX → element helpers (run on every OS), plus the domain changes the
Mac tree needed (popup nesting, the preview caption, the filter pills, the My Notes guard)."""

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backends  # noqa: E402
import viber_ocr  # noqa: E402
import viber_send  # noqa: E402
import viber_ui  # noqa: E402
from backends import _maclogic as ml  # noqa: E402

_PATH = "QGuiApplication.MainWindow_QMLTYPE_78.ApplicationWindowContentControl.SplitView_QMLTYPE_480"


def _el(kind, cls, rect, parent=None, text="", name="", ident=""):
    return {"type": kind, "name": name, "class": cls, "text": text, "parent": parent,
            "rect": list(rect), "_ident": ident}


def test_short_class_takes_the_last_qml_segment():
    assert ml.short_class(_PATH + ".StackView_QMLTYPE_732.FeedDelegate_QMLTYPE_1472."
                          "TextEditItem_QMLTYPE_1552_QML_1559") == "TextEditItem"
    assert ml.short_class(_PATH + ".StackView_QMLTYPE_732.SendToolbarButton") == "SendToolbarButton"
    assert ml.short_class("") == "" and ml.short_class(None) == ""


def test_ax_roles_map_to_the_uia_types_viber_ui_matches():
    assert ml.ax_type("AXTextField") == "Edit"
    assert ml.ax_type("AXButton") == "Button"
    assert ml.ax_type("AXMenuItem") == "MenuItem"
    assert ml.ax_type("AXStaticText") == "Text"
    assert ml.ax_type("AXGroup", "QQuickPopupItem") == "Window"
    assert ml.ax_type("AXSomethingNew") == "SomethingNew"


def test_rects_are_relative_to_the_window():
    assert ml.to_rect((740, 774, 36, 36), (8, 33)) == [732, 741, 768, 777]


def test_visible_center_clips_to_the_window():
    window = (8, 33, 1085, 785)
    assert ml.visible_center((100, 100, 20, 20), window) == (110, 110)
    # Scrolled half out of the bottom: hit-test the part that's still on screen.
    assert ml.visible_center((803, 792, 136, 136), window) == (871, 805)
    assert ml.visible_center((803, 900, 136, 136), window) is None


def test_utf16_units_count_surrogate_pairs():
    assert ml.utf16_units("a") == 1
    assert ml.utf16_units("😀") == 2


def test_press_key_names_match_the_windows_backend():
    assert set(ml.KEYCODES) == {"escape", "esc", "enter", "return", "backspace", "delete", "tab",
                                "space", "up", "down", "left", "right", "pageup", "pagedown",
                                "home", "end"}


def test_field_predicates():
    assert ml.is_compose(_el("Edit", "QQuickTextEdit", (0, 0, 1, 1)))
    assert ml.is_search(_el("Edit", "TextFieldItem", (0, 0, 1, 1)))
    assert ml.is_send(_el("Button", "SendToolbarButton", (0, 0, 1, 1)))
    assert not ml.is_compose(_el("Edit", "TextArea", (0, 0, 1, 1)))  # the ChatGPT panel's box


def _badge_column():
    img = Image.new("RGB", (120, 260), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((10, 10), "1:55 PM", fill=ml.BADGE_RGB)  # unread time stamp: same purple, thin text
    d.ellipse((80, 40, 100, 60), fill=ml.BADGE_RGB)  # "5"
    d.text((87, 44), "5", fill=(255, 255, 255))
    d.rounded_rectangle((74, 120, 104, 140), radius=10, fill=ml.BADGE_RGB)  # "23" (a pill)
    d.text((82, 124), "23", fill=(255, 255, 255))
    return img


def test_badge_boxes_find_circles_and_pills_but_not_purple_text():
    boxes = ml.badge_boxes(_badge_column())
    assert len(boxes) == 2
    (l1, t1, r1, b1), (l2, t2, r2, b2) = boxes
    assert (l1, t1) == (80, 40) and r1 - l1 >= 19
    assert (l2, t2) == (74, 120) and r2 - l2 > b2 - t2  # the pill is wider than tall


def _preview_elements():
    """What macOS reports while the file-send preview is up: everything hangs off the window, the
    hidden ChatGPT panel's TextArea first; the identifiers keep the popup's levels."""
    popup = "QGuiApplication.MainWindow_QMLTYPE_78.Popup"
    els = [
        _el("Window", "", (0, 0, 1085, 788)),
        _el("Edit", "TextArea", (809, 695, 1049, 712), 0, name="Ask ChatGPT",
            ident=_PATH + ".SplitView_QMLTYPE_480.ScrollView_QMLTYPE_483.TextArea_QMLTYPE_484"),
        _el("Button", "IconButton", (1049, 48, 1065, 64), 0, ident=popup + ".IconButton_QMLTYPE_63"),
        _el("Button", "IconButton", (545, 629, 605, 689), 0, ident=popup + ".IconButton_QMLTYPE_63"),
        _el("Edit", "TextArea", (259, 729, 791, 749), 0, name="Add a caption...",
            ident=popup + ".ScrollView_QMLTYPE_483.TextArea_QMLTYPE_484_QML_1154"),
        _el("Button", "RoundIconButton", (799, 721, 835, 757), 0, ident=popup + ".RoundIconButton_QMLTYPE_215"),
        _el("Group", "StackView", (300, 68, 784, 788), 0, ident=_PATH + ".StackView_QMLTYPE_732"),
        _el("Edit", "TextEditItem", (489, 398, 756, 551), 6, text="one",
            ident=_PATH + ".StackView_QMLTYPE_732.FeedDelegate_QMLTYPE_1472.TextEditItem_QMLTYPE_1552"),
        _el("Edit", "TextEditItem", (557, 635, 756, 652), 6, text="two",
            ident=_PATH + ".StackView_QMLTYPE_732.FeedDelegate_QMLTYPE_1472.TextEditItem_QMLTYPE_1552"),
    ]
    return els, list(range(len(els)))


def test_nest_popups_rebuilds_the_preview_levels():
    els, refs = ml.nest_popups(*_preview_elements())
    assert all("_ident" not in e for e in els)
    popup = next(i for i, e in enumerate(els) if e["class"] == "QQuickPopupItem")
    assert els[popup]["type"] == "Window" and refs[popup] is None
    caption = next(e for e in els if e["name"] == "Add a caption...")
    scroll = els[caption["parent"]]
    assert scroll["class"] == "ScrollView" and scroll["parent"] == popup
    assert els[popup]["rect"] == [259, 48, 1065, 757]  # covers all its children
    # Repeated page items aren't merged into one virtual FeedDelegate.
    texts = [e for e in els if e["class"] == "TextEditItem"]
    assert all(els[t["parent"]]["class"] == "StackView" for t in texts)
    # The real elements keep their AX refs, in order.
    assert [r for r in refs if r is not None] == list(range(9))


def test_preview_skips_the_hidden_chatgpt_textarea():
    els, _ = ml.nest_popups(*_preview_elements())
    caption, send, add, close = viber_ui._preview(els)
    assert caption["name"] == "Add a caption..."
    assert send["class"] == "RoundIconButton"
    assert add["rect"] == [545, 629, 605, 689]
    assert close["rect"] == [1049, 48, 1065, 64]


def test_preview_is_none_without_an_overlay():
    els = [_el("Window", "", (0, 0, 1085, 788)),
           _el("Edit", "TextArea", (809, 695, 1049, 712), 0, name="Ask ChatGPT")]
    assert viber_ui._preview(els) is None


def test_popup_window_is_named_by_its_title_text():
    popup = "QGuiApplication.MainWindow_QMLTYPE_78.Popup"
    els = [_el("Window", "", (0, 0, 1085, 788)),
           _el("Text", "", (400, 300, 600, 320), 0, name="Pin note?", ident=popup + ".Label_QMLTYPE_9"),
           _el("Button", "NotificationButton", (420, 360, 500, 390), 0, name="Pin",
               ident=popup + ".NotificationButton_QMLTYPE_7")]
    els, _ = ml.nest_popups(els, list(range(len(els))))
    dialog = viber_ui._popup(els)
    assert dialog is not None and dialog["name"] == "Pin note?"


class _FilterBackend:
    def __init__(self, obs):
        self.obs, self.clicks = obs, []

    def observe(self, region=None):
        return self.obs

    def screenshot(self):
        class Img:
            width, height = 1085, 788
        return Img()

    def click_at(self, x, y, button="left", double=False):
        self.clicks.append((x, y))

    def wait_still(self, timeout=2.0):
        return 0.0


def test_filter_pills_lower_on_a_mac_window_are_found(monkeypatch):
    # The Mac window's pills sit at ~0.23 of its height (Windows: under 0.22).
    fake = _FilterBackend([{"text": "All", "x": 0.024, "y": 0.226, "w": 0.019, "h": 0.02},
                           {"text": "Unread", "x": 0.075, "y": 0.226, "w": 0.045, "h": 0.02}])
    monkeypatch.setattr(backends, "_backend", fake)
    viber_ocr._click_filter("Unread")
    assert len(fake.clicks) == 1 and fake.clicks[0][0] == pytest.approx((0.075 + 0.0225) * 1085)


class _NotesBackend:
    def __init__(self):
        self.sent = False

    def app_running(self):
        return True

    def clipboard_get(self):
        return None

    def clipboard_set(self, text):
        pass

    def focus(self):
        pass

    def click_send(self):
        self.sent = True


def test_save_to_my_notes_checks_the_header_not_just_the_search_row(monkeypatch):
    # The search row read "My Notes" but the click opened another chat: nothing may be sent.
    fake = _NotesBackend()
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(viber_send, "_search_open", lambda name: ("My Notes", 3))

    def wrong_chat(expect):
        raise viber_send.SendError(f"The open chat is 'Mark', not '{expect}'.")
    monkeypatch.setattr(viber_send, "require_open_chat", wrong_chat)
    monkeypatch.setattr(viber_send, "_paste_into_compose", lambda text: pytest.fail("pasted"))
    with pytest.raises(viber_send.SendError):
        viber_send.save_to_my_notes("note")
    assert not fake.sent


def test_notification_box_dialogs_are_popups_covering_their_text():
    # macOS: "Pin note?" exposes only its NotificationButtons, under MainWindow.NotificationBox.
    box = "QGuiApplication.MainWindow_QMLTYPE_78.NotificationBox"
    els = [_el("Window", "", (0, 0, 1085, 788)),
           _el("Button", "NotificationButton", (459, 430, 528, 463), 0, name="Pin",
               ident=box + ".NotificationButton_QMLTYPE_66_QML_70"),
           _el("Button", "NotificationButton", (536, 430, 629, 463), 0, name="Cancel",
               ident=box + ".NotificationButton_QMLTYPE_66_QML_72")]
    els, refs = ml.nest_popups(els, list(range(len(els))))
    dialog = viber_ui._popup(els)
    assert dialog is not None
    assert [e["name"] for e in els if e["parent"] == els.index(dialog)] == ["Pin", "Cancel"]
    # The OCR region reaches above the buttons, where the title and message are.
    assert dialog["rect"][1] <= 430 - 150 and dialog["rect"][0] < 459 and dialog["rect"][2] > 629


def test_edit_text_turns_qt_line_separators_into_newlines():
    # macOS gives a message's line breaks as U+2028, so a client template parsed as one line.
    raw = "🏢 Company Name:  👤 Name:  Macroe Donaire 📱 Viber: 09761653772"
    assert ml.edit_text(raw) == "🏢 Company Name: \n👤 Name:  Macroe Donaire\n📱 Viber: 09761653772"
    assert ml.edit_text(None) == ""


def test_chat_pane_falls_back_to_the_stack_view():
    # macOS's tree has no SplitView; the sidebar then ended at a fixed 30% (325 px) and text
    # from an image in the open chat leaked into the chat list.
    els = [_el("Window", "", (0, 0, 1085, 788)), _el("Group", "StackView", (300, 68, 774, 786), 0)]
    assert viber_ocr._chat_pane(els)["rect"][0] == 300
    els.append(_el("Group", "SplitView", (298, 0, 1085, 788), 0))
    assert viber_ocr._chat_pane(els)["class"] == "SplitView"


def _o(text, x, y, w):
    return {"text": text, "x": x, "y": y, "w": w, "h": 0.02}


def test_name_and_stamp_read_as_one_line_become_their_own_row():
    # Vision read "Mark Anthony Sant... ✓✓ 7:59 PM" as one piece; the row merged into the one above.
    sb = 0.2762
    obs = [_o("ChatGPT", 0.065, 0.275, 0.07), _o("12:55 PM", 0.218, 0.279, 0.04),
           _o("No messages", 0.065, 0.303, 0.08),
           _o("Mark Anthony Sant... ~/ 7:59 PM", 0.065, 0.37, 0.198),
           _o("oki po noted", 0.065, 0.395, 0.07)]
    rows = viber_ocr.chat_rows(obs, sb)
    assert [r["name"] for r in rows] == ["ChatGPT", "Mark Anthony Sant..."]
    assert rows[1]["time"] == "7:59 PM" and rows[1]["last_from"] == "me"  # the tick stayed on the stamp
    assert rows[0]["preview"] == "No messages"


def test_a_preview_mentioning_a_time_is_not_split():
    obs = [_o("Meeting at 5:00 PM", 0.065, 0.4, 0.09)]  # doesn't reach the stamp column
    assert viber_ocr.split_name_stamps(obs, 0.15, 0.22) == obs


class _SearchLeftOpen:
    """The sidebar still shows a search ("My Notes") after search_and_open, as on macOS."""

    def __init__(self, query):
        self.query, self.calls = query, []

    def elements(self):
        return [_el("Edit", "TextFieldItem", (104, 40, 244, 68), None, text=self.query)]

    def click_search(self):
        self.calls.append("click_search")

    def delete(self):
        self.calls.append("delete")
        self.query = ""

    def press_key(self, key, repeat=1):
        self.calls.append(key)

    def wait_still(self, timeout=2.0):
        return 0.0


def test_a_leftover_search_is_closed_before_reading_the_chat_list(monkeypatch):
    # Windows (no avoid_spare_escape): clear, then Escape, exactly as before.
    fake = _SearchLeftOpen("My Notes")
    monkeypatch.setattr(backends, "_backend", fake)
    viber_ocr._leave_search()
    assert fake.calls == ["click_search", "delete", "escape"] and fake.query == ""
    fake.calls.clear()
    viber_ocr._leave_search()  # nothing open: nothing is clicked or typed
    assert fake.calls == []


def test_a_preview_ending_in_a_time_is_not_made_into_a_row():
    # Windows/macOS: a preview line that happens to end with a time and fill the row must not
    # become a fake chat row.
    sb = 0.2762
    obs = [_o("Mitch Kim", 0.065, 0.37, 0.07), _o("1:02 PM", 0.218, 0.372, 0.04),
           _o("sige see you tomorrow at 5:00 PM", 0.065, 0.395, 0.2),     # under a stamp
           _o("Kru: dito na kami around 3:30 PM", 0.065, 0.5, 0.2)]        # "Sender:" prefix
    rows = viber_ocr.chat_rows(obs, sb)
    assert [r["name"] for r in rows] == ["Mitch Kim"]
    assert "5:00 PM" in rows[0]["preview"] and "3:30 PM" in rows[0]["preview"]


def test_filter_pill_is_looked_for_again_while_the_list_redraws(monkeypatch):
    # Right after a leftover search was closed the first read showed no pills yet.
    reads = iter([[], [{"text": "Unread", "x": 0.075, "y": 0.226, "w": 0.045, "h": 0.02}]])
    fake = _FilterBackend([])
    fake.observe = lambda region=None: next(reads)
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_ocr.time, "sleep", lambda s: None)
    viber_ocr._click_filter("Unread")
    assert len(fake.clicks) == 1


def _stripes(h=300, offset=0):
    img = Image.new("L", (120, h), 0)
    d = ImageDraw.Draw(img)
    for y in range(-400, h + 400, 37):
        d.rectangle((10, y + offset, 110, y + offset + 9), fill=200)
        d.text((12, y + offset + 14), str(y), fill=120)
    return img


def test_vertical_shift_measures_scrolled_content():
    before = _stripes()
    assert ml.vertical_shift(before, _stripes(offset=24)) in (24, 22, 26)   # content moved down
    assert ml.vertical_shift(before, _stripes(offset=-40)) in (-40, -38, -42)
    assert ml.vertical_shift(before, before.copy()) == 0


def test_vertical_shift_is_not_fooled_by_repeating_rows():
    # Chat-list rows are all 73 pt tall: a shift also matches a whole row further away. The
    # known scroll direction and the smallest match decide.
    def rows(off):
        img = Image.new("L", (210, 400), 0)
        d = ImageDraw.Draw(img)
        for y in range(-400, 800, 73):
            d.rectangle((10, y + off, 200, y + off + 30), fill=200)
        return img
    base = rows(0)
    assert ml.vertical_shift(base, rows(48), direction=1) == 48
    assert ml.vertical_shift(base, rows(-30), direction=-1) == -30


def test_macos_skips_the_spare_escape_once_the_search_is_closed(monkeypatch):
    # macOS: clearing the box closes the search (its tabs go); an extra Escape would hide
    # Viber's window and take the foreground, so it's skipped.
    fake = _SearchLeftOpen("My Notes")
    fake.avoid_spare_escape = True
    monkeypatch.setattr(backends, "_backend", fake)
    viber_ocr._leave_search()
    assert fake.calls == ["click_search", "delete"]


def test_sponsored_ad_box_is_not_read_as_chat_rows():
    # The ad at the bottom of the chat list leaked into the last row's preview and hid its badge.
    sb = 0.2762
    obs = [_o("ChatGPT", 0.065, 0.275, 0.07), _o("12:55 PM", 0.218, 0.279, 0.04),
           _o("No messages", 0.065, 0.303, 0.08),
           _o("Max Alpha", 0.065, 0.371, 0.07), _o("8:30 PM", 0.218, 0.372, 0.04),
           _o("Photo message", 0.065, 0.395, 0.08),
           _o("MACROE-CS-(25-30S...", 0.065, 0.66, 0.15), _o("4:53PM", 0.218, 0.66, 0.04),
           _o("Sponsored", 0.015, 0.70, 0.06), _o("FlickReels", 0.02, 0.75, 0.07),
           _o("App Store", 0.03, 0.95, 0.06)]
    rows = viber_ocr.chat_rows(obs, sb)
    assert [r["name"] for r in rows] == ["ChatGPT", "Max Alpha"]  # MACROE is cut off by the ad
    assert all("Sponsored" not in r["preview"] and "App Store" not in r["preview"] for r in rows)


def test_my_notes_is_searched_again_when_the_first_click_left_another_chat_open(monkeypatch):
    # Live: APG CLIENTS (a big group) was still loading and stayed open after the click.
    opened = iter(["APG CLIENTS", "My Notes"])
    state = {"chat": "APG CLIENTS", "searches": 0}

    def search(name):
        state["searches"] += 1
        state["chat"] = next(opened)
        return ("My Notes", 3)

    def header(expect):
        if state["chat"] != expect:
            raise viber_send.SendError(f"The open chat is '{state['chat']}'")
    monkeypatch.setattr(viber_send, "_search_open", search)
    monkeypatch.setattr(viber_send, "require_open_chat", header)
    monkeypatch.setattr(viber_send, "viber_is_running", lambda: True)
    monkeypatch.setattr(viber_send, "backend", _NotesBackend())
    assert viber_send.open_my_notes()["opened"] == "My Notes"
    assert state["searches"] == 2


def test_time_stamps_with_ocr_lookalikes_still_start_a_row():
    # Live: "5:01 PM" OCR'd as "5:0TPM"; that row merged into the one above.
    assert viber_ocr.row_stamp("5:0TPM") == "5:01PM"
    assert viber_ocr.row_stamp("1O:3O AM") == "10:30 AM"
    assert viber_ocr.row_stamp("11:02 AM") == "11:02 AM"          # normal times unchanged
    assert viber_ocr.row_stamp("Total") is None and viber_ocr.row_stamp("STOP PM") is None
