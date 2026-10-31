"""The sidebar chat list as structured rows (name, time, preview, unread, who wrote last), the
"Unread" filter read, and the sidebar tools built on it (mark_as_read)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backends  # noqa: E402
import viber_ocr  # noqa: E402
import viber_send  # noqa: E402
import viber_ui  # noqa: E402
from viber_ocr import chat_rows  # noqa: E402


def _obs(text, x, y, w=0.08, h=0.012):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


# Sidebar OCR as captured live on 2026-10-24 (1101 x 1087 window, sidebar right edge at 0.28):
# read ticks come out as a "W" prefix on the time or as a lone "w" before it; "Me:" previews are
# ours in groups; a name is truncated with "..."; a pinned-message bar sits just right of the edge.
SIDEBAR = [
    _obs("Rakuten Viber", 0.035, 0.010), _obs("Q Search...", 0.081, 0.046), _obs("Chats", 0.018, 0.132),
    _obs("091781:", 0.294, 0.144), _obs("All", 0.033, 0.167), _obs("Unread", 0.083, 0.167),
    _obs("APG OCULAR", 0.073, 0.206), _obs("w", 0.210, 0.207, 0.015), _obs("1:46 PM", 0.233, 0.208),
    _obs("Me: ONGOING", 0.073, 0.225),
    _obs("Mark Anthony Santos", 0.073, 0.272), _obs("W 12:28 PM", 0.205, 0.274), _obs("send niyo 'yan sir", 0.073, 0.292),
    _obs("MON-WH-(2000SQM,..", 0.073, 0.340), _obs("11:58 AM", 0.228, 0.342), _obs("Mon Guinto: Okay po", 0.073, 0.359),
    _obs("Patrick", 0.073, 0.407), _obs("1102 AM", 0.228, 0.409), _obs("1. Yes", 0.074, 0.427), _obs("2. Yes", 0.073, 0.443),
    _obs("Shannah Audrey Ar...", 0.073, 0.474), _obs("Yesterday", 0.226, 0.476),
    _obs("Hello po ma'am! Wala pa po", 0.074, 0.493), _obs("kaming update, hindi pa po nam...", 0.073, 0.510),
    _obs("JHAZEL-CS-(30-35...", 0.072, 0.541), _obs("Yesterday", 0.226, 0.543), _obs("3", 0.262, 0.562, 0.012),
    _obs("Rika reacted to \"Noted po", 0.074, 0.560), _obs("ma'am, ground floor for walk-in .", 0.073, 0.577),
    _obs("Beatriz Alpha", 0.35, 0.083), _obs("Hello in the open chat", 0.5, 0.5),   # chat pane: ignored
]
SIDEBAR_RIGHT = 0.28


def test_chat_rows_name_time_preview_and_badge():
    rows = chat_rows(SIDEBAR, SIDEBAR_RIGHT)
    assert [r["name"] for r in rows] == ["APG OCULAR", "Mark Anthony Santos", "MON-WH-(2000SQM,..",
                                         "Patrick", "Shannah Audrey Ar...", "JHAZEL-CS-(30-35..."]
    assert [r["time"] for r in rows] == ["1:46 PM", "12:28 PM", "11:58 AM", "1102 AM", "Yesterday", "Yesterday"]
    by = {r["name"]: r for r in rows}
    assert by["Patrick"]["preview"] == "1. Yes 2. Yes"
    assert by["Shannah Audrey Ar..."]["preview"] == "Hello po ma'am! Wala pa po kaming update, hindi pa po nam..."
    assert by["JHAZEL-CS-(30-35..."]["unread"] == 3 and by["JHAZEL-CS-(30-35..."]["preview"].startswith("Rika reacted")
    assert all(r["unread"] == 0 for r in rows if r["name"] != "JHAZEL-CS-(30-35...")
    assert "091781:" not in str(rows) and "Hello in the open chat" not in str(rows)


def test_chat_rows_know_who_wrote_last():
    by = {r["name"]: r for r in chat_rows(SIDEBAR, SIDEBAR_RIGHT)}
    assert by["APG OCULAR"]["last_from"] == "me"            # "Me:" preview (and a lone "w" tick)
    assert by["Mark Anthony Santos"]["last_from"] == "me"   # tick OCR'd onto the time: "W 12:28 PM"
    assert by["MON-WH-(2000SQM,.."]["last_from"] == "them"
    assert by["Patrick"]["last_from"] == "them"
    assert by["Shannah Audrey Ar..."]["last_from"] == "them"


def test_chat_rows_carry_the_name_lines_position_for_clicks():
    rows = chat_rows(SIDEBAR, SIDEBAR_RIGHT)
    assert rows[0]["y"] == pytest.approx(0.206) and rows[3]["y"] == pytest.approx(0.407)


# macOS sidebar as read live on 2026-10-31 (1085 x 790 window, sidebar edge at 0.2762; names
# changed): Viber's floating "back to top" button covered the bottom row's time, so OCR read
# no stamp there and that row ("Unknown") was merged into the preview of the row above it.
HIDDEN_STAMP = [
    _obs("All", 0.040, 0.224), _obs("Unread", 0.083, 0.224),
    _obs("Staff GC", 0.066, 0.461), _obs("9:23 PM", 0.222, 0.462), _obs("Hahaha", 0.066, 0.485),
    _obs("CLIENT-CS-(25-...", 0.066, 0.738), _obs("V/ 9:01PM", 0.198, 0.739),
    _obs("Me: sir wala pa pong sagot si", 0.066, 0.761), _obs("owner sa pin, update po namin...", 0.066, 0.785),
    _obs("OWNER-OS-(50...", 0.066, 0.830), _obs("V1 6:38PM", 0.198, 0.831),
    _obs("Me: wala pa pong sagot si owner,", 0.066, 0.856), _obs("update po namin kayo ulit sa T...", 0.066, 0.880),
    _obs("Unknown", 0.066, 0.923),
    _obs("Good day po, Alpha Premier", 0.066, 0.947), _obs("Realty po ito. available pa po ba...", 0.066, 0.971),
]


def test_a_row_whose_time_is_hidden_is_its_own_row():
    rows = chat_rows(HIDDEN_STAMP, 0.2762)
    assert [r["name"] for r in rows] == ["Staff GC", "CLIENT-CS-(25-...", "OWNER-OS-(50...", "Unknown"]
    owner, unknown = rows[2], rows[3]
    assert owner["preview"] == "Me: wala pa pong sagot si owner, update po namin kayo ulit sa T..."
    assert unknown["preview"] == "Good day po, Alpha Premier Realty po ito. available pa po ba..."
    assert unknown["time"] is None and unknown["y"] == pytest.approx(0.923)
    assert unknown["last_from"] is None   # its tick sits by the hidden time: can't tell
    assert owner["last_from"] == "me" and rows[0]["last_from"] == "them"


def test_a_hidden_time_row_under_a_one_line_preview_and_the_button_alone():
    one_line = [o for o in HIDDEN_STAMP if not o["text"].startswith("update po namin kayo")]
    rows = chat_rows(one_line, 0.2762)
    assert [r["name"] for r in rows][-2:] == ["OWNER-OS-(50...", "Unknown"]
    assert rows[-2]["preview"] == "Me: wala pa pong sagot si owner,"
    # With no row under it, the button's arrow OCR'd at its left edge is no row and no preview.
    button = [o for o in HIDDEN_STAMP if o["y"] < 0.9] + [_obs("T", 0.226, 0.935, 0.01)]
    rows = chat_rows(button, 0.2762)
    assert rows[-1]["name"] == "OWNER-OS-(50..." and "T" not in rows[-1]["preview"].split()


class SidebarBackend:
    """A chat list with a right-click menu per row; `read` rows offer "Mark as unread"."""
    def __init__(self, read=("Patrick",)):
        self.read, self.menu_for, self.clicks, self.keys = set(read), None, [], []

    class _Img:
        width, height = 1101, 1087

    def screenshot(self): return self._Img()
    def elements(self):
        els = [{"type": "Pane", "class": "SplitView", "name": "", "text": "", "parent": None, "rect": [308, 31, 1093, 1079]}]
        if self.menu_for:
            item = "Mark as unread" if self.menu_for in self.read else "Mark as read"
            for j, name in enumerate(("Add to Favorite chats", "Mute", "Delete conversation", item)):
                els.append({"type": "MenuItem", "class": "MenuItem", "name": name, "text": "", "parent": None,
                            "rect": [160, 760 + j * 36, 350, 790 + j * 36]})
        return els

    def observe(self, region=None): return SIDEBAR
    def wait_still(self, timeout=2.0): return 0.0
    def press_key(self, key, repeat=1): self.keys.append(key)

    def click_at(self, x, y, button="left", double=False):
        self.clicks.append((round(x), round(y), button))
        if button == "right":
            rows = chat_rows(SIDEBAR, SIDEBAR_RIGHT)
            self.menu_for = next(r["name"] for r in rows if abs(r["y"] * 1087 - y) < 25)
            return
        for e in self.elements():
            if e["type"] == "MenuItem" and e["rect"][1] <= y <= e["rect"][3]:
                assert e["name"] != "Delete conversation", "never!"
                self.read.add(self.menu_for) if e["name"] == "Mark as read" else None
        self.menu_for = None   # any left click closes the menu


def _install(monkeypatch, fake):
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    return fake


def test_mark_as_read_picks_only_that_menu_item(monkeypatch):
    fake = _install(monkeypatch, SidebarBackend())
    out = viber_ui.mark_as_read("JHAZEL-CS")
    assert out == {"chat": "JHAZEL-CS-(30-35...", "marked_read": True}
    assert fake.clicks[0] == (154, round((0.541 + 0.01) * 1087), "right")
    assert "JHAZEL-CS-(30-35..." in fake.read and fake.menu_for is None


def test_mark_as_read_leaves_a_read_chat_alone_and_closes_the_menu_with_the_chats_label(monkeypatch):
    fake = _install(monkeypatch, SidebarBackend(read=("Patrick",)))
    out = viber_ui.mark_as_read("Patrick")
    assert out == {"chat": "Patrick", "marked_read": False, "already_read": True}
    # Escape doesn't close this menu; the "Chats" label click does (and does nothing else).
    assert fake.keys == [] and fake.clicks[-1][2] == "left" and fake.clicks[-1][1] < 0.2 * 1087
    assert fake.menu_for is None


def test_mark_as_read_refuses_unknown_or_ambiguous_names(monkeypatch):
    fake = _install(monkeypatch, SidebarBackend())
    with pytest.raises(viber_ui.UIError, match="isn't in the visible chat list"):
        viber_ui.mark_as_read("Carlo S")
    with pytest.raises(viber_ui.UIError, match="Which chat"):
        viber_ui.mark_as_read(" ")
    assert fake.clicks == []


def test_read_chat_list_switches_to_the_unread_filter_and_back(monkeypatch):
    fake = _install(monkeypatch, SidebarBackend())
    out = viber_ocr.read_chat_list(unread_only=True)
    assert out["filter"] == "unread" and [r["name"] for r in out["chats"]][:2] == ["APG OCULAR", "Mark Anthony Santos"]
    # Unread pill (x≈0.083+w/2, y≈0.167+h/2) first, then back to All (x≈0.033).
    assert [c[2] for c in fake.clicks] == ["left", "left"]
    assert fake.clicks[0][0] > fake.clicks[1][0] and all(c[1] < 0.2 * 1087 for c in fake.clicks)
    plain = viber_ocr.read_chat_list()
    assert plain["filter"] == "all" and len(fake.clicks) == 2 and plain["chats"] == out["chats"]
