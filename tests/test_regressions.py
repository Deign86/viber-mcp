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


def test_message_search_rows_group_name_time_and_preview():
    # "LOIs sent today or yesterday": the name search found nothing; the messages tab lists
    # chat + time + the matching message. OCR lines (x, y normalized) from that tab.
    from viber_send import message_search_rows

    obs = [
        {"text": "Q LOI", "x": 0.08, "y": 0.05},
        {"text": "JONAS-CS-6SQM-QC", "x": 0.07, "y": 0.20},
        {"text": "11:02 AM", "x": 0.23, "y": 0.20},
        {"text": "Me: Sir Jonas, update po:", "x": 0.07, "y": 0.22},
        {"text": "na-receive at na-acknowl...", "x": 0.07, "y": 0.24},
        {"text": "MON-WH-(2000SQM,..", "x": 0.07, "y": 0.30},
        {"text": "Yesterday", "x": 0.22, "y": 0.30},
        {"text": "Beatriz Alpha: Good", "x": 0.07, "y": 0.32},
        {"text": "W 10:13 AM", "x": 0.21, "y": 0.40},
        {"text": "Anne", "x": 0.07, "y": 0.40},
        {"text": "Hello in the open chat", "x": 0.6, "y": 0.41},
    ]
    rows = message_search_rows(obs)
    assert [r["chat"] for r in rows] == ["JONAS-CS-6SQM-QC", "MON-WH-(2000SQM,..", "Anne"]
    assert rows[0]["time"] == "11:02 AM"
    assert rows[0]["preview"] == "Me: Sir Jonas, update po: na-receive at na-acknowl..."
    assert rows[1]["time"] == "Yesterday" and rows[1]["preview"] == "Beatriz Alpha: Good"
    assert rows[2]["time"] == "10:13 AM"  # the "seen" tick OCR'd as "W" is stripped
    assert rows[2]["preview"] == ""


def test_message_search_stamps_survive_ocr_garbling():
    # Live OCR of the result list: colons dropped or turned into "-", read ticks as "w".
    from viber_send import _row_stamp

    for raw in ("1243 PM", "1-25 PM", "w 1242 PM", "1102 AM", "9.49 AM", "W 1:58 PM", "Tuesday"):
        assert _row_stamp(raw), raw
    for not_a_stamp in ("Me: Thank you po", "Anne", "4", "12", "1,000 sqm"):
        assert _row_stamp(not_a_stamp) is None, not_a_stamp


def test_existing_group_dialog_is_detected():
    # THSWV / Anfernee: "Start chat" showed "You already have a group together!" and the run
    # failed with "group didn't appear" — the client already had BASEL-WH-(650SQM, LAS PINAS).
    from viber_ui import existing_group_from_dialog

    texts = ["APG CLIENTS", "+3", "You already have a group together!",
             "BASEL-WH-(650SQM, LAS PI...", "Open", "Create Another Group"]
    assert existing_group_from_dialog(texts) == "BASEL-WH-(650SQM, LAS PI..."
    assert existing_group_from_dialog(["THSWV-WH-(967.76SQM, CAVITE)", "8 participants"]) is None


def test_privacy_blocked_dialog_is_detected():
    # KRYS-CS: the client's privacy settings blocked group invites. Viber created the GC without
    # them and showed "Couldn't be added"; the run went on to the listing and failed at the photos.
    from viber_ui import privacy_blocked_from_dialog

    texts = ["KRYS-CS-(40SQM, TAYTAY-ANGONO)", "7 participants", "Couldn't be added",
             "Maureen Donor was not added to the group due to", "their privacy settings.", "OK"]
    assert privacy_blocked_from_dialog(texts) == "Maureen Donor"
    assert privacy_blocked_from_dialog(["Couldn't be added Maureen Donor was not added to the "
                                        "group due to their privacy settings."]) == "Maureen Donor"
    assert privacy_blocked_from_dialog(["KAYLE-WH-(400SQM, MANILA)", "8 participants"]) is None


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


