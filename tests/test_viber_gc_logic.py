import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from viber_gc_logic import (  # noqa: E402
    clean, client_message, done_text, gc_name, parse_template, viber_e164,
)

ISO = "⁨", "⁩"

TPL1 = (
    "🏢 Company Name:  Plastic Manuf.\n🏗️ Type of Property:  Warehouse\n👤 Name: Lori\n"
    "📐 Preferred SQM: ⁨1200-1350⁩ sqm\n💰 Preferred Budget: \n"
    "📍 Preferred Location:  Valenzuela, Bulacan\n📱Viber: ⁨09639042544⁩\n\n"
    "Listings:\nQuezon City, 1,200 Novaliches"
)
TPL2 = (
    "Name: krizza\nLine of Business: logistic\nProperty: warehouse\nPreferred Sqm: 300-500\n"
    "Preferred Location:  taguig(north village,wester bicutan,pinagsama\nViber Number:09369469240"
)
TPL7 = (
    "👤 Name: Glenn Casisirano\n🏗️ Type of Property: Warehouse\n📐 Preferred SQM: 300–500 sqm\n"
    "📍 Preferred Location: Pasig City, preferably near Ortigas\n"
    "🛠️ Intended Use: Storage and minor fabrication, including welding and grinding works\n"
    "📱 Viber: ⁨09171704489⁩  \n      Listing: Pasig, 741 sqm, Santolan  \n"
    "      Intern: Fred\nDate: October 13, 2026"
)
TPL8 = (
    "Kindly fill this up:\n🏢 Company Name: \n👤 Name:  Karen Dianne \n🏗️ Type of Property: Warehouse \n"
    "📐 Preferred SQM: 250 sqm\n💰 Preferred Budget: ~\n📍 Preferred Location: Makati\n"
    "📱Viber: ⁨0917 642 2567⁩\n\n\nListing: Makati, 260 Hippodromo Street \n"
    "⁨https://drive.google.com/drive/u/0/folders/1tiN3...⁩\n\nIntern: Ar-jee\nDate: 10/13/2026"
)
TPL9 = (
    "🏢 Company Name: Solar Company\n👤 Name: Marvee\n🏗️ Type of Property: Commercial Space for Showroom\n"
    "📐 Preferred SQM: 50–100 sqm\n💰 Preferred Budget: Flexible\n📍 Preferred Location: Along Sucat Road\n"
    "📱 Viber: ⁨09352091466⁩\n\nListing: Paranaque, 230.75 Sucat Road\n\nIntern: Melanie \nOct 9, 2026"
)


def name_of(tpl):
    return gc_name(parse_template(tpl))


def test_clean_strips_invisibles_and_normalizes_newlines():
    out = clean("﻿⁨091⁩‎‏\r\nx\ry")
    assert out == "091\nx\ny"


def test_parse_example1():
    f = parse_template(TPL1)
    assert f["company"] == "Plastic Manuf."
    assert f["name"] == "Lori"
    assert f["property_type"] == "Warehouse"
    assert f["sqm"] == "1200-1350 sqm"
    assert f["budget"] == ""
    assert f["viber"] == "09639042544"
    assert f["listing"] == "Quezon City, 1,200 Novaliches"
    assert not any(c in "".join(f.values()) for c in ISO)


def test_parse_example2_variant_labels():
    f = parse_template(TPL2)
    assert (f["name"], f["property_type"], f["sqm"]) == ("krizza", "warehouse", "300-500")
    assert f["viber"] == "09369469240"
    assert f["line_of_business"] == "logistic"


def test_parse_example7_and_8_extras():
    f7 = parse_template(TPL7)
    assert f7["listing"] == "Pasig, 741 sqm, Santolan"
    assert f7["intern"] == "Fred" and f7["date"] == "October 13, 2026"
    assert f7["intended_use"].startswith("Storage and minor")
    f8 = parse_template(TPL8)
    assert f8["listing"] == "Makati, 260 Hippodromo Street"
    assert f8["company"] == "" and f8["intern"] == "Ar-jee"
    f9 = parse_template(TPL9)
    assert f9["date"] == "Oct 9, 2026"


@pytest.mark.parametrize("tpl,expected", [
    (TPL1, "LORI-WH-(1200-1350SQM, BULACAN)"),
    (TPL2, "KRIZZA-WH-(300-500SQM, TAGUIG)"),
    (TPL7, "GLENN-WH-(300-500SQM, PASIG)"),
    (TPL8, "KAREN-WH-(250SQM, MAKATI)"),
])
def test_gc_name_from_templates(tpl, expected):
    res = name_of(tpl)
    assert res["gc_name"] == expected
    assert res["uncertain"] == []


def test_example9_uncertain_location():
    res = name_of(TPL9)
    assert res["gc_name"] == "MARVEE-CS-(50-100SQM, ?)"
    assert res["uncertain"]


