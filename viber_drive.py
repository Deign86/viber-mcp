"""Google Drive access for property listings: find a listing, read its client-facing details,
download its photos, and send them to the open Viber chat.

Auth (read-only Drive), first match wins:
1. A read-only service account key at %LOCALAPPDATA%\\viber-mcp\\google_service_account.json
   (or VIBER_MCP_GOOGLE_SERVICE_ACCOUNT); the listings folder must be shared with its email.
   (gcloud's own application-default login can't be used: Google blocks it for Drive.)
2. OAuth "desktop app" sign-in, once per Windows user, using `google_oauth_client.json` next to
   this file (git-ignored) or VIBER_MCP_GOOGLE_CLIENT; token kept in google_token.json there.

Listing layout (APR LISTING): <root> / <CATEGORY> / <FOR LEASE/RENT | FOR SALE> / <property
folder>, where a property folder holds one listing doc (Google Doc or .docx) plus its photos.
The doc's client-facing text sits ABOVE a typed dashed separator line; everything below it is
internal and is never returned unless explicitly requested for staff eyes.
"""

from __future__ import annotations

import io
import os
import re
import time
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_HERE = Path(__file__).resolve().parent
_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "viber-mcp"
CLIENT_FILE = Path(os.environ.get("VIBER_MCP_GOOGLE_CLIENT", _HERE / "google_oauth_client.json"))
TOKEN_FILE = _DATA / "google_token.json"
SERVICE_ACCOUNT_FILE = Path(os.environ.get("VIBER_MCP_GOOGLE_SERVICE_ACCOUNT",
                                           _DATA / "google_service_account.json"))
CACHE_DIR = _DATA / "drive-cache"
# Downloads are kept this long before being pruned. Deleting right after send_files raced
# Viber's own upload (it reads the file after the click) and left photos "not sent".
KEEP_DOWNLOADS_DAYS = float(os.environ.get("VIBER_MCP_KEEP_DOWNLOADS_DAYS", "3"))
LISTINGS_ROOT_NAME = os.environ.get("VIBER_MCP_LISTINGS_FOLDER", "APR LISTING")
# The staff GC where new listings are posted (text, photos, separator, "Added to Drive" reply).
LISTINGS_GC = os.environ.get("VIBER_MCP_LISTINGS_GC", "APR LISTING ONLY")
GC_SEPARATOR = "-" * 39
# Drive calls retry this many times (with backoff) on 5xx/429 and dropped connections; both
# happen often enough to break a listing lookup.
RETRIES = 3

FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SEPARATOR = re.compile(r"^\s*[-_—–=─━]{3,}\s*$")  # a lone "—" (signature line) is not one
# corpora=allDrives so Shared Drive items are searched too, not just "My Drive".
_ALL = {"supportsAllDrives": True, "includeItemsFromAllDrives": True, "corpora": "allDrives"}

_service = None
_tree_cache: tuple[float, list[dict]] | None = None


class DriveError(Exception):
    """Raised when Drive can't be reached or a listing can't be read safely."""


# ------------------------------------------------------------------------------ auth


