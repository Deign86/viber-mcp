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
import threading
import time
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
# Uploads (new listing folders, docs, photos) use the company account through the OAuth client,
# never the service account (it has 0 bytes of storage). Full "drive" is needed: drive.file can't
# put files into the existing APR LISTING folders.
WRITE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_HERE = Path(__file__).resolve().parent
_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "viber-mcp"
CLIENT_FILE = Path(os.environ.get("VIBER_MCP_GOOGLE_CLIENT", _HERE / "google_oauth_client.json"))
TOKEN_FILE = _DATA / "google_token.json"
WRITE_TOKEN_FILE = _DATA / "google_token_write.json"
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

_local = threading.local()  # one Drive service per thread: httplib2 isn't thread-safe
_tree_cache: tuple[float, list[dict]] | None = None


class DriveError(Exception):
    """Raised when Drive can't be reached or a listing can't be read safely."""


# ------------------------------------------------------------------------------ auth


def _creds(scopes: list[str] = SCOPES, token_file: Path = TOKEN_FILE,
           service_account_ok: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if service_account_ok and SERVICE_ACCOUNT_FILE.exists():
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
    """The read-only Drive service for the calling thread (downloads run in parallel threads)."""
    service = getattr(_local, "service", None)
    if service is None:
        from googleapiclient.discovery import build
        service = _local.service = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    return service


_writer_service = None


def _writer():
    """Drive service that can create files: the company Google account via the OAuth client
    (browser sign-in once; the token is kept in WRITE_TOKEN_FILE)."""
    global _writer_service
    if _writer_service is None:
        if not CLIENT_FILE.exists() and not WRITE_TOKEN_FILE.exists():
            raise DriveError(f"Drive uploads aren't set up: the OAuth client file {CLIENT_FILE} is missing.")
        from googleapiclient.discovery import build
        creds = _creds(WRITE_SCOPES, WRITE_TOKEN_FILE, service_account_ok=False)
        _writer_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _writer_service


def can_upload() -> bool:
    return CLIENT_FILE.exists() or WRITE_TOKEN_FILE.exists()


def sign_in(write: bool = False) -> dict:
    """Sign in to Google Drive (opens a browser the first time) and report the account.
    write=True signs in the upload account (the company Google account, full Drive access)."""
    svc = _writer() if write else _drive()
    about = svc.about().get(fields="user(displayName,emailAddress)").execute(num_retries=RETRIES)
    return {"signed_in": True, "write": write, "account": about["user"]}


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


DOWNLOAD_THREADS = 6


def _cached_download(fid: str) -> Path | None:
    """The finished cache file for `fid` (named "<id>_<name>"), if any. Downloads land in a
    ".part" file first and are renamed when complete, so a file under its final name is whole."""
    return next((p for p in CACHE_DIR.glob(f"{fid}_*") if p.suffix != ".part"), None)


def _fetch(fid: str) -> str:
    from googleapiclient.http import MediaIoBaseDownload

    cached = _cached_download(fid)
    if cached is not None:
        return str(cached)  # no metadata round trip for a photo we already have
    meta = _drive().files().get(fileId=fid, fields="id,name,size,mimeType",
                                supportsAllDrives=True).execute(num_retries=RETRIES)
    if meta["mimeType"].startswith("application/vnd.google-apps"):
        raise DriveError(f"'{meta['name']}' is a Google Docs file, not a downloadable file.")
    safe = re.sub(r'[<>:"/\\|?*]', "_", meta["name"])
    path = CACHE_DIR / f"{fid}_{safe}"
    buf = io.BytesIO()
    req = _drive().files().get_media(fileId=fid, supportsAllDrives=True)
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk(num_retries=RETRIES)
    part = path.with_name(path.name + ".part")
    part.write_bytes(buf.getvalue())
    part.replace(path)
    return str(path)


def download(file_ids: list[str]) -> list[str]:
    """Download Drive files to the local cache in parallel; returns their paths in order (cached
    files are reused). A listing's 20 photos used to be 40 sequential round trips."""
    from concurrent.futures import ThreadPoolExecutor

    if not file_ids:
        return []
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=min(DOWNLOAD_THREADS, len(file_ids))) as pool:
        return list(pool.map(_fetch, file_ids))


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
                             "deal": f"{deal['name']} / {prop['name']}", "modified": p["modifiedTime"],
                             "parent_id": prop["id"]}
                            for p in kids[prop["id"]]]
                    continue
                out.append({"folder_id": prop["id"], "name": prop["name"],
                            "category": cat["name"], "deal": deal["name"],
                            "modified": prop["modifiedTime"], "parent_id": deal["id"]})
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


