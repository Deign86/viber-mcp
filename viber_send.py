"""Send Viber messages and drive chat navigation — platform-neutral domain logic.

All OS- and Viber-layout-specific work (window capture/OCR, synthetic input, clipboard, app
focus, UI-element locations) is delegated to the platform `Backend` (see `backends/`). This
module holds only the portable logic: recipient resolution, the send pipeline, search-result
scoring, panel detection, and send verification.

Send pipeline: resolve recipient → open the `viber://` chat → poll (via OCR) until a sendable
chat is confirmed → click compose, clear any draft, paste (clipboard, restored after) → click
send → OCR-verify the box cleared. We click send rather than pressing Return because
"Send message with Enter" may be off.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import viber_ocr
from backends import get_backend

backend = get_backend()

CONTACTS_PATH = Path(__file__).with_name("contacts.json")

# E.164: leading +, then 8–15 digits. We also accept bare digit strings and normalize them.
_E164_RE = re.compile(r"^\+\d{8,15}$")
_DIGITS_RE = re.compile(r"^\d{8,15}$")


class SendError(Exception):
    """Raised when a message cannot be sent (recipient unresolved, Viber not running, etc.)."""


# --------------------------------------------------------------------------- recipients


def viber_is_running() -> bool:
    """True if the Viber desktop app process is alive."""
    return backend.app_running()


def load_contacts() -> dict[str, str]:
    """Return the name->E.164 alias map, or {} if the file is absent/empty."""
    if not CONTACTS_PATH.exists():
        return {}
    try:
        data = json.loads(CONTACTS_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise SendError(f"contacts.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SendError("contacts.json must be a JSON object of {name: number}")
    return {str(k): str(v) for k, v in data.items()}


def _normalize_number(num: str) -> str:
    num = num.strip()
    if _E164_RE.match(num):
        return num
    if _DIGITS_RE.match(num):
        return "+" + num
    raise SendError(f"Invalid phone number: {num!r} (expected E.164 like +639171234567)")


def resolve_recipient(to: str) -> tuple[str, str | None]:
    """Resolve `to` to (E.164 number, contact_name_or_None).

    Accepts an E.164 string, a bare digit string, or a contact name in contacts.json.
    Never guesses: an unknown name raises SendError so we never message the wrong person.
    """
    to = to.strip()
    if _E164_RE.match(to):
        return to, None
    if _DIGITS_RE.match(to):
        return "+" + to, None

    contacts = load_contacts()
    if to in contacts:
        return _normalize_number(contacts[to]), to
    lowered = {k.lower(): (k, v) for k, v in contacts.items()}
    if to.lower() in lowered:
        name, num = lowered[to.lower()]
        return _normalize_number(num), name

    known = ", ".join(sorted(contacts)) or "(none)"
    raise SendError(
        f"Could not resolve recipient '{to}'. Provide an E.164 number (e.g. +639171234567) "
        f"or a name present in contacts.json. Known names: {known}"
    )


# ----------------------------------------------------------------------------- open / send


def _wait_for_sendable_chat(number: str, name: str | None, timeout: float = 12.0) -> str:
    """Poll the Viber window (via OCR) until a sendable chat is visible.

    Success signals (any one, and no error screen):
      - the contact `name` (first token) is visible in the header/list, OR
      - the compose placeholder "type a message" is visible (empty draft), OR
      - a settle period passed with no error screen (number-addressed, saved contact whose
        header shows a name we don't know).
    Raises SendError if the number is not a Viber user or nothing sendable appears in time.
    Returns the OCR text of the confirmed chat.
    """
    deadline = time.time() + timeout
    name_token = (name or "").strip().split()[0].lower() if name else ""
    started = time.time()
    last = ""
    while time.time() < deadline:
        try:
            last = viber_ocr.read_open_chat()["text"].lower()
        except viber_ocr.OCRError:
            time.sleep(0.6)
            continue
        # Not "not in your contact list": that banner shows on any unsaved-number chat.
        if "isn't on viber" in last:
            raise SendError(f"{number} is not a Viber user — cannot send a message.")
        if "cannot add yourself" in last:
            raise SendError("Cannot message your own number (Viber blocks self-chat via number).")
        if name_token and name_token in last:
            return last
        if "type a message" in last:
            return last
        if not name_token and time.time() - started >= 4.0:
            return last  # number-addressed, no error after settle — accept
        time.sleep(0.8)
    raise SendError(
        f"Timed out opening {number}'s chat. Viber may be slow/minimized, the number may be "
        "unreachable, or (for a name) the contact's chat didn't come to the front."
    )


def open_chat(to: str, confirm: bool = True) -> str:
    """Open (or focus) the Viber conversation for `to`. Returns the resolved E.164 number.

    With confirm=True, polls until a sendable chat is visible (and errors if the number
    is not a Viber user).
    """
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")
    number, name = resolve_recipient(to)
    backend.open_url(f"viber://chat?number={number}")
    if confirm:
        _wait_for_sendable_chat(number, name)
    else:
        time.sleep(5.0)  # desktop needs a few seconds to navigate
    return number


def _text_landed(text: str, ocr_text: str) -> bool:
    """Best-effort check that `text` appears in the OCR of the conversation.

    Compares an alphanumeric-only, lowercased signature so spacing/emoji differences and
    OCR noise don't cause false negatives. Returns True if we can't build a signature.
    """
    sig = re.sub(r"[^a-z0-9]", "", text.lower())[:24]
    if len(sig) < 6:
        return True  # too little ASCII to verify (e.g. emoji-only) — don't claim failure
    haystack = re.sub(r"[^a-z0-9]", "", ocr_text.lower())
    return sig in haystack


def _verify_sent(text: str):
    """After a send, verify the message actually left the compose box.

    Returns True only if the text appears in the OCR AND the compose placeholder is back
    (i.e. the box is empty again → it was sent, not left sitting in the box). Returns None if
    OCR failed. This avoids the false positive where text pasted-but-not-sent still shows.
    """
    try:
        ocr = viber_ocr.read_open_chat()["text"]
    except viber_ocr.OCRError:
        return None
    return _text_landed(text, ocr) and ("type a message" in ocr.lower())


# A right-side panel (chat-info OR the sticker "Settings" panel) occupies the right edge and
# shifts the send button, so it must be closed before a send. These titles/rows appear ONLY
# when such a panel is open.
_PANEL_MARKERS = {
    "clear chat", "set background", "hide completed notes", "delete chat",
    "settings", "add to groups", "participants",
}


def _side_panel_open(observations: list[dict]) -> bool:
    return any(
        o["x"] > 0.80 and o["text"].strip().lower() in _PANEL_MARKERS for o in observations
    )


def _ensure_panel_closed() -> None:
    """Close any right-side panel if open (it displaces the send button). Retries once."""
    for _ in range(2):
        try:
            obs = backend.observe()
        except viber_ocr.OCRError:
            return
        if not _side_panel_open(obs):
            return
        backend.click_panel_close()
        time.sleep(0.6)


def _paste_into_compose(text: str) -> None:
    """Focus the compose box of the open chat, clear any draft, and paste `text` (no send).

    Assumes `text` is already on the clipboard.
    """
    _ensure_panel_closed()          # a panel would displace the send button
    backend.click_compose()
    time.sleep(0.4)
    backend.select_all()            # select any leftover draft
    time.sleep(0.1)
    backend.delete()                # clear it
    time.sleep(0.15)
    backend.paste()
    time.sleep(0.4)


def paste_into_open_chat(text: str) -> dict:
    """Paste `text` into the CURRENTLY OPEN chat's compose box — no navigation, no send.

    Use this to drop a drafted reply into whatever chat the user has open, so they can
    review and send it themselves. The clipboard is saved and restored.
    """
    if not text:
        raise SendError("Refusing to draft an empty message.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")

    saved = backend.clipboard_get()
    try:
        backend.clipboard_set(text)
        backend.focus()
        _paste_into_compose(text)
    finally:
        time.sleep(0.25)
        if saved is not None:
            backend.clipboard_set(saved)
    return {"drafted": True, "sent": False, "text": text}


def reply(text: str) -> dict:
    """Send `text` into the CURRENTLY OPEN chat — no navigation, no recipient resolution.

    Use after read_open_chat when the desired chat is already on screen. Clears any draft,
    pastes, clicks send, and OCR-verifies. The clipboard is saved and restored.
    """
    if not text:
        raise SendError("Refusing to send an empty message.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")

    saved = backend.clipboard_get()
    verified = None
    try:
        backend.clipboard_set(text)
        backend.focus()
        _paste_into_compose(text)
        backend.click_send()
        time.sleep(1.3)  # let the sent bubble render before verifying
        verified = _verify_sent(text)
    finally:
        time.sleep(0.25)
        if saved is not None:
            backend.clipboard_set(saved)
    return {"sent": True, "text": text, "verified": verified}


# Search-result rows that are not contacts/chats and must never be opened.
_RESULT_SKIP = {
    "new group", "new community", "new channel", "new call link", "all contacts",
    "viber only", "contacts", "search...", "search", "chats", "unread", "favorites",
    "all", "recent", "messages", "people", "share a link to your viber call",
    # search-result section headers
    "conversations", "channels", "businesses", "bots", "cars", "communities",
}

# Observations above this normalized y are the search bar / top nav, not result rows.
_RESULTS_TOP_Y = 0.12
_PHONE_RE = re.compile(r"^[+\d][\d\s()\-]+$")
_COUNT_RE = re.compile(r"^\d+\s+contacts?$")


_OCR_FOLD = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"})


def _ocr_fold(s: str) -> str:
    return s.translate(_OCR_FOLD)


def _score_result(query: str, text: str) -> int:
    """Rank how well an OCR'd result row matches `query`. Higher = better; <=0 = reject."""
    t = text.lower().strip()
    if not t or t in _RESULT_SKIP or _PHONE_RE.match(t) or _COUNT_RE.match(t):
        return -1
    # OCR confuses I/l/1 and O/0 (e.g. "TESTCLlENT"); compare with those folded together.
    q, t = _ocr_fold(query.lower().strip()), _ocr_fold(t)
    if t == q:
        return 4
    if t.startswith(q):
        return 3
    if any(w.startswith(q) for w in t.split()):  # any word starts with the query
        return 3
    if q in t:                                    # substring only (weak, e.g. "ivy" in "Divya")
        return 1
    return -1


def _find_result_row(query: str, observations: list[dict]):
    """Pick the best-matching result row; return (text, norm_y, score) or None.

    Only considers the left column (the results list). Prefers exact/prefix matches over
    substrings so, e.g., searching "Ivy" opens "Ivy Cloma-Rosales", not "Divya".
    """
    candidates = []
    for o in observations:
        if o["x"] >= 0.35 or o["y"] < _RESULTS_TOP_Y:  # left column, below the search bar
            continue
        score = _score_result(query, o["text"])
        if score > 0:
            candidates.append((score, o["y"], o["text"]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], c[1]))  # best score, then topmost
    score, norm_y, text = candidates[0]
    return text, norm_y, score


