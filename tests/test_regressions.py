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
                        types.SimpleNamespace(reply=lambda text, expect_chat="": {"verified": True},
                                              require_open_chat=lambda expect: {}))
    monkeypatch.setitem(sys.modules, "viber_ui",
                        types.SimpleNamespace(send_files=lambda paths, expect_chat="": {"verified": True}))
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
                        types.SimpleNamespace(reply=lambda text, expect_chat="": {"verified": True},
                                              require_open_chat=lambda expect: {}))
    monkeypatch.setitem(sys.modules, "viber_ui", types.SimpleNamespace(
        send_files=lambda paths, expect_chat="": batches.append(paths) or {"verified": True}))
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

    def observe(self, region=None):
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
        self.value = ""  # the box clears on send (send_with_mention polls for it)


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


def test_same_listing_with_brgy_prefix_is_a_duplicate():
    # "*Quezon City, 36 Brgy. Vasra" was in Drive already; "Quezon City, 36 Vasra" got added
    # again because the duplicate check compared exact names.
    import viber_drive
    assert viber_drive.listing_key("*Quezon City, 36 Brgy. Vasra") == \
        viber_drive.listing_key("Quezon City, 36 Vasra")
    assert viber_drive.listing_key("Quezon City, 36 Vasra") != \
        viber_drive.listing_key("Quezon City, 360 Vasra")


class UnknownMentionBackend(MentionBackend):
    """A new GC with the staff and one brand-new client number, which Viber's mention list
    shows as "Unknown"."""

    ROWS = ["Max Alpha", "Unknown"]

    def wait_still(self, timeout=2.0):
        return 0.0


def _run_gc_with_unknown_client(monkeypatch, fake):
    import backends
    import viber_drive
    import viber_gc
    import viber_send
    import viber_ui

    done = []
    plan = {"ready": True, "problems": [], "gc_name": "LORI-WH-(1200-1350SQM)-CAVITE",
            "done_reply": "DONE-LORI-WH-(1200-1350SQM)-CAVITE", "client_number": "+639531830814",
            "client_viber_name": "Unknown", "intro_message_first": "", "client_message": "Name: Lori",
            "listing": {"folder_id": "F"}, "templates_chat": "APG CLIENTS",
            "template_text": "Name: Lori\nViber: 09531830814"}
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    monkeypatch.setattr(viber_gc.time, "sleep", lambda s: None)
    monkeypatch.setattr(viber_gc, "plan_client_gc", lambda *a, **k: dict(plan))
    monkeypatch.setattr(viber_gc, "_config", lambda: {"members": ["Max Alpha"], "self": ""})
    monkeypatch.setattr(viber_gc, "client_name_from_chat", lambda number: "")  # no banner name
    monkeypatch.setattr(viber_gc, "_find_message", lambda text, screens=6: {"n": 7})
    monkeypatch.setattr(viber_send, "open_chat", lambda number: {"opened": number})
    monkeypatch.setattr(viber_ui, "create_group_from_chat", lambda *a, **k: {
        "created": True, "renamed": True, "client_display_name": "Unknown",
        "privacy_blocked": None})
    monkeypatch.setattr(viber_ui, "reply_to_message",
                        lambda n, text, send=True: done.append((n, text)) or {"sent": True})
    monkeypatch.setattr(viber_send, "reply", lambda text: {"sent": True})
    monkeypatch.setattr(viber_send, "search_and_open", lambda chat: {})
    monkeypatch.setattr(viber_drive, "send_listing", lambda folder_id: {"sent": True})
    monkeypatch.setattr(viber_drive, "prune_downloads", lambda: {})
    return viber_gc.create_client_gc(1, dry_run=False), done


def test_unknown_client_is_mentioned_as_unknown_and_done_is_posted(monkeypatch):
    # 2026-10-20: brand-new numbers are "Unknown" in the picker AND the mention list, and the flow
    # stopped with "the client's Viber name couldn't be read". Staff mention them as @Unknown.
    fake = UnknownMentionBackend()
    out, done = _run_gc_with_unknown_client(monkeypatch, fake)
    assert fake.sent == "Hello po, @Unknown! Please check the listing above po."
    assert out["done"] and out["steps"]["mention"]["mentioned"] == "Unknown"
    assert done == [(7, "DONE-LORI-WH-(1200-1350SQM)-CAVITE")]


def test_two_unknown_participants_stop_before_mention_and_done(monkeypatch):
    import pytest
    import viber_gc

    fake = UnknownMentionBackend()
    fake.ROWS = ["Unknown", "Unknown"]
    out = None
    with pytest.raises(viber_gc.GCError, match="ambiguous"):
        out = _run_gc_with_unknown_client(monkeypatch, fake)
    assert out is None and fake.sent is None and fake.value == ""


# --- 2026-10-21: client GCs are built from the client's 1:1 chat (Add participants), because the
# New group screen failed ("GC NOT CREATED — name missing"). Fixtures measured on the live app.

def _el(type_, cls, rect, name="", text="", parent=None):
    return {"type": type_, "class": cls, "rect": rect, "name": name, "text": text, "parent": parent}


ADD_PICKER = [
    _el("Edit", "TextFieldItem", [112, 43, 252, 71]),                 # left sidebar search
    _el("Edit", "QQuickTextEdit", [460, 924, 724, 943]),               # message box
    _el("Pane", "SideBarContent", [793, 31, 1093, 959]),
    _el("Group", "SearchItem", [809, 87, 1077, 115]),
    _el("Button", "ModalButton", [905, 915, 981, 950], name="Save"),
    _el("Edit", "TextFieldItem", [845, 87, 1065, 115], text="Arjee Alpha"),
]


def test_add_participants_picker_is_the_side_panel_search_not_the_sidebar_one():
    from viber_ui import add_participants_picker
    search, save = add_participants_picker(ADD_PICKER)
    assert search["rect"] == [845, 87, 1065, 115] and save["name"] == "Save"
    assert add_participants_picker(ADD_PICKER[:2]) is None  # no side panel → not open


def test_picker_rows_and_counter_come_from_the_side_panel_only():
    from viber_ui import _invite_count, _panel_rows, picker_name_row
    search = ADD_PICKER[-1]
    obs = [{"text": "Arjee Alpha", "x": 80, "y": 300, "w": 90, "h": 16},     # a chat in the sidebar
           {"text": "Arjee Alpha", "x": 849, "y": 236, "w": 90, "h": 16},
           {"text": "+639704565482", "x": 849, "y": 257, "w": 110, "h": 16},
           {"text": "3/250", "x": 1046, "y": 926, "w": 32, "h": 14}]
    rows = _panel_rows(obs, search)
    assert picker_name_row(rows, "Arjee AIpha")["x"] == 849   # OCR I/l folded
    assert _invite_count([], rows) == 3


def test_select_circle_uses_the_given_picker_search_box():
    from viber_ui import select_circle_point
    x, y = select_circle_point(ADD_PICKER, {"y": 236, "h": 16}, False, search=ADD_PICKER[-1])
    assert 1055 <= x <= 1070 and 240 <= y <= 255   # the ○ measured at (1063, 247)


def test_rename_box_is_the_edit_level_with_the_pencil():
    from viber_ui import name_edit_button, rename_field
    els = [_el("Pane", "SideBarContent", [793, 31, 1093, 959]),
           _el("Button", "IconButton", [1061, 47, 1077, 63]),          # panel close ✕
           _el("Button", "IconButton", [1025, 245, 1045, 265]),        # pencil
           _el("Button", "IconButton", [1057, 245, 1077, 265]),        # star
           _el("Edit", "TextFieldItem", [112, 43, 252, 71]),
           _el("Edit", "TextFieldItem", [809, 246, 1013, 265], text="Group")]
    pencil = name_edit_button(els)
    assert pencil["rect"] == [1025, 245, 1045, 265]
    assert rename_field(els, pencil)["text"] == "Group"


def test_groups_in_common_page_lists_existing_client_gcs():
    from viber_ui import groups_in_common_from_page
    assert groups_in_common_from_page(["x", "Groups in common", "VER-OS-(30SQM, MAKATI)"]) == \
        ["VER-OS-(30SQM, MAKATI)"]
    assert groups_in_common_from_page(["x", "Groups in common", "No groups in common"]) == []


def test_existing_client_stops_before_any_group_is_made(monkeypatch):
    import pytest
    import viber_gc
    import viber_ui

    calls = []
    monkeypatch.setattr(viber_gc, "plan_client_gc", lambda *a, **k: {
        "ready": True, "problems": [], "gc_name": "VER-OS-(30SQM, MAKATI)", "client_number": "+639913518179",
        "client_viber_name": "", "intro_message_first": "", "client_message": "Name: Ver",
        "listing": None, "templates_chat": "APG CLIENTS", "template_text": "Name: Ver"})
    monkeypatch.setattr(viber_gc, "_config", lambda: {"members": ["Max Alpha"], "self": ""})
    monkeypatch.setattr(viber_gc, "client_name_from_chat", lambda number: "")
    monkeypatch.setattr(viber_gc.viber_send, "open_chat", lambda number: {})
    monkeypatch.setattr(viber_gc.viber_send, "reply", lambda text: calls.append(text))
    monkeypatch.setattr(viber_ui, "existing_group_with", lambda number: None)  # pre-check missed it
    monkeypatch.setattr(viber_ui, "create_group_from_chat", lambda *a, **k: {
        "created": False, "existing_group": "VER-OS-(30SQM, MAKATI)", "client_display_name": ""})
    with pytest.raises(viber_gc.GCError, match="EXISTING CLIENT"):
        viber_gc.create_client_gc(1, dry_run=False)
    assert calls == []   # nothing posted


