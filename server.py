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
def reply(text: str) -> dict:
    """Send `text` into the CURRENTLY OPEN chat (no navigation).

    Use after read_open_chat when the right chat is already on screen — avoids re-resolving
    or re-opening the recipient. Returns whether it sent and `verified` (seen on screen).

    Args:
        text: the message body (emoji/unicode/newlines supported).
    """
    return viber_send.reply(text)


@mcp.tool()
def search_and_open(name: str) -> dict:
    """Search Viber for `name` and open the best-matching contact/chat; return its visible text.

    Works for any Viber contact by name (not just contacts.json). Types into the search box,
    OCRs the results, and opens the row that best matches `name` — exact/prefix matches beat
    substrings (so "Ivy" opens "Ivy Cloma-Rosales", not "Divya"). Returns `matched` (the row it
    opened) and `low_confidence` (true if only a weak substring match existed). If
    `low_confidence` is true, confirm the opened chat is correct before sending.

    Args:
        name: the contact or chat name to search for.
    """
    return viber_send.search_and_open(name)


@mcp.tool()
def list_chats() -> dict:
    """List recent chats from the sidebar (names, previews, times, unread counts) via OCR.

    Reads only the left-hand chat list of whatever is currently on screen — a quick inbox
    overview. Reflects the sidebar's current filter/scroll position; scroll the list in Viber
    to see more.
    """
    return viber_ocr.read_chat_list()


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
def put_draft(text: str) -> dict:
    """Paste `text` into the currently-open Viber chat's compose box WITHOUT sending.

    Does not navigate — it drafts into whatever chat is open right now, so the user can
    review, edit, and press send themselves. Pair with read_open_chat to draft a reply:
    read the visible conversation, compose a fitting reply, then put_draft it.

    Args:
        text: the drafted reply to place in the compose box.
    """
    return viber_send.paste_into_open_chat(text)


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
def send_files(paths: list[str], caption: str = "", dry_run: bool = False) -> dict:
    """Send local files (photos, PDFs, videos, docs) to the OPEN chat in one batch, with an
    optional caption. Confirm the chat and the files with the staff member first.

    Several photos go out together as one album. With dry_run=True the files and caption are
    left in Viber's send preview for the staff member to check and send themselves.

    Args:
        paths: full paths of the files on this PC, in the order to send them.
        caption: optional text sent with the files.
        dry_run: True = stop at the preview, don't send.
    """
    return viber_ui.send_files(paths, caption=caption, dry_run=dry_run)


@mcp.tool()
def reply_to_message(n: int, text: str, send: bool = False) -> dict:
    """Quote-reply to a specific message (n from list_messages) — the client sees which message
    you're answering. Leaves it as a draft unless send=True (confirm with the staff member first).
    """
    return viber_ui.reply_to_message(n, text, send)


@mcp.tool()
def edit_message(n: int, text: str) -> dict:
    """Edit one of YOUR sent messages (n from list_messages) to `text` and save it.
    Confirm the new wording with the staff member first."""
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
    the recipient in Viber's forward dialog — confirm the recipient with the staff member."""
    return viber_ui.message_action(n, action)


@mcp.tool()
def close_viewer() -> dict:
    """Close the image viewer, or any open menu/dialog in Viber (presses Escape)."""
    return viber_ui.close_viewer()