def _search_open(name: str) -> tuple[str, int]:
    """Type `name` in the search box and click the best-matching result row.

    Returns (matched_row_text, score). Raises SendError if no result matches.
    """
    backend.click_search()
    time.sleep(0.4)
    backend.select_all()            # clear any previous query
    time.sleep(0.1)
    backend.delete()
    time.sleep(0.2)
    backend.type_text(name)
    time.sleep(1.6)                 # let results populate

    obs = backend.observe()
    match = _find_result_row(name, obs)
    if match is None:
        # Viber renders the matching result's name in highlighted bold that OCR often can't
        # read. Open the first result under a section header and confirm by the chat header.
        header = next((o for o in obs if o["x"] < 0.35 and o["text"].strip().lower()
                       in ("conversations", "contacts", "groups")), None)
        if header is not None:
            backend.click_result(header["y"] + 0.05)
            time.sleep(1.2)
            if _header_matches(name):
                return name, 2
        raise SendError(
            f"No matching contact/chat found for '{name}' in Viber search. Try a fuller name "
            "or use a phone number with open_chat."
        )
    matched_text, norm_y, score = match
    backend.click_result(norm_y)
    time.sleep(1.2)
    return matched_text, score


def _header_matches(name: str) -> bool:
    """Is the open chat's header title `name` (allowing truncation and OCR look-alikes)?"""
    want = _ocr_fold(re.sub(r"[^a-z0-9]", "", name.lower()))
    for o in backend.observe():
        if 0.3 <= o["x"] < 0.6 and o["y"] < 0.12:
            got = _ocr_fold(re.sub(r"[^a-z0-9]", "", o["text"].lower()))
            if len(got) >= 4 and (want.startswith(got) or got.startswith(want)):
                return True
    return False


