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

FOLDER = "application/vnd.google-apps.folder"
GDOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SEPARATOR = re.compile(r"^\s*[-_—–=]{3,}\s*$")  # a lone "—" (signature line) is not one
# corpora=allDrives so Shared Drive items are searched too, not just "My Drive".
_ALL = {"supportsAllDrives": True, "includeItemsFromAllDrives": True, "corpora": "allDrives"}

_service = None
_tree_cache: tuple[float, list[dict]] | None = None


class DriveError(Exception):
    """Raised when Drive can't be reached or a listing can't be read safely."""


# ------------------------------------------------------------------------------ auth


def _creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if SERVICE_ACCOUNT_FILE.exists():
        # Read-only service account (created with gcloud); the listing folder is shared with it.
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_FILE), scopes=SCOPES)

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
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
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
        # Opens the browser once; the staff member signs in with their company Google account.
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _drive():
    global _service
    if _service is None:
        from googleapiclient.discovery import build
        _service = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    return _service


def sign_in() -> dict:
    """Sign in to Google Drive (opens a browser the first time) and report the account."""
    about = _drive().about().get(fields="user(displayName,emailAddress)").execute()
    return {"signed_in": True, "account": about["user"]}


# ----------------------------------------------------------------------- primitives


def _list(q: str, fields: str = "id,name,mimeType,modifiedTime,size,parents") -> list[dict]:
    out, token = [], None
    while True:
        resp = _drive().files().list(
            q=q, fields=f"nextPageToken,files({fields})", pageSize=1000, pageToken=token,
            orderBy="name", **_ALL,
        ).execute()
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
                                    supportsAllDrives=True).execute()
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
                _, done = dl.next_chunk()
            path.write_bytes(buf.getvalue())
        paths.append(str(path))
    return paths


# ------------------------------------------------------------------------ listings


def _listings_root() -> str:
    folders = _list(f"name = '{_quote(LISTINGS_ROOT_NAME)}' and mimeType = '{FOLDER}' and trashed = false")
    if not folders:
        raise DriveError(f"Can't find the '{LISTINGS_ROOT_NAME}' folder in your Drive.")
    return folders[0]["id"]


def _property_folders() -> list[dict]:
    """All property folders: root / category / deal-type / property. Cached for 5 minutes."""
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
        data = _drive().files().export(fileId=f["id"], mimeType="text/plain").execute()
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