def test_free_form_inquiry_only_needs_name_and_number():
    # OLIVER (J&T Express): a free-form note with no labels was refused ("name missing",
    # "unknown property type", "location not a recognizable place"). Only the name and Viber
    # number are required; the rest is inferred or taken from the listing.
    from viber_gc_logic import client_message, fill_from_listing, gc_name, infer_fields, parse_template

    note = "Oliver\nJ and t express\nWarehouse\n200-300k\n600-1000 Sqm\nNear San Juan , Mariana 09155329932"
    fields = infer_fields(note, parse_template(note))
    assert fields["name"] == "Oliver" and "09155329932" in fields["viber"]
    naming = gc_name(fields)
    assert naming["parts"]["type"] == "WH" and naming["parts"]["sqm"] == "600-1000"
    filled = fill_from_listing(naming, "Quezon City, 860 Apolonio Samson", "WAREHOUSE")
    assert filled["gc_name"] == "OLIVER-WH-(600-1000SQM, QC)" and not filled["uncertain"]
    msg = client_message(note)
    assert "👤 Name: Oliver" in msg and "09155329932" in msg

    # Blank size and place come from the listing folder name.
    filled = fill_from_listing(gc_name({"name": "Ana", "property_type": "", "sqm": "", "location": ""}),
                               "Laguna, 1,080 Sta. Rosa", "WAREHOUSE")
    assert filled["gc_name"] == "ANA-WH-(1080SQM, LAGUNA)" and not filled["uncertain"]


def test_listing_without_dashed_line_is_split_by_inference():
    # MON-CS: the North Caloocan doc had no dashed line, so no listing could be sent. The client
    # part ends with our company contact block; owner details below it stay internal.
    import pytest

    from viber_drive import DriveError, split_listing

    doc = ("COMMERCIAL SPACE FOR RENT/LEASE in North Caloocan City\r\n\r\nFloor Area: 33 sqm\r\n"
           "Contact Alpha Premier Realty for Inquiries & Viewing:\r\nrealty@alphapremiergroup.com\r\n"
           "0927 555 5803 | 0915 888 9482 | 0921 217 4555\r\n\r\n1 MONTH STANDARD COMMISSION\r\n"
           "Contact Details:\r\n• Owner Name: Anne\r\n• Contact Number: 09498657086\r\n")
    public, internal = split_listing(doc)
    assert public.endswith("0921 217 4555") and "09498657086" not in public
    assert internal.startswith("1 MONTH STANDARD COMMISSION")

    # No company block: cut before the first internal-looking line.
    public, _ = split_listing("OFFICE FOR LEASE\nFloor Area: 50 sqm\nRent: 45k\nExact address: 12 X St.\n"
                              "Owner: Y 09171234567")
    assert public == "OFFICE FOR LEASE\nFloor Area: 50 sqm\nRent: 45k"

    # An owner's number left in the inferred client part: refuse rather than leak it.
    with pytest.raises(DriveError):
        split_listing("OFFICE FOR LEASE\nCall 09171234567\nFloor Area: 50 sqm")
    # New posts still require the dashed line.
    with pytest.raises(DriveError):
        split_listing(doc, strict=True)


def test_send_listing_sends_the_folders_videos(monkeypatch):
    # MONETTE-WH: the Sta. Rosa folder had only a video and no photos; send_listing skipped
    # videos by default, so the client got the text alone. Videos now go by default, after the
    # photos, as their own batch.
    import sys
    import types

    import viber_drive

    monkeypatch.setattr(viber_drive, "get_listing", lambda fid: {
        "doc": "d", "client_text": "t",
        "photos": [{"id": "p1", "name": "1.jpg"}],
        "videos": [{"id": "v1", "name": "tour.mp4"}]})
    monkeypatch.setattr(viber_drive, "download", lambda ids: [f"/c/{i}" for i in ids])
    monkeypatch.setattr(viber_drive, "prune_downloads", lambda: None)
    batches = []
    monkeypatch.setitem(sys.modules, "viber_send",
                        types.SimpleNamespace(reply=lambda text: {"verified": True}))
    monkeypatch.setitem(sys.modules, "viber_ui", types.SimpleNamespace(
        send_files=lambda paths: batches.append(paths) or {"verified": True}))
    out = viber_drive.send_listing("folder")
    assert batches == [["/c/p1"], ["/c/v1"]]
    assert out["photos_sent"] == 1 and out["videos_sent"] == 1

    batches.clear()  # video-only folder: just the video batch, no empty photo send
    monkeypatch.setattr(viber_drive, "get_listing", lambda fid: {
        "doc": "d", "client_text": "t", "photos": [],
        "videos": [{"id": "v1", "name": "tour.mp4"}]})
    viber_drive.send_listing("folder")
    assert batches == [["/c/v1"]]


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