def test_existing_client_gets_no_intro_message(monkeypatch):
    # 2026-10-21: Ray (+639123098637) already shared REALYN-CS with us, but create_client_gc sent
    # "I'll be creating a Viber group chat for you shortly" before noticing, then made no GC.
    # Deign's hard rule: one GC per client — check Groups in common BEFORE the intro.
    import pytest
    import viber_gc
    import viber_ui

    sent, groups = [], []
    monkeypatch.setattr(viber_gc, "plan_client_gc", lambda *a, **k: {
        "ready": True, "problems": [], "gc_name": "RAY-CS-(130SQM, PASAY)", "client_number": "+639123098637",
        "client_viber_name": "", "intro_message_first": "Hello po Ray! I'll be creating a GC...",
        "client_message": "Name: Ray", "listing": None, "templates_chat": "APG CLIENTS",
        "template_text": "Name: Ray"})
    monkeypatch.setattr(viber_gc, "_config", lambda: {"members": ["Max Alpha"], "self": ""})
    monkeypatch.setattr(viber_gc.viber_send, "open_chat", lambda number: {})
    monkeypatch.setattr(viber_gc.viber_send, "send_message", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(viber_ui, "existing_group_with", lambda number: "REALYN-CS-(100-150SQM, MANDALUYONG)")
    monkeypatch.setattr(viber_ui, "create_group_from_chat", lambda *a, **k: groups.append(a))
    with pytest.raises(viber_gc.GCError, match="EXISTING CLIENT.*REALYN-CS"):
        viber_gc.create_client_gc(1, dry_run=False)
    assert sent == [] and groups == []


def test_sponsored_ad_close_point():
    # 2026-10-21: a "Sponsored" ad box at the bottom of the chat list made Qt's UIA tree loop
    # (the content root listed itself as a child) and drop the chat pane, so send_files /
    # send_listing / reply failed with "No chat is open" while a chat was clearly open.
    # Closing the ad restores the tree. Rect measured on the live app; ✕ was at (284, 725).
    from backends._winlogic import ad_close_point
    ad = {"type": "Document", "class": "QObject", "rect": [8, 709, 308, 959]}
    assert ad_close_point([ad]) == (284, 725)
    assert ad_close_point([{"type": "Group", "class": "AdsWebView", "rect": [0, 0, 0, 0]}]) is None


def test_one_to_one_check_rejects_the_client_gc_that_shows_their_number():
    # 2026-10-21: open_chat('+639913518179') left the VER-OS GC open (the viber:// link didn't
    # navigate), and that GC shows the number inside the posted template — building the group
    # "from the client's chat" there would have added people to the wrong chat.
    from viber_ui import is_one_to_one_with
    gc = [{"text": "VER-OS-(30SQM, MAKATI)", "y": 97}, {"text": "8 participants", "y": 121},
          {"text": "Viber: +639913518179", "y": 447}]
    assert not is_one_to_one_with(gc, "+639913518179", 143)
    dm = [{"text": "Unknown", "y": 97}, {"text": "Active", "y": 121},
          {"text": "+639913518179 is not in your contact list - save it using Viber on", "y": 165}]
    assert is_one_to_one_with(dm, "+639913518179", 143)
    assert not is_one_to_one_with(dm, "+639175177830", 143)   # someone else's chat
    header_only = [{"text": "Unknown", "y": 97}, {"text": "+639913518179", "y": 121}]
    assert is_one_to_one_with(header_only, "+639913518179", 143)


def test_viber_plus_promo_close_point():
    # 2026-10-21: closing the Sponsored ad popped a "Discover a Premium Experience" dialog that
    # blocked the Add participants picker. Measured on the live app.
    from backends._winlogic import promo_close_point
    els = [{"type": "Pane", "class": "x", "name": "", "rect": [0, 0, 1, 1], "parent": None},
           {"type": "Pane", "class": "x", "name": "", "rect": [0, 0, 1, 1], "parent": None},
           {"type": "Window", "class": "QQuickPopupItem", "name": "", "rect": [341, 210, 761, 780], "parent": None},
           {"type": "Button", "class": "IconButton", "name": "", "rect": [729, 226, 745, 242], "parent": 2},
           {"type": "Button", "class": "SolidButton", "name": "Take me there", "rect": [449, 528, 653, 560], "parent": 2}]
    assert promo_close_point(els) == (737, 234)
    assert promo_close_point(els[:4]) is None   # some other popup (no "Take me there"): leave it


def test_side_panel_back_never_hits_the_chat_history_back():
    # 2026-10-21: leaving "Groups in common" clicked the chat pane's ← (history back) at
    # [308, 31, 348, 71] — it satisfied "top-left, left of panel+50" — and jumped from the
    # client's 1:1 chat to their existing GC, where Add participants then opened.
    from viber_ui import side_panel_back_point
    history_back = {"type": "Button", "class": "IconButton", "name": "", "text": "",
                    "rect": [308, 31, 348, 71], "parent": None}
    panel = {"type": "Pane", "class": "SideBarContent", "name": "", "text": "",
             "rect": [793, 31, 1093, 959], "parent": None}
    assert side_panel_back_point([history_back, panel]) == (816, 51)   # fallback spot, not 328
    arrow = dict(history_back, rect=[804, 39, 828, 63])
    assert side_panel_back_point([history_back, panel, arrow]) == (816, 51)
    assert side_panel_back_point([history_back]) is None


# --- 2026-10-21: "move the listing folder to SOLD" had no tool (done by a one-off script).

SOLD_KIDS = [{"id": "h", "name": "House/Lot Sold"}, {"id": "c", "name": "SOLD COMMERCIAL SPACE"},
             {"id": "o", "name": "SOLD OFFICE SPACE"}, {"id": "w", "name": "SOLD WAREHOUSE"}]


def test_sold_target_matches_live_sold_folders():
    from viber_drive import sold_target
    assert sold_target("WAREHOUSE", SOLD_KIDS)["id"] == "w"
    assert sold_target("COMMERCIAL SPACE", SOLD_KIDS)["id"] == "c"
    assert sold_target("OFFICE SPACE", SOLD_KIDS)["id"] == "o"
    assert sold_target("CONDO / HOUSE AND LOT", SOLD_KIDS)["id"] == "h"
    assert sold_target("LOT", SOLD_KIDS)["id"] == "h"
    assert sold_target("VO", SOLD_KIDS) is None          # no subfolder: goes into SOLD itself


def test_move_listing_to_sold_moves_out_of_its_deal_folder(monkeypatch):
    import pytest
    import viber_drive
    calls = []

    class Req:
        def __init__(self, kw): self.kw = kw
        def execute(self, num_retries=0): calls.append(self.kw); return {}

    class Files:
        def update(self, **kw): return Req(kw)

    class Svc:
        def files(self): return Files()

    folder = "application/vnd.google-apps.folder"
    monkeypatch.setattr(viber_drive, "_property_folders", lambda: [
        {"folder_id": "L", "name": "Las Piñas City, 240 Almanza 1", "category": "WAREHOUSE",
         "deal": "FOR LEASE/RENT", "parent_id": "lease", "modified": ""}])
    monkeypatch.setattr(viber_drive, "_listings_root", lambda: "root")
    monkeypatch.setattr(viber_drive, "_children", lambda fid: {
        "root": [{"id": "S", "name": "SOLD", "mimeType": folder}],
        "S": [dict(k, mimeType=folder) for k in SOLD_KIDS]}[fid])
    monkeypatch.setattr(viber_drive, "_writer", lambda: Svc())
    out = viber_drive.move_listing_to_sold("L")
    assert calls[0]["addParents"] == "w" and calls[0]["removeParents"] == "lease"
    assert out["to"].endswith("SOLD / SOLD WAREHOUSE")
    with pytest.raises(viber_drive.DriveError, match="already be in SOLD"):
        viber_drive.move_listing_to_sold("not-a-listing")


def test_move_listing_to_sold_refuses_a_folder_already_in_sold(monkeypatch):
    # 2026-10-21: run on the Las Piñas folder already in SOLD / SOLD WAREHOUSE, the tool saw it as
    # category "SOLD", deal "SOLD WAREHOUSE" and moved it UP into SOLD itself.
    import pytest
    import viber_drive
    monkeypatch.setattr(viber_drive, "_property_folders", lambda: [
        {"folder_id": "L", "name": "Las Piñas City, 240 Almanza 1", "category": "SOLD",
         "deal": "SOLD WAREHOUSE", "parent_id": "w", "modified": ""}])
    monkeypatch.setattr(viber_drive, "_writer", lambda: pytest.fail("must not write"))
    with pytest.raises(viber_drive.DriveError, match="already be in SOLD"):
        viber_drive.move_listing_to_sold("L")


# ------------------------------------------------------------- speed-ups (2026-10-21)
# Profiled on the live app: a full OCR read was ~0.5 s (PNG encode+decode cost more than the
# recognition), every value read re-walked the UIA tree, and fixed sleeps dominated the tools.


def test_region_box_crops_and_falls_back_to_the_whole_image():
    from backends._winlogic import region_box
    assert region_box(None, 1000, 900) is None
    assert region_box((0, 0, 1, 1), 1000, 900) is None            # whole image: no crop
    assert region_box((0, 0, 0.34, 1), 1000, 900) == (0, 0, 340, 900)
    assert region_box((0, 0, 1, 0.14), 1101, 967) == (0, 0, 1101, 136)
    assert region_box((0.3, 0.2, 0.6, 0.26), 1000, 900) == (300, 180, 600, 234)
    assert region_box((0, 0, 0.02, 1), 1000, 900) is None         # too thin for OCR → whole
    assert region_box((0, 0, 1.5, 2), 1000, 900) is None          # clamped to the image → whole


def test_offset_observations_maps_a_crop_back_to_window_coordinates():
    from backends._winlogic import offset_observations
    # A word at the crop's centre of a 30%-wide sidebar crop is at x=0.15 of the window.
    obs = [{"text": "Max Alpha", "x": 0.5, "y": 0.5, "w": 0.4, "h": 0.1}]
    out = offset_observations(obs, (0, 0, 300, 900), 1000, 900)
    assert out[0]["x"] == 0.15 and out[0]["y"] == 0.5
    assert abs(out[0]["w"] - 0.12) < 1e-9 and abs(out[0]["h"] - 0.1) < 1e-9
    # Header band crop: y is scaled into the band, x untouched.
    out = offset_observations(obs, (0, 0, 1000, 135), 1000, 900)
    assert out[0]["x"] == 0.5 and out[0]["y"] == 0.075
    assert out[0]["text"] == "Max Alpha"


def test_number_confirmed_by_the_banner_but_never_by_header_or_message_text():
    from viber_send import number_confirmed
    number = "+639175177830"
    # The chat title sits at y≈0.10 — the same band a message right under the header can use
    # (live: "My Notes" at y=0.104, first bubble from y≈0.11), so the header never confirms.
    header = {"text": "+63 917 517 7830", "x": 0.35, "y": 0.104}
    banner = {"text": "Rafael +639175177830 is not in your contact list - save it", "x": 0.31, "y": 0.16}
    garbled = {"text": "Rafael +63917517783O is not in your contact Iist", "x": 0.31, "y": 0.16}
    template = {"text": "Viber: 09175177830", "x": 0.35, "y": 0.4}       # a template line in a chat
    top_message = {"text": "Viber: 09175177830", "x": 0.35, "y": 0.104}  # right under the header
    assert number_confirmed([banner], number)
    assert number_confirmed([garbled], number)                   # OCR's O/0, l/I slips
    assert not number_confirmed([header], number)
    assert not number_confirmed([template], number)
    assert not number_confirmed([top_message], number)
    assert not number_confirmed([banner], "+639171234567")       # another number's chat
    assert not number_confirmed([{"text": "+63917 is not in your contact list", "x": 0.3, "y": 0.2}], "+63917")
    assert not number_confirmed([banner], "Max Alpha")           # search_and_open passes a name




def test_wait_for_sendable_chat_accepts_as_soon_as_the_number_is_the_header(monkeypatch):
    # An unsaved number's chat shows its banner at once: no 4 s settle needed. (Each GC flow
    # opens the client's 1:1 chat three or four times.)
    import viber_send
    reads = iter([[{"text": "Rafael +639175177830 is not in your contact list", "x": 0.31, "y": 0.16},
                   {"text": "Hello po", "x": 0.4, "y": 0.5}]])

    class FakeBackend:
        def observe(self, region=None):
            return next(reads)
    slept = []
    monkeypatch.setattr(viber_send, "backend", FakeBackend())
    monkeypatch.setattr(viber_send.time, "sleep", slept.append)
    text = viber_send._wait_for_sendable_chat("+639175177830", None)
    assert "hello po" in text and slept == []


def test_panel_check_is_skipped_where_the_send_button_is_found_by_identity(monkeypatch):
    import viber_send
    for shifts, expect_reads in ((True, 1), (False, 0)):
        reads = []

        class FakeBackend:
            panel_shifts_send = shifts
            def observe(self, region=None):
                reads.append(region)
                return []
            def click_panel_close(self): raise AssertionError("no panel to close")
        monkeypatch.setattr(viber_send, "backend", FakeBackend())
        viber_send._ensure_panel_closed()
        assert len(reads) == expect_reads, shifts


def test_search_reads_only_the_results_column_and_header_band(monkeypatch):
    import viber_send
    regions = []
    results = [{"text": "Conversations", "x": 0.05, "y": 0.13}, {"text": "Max Alpha", "x": 0.1, "y": 0.179}]

    class FakeBackend:
        def observe(self, region=None):
            regions.append(region)
            return results
    monkeypatch.setattr(viber_send, "backend", FakeBackend())
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    viber_send._await_results("Max")
    viber_send._header_matches("Max Alpha")
    assert regions[0] == viber_send._RESULTS_REGION and regions[-1] == viber_send._HEADER_REGION
    assert all(r is not None for r in regions)


def test_message_menu_is_read_as_soon_as_it_renders(monkeypatch):
    # message_action used to sleep 0.8 s twice; now it polls elements for the menu and its close.
    import backends
    import viber_ui
    menu = {"type": "MenuItem", "class": "MenuItem", "name": "Reply", "text": "", "parent": None,
            "rect": [400, 400, 500, 430]}
    chat = [{"type": "Pane", "class": "StackView", "name": "", "text": "", "parent": None, "rect": [308, 0, 1100, 967]},
            {"type": "Custom", "class": "FeedDelegate", "name": "", "text": "", "parent": 0, "rect": [330, 500, 900, 560]},
            {"type": "Edit", "class": "TextEditItem", "name": "", "text": "hello", "parent": 1, "rect": [340, 510, 600, 550]},
            {"type": "Edit", "class": "QQuickTextEdit", "name": "", "text": "", "parent": 0, "rect": [320, 900, 1000, 940]}]
    reads = [chat, chat, chat + [menu], chat + [menu], chat]
    clicks, slept = [], []

    class FakeBackend:
        def elements(self): return reads.pop(0) if len(reads) > 1 else reads[0]
        def click_at(self, x, y, button="left", double=False): clicks.append((x, y, button))
        def press_key(self, key, repeat=1): raise AssertionError("menu item should be found")
    previous = backends._backend
    backends._backend = FakeBackend()
    monkeypatch.setattr(viber_ui.time, "sleep", slept.append)
    try:
        result = viber_ui.message_action(1, "Reply")
    finally:
        backends._backend = previous
    assert result["chose"] == "Reply" and clicks[0][2] == "right" and clicks[1][:2] == (450, 415)
    assert sum(slept) < 1.0  # was 1.6 s of fixed sleeps


# ---------------------------------------------------- speed-ups, part 2: polls replace sleeps
# Stateful stand-ins for Viber's chat pane, the Add participants picker and the info panel, so
# the flows that used to sleep fixed amounts are exercised end to end with `time.sleep` recorded.


def _ui(kind, cls, rect, parent=None, text="", name=""):
    return {"type": kind, "name": name, "class": cls, "text": text, "parent": parent, "rect": list(rect)}


class _Img:
    width, height = 1100, 967


def _obs(text, x, y, w=0.1, h=0.02):
    return {"text": text, "x": x, "y": y, "w": w, "h": h}


class ChatBackend:
    """A chat with messages, a message box, the context menu and the "down" jump button."""
    PANE = [308, 71, 792, 959]
    COMPOSE = [320, 900, 760, 940]

    def __init__(self, messages, scrolled_up=False, lag=2):
        self.messages = list(messages)   # [(text, mine)]
        self.compose, self.staged = "", ""
        self.menu_open, self.target, self.editing = False, None, None
        self.scrolled_up, self.lag, self.reads_left = scrolled_up, lag, None
        self.clear_in = None
        self.clicks, self.scrolls, self.keys = [], [], []

    def _rects(self):
        for i, (text, mine) in enumerate(self.messages):
            top = 160 + i * 60  # below the pane's 72-px header bar (the feed view starts there)
            yield i, text, mine, [548 if mine else 320, top, 760 if mine else 600, top + 40]

    def elements(self):
        if self.reads_left is not None:      # the jump button goes a few reads after its click
            self.reads_left -= 1
            if self.reads_left < 0:
                self.scrolled_up, self.reads_left = False, None
        if self.clear_in is not None:        # the box clears a few reads after send
            self.clear_in -= 1
            if self.clear_in < 0:
                self._apply_send()
        els = [_ui("Pane", "StackView", self.PANE)]
        for _, text, mine, rect in self._rects():
            d = len(els)
            els.append(_ui("Group", "FeedDelegate", [308, rect[1] - 5, 792, rect[3] + 5], parent=0))
            els.append(_ui("Text", "TextEditItem", rect, parent=d, text=text))
        els.append(_ui("Edit", "QQuickTextEdit", self.COMPOSE, parent=0, text=self.compose))
        if self.menu_open:
            for j, name in enumerate(("Reply", "Edit", "Forward")):
                els.append(_ui("MenuItem", "MenuItem", [400, 500 + j * 30, 520, 525 + j * 30], name=name))
        if self.scrolled_up:
            els.append(_ui("Button", "ScrollToBottomItem", [700, 800, 740, 840]))
        return els

    def _apply_send(self):
        if self.editing is not None:
            self.messages[self.editing] = (self.compose, True)
        else:
            self.messages.append((self.compose, True))
        self.compose, self.editing, self.clear_in = "", None, None

    def click_at(self, x, y, button="left", double=False):
        self.clicks.append((x, y, button))
        if button == "right":
            self.target = next(i for i, _, _, r in self._rects() if r[1] <= y <= r[3])
            self.menu_open = True
            return
        if self.menu_open:
            j = int((y - 500) // 30)
            self.menu_open = False
            if j == 1:
                self.editing = self.target
                self.compose = self.messages[self.target][0]
            return
        if self.scrolled_up and 700 <= x <= 740 and 800 <= y <= 840:
            self.reads_left = self.lag

    def clipboard_set(self, text): self.staged = text
    def click_compose(self): pass
    def delete(self): self.compose = ""
    def paste(self): self.compose = self.staged
    def click_send(self): self.clear_in = self.lag
    def press_key(self, key, repeat=1): self.keys.append((key, repeat))
    def scroll(self, area, notches): self.scrolls.append((area, notches))


def _use(monkeypatch, fake, fast_clock=False):
    """Install `fake` as the backend and record sleeps. With fast_clock, sleeping advances a
    virtual clock that the polls' deadlines read, so a flow that must time out does so at once."""
    import backends
    import viber_ui
    monkeypatch.setattr(backends, "_backend", fake)
    slept = []
    if not fast_clock:
        monkeypatch.setattr(viber_ui.time, "sleep", slept.append)
        return slept
    clock = [0.0]

    def sleep(s):
        slept.append(s)
        clock[0] += s
    monkeypatch.setattr(viber_ui.time, "sleep", sleep)
    monkeypatch.setattr(viber_ui.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(viber_ui.time, "time", lambda: clock[0])
    return slept


def test_jump_to_latest_polls_until_the_down_button_goes(monkeypatch):
    import viber_ui
    fake = ChatBackend([("hi", False)], scrolled_up=True)
    slept = _use(monkeypatch, fake)
    out = viber_ui.jump_to_latest()
    assert out == {"at_latest": True, "used_button": True}
    assert fake.clicks[0][:2] == (720, 820) and fake.scrolls == [("chat", -60)]
    assert sum(slept) < 1.5  # was 1.0 s per click + 0.6 s fixed (+1.8 s of wheel-notch sleeps)


def test_jump_to_latest_reports_not_at_latest_when_the_button_stays(monkeypatch):
    import viber_ui
    fake = ChatBackend([("hi", False)], scrolled_up=True, lag=10 ** 6)
    _use(monkeypatch, fake, fast_clock=True)
    out = viber_ui.jump_to_latest()
    assert out["at_latest"] is False and out["used_button"] is True
    assert len([c for c in fake.clicks if c[2] == "left"]) == 4  # tried the button 4 times


def test_quote_reply_returns_once_the_box_clears(monkeypatch):
    import viber_ui
    fake = ChatBackend([("question?", False), ("their newest", False)])
    slept = _use(monkeypatch, fake)
    out = viber_ui.reply_to_message(1, "answer", send=True)
    assert out["sent"] and out["replying_to"] == "their newest"
    assert fake.messages[-1] == ("answer", True) and fake.compose == ""
    assert fake.clicks[0][2] == "right" and 500 <= fake.clicks[1][1] < 530  # right-click, then Reply
    assert sum(slept) < 1.2  # was 0.8 + 0.8 (menu) + 1.2 (after send) fixed


def test_edit_message_waits_for_the_edited_text_and_verifies_it(monkeypatch):
    import viber_ui
    fake = ChatBackend([("theirs", False), ("old text", True)])
    _use(monkeypatch, fake)
    out = viber_ui.edit_message(1, "new text")
    assert out == {"edited": True, "from": "old text", "to": "new text", "verified": True}
    assert fake.messages == [("theirs", False), ("new text", True)]


def test_edit_message_refuses_someone_elses_message(monkeypatch):
    import pytest
    import viber_ui
    fake = ChatBackend([("old text", True), ("theirs", False)])
    _use(monkeypatch, fake)
    with pytest.raises(viber_ui.UIError, match="yours"):
        viber_ui.edit_message(1, "new")
    assert fake.clicks == []


def test_message_action_escapes_when_the_item_is_not_offered(monkeypatch):
    import pytest
    import viber_ui
    fake = ChatBackend([("hi", False)])
    _use(monkeypatch, fake)
    with pytest.raises(viber_ui.UIError, match="Available: Reply, Edit, Forward"):
        viber_ui.message_action(1, "Translate")
    assert fake.keys == [("escape", 1)]


class OcrPxBackend:
    def __init__(self, pane=True):
        self.pane, self.regions, self.elements_calls = pane, [], 0

    def screenshot(self):
        return _Img()

    def elements(self):
        self.elements_calls += 1
        return [_ui("Pane", "SplitView", [308, 0, 1100, 967])] if self.pane else []

    def observe(self, region=None):
        self.regions.append(region)
        return [_obs("Row", 0.5, 0.5, 0.1, 0.02)]


def test_ocr_px_pixel_rects_region_hint_and_cached_sidebar_edge(monkeypatch):
    import viber_ui
    fake = OcrPxBackend()
    _use(monkeypatch, fake)
    out = viber_ui._ocr_px()
    assert out == [{"text": "Row", "x": 550.0, "y": 483.5, "w": 110.0, "h": 19.34, "W": 1100, "H": 967, "SB": 308}]
    viber_ui._ocr_px((300, 100, None, 500))
    viber_ui._ocr_px((-5, 0, 1200, None))
    assert fake.regions[0] is None
    assert fake.regions[1] == (300 / 1100, 100 / 967, 1.0, 500 / 967)   # None = the window's edge
    assert fake.regions[2] == (0.0, 0.0, 1200 / 1100, 1.0)              # the backend clamps
    assert fake.elements_calls == 1  # the sidebar edge is read once per window size, not per read


def test_ocr_px_does_not_cache_the_sidebar_edge_while_the_pane_is_missing(monkeypatch):
    # While the Sponsored ad shows, Qt drops the SplitView: fall back to 28% but keep looking.
    import viber_ui
    fake = OcrPxBackend(pane=False)
    _use(monkeypatch, fake)
    assert viber_ui._ocr_px()[0]["SB"] == 1100 * 0.28
    fake.pane = True
    assert viber_ui._ocr_px()[0]["SB"] == 308 and fake.elements_calls == 2


def test_panel_region_covers_exactly_what_panel_rows_keep():
    from viber_ui import _panel_region, _panel_rows
    search = _ui("Edit", "TextFieldItem", [780, 60, 1080, 90])
    assert _panel_region(search) == (740, 90, None, None)
    rows = [{"text": "in", "x": 745, "y": 100, "w": 10, "h": 10}, {"text": "left", "x": 100, "y": 100, "w": 10, "h": 10},
            {"text": "above", "x": 800, "y": 80, "w": 10, "h": 10}]
    assert [o["text"] for o in _panel_rows(rows, search)] == ["in"]


class PickerBackend:
    """The Add participants side panel: a search box, the rows it filters, the N/250 counter."""
    SEARCH, SAVE, PANEL = [780, 60, 1080, 90], [1000, 20, 1080, 50], [760, 0, 1100, 967]
    CONTACTS = ["Arjee Alpha", "Alpha Premier Group"]

    def __init__(self, ticks=True):
        self.search_text, self.counter, self.focus, self.ticks = "", 0, None, ticks
        self.clicks, self.regions, self.typed = [], [], []

    def screenshot(self): return _Img()

    def elements(self):
        return [_ui("Pane", "SplitView", [308, 0, 1100, 967]),
                _ui("Edit", "QQuickTextEdit", [320, 900, 740, 940]),
                _ui("Pane", "SideBarContent", self.PANEL),
                _ui("Edit", "TextFieldItem", self.SEARCH, text=self.search_text),
                _ui("Button", "Button", self.SAVE, name="Save")]

    def rows(self):
        if not self.search_text:
            return []
        return [c for c in self.CONTACTS if c.lower().startswith(self.search_text.lower())]

    def observe(self, region=None):
        self.regions.append(region)
        out = [_obs(f"{self.counter}/250", 1000 / 1100, 120 / 967, 0.05, 0.02)]
        for i, name in enumerate(self.rows()):
            out.append(_obs(name, 790 / 1100, (150 + 60 * i) / 967, 0.15, 20 / 967))
        return out

    def click_at(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        if self.SEARCH[0] <= x <= self.SEARCH[2] and self.SEARCH[1] <= y <= self.SEARCH[3]:
            self.focus = "search"
        for i, _ in enumerate(self.rows()):
            if self.ticks and 150 + 60 * i - 10 <= y <= 150 + 60 * i + 35 and x > self.PANEL[0]:
                self.counter += 1

    def type_into_focus(self, text):
        self.typed.append(text)
        if self.focus == "search":
            self.search_text += text

    def press_key(self, key, repeat=1):
        if key == "backspace" and self.focus == "search":
            self.search_text = self.search_text[:-repeat]

    def click_compose(self): self.focus = "compose"


def test_tick_participant_reads_only_the_panel_and_waits_only_for_state(monkeypatch):
    import viber_ui
    fake = PickerBackend()
    slept = _use(monkeypatch, fake)
    viber_ui._tick_participant("Arjee Alpha")
    assert fake.counter == 1 and fake.search_text == "Arjee Alpha"
    assert fake.clicks[-1] == (1075, 150 + 10 + 6)                 # the row's select circle
    assert all(r == (740 / 1100, 90 / 967, 1.0, 1.0) for r in fake.regions)  # panel-only OCR
    assert sum(slept) < 1.0  # was 0.3 + 1.2 (search) + 1.0/0.5 polls


def test_tick_participant_fails_loudly_when_the_counter_does_not_move(monkeypatch):
    import pytest
    import viber_ui
    fake = PickerBackend(ticks=False)
    _use(monkeypatch, fake, fast_clock=True)
    with pytest.raises(viber_ui.UIError, match="register"):
        viber_ui._tick_participant("Arjee Alpha")


def test_picker_search_retypes_a_dropped_first_letter(monkeypatch):
    # 2026-10-27: staff saw "ark Anthony Santos" typed into the Add participants search (the "M"
    # was lost), then the retry fixed it. The first letter is now typed alone and checked.
    import viber_ui

    class DropsFirst(PickerBackend):
        CONTACTS = ["Mark Anthony Santos"]
        dropped = False

        def type_into_focus(self, text):
            if not self.dropped and self.focus == "search" and not self.search_text:
                self.dropped = True       # the first keystroke after focusing the box is lost
                self.typed.append(text)
                return
            super().type_into_focus(text)

    fake = DropsFirst()
    _use(monkeypatch, fake, fast_clock=True)
    viber_ui._tick_participant("Mark Anthony Santos")
    assert fake.typed == ["M", "M", "ark Anthony Santos"]   # one pass, no full retype
    assert fake.search_text == "Mark Anthony Santos" and fake.counter == 1


class PanelBackend:
    """A 1:1 chat whose info panel has a main page (pencil, Groups in common) and a sub-page."""
    INFO, PENCIL, STAR = [1050, 20, 1080, 50], [800, 150, 820, 170], [830, 150, 850, 170]
    ROW, BACK, PANEL = [780, 400, 1080, 430], [770, 10, 790, 30], [760, 0, 1100, 967]

    def __init__(self, groups=("APG CLIENTS", "IT APG")):
        self.page, self.groups = "chat", list(groups)
        self.stills, self.clicks, self.regions = 0, [], []

    def screenshot(self): return _Img()

    def wait_still(self, timeout=2.0):
        self.stills += 1
        return 0.1

    def elements(self):
        els = [_ui("Pane", "SplitView", [308, 0, 1100, 967]), _ui("Button", "InfoButton", self.INFO)]
        if self.page == "main":
            els += [_ui("Pane", "SideBarContent", self.PANEL), _ui("Button", "IconButton", self.PENCIL),
                    _ui("Button", "IconButton", self.STAR), _ui("Button", "Button", self.ROW, name="Groups in common")]
        elif self.page == "groups":
            els += [_ui("Pane", "SideBarContent", self.PANEL), _ui("Button", "IconButton", self.BACK)]
        return els

    def observe(self, region=None):
        self.regions.append(region)
        if self.page != "groups":
            return []
        lines = [_obs("Groups in common", 0.72, 0.08)]
        lines += [_obs(g, 0.72, 0.2 + 0.05 * i) for i, g in enumerate(self.groups)]
        if not self.groups:
            lines.append(_obs("No groups in common", 0.72, 0.2))
        return lines

    def click_at(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        hit = lambda r: r[0] <= x <= r[2] and r[1] <= y <= r[3]  # noqa: E731
        if self.page == "chat" and hit(self.INFO):
            self.page = "main"
        elif self.page == "main" and hit(self.ROW):
            self.page = "groups"
        elif self.page == "groups" and hit(self.BACK):
            self.page = "main"


def test_groups_in_common_settles_after_each_slide_and_reads_the_panel_only(monkeypatch):
    import viber_ui
    fake = PanelBackend()
    slept = _use(monkeypatch, fake)
    assert viber_ui._groups_in_common() == ["APG CLIENTS", "IT APG"]
    assert fake.page == "main"                       # back on the main page, nothing left open
    assert fake.stills == 3                          # info slide, sub-page slide, back slide
    assert fake.regions == [(760 / 1100, 0.0, 1.0, 1.0)]
    assert sum(slept) < 0.5                          # was 0.8 + 1.5 + 0.6 fixed


def test_groups_in_common_is_empty_for_a_new_client(monkeypatch):
    import viber_ui
    fake = PanelBackend(groups=())
    _use(monkeypatch, fake)
    assert viber_ui._groups_in_common() == []


class SearchMessagesBackend:
    """The search box, its result-type tabs (which appear a moment after typing) and the rows."""
    def __init__(self, tabs_after=2):
        self.reads, self.tabs_after = 0, tabs_after
        self.clicks, self.regions, self.scrolls, self.typed = [], [], [], []

    def click_search(self): pass
    def select_all(self): pass
    def delete(self): pass
    def type_text(self, text): self.typed.append(text)
    def scroll(self, area, notches): self.scrolls.append((area, notches))
    def click_at(self, x, y, button="left", double=False): self.clicks.append((x, y))

    def elements(self):
        self.reads += 1
        if self.reads < self.tabs_after:
            return []
        return [_ui("Button", "TabComponent", [10 + 40 * i, 60, 40 + 40 * i, 90]) for i in range(3)]

    def observe(self, region=None):
        self.regions.append(region)
        return [_obs("APG CLIENTS", 0.05, 0.20), _obs("4:57 PM", 0.22, 0.20),
                _obs("Me: Kindly fill this up", 0.05, 0.23)]


def test_search_messages_polls_for_the_tabs_and_reads_the_results_column(monkeypatch):
    import viber_send
    fake = SearchMessagesBackend()
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(viber_send, "viber_is_running", lambda: True)
    slept = []
    monkeypatch.setattr(viber_send.time, "sleep", slept.append)
    out = viber_send.search_messages("Kindly", pages=1)
    assert out["count"] == 1 and out["results"][0]["chat"] == "APG CLIENTS"
    assert fake.typed == ["Kindly"] and fake.clicks == [(65, 75), (25, 75)]  # messages tab, then back
    assert fake.scrolls == [("chats", 50)]
    assert all(r == viber_send._RESULTS_REGION for r in fake.regions)
    assert sum(slept) < 2.0  # was 1.2 + 1.5 + 0.8 fixed


def test_search_messages_fails_loudly_without_the_tabs(monkeypatch):
    import pytest
    import viber_send
    fake = SearchMessagesBackend(tabs_after=10 ** 6)
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(viber_send, "viber_is_running", lambda: True)
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    monkeypatch.setattr(viber_send, "_poll", lambda check, timeout, first=0.0, interval=0.1: check())
    with pytest.raises(viber_send.SendError, match="tabs"):
        viber_send.search_messages("x", pages=1)


def test_search_fallback_polls_the_header_after_opening_the_first_result(monkeypatch):
    # The bold result name can't be OCR'd: the first row under the header is opened and the
    # chat header confirms it (polled now, not a fixed 1.2 s).
    import viber_send
    header = {"text": "Conversations", "x": 0.05, "y": 0.13}

    class FakeBackend:
        clicked = []
        input_confirmed = True
        def click_search(self): pass
        def select_all(self): pass
        def delete(self): pass
        def type_text(self, text): pass
        def click_result(self, norm_y): self.clicked.append(norm_y)
        def observe(self, region=None):
            if region == viber_send._HEADER_REGION:
                return [{"text": "My Notes", "x": 0.35, "y": 0.104}]
            return [header]
    monkeypatch.setattr(viber_send, "backend", FakeBackend())
    monkeypatch.setattr(viber_send, "_await_results", lambda name: ([header], None))
    slept = []
    monkeypatch.setattr(viber_send.time, "sleep", slept.append)
    assert viber_send._search_open("My Notes") == ("My Notes", 2)
    assert FakeBackend.clicked == [0.13 + 0.05] and sum(slept) < 1.0


class DialogBackend:
    def __init__(self, dialog):
        self.dialog, self.reads, self.clicks, self.keys = dialog, 0, [], []

    def screenshot(self): return _Img()

    def elements(self):
        els = [_ui("Pane", "SplitView", [308, 0, 1100, 967])]
        if self.dialog:
            els.append(_ui("Button", "Button", [500, 500, 560, 530], name="OK"))
        return els

    def observe(self, region=None):
        self.reads += 1
        if not self.dialog:
            return [_obs("Group", 0.35, 0.10), _obs("3 participants", 0.35, 0.125)]
        return [_obs("Couldn't be added", 0.4, 0.4),
                _obs("Rafael was not added to the group due to their privacy settings", 0.4, 0.45)]

    def click_at(self, x, y, button="left", double=False): self.clicks.append((x, y))
    def press_key(self, key, repeat=1): self.keys.append(key)


def test_privacy_dialog_is_read_and_dismissed_with_ok(monkeypatch):
    import viber_ui
    fake = DialogBackend(dialog=True)
    _use(monkeypatch, fake)
    assert viber_ui._dismiss_privacy_dialog(viber_ui._ocr_px()) == "Rafael"
    assert fake.clicks == [(530, 515)] and fake.keys == []


class GroupFlowBackend:
    """Everything create_group_from_chat touches, from the client's 1:1 chat to the renamed group:
    banner, info panel (main + Groups in common), Add participants picker, Save, the new group's
    header, the rename pencil/field. hold_foreground blocks until the test releases it, which
    proves the flow no longer waits on it."""
    INFO, ADD = [1050, 20, 1080, 50], [900, 20, 930, 50]
    PANEL, PENCIL, STAR = [760, 0, 1100, 967], [800, 150, 820, 170], [830, 150, 850, 170]
    ROW, BACK = [780, 400, 1080, 430], [770, 10, 790, 30]
    SEARCH, SAVE, FIELD = [780, 60, 1080, 90], [1000, 20, 1080, 50], [770, 150, 795, 170]
    NUMBER = "+639175177830"

    def __init__(self):
        import threading
        self.page, self.group_name, self.renaming, self.rename_text = "chat", "Group", False, "Group"
        self.focus, self.release, self.clicks, self.stills = None, threading.Event(), [], 0
        self.foreground_calls = []

    def screenshot(self): return _Img()
    def foreground(self): return 1

    def wait_still(self, timeout=2.0):
        self.stills += 1
        return 0.1

    def hold_foreground(self, prev, seconds):
        self.foreground_calls.append((prev, seconds))
        self.release.wait(5)

    def elements(self):
        els = [_ui("Pane", "SplitView", [308, 0, 1100, 967]), _ui("Pane", "StackView", [308, 71, 1100, 959]),
               _ui("Button", "InfoButton", self.INFO), _ui("Edit", "QQuickTextEdit", [320, 900, 740, 940])]
        if self.page in ("chat", "main", "groups", "picker"):
            els.append(_ui("Button", "AddParticipantsButton", self.ADD))
        if self.page in ("main", "group_main"):
            els += [_ui("Pane", "SideBarContent", self.PANEL), _ui("Button", "IconButton", self.PENCIL),
                    _ui("Button", "IconButton", self.STAR), _ui("Button", "Button", self.ROW, name="Groups in common")]
            if self.renaming:
                els.append(_ui("Edit", "TextField", self.FIELD, text=self.rename_text))
        elif self.page == "groups":
            els += [_ui("Pane", "SideBarContent", self.PANEL), _ui("Button", "IconButton", self.BACK)]
        elif self.page == "picker":
            els += [_ui("Pane", "SideBarContent", self.PANEL), _ui("Edit", "TextFieldItem", self.SEARCH),
                    _ui("Button", "Button", self.SAVE, name="Save")]
        return els

    def observe(self, region=None):
        if self.page in ("chat", "main", "groups", "picker"):
            lines = [_obs("Rafael", 0.35, 0.10),
                     _obs(f"Rafael {self.NUMBER} is not in your contact list - save it", 0.31, 145 / 967, 0.5)]
            if self.page == "groups":
                lines += [_obs("Groups in common", 0.72, 0.08), _obs("No groups in common", 0.72, 0.2)]
            return lines
        return [_obs(self.group_name, 0.35, 0.10, 0.2), _obs("3 participants", 0.35, 0.125)]

    def click_at(self, x, y, button="left", double=False):
        self.clicks.append((x, y))
        hit = lambda r: r[0] <= x <= r[2] and r[1] <= y <= r[3]  # noqa: E731
        if self.page == "chat" and hit(self.INFO):
            self.page = "main"
        elif self.page == "main" and hit(self.ROW):
            self.page = "groups"
        elif self.page == "groups" and hit(self.BACK):
            self.page = "main"
        elif self.page in ("chat", "main") and hit(self.ADD):
            self.page = "picker"
        elif self.page == "picker" and hit(self.SAVE):
            self.page = "group"
        elif self.page == "group" and hit(self.INFO):
            self.page = "group_main"
        elif self.page == "group_main" and hit(self.PENCIL):
            if self.renaming:                       # the pencil is the tick now
                self.group_name, self.renaming = self.rename_text, False
            else:
                self.renaming = True
        elif self.page == "group_main" and hit(self.FIELD):
            self.focus = "field"

    def press_key(self, key, repeat=1):
        if key == "backspace" and self.focus == "field":
            self.rename_text = self.rename_text[:-repeat]

    def type_into_focus(self, text):
        if self.focus == "field":
            self.rename_text += text


def test_create_group_from_chat_runs_on_polls_and_does_not_wait_for_the_foreground_guard(monkeypatch):
    import viber_ui
    fake = GroupFlowBackend()
    slept = _use(monkeypatch, fake, fast_clock=True)
    try:
        out = viber_ui.create_group_from_chat("RAFAEL-WH-(300SQM, PASIG)", [], fake.NUMBER, self_name="Deign Alpha")
        guard_done = fake.release.is_set()
    finally:
        fake.release.set()
    assert out == {"created": True, "renamed": True, "gc_name": "RAFAEL-WH-(300SQM, PASIG)", "members_added": [],
                   "client_display_name": "Rafael", "privacy_blocked": None}
    assert fake.group_name == "RAFAEL-WH-(300SQM, PASIG)"
    assert fake.foreground_calls == [(1, 2.0)]        # the guard ran (on its thread) ...
    assert not guard_done                             # ... and the flow finished before it ended
    assert fake.stills >= 5                           # info, sub-page, back, picker, info, ...
    assert sum(slept) < 3.0  # was ~2 s hold + 0.5-s picker polls + 3.2 s dialog waits + 1.7 s rename


def test_create_group_from_chat_stops_for_an_existing_client(monkeypatch):
    import viber_ui
    fake = GroupFlowBackend()
    original = fake.observe

    def observe(region=None):
        return [line if line["text"] != "No groups in common" else _obs("KM-WH-(240SQM, PASIG)", 0.72, 0.2)
                for line in original(region)]
    fake.observe = observe
    _use(monkeypatch, fake)
    out = viber_ui.create_group_from_chat("X", [], fake.NUMBER)
    assert out["created"] is False and out["existing_group"] == "KM-WH-(240SQM, PASIG)"
    assert fake.page == "main" and fake.foreground_calls == []  # never reached the picker/Save


# ------------------------------------------- GC rename lost after Save (2026-10-22)
# Four GCs in a row were left named "Group": the pencil was clicked while the info panel was
# still sliding in, the click was lost, and the rename gave up after one try.


class LostClickGroupBackend(GroupFlowBackend):
    """The first `lose` clicks on the pencil (or ✓) land while the panel moves and do nothing."""
    def __init__(self, lose=1):
        super().__init__()
        self.lose = lose

    def click_at(self, x, y, button="left", double=False):
        p = self.PENCIL
        if self.page == "group_main" and self.lose and p[0] <= x <= p[2] and p[1] <= y <= p[3]:
            self.lose -= 1
            self.clicks.append((x, y))
            return
        super().click_at(x, y, button, double)


def test_rename_retries_a_pencil_click_lost_while_the_panel_slides(monkeypatch):
    import viber_ui
    fake = LostClickGroupBackend(lose=1)
    _use(monkeypatch, fake, fast_clock=True)
    try:
        out = viber_ui.create_group_from_chat("JHAZEL-CS-(30-35SQM, MAKATI)", [], fake.NUMBER)
    finally:
        fake.release.set()
    assert out["created"] and out["renamed"]
    assert fake.group_name == "JHAZEL-CS-(30-35SQM, MAKATI)"


def test_rename_retries_a_lost_tick_click(monkeypatch):
    import viber_ui
    fake = GroupFlowBackend()
    fake.page = "group"
    _use(monkeypatch, fake, fast_clock=True)
    original = fake.click_at
    lost = {"tick": 1}

    def click_at(x, y, button="left", double=False):
        p = fake.PENCIL
        if fake.renaming and lost["tick"] and p[0] <= x <= p[2] and p[1] <= y <= p[3]:
            lost["tick"] -= 1
            return
        original(x, y, button, double)
    fake.click_at = click_at
    viber_ui._rename_open_chat("JHAZEL-CS-(30-35SQM, MAKATI)")
    assert fake.group_name == "JHAZEL-CS-(30-35SQM, MAKATI)" and not fake.renaming


def test_rename_reports_when_the_name_box_never_opens(monkeypatch):
    import pytest
    import viber_ui
    fake = LostClickGroupBackend(lose=99)
    fake.page = "group"
    _use(monkeypatch, fake, fast_clock=True)
    with pytest.raises(viber_ui.UIError, match="name box didn't open"):
        viber_ui._rename_open_chat("X")
    assert fake.group_name == "Group"


def test_rename_refuses_when_another_group_took_over_the_chat(monkeypatch):
    import pytest
    import viber_ui
    fake = GroupFlowBackend()
    fake.page, fake.group_name, fake.rename_text = "group", "APR LISTING ONLY", "APR LISTING ONLY"
    _use(monkeypatch, fake, fast_clock=True)
    with pytest.raises(viber_ui.UIError, match="nothing was renamed"):
        viber_ui._rename_open_chat("JHAZEL-CS-(30-35SQM, MAKATI)", expect="Group")
    assert fake.group_name == "APR LISTING ONLY"


def test_mention_flow_sleeps_well_under_the_old_fixed_waits(monkeypatch):
    import backends
    import viber_ui
    fake = MentionBackend()
    monkeypatch.setattr(backends, "_backend", fake)
    slept = []
    monkeypatch.setattr(viber_ui.time, "sleep", slept.append)
    out = viber_ui.send_with_mention("Hello ", "Anthony")
    assert out["sent"] and fake.sent == "Hello @Anthony "
    assert sum(slept) < 2.0  # was 1.5 + 0.5 + 0.6 + 1.2 fixed


def test_mention_opens_number_polls_for_the_number_after_the_click(monkeypatch):
    import viber_ui

    class FakeBackend:
        def __init__(self): self.clicked, self.shown = [], False
        def screenshot(self): return _Img()
        def elements(self): return [_ui("Pane", "SplitView", [308, 0, 1100, 967])]

        def observe(self, region=None):
            lines = [_obs("Hello @shiso alpha", 0.4, 0.5, 0.2)]
            if self.shown:
                lines.append(_obs("+639175177830", 0.35, 0.12))
            return lines

        def click_at(self, x, y, button="left", double=False):
            self.clicked.append((x, y))
            self.shown = True
    fake = FakeBackend()
    slept = _use(monkeypatch, fake)
    assert viber_ui.mention_opens_number("shiso alpha", "+639175177830") is True
    (x, y), = fake.clicked
    assert 0.4 * 1100 < x < 0.6 * 1100 and abs(y - 0.51 * 967) < 2   # inside the "@shiso alpha" token
    assert sum(slept) < 1.0  # was a fixed 1.5 s
    assert viber_ui.mention_opens_number("shiso alpha", "+639175177830") is None  # already visible: inconclusive


# ------------------------------------------- the right chat: header-only confirmation (2026-10-21)
# open_chat used to accept a chat once the contact's first name appeared ANYWHERE in the window
# (the sidebar row counts) or the compose placeholder showed — both true of the chat it came from
# when viber:// didn't navigate, which is how messages went to the wrong person.


def test_chat_confirmed_only_by_the_header_or_the_banner():
    from viber_send import chat_confirmed
    hb = (71 + 64) / 967
    saved = [_obs("shiso alpha", 0.349, 0.094), _obs("+639620429272", 0.35, 0.119)]
    assert chat_confirmed(saved, "+639620429272", None, hb)                 # the number under the name
    assert chat_confirmed(saved, "+639620429272", "Narci", hb)              # even when the alias differs
    assert chat_confirmed(saved, "", "shiso alpha", hb)                     # by name
    assert chat_confirmed([_obs("TESTCLIENT-WH-(300S...", 0.35, 0.094)], "", "TESTCLIENT-WH-(300SQM, PASIG)", hb)
    assert chat_confirmed([_obs("Max Alpha", 0.35, 0.094)], "", "Max", hb)  # a search query is a prefix
    banner = [_obs("Rafael +639175177830 is not in your contact list - save it", 0.31, 0.16)]
    assert chat_confirmed(banner, "+639175177830", None, hb)
    # Never from the sidebar, the compose placeholder, or a message that holds the number/name.
    assert not chat_confirmed([_obs("shiso alpha", 0.05, 0.3)], "", "shiso alpha", hb)
    assert not chat_confirmed([_obs("Type a message...", 0.4, 0.96)], "+639620429272", None, hb)
    assert not chat_confirmed([_obs("Viber: 09620429272", 0.35, 0.3)], "+639620429272", None, hb)
    assert not chat_confirmed([_obs("Viber: 09620429272", 0.35, hb + 0.001)], "+639620429272", None, hb)
    assert not chat_confirmed([_obs("Maxine Cruz", 0.35, 0.094)], "", "Max Alpha", hb)
    assert not chat_confirmed([_obs("Rakuten Viber", 0.035, 0.011)], "", "Rak", hb)   # the title bar


def test_header_key_ignores_ocr_junk_but_tells_chats_apart():
    from viber_send import header_key
    a = header_key(["Viber Out", "shiso alpha", "Q +8 00", "+639620429272"])
    assert a == header_key(["Viber Out", "shiso alpha", "00 0", "+63962O429272"])   # junk and O/0 slips
    assert header_key(["Viber Out", "Max Alpha"]) != a
    assert header_key(["Viber Out", "Max Alpha"]) != header_key(["Viber Out", "Jean Alpha"])


class OpenChatBackend:
    """What the window shows on each read after a viber:// open: `pages` in order, the last one
    repeating (one page = the chat didn't navigate)."""
    def __init__(self, pages):
        self.pages, self.reads, self.urls = pages, 0, []

    def app_running(self): return True
    def elements(self): return [_ui("Pane", "StackView", [308, 71, 1100, 959])]
    def screenshot(self): return _Img()
    def open_url(self, url): self.urls.append(url)

    def observe(self, region=None):
        page = self.pages[min(self.reads, len(self.pages) - 1)]
        self.reads += 1
        return page


PREVIOUS_CHAT = [_obs("Max Alpha", 0.35, 0.094), _obs("Type a message...", 0.4, 0.96), _obs("shiso alpha", 0.05, 0.5)]


def _open_chat_with(monkeypatch, pages, number):
    import viber_send
    fake = OpenChatBackend(pages)
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(viber_send, "resolve_recipient", lambda to: (number, None))
    clock = [0.0]
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(viber_send.time, "time", lambda: clock[0])
    monkeypatch.setattr(viber_send.time, "monotonic", lambda: clock[0])
    return fake, clock


def test_open_chat_refuses_the_chat_it_came_from(monkeypatch):
    # viber:// didn't navigate: the previous chat stays, its box empty, the wanted contact's
    # name in the sidebar. Both used to pass as "sendable".
    import pytest
    import viber_send
    fake, clock = _open_chat_with(monkeypatch, [PREVIOUS_CHAT], "+639620429272")
    with pytest.raises(viber_send.SendError, match="didn't change"):
        viber_send.open_chat("+639620429272")
    assert fake.urls == ["viber://chat?number=+639620429272"] and clock[0] >= 12


def test_open_chat_accepts_a_saved_contact_from_the_number_under_the_name(monkeypatch):
    import viber_send
    opened = [_obs("shiso alpha", 0.349, 0.094), _obs("+639620429272", 0.35, 0.119)]
    fake, clock = _open_chat_with(monkeypatch, [PREVIOUS_CHAT, PREVIOUS_CHAT, opened], "+639620429272")
    assert viber_send.open_chat("+639620429272") == "+639620429272"
    assert clock[0] < 2.0  # no settle period


def test_open_chat_settles_only_once_the_header_changed(monkeypatch):
    # Neither the number nor a known name in the header (a saved contact whose chat hides the
    # number): accepted after the settle, and only because the header isn't the previous one.
    import viber_send
    other = [_obs("Anne", 0.35, 0.094), _obs("Type a message...", 0.4, 0.96)]
    fake, clock = _open_chat_with(monkeypatch, [PREVIOUS_CHAT, other], "+639170000000")
    assert viber_send.open_chat("+639170000000") == "+639170000000"
    assert 4.0 <= clock[0] < 6.0


def test_open_chat_still_reports_non_viber_numbers(monkeypatch):
    import pytest
    import viber_send
    _open_chat_with(monkeypatch, [PREVIOUS_CHAT, [_obs("This number isn't on Viber", 0.4, 0.5)]], "+10000000000")
    with pytest.raises(viber_send.SendError, match="not a Viber user"):
        viber_send.open_chat("+10000000000")


# ------------------------------------------- send_files: wait for the upload, not a fixed time
# The fixed 4 s + 1.5 s per file (up to 30 s) after an album was idle time: five cached photos
# show their delivered ticks 1.6 s after the preview closes. Viber animates a progress ring on
# the bubbles while uploading (nothing of it is in UIA), so the feed going still is the signal.


class UploadBackend:
    """Captures of the feed keep changing for `moving` reads (the progress ring), then hold
    still; with `banner`, the not-sent banner shows once it's still until Resend is pressed."""
    def __init__(self, moving=5, banner=False):
        self.moving, self.banner, self.reads, self.clicks = moving, banner, 0, []

    class _Shot:
        def __init__(self, data): self.data = data
        def crop(self, box): return self
        def tobytes(self): return self.data

    def screenshot(self):
        self.reads += 1
        return self._Shot(min(self.reads, self.moving).to_bytes(4, "big"))

    def elements(self):
        els = [_ui("Pane", "StackView", [308, 71, 1100, 959]), _ui("Edit", "QQuickTextEdit", [320, 900, 760, 940])]
        if self.banner and self.reads > self.moving and not self.clicks:
            els.append(_ui("Button", "BannerButton", [968, 155, 1044, 175], name="Resend all"))
        return els

    def click_at(self, x, y, button="left", double=False): self.clicks.append((x, y))


def test_send_settle_returns_once_the_feed_stops_moving(monkeypatch):
    import viber_ui
    fake = UploadBackend(moving=6)
    slept = _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._settle_and_resend(20) == {"attempts": 0, "still_unsent": False, "uploading": False}  # fixed wait was 30 s
    assert 1.5 <= sum(slept) <= 4.0  # ~6 moving reads, then 4 identical ones at 0.3 s


def test_send_settle_presses_resend_when_the_banner_follows_the_upload(monkeypatch):
    import viber_ui
    fake = UploadBackend(moving=3, banner=True)
    _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._settle_and_resend(3) == {"attempts": 1, "still_unsent": False, "uploading": False}
    assert fake.clicks == [(1006, 165)]


def test_send_settle_caps_at_the_old_fixed_wait_when_the_feed_never_settles(monkeypatch):
    import viber_ui
    fake = UploadBackend(moving=10 ** 6)
    slept = _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._settle_and_resend(2) == {"attempts": 0, "still_unsent": False, "uploading": False}
    assert abs(sum(slept) - 7.0) < 0.35  # 4 + 1.5 × 2


def test_send_settle_falls_back_to_the_fixed_wait_without_captures(monkeypatch):
    import viber_ui

    class NoShots:
        def elements(self): return []
    slept = _use(monkeypatch, NoShots(), fast_clock=True)
    assert viber_ui._settle_and_resend(2) == {"attempts": 0, "still_unsent": False, "uploading": False}
    assert slept == [7.0]


# ------------------------------------------------------- remaining speed-ups (2026-10-21, part 3)


def test_download_runs_in_parallel_and_skips_metadata_for_cached_files(monkeypatch, tmp_path):
    import threading
    import time as _time
    import googleapiclient.http
    import viber_drive
    monkeypatch.setattr(viber_drive, "CACHE_DIR", tmp_path)
    (tmp_path / "cached1_photo.jpg").write_bytes(b"old")
    (tmp_path / "half_photo.jpg.part").write_bytes(b"partial")   # an interrupted download
    calls, active, peak, lock = [], [0], [0], threading.Lock()

    class Exec:
        def __init__(self, value): self.value = value
        def execute(self, num_retries=0): return self.value

    class Files:
        def get(self, fileId, fields, supportsAllDrives):
            calls.append(fileId)
            return Exec({"id": fileId, "name": f"{fileId} photo.jpg", "size": "3", "mimeType": "image/jpeg"})

        def get_media(self, fileId, supportsAllDrives): return fileId

    class Service:
        def files(self): return Files()

    class FakeDownload:
        def __init__(self, buf, req): self.buf = buf

        def next_chunk(self, num_retries=0):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            _time.sleep(0.05)
            with lock:
                active[0] -= 1
            self.buf.write(b"img")
            return None, True
    monkeypatch.setattr(viber_drive, "_drive", lambda: Service())
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", FakeDownload)
    paths = viber_drive.download(["cached1", "half", "a", "b", "c"])
    assert paths[0] == str(tmp_path / "cached1_photo.jpg") and "cached1" not in calls  # reused, no round trip
    assert [Path(p).read_bytes() for p in paths[1:]] == [b"img"] * 4            # "half" was fetched again
    assert list(tmp_path.glob("*.part")) == [tmp_path / "half_photo.jpg.part"]  # only the stale one remains
    assert peak[0] >= 2                                                          # fetches overlapped
    assert viber_drive.download([]) == []


def test_await_results_hands_over_to_the_fallback_when_no_row_reads_as_the_name(monkeypatch):
    # Viber draws the matched name in bold that OCR can't read: once the results are up and two
    # reads agree there's no readable match, open-first-result takes over (was the full 2.4 s).
    import viber_send
    results = [{"text": "Conversations", "x": 0.05, "y": 0.13}, {"text": "APG CLIENTS", "x": 0.1, "y": 0.179}]
    reads = iter([[{"text": "Max Alpha", "x": 0.1, "y": 0.942}], results, results, results, results])

    class FakeBackend:
        def observe(self, region=None): return next(reads)
    monkeypatch.setattr(viber_send, "backend", FakeBackend())
    clock = [0.0]
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(viber_send.time, "monotonic", lambda: clock[0])
    obs, match = viber_send._await_results("TEST-NARCI")
    assert match is None and obs == results and clock[0] < 1.0


class GcSearchBackend:
    def __init__(self):
        self.results, self.stills, self.keys = [], 0, []

    def click_search(self): pass
    def delete(self): pass
    def type_text(self, text): pass
    def press_key(self, key, repeat=1): self.keys.append(key)
    def click_result(self, norm_y): self.results.append(norm_y)
    def wait_still(self, timeout=2.0): self.stills += 1; return 0.1
    def elements(self): return []


def test_gc_exists_reads_the_results_once_up_and_leaves_the_search(monkeypatch):
    import backends
    import viber_gc
    import viber_send
    import viber_gc_logic as logic
    fake = GcSearchBackend()
    monkeypatch.setattr(backends, "_backend", fake)
    rows = [{"text": "Groups", "x": 0.05, "y": 0.13}, {"text": "KM-WH-(240SQM, PASIG)", "x": 0.1, "y": 0.18}]
    monkeypatch.setattr(viber_send, "_await_results", lambda name: (rows, None))
    monkeypatch.setattr(logic, "search_says_gc_exists", lambda texts, name: "yes" if name in texts else "no")
    assert viber_gc.gc_exists("KM-WH-(240SQM, PASIG)") == (True, False)
    assert viber_gc.gc_exists("NOPE-WH-(1SQM, X)") == (False, False)
    assert fake.results == [] and fake.keys == ["escape", "escape"] and fake.stills == 2


def test_gc_exists_opens_the_first_result_when_the_name_is_unreadable(monkeypatch):
    import backends
    import viber_gc
    import viber_send
    import viber_gc_logic as logic
    fake = GcSearchBackend()
    monkeypatch.setattr(backends, "_backend", fake)
    rows = [{"text": "Groups", "x": 0.05, "y": 0.13}, {"text": "bold-unreadable", "x": 0.1, "y": 0.18}]
    monkeypatch.setattr(viber_send, "_await_results", lambda name: (rows, None))
    monkeypatch.setattr(logic, "search_says_gc_exists", lambda texts, name: "unknown")
    monkeypatch.setattr(viber_send, "_header_matches", lambda name: True)
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    assert viber_gc.gc_exists("KM-WH-(240SQM, PASIG)") == (True, True)
    assert fake.results == [0.13 + 0.05]  # the first row under the header


class HistoryBackend:
    """A chat whose older messages render a moment after each scroll up."""
    def __init__(self, pages):
        self.pages, self.page, self.pending, self.scrolls = pages, 0, 0, []

    def elements(self):
        if self.pending:
            self.pending -= 1
            if self.pending == 0:
                self.page = min(self.page + 1, len(self.pages) - 1)
        els = [_ui("Pane", "StackView", [308, 71, 1100, 959])]
        for i, text in enumerate(self.pages[self.page]):
            d = len(els)
            els.append(_ui("Group", "FeedDelegate", [308, 160 + i * 60 - 5, 792, 200 + i * 60 + 5], parent=0))
            els.append(_ui("Text", "TextEditItem", [320, 160 + i * 60, 600, 200 + i * 60], parent=d, text=text))
        els.append(_ui("Edit", "QQuickTextEdit", [320, 900, 760, 940], parent=0))
        return els

    def scroll(self, area, notches):
        self.scrolls.append(notches)
        if notches > 0:
            self.pending = 2  # older messages show after two reads


def test_find_message_scrolls_up_and_waits_only_until_older_messages_render(monkeypatch):
    import viber_gc
    fake = HistoryBackend([["newest"], ["older", "newest"], ["template text", "older"]])
    slept = _use(monkeypatch, fake, fast_clock=True)
    hit = viber_gc._find_message("template text")
    assert hit and hit["text"] == "template text"
    assert [n for n in fake.scrolls if n > 0] == [10, 10] and sum(slept) < 2.5  # was 1 s per screen


def test_collect_templates_chat_returns_oldest_first(monkeypatch):
    import viber_gc
    fake = HistoryBackend([["c", "d"], ["a", "b", "c"]])
    _use(monkeypatch, fake, fast_clock=True)
    assert viber_gc._collect_templates_chat(2) == ["a", "b", "c", "d"]


def test_merge_older_joins_at_the_overlap_and_keeps_repeated_texts():
    import viber_ui
    r = lambda *ts: [{"from": "them", "kind": "text", "text": t} for t in ts]
    assert viber_ui._merge_older(r("x", "x", "y"), r("w", "x", "x")) == (r("w", "x", "x", "y"), True)
    assert viber_ui._merge_older(r("c", "d"), r("c", "d")) == (r("c", "d"), True)  # didn't move
    assert viber_ui._merge_older(r("c", "d"), r("a", "b")) == (r("a", "b", "c", "d"), False)


def test_backread_reads_the_whole_chat_oldest_first(monkeypatch):
    # Staff: "you need a tool to properly backread gcs by scrolling up" — 50-notch scroll_chat
    # reads skipped messages and OCR mixed in the sidebar; checking "already offered?" missed.
    import viber_ui
    fake = HistoryBackend([["ok", "ok", "listing B"], ["listing A", "ok", "ok"],
                           ["template", "listing A", "ok"]])
    _use(monkeypatch, fake, fast_clock=True)
    out = viber_ui.backread()
    assert [m["text"] for m in out["messages"]] == ["template", "listing A", "ok", "ok", "listing B"]
    assert out["reached_top"] and out["gaps"] == 0 and out["messages"][0]["i"] == 1


def test_backread_stops_at_the_until_text(monkeypatch):
    import viber_ui
    fake = HistoryBackend([["c", "d"], ["b", "c"], ["a", "b"]])
    _use(monkeypatch, fake, fast_clock=True)
    out = viber_ui.backread(until="B")
    assert [m["text"] for m in out["messages"]] == ["b", "c", "d"] and not out["reached_top"]


def test_client_name_is_polled_from_the_banner(monkeypatch):
    import viber_gc
    import viber_send

    class FakeBackend:
        def __init__(self): self.reads = 0
        def screenshot(self): return _Img()
        def elements(self): return [_ui("Pane", "SplitView", [308, 0, 1100, 967])]

        def observe(self, region=None):
            self.reads += 1
            if self.reads < 3:
                return []
            return [_obs("Rafael Luijie +639175177830 is not in your contact list - save it", 0.31, 0.16, 0.5)]
    fake = FakeBackend()
    slept = _use(monkeypatch, fake, fast_clock=True)
    monkeypatch.setattr(viber_send, "open_chat", lambda number: number)
    assert viber_gc.client_name_from_chat("+639175177830") == "Rafael Luijie"
    assert fake.reads == 3 and sum(slept) < 1.0


class PopupDialogBackend(DialogBackend):
    """The privacy dialog as UIA shows it: a QQuickPopupItem window with an OK button; its
    sentence is only readable by OCR inside the popup's rect."""
    def elements(self):
        els = [_ui("Pane", "SplitView", [308, 0, 1100, 967])]
        if self.dialog:
            els += [_ui("Window", "QQuickPopupItem", [299, 395, 804, 595], name="Couldn't be added"),
                    _ui("Button", "NotificationButton", [500, 520, 560, 555], name="OK")]
        return els

    def observe(self, region=None):
        self.reads += 1
        if self.dialog and region is not None:
            return [_obs("Rafael was not added to the group due to their privacy settings", 0.4, 0.45)]
        return [_obs("Group", 0.35, 0.10), _obs("3 participants", 0.35, 0.125)]


def test_privacy_dialog_is_found_as_a_popup_window_and_read_inside_it(monkeypatch):
    import viber_ui
    fake = PopupDialogBackend(dialog=True)
    _use(monkeypatch, fake)
    assert viber_ui._dismiss_privacy_dialog(viber_ui._ocr_px()) == "Rafael"
    assert fake.clicks == [(530, 537.5)] and fake.reads == 2  # the initial read + the popup's rect


def test_privacy_dialog_check_without_a_dialog_is_a_few_cheap_walks(monkeypatch):
    import viber_ui
    fake = PopupDialogBackend(dialog=False)
    slept = _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._dismiss_privacy_dialog(viber_ui._ocr_px()) is None
    assert fake.reads == 1 and sum(slept) <= 1.8   # no OCR beyond the caller's read; was four reads over ~3 s


def test_other_dialogs_are_left_alone_by_the_privacy_check(monkeypatch):
    import viber_ui

    class PinDialogBackend(PopupDialogBackend):
        def elements(self):
            return [_ui("Pane", "SplitView", [308, 0, 1100, 967]),
                    _ui("Window", "QQuickPopupItem", [299, 395, 804, 595], name="Pin note?"),
                    _ui("Button", "NotificationButton", [470, 520, 537, 555], name="Pin")]

        def observe(self, region=None):
            self.reads += 1
            return [_obs("Pin this note?", 0.4, 0.45)] if region else []
    fake = PinDialogBackend(dialog=True)
    _use(monkeypatch, fake)
    assert viber_ui._dismiss_privacy_dialog(viber_ui._ocr_px()) is None and fake.clicks == []


def test_await_results_finishes_at_once_on_no_results(monkeypatch):
    # A name nobody has: Viber shows "No results" and no section header; the wait used to run
    # out its 2.4 s timeout before the caller could say so.
    import viber_send
    none = [{"text": "No results", "x": 0.1, "y": 0.2}]
    reads = iter([[{"text": "Max Alpha", "x": 0.1, "y": 0.942}], none, none, none])

    class FakeBackend:
        def observe(self, region=None): return next(reads)
    monkeypatch.setattr(viber_send, "backend", FakeBackend())
    clock = [0.0]
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(viber_send.time, "monotonic", lambda: clock[0])
    obs, match = viber_send._await_results("ZZZ-NOPE")
    assert match is None and obs == none and clock[0] < 1.0


# 2026-10-26: a 7-photo album's last photos still showed their (static) upload clocks when the
# feed went still, send_files returned, and the listing's dashed separator landed in the middle
# of its photos. Delivered photo bubbles carry an AbstractButton; uploading ones don't.


class AlbumBackend(UploadBackend):
    """A still feed (its captures never change) ending in my photos; the newest `pending`
    photos get their delivered marker after `deliver_after` reads."""
    def __init__(self, photos=4, pending=2, deliver_after=8):
        super().__init__(moving=1)
        self.photos, self.pending, self.deliver_after = photos, pending, deliver_after
        self.el_reads = 0

    def elements(self):
        self.el_reads += 1
        done = self.el_reads > self.deliver_after
        els = [_ui("Pane", "StackView", [308, 71, 1100, 959])]
        for i in range(self.photos):
            d = len(els)
            top = 160 + i * 120
            els.append(_ui("Group", "FeedDelegate", [308, top - 5, 1100, top + 105], parent=0))
            els.append(_ui("Button", "RemixImageButton", [780, top, 1076, top + 100], parent=d))
            if done or i < self.photos - self.pending:
                els.append(_ui("Button", "AbstractButton", [720, top + 40, 750, top + 70], parent=d))
        els.append(_ui("Edit", "QQuickTextEdit", [320, 900, 760, 940]))
        return els


def test_messages_mark_photos_delivered_by_their_abstract_button():
    import viber_ui
    msgs = viber_ui._messages(AlbumBackend(photos=3, pending=1).elements())
    assert [m["delivered"] for m in msgs] == [True, True, False]


def test_send_settle_waits_for_photos_still_uploading_on_a_still_feed(monkeypatch):
    import viber_ui
    fake = AlbumBackend(photos=4, pending=2, deliver_after=8)
    _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._settle_and_resend(4) == {"attempts": 0, "still_unsent": False, "uploading": False}
    assert fake.el_reads > 8  # didn't return while the last two showed clocks



def test_send_settle_reports_photos_that_never_finish_uploading(monkeypatch):
    import viber_ui
    fake = AlbumBackend(photos=3, pending=1, deliver_after=10 ** 6)
    _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._settle_and_resend(3) == {"attempts": 0, "still_unsent": False, "uploading": True}


def test_send_settle_doesnt_wait_out_the_cap_when_someone_posts_after_the_album(monkeypatch):
    import viber_ui

    class Busy(AlbumBackend):
        def elements(self):
            els = super().elements()
            els.insert(-1, _ui("Group", "FeedDelegate", [308, 700, 1100, 760], parent=0))
            els.insert(-1, _ui("Text", "TextEditItem", [320, 705, 600, 755], parent=len(els) - 2, text="ok"))
            return els
    fake = Busy(photos=3, pending=0)
    slept = _use(monkeypatch, fake, fast_clock=True)
    assert viber_ui._settle_and_resend(3)["uploading"] is False
    assert sum(slept) < 5


def test_blank_sqm_is_filled_from_listing_sizes_and_tilde_name_matches_ocr():
    # CE-WH-(~, TAYTAY): the template's SQM was blank and the Taytay listing (500/710/1,420 sqm) was
    # sent, but the GC kept "~". Then rename_group refused: OCR read the header as "CE-WH-e, TAYTAY)".
    from viber_gc_logic import fill_from_listing, gc_name
    from viber_ui import _name_matches

    naming = gc_name({"name": "Ce", "property_type": "Warehouse", "sqm": "", "location": "Taytay"})
    assert naming["parts"]["sqm"] == "~" and not naming["uncertain"]
    filled = fill_from_listing(naming, "Taytay Rizal, 500, 710, 1,420  ", "WAREHOUSE")
    assert filled["gc_name"] == "CE-WH-(500-1420SQM, TAYTAY)"
    for ocr in ("CE-WH-e, TAYTAY)", "CE-WH-(-, TAYTAY)", "CE-WH-(~, TAYTAY)"):
        assert _name_matches(ocr, "CE-WH-(~, TAYTAY)")
    assert not _name_matches("CE-WH-(500SQM, TAYTAY)", "CE-WH-(~, TAYTAY)")


def test_remix_image_popup_is_dismissed_with_maybe_later_only():
    # 2026-10-27: after a GC's listing photos were posted, Viber's ChatGPT "Remix your image"
    # pop-up covered the window, so the search click missed and the DONE reply failed
    # ("No matching contact/chat found for 'APG CLIENTS'") — twice. Measured on the live app.
    from backends._winlogic import is_remix_later, remix_close_point
    els = [{"type": "Window", "class": "QQuickPopupItem", "name": "", "rect": [340, 300, 900, 640], "parent": None},
           {"type": "Button", "class": "ModalButton", "name": "Remix image", "rect": [520, 530, 716, 562], "parent": 0},
           {"type": "Button", "class": "ModalButton", "name": "Maybe later", "rect": [520, 576, 716, 608], "parent": 0}]
    assert remix_close_point(els) == (618, 592)
    assert remix_close_point(els[:2]) is None          # never "Remix image" (sends it to ChatGPT)
    assert is_remix_later("ButtonControl", "ModalButton_QMLTYPE_12", "Maybe later")
    assert not is_remix_later("ButtonControl", "ModalButton_QMLTYPE_12", "Remix image")
