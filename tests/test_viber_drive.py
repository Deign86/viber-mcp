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


# "─" box-drawing lines are used in some docs (e.g. "Cavite, 40-45 General Trias").
@pytest.mark.parametrize("sep", ["-----   ", "_____", "———", "=========", "–––––––––",
                                 "────────────────", "━━━━━━"])
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


# ------------------------------------------------------------- adding new listings

LISTING = "WAREHOUSE FOR RENT\nMonthly Rent: ₱120,000\n-------------------------\nOwner: X\n0995 459 5602"
CATS = [{"id": "c1", "name": "WAREHOUSE"}, {"id": "c2", "name": "COMMERCIAL SPACE"},
        {"id": "c3", "name": "OFFICE SPACE"}]
DEALS = [{"id": "d1", "name": "FOR LEASE/RENT"}, {"id": "d2", "name": "FOR SALE"}]


def test_pick_folder_exact_then_contains():
    assert viber_drive.pick_folder(CATS, "warehouse", "category")["id"] == "c1"
    assert viber_drive.pick_folder(DEALS, "LEASE", "deal")["id"] == "d1"
    assert viber_drive.pick_folder(DEALS, "sale", "deal")["id"] == "d2"


def test_pick_folder_refuses_ambiguous_or_missing():
    with pytest.raises(viber_drive.DriveError, match="several"):
        viber_drive.pick_folder(CATS, "SPACE", "category")
    with pytest.raises(viber_drive.DriveError, match="Options"):
        viber_drive.pick_folder(CATS, "LOT", "category")


def test_doc_export_round_trips_without_doubling_blank_lines():
    # Live (Pio del Pilar doc, 2026-10-23): get_listing → update_listing_doc doubled every gap,
    # because Drive's text/plain export writes \r\n and two breaks per empty paragraph.
    written = "TITLE\n\nFloor Area: 25 sqm\nGROUND\n\n\nNOTE\n---\nOwner: X"
    exported = ("﻿TITLE\r\n\r\n\r\nFloor Area: 25 sqm\r\nGROUND\r\n\r\n\r\n\r\n\r\nNOTE\r\n"
                "---\r\nOwner: X").encode("utf-8")
    assert viber_drive._plain_export(exported) == written
    assert viber_drive._plain_export("a\nb\n\nc") == "a\nb\n\nc"  # already plain: unchanged


def test_listing_doc_text_adds_drive_date_once():
    body = viber_drive.listing_doc_text(LISTING, "October 16, 2026")
    assert body.endswith("\n\nAdded to Drive: October 16, 2026")
    assert viber_drive.listing_doc_text(body, "October 17, 2026") == body


def test_added_to_drive_reply_format():
    assert (viber_drive.added_to_drive_reply(" Pasay City, 8, 12, 22 Malibay ")
            == "Added to Drive [File name:*Pasay City, 8, 12, 22 Malibay]")


def test_find_posted_message_ignores_viber_isolate_marks():
    # Viber shows numbers/links wrapped in U+2068/U+2069, so a plain == never matched.
    shown = LISTING.replace("0995 459 5602", "⁨0995 459 5602⁩")
    msgs = [{"n": 3, "from": "me", "kind": "text", "text": shown},
            {"n": 2, "from": "me", "kind": "image", "text": ""},
            {"n": 1, "from": "me", "kind": "text", "text": viber_drive.GC_SEPARATOR}]
    assert viber_drive.find_posted_message(msgs, LISTING) == 3


def test_find_posted_message_only_mine_and_falls_back_to_title():
    msgs = [{"n": 2, "from": "them", "kind": "text", "text": LISTING},
            {"n": 1, "from": "me", "kind": "text", "text": "WAREHOUSE FOR RENT\n(edited)"}]
    assert viber_drive.find_posted_message(msgs, LISTING) == 1
    assert viber_drive.find_posted_message(msgs[:1], LISTING) is None


def _fake_drive(monkeypatch):
    monkeypatch.setattr(viber_drive, "_listings_root", lambda: "root")
    tree = {"root": CATS, "c1": DEALS, "c2": DEALS, "c3": DEALS}
    monkeypatch.setattr(viber_drive, "_children", lambda fid: [
        dict(f, mimeType=viber_drive.FOLDER) for f in tree.get(fid, [])])


def test_check_new_listing_resolves_parent(monkeypatch, tmp_path):
    _fake_drive(monkeypatch)
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"jpg")
    plan = viber_drive._check_new_listing(LISTING, "warehouse", "lease",
                                          " Quezon City, 860 Apolonio Samson ", [str(photo)])
    assert plan["parent_id"] == "d1"
    assert plan["folder_name"] == "Quezon City, 860 Apolonio Samson"
    assert plan["path"].endswith("WAREHOUSE / FOR LEASE/RENT")


def test_check_new_listing_refuses_before_sending(monkeypatch, tmp_path):
    _fake_drive(monkeypatch)
    with pytest.raises(viber_drive.DriveError, match="separator"):
        viber_drive._check_new_listing("no line here", "warehouse", "lease", "A, 1 B", [])
    with pytest.raises(viber_drive.DriveError, match="not found"):
        viber_drive._check_new_listing(LISTING, "warehouse", "lease", "A, 1 B",
                                       [str(tmp_path / "missing.jpg")])
    with pytest.raises(viber_drive.DriveError, match="City, sqm Area"):
        viber_drive._check_new_listing(LISTING, "warehouse", "lease", "Malibay", [])