def test_unsend_picks_one_to_one_delete_for_me_and_contact():
    # 1:1 menus (2026-10-16, chat with Beatriz Alpha) have no plain "Delete" and no
    # "everyone": the for-everyone item is "Delete for me and <contact>". Unsend failed with
    # "too old to unsend" even for fresh messages. Never pick "Delete without a trace".
    from viber_ui import _delete_menu_pick
    one_to_one = ["Delete for myself", "Delete without a trace",
                  "Delete for me and Beatriz Alpha", "Check for spam"]
    assert _delete_menu_pick(one_to_one, True) == "Delete for me and Beatriz Alpha"
    assert _delete_menu_pick(one_to_one, False) == "Delete for myself"
    # Too old for everyone: only these remain — refuse rather than fall back to "without a trace".
    assert _delete_menu_pick(["Delete for myself", "Delete without a trace"], True) is None
    assert _delete_menu_pick(["Delete without a trace"], False) is None


def test_unsend_one_to_one_menu_with_isolate_marks():
    # Live 2026-10-16: the real 1:1 menu wraps the contact name in U+2068/U+2069, and unsaved
    # numbers show "Unknown". Unsend still failed with "too old to unsend for everyone".
    from viber_ui import _delete_menu_pick
    head = ["Reply", "Edit", "Copy message", "Select", "Translate", "Pin", "Forward",
            "Delete for myself", "Delete without a trace"]
    for name in ("Beatriz Alpha", "Unknown"):
        item = f"Delete for me and ⁨{name}⁩"
        menu = head + [item]
        assert _delete_menu_pick(menu, True) == item
        assert _delete_menu_pick(menu, False) == "Delete for myself"
    # Same with a non-breaking space and a leading isolate mark on the whole label.
    odd = "⁨Delete for me and ⁨Unknown⁩⁩"
    assert _delete_menu_pick(head + [odd], True) == odd
    # Never the myself / without-a-trace items for for_everyone=True.
    assert _delete_menu_pick(head, True) is None


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
            "folder_name": "Quezon City, 860 Apolonio Samson", "photos": ["C:/p/a.JPG", "C:/p/b.png"]}


class _GC:
    """Fake viber_send + viber_ui for the listings GC: records what is sent and shows my sent
    texts back in list_messages (newest first), like Viber does."""

    def __init__(self, matched="APR LISTING ONLY", echo=True, visible=()):
        self.matched, self.echo, self.sent = matched, echo, []
        self.visible = [{"from": "me", "kind": "text", "text": t} for t in visible]

    def search_and_open(self, name):
        return {"matched": self.matched}

    def reply(self, text):
        self.sent.append(text)
        if self.echo:
            self.visible.insert(0, {"from": "me", "kind": "text", "text": text})
        return {"verified": False}  # reply's own check gives false negatives on long texts

    def send_files(self, paths):
        self.sent.append(list(paths))
        return {"verified": True}

    def jump_to_latest(self):
        return {}

    def list_messages(self):
        return {"messages": [dict(m, n=i) for i, m in enumerate(self.visible, 1)]}

    def install(self, monkeypatch):
        import sys
        import types

        ns = types.SimpleNamespace(**{k: getattr(self, k) for k in (
            "search_and_open", "reply", "send_files", "jump_to_latest", "list_messages")})
        monkeypatch.setitem(sys.modules, "viber_send", ns)
        monkeypatch.setitem(sys.modules, "viber_ui", ns)
        return self


