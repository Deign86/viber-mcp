# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A macOS-only MCP server that sends and reads Viber Desktop messages by **automating the running,
signed-in Viber app** — Viber has no API, its message DB is encrypted, and its Qt accessibility tree
is unusable, so everything is done via the `viber://` URL scheme, synthetic HID input, and screen OCR.

## Commands

```bash
uv sync                                    # install deps (Python managed by uv)

# Standalone client = the way to test/run tools without Claude (spawns a fresh server each call,
# so it always uses the latest code). Subcommands map 1:1 to tools:
uv run client.py list                      # advertise tools
uv run client.py open|openread|search "<name-or-+E164>"
uv run client.py send  "<to>" "<text>" [--dry-run]
uv run client.py reply "<text>"            # into the currently-open chat
uv run client.py draft "<text>"            # paste into open chat, do NOT send
uv run client.py note  "<text>"            # save to My Notes
uv run client.py chats | read | contacts

# Register with Claude Code (user scope):
claude mcp add --transport stdio --scope user viber -- uv --directory "$(pwd)" run server.py
```

There is no test suite, linter, or build step. **Verify changes by driving the real app** with
`client.py` and screenshotting the Viber window (`viber_ocr.capture_window`) to confirm the outcome —
the returned `verified` flag is best-effort and gives false negatives.

After editing server code, the **registered** MCP server keeps running the old code until reconnected
(`/mcp` → reconnect `viber`, or restart Claude Code); `client.py` always runs fresh code.

## Architecture

The code is split into **platform-neutral domain logic** and a **platform Backend** (so the app is
port-ready for Windows/Linux — see "Porting" below):

- `server.py` — FastMCP stdio server; thin `@mcp.tool()` wrappers only. Tools: `send_message`,
  `reply`, `open_chat`, `open_and_read`, `read_open_chat`, `list_chats`, `put_draft`,
  `search_and_open`, `save_to_my_notes`, `open_my_notes`, `list_contacts`.
- `viber_send.py` — **platform-neutral** write/navigation logic: recipient resolution
  (`contacts.json` alias → E.164), the send pipeline, search-result scoring, panel detection, send
  verification. Holds a module-level `backend = get_backend()` and calls only its semantic methods.
  Raises `SendError`.
- `viber_ocr.py` — **platform-neutral** read side: `read_open_chat()` / `read_chat_list()` format
  `backend.observe()` output (`read_chat_list` keeps only left-column x<0.30). Raises `OCRError`.
- `backends/` — the platform layer.
  - `base.py` — `Backend` ABC + `OCRError`. Semantic interface: `app_running/focus/open_url`,
    `observe()→[{text,x,y}]`, clipboard, `type_text/select_all/delete/paste`, and element clicks
    `click_compose/send/search/result(norm_y)/panel_close`. The domain never sees pixels or key codes.
  - `macos.py` — `MacBackend`: all Quartz/Vision/AppKit code AND the macOS Viber-layout constants
    (element pixel/fraction offsets). This is the only file with OS APIs.
  - `__init__.py` — `get_backend()` picks the backend by `sys.platform` (macOS only today).
- `client.py` — standalone MCP stdio client for testing.
- `contacts.json` — name→E.164 map, **git-ignored (real numbers)**; ship `contacts.example.json`.

Data flow for a send: resolve recipient → `backend.open_url("viber://chat?number=…")` → poll
`backend.observe()` until the chat is confirmed → `click_compose` → `select_all`+`delete` → `paste`
(clipboard, restored after) → `click_send` → OCR-verify the box cleared.

## Porting to another OS

Add `backends/<os>.py` implementing `Backend`, wire it into `get_backend()`, and re-tune that
platform's element locations. The domain logic, tool definitions, and `contacts.json` handling are
already shared and need no changes.

Status:

| Platform | Backend | State |
|----------|---------|-------|
| macOS | `backends/macos.py` (`MacBackend`) | ✅ implemented (Quartz/Vision/AppKit, coordinate-based) |
| Windows | `backends/windows.py` (`WindowsBackend`) | ✅ implemented, background (PrintWindow + Windows.Media.Ocr, UIA to locate, posted messages for input; `windows_selftest.py`) |
| Linux | — | ❌ not started |