def _plain_export(data: bytes | str) -> str:
    """A Google Doc's text/plain export as the doc reads: Drive writes \\r\\n and two line
    breaks per empty paragraph, so one blank line came back as two and update_listing_doc
    doubled every gap on a read-then-write (n breaks → n//2 + 1)."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    text = text.replace("\r\n", "\n")
    return re.sub(r"\n{3,}", lambda m: "\n" * (len(m.group()) // 2 + 1), text)


def _doc_text(f: dict) -> str:
    if f["mimeType"] == GDOC:
        return _plain_export(_drive().files().export(fileId=f["id"], mimeType="text/plain")
                             .execute(num_retries=RETRIES))
    if f["mimeType"] == DOCX:
        import docx

        path = download([f["id"]])[0]
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    raise DriveError(f"Unsupported listing document type: {f['mimeType']}")


# Without a dashed line, the client part ends with our company contact block…
_COMPANY_CONTACT = re.compile(r"realty@alphapremiergroup\.com|0927\s*555\s*5803|0915\s*888\s*9482|"
                              r"0921\s*217\s*4555", re.I)
# …or, failing that, right before the first line of the internal notes.
_INTERNAL_START = re.compile(
    r"^\W*(internal|\d+(\.\d+)?\s*(month|%).*commission|commission|exact address|owner|"
    r"contact (person|details|number)|intern\b|added to drive|map( link)?\s*:|location\s*:|"
    r"google pin|viber\s*:|whatsapp|facebook profile|call update|note[s]?\s*:)", re.I)
_PHONE = re.compile(r"(?:\+?63|0)\s*9\d{2}[\s-]*\d{3}[\s-]*\d{4}")


def _infer_split(lines: list[str]) -> int | None:
    """Index where the internal part starts in a doc with no dashed line, or None."""
    contact = [i for i, l in enumerate(lines) if _COMPANY_CONTACT.search(l)]
    if contact:
        end = contact[0]
        while end + 1 < len(lines) and (_COMPANY_CONTACT.search(lines[end + 1])
                                        or not lines[end + 1].strip()):
            end += 1
        return end + 1
    return next((i for i, l in enumerate(lines) if i and _INTERNAL_START.match(l)), None)


def split_listing(text: str, strict: bool = False) -> tuple[str, str]:
    """(client-facing part above the dashed separator, internal part below it).

    Docs without the dashed line are split by inference (strict=False): the client part ends
    with our company contact block (email / company numbers), or else right before the first
    internal-looking line (commission, owner, contact person, exact address, intern…). If the
    inferred client part still holds a non-company phone number, it refuses rather than risk
    sending an owner's number."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        if _SEPARATOR.match(line) and any(l.strip() for l in lines[:i]):
            head = [l for l in lines[:i] if not _SEPARATOR.match(l)]  # drop a stray leading rule
            return "\n".join(head).strip(), "\n".join(lines[i + 1:]).strip()
    cut = None if strict else _infer_split(lines)
    if cut is None:
        raise DriveError("No dashed separator line found in the listing doc, and the client-facing "
                         "part couldn't be told apart from the internal notes. Ask the staff member.")
    public = "\n".join(lines[:cut]).strip()
    leaked = [p for p in _PHONE.findall(public) if not _COMPANY_CONTACT.search(p)]
    if not public or leaked:
        raise DriveError("No dashed separator line in the listing doc, and the inferred client part "
                         "contains a non-company phone number (possibly the owner's). Ask the staff "
                         "member to add the dashed line; don't send anything from this doc.")
    return public, "\n".join(lines[cut:]).strip()


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