def test_new_listing_dry_run_sends_nothing_and_plans_the_upload(monkeypatch):
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    gc = _GC().install(monkeypatch)
    out = viber_drive.post_new_listing("t\n---\ni", "warehouse", "lease", "x", [], dry_run=True)
    assert gc.sent == [] and out["posted"] is False
    assert out["added_to_drive_reply"] == "Added to Drive [File name:*Quezon City, 860 Apolonio Samson]"
    up = out["drive_upload"]
    assert up["parent_folder_id"] == "d" and up["doc_title"] == "*Quezon City, 860 Apolonio Samson"
    assert up["doc_text"].startswith("t\n---\ni\n\nAdded to Drive: ")
    assert [p["name"] for p in up["photos"]] == ["AlphaPremier_Photo_01.jpg", "AlphaPremier_Photo_02.png"]


def test_new_listing_without_oauth_client_returns_the_plan(monkeypatch):
    # The Drive write ran as the read-only service account (0 bytes of storage): the folder was
    # created, then the doc upload failed with storageQuotaExceeded. Uploads never use the service
    # account; without the OAuth client the server only posts and returns the plan.
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    gc = _GC().install(monkeypatch)
    out = viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", ["C:/p/a.JPG", "C:/p/b.png"])
    assert out["posted"] is True and "STOP" not in out
    assert gc.sent == ["t\n---\ni", ["C:/p/a.JPG", "C:/p/b.png"], viber_drive.GC_SEPARATOR]
    assert out["drive_upload"]["parent_folder_id"] == "d"
    assert "drive" not in out and "connector" in out["next"]


def test_new_listing_uploads_to_drive_and_confirms(monkeypatch):
    # With the OAuth client the server adds the listing to Drive itself and posts the reply.
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    monkeypatch.setattr(viber_drive, "can_upload", lambda: True)
    calls = []
    monkeypatch.setattr(viber_drive, "upload_listing", lambda up: calls.append(up) or {"folder_id": "F"})
    monkeypatch.setattr(viber_drive, "confirm_listing_in_drive",
                        lambda name, n: {"confirmed": True, "name": name, "photos": n})
    _GC().install(monkeypatch)
    out = viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", ["C:/p/a.JPG"])
    assert out["drive"] == {"folder_id": "F"} and calls[0]["folder_name"].startswith("*")
    assert out["confirmed"] == {"confirmed": True, "name": "Quezon City, 860 Apolonio Samson", "photos": 2}


class _FakeWriter:
    """Records Drive create/list calls; `existing` maps (parent, name, mime) -> id."""

    def __init__(self, existing=None, images=0):
        self.existing, self.images, self.created = existing or {}, images, []

    def files(self):
        return self

    def list(self, q="", **kw):
        if "mimeType contains 'image/'" in q:
            return _Exec({"files": [{"id": f"img{i}"} for i in range(self.images)]})
        hit = [v for (p, n, m), v in self.existing.items() if f"'{p}' in parents" in q
               and f"name = '{n}'" in q and m in q]
        return _Exec({"files": [{"id": hit[0], "name": "x"}] if hit else []})

    def create(self, body=None, **kw):
        self.created.append(body)
        return _Exec({"id": f"new{len(self.created)}", "name": body["name"]})


class _Exec:
    def __init__(self, v):
        self.v = v

    def execute(self, **kw):
        return self.v


def test_upload_listing_reuses_folder_and_numbers_after_existing_photos(monkeypatch, tmp_path):
    # A folder made by hand earlier must be reused (not duplicated) and new photos must not
    # overwrite-number the ones already there.
    import viber_drive

    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    w = _FakeWriter(existing={("P", "*X, 1 Y", viber_drive.FOLDER): "F1"}, images=2)
    monkeypatch.setattr(viber_drive, "_writer", lambda: w)
    out = viber_drive.upload_listing({"parent_folder_id": "P", "folder_name": "*X, 1 Y",
                                      "doc_title": "*X, 1 Y", "doc_text": "a\n---\nb",
                                      "photos": [{"path": str(photo), "name": "ignored"}]})
    assert out["folder_id"] == "F1" and out["folder_created"] is False and out["doc_created"] is True
    assert [b["name"] for b in w.created] == ["*X, 1 Y", "AlphaPremier_Photo_03.jpg"]
    assert w.created[0]["mimeType"] == viber_drive.GDOC and w.created[0]["parents"] == ["F1"]


def test_new_listing_accepts_ocr_junk_after_the_gc_name(monkeypatch):
    # search_and_open matched "APR LISTING ONLY w" (a stray tick); the exact check refused it.
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    gc = _GC(matched="APR LISTING ONLY w").install(monkeypatch)
    assert viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", [])["posted"] is True
    assert gc.sent[0] == "t\n---\ni"


