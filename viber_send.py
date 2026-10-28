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


# The chat pane's header: title, then the number / status line. 64 px below the pane's top keeps
# the band clear of the first message, which can start right under the header bar (72 px).
_HEADER_PX = 64
_HEADER_BOTTOM_DEFAULT = 0.135
_HEADER_X = (0.28, 0.7)


_HEADER_TOP_DEFAULT = 0.06


def _header_band() -> tuple[float, float]:
    """Normalized (top, bottom) of the open chat's header band, from the chat pane's UIA rect
    (the window's title bar height varies); window fractions where UIA isn't available."""
    try:
        pane = next((e for e in backend.elements() if e["class"] == "StackView"), None)
        height = backend.screenshot().height
    except (NotImplementedError, AttributeError, viber_ocr.OCRError):
        return _HEADER_TOP_DEFAULT, _HEADER_BOTTOM_DEFAULT
    if pane is None or not height:
        return _HEADER_TOP_DEFAULT, _HEADER_BOTTOM_DEFAULT
    return pane["rect"][1] / height, (pane["rect"][1] + _HEADER_PX) / height


def _header_bottom() -> float:
    """Normalized y under which the open chat's header lies."""
    return _header_band()[1]


_PARTICIPANTS_RE = re.compile(r"(\d+)\s+participants", re.I)


def current_chat() -> dict:
    """Which chat is open, read from the chat pane's header band ONLY (never the sidebar, the
    compose placeholder or the messages, which belong to whatever was open before): its title,
    the line under it (last seen / number / "N participants"), whether it is a group, and the
    number when one shows (an unsaved number is the title itself)."""
    top, bottom = _header_band()
    try:
        obs = backend.observe(region=_HEADER_REGION)
    except viber_ocr.OCRError as exc:
        raise SendError(f"Couldn't read the Viber window: {exc}") from exc
    # Icons right of the title OCR as junk ("00 0"); the title sits left of them, under the nav.
    texts = [o["text"].strip() for o in sorted(obs, key=lambda o: o["y"])
             if _HEADER_X[0] <= o["x"] < 0.6 and top <= o["y"] < bottom and len(o["text"].strip()) > 1]
    title = texts[0] if texts else ""
    subtitle = texts[1] if len(texts) > 1 else ""
    group = _PARTICIPANTS_RE.search(subtitle) or _PARTICIPANTS_RE.search(title)
    phone = next((t for t in texts[:2] if _PHONE_RE.match(t.strip()) and len(_digits(t)) >= 10), "")
    return {"open": bool(title), "title": title, "subtitle": subtitle, "is_group": group is not None,
            "participants": int(group.group(1)) if group else None,
            "number": ("+" + _digits(phone)) if phone else ""}


def require_open_chat(expect: str) -> dict:
    """Refuse (SendError) unless the open chat's header is `expect`: a chat/contact name (prefix
    tolerant, OCR look-alikes folded, so the name passed to search_and_open works) or a number.
    Empty `expect` checks nothing. Returns current_chat(). The guard for every tool that acts on
    \"the open chat\": the staff member may have switched chats, a viber:// link may not have
    navigated, or a new post may have pulled another chat to the front."""
    expect = expect.strip()
    if not expect:
        return {}
    info = current_chat()
    obs = backend.observe(region=_HEADER_REGION)
    if chat_confirmed(obs, expect, expect, _header_bottom()):
        return info
    shown = f"'{info['title']}'" + (f" ({info['subtitle']})" if info["subtitle"] else "") if info["open"] \
        else "no chat"
    raise SendError(f"The open chat is {shown}, not '{expect}'. Nothing was sent — open the right "
                    "chat first (search_and_open) and try again.")


def header_lines(obs: list[dict], header_bottom: float) -> list[str]:
    """OCR lines inside the chat pane's header band (not the sidebar, not the messages)."""
    return [o["text"] for o in obs if _HEADER_X[0] <= o["x"] < _HEADER_X[1] and o["y"] < header_bottom]


def header_key(lines: list[str]) -> frozenset:
    """What identifies a header across OCR reads: its longer words, folded (the short junk OCR
    makes of icons differs read to read)."""
    return frozenset(w for w in _ocr_fold(re.sub(r"[^a-z0-9 ]", "", " ".join(lines).lower())).split()
                     if len(w) >= 3)