def _creds(scopes: list[str] = SCOPES, token_file: Path = TOKEN_FILE):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if SERVICE_ACCOUNT_FILE.exists():
        # Service account (created with gcloud); the listing folder is shared with it (as an
        # editor if listings are to be added).
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_FILE), scopes=scopes)

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not CLIENT_FILE.exists():
            raise DriveError(
                "Google Drive isn't set up on this PC. Ask the admin for the service account key "
                f"and save it as {SERVICE_ACCOUNT_FILE} (or run setup.ps1 -ServiceAccountKey <path>), "
                f"or add the OAuth client file as {CLIENT_FILE} (see README → Google Drive)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), scopes)
        # Opens the browser once; the staff member signs in with their company Google account.
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _drive():
    global _service
    if _service is None:
        from googleapiclient.discovery import build
        _service = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    return _service


def sign_in() -> dict:
    """Sign in to Google Drive (opens a browser the first time) and report the account."""
    about = _drive().about().get(fields="user(displayName,emailAddress)").execute(num_retries=RETRIES)
    return {"signed_in": True, "account": about["user"]}


# ----------------------------------------------------------------------- primitives


def _list(q: str, fields: str = "id,name,mimeType,modifiedTime,size,parents") -> list[dict]:
    out, token = [], None
    while True:
        resp = _drive().files().list(
            q=q, fields=f"nextPageToken,files({fields})", pageSize=1000, pageToken=token,
            orderBy="name", **_ALL,
        ).execute(num_retries=RETRIES)
        out += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return out


def _children(folder_id: str) -> list[dict]:
    return _list(f"'{folder_id}' in parents and trashed = false")


def _quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def search(query: str, mime_contains: str = "") -> list[dict]:
    """Drive files whose name contains `query` (optionally filtered by MIME type)."""
    q = f"name contains '{_quote(query)}' and trashed = false"
    if mime_contains:
        q += f" and mimeType contains '{_quote(mime_contains)}'"
    return [{"id": f["id"], "name": f["name"], "type": f["mimeType"], "modified": f["modifiedTime"]}
            for f in _list(q)[:50]]


def download(file_ids: list[str]) -> list[str]:
    """Download Drive files to the local cache; returns their paths (cached files are reused)."""
    from googleapiclient.http import MediaIoBaseDownload

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for fid in file_ids:
        meta = _drive().files().get(fileId=fid, fields="id,name,size,mimeType",
                                    supportsAllDrives=True).execute(num_retries=RETRIES)
        if meta["mimeType"].startswith("application/vnd.google-apps"):
            raise DriveError(f"'{meta['name']}' is a Google Docs file, not a downloadable file.")
        safe = re.sub(r'[<>:"/\\|?*]', "_", meta["name"])
        path = CACHE_DIR / f"{fid}_{safe}"
        if not (path.exists() and str(path.stat().st_size) == meta.get("size")):
            buf = io.BytesIO()
            req = _drive().files().get_media(fileId=fid, supportsAllDrives=True)
            dl = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk(num_retries=RETRIES)
            path.write_bytes(buf.getvalue())
        paths.append(str(path))
    return paths


# ------------------------------------------------------------------------ listings


def _listings_root() -> str:
    folders = _list(f"name = '{_quote(LISTINGS_ROOT_NAME)}' and mimeType = '{FOLDER}' and trashed = false")
    if not folders:
        raise DriveError(f"Can't find the '{LISTINGS_ROOT_NAME}' folder in your Drive.")
    return folders[0]["id"]


def _is_subfolder_group(folder: dict, kids: dict[str, list[dict]]) -> bool:
    """A folder under a deal folder that groups property folders (e.g. CONDO / HOUSE AND LOT /
    FOR LEASE/ FOR RENT / LOT) rather than being one: it has subfolders and, unlike property
    folders ("City, sqm Area"), no comma in its name."""
    return "," not in folder["name"] and bool(kids.get(folder["id"]))


def _property_folders() -> list[dict]:
    """All property folders: root / category / deal-type / [group /] property. Cached 5 minutes."""
    global _tree_cache
    if _tree_cache and time.time() - _tree_cache[0] < 300:
        return _tree_cache[1]
    # One paged query for every visible folder, then walk the tree in memory (a call per
    # folder takes minutes).
    folders = _list(f"mimeType = '{FOLDER}' and trashed = false", fields="id,name,modifiedTime,parents")
    kids: dict[str, list[dict]] = {}
    for f in folders:
        for parent in f.get("parents", []):
            kids.setdefault(parent, []).append(f)
    root = next((f["id"] for f in folders if f["name"] == LISTINGS_ROOT_NAME), None) or _listings_root()
    out = []
    for cat in kids.get(root, []):
        for deal in kids.get(cat["id"], []):
            for prop in kids.get(deal["id"], []):
                if _is_subfolder_group(prop, kids):
                    out += [{"folder_id": p["id"], "name": p["name"], "category": cat["name"],
                             "deal": f"{deal['name']} / {prop['name']}", "modified": p["modifiedTime"]}
                            for p in kids[prop["id"]]]
                    continue
                out.append({"folder_id": prop["id"], "name": prop["name"],
                            "category": cat["name"], "deal": deal["name"],
                            "modified": prop["modifiedTime"]})
    _tree_cache = (time.time(), out)
    return out


def find_listings(query: str = "", category: str = "", deal: str = "") -> dict:
    """Property folders whose name matches every word of `query` (e.g. "Taytay 1000")."""
    words = [w.lower().replace(",", "") for w in query.split()]
    hits = []
    for p in _property_folders():
        name = p["name"].lower().replace(",", "")
        if category and category.lower() not in p["category"].lower():
            continue
        if deal and deal.lower() not in p["deal"].lower():
            continue
        if all(w in name for w in words):
            hits.append(p)
    hits.sort(key=lambda p: p["modified"], reverse=True)
    return {"count": len(hits), "listings": hits[:40],
            "note": "Folder names are 'City, sqm Area'. Pick one and call get_listing(folder_id)."}


def _doc_text(f: dict) -> str:
    if f["mimeType"] == GDOC:
        data = _drive().files().export(fileId=f["id"], mimeType="text/plain").execute(num_retries=RETRIES)
        return data.decode("utf-8-sig") if isinstance(data, bytes) else data
    if f["mimeType"] == DOCX:
        import docx

        path = download([f["id"]])[0]
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    raise DriveError(f"Unsupported listing document type: {f['mimeType']}")


def split_listing(text: str) -> tuple[str, str]:
    """(client-facing part above the dashed separator, internal part below it)."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        if _SEPARATOR.match(line) and any(l.strip() for l in lines[:i]):
            head = [l for l in lines[:i] if not _SEPARATOR.match(l)]  # drop a stray leading rule
            return "\n".join(head).strip(), "\n".join(lines[i + 1:]).strip()
    raise DriveError("No dashed separator line found in the listing doc — can't tell which part "
                     "is client-facing. Ask the staff member; don't send anything from this doc.")


def get_listing(folder_id: str, include_internal: bool = False) -> dict:
    """The listing in a property folder: client-facing text, and its photos (and videos)."""
    files = _children(folder_id)
    docs = [f for f in files if f["mimeType"] in (GDOC, DOCX)]
    if not docs:
        raise DriveError("No listing document (Google Doc / .docx) in this folder.")
    if len(docs) > 1:
        # Listing docs are conventionally prefixed with '*'.
        starred = [d for d in docs if d["name"].startswith("*")]
        docs = starred or docs
    doc = docs[0]
    public, internal = split_listing(_doc_text(doc))
    photos = [f for f in files if f["mimeType"].startswith("image/")]
    videos = [f for f in files if f["mimeType"].startswith("video/")]
    result = {
        "folder_id": folder_id,
        "doc": doc["name"],
        "client_text": public,
        "photos": [{"id": f["id"], "name": f["name"]} for f in photos],
        "videos": [{"id": f["id"], "name": f["name"]} for f in videos],
        "internal_part": "withheld (below the separator — never send to clients)",
    }
    if include_internal:
        result["internal_part"] = "INTERNAL — FOR STAFF ONLY, NEVER SEND:\n" + internal
    return result


def send_listing(folder_id: str, max_photos: int = 20, include_videos: bool = False,
                 dry_run: bool = False) -> dict:
    """Send a listing to the OPEN Viber chat: its client-facing text, then its photos together.
    The internal part of the doc is never sent."""
    import viber_send
    import viber_ui

    listing = get_listing(folder_id)
    media = listing["photos"][:max(0, max_photos)]
    if include_videos:
        media += listing["videos"]
    plan = {"doc": listing["doc"], "text": listing["client_text"],
            "files": [m["name"] for m in media]}
    if dry_run:
        return {"sent": False, "would_send": plan,
                "note": "Nothing sent. Check the chat and the text, then call again without dry_run."}
    paths = download([m["id"] for m in media])  # download first so a Drive error sends nothing
    try:
        text_result = viber_send.reply(listing["client_text"])
        files_result = viber_ui.send_files(paths) if paths else {"sent": False, "files": []}
    finally:
        prune_downloads()  # keep fresh photos (Viber may still be uploading); drop old ones
    return {"sent": True, "doc": listing["doc"], "text_verified": text_result.get("verified"),
            "photos_sent": len(paths), "photos_verified": files_result.get("verified")}


# ------------------------------------------------------------- adding new listings


def pick_folder(folders: list[dict], wanted: str, kind: str) -> dict:
    """The one folder whose name is `wanted` (case-insensitive), else the one containing it
    (so deal "LEASE" finds "FOR LEASE/RENT" and "SALE" finds "FOR SALE"). Raises if none or
    several match."""
    w = wanted.strip().casefold()
    if not w:
        raise DriveError(f"No {kind} given.")
    exact = [f for f in folders if f["name"].strip().casefold() == w]
    hits = exact or [f for f in folders if w in f["name"].casefold()]
    names = ", ".join(sorted(f["name"] for f in folders))
    if not hits:
        raise DriveError(f"No {kind} folder matches '{wanted}'. Options: {names}")
    if len(hits) > 1:
        raise DriveError(f"'{wanted}' matches several {kind} folders "
                         f"({', '.join(f['name'] for f in hits)}); be more specific.")
    return hits[0]


def listing_doc_text(text: str, added: str) -> str:
    """The listing doc body: the posted text plus the 'Added to Drive: <date>' line staff add."""
    text = text.strip()
    if "added to drive" in text.casefold():
        return text
    return f"{text}\n\nAdded to Drive: {added}"


def added_to_drive_reply(folder_name: str) -> str:
    """The quote-reply staff post under a listing in the listings GC once it's in Drive."""
    return f"Added to Drive [File name:*{folder_name.strip()}]"


def _norm_message(text: str) -> str:
    # Viber wraps numbers/links in Unicode isolate marks and may trim trailing spaces.
    text = text.replace("⁨", "").replace("⁩", "").replace("\r\n", "\n")
    return "\n".join(line.strip() for line in text.strip().split("\n"))


def find_posted_message(messages: list[dict], text: str) -> int | None:
    """n of MY newest visible text message equal to `text` (or, failing that, starting with its
    first line), from list_messages output. None if it isn't on screen."""
    want = _norm_message(text)
    first = want.split("\n", 1)[0]
    mine = [m for m in messages if m.get("from") == "me" and m.get("kind") == "text"]
    for match in (lambda t: t == want, lambda t: t.split("\n", 1)[0] == first):
        hits = [m["n"] for m in mine if match(_norm_message(m.get("text", "")))]
        if hits:
            return min(hits)
    return None


def _check_new_listing(text: str, category: str, deal: str, folder_name: str,
                       photo_paths: list[str], subfolder: str = "") -> dict:
    """Validate a new listing and resolve its Drive parent folder. Changes nothing."""
    split_listing(text)  # raises if there's no dashed separator between client and internal parts
    folder_name = folder_name.strip()
    if not folder_name or "," not in folder_name:
        raise DriveError("folder_name must follow 'City, sqm Area' (e.g. 'Pasay City, 8, 12, 22 Malibay').")
    photos = [Path(p).expanduser().resolve() for p in photo_paths]
    missing = [str(p) for p in photos if not p.is_file()]
    if missing:
        raise DriveError(f"Photo file(s) not found: {', '.join(missing)}")
    root = _listings_root()
    cat = pick_folder([f for f in _children(root) if f["mimeType"] == FOLDER], category, "category")
    parent = pick_folder([f for f in _children(cat["id"]) if f["mimeType"] == FOLDER], deal, "deal")
    path = f"{LISTINGS_ROOT_NAME} / {cat['name']} / {parent['name']}"
    if subfolder.strip():
        parent = pick_folder([f for f in _children(parent["id"]) if f["mimeType"] == FOLDER],
                             subfolder, "subfolder")
        path += f" / {parent['name']}"
    clash = [p for p in _property_folders()
             if p["name"].strip().lstrip("*").strip().casefold() == folder_name.casefold()]
    if clash:
        raise DriveError(f"A listing folder named '{folder_name}' already exists "
                         f"({clash[0]['category']} / {clash[0]['deal']}) — it may already be posted. "
                         "Check before adding it again.")
    return {"parent_id": parent["id"], "path": path,
            "folder_name": folder_name, "photos": [str(p) for p in photos]}


def drive_upload_plan(text: str, plan: dict, added: str) -> dict:
    """What to create in Drive (with the Drive connector — this server never uploads): the folder,
    the '*' listing doc and the photos, named the way staff name them."""
    return {
        "parent_folder_id": plan["parent_id"],
        "parent_path": plan["path"],
        "folder_name": plan["folder_name"],
        "doc_title": f"*{plan['folder_name']}",
        "doc_text": listing_doc_text(text, added),
        "photos": [{"path": p, "name": f"AlphaPremier_Photo_{i:02d}{Path(p).suffix.lower() or '.jpg'}"}
                   for i, p in enumerate(plan["photos"], 1)],
    }


def is_listings_gc(matched: str) -> bool:
    """True if the opened chat is the listings GC. OCR sometimes tacks a stray mark onto the
    header (seen: 'APR LISTING ONLY w'); anything longer than that is a different chat."""
    got, want = matched.strip().casefold(), LISTINGS_GC.casefold()
    return got == want or (got.startswith(want + " ") and len(got) - len(want) <= 3)


def _open_listings_gc() -> None:
    import viber_send

    opened = viber_send.search_and_open(LISTINGS_GC)
    if not is_listings_gc(opened.get("matched", "")):
        raise DriveError(f"Opened '{opened.get('matched')}' instead of '{LISTINGS_GC}'. Nothing sent.")


def _quote_reply_posted(text: str, reply_text: str, tries: int = 8) -> dict:
    """Quote-reply `reply_text` to my posted message `text` in the open chat, scrolling up to it."""
    import viber_ocr
    import viber_ui

    viber_ui.jump_to_latest()
    for _ in range(tries):
        n = find_posted_message(viber_ui.list_messages()["messages"], text)
        if n is not None:
            return viber_ui.reply_to_message(n, reply_text)
        viber_ocr.scroll_and_read("chat", "up", 10)
    raise DriveError("Couldn't find the posted listing on screen to quote-reply to.")


def post_new_listing(text: str, category: str, deal: str, folder_name: str,
                     photo_paths: list[str], subfolder: str = "", dry_run: bool = False) -> dict:
    """Post a NEW listing in the listings GC the way staff do (full text incl. the internal part
    below the dashed line, then the photos, then a dashed separator) and return the Drive upload
    plan. Uploading is done with the Drive connector (this server's Google identity is a
    read-only service account with no storage); then confirm_listing_in_drive posts the
    'Added to Drive' reply. Everything is checked before anything is sent."""
    from datetime import date

    import viber_send
    import viber_ui

    plan = _check_new_listing(text, category, deal, folder_name, photo_paths, subfolder)
    today = date.today()
    upload = drive_upload_plan(text, plan, f"{today:%B} {today.day}, {today.year}")
    if dry_run:
        return {"posted": False, "gc": LISTINGS_GC, "drive_upload": upload,
                "added_to_drive_reply": added_to_drive_reply(plan["folder_name"]),
                "note": "Nothing sent. Call again without dry_run."}

    _open_listings_gc()
    viber_ui.jump_to_latest()
    if find_posted_message(viber_ui.list_messages()["messages"], text) is not None:
        raise DriveError("This listing is already the latest post in the GC. Nothing sent.")
    done: dict = {"gc": LISTINGS_GC, "posted": False}
    try:
        viber_send.reply(text.strip())
        # reply's own 'verified' gives false negatives on long texts; look for the post itself.
        if find_posted_message(viber_ui.list_messages()["messages"], text) is None:
            raise DriveError("the listing text isn't showing in the GC after sending — check it "
                             "before posting the photos")
        done["text_posted"] = True
        if plan["photos"]:
            done["photos_verified"] = viber_ui.send_files(plan["photos"]).get("verified")
        viber_send.reply(GC_SEPARATOR)
        done["posted"] = True
    except Exception as e:
        done["STOP"] = (f"Stopped part-way: {e}. Everything listed above here is done — finish the "
                        "rest by hand, don't re-run (it would post the listing twice).")
        return done
    done["drive_upload"] = upload
    done["next"] = ("Create the folder, the doc and the photos in drive_upload with the Drive "
                    "connector, then call confirm_listing_in_drive(folder_name, photo count).")
    return done


def confirm_listing_in_drive(folder_name: str, photos: int) -> dict:
    """Check a newly posted listing is really in Drive (its folder, the '*' doc and at least
    `photos` photos), then quote-reply 'Added to Drive [File name:*<folder>]' to its GC post."""
    global _tree_cache
    _tree_cache = None  # the folder was just created
    name = folder_name.strip()
    hits = [p for p in _property_folders() if p["name"].strip().casefold() == name.casefold()]
    if len(hits) != 1:
        raise DriveError(f"Expected one Drive folder named '{name}', found {len(hits)}. Nothing posted.")
    files = _children(hits[0]["folder_id"])
    docs = [f for f in files if f["mimeType"] in (GDOC, DOCX) and f["name"].strip() == f"*{name}"]
    if not docs:
        raise DriveError(f"The folder has no listing doc '*{name}' yet. Nothing posted.")
    images = [f for f in files if f["mimeType"].startswith("image/")]
    if len(images) < photos:
        raise DriveError(f"The folder has {len(images)} photo(s), expected {photos}. Nothing posted.")
    text = _doc_text(docs[0])
    _open_listings_gc()
    reply_text = added_to_drive_reply(name)
    _quote_reply_posted(text, reply_text)
    return {"confirmed": True, "folder_id": hits[0]["folder_id"], "photos": len(images),
            "added_to_drive_reply": reply_text}


def remove_downloads(paths: list[str], cache_dir: Path | None = None) -> int:
    """Delete downloaded files — only ones inside the download cache folder. Returns the count."""
    root = (cache_dir or CACHE_DIR).resolve()
    removed = 0
    for p in paths:
        path = Path(p).resolve()
        if path.parent != root or not path.is_file():
            continue  # never touch anything outside the cache
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass  # still open in Viber's upload; clear_downloads gets it later
    return removed


def clear_downloads(cache_dir: Path | None = None) -> dict:
    """Empty the download cache folder (files only; nothing outside it)."""
    root = (cache_dir or CACHE_DIR)
    files = [str(f) for f in root.iterdir() if f.is_file()] if root.exists() else []
    removed = remove_downloads(files, root)
    return {"removed": removed, "left": len(files) - removed, "folder": str(root)}


def prune_downloads(cache_dir: Path | None = None, max_age_days: float | None = None) -> dict:
    """Delete only cache files older than max_age_days (default KEEP_DOWNLOADS_DAYS)."""
    root = (cache_dir or CACHE_DIR)
    days = KEEP_DOWNLOADS_DAYS if max_age_days is None else max_age_days
    cutoff = time.time() - days * 86400
    files = [f for f in root.iterdir() if f.is_file()] if root.exists() else []
    old = [str(f) for f in files if f.stat().st_mtime < cutoff]
    removed = remove_downloads(old, root)
    return {"removed": removed, "kept": len(files) - removed, "older_than_days": days,
            "folder": str(root)}