def test_new_listing_refuses_wrong_chat(monkeypatch):
    import pytest
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    for other in ("APR LISTINGS ARCHIVE", "APR LISTING ONLY OLD"):
        gc = _GC(matched=other).install(monkeypatch)
        with pytest.raises(viber_drive.DriveError, match="Nothing sent"):
            viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", [])
        assert gc.sent == []


def test_new_listing_refuses_a_repost(monkeypatch):
    import pytest
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    gc = _GC(visible=["t\n---\ni"]).install(monkeypatch)
    with pytest.raises(viber_drive.DriveError, match="already the latest post"):
        viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", [])
    assert gc.sent == []


def test_new_listing_stops_before_photos_if_the_text_did_not_post(monkeypatch):
    # A failure after the first send must report what's done, not raise (a retry would post twice).
    import viber_drive

    monkeypatch.setattr(viber_drive, "_check_new_listing", _plan)
    gc = _GC(echo=False).install(monkeypatch)
    out = viber_drive.post_new_listing("t\n---\ni", "w", "lease", "x", ["C:/p/a.JPG"])
    assert out["posted"] is False and "drive_upload" not in out
    assert "don't re-run" in out["STOP"]
    assert gc.sent == ["t\n---\ni"]


def test_lots_in_a_group_folder_are_listings(monkeypatch):
    # Lots for lease live one level deeper (CONDO / HOUSE AND LOT / FOR LEASE/ FOR RENT / LOT /
    # <property>); they were invisible to find_listings and to the duplicate check.
    import viber_drive

    def f(i, name, parent):
        return {"id": i, "name": name, "parents": [parent], "modifiedTime": "2026-10-16"}
    folders = [f("root", "APR LISTING", "x"), f("cat", "CONDO / HOUSE AND LOT", "root"),
               f("deal", "FOR LEASE/ FOR RENT", "cat"), f("grp", "LOT", "deal"),
               f("p1", "Cebu, 3,500 Tunga Moalboal", "grp"),
               f("p2", "Cavite (Sabutan Silang Cavite)", "deal")]
    monkeypatch.setattr(viber_drive, "_tree_cache", None)
    monkeypatch.setattr(viber_drive, "_list", lambda q, fields="": folders)
    got = {p["name"]: p["deal"] for p in viber_drive._property_folders()}
    assert got == {"Cebu, 3,500 Tunga Moalboal": "FOR LEASE/ FOR RENT / LOT",
                   "Cavite (Sabutan Silang Cavite)": "FOR LEASE/ FOR RENT"}


def test_new_listing_can_target_a_group_subfolder(monkeypatch):
    import viber_drive

    tree = {"root": [{"id": "cat", "name": "CONDO / HOUSE AND LOT"}],
            "cat": [{"id": "deal", "name": "FOR LEASE/ FOR RENT"}, {"id": "s", "name": "FOR SALE"}],
            "deal": [{"id": "lot", "name": "LOT"}, {"id": "ch", "name": "CONDO / HOUSE"}]}
    monkeypatch.setattr(viber_drive, "_listings_root", lambda: "root")
    monkeypatch.setattr(viber_drive, "_children", lambda fid: [
        dict(c, mimeType=viber_drive.FOLDER) for c in tree.get(fid, [])])
    monkeypatch.setattr(viber_drive, "_property_folders", lambda: [])
    plan = viber_drive._check_new_listing("LOT\n---\ni", "condo / house and lot", "lease",
                                          "Cebu, 3,500 Tunga Moalboal", [], subfolder="lot")
    assert plan["parent_id"] == "lot"
    assert plan["path"] == "APR LISTING / CONDO / HOUSE AND LOT / FOR LEASE/ FOR RENT / LOT"


