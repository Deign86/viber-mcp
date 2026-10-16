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
uv run client.py call  <tool> '<json>'     # any tool (images saved as tool_image_N.png)
uv run client.py chats | read | contacts

# Register with Claude Code (user scope):
claude mcp add --transport stdio --scope user viber -- uv --directory "$(pwd)" run server.py
```

**Tests:** `uv run --with pytest pytest tests -q` (no Viber needed; CI runs it on windows-latest via
`.github/workflows/tests.yml`). Pure decision logic lives in `viber_gc_logic.py` (template parsing,
naming, `pending_templates`, `choose_listing`) and pure helpers in `viber_ui.py` (`_messages`,
`_preview`, `find_number_row`, ...) so it can be tested with fixture data; UI-structure tests inject a
fake backend via `backends._backend` (no module mocking). **Every bug found on the live app gets a
regression test** in `tests/test_regressions.py` (or the matching test file) before/with the fix.

Then **verify on the real app** with `client.py` (or `client.py call <tool> '<json>'`) and screenshots —
the returned `verified` flag is best-effort and gives false negatives. Only test writes in My Notes.

After editing server code, the **registered** MCP server keeps running the old code until reconnected
(`/mcp` → reconnect `viber`, or restart Claude Code); `client.py` always runs fresh code.

## Architecture

The code is split into **platform-neutral domain logic** and a **platform Backend** (so the app is
port-ready for Windows/Linux — see "Porting" below):

- `server.py` — FastMCP stdio server; thin `@mcp.tool()` wrappers only. Tools: `send_message`,
  `reply`, `open_chat`, `open_and_read`, `read_open_chat`, `list_chats`, `put_draft`,
  `search_and_open`, `save_to_my_notes`, `open_my_notes`, `list_contacts`, `scroll_chat`,
  `scroll_chat_list` (backend `scroll(area, notches)`; Windows posts `WM_MOUSEWHEEL`, macOS not yet).
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

## Staff rules, prompts, computer use

- `guidelines.md` is the staff operating rulebook; `server.py` serves it as MCP server
  `instructions`, so every client gets it automatically. Change rules there, not in tool docs.
- MCP prompts (`inbox_triage`, `draft_reply`, `unsend`, `property_details`, `client_gc`) are the
  slash-command workflows.
- Client GCs: `viber_gc_logic.py` (pure: template parsing, client-field whitelist, naming rules,
  tested) + `viber_gc.py` (plan/run) + `viber_ui.create_group` / `mention`. The new-group picker
  and the mention list aren't in the UIA tree — rows are found by OCR; OCR confuses I/l/1 and O/0,
  so names are compared after folding those (`_norm`, `viber_send._ocr_fold`).
  The picker labels unsaved numbers "Unknown"; their Viber name comes from the 1:1 chat's
  "<Name> +63… is not in your contact list" banner (`viber_gc.client_name_from_chat`). Mentions
  are picked with Down×k + Tab and accepted only if the box holds exactly `@<name>`
  (`mention_inserted` — "Anthony" is inside "Mark Anthony Santos"); then a space closes the list
  before send (sending with it open sent the highlighted, wrong person). Clicking a sent mention
  opens that person's 1:1 chat (number under the name, or in the banner) — `mention_opens_number`.
- `viber_ui.py` holds the computer-use primitives (screenshot/click/type/key/scroll/elements)
  and the actions built on them (`list_messages`, `open_image`, `unsend_message`). On Windows,
  messages are `FeedDelegate` elements; text is the child `TextEditItem` edit's value, images
  have a `RemixImageButton` child just under the picture; own messages are right of the chat
  pane's (`StackView`) centre. The context menu, image viewer and dialogs render inside the main
  window. Never click a generic "Delete" *button* — the info panel's "Delete" deletes the chat;
  unsend only uses the context-menu `MenuItem` "Delete".
- `send_files` (viber_ui.py, Windows) drives Viber's attach flow with window messages only. The
  attach button is the leftmost `IconButton` on the message-box row; the first click on it is
  sometimes swallowed, so it retries. The picker is a `#32770` dialog: fill the filename via
  `WM_SETTEXT` on `ComboBoxEx32 > ComboBox > Edit`, then `WM_COMMAND` `IDOK`. After it closes Windows
  re-activates Viber, so focus is handed back immediately. The preview overlay: the caption `Edit`
  (class `TextArea`); the overlay container is the caption's grandparent; send is a `RoundIconButton`
  child of the overlay; add-more is the `IconButton` child of the overlay just above the caption
  (NOT the overlay's close X `IconButton` at the top right). `dry_run` stops before send.
- `viber_drive.py` — Google Drive listings (read-only OAuth desktop flow). Client file
  `google_oauth_client.json` next to `server.py` (git-ignored) or env `VIBER_MCP_GOOGLE_CLIENT`;
  token at `%LOCALAPPDATA%\viber-mcp\google_token.json`; downloads go to
  `%LOCALAPPDATA%\viber-mcp\drive-cache` and are kept ~3 days, then pruned by `prune_downloads`
  (`VIBER_MCP_KEEP_DOWNLOADS_DAYS`; never delete right after sending — that raced Viber's upload and
  left photos "not sent"). `remove_downloads` /
  `clear_downloads`, which never delete outside that folder); root folder name from `VIBER_MCP_LISTINGS_FOLDER`
  (default `APR LISTING`). Layout: root / CATEGORY / `FOR LEASE/RENT` or `FOR SALE` / [group, e.g.
  `LOT` — a comma-less folder with subfolders, `_is_subfolder_group`] / `City, sqm Area`
  folder with one Google Doc (title starts with `*`) plus JPGs. Drive calls use
  `execute(num_retries=RETRIES)` — 500s and dropped connections (WinError 10053) are common. The doc is split at the typed dashed
  line: text above is client-facing, below is INTERNAL (address, owner/agent numbers) and withheld in
  code unless `get_listing(include_internal=True)`. `send_listing` sends the text, then the photos via
  `send_files`; never the internal part. Tools: `drive_sign_in`, `find_listings`, `get_listing`,
  `send_listing`, `drive_search`, `drive_download`, `post_new_listing`, `confirm_listing_in_drive`.
- **This server never writes to Drive.** Its Google identity is the read-only service account
  (`viber-drive-reader@apg-viber-assistant.iam.gserviceaccount.com`, gcloud project
  `apg-viber-assistant`); service accounts have 0 bytes of storage, so uploads failed with
  `storageQuotaExceeded` after creating an empty folder. Uploads go through the Google Drive
  connector (thealphapremiergroup@gmail.com).
- `post_new_listing` (viber_drive.py) validates everything first (separator, photos,
  category/deal[/subfolder] via `pick_folder`, no existing folder of that name), opens
  `VIBER_MCP_LISTINGS_GC` (default `APR LISTING ONLY`; `is_listings_gc` tolerates OCR junk like a
  trailing "w"), refuses if the listing is already the latest post, posts the text, confirms it is
  on screen (`find_posted_message`, which strips Viber's U+2068/U+2069 isolate marks — `reply`'s
  `verified` gives false negatives on long texts), then photos → dashed separator, and returns
  `drive_upload` (parent folder id, folder name, doc title/text with `Added to Drive: <date>`,
  `AlphaPremier_Photo_NN` names). `confirm_listing_in_drive` checks folder/doc/photo count in Drive,
  then quote-replies `Added to Drive [File name:*<name>]`. A failure after the first GC send returns
  `STOP` with what's done instead of raising — never re-run it.
- Test any tool with `uv run client.py call <tool> '<json>'`; element ids from `list_elements`
  only live within one server process (each `client.py` call starts a new one).