def send_listing(folder_id: str, max_photos: int = 20, include_videos: bool = True,
                 dry_run: bool = False) -> dict:
    """Send a listing to the OPEN Viber chat: its client-facing text, then its photos together,
    then its videos (a separate batch, so a big upload can't hold up the photos). Some listings
    only have a video. The internal part of the doc is never sent."""
    import viber_send
    import viber_ui

    listing = get_listing(folder_id)
    photos = listing["photos"][:max(0, max_photos)]
    videos = listing["videos"] if include_videos else []
    plan = {"doc": listing["doc"], "text": listing["client_text"],
            "files": [m["name"] for m in photos + videos]}
    if dry_run:
        return {"sent": False, "would_send": plan,
                "note": "Nothing sent. Check the chat and the text, then call again without dry_run."}
    # Download first so a Drive error sends nothing.
    photo_paths = download([m["id"] for m in photos])
    video_paths = download([m["id"] for m in videos])
    try:
        text_result = viber_send.reply(listing["client_text"])
        photos_result = viber_ui.send_files(photo_paths) if photo_paths else {}
        videos_result = viber_ui.send_files(video_paths) if video_paths else {}
    finally:
        prune_downloads()  # keep fresh files (Viber may still be uploading); drop old ones
    return {"sent": True, "doc": listing["doc"], "text_verified": text_result.get("verified"),
            "photos_sent": len(photo_paths), "photos_verified": photos_result.get("verified"),
            "videos_sent": len(video_paths), "videos_verified": videos_result.get("verified")}


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


def listing_key(name: str) -> str:
    """A folder name reduced for duplicate checks: staff write the same place as
    '*Quezon City, 36 Brgy. Vasra' and 'Quezon City, 36 Vasra', so drop the '*', barangay
    prefixes and punctuation."""
    words = re.sub(r"[^\w]+", " ", name.casefold()).split()
    return " ".join(w for w in words if w not in ("brgy", "bgy", "barangay"))


def _check_new_listing(text: str, category: str, deal: str, folder_name: str,
                       photo_paths: list[str], subfolder: str = "",
                       allow_existing: bool = False) -> dict:
    """Validate a new listing and resolve its Drive parent folder. Changes nothing.
    allow_existing: a folder of that name in the same parent is fine (it gets reused)."""
    split_listing(text, strict=True)  # new posts must have the dashed separator
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
             if listing_key(p["name"]) == listing_key(folder_name)]
    if allow_existing:
        clash = [p for p in clash if p.get("parent_id") != parent["id"]]
    if clash:
        raise DriveError(f"A listing folder named '{folder_name}' already exists "
                         f"({clash[0]['category']} / {clash[0]['deal']}) — it may already be posted. "
                         "Check before adding it again.")
    return {"parent_id": parent["id"], "path": path,
            "folder_name": folder_name, "photos": [str(p) for p in photos]}


def drive_upload_plan(text: str, plan: dict, added: str) -> dict:
    """What to create in Drive (with the Drive connector — this server never uploads): the folder,
    the listing doc and the photos, named the way staff name them. New listings get the '*'
    identifier on the folder as well as the doc."""
    return {
        "parent_folder_id": plan["parent_id"],
        "parent_path": plan["path"],
        "folder_name": f"*{plan['folder_name']}",
        "doc_title": f"*{plan['folder_name']}",
        "doc_text": listing_doc_text(text, added),
        "photos": [{"path": p, "name": f"AlphaPremier_Photo_{i:02d}{Path(p).suffix.lower() or '.jpg'}"}
                   for i, p in enumerate(plan["photos"], 1)],
    }


_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime"}