def test_drive_listing_queries_retry_transient_errors(monkeypatch):
    # find_listings failed with WinError 10053 (connection aborted) and the folder walk with an
    # HTTP 500 "Internal Error"; the client only retries those when asked to.
    import types

    import viber_drive

    seen = []
    req = types.SimpleNamespace(execute=lambda num_retries=0: seen.append(num_retries) or {"files": []})
    files = types.SimpleNamespace(list=lambda **kw: req)
    monkeypatch.setattr(viber_drive, "_drive", lambda: types.SimpleNamespace(files=lambda: files))
    viber_drive._list("trashed = false")
    assert seen == [viber_drive.RETRIES] and viber_drive.RETRIES >= 3


def _drive_folder(monkeypatch, files):
    import viber_drive

    monkeypatch.setattr(viber_drive, "_property_folders", lambda: [
        {"folder_id": "F", "name": "Cebu, 3,500 Tunga Moalboal", "category": "C", "deal": "D"}])
    monkeypatch.setattr(viber_drive, "_children", lambda fid: files)
    monkeypatch.setattr(viber_drive, "_doc_text", lambda f: "LOT FOR LEASE\n---\ni")
    replies = []
    monkeypatch.setattr(viber_drive, "_quote_reply_posted", lambda t, r: replies.append((t, r)))
    return replies


def test_confirm_in_drive_posts_nothing_until_the_photos_are_there(monkeypatch):
    import pytest
    import viber_drive

    doc = {"name": "*Cebu, 3,500 Tunga Moalboal", "mimeType": viber_drive.GDOC, "id": "doc"}
    replies = _drive_folder(monkeypatch, [doc, {"name": "p1.jpg", "mimeType": "image/jpeg"}])
    _GC().install(monkeypatch)
    with pytest.raises(viber_drive.DriveError, match="1 photo"):
        viber_drive.confirm_listing_in_drive("Cebu, 3,500 Tunga Moalboal", 2)
    assert replies == []


def test_confirm_in_drive_quote_replies_added_to_drive(monkeypatch):
    import viber_drive

    doc = {"name": "*Cebu, 3,500 Tunga Moalboal", "mimeType": viber_drive.GDOC, "id": "doc"}
    imgs = [{"name": f"p{i}.jpg", "mimeType": "image/jpeg"} for i in (1, 2)]
    replies = _drive_folder(monkeypatch, [doc, *imgs])
    _GC(matched="APR LISTING ONLY w").install(monkeypatch)
    out = viber_drive.confirm_listing_in_drive("Cebu, 3,500 Tunga Moalboal", 2)
    assert out["confirmed"] is True
    assert replies == [("LOT FOR LEASE\n---\ni", "Added to Drive [File name:*Cebu, 3,500 Tunga Moalboal]")]


def test_new_listing_folder_gets_the_star_too(monkeypatch):
    # 2026-10-16: the General Trias folder was created without the '*' — new listings carry the
    # identifier on the folder name as well as the doc. confirm still finds it (with or without *).
    import viber_drive

    plan = {"parent_id": "P", "path": "x", "folder_name": "Cavite, 1,416 General Trias", "photos": []}
    up = viber_drive.drive_upload_plan("A\n---\nB", plan, "October 16, 2026")
    assert up["folder_name"] == up["doc_title"] == "*Cavite, 1,416 General Trias"

    doc = {"name": "*Cavite, 1,416 General Trias", "mimeType": viber_drive.GDOC, "id": "doc"}
    monkeypatch.setattr(viber_drive, "_property_folders", lambda: [
        {"folder_id": "F", "name": "*Cavite, 1,416 General Trias", "category": "C", "deal": "D"}])
    monkeypatch.setattr(viber_drive, "_children", lambda fid: [doc])
    monkeypatch.setattr(viber_drive, "_doc_text", lambda f: "A\n---\nB")
    replies = []
    monkeypatch.setattr(viber_drive, "_quote_reply_posted", lambda t, r: replies.append(r))
    _GC().install(monkeypatch)
    for name in ("*Cavite, 1,416 General Trias", "Cavite, 1,416 General Trias"):
        assert viber_drive.confirm_listing_in_drive(name, 0)["folder_id"] == "F"
    assert replies == ["Added to Drive [File name:*Cavite, 1,416 General Trias]"] * 2


# ------------------------------------------------------------- mentioning the client