**Windows (background mode) — constraints learned the hard way:**
- **Pick the window titled "Rakuten Viber".** Qt also owns hidden helper windows
  (`...ThemeChangeObserverWindow`, `QTrayIconMessageWindow`) that can be *larger*; never
  `ShowWindow` them — doing so leaves a blank white window on screen.
- **UIA is for locating/reading only.** UIA `SetValue`/`Invoke` make Qt activate Viber and steal
  keyboard focus. Clicks and text are *posted* (`WM_LBUTTONDOWN/UP`, `WM_CHAR`, `WM_KEYDOWN`) to
  the main hwnd at UIA-derived client coords — Qt handles them without activation.
- **Posted `WM_CHAR` drops non-BMP chars** (surrogate pairs, e.g. 😀; `WM_UNICHAR` is ignored
  too) and Enter may send, so those texts fall back to UIA `SetValue` + `_restore_foreground`
  (AttachThreadInput trick; activation arrives asynchronously, so wait for it before restoring).
- **Search-result UIA delegates are whole sections**, not rows — click the OCR'd row position.
- Capture with `PrintWindow(..., PW_RENDERFULLCONTENT)`; it works while Viber is covered.
- The "not in your contact list" banner appears on every unsaved-number chat — not an error.

**Linux:** hardest target — Wayland heavily restricts screen capture and synthetic input; X11
(via `xdotool`/`wmctrl` + Tesseract) is far easier.

## Critical, non-obvious constraints (most bugs here come from violating these)

- **All synthetic input MUST come from a real HID event source**
  (`_SRC = CGEventSourceCreate(kCGEventSourceStateHIDSystemState)`). Qt silently drops plain
  `CGEvent`s and System Events keystrokes. Use the `_click` / `_key` / `_type` helpers.
- **`_type` must zero modifier flags per event.** A Cmd leaking from a prior `Cmd+A` turns the next
  character into a shortcut (e.g. typing "My Notes" → **Cmd+M = minimize the window**).
- **Sidebar/search/send-button/panel-close are targeted by fixed PIXEL offsets from window edges**
  (`_send_point`, `_panel_close_point`, and the `b["X"]+175` search click), NOT fractions of the
  window. The sidebar and side panels have fixed pixel widths, so fraction-based clicks miss on
  wide/maximized windows. Only the compose box uses a fraction (`_COMPOSE_FRAC`) because the input is
  wide and forgiving.
- **Close any right-side panel before sending.** The chat-info and sticker "Settings" panels shift
  the send button left; `_ensure_panel_closed` (called inside `_paste_into_compose`) detects
  (`_side_panel_open` via OCR markers) and closes them.
- **Click the send button; do not press Return.** "Send message on Enter" may be off, in which case
  Return just inserts a newline.
- **`viber://chat?number=` navigation is slow (~seconds) and mobile-first.** Never assume the chat
  opened immediately — poll via OCR (`_wait_for_sendable_chat`). It also errors "isn't on Viber" for
  non-users and "cannot add yourself" for the user's own number (My Notes is separate).
- **Search results are substring-matched and alphabetically sorted**, so the first row is often wrong
  (searching "Ivy" lists "Divya" first). `_find_result_row` scores exact/prefix over substring and
  clicks the matched row's actual OCR position; excludes the search-bar text (y<0.12), section
  headers, phone numbers, and action rows.
- **`find_viber_window` ignores the tiny menu-bar window** (>200px filter) so captures aren't blank.
- **OCR interleaves the sidebar and the open conversation.** Use x-position filtering
  (`read_chat_list` keeps x<0.30) or headers to disambiguate; OCR also garbles small/emoji text.

## Environment requirements

- Viber running, signed in, and in a **normal window on the main desktop — not macOS fullscreen**
  (fullscreen puts it on a separate Space, causing window-ID churn and blank captures). Don't resize
  it mid-operation.
- macOS **Accessibility** (synthetic input) and **Screen Recording** (capture) granted to the process
  that launches the server (the terminal, e.g. iTerm). A stuck grant shows error 1002 ("not allowed
  to send keystrokes") despite the toggle being on — fix with
  `tccutil reset Accessibility <bundle-id>` (e.g. `com.googlecode.iterm2`) and relaunch.

## Slash command

`commands/viber-reply.md` (copy to `~/.claude/commands/`) reads the open chat and drafts a reply into
the compose box via `read_open_chat` + `put_draft` — it never sends.
