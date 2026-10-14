import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import viber_drive  # noqa: E402

NUMBERS = "📱 0915 888 9482 | 0921 217 4555 | 0927 555 5803"
SAMPLE = f"""WAREHOUSE FOR RENT – MAKATI CITY

Looking for a spacious, ready-to-use warehouse ...

📐 Warehouse Area: 260 sqm
💰 Monthly Rent: ₱120,000
📲 For Inquiries & Viewing Appointments:
{NUMBERS}
--------------------------------------------------------------------------


📍 5516 Hippodromo Street, Makati City
For Inquiries & Viewing Appointments:
📱 0969-633-9222
—
cris (nikolai alpha)
"""


def test_split_sample():
    public, internal = viber_drive.split_listing(SAMPLE)
    assert public.startswith("WAREHOUSE FOR RENT")
    assert public.endswith(NUMBERS)
    for secret in ("Hippodromo", "0969", "cris", "nikolai"):
        assert secret not in public
    assert "Hippodromo" in internal and "cris (nikolai alpha)" in internal


def test_split_crlf_and_bom():
    public, internal = viber_drive.split_listing("﻿" + SAMPLE.replace("\n", "\r\n"))
    assert public.startswith("WAREHOUSE") and public.endswith(NUMBERS)
    assert "Hippodromo" not in public and "Hippodromo" in internal


@pytest.mark.parametrize("sep", ["-----   ", "_____", "———", "=========", "–––––––––"])
def test_other_separators(sep):
    public, internal = viber_drive.split_listing(f"Hello\nWorld\n{sep}\nsecret")
    assert public == "Hello\nWorld" and internal == "secret"


def test_no_separator_raises():
    with pytest.raises(viber_drive.DriveError):
        viber_drive.split_listing("Hello\n—\nsigned, me\n")


def test_separator_before_content_ignored():
    public, internal = viber_drive.split_listing("\n-----------\nHello\n-----------\nsecret")
    assert public == "Hello"
    assert internal == "secret"


FAKE = [
    {"folder_id": "1", "name": "Rizal, 1000, 2000 Taytay", "category": "WAREHOUSE",
     "deal": "FOR LEASE/RENT", "modified": "2026-01-01"},
    {"folder_id": "2", "name": "Makati City, 260 sqm", "category": "WAREHOUSE",
     "deal": "FOR LEASE/RENT", "modified": "2026-03-01"},
    {"folder_id": "3", "name": "Pasig, 4,400 sqm", "category": "LOT",
     "deal": "FOR SALE", "modified": "2026-02-01"},
]


@pytest.fixture(autouse=True)
def fake_tree(monkeypatch):
    monkeypatch.setattr(viber_drive, "_property_folders", lambda: FAKE)


def ids(**kw):
    return [p["folder_id"] for p in viber_drive.find_listings(**kw)["listings"]]


def test_find_words_any_order():
    assert ids(query="1000 Taytay") == ["1"]
    assert ids(query="taytay 1000") == ["1"]


def test_find_comma_numbers():
    assert ids(query="4400") == ["3"]
    assert ids(query="4,400") == ["3"]


def test_find_filters_and_order():
    assert ids(category="warehouse") == ["2", "1"]  # newest first
    assert ids(deal="sale") == ["3"]
    assert ids(query="makati", deal="sale") == []
    assert viber_drive.find_listings()["count"] == 3