def _find_child(svc, parent_id: str, name: str, mime: str) -> dict | None:
    q = (f"'{parent_id}' in parents and name = '{_quote(name)}' and mimeType = '{mime}' "
         "and trashed = false")
    files = svc.files().list(q=q, fields="files(id,name)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute(num_retries=RETRIES).get("files", [])
    return files[0] if files else None


def upload_files(folder_id: str, paths: list[str], start: int = 1) -> list[dict]:
    """Upload photos/videos into a Drive folder as AlphaPremier_Photo_NN.<ext> (numbering from
    `start`). Returns [{id, name}]."""
    from googleapiclient.http import MediaFileUpload

    svc = _writer()
    out = []
    for i, p in enumerate(paths, start):
        path = Path(p).expanduser().resolve()
        if not path.is_file():
            raise DriveError(f"File not found: {path}")
        ext = path.suffix.lower() or ".jpg"
        mime = _IMAGE_TYPES.get(ext, "application/octet-stream")
        media = MediaFileUpload(str(path), mimetype=mime, resumable=path.stat().st_size > 5_000_000)
        f = svc.files().create(body={"name": f"AlphaPremier_Photo_{i:02d}{ext}", "parents": [folder_id]},
                               media_body=media, fields="id,name",
                               supportsAllDrives=True).execute(num_retries=RETRIES)
        out.append(f)
    return out


def upload_listing(upload: dict) -> dict:
    """Create a drive_upload_plan in Drive with the company account: the '*' folder (reused if
    it's already there), the '*' listing doc as a Google Doc (skipped if it exists), then the
    photos, numbered after any photos already in the folder."""
    from googleapiclient.http import MediaInMemoryUpload

    global _tree_cache
    svc = _writer()
    name = upload["folder_name"]
    folder = _find_child(svc, upload["parent_folder_id"], name, FOLDER)
    created_folder = folder is None
    if folder is None:
        folder = svc.files().create(body={"name": name, "mimeType": FOLDER,
                                          "parents": [upload["parent_folder_id"]]},
                                    fields="id,name", supportsAllDrives=True).execute(num_retries=RETRIES)
    doc = _find_child(svc, folder["id"], upload["doc_title"], GDOC)
    created_doc = doc is None
    if doc is None:
        media = MediaInMemoryUpload(upload["doc_text"].encode("utf-8"), mimetype="text/plain")
        doc = svc.files().create(body={"name": upload["doc_title"], "mimeType": GDOC,
                                       "parents": [folder["id"]]},
                                 media_body=media, fields="id,name",
                                 supportsAllDrives=True).execute(num_retries=RETRIES)
    existing = svc.files().list(
        q=f"'{folder['id']}' in parents and mimeType contains 'image/' and trashed = false",
        fields="files(id)", supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute(num_retries=RETRIES).get("files", [])
    photos = upload_files(folder["id"], [p["path"] for p in upload.get("photos", [])],
                          start=len(existing) + 1) if upload.get("photos") else []
    _tree_cache = None
    return {"folder_id": folder["id"], "folder_created": created_folder,
            "doc_id": doc["id"], "doc_created": created_doc, "photos_uploaded": len(photos),
            "folder_url": f"https://drive.google.com/drive/folders/{folder['id']}"}


def update_listing_doc(folder_id: str, text: str) -> dict:
    """Replace the whole text of a listing folder's '*' Google Doc (e.g. to reformat it). The
    doc keeps its id/link. Refuses text without the dashed separator."""
    from googleapiclient.http import MediaInMemoryUpload

    split_listing(text, strict=True)
    svc = _writer()
    docs = [f for f in _children(folder_id) if f["mimeType"] == GDOC]
    if len(docs) != 1:
        raise DriveError(f"Expected one Google Doc in the folder, found {len(docs)}.")
    media = MediaInMemoryUpload(text.strip().encode("utf-8"), mimetype="text/plain")
    svc.files().update(fileId=docs[0]["id"], media_body=media,
                       supportsAllDrives=True).execute(num_retries=RETRIES)
    return {"updated": True, "doc_id": docs[0]["id"], "doc": docs[0]["name"]}


SOLD_FOLDER_NAME = "SOLD"


def sold_target(category: str, sold_folders: list[dict]) -> dict | None:
    """The folder inside SOLD a listing of `category` goes to: "SOLD <CATEGORY>" (e.g. SOLD
    WAREHOUSE), or "House/Lot Sold" for condos/houses/lots. None = put it in SOLD itself (many
    older sold listings sit there directly)."""
    c = category.strip().casefold()
    by_name = {f["name"].strip().casefold(): f for f in sold_folders}
    if f"sold {c}" in by_name:
        return by_name[f"sold {c}"]
    if "house" in c or "lot" in c or "condo" in c:
        return next((f for n, f in by_name.items() if "house" in n and "sold" in n), None)
    return None


def move_listing_to_sold(folder_id: str) -> dict:
    """Move a listing folder that's no longer available (leased, sold, taken) from its FOR
    LEASE/SALE folder into APR LISTING / SOLD / SOLD <CATEGORY>. Same folder and link; only
    its location changes. Uses the company account (_writer)."""
    global _tree_cache
    # _property_folders also walks SOLD (as category "SOLD", deal "SOLD WAREHOUSE", ...), so a
    # folder already there must be refused, not "moved" up into SOLD itself.
    listing = next((p for p in _property_folders() if p["folder_id"] == folder_id
                    and p["category"].strip().upper() != SOLD_FOLDER_NAME), None)
    if listing is None:
        raise DriveError("That folder isn't an active listing (not under a category's FOR "
                         "LEASE/SALE folder) — it may already be in SOLD.")
    sold = next((f for f in _children(_listings_root())
                 if f["mimeType"] == FOLDER and f["name"].strip().upper() == SOLD_FOLDER_NAME), None)
    if sold is None:
        raise DriveError(f"Can't find the '{SOLD_FOLDER_NAME}' folder in {LISTINGS_ROOT_NAME}.")
    target = sold_target(listing["category"], [f for f in _children(sold["id"]) if f["mimeType"] == FOLDER]) or sold
    _writer().files().update(fileId=folder_id, addParents=target["id"], removeParents=listing["parent_id"],
                             fields="id,parents", supportsAllDrives=True).execute(num_retries=RETRIES)
    _tree_cache = None
    path = SOLD_FOLDER_NAME + ("" if target is sold else f" / {target['name']}")
    return {"moved": True, "listing": listing["name"], "from": f"{listing['category']} / {listing['deal']}",
            "to": f"{LISTINGS_ROOT_NAME} / {path}",
            "folder_url": f"https://drive.google.com/drive/folders/{folder_id}"}


def add_listing_to_drive(text: str, category: str, deal: str, folder_name: str,
                         photo_paths: list[str], subfolder: str = "",
                         dry_run: bool = False) -> dict:
    """Add a listing that is ALREADY posted (e.g. by staff) to Drive: folder + doc + photos.
    Does not post anything in Viber."""
    from datetime import date

    plan = _check_new_listing(text, category, deal, folder_name, photo_paths, subfolder,
                              allow_existing=True)
    today = date.today()
    upload = drive_upload_plan(text, plan, f"{today:%B} {today.day}, {today.year}")
    if dry_run:
        return {"uploaded": False, "drive_upload": upload, "note": "Nothing created."}
    result = upload_listing(upload)
    result["added_to_drive_reply"] = added_to_drive_reply(plan["folder_name"])
    return result


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
    below the dashed line, then the photos, then a dashed separator), then add it to Drive with
    the company account (upload_listing) and post the 'Added to Drive' reply. Without the OAuth
    client it returns the upload plan for the Drive connector instead (the read-only service
    account has no storage). Everything is checked before anything is sent."""
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
    if not can_upload():
        done["next"] = ("Drive uploads aren't set up on this PC: create the folder, the doc and the "
                        "photos in drive_upload with the Drive connector, then call "
                        "confirm_listing_in_drive(folder_name, photo count).")
        return done
    try:
        done["drive"] = upload_listing(upload)
        done["confirmed"] = confirm_listing_in_drive(plan["folder_name"], len(plan["photos"]))
    except Exception as e:
        done["STOP"] = (f"Posted in the GC, but adding it to Drive stopped: {e}. Re-run "
                        "add_listing_to_drive (it reuses what's there), then confirm_listing_in_drive.")
    return done


def confirm_listing_in_drive(folder_name: str, photos: int) -> dict:
    """Check a newly posted listing is really in Drive (its folder, the '*' doc and at least
    `photos` photos), then quote-reply 'Added to Drive [File name:*<folder>]' to its GC post."""
    global _tree_cache
    _tree_cache = None  # the folder was just created
    name = folder_name.strip().lstrip("*").strip()  # the folder itself is named "*<name>"
    hits = [p for p in _property_folders()
            if p["name"].strip().lstrip("*").strip().casefold() == name.casefold()]
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