def chat_confirmed(obs: list[dict], number: str, name: str | None, header_bottom: float) -> bool:
    """Strong evidence that the open chat is `number`/`name`'s: the number in the chat header
    (a saved contact's chat shows it under the name; an unsaved number IS the title), the
    "<Name> +63… is not in your contact list" banner, or the header title matching `name`
    (prefix-tolerant: group titles are truncated). Nothing in the sidebar or the messages counts
    — the contact's name in the chat list, the compose placeholder, or their number inside a
    message used to confirm the wrong chat."""
    want_digits = _digits(number or "")[-9:]
    want_name = _ocr_fold(re.sub(r"[^a-z0-9]", "", (name or "").lower()))
    for text in header_lines(obs, header_bottom):
        if len(want_digits) >= 7 and want_digits in _digits(text):
            return True
        got = _ocr_fold(re.sub(r"[^a-z0-9]", "", text.lower()))
        if len(want_name) >= 3 and len(got) >= 4 and (want_name.startswith(got) or got.startswith(want_name)):
            return True
    return number_confirmed(obs, number or "")


def _wait_for_sendable_chat(number: str, name: str | None, timeout: float = 12.0,
                            previous_header: frozenset | None = None,
                            header_bottom: float | None = None) -> str:
    """Poll the Viber window (via OCR) until `number`/`name`'s chat is confirmed open.

    Strong confirmation (`chat_confirmed`): the number or the name in the chat pane's header, or
    the number in the "not in your contact list" banner. Without one, the chat is accepted only
    after a settle period with no error screen AND a header different from `previous_header`
    (the chat open before navigating) — Viber's viber:// link sometimes doesn't navigate, and
    accepting the previous chat sent messages to the wrong person.
    Raises SendError if the number is not a Viber user or nothing is confirmed in time.
    Returns the OCR text of the confirmed chat.
    """
    deadline = time.time() + timeout
    started = time.time()
    header_bottom = _header_bottom() if header_bottom is None else header_bottom
    last, changed = "", previous_header is None
    while time.time() < deadline:
        try:
            obs = backend.observe()
        except viber_ocr.OCRError:
            time.sleep(0.4)
            continue
        last = "\n".join(o["text"] for o in obs).lower()
        # Not "not in your contact list": that banner shows on any unsaved-number chat.
        if "isn't on viber" in last:
            raise SendError(f"{number} is not a Viber user — cannot send a message.")
        if "cannot add yourself" in last:
            raise SendError("Cannot message your own number (Viber blocks self-chat via number).")
        if chat_confirmed(obs, number, name, header_bottom):
            return last
        changed = changed or header_key(header_lines(obs, header_bottom)) != previous_header
        if changed and time.time() - started >= 4.0:
            return last  # no error after the settle and not the chat we came from — accept
        time.sleep(0.5)
    if not changed:
        raise SendError(
            f"The open chat didn't change after asking Viber to open {number} (its viber:// link "
            "sometimes doesn't navigate). If that chat is already the open one, use reply; "
            "otherwise open it by name with search_and_open. Nothing was sent."
        )
    raise SendError(
        f"Timed out opening {number}'s chat. Viber may be slow/minimized, the number may be "
        "unreachable, or (for a name) the contact's chat didn't come to the front."
    )


_DIGIT_FOLD = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"})


def _digits(text: str) -> str:
    """The digits of OCR'd text, with the look-alikes OCR produces folded back (O→0, l→1)."""
    return "".join(ch for ch in text.translate(_DIGIT_FOLD) if ch.isdigit())


def number_confirmed(obs: list[dict], number: str) -> bool:
    """Is `number`'s 1:1 chat open? True when its last digits are in the chat's "<Name> +63…
    is not in your contact list" banner (every unsaved number's chat has one). Only that OCR
    line counts: the same number sits inside staff templates ("Viber: 0917…") and message text,
    and a previous chat showing it must never confirm. The chat header shows the number too,
    but its band overlaps the first message's, so it isn't used."""
    want = _digits(number)[-9:]
    if len(want) < 7:
        return False
    for o in obs:
        text = _ocr_fold(o["text"].lower())
        if _ocr_fold("contact list") in text and want in _digits(o["text"]):  # "contact Iist" too
            return True
    return False