def test_unknown_picker_label_is_not_a_viber_name():
    # 2026-10-16: an unsaved number showed as "Unknown" in the group picker, and the flow tried
    # to mention "Unknown" ("'Unknown' isn't in this chat's mention list").
    import viber_ui

    for label in ("Unknown", "", "  ", "+639531830814", "0953 183 0814"):
        assert viber_ui.is_placeholder_name(label), label
    assert not viber_ui.is_placeholder_name("Anthony")


def test_client_name_is_read_from_the_unsaved_number_banner():
    import viber_ui

    lines = ["Viber Out", "Anthony", "Active",
             "Anthony +639531830814 is not in your contact list - save it using"]
    assert viber_ui.name_from_contact_banner(lines, "+639531830814") == "Anthony"
    # OCR letter-for-digit slips in the number still match; another number's banner doesn't.
    ocr = ["Mark Anthony Santos +6395318308l4 is not in your contact list"]
    assert viber_ui.name_from_contact_banner(ocr, "+639531830814") == "Mark Anthony Santos"
    assert viber_ui.name_from_contact_banner(lines, "+639922064099") == ""


def test_mention_must_be_the_whole_name():
    import viber_ui

    assert viber_ui.mention_inserted("Hello @Anthony", "Hello ", "Anthony")
    assert viber_ui.mention_inserted("Hello @Anthony ", "Hello ", "Anthony")
    assert not viber_ui.mention_inserted("Hello @Mark Anthony Santos", "Hello ", "Anthony")
    assert not viber_ui.mention_inserted("Hello @Anth", "Hello ", "Anthony")
    assert viber_ui.mention_inserted("DONE-X — @Jean Ashley existing client po.", "DONE-X — ",
                                     "Jean Ashley", " existing client po.")


class MentionBackend:
    """Viber's mention list for a group with "Mark Anthony Santos" (the CEO) and the client
    "Anthony": typing "@Anth" lists both, the CEO highlighted first; Down moves the highlight,
    Tab inserts the highlighted name, a space closes the list. Sending with the list open sends
    the highlighted person (what mentioned the CEO on 2026-10-16)."""

    ROWS = ["Mark Anthony Santos", "Anthony"]
    COMPOSE = (320, 900, 1000, 940)

    def __init__(self):
        self.value, self.open, self.highlight, self.sent = "", False, 0, None

    class _Img:
        width, height = 1100, 967

    def screenshot(self):
        return self._Img()

    def elements(self):
        return [{"type": "Pane", "class": "SplitView", "rect": (308, 0, 1100, 967)},
                {"type": "Edit", "class": "QQuickTextEdit_QML_1", "rect": self.COMPOSE}]

    def observe(self):
        if not self.open:
            return [{"text": "Hello everyone", "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.02}]
        return [{"text": r, "x": 0.35, "y": (850 - 40 * i) / 967, "w": 0.2, "h": 0.02}
                for i, r in enumerate(self.ROWS)]

    def click_compose(self):
        pass

    def delete(self):
        self.value, self.open = "", False

    def _value(self):
        return self.value

    def type_into_focus(self, text):
        self.value += text
        if "@" in text:
            self.open, self.highlight = True, 0
        elif text.startswith(" "):
            self.open = False

    def press_key(self, key, repeat=1):
        if key == "down" and self.open:
            self.highlight = (self.highlight + repeat) % len(self.ROWS)
        elif key == "tab" and self.open:
            self.value = self.value[:self.value.rindex("@")] + "@" + self.ROWS[self.highlight]
        elif key == "backspace":
            self.value = self.value[:-repeat]
        elif key == "escape":
            self.open = False

    def click_at(self, x, y, button="left"):
        pass

    def click_send(self):
        self.sent = "Hello @" + self.ROWS[self.highlight] if self.open else self.value


def test_mention_picks_the_exact_member_not_a_longer_name_containing_it(monkeypatch):
    import backends
    import viber_ui

    fake = MentionBackend()
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    out = viber_ui.mention("Anthony")
    assert fake.sent == "Hello @Anthony "
    assert out["mentioned"] == "Anthony" and not fake.open


def test_mention_of_a_non_member_sends_nothing(monkeypatch):
    import pytest
    import backends
    import viber_ui

    fake = MentionBackend()
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    with pytest.raises(viber_ui.UIError, match="mention list"):
        viber_ui.mention("Unknown")
    assert fake.sent is None and fake.value == ""
