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
