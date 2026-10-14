"""Templates are typed by hand in many shapes — parsing must adapt, not demand one format."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import viber_gc_logic as logic  # noqa: E402

# Raphael's real template: no emoji, "Viber No:", "Pasig or along C5", no Listing line.
RAFAEL = ("Name: Rafael Esquivel\nType of Property: Commercial\nPreferred SQM: 60-70\n"
          "Preferred Budget: Not specified\nPreferred Location: Pasig or along C5\n"
          "Viber No: ⁨09175177830⁩\n\nIntern: Raphael\nDate: 14 October 2026")


def test_rafael_template_parses_and_names():
    fields = logic.parse_template(RAFAEL)
    assert logic.viber_e164(fields["viber"]) == "+639175177830"
    naming = logic.gc_name(fields)
    assert naming["gc_name"] == "RAFAEL-CS-(60-70SQM, PASIG)"
    assert naming["uncertain"] == []


def test_rafael_template_counts_as_pending_template():
    assert logic.is_template(RAFAEL)
    assert logic.pending_templates(["DONE-DAVE-WH-(177SQM, MAKATI)", RAFAEL]) == [1]


def test_rafael_client_message_keeps_number_and_drops_intern_and_date():
    message = logic.client_message(RAFAEL)
    assert "Viber No:" in message and "09175177830" in message
    assert "Intern" not in message and "October" not in message


def test_number_label_variants():
    for label in ("Viber No", "Viber No.", "Viber #", "Viber Number", "Contact No", "Contact #",
                  "Mobile", "Mobile No", "Phone", "Cellphone No", "CP No"):
        text = f"Name: Ana\nType of Property: Warehouse\nPreferred SQM: 100\n{label}: 0917 123 4567"
        assert logic.parse_template(text)["viber"].replace(" ", "") == "09171234567", label


def test_unlabeled_number_in_client_lines_is_found():
    text = "Name: Ana\nWarehouse 100 sqm in Taguig\n0917-123-4567\n\nListing:\nTaguig, 0999 Street"
    assert logic.viber_e164(logic.parse_template(text)["viber"]) == "+639171234567"


def test_numbers_in_listing_or_links_are_never_taken_as_the_client():
    text = ("Name: Ana\nType of Property: Warehouse\nPreferred SQM: 100\n\nListing:\n"
            "Makati, 260 — call 0927 555 5803\nhttps://example.com/09171234567")
    assert logic.parse_template(text)["viber"] == ""


def test_location_variants_pick_the_first_real_place():
    cases = {
        "Pasig or along C5": "PASIG",
        "Makati / BGC": "MAKATI",
        "Valenzuela, Bulacan": "BULACAN",
        "Quezon City and Caloocan": "QC",
        "Pasig City, preferably near Ortigas": "PASIG",
        "along EDSA or Mandaluyong": "MANDALUYONG",
        "taguig(north village)": "TAGUIG",
    }
    for raw, expected in cases.items():
        loc, issue = logic._location_part(raw)
        assert (loc, issue) == (expected, None), raw


def test_location_with_no_place_is_still_flagged():
    loc, issue = logic._location_part("Along Sucat Road")
    assert loc == "?" and issue


def test_other_label_variants():
    text = ("Client Name: Bea Cruz\nProperty: office space\nSize: 120 sqm\nBudget: 80k\n"
            "Location: Ortigas, Pasig\nContact #: 09181112222")
    fields = logic.parse_template(text)
    assert logic.gc_name(fields)["gc_name"] == "BEA-OS-(120SQM, ORTIGAS)"
    assert logic.viber_e164(fields["viber"]) == "+639181112222"
