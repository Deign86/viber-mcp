"""Regression tests for bugs found while running the client-GC flow on the live Viber app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import viber_gc_logic as logic  # noqa: E402
from viber_ui import find_number_row  # noqa: E402

LORI = ("🏢 Company Name:  Plastic Manuf.\n🏗️ Type of Property:  Warehouse\n👤 Name: Lori\n"
        "📐 Preferred SQM: ⁨1200-1350⁩ sqm\n💰 Preferred Budget: \n"
        "📍 Preferred Location:  Valenzuela, Bulacan\n📱Viber: ⁨09639042544⁩\n\n"
        "Listings:\nQuezon City, 1,200 Novaliches")
LEAH = ("🏢 Company Name: Not specified (complete)\n👤 Name: Leah\n🏗️ Type of Property: WAREHOUSE\n"
        "📐 Preferred SQM: 240 sqm\n📍 Preferred Location: Las Piñas\n📱Viber: 09394208832\n\n"
        "Listing:\nLas Piñas, 240")
NEW = ("🏢 Company Name: Test Co.\n🏗 Type of Property: Warehouse\n👤 Name: Testclient\n"
       "📐 Preferred SQM: 300 sqm\n📍 Preferred Location: Quezon City\n📱Viber: 09754659812\n\n"
       "Intern: Testing\n\nListing:\n\nAntipolo, 520 Cupang")


# --------------------------------------------------------------- pending templates


def test_template_with_its_own_done_is_handled():
    texts = [LORI, "DONE-LORI-WH-(1200-1350SQM, BULACAN)"]
    assert logic.pending_templates(texts) == []


def test_done_spacing_differences_still_match():
    texts = ["chatter", LORI, "done-lori-wh-(1200-1350sqm,bulacan)"]
    assert logic.pending_templates(texts) == []


def test_template_named_differently_by_staff_is_not_duplicated():
    # Leah's GC was named after the company (JNT), not her: the DONE line doesn't match the
    # derived name, but a DONE after the template means it was handled.
    texts = [LEAH, "ma'am may listing na po", "DONE-JNT-WH-(240SQM, LAS PINAS)"]
    assert logic.pending_templates(texts) == []


def test_newest_template_without_done_is_pending():
    texts = [LEAH, "DONE-JNT-WH-(240SQM, LAS PINAS)", LORI, "DONE-LORI-WH-(1200-1350SQM, BULACAN)", NEW]
    assert logic.pending_templates(texts) == [4]


def test_chatter_and_done_lines_are_not_templates():
    assert not logic.is_template("Meron taguig 300+")
    assert not logic.is_template("DONE-KRIZZA-WH-(300-500SQM, TAGUIG)")
    assert logic.is_template(NEW)


def test_listing_after_blank_line_is_read():
    assert logic.parse_template(NEW)["listing"] == "Antipolo, 520 Cupang"


# ---------------------------------------------------------------- listing choice


def _listing(name, category="WAREHOUSE"):
    return {"folder_id": name.lower().replace(" ", "-"), "name": name, "category": category,
            "deal": "FOR LEASE/RENT"}


def test_sold_listing_is_never_chosen():
    pick = logic.choose_listing("Quezon City, 1,200 Novaliches", [
        _listing("Quezon City, 1,200 Novaliches"),
        _listing("QUEZON CITY 1200 sqm (Quirino highway)", category="SOLD"),
    ])
    assert pick["listing"]["name"] == "Quezon City, 1,200 Novaliches"
    assert all("SOLD" not in c["category"] for c in pick["candidates"])


def test_only_sold_matches_gives_no_listing():
    pick = logic.choose_listing("Makati, 260", [_listing("Makati, 260", category="SOLD")])
    assert pick["listing"] is None
    assert pick["problem"]


def test_exact_name_wins_over_partial_matches():
    pick = logic.choose_listing("Rizal, 4,400 Taytay", [
        _listing("Rizal, 4,400 Taytay"), _listing("Rizal, 1000, 2000 Taytay"),
    ])
    assert pick["listing"]["name"] == "Rizal, 4,400 Taytay"


def test_ambiguous_listing_is_refused():
    pick = logic.choose_listing("Taytay", [
        _listing("Rizal, 4,400 Taytay"), _listing("Rizal, 1000, 2000 Taytay"),
    ])
    assert pick["listing"] is None
    assert "2 active" in pick["problem"]


# ----------------------------------------------------------- client number rows


def _row(text, y, x=80.0, h=20.0):
    return {"text": text, "x": x, "y": y, "w": 150.0, "h": h, "W": 1100, "H": 967}


def test_number_row_found_with_display_name_above():
    rows = [_row("Contacts", 220, x=20), _row("Raineer Rosado", 263), _row("o", 268, x=270),
            _row("+639754659812", 284), _row("1 Contact", 331, x=128)]
    hit, display = find_number_row(rows, "+639754659812")
    assert hit["text"] == "+639754659812"
    assert display == "Raineer Rosado"  # not the "o" select tick


def test_number_row_tolerates_ocr_letter_for_digit():
    rows = [_row("Unknown", 263), _row("+6396243O92O", 284)]  # O read instead of 0
    hit, display = find_number_row(rows, "+639624309200"[:-1])
    assert hit is not None
    assert display == "Unknown"


def test_missing_number_returns_none():
    rows = [_row("Contacts", 220, x=20), _row("Max Alpha", 263), _row("+639308887734", 284)]
    assert find_number_row(rows, "+639000000017") == (None, "")


# ------------------------------------------------------------- privacy whitelist


def test_client_message_drops_listing_and_intern_for_new_layout():
    message = logic.client_message(NEW)
    assert "Antipolo" not in message and "Intern" not in message and "Listing" not in message
    assert "👤 Name: Testclient" in message


# ----------------------------------------------------------- jump to newest


def test_jump_down_button_is_the_chat_one_not_the_chat_list_one():
    from viber_ui import scroll_to_bottom_button

    elements = [
        {"type": "Button", "name": "", "class": "ScrollToTopItem", "text": "", "parent": 1,
         "rect": [252, 895, 296, 939]},   # chat list's "↑"
        {"type": "Button", "name": "", "class": "ScrollToBottomItem", "text": "", "parent": 31,
         "rect": [734, 855, 778, 899]},   # chat's "↓" (shown when scrolled up)
    ]
    assert scroll_to_bottom_button(elements)["rect"] == [734, 855, 778, 899]
    assert scroll_to_bottom_button(elements[:1]) is None  # at the bottom: no button


# ------------------------------------------- intro message for numbers not in the picker


def test_intro_message_uses_clients_first_name():
    fields = logic.parse_template(NEW)
    text = logic.intro_message(fields, "Hello po {name}! A GC is coming.")
    assert text == "Hello po Testclient! A GC is coming."


def test_intro_message_default_mentions_the_group_chat():
    text = logic.intro_message(logic.parse_template(LORI))
    assert text.startswith("Hello po Lori!")
    assert "group chat" in text


def test_intro_message_without_a_name_reads_cleanly():
    assert logic.intro_message({"name": ""}, "Hello po {name}! A GC is coming.") == "Hello po! A GC is coming."


# ------------------------------------------------------- duplicate-GC guard


def test_existing_gc_found_despite_ocr_misreads_and_truncation():
    lines = ["Conversations", "LORl-WH-(1200-1350S...", "Max Alpha: Hello @Unknown"]
    assert logic.search_says_gc_exists(lines, "LORI-WH-(1200-1350SQM, BULACAN)") == "yes"


def test_no_results_means_no_gc():
    assert logic.search_says_gc_exists(["No results found"], "ZED-WH-(1SQM, QC)") == "no"


def test_unreadable_highlighted_title_needs_header_check():
    # Viber renders the matching title in highlighted bold that OCR drops; only the preview
    # line survives — the caller must open the result and compare the chat header.
    lines = ["Conversations", "Beatriz Alpha: DONE-LORI-WH-"]
    assert logic.search_says_gc_exists(lines, "LORI-WH-(1200-1350SQM, BULACAN)") == "unknown"


def test_different_client_with_similar_name_is_not_a_match():
    lines = ["Conversations", "LORNA-WH-(300SQM, PASIG)"]
    assert logic.search_says_gc_exists(lines, "LORI-WH-(1200-1350SQM, BULACAN)") == "unknown"


def test_done_for_another_client_does_not_hide_a_new_template():
    # Dave's template was followed by Claudia's DONE (a quote-reply to her older template).
    claudia = ("🏢 Company Name: Not specified\n👤 Name: Claudia\n🏗️ Type of Property: COMMERCIAL SPACE\n"
               "📐 Preferred SQM: 380 sqm\n📍 Preferred Location: Pasig\n📱Viber: 09171234567")
    dave = ("🏢 Company Name:  \n👤 Name: Dave mur\n🏗️ Type of Property: warehouse\n"
            "📐 Preferred SQM: 177sqm\n💰 Preferred Budget: ~\n📍 Preferred Location: makati\n"
            "📱Viber: ⁨09178801748⁩\n\nIntern: Ar-jee\n\nListing:\n*Makati, 140 Bangkal")
    texts = [claudia, dave, "DONE-CLAUDIA-CS-(380SQM, PASIG)"]
    assert logic.pending_templates(texts) == [1]


def test_done_for_another_client_out_of_view_errs_toward_handled():
    # If the other client's template scrolled out of view, the unnamed DONE covers the nearest
    # template above it: a skip (reported), never a duplicate GC.
    dave = ("👤 Name: Dave mur\n🏗️ Type of Property: warehouse\n📐 Preferred SQM: 177sqm\n"
            "📍 Preferred Location: makati\n📱Viber: 09178801748")
    assert logic.pending_templates([dave, "DONE-CLAUDIA-CS-(380SQM, PASIG)"]) == []


# ------------------------------------------------------- download cleanup


def test_remove_downloads_only_deletes_inside_the_cache(tmp_path):
    import viber_drive

    cache = tmp_path / "drive-cache"
    cache.mkdir()
    photo = cache / "abc_1.jpg"
    photo.write_bytes(b"jpg")
    outside = tmp_path / "keep.jpg"
    outside.write_bytes(b"jpg")
    removed = viber_drive.remove_downloads([str(photo), str(outside)], cache)
    assert removed == 1
    assert not photo.exists()
    assert outside.exists()  # never touched


def test_clear_downloads_empties_the_cache(tmp_path):
    import viber_drive

    cache = tmp_path / "drive-cache"
    cache.mkdir()
    for n in range(3):
        (cache / f"id{n}_photo.jpg").write_bytes(b"jpg")
    report = viber_drive.clear_downloads(cache)
    assert report["removed"] == 3 and report["left"] == 0
    assert list(cache.iterdir()) == []


def test_clear_downloads_without_a_cache_folder_is_harmless(tmp_path):
    import viber_drive

    assert viber_drive.clear_downloads(tmp_path / "missing")["removed"] == 0


def test_prune_downloads_keeps_recent_photos(tmp_path):
    # Deleting right after send_files raced Viber's upload ("Your last N messages were not sent").
    import os
    import time

    import viber_drive

    cache = tmp_path / "drive-cache"
    cache.mkdir()
    fresh = cache / "new_1.jpg"
    fresh.write_bytes(b"jpg")
    old = cache / "old_1.jpg"
    old.write_bytes(b"jpg")
    week_ago = time.time() - 7 * 86400
    os.utime(old, (week_ago, week_ago))
    report = viber_drive.prune_downloads(cache, max_age_days=3)
    assert report["removed"] == 1 and report["kept"] == 1
    assert fresh.exists() and not old.exists()
    assert viber_drive.prune_downloads(tmp_path / "missing")["removed"] == 0


# ------------------------------------------- picker select circle (Rafael's row didn't tick)


def test_select_circle_lines_up_with_the_row():
    from viber_ui import select_circle_point

    elements = [{"type": "Edit", "class": "TextFieldItem", "name": "", "text": "", "parent": 1,
                 "rect": [88, 43, 284, 71]}]
    # Rafael's row as OCR'd: "Unknown" at y=257, "+639175177830" at y=278; circle drawn at (279, 273).
    name_line = {"text": "Unknown", "x": 80, "y": 257, "w": 60, "h": 20}
    number_line = {"text": "+639175177830", "x": 81, "y": 278, "w": 105, "h": 20}
    for point in (select_circle_point(elements, name_line, False),
                  select_circle_point(elements, number_line, True)):
        x, y = point
        assert abs(x - 279) <= 6 and abs(y - 273) <= 6


def test_select_circle_needs_the_search_box():
    from viber_ui import select_circle_point

    assert select_circle_point([], {"y": 257, "h": 20}, False) is None


# ------------------------------------------- New group screen (pencil toggles the panel)


def test_new_group_screen_state_reads_the_sidebar():
    from viber_ui import new_group_screen_state

    assert new_group_screen_state(["Rakuten Viber", "Q Search...", "Chats", "Unread"]) == "chats"
    assert new_group_screen_state(["Q Search name or number", "New group", "New Community"]) == "menu"
    assert new_group_screen_state(["Q Search name or number", "Group name*", "Invite contacts", "0/250"]) == "group_form"


# ------------------- chat-pane banner must not be mistaken for the picker row (Rafael)


def test_banner_running_into_the_chat_pane_is_not_sidebar_text():
    from viber_ui import _left_column, find_number_row

    def line(text, x, y, w):
        return {"text": text, "x": x, "y": y, "w": w, "h": 20, "W": 1101, "H": 967, "SB": 308}

    obs = [
        line("Invite contacts", 20, 176, 92),
        # the open 1:1 chat's banner: starts at x=325 (old 30% cutoff was 330) and runs right
        line("Rafael Luijie Esquivel +639175177830 is not in your contact list - save it", 325, 159, 600),
        line("Rafael Luijie Esquivel", 80, 257, 132),
        line("+639175177830", 81, 278, 105),
    ]
    rows = _left_column(obs)
    assert all(r["x"] + r["w"] <= 312 for r in rows)
    hit, display = find_number_row(rows, "+639175177830")
    assert (hit["x"], hit["y"]) == (81, 278)  # the picker row, not the banner
    assert display == "Rafael Luijie Esquivel"


def test_send_listing_keeps_the_photos_it_just_sent(tmp_path, monkeypatch):
    # Root cause of "all photos but the last not sent": send_listing deleted the downloads as
    # soon as the preview closed, while Viber was still uploading them (only the last survived,
    # because Viber still held it open). Fresh downloads must stay on disk after sending.
    import sys
    import types

    import viber_drive

    cache = tmp_path / "drive-cache"
    cache.mkdir()
    photos = [cache / f"id{n}_{n}.jpg" for n in range(3)]
    for p in photos:
        p.write_bytes(b"jpg")
    monkeypatch.setattr(viber_drive, "CACHE_DIR", cache)
    monkeypatch.setattr(viber_drive, "get_listing", lambda fid: {
        "doc": "d", "client_text": "t", "videos": [],
        "photos": [{"id": f"id{n}", "name": f"{n}.jpg"} for n in range(3)]})
    monkeypatch.setattr(viber_drive, "download", lambda ids: [str(p) for p in photos])
    monkeypatch.setitem(sys.modules, "viber_send",
                        types.SimpleNamespace(reply=lambda text: {"verified": True}))
    monkeypatch.setitem(sys.modules, "viber_ui",
                        types.SimpleNamespace(send_files=lambda paths: {"verified": True}))
    viber_drive.send_listing("folder")
    assert all(p.exists() for p in photos)


def test_blank_client_fields_are_sent_as_tilde():
    # Jensi template: blank SQM/Budget must go to the GC as "~", not empty.
    msg = logic.client_message(
        "Kindly fill this up:\n🏢 Company Name: (for massage spa)\n👤 Name: Jensi\n"
        "📐 Preferred SQM: \n💰 Preferred Budget:\n📍 Preferred Location: Metro Manila")
    assert "📐 Preferred SQM: ~" in msg
    assert "💰 Preferred Budget: ~" in msg
    assert "Company Name: (for massage spa)" in msg and "Kindly" not in msg


# --------------------------------------------------------------- unsend in group chats


def test_unsend_picks_group_chat_delete_items():
    # Group chats have no plain "Delete" — the choice is in the message menu itself
    # (unsend failed with "Viber didn't offer Delete" in JNT-WH).
    from viber_ui import _delete_menu_pick
    group = ["Delete for myself", "Delete without a trace", "Delete for all participants"]
    assert _delete_menu_pick(group, True) == "Delete for all participants"
    assert _delete_menu_pick(group, False) == "Delete for myself"
    assert _delete_menu_pick(["Delete"], True) == "Delete"
    assert _delete_menu_pick(["Delete for myself"], True) is None
    assert _delete_menu_pick([], True) is None


# --------------------------------------------------------------- foreign numbers / placeholder names


def test_foreign_numbers_with_country_code_are_accepted():
    # Caren (+81) and Jane (+60) were refused as "not a valid PH mobile number".
    assert logic.viber_e164("+818045489863") == "+818045489863"
    assert logic.viber_e164("+60 11 2726 6942") == "+601127266942"
    assert logic.viber_e164("0917 128 7661") == "+639171287661"
    assert logic.viber_e164("+63 966 665 5551") == "+639666655551"


def test_name_not_provided_falls_back_to_company():
    # "Name: Not provided" made the GC "NOT-WH-(270SQM, QC)".
    f = {"name": "Not provided", "company": "TPEHcare", "property_type": "Warehouse",
         "sqm": "270 sqm", "location": "Quezon City"}
    assert logic.gc_name(f)["gc_name"] == "TPEHCARE-WH-(270SQM, QC)"
    assert "Tpehcare" in logic.intro_message(f)
    f2 = dict(f, company="")
    assert "name missing" in logic.gc_name(f2)["uncertain"]


# ------------------------------------------------- right-clicking a message taller than the feed


class MenuBackend:
    """A chat with one long listing whose top is scrolled above the pane; right-clicking it
    opens the message menu only when the click lands on its visible part."""

    PANE = [308, 71, 1100, 959]             # header bar: 71..143
    COMPOSE = [400, 925, 730, 941]          # feed ends just above the message box
    LONG = [600, -590, 1000, 790]           # centre (y=100) is under the header

    def __init__(self):
        self.clicks = []
        self.menu_open = False

    def elements(self):
        def el(kind, cls, rect, parent=None, text="", name=""):
            return {"type": kind, "name": name, "class": cls, "text": text, "parent": parent, "rect": rect}
        out = [el("Pane", "StackView", self.PANE),
               el("Edit", "QQuickTextEdit", self.COMPOSE),
               el("Group", "FeedDelegate", [308, -600, 1100, 800], parent=0),
               el("Text", "TextEditItem", self.LONG, parent=2, text="FOR LEASE\n" * 60)]
        if self.menu_open:
            out += [el("MenuItem", "MenuItem", [700, 300, 800, 320], name=n) for n in ("Reply", "Delete")]
        return out

    def visible(self, x, y):
        l, t, r, b = self.LONG
        return l <= x <= r and max(t, self.PANE[1] + 72) <= y <= min(b, self.COMPOSE[1])

    def click_at(self, x, y, button="left"):
        self.clicks.append((x, y, button))
        if button == "right":
            self.menu_open = self.visible(x, y)

    def press_key(self, key):
        self.menu_open = False


def test_long_message_is_right_clicked_inside_its_visible_part(monkeypatch):
    # reply_to_message(n=2) on a long listing raised "'Reply' isn't offered ... Available:" with an
    # empty menu: the right-click went to the message's centre, which was above the chat pane.
    import backends
    import viber_ui

    fake = MenuBackend()
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    result = viber_ui.message_action(1, "Reply")
    assert result["chose"] == "Reply"
    x, y, button = fake.clicks[0]
    assert button == "right" and fake.visible(x, y)

    fake.clicks.clear()
    fake.menu_open = False
    viber_ui.unsend_message(n=1)
    x, y, button = fake.clicks[0]
    assert button == "right" and fake.visible(x, y)


def test_short_message_is_still_pressed_at_its_centre():
    import viber_ui

    m = {"kind": "text", "text": "ok", "rect": [600, 300, 700, 340], "view": (308, 143, 1100, 913)}
    assert viber_ui._press_point(m) == (650, 320)


# ------------------------------------------------------------- posting new listings


def test_new_listing_refuses_an_already_listed_folder(monkeypatch):
    # The One Premier unit was already in Drive ("*Las Piñas, 60 Almanza Uno"); posting it again
    # would have duplicated it in the listings GC and in Drive.
    import pytest
    import viber_drive

    existing = [{"folder_id": "x", "name": "*Las Piñas, 60 Almanza Uno", "category": "COMMERCIAL SPACE",
                 "deal": "For lease", "modified": "2026-10-13"}]
    monkeypatch.setattr(viber_drive, "_listings_root", lambda: "root")
    monkeypatch.setattr(viber_drive, "_children", lambda fid: [
        {"id": "c", "name": "COMMERCIAL SPACE", "mimeType": viber_drive.FOLDER}] if fid == "root" else [
        {"id": "d", "name": "For lease", "mimeType": viber_drive.FOLDER}])
    monkeypatch.setattr(viber_drive, "_property_folders", lambda: existing)
    text = "COMMERCIAL SPACE FOR RENT\n------\nOwner: Masters Tolentino"
    with pytest.raises(viber_drive.DriveError, match="already exists"):
        viber_drive._check_new_listing(text, "commercial space", "lease", "las piñas, 60 almanza uno", [])


def _plan(*_):
    return {"parent_id": "d", "path": "APR LISTING / WAREHOUSE / FOR LEASE/RENT",
            "folder_name": "Quezon City, 860 Apolonio Samson", "photos": []}


def test_new_listing_dry_run_sends_nothing(monkeypatch):
    import sys
    import types

    import viber_drive

    calls = []
    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    monkeypatch.setitem(sys.modules, "viber_send", types.SimpleNamespace(
        search_and_open=calls.append, reply=calls.append))
    monkeypatch.setitem(sys.modules, "viber_ui", types.SimpleNamespace())
    out = viber_drive.post_new_listing("t\n---\ni", "warehouse", "lease", "x", [], dry_run=True)
    assert calls == [] and out["posted"] is False
    assert out["added_to_drive_reply"] == "Added to Drive [File name:*Quezon City, 860 Apolonio Samson]"


def test_new_listing_stops_without_reposting_when_drive_fails(monkeypatch):
    # A failure after the GC post must report what's done, not raise (a retry would post twice).
    import sys
    import types

    import viber_drive

    sent = []
    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    monkeypatch.setitem(sys.modules, "viber_send", types.SimpleNamespace(
        search_and_open=lambda n: {"matched": "APR LISTING ONLY"},
        reply=lambda t: sent.append(t) or {"verified": True}))
    monkeypatch.setitem(sys.modules, "viber_ui", types.SimpleNamespace())

    def boom(*_):
        raise viber_drive.DriveError("403 insufficient permissions")
    monkeypatch.setattr(viber_drive, "add_listing_to_drive", boom)
    out = viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", [])
    assert out["posted"] is True and "drive" not in out
    assert "403" in out["STOP"] and "don't re-run" in out["STOP"]
    assert sent == ["t\n---\ni", viber_drive.GC_SEPARATOR]


def test_new_listing_refuses_wrong_chat(monkeypatch):
    import sys
    import types

    import pytest
    import viber_drive

    sent = []
    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    monkeypatch.setitem(sys.modules, "viber_send", types.SimpleNamespace(
        search_and_open=lambda n: {"matched": "APR LISTINGS ARCHIVE"}, reply=sent.append))
    monkeypatch.setitem(sys.modules, "viber_ui", types.SimpleNamespace())
    with pytest.raises(viber_drive.DriveError, match="Nothing sent"):
        viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", [])
    assert sent == []