def _doc_content(*texts):
    """A Docs API body: the leading section break, then one paragraph per text (ASCII only, so
    len() equals UTF-16 units)."""
    content, i = [{"endIndex": 1, "sectionBreak": {}}], 1
    for t in texts:
        content.append({"startIndex": i, "endIndex": i + len(t),
                        "paragraph": {"elements": [{"textRun": {"content": t}}]}})
        i += len(t)
    return content


def test_availability_line_format():
    from datetime import date
    day = date(2026, 10, 22)
    assert (viber_drive.availability_line("available.", " Owner Joseph See confirmed on Viber. ", day)
            == "Last availability check (Oct 22, 2026): AVAILABLE. Owner Joseph See confirmed on Viber.")
    assert (viber_drive.availability_line("not available", day=date(2026, 10, 5))
            == "Last availability check (Oct 5, 2026): NOT AVAILABLE.")


def test_availability_goes_first_under_the_separator():
    # TITLE\n = 1..7, separator 7..13, blank 13..14, commission 14..
    content = _doc_content("TITLE\n", "-----\n", "\n", "1 MONTH STANDARD COMMISSION\n")
    assert viber_drive.availability_requests(content, "LINE") == [
        {"insertText": {"location": {"index": 13}, "text": "LINE\n"}}]


def test_availability_replaces_the_previous_check_line():
    content = _doc_content("TITLE\n", "-----\n", "Last availability check (Oct 1, 2026): AVAILABLE.\n",
                           "Owner: X\n")
    old_start, old_end = content[3]["startIndex"], content[3]["endIndex"]
    assert viber_drive.availability_requests(content, "NEW") == [
        {"deleteContentRange": {"range": {"startIndex": old_start, "endIndex": old_end - 1}}},
        {"insertText": {"location": {"index": old_start}, "text": "NEW"}}]


def test_availability_never_lands_in_the_client_part():
    # A check line ABOVE the separator is client text: leave it, add the note below the line.
    content = _doc_content("Last availability check is free\n", "-----\n", "Owner: X\n")
    assert viber_drive.availability_requests(content, "LINE") == [
        {"insertText": {"location": {"index": content[2]["endIndex"]}, "text": "LINE\n"}}]
    # A rule before any content isn't the separator; with no real one there's no internal part.
    with pytest.raises(viber_drive.DriveError, match="separator"):
        viber_drive.availability_requests(_doc_content("-----\n", "TITLE\n", "Owner: X\n"), "LINE")


def test_availability_when_the_separator_is_the_last_line():
    content = _doc_content("TITLE\n", "-----\n")
    assert viber_drive.availability_requests(content, "LINE") == [
        {"insertText": {"location": {"index": content[2]["endIndex"] - 1}, "text": "\nLINE"}}]


class _FakeDocs:
    def __init__(self, content):
        self.doc = {"revisionId": "rev-1", "body": {"content": content}}
        self.updates = []

    def documents(self):
        return self

    def get(self, documentId):
        return _Call(self.doc)

    def batchUpdate(self, documentId, body):
        self.updates.append((documentId, body))
        return _Call({})


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self, num_retries=0):
        return self.result


def test_record_availability_edits_in_place_with_revision_guard(monkeypatch):
    docs = _FakeDocs(_doc_content("TITLE\n", "-----\n", "Owner: X\n"))
    monkeypatch.setattr(viber_drive, "_docs", lambda: docs)
    monkeypatch.setattr(viber_drive, "_children", lambda fid: [
        {"id": "doc1", "name": "*Cavite, 1890 Silang", "mimeType": viber_drive.GDOC},
        {"id": "p1", "name": "1.jpg", "mimeType": "image/jpeg"}])
    got = viber_drive.record_availability("f1", "available", "Owner confirmed on Viber.", "2026-10-22")
    assert got["line"] == "Last availability check (Oct 22, 2026): AVAILABLE. Owner confirmed on Viber."
    assert got["replaced_previous"] is False
    doc_id, body = docs.updates[0]
    assert doc_id == "doc1" and body["writeControl"] == {"requiredRevisionId": "rev-1"}
    with pytest.raises(viber_drive.DriveError, match="status"):
        viber_drive.record_availability("f1", " ")


def test_listing_photos_are_shrunk_and_counted(monkeypatch, tmp_path):
    pil = pytest.importorskip("PIL.Image")
    big = tmp_path / "big.jpg"
    pil.new("RGB", (4000, 3000), "white").save(big)
    monkeypatch.setattr(viber_drive, "_children", lambda fid: [
        {"id": f"p{i}", "name": f"{i}.jpg", "mimeType": "image/jpeg"} for i in range(3)]
        + [{"id": "v", "name": "tour.mp4", "mimeType": "video/mp4"},
           {"id": "d", "name": "*doc", "mimeType": viber_drive.GDOC}])
    monkeypatch.setattr(viber_drive, "download", lambda ids: [str(big)] * len(ids))
    got = viber_drive.listing_photos("f1", max_photos=2, size=800)
    assert got["names"] == ["0.jpg", "1.jpg"] and got["total_photos"] == 3 and got["videos"] == 1
    import io
    assert max(pil.open(io.BytesIO(got["images"][0])).size) == 800