@mcp.tool()
def unsend_message(text: str = "", n: int = 0, for_everyone: bool = True) -> dict:
    """Delete (unsend) ONE of your own messages in the open chat. Permanent — confirm with the
    staff member first.

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
def process_client_templates(limit: int = 5, preview: bool = True) -> dict:
    """Make client GCs for the templates in the templates chat (APG CLIENTS) that have no
    "DONE-..." reply yet.

    preview=True (default) only lists the GCs it would create and the templates it would skip.
    Show that list to the staff member; after a clear yes, call again with preview=False to
    create them. For each: create the group (staff from gc_members.json, then the client's
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
                     dry_run: bool = True) -> dict:
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
        listing_folder_id: override/choose the listing folder (from find_listings), or "none".
        dry_run: True = plan only.
    """
    return viber_gc.create_client_gc(template_n, gc_name, listing_folder_id, dry_run)


# ------------------------------------------------------------- Google Drive listings


@mcp.tool()
def drive_sign_in() -> dict:
    """Sign in to Google Drive for property listings (opens a browser the first time on this PC).
    Other Drive tools do this automatically when needed."""
    return viber_drive.sign_in()


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
def send_listing(folder_id: str, max_photos: int = 20, include_videos: bool = False,
                 dry_run: bool = False) -> dict:
    """Send a listing to the OPEN Viber chat: the client-facing details text, then the photos
    together in one batch. The doc's internal part is never sent.

    Confirm the chat and the listing with the staff member first (or use dry_run=True to preview
    exactly what would be sent without sending anything).
    """
    return viber_drive.send_listing(folder_id, max_photos, include_videos, dry_run)


@mcp.tool()
def drive_search(query: str, type_contains: str = "") -> dict:
    """Search Google Drive by file name (any folder). type_contains filters the MIME type,
    e.g. "image/", "pdf", "folder"."""
    return {"files": viber_drive.search(query, type_contains)}


@mcp.tool()
def drive_download(file_ids: list[str]) -> dict:
    """Download Drive files to this PC; returns local paths for send_files."""
    return {"paths": viber_drive.download(file_ids)}


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
    return ("Check the Viber inbox with list_chats (scroll_chat_list for more). For each chat with "
            "unread messages, open it (search_and_open), read the recent messages (list_messages, "
            "open_image for photos — then close_viewer), and summarize: who, what they need, how "
            "urgent. Don't reply to anyone. End with a short prioritized list of suggested replies "
            "for me to approve.")


@mcp.prompt()
def draft_reply(chat: str, guidance: str = "") -> str:
    """Draft (not send) a reply in a Viber chat for review."""
    return (f"Open the Viber chat '{chat}' (search_and_open; if low_confidence or several similar "
            "chats, ask me which). Read the recent messages with list_messages (open_image for photos, "
            "then close_viewer), then write a reply in the chat's language and tone. Guidance: "
            f"{guidance or 'none — infer from context'}. Show me the reply and what it answers. Put it "
            "in the message box with put_draft — or, if it answers a specific earlier message, with "
            "reply_to_message(n, text) so it's quoted. Do NOT send.")


@mcp.prompt()
def unsend(chat: str, message: str) -> str:
    """Unsend one of our messages in a chat, with confirmation."""
    return (f"Open the Viber chat '{chat}', find our message matching '{message}' with "
            "list_messages, show me exactly which message (n, text) you'll unsend, and wait for my "
            "yes before calling unsend_message. Report whether it was verified.")


@mcp.prompt()
def property_details(chat: str, property: str) -> str:
    """Send a property listing (text + photos) from Google Drive to a Viber chat, after approval."""
    return (f"Open the Viber chat '{chat}' (search_and_open; if low_confidence or several similar "
            f"chats, ask me which). Then find_listings for '{property}' (if several match, ask me "
            "which) and get_listing for the chosen folder_id. Show me the chat name, the client_text "
            "exactly as it will be sent, and the photo count, then wait for my clear yes before "
            "calling send_listing. Never send, quote or summarize the internal part of the doc. "
            "Report whether the text and photos were verified.")


@mcp.prompt()
def client_gc(client: str = "") -> str:
    """Make client group chats from the templates in APG CLIENTS (shows the plan first)."""
    if client:
        return (f"Open APG CLIENTS, find the template for '{client}' with list_messages, and call "
                "create_client_gc with its n and dry_run=true. Show me the plan; after my yes, call "
                "it again with dry_run=false and report each step's result.")
    return ("Call process_client_templates (preview) and show me the GCs it would create and the "
            "templates it would skip, with reasons. After my yes, call it with preview=false, then "
            "report each GC created (name, whether the listing and DONE reply went through) and "
            "anything skipped.")


if __name__ == "__main__":
    mcp.run(transport="stdio")
