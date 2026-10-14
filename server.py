"""Viber MCP server — expose send/receive of Viber Desktop messages as MCP tools.

Transport: stdio. Register with Claude Code via `claude mcp add` (see README).
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

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
            "chats, ask me which). Read the recent messages, then write a reply in the chat's "
            f"language and tone. Guidance: {guidance or 'none — infer from context'}. Show me the "
            "reply and what it answers, then put it in the message box with put_draft. Do NOT send.")


@mcp.prompt()
def unsend(chat: str, message: str) -> str:
    """Unsend one of our messages in a chat, with confirmation."""
    return (f"Open the Viber chat '{chat}', find our message matching '{message}' with "
            "list_messages, show me exactly which message (n, text) you'll unsend, and wait for my "
            "yes before calling unsend_message. Report whether it was verified.")


if __name__ == "__main__":
    mcp.run(transport="stdio")