def open_chat(to: str, confirm: bool = True) -> str:
    """Open (or focus) the Viber conversation for `to`. Returns the resolved E.164 number.

    With confirm=True, polls until a sendable chat is visible (and errors if the number
    is not a Viber user).
    """
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")
    number, name = resolve_recipient(to)
    # Remember the header of the chat open NOW: a chat is accepted without strong proof only
    # once the header has changed from this (viber:// sometimes doesn't navigate).
    header_bottom = _header_bottom()
    try:
        previous = header_key(header_lines(backend.observe(region=_HEADER_REGION), header_bottom))
    except viber_ocr.OCRError:
        previous = None
    backend.open_url(f"viber://chat?number={number}")
    if confirm:
        _wait_for_sendable_chat(number, name, previous_header=previous, header_bottom=header_bottom)
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
    """After a send, verify the message actually left the compose box (OCR).

    Returns True only if the text appears in the OCR AND the compose placeholder is back
    (i.e. the box is empty again → it was sent, not left sitting in the box). Returns None if
    OCR failed. This avoids the false positive where text pasted-but-not-sent still shows.
    """
    try:
        ocr = viber_ocr.read_open_chat()["text"]
    except viber_ocr.OCRError:
        return None
    return _text_landed(text, ocr) and ("type a message" in ocr.lower())


_ISOLATES = str.maketrans("", "", "⁨⁩")


def _norm_sent(text: str) -> str:
    """Message text as Viber's UI reports it back: no bidi isolate marks around numbers/links,
    no trailing spaces per line."""
    return "\n".join(line.strip() for line in text.translate(_ISOLATES).replace("\r\n", "\n").strip().split("\n"))


def own_message_texts(b=None) -> list[str] | None:
    """Texts of MY visible messages, exactly from the UI tree (newest last); None where the
    backend has no UI tree or no chat is open, so callers fall back to OCR. `b` = the backend
    to read (viber_ui passes its own; default: this module's)."""
    import viber_ui  # late: viber_ui imports this module
    try:
        return [m["text"] for m in viber_ui._messages((b or backend).elements())
                if m["mine"] and m["kind"] == "text"]
    except (NotImplementedError, AttributeError, viber_ui.UIError, viber_ocr.OCRError):
        return None


def sent_confirmed(text: str, before: list[str] | None, b=None):
    """Did `text` arrive as a NEW message of mine since `before` (own_message_texts() taken
    before the send)? Exact from the UI tree, so long texts and emoji don't give the false
    negatives OCR does; when the tree isn't available it falls back to the OCR check."""
    after = own_message_texts(b) if before is not None else None
    if after is None:
        return _verify_sent(text)
    want = _norm_sent(text)
    first = want.split("\n", 1)[0]

    def hits(texts):
        return sum(1 for t in texts
                   if (n := _norm_sent(t)) == want or ("\n" in want and n.split("\n", 1)[0] == first))
    if hits(after) > hits(before):
        return True
    return bool(_verify_sent(text))


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
    if not backend.panel_shifts_send:
        return  # the send button is found by identity there: skip the OCR read
    for _ in range(2):
        try:
            obs = backend.observe()
        except viber_ocr.OCRError:
            return
        if not _side_panel_open(obs):
            return
        backend.click_panel_close()
        time.sleep(0.6)


def _settle(seconds: float) -> None:
    """Fixed pause after fire-and-forget input (macOS). Skipped when the backend already
    waited for the field to change (Windows checks the box's value)."""
    if not backend.input_confirmed:
        time.sleep(seconds)


def _paste_into_compose(text: str) -> None:
    """Focus the compose box of the open chat, clear any draft, and paste `text` (no send).

    Assumes `text` is already on the clipboard.
    """
    _ensure_panel_closed()          # a panel would displace the send button
    backend.click_compose()
    _settle(0.4)
    backend.select_all()            # select any leftover draft
    _settle(0.1)
    backend.delete()                # clear it
    _settle(0.15)
    backend.paste()
    _settle(0.4)


def paste_into_open_chat(text: str, expect_chat: str = "") -> dict:
    """Paste `text` into the CURRENTLY OPEN chat's compose box — no navigation, no send.

    Use this to drop a drafted reply into whatever chat the user has open, so they can
    review and send it themselves. The clipboard is saved and restored. With `expect_chat`,
    refuses unless that chat's header is open.
    """
    if not text:
        raise SendError("Refusing to draft an empty message.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")
    require_open_chat(expect_chat)

    saved = backend.clipboard_get()
    try:
        backend.clipboard_set(text)
        backend.focus()
        _paste_into_compose(text)
    finally:
        if saved is not None:
            time.sleep(0.25)  # let the paste read the clipboard before restoring it
            backend.clipboard_set(saved)
    return {"drafted": True, "sent": False, "text": text}