def search_and_open(name: str) -> dict:
    """Search Viber for `name` and open the best-matching result.

    Works for any Viber contact/chat by name (not limited to contacts.json). Types into the
    search box, then picks the result row that best matches `name` (exact/prefix beats
    substring) by OCR-ing the results and clicking that row's actual position. Raises
    SendError if there's no confident match. Returns the opened chat's visible text plus the
    matched name and a `low_confidence` flag (true if only a weak substring match was found).
    """
    name = name.strip()
    if not name:
        raise SendError("Search query is empty.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")

    backend.focus()
    if _header_matches(name):  # already open
        return {"query": name, "matched": name, "low_confidence": False, "opened": True,
                "text": viber_ocr.read_open_chat()["text"]}
    matched_text, score = _search_open(name)
    result = _wait_for_sendable_chat(number=name, name=name, timeout=8.0)
    return {
        "query": name,
        "matched": matched_text,
        "low_confidence": score <= 1,  # only a substring match — verify before sending
        "opened": True,
        "text": result,
    }


def open_my_notes() -> dict:
    """Open Viber's private "My Notes" self-chat (found via search). Returns confirmation.

    Raises SendError if the opened chat doesn't look like My Notes.
    """
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")
    backend.focus()
    matched, score = _search_open("My Notes")
    if "notes" not in matched.lower():
        raise SendError(f"Could not confirm the My Notes chat opened (matched '{matched}').")
    return {"opened": "My Notes", "matched": matched}


