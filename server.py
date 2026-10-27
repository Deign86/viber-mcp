"""Viber MCP server — expose send/receive of Viber Desktop messages as MCP tools.

Transport: stdio. Register with Claude Code via `claude mcp add` (see README).
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

import viber_drive
import viber_gc
import viber_ocr
import viber_send
import viber_ui

# The staff operating rules ship with the server: MCP clients add them to Claude's context.
GUIDELINES = Path(__file__).with_name("guidelines.md").read_text(encoding="utf-8")

mcp = FastMCP("viber", instructions=GUIDELINES)


@mcp.tool()
def send_message(to: str, text: str, dry_run: bool = False) -> dict:
    """Send a Viber message.

    Args:
        to: recipient — an E.164 phone number (e.g. "+639171234567") or a contact name
            defined in contacts.json.
        text: the message body (emoji/unicode/newlines supported).
        dry_run: if true, open the chat and paste into the compose box but do NOT click
            send (nothing is sent). Useful for verifying the correct chat opened.

    Returns a dict with the resolved recipient, whether it was sent, and `verified`
    (true = the message was confirmed visible on screen after sending).
    """
    return viber_send.send_message(to=to, text=text, dry_run=dry_run)


@mcp.tool()
def open_chat(to: str) -> dict:
    """Open (foreground) a Viber conversation without sending anything.

    Args:
        to: an E.164 phone number or a contact name from contacts.json.
    """
    number = viber_send.open_chat(to)
    return {"opened": number}


@mcp.tool()
def open_and_read(to: str) -> dict:
    """Open a conversation and return its visible text in one step ("read my chat with X").

    Args:
        to: an E.164 phone number or a contact name from contacts.json.
    """
    number = viber_send.open_chat(to)
    result = viber_ocr.read_open_chat()
    result["opened"] = number
    return result


@mcp.tool()
def reply(text: str, expect_chat: str = "") -> dict:
    """Send `text` into the CURRENTLY OPEN chat (no navigation).

    Use after read_open_chat when the right chat is already on screen — avoids re-resolving
    or re-opening the recipient. Returns whether it sent and `verified` (the message seen as a
    new one of yours in the chat).

    Args:
        text: the message body (emoji/unicode/newlines supported).
        expect_chat: the chat this must go to (the name you opened, or a number). The send is
            refused if another chat is open — always pass it; the open chat can change under you.
    """
    return viber_send.reply(text, expect_chat=expect_chat)


@mcp.tool()
def current_chat() -> dict:
    """Which chat is open right now, from the chat pane's header only: title, the line under it
    (last seen / number / "N participants"), is_group, and the number if shown. Cheap — use it
    before acting on "the open chat" and after search_and_open / open_chat when in doubt (the
    sidebar, the message box placeholder and the messages can all belong to the previous chat)."""
    return viber_send.current_chat()


@mcp.tool()
def search_and_open(name: str) -> dict:
    """Search Viber for `name` and open the best-matching contact/chat; return its visible text.

    Works for any Viber contact by name (not just contacts.json). Types into the search box,
    OCRs the results, and opens the row that best matches `name` — exact/prefix matches beat
    substrings (so "Ivy" opens "Ivy Cloma-Rosales", not "Divya"). Returns `matched` (the row it
    opened) and `low_confidence` (true if only a weak substring match existed). If
    `low_confidence` is true the match is ambiguous: stop and ask which chat is meant.

    Args:
        name: the contact or chat name to search for.
    """
    return viber_send.search_and_open(name)


@mcp.tool()
def search_messages(query: str, pages: int = 2) -> dict:
    """Search the TEXT of messages across all chats (e.g. "LOI", "letter of intent", a street).

    Uses Viber's message-search tab (the 2nd icon under the search box — search_and_open only
    matches chat names). Returns rows {chat, time, preview}, newest first; times are like
    "1:57 PM", "Yesterday", "Tuesday", "10/8/2026". Previews are OCR and may be cut off — open
    the chat to read the whole message.

    Args:
        query: words to find in messages.
        pages: how many screens of results to read (scrolls the list between them).
    """
    return viber_send.search_messages(query, pages)


@mcp.tool()
def list_chats(unread_only: bool = False) -> dict:
    """List the chats in the sidebar (OCR): raw lines plus `chats` rows with name, time, preview,
    unread badge count and last_from ("me"/"them" — who sent the last message, best-effort from
    the "Me:" prefix / read tick; a client chat should never end on "them").

    Reads only the left-hand chat list of what is currently on screen; scroll_chat_list for
    more. unread_only=True switches the list to Viber's "Unread" filter for the read (and back
    to "All"), so it lists exactly the chats with unread messages.
    """
    return viber_ocr.read_chat_list(unread_only)


@mcp.tool()
def mark_as_read(name: str) -> dict:
    """Clear a chat's unread badge: right-clicks its sidebar row and picks "Mark as read" (opening
    a chat in the background doesn't clear the badge). The row must be visible in the chat list
    (scroll_chat_list). Already-read chats are left alone; nothing else in that menu is ever
    clicked.

    Args:
        name: the chat's name as the sidebar shows it (prefix of a long group name is fine).
    """
    return viber_ui.mark_as_read(name)


@mcp.tool()
def scroll_chat(direction: str = "up", amount: int = 5) -> dict:
    """Scroll the currently open conversation and return its visible text afterwards.

    Use "up" to load older messages (read_open_chat only sees what is on screen), "down" to
    go back toward the newest. Repeat to go further. Runs in the background on Windows.

    Args:
        direction: "up" (older messages) or "down" (newer).
        amount: mouse-wheel notches, 1-50 (about 3 lines each; 5 ≈ a third of a screen).
    """
    return viber_ocr.scroll_and_read("chat", direction, amount)


@mcp.tool()
def jump_to_latest() -> dict:
    """Jump the open chat to its newest messages (clicks Viber's "↓" button when the chat is
    scrolled up — wheel-scrolling alone can't get back from far up). Use before reading or
    replying when the newest messages aren't on screen."""
    return viber_ui.jump_to_latest()


@mcp.tool()
def scroll_chat_list(direction: str = "down", amount: int = 5) -> dict:
    """Scroll the sidebar chat list and return the chats visible afterwards.

    Use "down" to see older/more chats beyond what list_chats showed, "up" to return to the
    top. Repeat to go further. Runs in the background on Windows.

    Args:
        direction: "down" (more/older chats) or "up" (back toward the top).
        amount: mouse-wheel notches, 1-50 (about one chat row each).
    """
    return viber_ocr.scroll_and_read("chats", direction, amount)


@mcp.tool()
def put_draft(text: str, expect_chat: str = "") -> dict:
    """Paste `text` into the currently-open Viber chat's compose box WITHOUT sending.

    Does not navigate — it drafts into whatever chat is open right now, so the user can
    review, edit, and press send themselves. Pair with read_open_chat to draft a reply:
    read the visible conversation, compose a fitting reply, then put_draft it.

    Args:
        text: the drafted reply to place in the compose box.
        expect_chat: the chat this is for; refused if another chat is open.
    """
    return viber_send.paste_into_open_chat(text, expect_chat=expect_chat)


@mcp.tool()
def save_to_my_notes(text: str) -> dict:
    """Save a note to Viber's private "My Notes" self-chat.

    My Notes is a private conversation only you can see — good for saving reminders, links,
    or snippets. Opens My Notes (via search), confirms it, and sends `text` there. Returns
    whether it sent and `verified` (confirmed the box cleared after sending).

    Args:
        text: the note to save (emoji/unicode/newlines supported).
    """
    return viber_send.save_to_my_notes(text)


@mcp.tool()
def open_my_notes() -> dict:
    """Open Viber's private "My Notes" self-chat without saving anything."""
    return viber_send.open_my_notes()


@mcp.tool()
def read_open_chat() -> dict:
    """Read the currently-open Viber conversation via screen OCR.

    Captures the visible Viber window and runs Apple Vision OCR over it. Returns the
    recognized lines (top-to-bottom) and the joined text. Only sees what is on screen —
    scroll the chat in Viber to bring more history into view before calling.
    """
    return viber_ocr.read_open_chat()


@mcp.tool()
def list_contacts() -> dict:
    """List the known contact name -> phone number aliases from contacts.json."""
    return {"contacts": viber_send.load_contacts()}


# ---------------------------------------------------------------- message-level actions


@mcp.tool()
def list_messages() -> dict:
    """List the messages visible in the open chat, read exactly from Viber's UI (not OCR).

    Each has n (1 = newest visible), from ("me"/"them"), kind ("text"/"image") and text.
    Use the n values with unsend_message / open_image. Scroll with scroll_chat for older ones.
    """
    return viber_ui.list_messages()


@mcp.tool()
def backread_chat(screens: int = 40, until: str = "") -> dict:
    """Read the open chat's whole history, oldest first, exactly from Viber's UI (not OCR).

    Jumps to the newest message, scrolls up a screen at a time and stitches the screens
    together until the first message of the chat, then returns to the newest. Use it before
    offering a listing (was it already offered?) or replying to a long thread.

    Args:
        screens: max screens to scroll up (each ~10 wheel notches).
        until: optional text; stop once a message containing it has been read.
    """
    return viber_ui.backread(screens, until)


@mcp.tool()
def open_image(n: int = 1) -> list:
    """Open the n-th newest visible image in the open chat full-size and return what it shows.

    Use to read photos/screenshots people sent (listings, floor plans, receipts).
    Always call close_viewer afterwards.

    Args:
        n: 1 = newest visible image, 2 = the one before it, ...
    """
    png = viber_ui.open_image(n)
    return [Image(data=png, format="png"), "Image viewer is open — call close_viewer when done."]


@mcp.tool()
def send_files(paths: list[str], caption: str = "", dry_run: bool = False, expect_chat: str = "") -> dict:
    """Send local files (photos, PDFs, videos, docs) to the OPEN chat in one batch, with an
    optional caption.

    Several photos go out together as one album. With dry_run=True the files and caption are
    left in Viber's send preview for the staff member to check and send themselves.

    Args:
        paths: full paths of the files on this PC, in the order to send them.
        caption: optional text sent with the files.
        dry_run: True = stop at the preview, don't send.
        expect_chat: the chat this must go to; refused if another chat is open. Always pass it.
    """
    return viber_ui.send_files(paths, caption=caption, dry_run=dry_run, expect_chat=expect_chat)


@mcp.tool()
def reply_to_message(n: int, text: str, send: bool = True, expect_chat: str = "") -> dict:
    """Quote-reply to a specific message (n from list_messages) — the client sees which message
    you're answering. Sends by default; send=False leaves it as a draft. Returns `verified`
    (the reply seen as a new message of yours). expect_chat: the chat this must go to; refused
    if another chat is open — always pass it.
    """
    return viber_ui.reply_to_message(n, text, send, expect_chat=expect_chat)


@mcp.tool()
def rename_group(new_name: str, expect_current: str = "") -> dict:
    """Rename the OPEN group chat (e.g. a client GC after the client accepts a listing:
    JANE-WH-(~, LAS PINAS) → JANE-WH-(240SQM, LAS PINAS)). Goes through the info panel's pencil
    and verifies the header shows the new name. Refuses if the open chat isn't a group or (with
    expect_current) isn't that group — pass the current name so a chat that switched under you is
    never renamed. Staff see a "You changed the group name" notice in the chat."""
    return viber_ui.rename_group(new_name, expect_current)


@mcp.tool()
def groups_in_common(number: str = "") -> dict:
    """The groups the OPEN 1:1 chat's contact already shares with us (info panel → Groups in
    common). `existing_client: true` means the client already has a GC — HARD RULE: never make
    another; send the listing in that GC and rename it instead. Check this before any manual GC
    build. Pass the client's number to also confirm the open chat is their 1:1 chat."""
    return viber_ui.groups_in_common(number)


@mcp.tool()
def edit_message(n: int, text: str) -> dict:
    """Edit one of YOUR sent messages (n from list_messages) to `text` and save it.
    """
    return viber_ui.edit_message(n, text)


@mcp.tool()
def like_message(n: int) -> dict:
    """Toggle the heart reaction on message n (from list_messages)."""
    return viber_ui.like_message(n)


@mcp.tool()
def message_action(n: int, action: str) -> dict:
    """Right-click message n and pick an item from Viber's message menu, e.g. "Pin", "Forward",
    "Copy message", "Translate", "Set reminder", "Select". Returns the full menu that was offered.
    (Deleting goes through unsend_message.) After "Forward", use screenshot/list_elements to pick
    the recipient the staff member named in Viber's forward dialog."""
    return viber_ui.message_action(n, action)


@mcp.tool()
def close_viewer() -> dict:
    """Close the image viewer, or any open menu/dialog in Viber (presses Escape)."""
    return viber_ui.close_viewer()


@mcp.tool()
def unsend_message(text: str = "", n: int = 0, for_everyone: bool = True) -> dict:
    """Delete (unsend) ONE of your own messages in the open chat (permanent; other people's
    messages are refused).

    Identify it by `n` from list_messages (preferred) or by its text (exact, or a unique part).
    Refuses other people's messages and ambiguous matches. With for_everyone=True it chooses
    "Delete for everyone"; if Viber only allows "for myself" (message too old) nothing is deleted.

    Args:
        text: the message text (or a unique part of it).
        n: the message number from list_messages (1 = newest visible).
        for_everyone: True = unsend for everyone; False = delete only on this account.
    """
    return viber_ui.unsend_message(text=text or None, n=n or None, for_everyone=for_everyone)


# ------------------------------------------------------------------- client GCs


@mcp.tool()
def process_client_templates(limit: int = 5, preview: bool = False) -> dict:
    """Make client GCs for the templates in the templates chat (APG CLIENTS) that have no
    "DONE-..." reply yet.

    Runs autonomously (preview=True only lists what it would do). For each: create the group (staff from gc_members.json, then the client's
    number), post the client's template (client fields only), the listing (text above the dashed
    line + photos), "Hello @client", then quote-reply DONE-<GC NAME> to the template. Templates
    with an uncertain name, ambiguous/missing/SOLD listing, bad number, an existing chat of that
    name, or any DONE reply after them are skipped — report those.

    Args:
        limit: max GCs to create in one run (each takes about a minute).
        preview: True = list only; False = create.
    """
    return viber_gc.process_client_templates(limit, preview=preview)


@mcp.tool()
def create_client_gc(template_n: int, gc_name: str = "", listing_folder_id: str = "",
                     dry_run: bool = False) -> dict:
    """Make the client GC for ONE specific template (use process_client_templates for "make the
    GCs"). Use it to retry a template that was skipped, with the staff member's gc_name or
    listing_folder_id.

    Open the templates chat first and pass the template's n from list_messages. dry_run=True
    returns the plan (GC name, members, client number, client message, listing, DONE reply);
    dry_run=False runs it: create group → client's template → listing (above the dashed line +
    photos) → "Hello @client" → quote-reply "DONE-<GC NAME>" to the template. Show the plan and
    get the staff member's yes before dry_run=False.

    Args:
        template_n: the template message's n (from list_messages in the templates chat).
        gc_name: override the derived name (required when the plan reports it's uncertain).
        listing_folder_id: the listing the template names, when it couldn't be matched (from
            find_listings), or "none". A template with no listing gets none — the employee
            sends it; never pick one yourself.
        dry_run: True = plan only.
    """
    return viber_gc.create_client_gc(template_n, gc_name, listing_folder_id, dry_run)


# ------------------------------------------------------------- Google Drive listings


@mcp.tool()
def drive_sign_in(write: bool = False) -> dict:
    """Sign in to Google Drive for property listings (opens a browser the first time on this PC).
    Other Drive tools do this automatically when needed. write=True signs in the upload account
    (the company Google account, used to add listings, docs and photos to Drive)."""
    return viber_drive.sign_in(write)


@mcp.tool()
def find_listings(query: str = "", category: str = "", deal: str = "") -> dict:
    """Find property listings in the APR LISTING Drive.

    Property folders are named "City, sqm Area" (e.g. "Rizal, 1000, 2000 Taytay").
    Matches folders containing every word of `query`, newest first.

    Args:
        query: words to match, e.g. "Taytay", "Makati 260", "Quezon City".
        category: optional, e.g. "WAREHOUSE", "OFFICE SPACE", "COMMERCIAL SPACE", "LOT",
            "CONDO / HOUSE AND LOT".
        deal: optional, "LEASE" or "SALE".
    """
    return viber_drive.find_listings(query, category, deal)


@mcp.tool()
def get_listing(folder_id: str, include_internal: bool = False) -> dict:
    """Read a listing: the client-facing details (the doc's text ABOVE the dashed line) and its
    photos/videos. The part below the line is internal and withheld unless include_internal=True —
    and even then it must NEVER be sent to a client."""
    return viber_drive.get_listing(folder_id, include_internal)


@mcp.tool()
def view_listing_photos(folder_id: str, max_photos: int = 6) -> list:
    """Look at a listing's actual photos before recommending, teasing or describing it; the doc
    alone isn't enough. Check the condition, ceiling height, truck access and frontage, floor and
    layout, and whether it suits the client's line of business. Returns up to max_photos photos
    (shrunk) and how many photos/videos the folder has."""
    got = viber_drive.listing_photos(folder_id, max_photos)
    return [*(Image(data=b, format="jpeg") for b in got["images"]),
            f"Showing {len(got['images'])} of {got['total_photos']} photos "
            f"({', '.join(got['names']) or 'none'}); {got['videos']} video(s) not shown."]


@mcp.tool()
def send_listing(folder_id: str, max_photos: int = 20, include_videos: bool = True,
                 dry_run: bool = False, expect_chat: str = "") -> dict:
    """Send a listing to the OPEN Viber chat: the client-facing details text, the photos together
    in one batch, then the listing's videos (on by default; some listings only have a video).
    The doc's internal part is never sent.

    dry_run=True previews exactly what would be sent without sending anything. expect_chat: the
    chat this must go to (the GC/contact name you opened); each step is refused if another chat
    is open — always pass it.
    """
    return viber_drive.send_listing(folder_id, max_photos, include_videos, dry_run, expect_chat)


@mcp.tool()
def post_new_listing(text: str, category: str, deal: str, folder_name: str,
                     photo_paths: list[str], subfolder: str = "", dry_run: bool = False) -> dict:
    """Post a NEW listing to the staff listings GC (APR LISTING ONLY) and add it to Drive.

    Does what staff do by hand: posts the full listing text (client part, dashed line, internal
    part) → the photos as one album → a dashed separator, then creates the "*<folder_name>"
    folder, the listing doc and the photos in Drive and quote-replies "Added to Drive". (If the
    OAuth client isn't set up on this PC it returns `drive_upload` for the Drive connector
    instead.) Refuses (before sending anything) if the text has no dashed
    separator, a photo is missing, the category/deal/subfolder isn't found, a listing folder with
    that name already exists, or the listing is already the latest GC post. If it stops part-way
    it says what's done.

    Args:
        text: the full listing in the staff format (see other posts in the GC).
        category: Drive category folder, e.g. "WAREHOUSE", "COMMERCIAL SPACE", "OFFICE SPACE",
            "CONDO / HOUSE AND LOT".
        deal: "LEASE" or "SALE".
        folder_name: "City, sqm Area", e.g. "Pasay City, 8, 12, 22 Malibay".
        photo_paths: full paths of the listing's photos on this PC, in order (may be empty).
        subfolder: a group folder under the deal folder, e.g. "LOT" for lots (CONDO / HOUSE AND
            LOT / FOR LEASE/ FOR RENT / LOT).
        dry_run: True = check everything and show the plan; send nothing.
    """
    return viber_drive.post_new_listing(text, category, deal, folder_name, photo_paths, subfolder,
                                        dry_run)


@mcp.tool()
def confirm_listing_in_drive(folder_name: str, photos: int) -> dict:
    """After uploading a posted listing with the Drive connector: check its Drive folder has the
    "*<folder_name>" doc and at least `photos` photos, then quote-reply
    "Added to Drive [File name:*<folder_name>]" to its post in APR LISTING ONLY. Posts nothing if
    anything is missing."""
    return viber_drive.confirm_listing_in_drive(folder_name, photos)


@mcp.tool()
def add_listing_to_drive(text: str, category: str, deal: str, folder_name: str,
                         photo_paths: list[str], subfolder: str = "", dry_run: bool = False) -> dict:
    """Add a listing that's ALREADY posted in Viber (e.g. by staff) to Drive: creates (or reuses)
    the "*<folder_name>" folder, the "*<folder_name>" Google Doc from `text`, and uploads the
    photos as AlphaPremier_Photo_NN. Posts nothing in Viber — quote-reply its
    `added_to_drive_reply` to the staff post yourself. Same arguments as post_new_listing; the
    text must use the listing template with the dashed separator. Photos posted in Viber are
    saved in Documents\\ViberDownloads (match them to the post's time)."""
    return viber_drive.add_listing_to_drive(text, category, deal, folder_name, photo_paths,
                                            subfolder, dry_run)


@mcp.tool()
def drive_upload_files(folder_id: str, paths: list[str], start: int = 1) -> dict:
    """Upload photos/videos from this PC into a Drive folder, named AlphaPremier_Photo_NN
    (numbered from `start`)."""
    return {"uploaded": viber_drive.upload_files(folder_id, paths, start)}


@mcp.tool()
def update_listing_doc(folder_id: str, text: str) -> dict:
    """Replace the whole text of a listing folder's Google Doc (e.g. to reformat it to the
    listing template). The doc keeps its link. The text must have the dashed separator."""
    return viber_drive.update_listing_doc(folder_id, text)


@mcp.tool()
def record_availability(folder_id: str, status: str, note: str = "", checked_on: str = "") -> dict:
    """Record an owner's availability answer in the listing's Drive doc so coworkers see when it
    was last checked. Do this EVERY time an owner (or the call staff) answers about a listing
    (available, taken, partly available, on hold, viewing arranged), right after reading it.

    Writes "Last availability check (Oct 22, 2026): AVAILABLE. <note>" as the first line under the
    dashed separator (internal part, never sent to clients), replacing the previous check line.
    The doc is edited in place, so its formatting and link stay. A taken/sold listing still goes
    to move_listing_to_sold afterwards.

    Args:
        folder_id: the listing folder (from find_listings).
        status: e.g. AVAILABLE, NOT AVAILABLE, PARTLY AVAILABLE, UNDER NEGOTIATION, ON HOLD.
        note: who confirmed and how, plus what coworkers need, e.g. "Owner Joseph See confirmed
            on Viber. Viewing set Mon, Oct 26, 11 AM (JAYSON-WH)."
        checked_on: date of the owner's answer as YYYY-MM-DD; empty = today.
    """
    return viber_drive.record_availability(folder_id, status, note, checked_on)


@mcp.tool()
def move_listing_to_sold(folder_id: str) -> dict:
    """Move a listing that's no longer available (the owner said it's leased, sold or taken)
    to APR LISTING / SOLD / SOLD <CATEGORY> in Drive, so it stops showing up in find_listings.
    folder_id comes from find_listings. The folder keeps its link."""
    return viber_drive.move_listing_to_sold(folder_id)


@mcp.tool()
def drive_search(query: str, type_contains: str = "") -> dict:
    """Search Google Drive by file name (any folder). type_contains filters the MIME type,
    e.g. "image/", "pdf", "folder"."""
    return {"files": viber_drive.search(query, type_contains)}


@mcp.tool()
def drive_download(file_ids: list[str]) -> dict:
    """Download Drive files to this PC; returns local paths for send_files. Don't delete them
    right after sending (Viber may still be uploading); old downloads are pruned automatically."""
    return {"paths": viber_drive.download(file_ids)}


@mcp.tool()
def clear_downloads(everything: bool = False) -> dict:
    """Prune the Drive download folder: deletes only files older than a few days (default 3,
    env VIBER_MCP_KEEP_DOWNLOADS_DAYS) so photos still uploading aren't pulled out from under
    Viber. everything=True empties the whole folder — only when the staff member asks.
    send_listing and client-GC runs prune automatically."""
    if everything:
        return viber_drive.clear_downloads()
    return viber_drive.prune_downloads()


# ------------------------------------------------------------- computer-use primitives


@mcp.tool()
def screenshot() -> Image:
    """Screenshot of the Viber window (taken in the background, even if Viber is covered).

    Coordinates for click/scroll are pixels in this image. Take a new one after actions that
    change the screen.
    """
    return Image(data=viber_ui.screenshot(), format="png")


@mcp.tool()
def list_elements(query: str = "") -> str:
    """List Viber's on-screen controls and texts with ids and click points (screenshot pixels).

    More precise than reading a screenshot: names of buttons/menu items, message texts, input
    boxes. Use the id with click_element. Ids change whenever the screen changes.

    Args:
        query: optional filter, e.g. "delete", "menuitem", "button", or part of a message.
    """
    return viber_ui.list_elements(query or None)


@mcp.tool()
def click_element(element_id: int, button: str = "left", double: bool = False) -> dict:
    """Click an element from the latest list_elements (left/right/middle, optional double-click).
    Right-click a message to get its menu (Reply, Edit, Copy, Forward, Pin, Delete, ...)."""
    return viber_ui.click_element(element_id, button=button, double=double)


@mcp.tool()
def click(x: int, y: int, button: str = "left", double: bool = False) -> dict:
    """Click at (x, y) in the latest screenshot. button: "left", "right" or "middle".
    Runs in the background — the real mouse cursor doesn't move."""
    return viber_ui.click(x, y, button=button, double=double)


@mcp.tool()
def type_text(text: str) -> dict:
    """Type text into the field you last clicked in Viber (search box, message box, edit box).
    Does NOT send — sending is a separate click on the send button or press_key("enter")."""
    return viber_ui.type_text(text)


@mcp.tool()
def press_key(key: str, repeat: int = 1) -> dict:
    """Press a key in Viber: escape, enter, backspace, delete, tab, space, up, down, left, right,
    pageup, pagedown, home, end. Ctrl/Alt shortcuts aren't available in the background.
    Careful: "enter" in a message box sends the message."""
    return viber_ui.press_key(key, repeat)


@mcp.tool()
def scroll(x: int, y: int, direction: str = "down", amount: int = 5) -> dict:
    """Scroll whatever is under (x, y) in the latest screenshot. direction "up"/"down",
    amount = wheel notches (1-50)."""
    return viber_ui.scroll(x, y, direction, amount)


# ---------------------------------------------------------------------------- prompts


@mcp.prompt()
def inbox_triage() -> str:
    """Summarize the Viber inbox: who is waiting for a reply and what they need."""
    return ("Check the Viber inbox with list_chats(unread_only=True) (scroll_chat_list for more; "
            "list_chats() shows every chat with last_from = who wrote last). For each chat with "
            "unread messages, open it (search_and_open), read the recent messages (list_messages, "
            "open_image for photos — then close_viewer), and summarize: who, what they need, how "
            "urgent, and whether the client has the last word. Don't reply to anyone and don't "
            "mark_as_read unless I ask. If an owner (or the call staff) answered about a listing's "
            "availability, record it in the listing doc with record_availability (find_listings for "
            "the folder). End with a short prioritized list of suggested replies for me to approve.")


@mcp.prompt()
def draft_reply(chat: str, guidance: str = "") -> str:
    """Draft (not send) a reply in a Viber chat for review."""
    return (f"Open the Viber chat '{chat}' (search_and_open; if low_confidence or several similar "
            "chats, ask me which). Read the recent messages with list_messages (open_image for photos, "
            "then close_viewer), then write a reply in the chat's language and tone. Guidance: "
            f"{guidance or 'none — infer from context'}. If the reply recommends a listing, first make "
            "sure you know the client's line of business (if not, the reply asks for it) and look at "
            "the listing's photos with view_listing_photos. Show me the reply and what it answers. Put it "
            "in the message box with put_draft — or, if it answers a specific earlier message, with "
            "reply_to_message(n, text) so it's quoted. Do NOT send.")


@mcp.prompt()
def unsend(chat: str, message: str) -> str:
    """Unsend one of our messages in a chat."""
    return (f"Open the Viber chat '{chat}', find our message matching '{message}' with "
            "list_messages and unsend it with unsend_message. If several of our messages match, ask "
            "me which. Report which message was unsent and whether it was verified.")


@mcp.prompt()
def property_details(chat: str, property: str) -> str:
    """Send a property listing (text + photos) from Google Drive to a Viber chat."""
    return (f"Open the Viber chat '{chat}' (search_and_open; if low_confidence or several similar "
            "chats, ask me which) and backread_chat for the client's requirements and line of business "
            "(what the space is for). If the line of business isn't known, ask the client in one short "
            f"line and stop there. Then find_listings for '{property}' (if several match, ask me "
            "which), read it with get_listing and look at its photos with view_listing_photos to check "
            "it fits their business. If the owner hasn't confirmed availability yet, follow the "
            "guidelines (short teaser, ask the owner, record_availability with the answer) instead of "
            "sending. Once confirmed, send_listing for that folder_id with expect_chat set to the chat "
            "name, so it refuses if another chat took over. Never send, quote or summarize the "
            "internal part of the doc. Report what was sent and whether the text and photos were "
            "verified.")


@mcp.prompt()
def client_gc(client: str = "") -> str:
    """Make client group chats from the templates in APG CLIENTS."""
    if client:
        return (f"Open APG CLIENTS, find the template for '{client}' with list_messages, and call "
                "create_client_gc with its n. Report each step's result; if it fails, tell me exactly "
                "what failed and don't retry.")
    return ("Call process_client_templates to make the GCs for every APG CLIENTS template without a "
            "DONE reply. Then report each GC created (name, whether the listing and DONE reply went "
            "through) and anything skipped or failed, with the reason.")


if __name__ == "__main__":
    mcp.run(transport="stdio")