def reply(text: str, expect_chat: str = "") -> dict:
    """Send `text` into the CURRENTLY OPEN chat — no navigation, no recipient resolution.

    Use after read_open_chat when the desired chat is already on screen. Clears any draft,
    pastes, clicks send, and verifies (a new message of mine in the UI tree, else OCR). The
    clipboard is saved and restored. With `expect_chat`, refuses unless that chat is open.
    """
    if not text:
        raise SendError("Refusing to send an empty message.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")
    info = require_open_chat(expect_chat)

    saved = backend.clipboard_get()
    verified = None
    try:
        backend.clipboard_set(text)
        backend.focus()
        before = own_message_texts()
        _paste_into_compose(text)
        backend.click_send()
        verified = _poll(lambda: sent_confirmed(text, before), timeout=2.5, first=0.3)  # was a fixed 1.3s
    finally:
        if saved is not None:
            time.sleep(0.25)  # let the paste read the clipboard before restoring it
            backend.clipboard_set(saved)
    out = {"sent": True, "text": text, "verified": verified}
    if info:
        out["chat"] = info["title"]
    return out


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
        return 6
    # A whole-word match beats a longer word that merely starts with the query, so "Max" opens
    # "Max Alpha", not "Maxine Cruz" listed above it.
    if t.startswith(q + " "):
        return 5
    if f" {q} " in f" {t} ":
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


def _poll(check, timeout: float, first: float = 0.0, interval: float = 0.1):
    """Call `check()` until it returns something truthy or `timeout` seconds pass; return the
    last result. Replaces fixed sleeps: fast cases finish early, slow ones still get the time."""
    time.sleep(first)
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result or time.monotonic() >= deadline:
            return result
        time.sleep(interval)


# Left-column section headers that only exist once Viber is showing search results.
_RESULT_HEADERS = {"conversations", "contacts", "groups", "businesses", "channels", "communities"}
# OCR regions (hints for the backend): the results/chat list column, and the chat header band.
_RESULTS_REGION = (0, 0, 0.36, 1)
_HEADER_REGION = (0, 0, 1, 0.16)


def _await_results(name: str, timeout: float = 2.4):
    """Read the search results until they are safe to click; return (observations, match).

    Replaces a fixed 1.6s wait plus one read. The first read after typing still shows the OLD
    chat list, with the same name at a different y — so a match counts only once a results
    section header is visible and two reads in a row agree on the row."""
    state = {"prev": None, "obs": [], "match": None, "headed": False, "reads": 0}

    def check():
        state["reads"] += 1
        obs = backend.observe(region=_RESULTS_REGION)
        match = _find_result_row(name, obs)
        header = _results_header_visible(obs) or _no_results(obs)
        if match is None:
            # Results are up but no row reads as the name (Viber draws the match in bold OCR
            # can't read): two such reads in a row hand over to the open-first-result fallback
            # instead of waiting out the timeout.
            ready = header and state["headed"]
        else:
            ready = state["prev"] is not None and _same_row(state["prev"], match) and header
        state.update(prev=match, obs=obs, match=match, headed=header)
        return ready

    ready = _poll(check, timeout=timeout, first=0.3)
    # Slow OCR (Vision on an Intel Mac: 1.5-4 s a read) fits only one read in the timeout, and
    # that one may still show the old chat list: always allow a few reads.
    while not ready and state["reads"] < 3:
        time.sleep(0.1)
        ready = check()
    return state["obs"], state["match"]


def _results_header_visible(obs) -> bool:
    return any(o["x"] < 0.35 and o["text"].strip().lower() in _RESULT_HEADERS for o in obs)


def _no_results(obs) -> bool:
    """Viber's "No results" notice in the results column: the search is done, nothing matched."""
    return any(o["x"] < 0.35 and "no result" in o["text"].lower() for o in obs)


def _same_row(a, b) -> bool:
    return a[0] == b[0] and abs(a[1] - b[1]) < 0.01


def _compose_text(b) -> str | None:
    """The open chat's message box text, or None when the backend has no UI tree to read."""
    try:
        els = b.elements()
    except (NotImplementedError, AttributeError, viber_ocr.OCRError):
        return None
    return next((e["text"] for e in els if e["type"] == "Edit"
                 and e["class"].startswith("QQuickTextEdit")), "")