@pytest.mark.parametrize("fields,expected", [
    ({"name": "Daryll", "property_type": "Commercial Space", "sqm": "400-500", "location": "Quezon City"},
     "DARYLL-CS-(400-500SQM, QC)"),
    ({"name": "Casimir", "property_type": "Warehouse", "sqm": "300-500", "location": "Bulacan"},
     "CASIMIR-WH-(300-500SQM, BULACAN)"),
    ({"name": "Odern", "property_type": "Virtual Office"}, "ODERN-VIRTUALOFFICE"),
    ({"name": "CH", "property_type": "Office Space", "sqm": "1200", "location": "Pasig"},
     "CH-OS-(1200SQM, PASIG)"),
    ({"name": "Patrick", "property_type": "Warehouse", "sqm": "~", "location": "Makati"},
     "PATRICK-WH-(~, MAKATI)"),
    ({"name": "Patrick", "property_type": "Warehouse", "sqm": "", "location": "Makati"},
     "PATRICK-WH-(~, MAKATI)"),
    ({"name": "JNT", "property_type": "Warehouse", "sqm": "240", "location": "Las Piñas City"},
     "JNT-WH-(240SQM, LAS PIÑAS)"),
    ({"name": "Charina", "property_type": "House and Lot", "sqm": "135", "location": "Cavite"},
     "CHARINA-HOUSE-(135SQM, CAVITE)"),
    ({"name": "Ana", "property_type": "Lot", "sqm": "1,200", "location": "Rizal"},
     "ANA-LOT-(1200SQM, RIZAL)"),
    ({"name": "Ben", "property_type": "Condo", "sqm": "30 sqm", "location": "QC"},
     "BEN-CONDO-(30SQM, QC)"),
    ({"name": "Tap Talent", "property_type": "Office", "sqm": "50–80 sqm", "location": "Makati City"},
     "TAP-OS-(50-80SQM, MAKATI)"),
    ({"name": "Kim", "property_type": "Retail", "sqm": "250", "location": "Quezon City, Metro Manila"},
     "KIM-CS-(250SQM, QC)"),
    ({"name": "Zed", "property_type": "Warehouse", "sqm": "100", "location": "Quezon Province"},
     "ZED-WH-(100SQM, QUEZON PROVINCE)"),
])
def test_gc_name_rules(fields, expected):
    assert gc_name(fields)["gc_name"] == expected


def test_unknown_type_is_uncertain():
    res = gc_name({"name": "Bo", "property_type": "Farm", "sqm": "10", "location": "Pasig"})
    assert res["uncertain"] and res["gc_name"].startswith("BO-?-")


def test_done_text():
    assert done_text("LORI-WH-(1200-1350SQM, BULACAN)") == "DONE-LORI-WH-(1200-1350SQM, BULACAN)"


@pytest.mark.parametrize("tpl", [TPL1, TPL7, TPL8, TPL9])
def test_client_message_privacy(tpl):
    msg = client_message(tpl)
    for banned in ("Listing", "Intern", "Date", "drive.google", "http", "Novaliches",
                   "Santolan", "Hippodromo", "Paranaque", "Kindly", "2026", "Fred", "Melanie"):
        assert banned not in msg
    assert not any(c in msg for c in ISO)
    assert all(l == l.rstrip() for l in msg.split("\n"))


def test_client_message_verbatim_lines():
    lines = TPL8.split("\n")
    kept = (lines[i].replace(ISO[0], "").replace(ISO[1], "").rstrip() for i in range(1, 8))
    expected = "\n".join(l + " ~" if l.endswith(":") else l for l in kept)  # blank field -> "~"
    msg = client_message(TPL8)
    assert msg == expected
    assert "👤" in msg and "🏗️ Type of Property: Warehouse" in msg
    assert "📱Viber: 0917 642 2567" in msg


def test_client_message_keeps_intended_use_and_lob():
    assert "Intended Use: Storage" in client_message(TPL7)
    assert "Line of Business: logistic" in client_message(TPL2)


@pytest.mark.parametrize("raw,expected", [
    ("09639042544", "+639639042544"),
    ("0917 642 2567", "+639176422567"),
    ("⁨0917 642 2567⁩", "+639176422567"),
    ("+639171704489", "+639171704489"),
    ("639171704489", "+639171704489"),
    ("+63 917-170-4489", "+639171704489"),
])
def test_viber_e164(raw, expected):
    assert viber_e164(raw) == expected


@pytest.mark.parametrize("raw", ["", "0212345678", "0917123", "abc", "+14155550123", "08171704489"])
def test_viber_e164_invalid(raw):
    with pytest.raises(ValueError):
        viber_e164(raw)


def test_listing_after_blank_line():
    text = ("👤 Name: Testclient\n🏗 Type of Property: Warehouse\n📐 Preferred SQM: 300 sqm\n"
            "📍 Preferred Location: Quezon City\n📱Viber: ⁨09754659812⁩\n\n"
            "Intern: Testing\n\nListing:\n\nAntipolo, 520 Cupang")
    fields = parse_template(text)
    assert fields["listing"] == "Antipolo, 520 Cupang"
    assert "Antipolo" not in client_message(text)