def save_to_my_notes(text: str) -> dict:
    """Save `text` to Viber's private "My Notes" self-chat.

    Opens My Notes (via search), confirms the match is really My Notes, then pastes and
    sends — like sending a message where the recipient is your own notes. The clipboard is
    saved and restored.
    """
    if not text:
        raise SendError("Refusing to save an empty note.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")

    saved = backend.clipboard_get()
    verified = None
    try:
        backend.clipboard_set(text)
        backend.focus()
        matched, score = _search_open("My Notes")
        if "notes" not in matched.lower():
            raise SendError(
                f"Could not confirm My Notes opened (search matched '{matched}'); aborting to "
                "avoid saving to the wrong chat."
            )
        _paste_into_compose(text)     # also closes the info panel if open
        backend.click_send()
        time.sleep(1.3)
        verified = _verify_sent(text)
    finally:
        time.sleep(0.25)
        if saved is not None:
            backend.clipboard_set(saved)
    return {"saved_to": "My Notes", "text": text, "sent": True, "verified": verified}


def send_message(to: str, text: str, dry_run: bool = False) -> dict:
    """Send `text` to `to` via the Viber desktop app.

    Steps: resolve recipient -> open & confirm chat -> click compose -> paste ->
    click send button -> verify the message appears. The user's clipboard is saved and
    restored. With dry_run=True the text is pasted into the compose box but NOT sent.
    """
    if not text:
        raise SendError("Refusing to send an empty message.")

    number = open_chat(to, confirm=True)

    saved = backend.clipboard_get()
    verified = None
    try:
        backend.clipboard_set(text)
        backend.focus()
        _paste_into_compose(text)

        if dry_run:
            return {"recipient": number, "text": text, "sent": False, "dry_run": True}

        backend.click_send()
        time.sleep(1.3)  # let the sent bubble render before verifying
        verified = _verify_sent(text)
    finally:
        time.sleep(0.25)
        if saved is not None:
            backend.clipboard_set(saved)

    return {
        "recipient": number,
        "text": text,
        "sent": True,
        "dry_run": False,
        "verified": verified,  # True = confirmed on screen; False = not seen; None = couldn't check
    }