def type_search_query(query: str, b=None, settle: bool = True) -> None:
    """Put `query` in Viber's search box (replacing what's there) and make sure none of it went
    into the open chat's message box. A click that misses the search box leaves the keyboard
    focus in the message box, and from there an Enter or the next send posts the query to a
    client (2026-10-28: "frn" reached "Hazel- Franchise Support"). Nothing here presses a key
    that could send; a message box that changed is put back as it was and SendError is raised.
    `b` is the backend (default: this module's); `settle` adds the fixed pauses macOS needed."""
    b = backend if b is None else b
    pause = settle and not getattr(b, "input_confirmed", False)
    before = _compose_text(b)
    b.click_search()
    if pause:
        time.sleep(0.4)
    b.select_all()            # clear any previous query
    if pause:
        time.sleep(0.1)
    b.delete()
    if pause:
        time.sleep(0.2)
    b.type_text(query)
    after = _compose_text(b)
    if before is None or after is None or after == before:
        return
    b.click_compose()
    b.select_all()
    b.delete()
    if before:
        b.type_text(before)
    raise SendError(
        "The search text went into the open chat's message box instead of Viber's search box; "
        "it was removed and nothing was sent. Try again (current_chat shows which chat is open).")


def _search_open(name: str) -> tuple[str, int]:
    """Type `name` in the search box and click the best-matching result row.

    Returns (matched_row_text, score). Raises SendError if no result matches.
    """
    type_search_query(name)

    obs, match = _await_results(name)
    if match is None:
        # Viber renders the matching result's name in highlighted bold that OCR often can't
        # read. Open the first result under a section header and confirm by the chat header.
        header = next((o for o in obs if o["x"] < 0.35 and o["text"].strip().lower()
                       in ("conversations", "contacts", "groups")), None)
        if header is not None:
            backend.click_result(header["y"] + 0.05)
            if _poll(lambda: _header_matches(name), timeout=2.0, first=0.3):  # was a fixed 1.2 s
                return name, 2
        raise SendError(
            f"No matching contact/chat found for '{name}' in Viber search. Try a fuller name "
            "or use a phone number with open_chat."
        )
    matched_text, norm_y, score = match
    backend.click_result(norm_y)
    _poll(lambda: _header_matches(matched_text), timeout=2.0)  # usually ~0.5s; was a fixed 1.2s
    return matched_text, score


# The row time/day stamp parser lives with the chat-list reader (shared by both row readers).
_ROW_TIME = viber_ocr._ROW_TIME
_row_stamp = viber_ocr.row_stamp


def message_search_rows(obs: list[dict]) -> list[dict]:
    """Group the sidebar's OCR lines (message-search results) into rows: chat name + time on the
    row's first line, then the matching message preview. Pure, so it's tested with fixtures."""
    left = sorted((o for o in viber_ocr.split_name_stamps(obs, 0.18, 0.24)
                   if o["x"] < 0.30 and o["y"] > 0.13), key=lambda o: o["y"])
    stamps = [o for o in left if o["x"] > 0.18 and _row_stamp(o["text"])]
    rows = []
    for i, stamp in enumerate(stamps):
        end = stamps[i + 1]["y"] - 0.012 if i + 1 < len(stamps) else 1.0
        name = next((o["text"].strip() for o in left
                     if abs(o["y"] - stamp["y"]) < 0.012 and o["x"] < stamp["x"]), "")
        preview = [o["text"].strip() for o in left
                   if stamp["y"] + 0.012 <= o["y"] < end and o["x"] < 0.27]
        rows.append({"chat": name, "time": _row_stamp(stamp["text"]),
                     "preview": " ".join(preview)})
    return rows


