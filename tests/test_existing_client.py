"""Existing-client notice: who posted the template, and the notice counting as handled."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import viber_gc_logic as logic  # noqa: E402
from viber_ui import sender_above  # noqa: E402


def _line(text, x, y):
    return {"text": text, "x": x, "y": y, "w": 150, "h": 20, "W": 1101, "H": 967, "SB": 308}


# Arjee's template as laid out in APG CLIENTS: name at the top of the bubble, then a Drive link
# card (indented), then the template text starting at y=272.
ARJEE_LINES = [
    _line("11:03 AM", 590, 120),                       # previous bubble's time
    _line("Arjee Alpha", 371, 159),
    _line("Makati, 140 Bangkal -", 455, 196),
    _line("Google Drive", 455, 213),
    _line("drive.google.com", 455, 240),
    _line("Company Name:", 392, 281),
]
TEXT_RECT = [371, 272, 638, 595]


def test_sender_found_above_a_link_card():
    assert sender_above(ARJEE_LINES, TEXT_RECT, floor_y=140) == "Arjee Alpha"


def test_previous_message_text_is_not_taken_as_sender():
    lines = [_line("Beatriz Alpha", 371, 100), _line("Hello po", 371, 125)] + ARJEE_LINES[1:]
    # floor at the previous message's bottom keeps its lines out
    assert sender_above(lines, TEXT_RECT, floor_y=150) == "Arjee Alpha"


def test_no_sender_line_gives_empty():
    assert sender_above([_line("Company Name:", 392, 281)], TEXT_RECT, floor_y=140) == ""


def test_existing_client_notice_marks_the_template_handled():
    template = ("Name: Lori\nType of Property: Warehouse\nPreferred SQM: 1200-1350\n"
                "Preferred Location: Valenzuela, Bulacan\nViber No: 09639042544")
    notice = "DONE-LORI-WH-(1200-1350SQM, BULACAN) — @Arjee Alpha existing client po, GC already created."
    assert logic.pending_templates([template, notice]) == []


def test_quoted_block_lines_are_not_the_sender():
    # A quote-reply whose sender name scrolled off: the quoted template lines are indented
    # (x=379 vs 371) and contain "Label:" text — neither may be returned as the sender.
    lines = [_line("You", 379, 520), _line("Company Name: Not specified", 379, 540),
             _line("2 Name: Claudia", 379, 560)]
    assert sender_above(lines, [371, 600, 638, 620], floor_y=500) == ""
