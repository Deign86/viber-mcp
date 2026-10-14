"""Viber MCP server — expose send/receive of Viber Desktop messages as MCP tools.

Transport: stdio. Register with Claude Code via `claude mcp add` (see README).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import viber_ocr
import viber_send

mcp = FastMCP("viber")


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


if __name__ == "__main__":
    mcp.run(transport="stdio")