def search_messages(query: str, pages: int = 2) -> dict:
    """Search the TEXT of messages in all chats (Viber's 2nd search tab, under the search box —
    the 1st tab only matches chat/contact names). Returns rows (chat, time, preview), newest
    first. Switches back to the name tab afterwards so search_and_open keeps working."""
    query = query.strip()
    if not query:
        raise SendError("Search query is empty.")
    if not viber_is_running():
        raise SendError("Viber is not running. Launch and sign in to Viber first.")
    type_search_query(query)

    # Result-type tabs under the search box, left to right: names, messages, businesses.
    def tabs_now():
        return sorted((e for e in backend.elements() if e["class"] == "TabComponent"),
                      key=lambda e: e["rect"][0])
    tabs = _poll(lambda: tabs if len(tabs := tabs_now()) >= 2 else [], timeout=2.5, first=0.3)
    if len(tabs) < 2:
        raise SendError("Viber's search tabs didn't appear (is the search box open?).")
    mid = lambda e: ((e["rect"][0] + e["rect"][2]) / 2, (e["rect"][1] + e["rect"][3]) / 2)  # noqa: E731
    rows, seen = [], set()
    try:
        backend.click_at(*mid(tabs[1]))
        # The result rows take a moment to render (an empty list is also an answer).
        _poll(lambda: message_search_rows(backend.observe(region=_RESULTS_REGION)),
              timeout=2.0, first=0.4)
        backend.scroll("chats", 50)  # the list keeps its old scroll position — start at the top
        time.sleep(0.5)
        for page in range(max(1, pages)):
            for r in message_search_rows(backend.observe(region=_RESULTS_REGION)):
                key = (r["chat"], r["time"], r["preview"])
                if key not in seen:
                    seen.add(key)
                    rows.append(r)
            if page + 1 < pages:
                backend.scroll("chats", -8)
                time.sleep(0.8)
    finally:
        backend.click_at(*mid(tabs[0]))  # back to name search
    return {"query": query, "count": len(rows), "results": rows,
            "note": "OCR of the result list: chat names/previews may be truncated or garbled. "
                    "Open a chat with search_and_open to read the full message."}


def _header_matches(name: str) -> bool:
    """Is the open chat's header title `name` (allowing truncation and OCR look-alikes)?"""
    want = _ocr_fold(re.sub(r"[^a-z0-9]", "", name.lower()))
    for o in backend.observe(region=_HEADER_REGION):
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
    # The chat open now: without the name in the header, the result is accepted only once the
    # header has changed from this (a result click can miss and leave this chat open — on
    # 2026-10-28 "Sir Mark/Sean" stayed open and was reported as "Hazel- Franchise").
    header_bottom = _header_bottom()
    try:
        previous = header_key(header_lines(backend.observe(region=_HEADER_REGION), header_bottom))
    except viber_ocr.OCRError:
        previous = None
    matched_text, score = _search_open(name)
    try:
        result = _wait_for_sendable_chat(number=name, name=name, timeout=8.0,
                                         previous_header=previous, header_bottom=header_bottom)
    except SendError as exc:
        raise SendError(
            f"Clicked the search result '{matched_text}', but its chat didn't open (the header "
            "doesn't show it). Nothing was sent; check current_chat and search again.") from exc
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
    matched = _open_my_notes_checked()
    return {"opened": "My Notes", "matched": matched}


def _open_my_notes_checked() -> str:
    """Search-open My Notes and confirm it from the chat HEADER (a matching search row isn't
    proof: a click on a busy screen — a big group still loading — can leave the old chat open).
    One retry, then SendError."""
    for attempt in range(2):
        matched, _ = _search_open("My Notes")
        if "notes" not in matched.lower():
            raise SendError(
                f"Could not confirm My Notes opened (search matched '{matched}'); aborting to "
                "avoid saving to the wrong chat.")
        try:
            require_open_chat("My Notes")
            return matched
        except SendError:
            if attempt:
                raise


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
        _open_my_notes_checked()
        before = own_message_texts()
        _paste_into_compose(text)     # also closes the info panel if open
        backend.click_send()
        verified = _poll(lambda: sent_confirmed(text, before), timeout=2.5, first=0.3)  # was a fixed 1.3s
    finally:
        if saved is not None:
            time.sleep(0.25)  # let the paste read the clipboard before restoring it
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
        before = own_message_texts()
        _paste_into_compose(text)

        if dry_run:
            return {"recipient": number, "text": text, "sent": False, "dry_run": True}

        backend.click_send()
        verified = _poll(lambda: sent_confirmed(text, before), timeout=2.5, first=0.3)  # was a fixed 1.3s
    finally:
        if saved is not None:
            time.sleep(0.25)  # let the paste read the clipboard before restoring it
            backend.clipboard_set(saved)

    return {
        "recipient": number,
        "text": text,
        "sent": True,
        "dry_run": False,
        "verified": verified,  # True = confirmed on screen; False = not seen; None = couldn't check
    }
